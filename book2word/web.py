"""Interface web locale : mêmes fonctionnalités que la CLI/l'assistant, pilotées depuis un
navigateur. Ne tourne que sur 127.0.0.1 (jamais exposée sur le réseau) — pensée pour un poste
partagé où personne ne doit ouvrir de terminal.

Réutilise `cli.process_pdf` tel quel (aucune logique de traitement dupliquée) : cette page
web est juste une nouvelle façon d'afficher/piloter ce que l'assistant terminal fait déjà.
"""
import os
import platform
import subprocess
import re
import shutil
import threading
import time
import unicodedata
import uuid
import webbrowser
from typing import Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for

from book2word.cli import (
    DEBUG_DIR,
    DEFAULT_TEMPLATE_PATH,
    INPUT_DIR,
    OCR_LANGUAGES,
    OUTPUT_DIR,
    bundled_path,
    ensure_user_data_dirs,
    process_pdf,
    resolve_output_path,
    resolve_template_path,
    setup_file_logging,
)
from book2word.text_extract import preload_ocr

JOBS = {}
JOBS_LOCK = threading.Lock()

INACTIVITY_TIMEOUT_SECONDS = 20 * 60
_last_activity = time.time()


def _touch_activity() -> None:
    global _last_activity
    _last_activity = time.time()


def _any_job_running() -> bool:
    return bool(_running_jobs())


def _running_jobs():
    with JOBS_LOCK:
        return [
            {"id": job_id, "pdf_name": job["pdf_name"]}
            for job_id, job in JOBS.items()
            if job["status"] == "running"
        ]


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


def _safe_path_in(base_dir: str, filename: str) -> Optional[str]:
    """Reconstruit un chemin sûr dans `base_dir` à partir d'un nom de fichier envoyé par la page.

    `secure_filename()` de Werkzeug est trop agressif pour cet usage : il transforme par ex.
    "input3 (2).docx" (nommage auto-incrémenté généré par l'appli elle-même, voir
    `resolve_output_path`) en "input3_2.docx", qui ne correspond à aucun fichier réel — les
    boutons "Afficher dans le dossier"/"Supprimer" échouaient silencieusement sur ces fichiers.
    On ne garde que `os.path.basename()` (élimine tout `..`/séparateur de chemin) puis on
    vérifie que le résultat reste bien dans `base_dir`.
    """
    name = os.path.basename(filename)
    path = os.path.join(base_dir, name)
    if os.path.commonpath([os.path.abspath(path), os.path.abspath(base_dir)]) != os.path.abspath(base_dir):
        return None
    return path


_FORBIDDEN_NAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def _clean_upload_name(filename: str) -> str:
    """Nom de fichier sûr pour un PDF importé, en restant lisible.

    `secure_filename()` de Werkzeug transforme "Le Père Noël et l'ours.pdf" en
    "Le_Pere_Noel_et_lours.pdf" (accents, espaces et apostrophes supprimés), et le document
    Word généré hérite de ce nom. Ici on ne retire que ce qui est réellement dangereux ou
    interdit sur Windows/macOS : chemins, caractères de contrôle, `<>:"/\\|?*`, points en tête.
    """
    name = unicodedata.normalize("NFC", filename)  # macOS envoie parfois les accents décomposés
    name = re.split(r"[\\/]", name)[-1]
    name = _FORBIDDEN_NAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().lstrip(".").strip()
    stem, ext = os.path.splitext(name)
    stem = stem[:150].strip() or "document"
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = "_" + stem
    return stem + (ext.lower() if ext.lower() == ".pdf" else ext + ".pdf")


