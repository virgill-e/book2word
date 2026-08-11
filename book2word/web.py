"""Interface web locale : mêmes fonctionnalités que la CLI/l'assistant, pilotées depuis un
navigateur. Ne tourne que sur 127.0.0.1 (jamais exposée sur le réseau) — pensée pour un poste
partagé où personne ne doit ouvrir de terminal.

Réutilise `cli.process_pdf` tel quel (aucune logique de traitement dupliquée) : cette page
web est juste une nouvelle façon d'afficher/piloter ce que l'assistant terminal fait déjà.
"""
import os
import platform
import subprocess
import threading
import time
import uuid
import webbrowser
from typing import Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from book2word.cli import (
    DEFAULT_TEMPLATE_PATH,
    INPUT_DIR,
    OUTPUT_DIR,
    bundled_path,
    ensure_user_data_dirs,
    process_pdf,
    resolve_output_path,
    resolve_template_path,
    setup_file_logging,
)

JOBS = {}
JOBS_LOCK = threading.Lock()

INACTIVITY_TIMEOUT_SECONDS = 20 * 60
_last_activity = time.time()


def _touch_activity() -> None:
    global _last_activity
    _last_activity = time.time()


def _any_job_running() -> bool:
    with JOBS_LOCK:
        return any(job["status"] == "running" for job in JOBS.values())


def _watch_inactivity() -> None:
    """Éteint le serveur si personne n'a rien fait depuis longtemps et qu'aucun job ne tourne.

    Ferme l'onglet en oubliant de cliquer "Quitter" est le comportement attendu de la plupart
    des utilisateurs non techniques (voir discussion produit) — sur macOS, on ne peut pas
    fiablement s'appuyer sur une fenêtre de terminal à fermer (le bundle .app, seul moyen
    fiable de passer Gatekeeper, n'en a pas). Cet auto-arrêt évite qu'un process oublié tourne
    indéfiniment en arrière-plan, sans dépendre d'une action explicite de l'utilisateur.
    """
    while True:
        time.sleep(60)
        idle_for = time.time() - _last_activity
        if idle_for > INACTIVITY_TIMEOUT_SECONDS and not _any_job_running():
            print(
                f"book2word : arrêt automatique après {INACTIVITY_TIMEOUT_SECONDS // 60} "
                "minutes sans activité."
            )
            os._exit(0)


