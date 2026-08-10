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

    @property
    def total_warnings(self) -> int:
        return sum(len(p.warnings) for p in self.pages)

    @property
    def pages_to_check(self) -> List[int]:
        return [p.page_number for p in self.pages if p.warnings or p.crop_abandoned]


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
    on_page_done: Optional[Callable[[int, int], None]] = None,
) -> Report:
    """Traite le PDF page par page. Le détail technique est journalisé (logger "book2word"),
    seul un rapport structuré est retourné pour l'affichage (laissé à l'appelant)."""
    document = fitz.open(pdf_path)
    total_pages = document.page_count
    debug_dir = "debug"
    if debug:
        os.makedirs(debug_dir, exist_ok=True)

    report = Report(output_path=output_path)
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

    build_docx(pages_out, output_path)
    document.close()

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
    parser.add_argument("output", nargs="?", help="Chemin du .docx de sortie")
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
    return parser


def main() -> None:
    if len(sys.argv) == 1:
        from book2word.ui import run_wizard

        run_wizard()
        return

    parser = _build_arg_parser()
    args = parser.parse_args()

    if not args.pdf or not args.output:
        parser.error("les arguments pdf et output sont requis (ou lancez sans argument pour l'assistant)")

    if not os.path.isfile(args.pdf):
        print(f"Erreur : fichier introuvable : {args.pdf}", file=sys.stderr)
        sys.exit(1)

    from book2word.ui import run_cli_with_progress

    run_cli_with_progress(
        args.pdf,
        args.output,
        dpi=args.dpi,
        ocr_fallback=args.ocr_fallback,
        debug=args.debug,
        auto_crop=not args.no_crop,
        ocr_lang=args.ocr_lang,
        force_ocr=args.force_ocr,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
