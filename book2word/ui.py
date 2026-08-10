"""Présentation console (assistant interactif + affichage) pour les utilisateurs non techniques.

Ce module ne contient aucune logique de traitement : il pilote `cli.process_pdf` et affiche
un rendu clair (rich) pendant que le détail technique est journalisé dans un fichier .log.
"""
import logging
import os
import sys
import traceback

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm, IntPrompt, InvalidResponse, Prompt
from rich.table import Table

console = Console()


class Confirmation(Confirm):
    """Confirmation en français (o/n, "oui"/"non" acceptés) plutôt que y/n."""

    choices = ["o", "n"]
    validate_error_message = "[prompt.invalid]Merci de répondre par o (oui) ou n (non)"

    def process_response(self, value: str) -> bool:
        value = value.strip().lower()
        if value in ("o", "oui"):
            return True
        if value in ("n", "non"):
            return False
        raise InvalidResponse(self.validate_error_message)


BANNER = (
    "[bold cyan]book2word[/bold cyan] — transforme un livre PDF illustré en document Word\n"
    "[dim]image nettoyée (texte effacé) + texte extrait, page par page[/dim]"
)


def _log_path_for(output_path: str) -> str:
    base, _ = os.path.splitext(output_path)
    return f"{base}.log"


def _run_with_progress(pdf_path, output_path, console_, **kwargs):
    from book2word.cli import process_pdf
    from book2word.text_extract import preload_ocr

    if kwargs.get("force_ocr") or kwargs.get("ocr_fallback"):
        with console_.status("[cyan]Chargement du moteur de reconnaissance de texte (une seule fois)…[/cyan]"):
            preload_ocr([kwargs.get("ocr_lang", "fr")])

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("page {task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console_,
    ) as progress:
        task = progress.add_task("Traitement du PDF…", total=None)

        def on_page_done(page_num, total_pages):
            if progress.tasks[task].total is None:
                progress.update(task, total=total_pages)
            progress.update(task, completed=page_num)

        report = process_pdf(pdf_path, output_path, on_page_done=on_page_done, **kwargs)

    return report


def render_summary(report, console_: Console, log_path: str, debug: bool) -> None:
    size_mb = os.path.getsize(report.output_path) / (1024 * 1024) if os.path.isfile(report.output_path) else 0
    n_pages = len(report.pages)
    to_check = report.pages_to_check

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row("Fichier généré", f"[bold green]{report.output_path}[/bold green] ({size_mb:.1f} Mo)")
    table.add_row("Pages traitées", str(n_pages))
    if to_check:
        table.add_row(
            "Pages à vérifier",
            f"[yellow]{', '.join(str(p) for p in to_check)}[/yellow] (nettoyage ou recadrage incertain)",
        )
    else:
        table.add_row("Pages à vérifier", "[green]aucune[/green]")
    table.add_row("Modèle de mise en page", report.template_path or "aucun (police par défaut)")
    table.add_row("Détail technique", log_path)
    if debug:
        table.add_row("Images avant/après", "debug/")

    console_.print()
    console_.print(Panel(table, title="[bold]Terminé[/bold]", border_style="green"))
    if to_check:
        console_.print(
            "[dim]Astuce : ouvrez le .docx et vérifiez les pages listées ci-dessus — "
            "le nettoyage automatique peut y avoir laissé une trace.[/dim]"
        )


def _friendly_error(exc: Exception, log_path: str) -> None:
    hint = ""
    if isinstance(exc, ImportError):
        hint = "Une dépendance manque. Lancez : [bold]pip install -r requirements.txt[/bold]"
    elif isinstance(exc, FileNotFoundError):
        hint = "Vérifiez le chemin du fichier."
    elif "tesseract" in str(exc).lower() or "easyocr" in str(exc).lower():
        hint = "Problème avec le moteur de reconnaissance de texte. Vérifiez son installation."

    console.print()
    console.print(Panel(f"[bold red]Une erreur est survenue :[/bold red] {exc}", border_style="red"))
    if hint:
        console.print(hint)

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n--- ERREUR ---\n")
            f.write(traceback.format_exc())
        console.print(f"[dim]Détails techniques enregistrés dans {log_path}[/dim]")
    except OSError:
        pass