def _dir_size_mb(directory: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return round(total / (1024 * 1024), 1)


def _list_inputs():
    return _list_dir_entries(INPUT_DIR, ".pdf")


def _list_outputs():
    return _list_dir_entries(OUTPUT_DIR, ".docx")


def _run_job(job_id: str, pdf_path: str, output_path: str, options: dict) -> None:
    def set_stage(stage):
        with JOBS_LOCK:
            JOBS[job_id]["stage"] = stage

    def on_ocr_download(done_bytes, total_bytes):
        with JOBS_LOCK:
            JOBS[job_id]["stage"] = "ocr_download"
            JOBS[job_id]["download"] = {"done": done_bytes, "total": total_bytes}

    def on_page_done(page_num, total_pages):
        with JOBS_LOCK:
            JOBS[job_id]["current_page"] = page_num
            JOBS[job_id]["total_pages"] = total_pages
            if page_num == 0:
                JOBS[job_id]["pages_started_at"] = time.time()

    try:
        log_path = os.path.splitext(output_path)[0] + ".log"
        setup_file_logging(log_path)
        if options.get("force_ocr") or options.get("ocr_fallback"):
            # Chargé à part (comme dans ui.py) pour pouvoir afficher le téléchargement des
            # modèles au premier lancement, sans quoi la page semble figée plusieurs minutes.
            set_stage("ocr_loading")
            preload_ocr([options.get("ocr_lang", "fr")], on_download_progress=on_ocr_download)
        set_stage("pages")
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
    app.add_template_filter(lambda size_mb: f"{size_mb:.1f} Mo".replace(".", ","), "format_mb")
    ensure_user_data_dirs()

    @app.before_request
    def _mark_activity():
        _touch_activity()

    def _index_context(error=None):
        inputs = _list_inputs()
        return {
            "error": error,
            "inputs": inputs,
            "inputs_total_mb": round(sum(f["size_mb"] for f in inputs), 1),
            "debug_size_mb": _dir_size_mb(DEBUG_DIR),
            "outputs": _list_outputs(),
            "has_template": resolve_template_path(None) is not None,
            "default_template_name": os.path.basename(DEFAULT_TEMPLATE_PATH),
            "template_path": DEFAULT_TEMPLATE_PATH,
            "template_dir": os.path.dirname(DEFAULT_TEMPLATE_PATH),
            "output_dir": OUTPUT_DIR,
            "debug_dir": DEBUG_DIR,
            "ocr_languages": OCR_LANGUAGES,
            "running_jobs": _running_jobs(),
        }

    @app.route("/")
    def index():
        return render_template("index.html", **_index_context())

    @app.route("/convert", methods=["POST"])
    def convert():
        uploaded = request.files.get("upload")
        pdf_name = None

        if uploaded and uploaded.filename:
            pdf_name = _clean_upload_name(uploaded.filename)
            uploaded.save(os.path.join(INPUT_DIR, pdf_name))
        else:
            pdf_name = request.form.get("existing_pdf") or None
            if pdf_name and _safe_path_in(INPUT_DIR, pdf_name) is None:
                pdf_name = None

        if not pdf_name:
            context = _index_context(error="Choisissez ou importez un fichier PDF.")
            return render_template("index.html", **context), 400

        pdf_path = os.path.join(INPUT_DIR, pdf_name)
        output_path = resolve_output_path(pdf_path)

        book_type = request.form.get("book_type") or "mixed"
        if book_type not in ("mixed", "standard"):
            book_type = "mixed"

        options = {
            "dpi": int(request.form.get("dpi") or 300),
            "force_ocr": request.form.get("force_ocr") == "on",
            "ocr_fallback": True,
            "ocr_lang": request.form.get("ocr_lang") if request.form.get("ocr_lang") in OCR_LANGUAGES else "fr",
            "auto_crop": request.form.get("auto_crop") == "on",
            "debug": request.form.get("debug") == "on",
            "book_type": book_type,
        }

        job_id = uuid.uuid4().hex[:8]
        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "running",
                "stage": "starting",
                "download": None,
                "pages_started_at": None,
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
        payload = dict(job)
        # Temps écoulé calculé ici plutôt que dans le navigateur : aucune dépendance à l'horloge du client.
        started = job.get("pages_started_at")
        payload["pages_elapsed"] = time.time() - started if started else None
        return jsonify(payload)

    @app.route("/api/running")
    def api_running():
        return jsonify({"running": _running_jobs()})

    @app.route("/reveal/<path:filename>", methods=["POST"])
    def reveal(filename):
        path = _safe_path_in(OUTPUT_DIR, filename)
        if path is None or not os.path.isfile(path):
            return jsonify({"ok": False, "error": "not_found"}), 404
        opened = reveal_in_file_manager(path)
        return jsonify({"ok": opened, "path": path})

    @app.route("/reveal-template", methods=["POST"])
    def reveal_template():
        # Modèle absent : on ouvre au moins le dossier où le déposer.
        path = DEFAULT_TEMPLATE_PATH if os.path.isfile(DEFAULT_TEMPLATE_PATH) else os.path.dirname(DEFAULT_TEMPLATE_PATH)
        return jsonify({"ok": reveal_in_file_manager(path), "path": path})

    @app.route("/clear/<kind>", methods=["POST"])
    def clear(kind):
        """Suppression groupée des fichiers jetables (images de vérification, copies des PDF).

        Volontairement absent pour les documents Word générés : c'est le travail de
        l'utilisateur, souvent sa seule copie — suppression un par un uniquement.
        """
        running = _running_jobs()
        if kind == "debug":
            if running:
                return render_template(
                    "index.html", **_index_context(error="Attendez la fin de la conversion en cours avant de supprimer les images de vérification.")
                ), 409
            for entry in os.listdir(DEBUG_DIR) if os.path.isdir(DEBUG_DIR) else []:
                path = os.path.join(DEBUG_DIR, entry)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif not entry.startswith("."):
                    os.remove(path)
        elif kind == "inputs":
            in_use = {job["pdf_name"] for job in running}
            for f in _list_inputs():
                if f["name"] not in in_use:
                    os.remove(f["path"])
        else:
            return "Requête invalide", 400
        return redirect(url_for("index"))

    @app.route("/delete/<kind>/<path:filename>", methods=["POST"])
    def delete(kind, filename):
        base_dir = INPUT_DIR if kind == "input" else OUTPUT_DIR if kind == "output" else None
        if base_dir is None:
            return "Requête invalide", 400
        path = _safe_path_in(base_dir, filename)
        if path and os.path.isfile(path):
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
