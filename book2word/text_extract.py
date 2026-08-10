"""Extraction du texte et des zones de texte (bounding boxes) d'un PDF via PyMuPDF."""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pymupdf as fitz  # PyMuPDF

BBox = Tuple[float, float, float, float]


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


_easyocr_reader = None


def _get_easyocr_reader(langs: List[str]):
    global _easyocr_reader
    if _easyocr_reader is None:
        import warnings

        import easyocr

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # bruit de dépréciation interne à torch/easyocr
            _easyocr_reader = easyocr.Reader(langs, gpu=False, verbose=False)
    return _easyocr_reader


def preload_ocr(langs: Optional[List[str]] = None) -> None:
    """Charge le modèle EasyOCR à l'avance (permet d'afficher un indicateur de chargement dédié)."""
    if langs is None:
        langs = ["fr"]
    _get_easyocr_reader(langs)


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
