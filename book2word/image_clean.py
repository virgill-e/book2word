"""Rasterisation des pages PDF et suppression du texte (masquage + reconstruction du fond)."""
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
import pymupdf as fitz  # PyMuPDF

BBox = Tuple[float, float, float, float]
IntBox = Tuple[int, int, int, int]


def render_page(page: "fitz.Page", dpi: int) -> np.ndarray:
    """Rasterise une page PDF en image RGB (numpy array) à la résolution demandée."""
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    return img.copy()  # RGB, copie car `pix.samples` référence un buffer C géré par PyMuPDF


def autocrop_page(
    img: np.ndarray,
    dark_threshold: int = 30,
    min_area_ratio: float = 0.12,
    max_area_ratio: float = 0.95,
) -> Tuple[np.ndarray, Optional[IntBox]]:
    """Recadre une page photographiée sur fond sombre (bordures noires, marges de prise de vue).

    Détecte le plus grand contour de pixels "non noirs" et recadre sur sa bounding box.
    Si ce contour est trop petit (rien de net à recadrer) ou trop grand (touche presque
    tout le cadre, typiquement quand un objet — main, reflet — touche la page et empêche
    une séparation fiable), le recadrage est abandonné et l'image est retournée inchangée
    (bbox=None) : ce cas doit être signalé à l'appelant plutôt que produire un mauvais crop.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    mask = (gray > dark_threshold).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img, None

    biggest = max(contours, key=cv2.contourArea)
    x, y, cw, ch = cv2.boundingRect(biggest)
    # Le ratio pertinent est celui de la bounding box retenue, pas de l'aire du contour :
    # un objet qui touche la page (main, reflet) étire la bbox jusqu'aux bords de l'image
    # même si sa propre surface reste modeste — c'est ce cas qu'il faut détecter et rejeter.
    bbox_ratio = (cw * ch) / (h * w)
    if bbox_ratio < min_area_ratio or bbox_ratio > max_area_ratio:
        return img, None

    return img[y:y + ch, x:x + cw], (x, y, x + cw, y + ch)


def _dilate_bbox(bbox: BBox, pad: int, shape: Tuple[int, int]) -> IntBox:
    h, w = shape[:2]
    x0, y0, x1, y1 = bbox
    x0 = max(0, int(x0) - pad)
    y0 = max(0, int(y0) - pad)
    x1 = min(w, int(x1) + pad)
    y1 = min(h, int(y1) + pad)
    return x0, y0, x1, y1


def _ring_stats(img: np.ndarray, box: IntBox, ring: int) -> Tuple[np.ndarray, float]:
    """Couleur médiane et écart-type des pixels formant un anneau autour de la zone."""
    x0, y0, x1, y1 = box
    h, w = img.shape[:2]
    ox0, oy0 = max(0, x0 - ring), max(0, y0 - ring)
    ox1, oy1 = min(w, x1 + ring), min(h, y1 + ring)

    outer = img[oy0:oy1, ox0:ox1]
    mask = np.ones(outer.shape[:2], dtype=bool)
    iy0, ix0 = y0 - oy0, x0 - ox0
    iy1, ix1 = iy0 + (y1 - y0), ix0 + (x1 - x0)
    mask[max(0, iy0):max(0, iy1), max(0, ix0):max(0, ix1)] = False

    ring_pixels = outer[mask]
    if ring_pixels.size == 0:
        return np.array([255, 255, 255], dtype=np.float64), 0.0

    median_color = np.median(ring_pixels.reshape(-1, ring_pixels.shape[-1]), axis=0)
    std = float(np.std(ring_pixels))
    return median_color, std


def clean_page_image(
    img: np.ndarray,
    bboxes: Sequence[BBox],
    pad: int = 4,
    ring: int = 12,
    uniform_std_threshold: float = 10.0,
    quality_std_threshold: float = 25.0,
    page_number: Optional[int] = None,
) -> Tuple[np.ndarray, List[str]]:
    """Efface le texte de l'image en reconstruisant le fond localement.

    Pour chaque bbox : si le fond environnant est uniforme (faible écart-type dans
    l'anneau autour de la zone), remplissage par la couleur médiane locale ; sinon
    (fond texturé/photo), `cv2.inpaint`. Retourne l'image nettoyée et une liste
    d'avertissements pour les zones dont le résultat est visuellement suspect.
    """
    warnings: List[str] = []
    if not bboxes:
        return img, warnings

    out = img.copy()
    inpaint_mask = np.zeros(img.shape[:2], dtype=np.uint8)
    inpaint_boxes: List[IntBox] = []

    for bbox in bboxes:
        box = _dilate_bbox(bbox, pad, img.shape)
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            continue

        median_color, std = _ring_stats(img, box, ring)

        if std <= uniform_std_threshold:
            out[y0:y1, x0:x1] = median_color.astype(np.uint8)
        else:
            inpaint_mask[y0:y1, x0:x1] = 255
            inpaint_boxes.append(box)

    if inpaint_boxes:
        out = cv2.inpaint(out, inpaint_mask, inpaintRadius=5, flags=cv2.INPAINT_TELEA)

    for box in inpaint_boxes:
        x0, y0, x1, y1 = box
        region = out[y0:y1, x0:x1]
        region_std = float(np.std(region)) if region.size else 0.0
        if region_std > quality_std_threshold:
            label = f"page {page_number}" if page_number is not None else "page inconnue"
            warnings.append(
                f"{label}: zone {box} pourrait présenter un nettoyage visuellement imparfait "
                f"(écart-type résiduel={region_std:.1f})."
            )

    return out, warnings
