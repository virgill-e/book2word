"""Extraction du texte et des zones de texte (bounding boxes) d'un PDF via PyMuPDF."""
import functools
import http.client
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import pymupdf as fitz  # PyMuPDF

BBox = Tuple[float, float, float, float]

logger = logging.getLogger("book2word")


@dataclass
class TextBlock:
    bbox: BBox  # (x0, y0, x1, y1)
    text: str
    lines: List[str] = field(default_factory=list)


@dataclass
class PageText:
    page_number: int  # 0-indexé
    width: float
    height: float
    blocks: List[TextBlock]

    @property
    def has_text(self) -> bool:
        return any(b.text.strip() for b in self.blocks)


def extract_page_text(page: "fitz.Page") -> PageText:
    """Extrait les blocs de texte d'une page avec leur bounding box (coordonnées en points PDF).

    Le regroupement se fait par bloc PyMuPDF (paragraphe), pas ligne par ligne,
    afin de conserver les blocs multi-lignes comme une seule zone à traiter.
    """
    raw = page.get_text("dict")
    blocks: List[TextBlock] = []

    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # 0 = texte, 1 = image
            continue

        lines_text: List[str] = []
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")

        for line in block.get("lines", []):
            span_text = "".join(span.get("text", "") for span in line.get("spans", []))
            lx0, ly0, lx1, ly1 = line["bbox"]
            x0, y0 = min(x0, lx0), min(y0, ly0)
            x1, y1 = max(x1, lx1), max(y1, ly1)
            lines_text.append(span_text)

        block_text = "\n".join(lines_text).strip()
        if not block_text:
            continue

        blocks.append(TextBlock(bbox=(x0, y0, x1, y1), text=block_text, lines=lines_text))

    return PageText(page_number=page.number, width=page.rect.width, height=page.rect.height, blocks=blocks)


def _cluster_columns(bboxes: List[BBox]) -> List[List[int]]:
    """Regroupe des bbox en "colonnes" (chevauchement horizontal transitif).

    Retourne des groupes d'indices, chaque groupe trié du haut vers le bas, les groupes
    étant eux-mêmes triés de gauche à droite. Sert de base à l'ordre de lecture : sur une
    double page ou une mise en page multi-colonnes, deux textes à la même hauteur mais dans
    des colonnes différentes ne doivent pas être interclassés par un simple tri global sur Y.
    """
    n = len(bboxes)
    if n == 0:
        return []

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    by_x0 = sorted(range(n), key=lambda i: bboxes[i][0])
    for a in range(n):
        i = by_x0[a]
        x0i, _, x1i, _ = bboxes[i]
        for b in range(a + 1, n):
            j = by_x0[b]
            x0j, _, x1j, _ = bboxes[j]
            if x0j >= x1i:
                break  # trié par x0 : plus aucun chevauchement possible au-delà
            if min(x1i, x1j) - max(x0i, x0j) > 0:
                union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    columns = list(groups.values())
    columns.sort(key=lambda idxs: min(bboxes[i][0] for i in idxs))
    for idxs in columns:
        idxs.sort(key=lambda i: bboxes[i][1])
    return columns


def reading_order(bboxes: List[BBox]) -> List[int]:
    """Ordre de lecture : colonnes de gauche à droite, puis de haut en bas dans chaque colonne."""
    order: List[int] = []
    for column in _cluster_columns(bboxes):
        order.extend(column)
    return order


DownloadProgress = Callable[[int, int], None]  # (octets reçus, taille totale ou 0 si inconnue)

MODEL_DOWNLOAD_TIMEOUT_SECONDS = 30
MODEL_DOWNLOAD_ATTEMPTS = 5
_DOWNLOAD_CHUNK_SIZE = 256 * 1024

_easyocr_readers = {}  # un Reader par combinaison de langues (le jeu de caractères en dépend)
_easyocr_reader_lock = threading.Lock()


class ModelDownloadError(RuntimeError):
    """Échec du téléchargement des modèles OCR, avec un message lisible par un non-technicien."""


_DOWNLOAD_FAILED_MESSAGE = (
    "Impossible de télécharger le moteur de reconnaissance de texte. Une connexion internet "
    "est nécessaire lors de la première utilisation. Vérifiez la connexion puis relancez la "
    "conversion : le téléchargement reprendra là où il s'était arrêté."
)


def _download_to_part_file(url: str, part_path: str, on_progress: Optional[DownloadProgress]) -> None:
    """Télécharge `url` dans `part_path` en reprenant un fichier partiel existant (en-tête Range)."""
    already = os.path.getsize(part_path) if os.path.isfile(part_path) else 0
    headers = {"Range": f"bytes={already}-"} if already else {}
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=MODEL_DOWNLOAD_TIMEOUT_SECONDS
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and already:
            return  # fichier partiel déjà complet (interruption juste avant l'extraction)
        raise

    with response:
        if already and response.status == 206:
            done = already
            total_str = response.headers.get("Content-Range", "").rpartition("/")[2]  # "bytes 10-99/100"
            total = int(total_str) if total_str.isdigit() else 0
            mode = "ab"
        else:  # serveur sans reprise : on repart de zéro
            done = 0
            total = int(response.headers.get("Content-Length") or 0)
            mode = "wb"

        with open(part_path, mode) as out:
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)

    if total and done < total:
        raise OSError(f"téléchargement incomplet ({done}/{total} octets)")


