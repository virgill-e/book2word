#!/usr/bin/env python3
"""Point d'entrée de l'interface web locale.

Double-cliquer sur ce fichier (ou son exécutable empaqueté) lance un serveur local
(127.0.0.1 uniquement — jamais exposé sur le réseau) et ouvre le navigateur automatiquement.
"""
import sys

try:
    from book2word.web import run_server
except ImportError as exc:
    print(
        "Erreur : une dépendance requise est manquante ({}).\n"
        "Installez les dépendances avec :\n\n"
        "    {} -m pip install -r requirements.txt\n".format(exc.name or exc, sys.executable)
    )
    sys.exit(1)

if __name__ == "__main__":
    run_server()