def reveal_in_file_manager(path: str) -> bool:
    """Ouvre le Finder (macOS) ou l'Explorateur (Windows) avec le fichier sélectionné.

    L'application étant strictement locale (même machine que le navigateur), c'est un vrai
    processus qui tourne dessus — contrairement à un site web classique, il peut piloter le
    gestionnaire de fichiers du système. Retourne False si la commande n'existe pas (OS non
    pris en charge) ; l'appelant doit alors afficher le chemin en repli.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["open", "-R", path])
        elif system == "Windows":
            subprocess.run(["explorer", f"/select,{path}"])
        elif system == "Linux":
            subprocess.run(["xdg-open", os.path.dirname(path)])
        else:
            return False
    except OSError:
        return False
    return True


def _list_dir_entries(directory: str, extension: str):
    if not os.path.isdir(directory):
        return []
    entries = []
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(extension):
            continue
        path = os.path.join(directory, name)
        entries.append(
            {
                "name": name,
                "path": path,
                "size_mb": round(os.path.getsize(path) / (1024 * 1024), 1),
                "mtime": os.path.getmtime(path),
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def _list_inputs():
    return _list_dir_entries(INPUT_DIR, ".pdf")


def _list_outputs():
    return _list_dir_entries(OUTPUT_DIR, ".docx")


def _run_job(job_id: str, pdf_path: str, output_path: str, options: dict) -> None:
    def on_page_done(page_num, total_pages):
        with JOBS_LOCK:
            JOBS[job_id]["current_page"] = page_num
            JOBS[job_id]["total_pages"] = total_pages

    try:
        log_path = os.path.splitext(output_path)[0] + ".log"
        setup_file_logging(log_path)
        report = process_pdf(pdf_path, output_path, on_page_done=on_page_done, **options)
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["report"] = {
                "output_path": report.output_path,
                "output_name": os.path.basename(report.output_path),
                "n_pages": len(report.pages),
                "pages_to_check": report.pages_to_check,
                "pages_not_cropped": report.pages_not_cropped,
            }
    except Exception as exc:  # noqa: BLE001 — le job doit signaler l'échec, jamais planter le thread
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(exc)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=bundled_path("book2word", "templates"),
        static_folder=bundled_path("book2word", "static"),
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    ensure_user_data_dirs()

    @app.before_request
    def _mark_activity():
        _touch_activity()

    def _index_context(error=None):
        return {
            "error": error,
            "inputs": _list_inputs(),
            "outputs": _list_outputs(),
            "has_template": resolve_template_path(None) is not None,
            "default_template_name": os.path.basename(DEFAULT_TEMPLATE_PATH),
        }

    @app.route("/")
    def index():
        return render_template("index.html", **_index_context())

    @app.route("/convert", methods=["POST"])
    def convert():
        uploaded = request.files.get("upload")
        pdf_name = None

        if uploaded and uploaded.filename:
            pdf_name = secure_filename(uploaded.filename)
            if not pdf_name.lower().endswith(".pdf"):
                pdf_name += ".pdf"
            uploaded.save(os.path.join(INPUT_DIR, pdf_name))
        else:
            pdf_name = request.form.get("existing_pdf") or None

        if not pdf_name:
            context = _index_context(error="Choisissez ou importez un fichier PDF.")
            return render_template("index.html", **context), 400

        pdf_path = os.path.join(INPUT_DIR, pdf_name)
        output_path = resolve_output_path(pdf_path)

        options = {
            "dpi": int(request.form.get("dpi") or 300),
            "force_ocr": request.form.get("force_ocr") == "on",
            "ocr_fallback": True,
            "ocr_lang": request.form.get("ocr_lang") or "fr",
            "auto_crop": request.form.get("auto_crop") == "on",
            "debug": request.form.get("debug") == "on",
        }

        job_id = uuid.uuid4().hex[:8]
        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "running",
                "current_page": 0,
                "total_pages": 0,
                "pdf_name": pdf_name,
                "output_path": output_path,
                "report": None,
                "error": None,
            }

        thread = threading.Thread(target=_run_job, args=(job_id, pdf_path, output_path, options), daemon=True)
        thread.start()

        return redirect(url_for("progress", job_id=job_id))

    @app.route("/progress/<job_id>")
    def progress(job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is None:
            return redirect(url_for("index"))
        return render_template("progress.html", job_id=job_id, pdf_name=job["pdf_name"])

    @app.route("/api/status/<job_id>")
    def api_status(job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is None:
            return jsonify({"status": "unknown"}), 404
        return jsonify(job)

    @app.route("/reveal/<path:filename>", methods=["POST"])
    def reveal(filename):
        safe_name = secure_filename(filename)
        path = os.path.join(OUTPUT_DIR, safe_name)
        if not os.path.isfile(path):
            return jsonify({"ok": False, "error": "not_found"}), 404
        opened = reveal_in_file_manager(path)
        return jsonify({"ok": opened, "path": path})

    @app.route("/delete/<kind>/<path:filename>", methods=["POST"])
    def delete(kind, filename):
        base_dir = INPUT_DIR if kind == "input" else OUTPUT_DIR if kind == "output" else None
        if base_dir is None:
            return "Requête invalide", 400
        safe_name = secure_filename(filename)
        path = os.path.join(base_dir, safe_name)
        if os.path.isfile(path):
            os.remove(path)
            log_path = os.path.splitext(path)[0] + ".log"
            if os.path.isfile(log_path):
                os.remove(log_path)
        return redirect(url_for("index"))

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
        return render_template("bye.html")

    return app


def _already_running(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def run_server(host: str = "127.0.0.1", port: int = 5057, open_browser: bool = True) -> None:
    url = f"http://{host}:{port}/"

    if _already_running(host, port):
        # Double-clic accidentel sur une instance déjà lancée : on rouvre juste la page
        # plutôt que d'échouer sur "port déjà utilisé", qui ne dirait rien à un utilisateur non technique.
        print(f"book2word tourne déjà : {url}")
        if open_browser:
            webbrowser.open(url)
        return

    app = create_app()
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    threading.Thread(target=_watch_inactivity, daemon=True).start()

    idle_minutes = INACTIVITY_TIMEOUT_SECONDS // 60
    print("=" * 60)
    print(f" book2word est lancé : {url}")
    print(' Pour arrêter : cliquez sur "Quitter l\'application" dans la page')
    print(f" (ou fermez cette fenêtre). Sinon, arrêt automatique après")
    print(f" {idle_minutes} minutes sans utilisation.")
    print("=" * 60)
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    run_server()