def _download_and_unzip_resumable(url, filename, model_storage_directory, verbose=True, on_progress=None):
    """Remplace `easyocr.utils.download_and_unzip` (même signature).

    La version d'EasyOCR utilise `urlretrieve` sans délai d'expiration (une connexion figée
    bloque l'application indéfiniment), repart de zéro après chaque interruption et n'affiche
    sa progression que dans un terminal. Ici : délai d'expiration, nouvelles tentatives,
    reprise d'un téléchargement interrompu (même après fermeture de l'application) et
    extraction atomique (jamais de modèle à moitié écrit sous son nom final).
    """
    # Fichier temporaire de l'implémentation d'origine, laissé par une interruption.
    stale_temp = os.path.join(model_storage_directory, "temp.zip")
    if os.path.isfile(stale_temp):
        os.remove(stale_temp)

    part_path = os.path.join(model_storage_directory, filename + ".zip.part")
    for attempt in range(1, MODEL_DOWNLOAD_ATTEMPTS + 1):
        try:
            _download_to_part_file(url, part_path, on_progress)
            break
        except (OSError, http.client.HTTPException) as exc:  # URLError, timeout, coupure...
            logger.warning(
                "téléchargement de %s : tentative %s/%s échouée (%s).", filename, attempt, MODEL_DOWNLOAD_ATTEMPTS, exc
            )
            if attempt == MODEL_DOWNLOAD_ATTEMPTS:
                raise ModelDownloadError(_DOWNLOAD_FAILED_MESSAGE) from exc
            time.sleep(min(2 ** attempt, 15))

    final_path = os.path.join(model_storage_directory, filename)
    tmp_path = final_path + ".tmp"
    try:
        with zipfile.ZipFile(part_path) as archive, archive.open(filename) as src, open(tmp_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except (zipfile.BadZipFile, KeyError) as exc:
        # Archive corrompue : on la jette pour repartir de zéro au prochain essai.
        os.remove(part_path)
        raise ModelDownloadError(_DOWNLOAD_FAILED_MESSAGE) from exc
    os.replace(tmp_path, final_path)
    os.remove(part_path)


def _get_easyocr_reader(langs: List[str], on_download_progress: Optional[DownloadProgress] = None):
    key = tuple(langs)
    with _easyocr_reader_lock:  # deux conversions simultanées ne doivent pas télécharger en parallèle
        if key not in _easyocr_readers:
            import warnings

            import easyocr
            import easyocr.easyocr as easyocr_module

            original_download = easyocr_module.download_and_unzip
            easyocr_module.download_and_unzip = functools.partial(
                _download_and_unzip_resumable, on_progress=on_download_progress
            )
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")  # bruit de dépréciation interne à torch/easyocr
                    _easyocr_readers[key] = easyocr.Reader(langs, gpu=False, verbose=False)
            except AssertionError as exc:  # contrôle MD5 d'EasyOCR : fichier corrompu, re-téléchargé au prochain essai
                raise ModelDownloadError(_DOWNLOAD_FAILED_MESSAGE) from exc
            finally:
                easyocr_module.download_and_unzip = original_download
    return _easyocr_readers[key]


def preload_ocr(langs: Optional[List[str]] = None, on_download_progress: Optional[DownloadProgress] = None) -> None:
    """Charge le modèle EasyOCR à l'avance (permet d'afficher un indicateur de chargement dédié).

    Au premier lancement, EasyOCR télécharge ses modèles (~90 Mo) : `on_download_progress`
    reçoit alors (octets reçus, taille totale) pour chaque fichier téléchargé.
    """
    if langs is None:
        langs = ["fr"]
    _get_easyocr_reader(langs, on_download_progress)


def _merge_same_line_fragments(lines: List[dict]) -> List[dict]:
    """Fusionne les détections EasyOCR qui appartiennent à la même ligne de texte.

    EasyOCR découpe parfois une ligne en plusieurs morceaux (un par mot, ex. "Un" / "prince" /
    "grenouille !" au lieu d'une seule détection "Un prince grenouille !") — le découpage n'est
    pas prévisible, ça dépend de l'espacement/rendu. Sans cette fusion, un mot court isolé (ex.
    "Un") a une bbox quasi carrée et se fait rejeter par `_drop_implausible_lines` comme s'il
    s'agissait de bruit détecté sur l'illustration, alors que c'est du texte réel tronqué.

    Deux détections fusionnent si elles sont à une hauteur quasi identique (chevauchement
    vertical significatif) ET proches horizontalement (écart petit par rapport à leur hauteur,
    donc un espacement de mots normal — pas l'écart, bien plus grand, entre deux colonnes/pages).
    """
    n = len(lines)
    if n < 2:
        return lines

    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        x0i, y0i, x1i, y1i = lines[i]["bbox"]
        hi = y1i - y0i
        for j in range(i + 1, n):
            x0j, y0j, x1j, y1j = lines[j]["bbox"]
            hj = y1j - y0j
            vertical_overlap = min(y1i, y1j) - max(y0i, y0j)
            horizontal_gap = max(x0i, x0j) - min(x1i, x1j)
            if vertical_overlap > 0.5 * min(hi, hj) and horizontal_gap < 2 * max(hi, hj):
                union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    merged = []
    for idxs in groups.values():
        idxs.sort(key=lambda i: lines[i]["bbox"][0])
        x0 = min(lines[i]["bbox"][0] for i in idxs)
        y0 = min(lines[i]["bbox"][1] for i in idxs)
        x1 = max(lines[i]["bbox"][2] for i in idxs)
        y1 = max(lines[i]["bbox"][3] for i in idxs)
        text = " ".join(lines[i]["text"] for i in idxs)
        merged.append({"bbox": [x0, y0, x1, y1], "text": text})
    return merged


def _drop_implausible_lines(lines: List[dict]) -> List[dict]:
    """Écarte les détections EasyOCR qui ne peuvent pas être du texte réel.

    Sur des pages illustrées, le détecteur "voit" parfois du texte dans des traits de dessin
    (silhouettes d'arbres, plis) et lui associe un caractère au hasard, avec une bbox énorme
    et quasi carrée — sans rapport avec les vraies lignes de texte de la page, toujours bien
    plus larges que hautes. On compare chaque détection aux autres détections de la même page
    (hauteur médiane) plutôt qu'à un seuil absolu, pour rester générique quel que soit le DPI.
    """
    if len(lines) < 2:
        return lines

    heights = sorted((l["bbox"][3] - l["bbox"][1]) for l in lines)
    median_height = heights[len(heights) // 2]
    if median_height <= 0:
        return lines

    plausible = []
    for line in lines:
        x0, y0, x1, y1 = line["bbox"]
        width, height = x1 - x0, y1 - y0
        if height <= 0:
            continue
        aspect_ratio = width / height
        is_huge = height > 4 * median_height
        is_squarish = aspect_ratio < 1.5
        if is_huge or is_squarish:
            continue
        plausible.append(line)
    return plausible


def ocr_page_blocks(image_rgb, langs: Optional[List[str]] = None) -> List[TextBlock]:
    """Fallback OCR (EasyOCR) : détecte les lignes de texte puis les regroupe en blocs.

    EasyOCR a été retenu à la place de Tesseract : sur des pages illustrées avec du texte
    stylisé sur fond photographié, Tesseract en segmentation automatique ne détecte souvent
    rien (page jugée non textuelle) et en mode forcé confond des traits d'illustration avec
    du texte ; EasyOCR isole correctement le texte réel sans ce bruit sur ce type de page.

    Les bbox retournées sont en pixels, dans le référentiel de `image_rgb`.
    """
    if langs is None:
        langs = ["fr"]

    reader = _get_easyocr_reader(langs)
    results = reader.readtext(image_rgb)  # [(polygon, text, confidence), ...]

    lines = []
    for polygon, text, _confidence in results:
        text = text.strip()
        if not text:
            continue
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        lines.append({"bbox": [min(xs), min(ys), max(xs), max(ys)], "text": text})

    lines = _merge_same_line_fragments(lines)
    lines = _drop_implausible_lines(lines)
    if not lines:
        return []

    line_bboxes = [tuple(l["bbox"]) for l in lines]
    blocks: List[TextBlock] = []

    # Colonnes d'abord (chevauchement horizontal), puis fusion verticale à l'intérieur de
    # chaque colonne : deux lignes de colonnes différentes à hauteur quasi égale (ex. double
    # page) ne doivent jamais se retrouver dans le même bloc ni s'interclasser.
    for column in _cluster_columns(line_bboxes):
        current: Optional[dict] = None
        for i in column:
            x0, y0, x1, y1 = lines[i]["bbox"]
            line_height = y1 - y0
            if current is not None:
                gap = y0 - current["bbox"][3]
                same_block = gap < 0.6 * line_height
            else:
                same_block = False

            if same_block:
                current["lines"].append(lines[i]["text"])
                current["bbox"][0] = min(current["bbox"][0], x0)
                current["bbox"][1] = min(current["bbox"][1], y0)
                current["bbox"][2] = max(current["bbox"][2], x1)
                current["bbox"][3] = max(current["bbox"][3], y1)
            else:
                if current is not None:
                    blocks.append(TextBlock(bbox=tuple(current["bbox"]), text="\n".join(current["lines"]), lines=current["lines"]))
                current = {"bbox": [x0, y0, x1, y1], "lines": [lines[i]["text"]]}

        if current is not None:
            blocks.append(TextBlock(bbox=tuple(current["bbox"]), text="\n".join(current["lines"]), lines=current["lines"]))

    return blocks
