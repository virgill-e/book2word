#!/usr/bin/env python3
"""CLI : transforme un PDF illustré (album) en document Word.

Chaque page devient : l'image nettoyée (texte effacé) suivie du texte extrait.

Usage:
    python book2word.py livre.pdf sortie.docx [--dpi 300] [--ocr-fallback] [--debug]

Lancé sans argument, le script démarre un assistant interactif (voir book2word/ui.py).
"""
import argparse
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import cv2
import pymupdf as fitz  # PyMuPDF

from book2word.docx_builder import PageContent, build_docx
from book2word.image_clean import autocrop_page, clean_page_image, render_page
from book2word.text_extract import extract_page_text, ocr_page_blocks, reading_order

BBox = tuple

logger = logging.getLogger("book2word")


def _is_frozen() -> bool:
    """True dans un exécutable empaqueté (PyInstaller), False en exécution depuis les sources."""
    return bool(getattr(sys, "frozen", False))


def bundled_path(*parts: str) -> str:
    """Chemin vers une ressource embarquée (le modèle par défaut) — PyInstaller l'extrait dans
    un dossier temporaire (`sys._MEIPASS`) ; en mode source, c'est simplement la racine du dépôt."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, *parts)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *parts)


if _is_frozen():
    # Une app empaquetée peut être lancée depuis n'importe où (Bureau, Téléchargements,
    # /Applications en lecture seule sur Mac...) : les fichiers de l'utilisateur doivent vivre
    # dans un dossier stable et inscriptible, pas "à côté" de l'exécutable.
    _PROJECT_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "book2word")
else:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_TEMPLATE_PATH = os.path.join(_PROJECT_ROOT, "template.docx")
INPUT_DIR = os.path.join(_PROJECT_ROOT, "input")
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")


def ensure_user_data_dirs() -> None:
    """Prépare input/ et output/ ; copie le modèle embarqué au premier lancement (app empaquetée).

    Sans effet notable en mode source : ces dossiers/le modèle existent déjà dans le dépôt.
    """
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if _is_frozen() and not os.path.isfile(DEFAULT_TEMPLATE_PATH):
        bundled_template = bundled_path("template.docx")
        if os.path.isfile(bundled_template):
            shutil.copyfile(bundled_template, DEFAULT_TEMPLATE_PATH)


def resolve_template_path(template_path: Optional[str]) -> Optional[str]:
    """Si aucun modèle n'est précisé, utilise `template.docx` à la racine du projet s'il existe."""
    if template_path:
        return template_path
    if os.path.isfile(DEFAULT_TEMPLATE_PATH):
        return DEFAULT_TEMPLATE_PATH
    return None


def list_input_pdfs() -> List[str]:
    """Liste les PDF disponibles dans le dossier input/ (chemins complets, triés par nom)."""
    if not os.path.isdir(INPUT_DIR):
        return []
    names = sorted(f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".pdf"))
    return [os.path.join(INPUT_DIR, name) for name in names]


def resolve_output_path(pdf_path: str, explicit_output: Optional[str] = None) -> str:
    """Chemin de sortie basé sur le nom du PDF, dans output/ — incrémenté s'il existe déjà.

    Ex. livre.pdf -> output/livre.docx, puis output/livre (2).docx si le premier existe.
    """
    if explicit_output:
        return explicit_output

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]

    candidate = os.path.join(OUTPUT_DIR, f"{base}.docx")
    if not os.path.exists(candidate):
        return candidate

    n = 2
    while True:
        candidate = os.path.join(OUTPUT_DIR, f"{base} ({n}).docx")
        if not os.path.exists(candidate):
            return candidate
        n += 1


@dataclass
class PageReport:
    page_number: int
    used_ocr: bool = False
    ocr_reason: str = ""
    crop_applied: bool = False
    crop_abandoned: bool = False
    no_text_found: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class Report:
    output_path: str
    pages: List[PageReport] = field(default_factory=list)
    template_path: Optional[str] = None

    @property
    def total_warnings(self) -> int:
        return sum(len(p.warnings) for p in self.pages)

    @property
    def pages_to_check(self) -> List[int]:
        """Pages où le nettoyage du texte est visuellement incertain — à ouvrir en priorité."""
        return [p.page_number for p in self.pages if p.warnings]

    @property
    def pages_not_cropped(self) -> List[int]:
        """Pages où le recadrage automatique s'est abstenu (image gardée telle quelle).

        Pas forcément un problème : sur un PDF sans bordure sombre à retirer, l'abstention
        est même le résultat correct (rien à recadrer).
        """
        return [p.page_number for p in self.pages if p.crop_abandoned]


def _scale_bbox(bbox: BBox, zoom: float) -> BBox:
    x0, y0, x1, y1 = bbox
    return (x0 * zoom, y0 * zoom, x1 * zoom, y1 * zoom)


def _offset_bbox(bbox: BBox, dx: float, dy: float) -> BBox:
    x0, y0, x1, y1 = bbox
    return (x0 - dx, y0 - dy, x1 - dx, y1 - dy)


def setup_file_logging(log_path: str) -> None:
    """Journalise le détail technique (zones, avertissements) dans un fichier, pas la console."""
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)


def process_pdf(
    pdf_path: str,
    output_path: str,
    dpi: int = 300,
    ocr_fallback: bool = False,
    debug: bool = False,
    auto_crop: bool = True,
    ocr_lang: str = "fr",
    force_ocr: bool = False,
    template_path: Optional[str] = None,
    on_page_done: Optional[Callable[[int, int], None]] = None,
) -> Report:
    """Traite le PDF page par page. Le détail technique est journalisé (logger "book2word"),
    seul un rapport structuré est retourné pour l'affichage (laissé à l'appelant)."""
    template_path = resolve_template_path(template_path)
    document = fitz.open(pdf_path)
    total_pages = document.page_count
    debug_dir = "debug"
    if debug:
        os.makedirs(debug_dir, exist_ok=True)

    report = Report(output_path=output_path, template_path=template_path)
    pages_out = []
    zoom = dpi / 72.0

    for page in document:
        page_num = page.number + 1
        page_report = PageReport(page_number=page_num)

        page_text = extract_page_text(page)
        img = render_page(page, dpi)

        crop_offset = (0, 0)
        if auto_crop:
            cropped, crop_box = autocrop_page(img)
            if crop_box is not None:
                img = cropped
                crop_offset = (crop_box[0], crop_box[1])
                page_report.crop_applied = True
            else:
                page_report.crop_abandoned = True
                logger.warning(
                    "page %s: recadrage automatique abandonné (zone détectée non fiable, "
                    "ex. main/reflet touchant la page) — image conservée entière.",
                    page_num,
                )

        if debug:
            cv2.imwrite(
                os.path.join(debug_dir, f"page_{page_num:03d}_before.png"),
                cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
            )

        if page_text.has_text and not force_ocr:
            bboxes = [
                _offset_bbox(_scale_bbox(b.bbox, zoom), crop_offset[0], crop_offset[1])
                for b in page_text.blocks
            ]
            texts = [b.text for b in page_text.blocks]
        elif ocr_fallback or force_ocr:
            page_report.used_ocr = True
            page_report.ocr_reason = "--force-ocr" if force_ocr and page_text.has_text else "pas de couche texte native"
            ocr_blocks = ocr_page_blocks(img, langs=[ocr_lang])
            bboxes = [b.bbox for b in ocr_blocks]
            texts = [b.text for b in ocr_blocks]
        else:
            bboxes, texts = [], []
            page_report.no_text_found = True
            logger.info(
                "page %s: pas de couche texte détectée, image conservée telle quelle "
                "(--ocr-fallback pour tenter une extraction OCR).",
                page_num,
            )

        if bboxes:
            cleaned, warnings = clean_page_image(img, bboxes, page_number=page_num)
            for w in warnings:
                logger.warning(w)
            page_report.warnings = warnings
        else:
            cleaned = img

        if debug:
            cv2.imwrite(
                os.path.join(debug_dir, f"page_{page_num:03d}_after.png"),
                cv2.cvtColor(cleaned, cv2.COLOR_RGB2BGR),
            )

        order = reading_order(bboxes)
        ordered_texts = [texts[i] for i in order]

        if page_report.used_ocr:
            logger.info("page %s: texte extrait via OCR (%s).", page_num, page_report.ocr_reason)

        pages_out.append(PageContent(image=cleaned, texts=ordered_texts, page_number=page_num))
        report.pages.append(page_report)

        if on_page_done:
            on_page_done(page_num, total_pages)

    build_docx(pages_out, output_path, template_path=template_path)
    document.close()

    if template_path:
        logger.info("Modèle utilisé : %s", template_path)
    logger.info("Document généré : %s (%s page(s))", output_path, len(pages_out))
    return report


def _build_arg_parser() -> argparse.ArgumentParser:
    epilog = """\
