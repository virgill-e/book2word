"""Interface web locale : mêmes fonctionnalités que la CLI/l'assistant, pilotées depuis un
navigateur. Ne tourne que sur 127.0.0.1 (jamais exposée sur le réseau) — pensée pour un poste
partagé où personne ne doit ouvrir de terminal.

Réutilise `cli.process_pdf` tel quel (aucune logique de traitement dupliquée) : cette page
web est juste une nouvelle façon d'afficher/piloter ce que l'assistant terminal fait déjà.
"""
import os
import threading
import time
import uuid
import webbrowser
from typing import Optional

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from book2word.cli import (
    DEFAULT_TEMPLATE_PATH,
    INPUT_DIR,
    OUTPUT_DIR,
    list_input_pdfs,
    process_pdf,
    resolve_output_path,
    resolve_template_path,
    setup_file_logging,
)

JOBS = {}
JOBS_LOCK = threading.Lock()


def _list_outputs():
    if not os.path.isdir(OUTPUT_DIR):
        return []
    entries = []
    for name in sorted(os.listdir(OUTPUT_DIR)):
        if not name.lower().endswith(".docx"):
            continue
        path = os.path.join(OUTPUT_DIR, name)
        entries.append(
            {
                "name": name,
                "size_mb": round(os.path.getsize(path) / (1024 * 1024), 1),
                "mtime": os.path.getmtime(path),
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


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
    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            inputs=[os.path.basename(p) for p in list_input_pdfs()],
            outputs=_list_outputs(),
            has_template=resolve_template_path(None) is not None,
            default_template_name=os.path.basename(DEFAULT_TEMPLATE_PATH),
        )

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
            return render_template("index.html", error="Choisissez ou importez un fichier PDF.",
                                    inputs=[os.path.basename(p) for p in list_input_pdfs()],
                                    outputs=_list_outputs(),
                                    has_template=resolve_template_path(None) is not None,
                                    default_template_name=os.path.basename(DEFAULT_TEMPLATE_PATH)), 400

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

    @app.route("/download/<path:filename>")
    def download(filename):
        safe_name = secure_filename(filename)
        path = os.path.join(OUTPUT_DIR, safe_name)
        if not os.path.isfile(path):
            return "Fichier introuvable", 404
        return send_file(path, as_attachment=True)

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


def run_server(host: str = "127.0.0.1", port: int = 5057, open_browser: bool = True) -> None:
    app = create_app()
    url = f"http://{host}:{port}/"

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"book2word est lancé : {url}")
    print("Laissez cette fenêtre ouverte pendant l'utilisation. Fermez-la pour arrêter l'application.")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    run_server()
