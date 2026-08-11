#!/usr/bin/env python3
"""Point d'entrée de l'interface web locale.

Double-cliquer sur ce fichier (ou son exécutable empaqueté) lance un serveur local
(127.0.0.1 uniquement — jamais exposé sur le réseau) et ouvre le navigateur automatiquement.

Les versions empaquetées (voir packaging/) tournent sans console (--windowed) : en cas
d'erreur au démarrage, on affiche une fenêtre de message plutôt que de laisser l'app
disparaître sans aucune explication.
"""
import sys


def _show_fatal_error(message: str) -> None:
    print(message, file=sys.stderr)
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("book2word", message)
    except Exception:
        pass  # pas d'affichage graphique possible (ex. lancé depuis un terminal sans display)


try:
    from book2word.web import run_server
except ImportError as exc:
    _show_fatal_error(
        "Une dépendance requise est manquante ({}).\n\n"
        "Installez les dépendances avec :\n"
        "{} -m pip install -r requirements.txt".format(exc.name or exc, sys.executable)
    )
    sys.exit(1)


if __name__ == "__main__":
    try:
        run_server()
    except Exception as exc:  # noqa: BLE001 — dernier filet avant une disparition silencieuse
        _show_fatal_error(f"book2word n'a pas pu démarrer : {exc}")
        sys.exit(1)