Exemples :
  python book2word.py livre.pdf sortie.docx
  python book2word.py livre.pdf sortie.docx --force-ocr --debug
  python book2word.py                              (assistant interactif)

Guide rapide :
  Le PDF a du texte sélectionnable et net dans le résultat ?
      -> ne rien ajouter, l'outil le détecte automatiquement.
  Le texte du .docx généré est incohérent, tronqué ou absent ?
      -> ajoutez --force-ocr (relit toutes les pages par reconnaissance d'image).
  Le PDF vient d'un scan/photo/vidéo avec des bordures noires autour des pages ?
      -> le recadrage automatique est actif par défaut ; désactivez-le avec --no-crop
         s'il abîme des pages (vérifiable avec --debug).
  Besoin de voir le détail (zones nettoyées, décisions page par page) ?
      -> consultez book2word.log généré à côté du .docx.
"""
    parser = argparse.ArgumentParser(
        prog="book2word",
        description="Transforme un PDF illustré en document Word (image nettoyée + texte, page par page).",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", nargs="?", help="Chemin du PDF source")
    parser.add_argument(
        "output", nargs="?", help="Chemin du .docx de sortie (défaut : output/<nom_du_pdf>.docx)"
    )
    parser.add_argument("--dpi", type=int, default=300, help="Résolution de rasterisation (défaut : 300)")
    parser.add_argument(
        "--ocr-fallback",
        action="store_true",
        help="Active l'OCR (EasyOCR) pour les pages sans couche texte détectable",
    )
    parser.add_argument(
        "--ocr-lang",
        default="fr",
        help="Code langue EasyOCR pour l'OCR fallback (défaut : fr)",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help=(
            "Ignore la couche de texte native et utilise l'OCR (EasyOCR) sur toutes les pages. "
            "Utile quand la couche native existe mais est corrompue/sans rapport avec le texte "
            "visible (scans/exports de mauvaise qualité)."
        ),
    )
    parser.add_argument(
        "--no-crop",
        action="store_true",
        help="Désactive le recadrage automatique des pages photographiées (bordures sombres)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Sauvegarde les images avant/après nettoyage dans un dossier debug/",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Affiche aussi le détail technique dans la console (par défaut : dans book2word.log uniquement)",
    )
    parser.add_argument(
        "--template",
        default=None,
        help=(
            "Document .docx de base à utiliser pour la police/mise en page (défaut : "
            f"{os.path.basename(DEFAULT_TEMPLATE_PATH)} à la racine du projet, si présent)"
        ),
    )
    return parser


def main() -> None:
    ensure_user_data_dirs()

    if len(sys.argv) == 1:
        from book2word.ui import run_wizard

        run_wizard()
        return

    parser = _build_arg_parser()
    args = parser.parse_args()

    if not args.pdf:
        parser.error("l'argument pdf est requis (ou lancez sans argument pour l'assistant)")

    if not os.path.isfile(args.pdf):
        print(f"Erreur : fichier introuvable : {args.pdf}", file=sys.stderr)
        sys.exit(1)

    output_path = resolve_output_path(args.pdf, args.output)

    from book2word.ui import run_cli_with_progress

    run_cli_with_progress(
        args.pdf,
        output_path,
        dpi=args.dpi,
        ocr_fallback=args.ocr_fallback,
        debug=args.debug,
        auto_crop=not args.no_crop,
        ocr_lang=args.ocr_lang,
        force_ocr=args.force_ocr,
        verbose=args.verbose,
        template_path=args.template,
    )


if __name__ == "__main__":
    main()