def run_cli_with_progress(pdf_path, output_path, verbose=False, **kwargs) -> None:
    """Utilisé par le mode ligne de commande (arguments classiques)."""
    from book2word.cli import setup_file_logging

    log_path = _log_path_for(output_path)
    setup_file_logging(log_path)
    if verbose:
        from rich.logging import RichHandler

        handler = RichHandler(console=console, show_time=False, show_path=False, markup=False)
        logging.getLogger("book2word").addHandler(handler)

    console.print(BANNER)
    try:
        report = _run_with_progress(pdf_path, output_path, console, **kwargs)
    except KeyboardInterrupt:
        console.print("\n[yellow]Traitement interrompu.[/yellow]")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 — on veut un message clair pour tout type d'échec
        _friendly_error(exc, log_path)
        sys.exit(1)

    render_summary(report, console, log_path, debug=kwargs.get("debug", False))


def _ask_pdf_path() -> str:
    while True:
        path = Prompt.ask("Chemin du fichier PDF à convertir").strip().strip('"').strip("'")
        if not os.path.isfile(path):
            console.print(f"[red]Fichier introuvable :[/red] {path}")
            continue
        try:
            import pymupdf as fitz

            fitz.open(path).close()
        except Exception:
            console.print(f"[red]Ce fichier ne semble pas être un PDF valide :[/red] {path}")
            continue
        return path


def _ask_output_path(pdf_path: str) -> str:
    default = os.path.splitext(pdf_path)[0] + ".docx"
    path = Prompt.ask("Nom du document Word à générer", default=default)
    if not path.lower().endswith(".docx"):
        path += ".docx"
    return path


def run_wizard() -> None:
    """Assistant interactif : pose quelques questions simples puis lance le traitement."""
    console.print(Panel(BANNER, border_style="cyan"))
    console.print(
        "[dim]Répondez aux quelques questions ci-dessous (une valeur par défaut est proposée "
        "entre crochets — appuyez sur Entrée pour l'accepter).[/dim]\n"
    )

    pdf_path = _ask_pdf_path()
    output_path = _ask_output_path(pdf_path)

    console.print(
        "\n[bold]Reconnaissance du texte[/bold] : par défaut, l'outil utilise le texte déjà "
        "présent dans le PDF quand il en trouve, et lit l'image (OCR) sinon. Si le texte "
        "obtenu est incohérent ou tronqué, forcez la lecture par image sur toutes les pages."
    )
    force_ocr = Confirmation.ask("Forcer la reconnaissance par image (OCR) sur toutes les pages ?", default=False)

    advanced = Confirmation.ask(
        "\nConfigurer les options avancées (résolution, langue, recadrage, débogage) ?", default=False
    )
    from book2word.cli import resolve_template_path

    if advanced:
        dpi = IntPrompt.ask("Résolution des images en points par pouce (plus haut = plus net mais plus lourd)", default=300)
        ocr_lang = Prompt.ask("Langue pour la reconnaissance de texte (code EasyOCR)", default="fr")
        auto_crop = Confirmation.ask("Recadrer automatiquement les bordures sombres des pages photographiées ?", default=True)
        debug = Confirmation.ask("Sauvegarder les images avant/après nettoyage pour vérification (dossier debug/) ?", default=False)
        default_template = resolve_template_path(None) or ""
        template_input = Prompt.ask(
            "Document .docx de base pour la police/mise en page (laisser vide pour aucun)",
            default=default_template,
        )
        template_path = template_input.strip() or None
    else:
        dpi, ocr_lang, auto_crop, debug = 300, "fr", True, False
        template_path = None

    resolved_template = resolve_template_path(template_path)

    console.print()
    recap = Table(show_header=False, box=None)
    recap.add_row("PDF source", pdf_path)
    recap.add_row("Document généré", output_path)
    recap.add_row("Reconnaissance de texte", "OCR forcé sur toutes les pages" if force_ocr else "automatique (texte natif puis OCR si besoin)")
    recap.add_row("Résolution", f"{dpi} dpi")
    recap.add_row("Recadrage automatique", "activé" if auto_crop else "désactivé")
    recap.add_row("Modèle de mise en page", resolved_template or "aucun (police par défaut)")
    console.print(Panel(recap, title="Récapitulatif", border_style="cyan"))

    if not Confirmation.ask("\nLancer le traitement ?", default=True):
        console.print("Annulé.")
        return

    log_path = _log_path_for(output_path)
    from book2word.cli import setup_file_logging

    setup_file_logging(log_path)

    console.print()
    try:
        report = _run_with_progress(
            pdf_path,
            output_path,
            console,
            dpi=dpi,
            ocr_fallback=True,
            force_ocr=force_ocr,
            auto_crop=auto_crop,
            ocr_lang=ocr_lang,
            debug=debug,
            template_path=template_path,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Traitement interrompu.[/yellow]")
        return
    except Exception as exc:  # noqa: BLE001
        _friendly_error(exc, log_path)
        return

    render_summary(report, console, log_path, debug=debug)
