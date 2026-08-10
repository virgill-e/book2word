#!/usr/bin/env python3
"""Point d'entrée : python book2word.py livre.pdf sortie.docx [--dpi 300] [--ocr-fallback] [--debug]

Lancé sans argument : assistant interactif.
"""
import sys

try:
    from book2word.cli import main
except ImportError as exc:
    print(
        "Erreur : une dépendance requise est manquante ({}).\n"
        "Installez les dépendances avec :\n\n"
        "    pip install -r requirements.txt\n".format(exc.name or exc)
    )
    sys.exit(1)

if __name__ == "__main__":
    main()
