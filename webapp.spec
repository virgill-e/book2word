# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller pour l'app de bureau (interface web locale).

Construit avec : pyinstaller webapp.spec --noconfirm
Sur macOS, produit dist/book2word.app ; sur Windows, dist/book2word/book2word.exe (+ ses
dépendances dans le même dossier — mode "onedir", plus fiable que "onefile" avec une pile
aussi lourde que torch/easyocr).
"""
import sys

from PyInstaller.utils.hooks import collect_all

datas = [
    ("book2word/templates", "book2word/templates"),
    ("book2word/static", "book2word/static"),
]
if __import__("os").path.isfile("template.docx"):
    datas.append(("template.docx", "."))

binaries = []
hiddenimports = []

for pkg in ("easyocr", "torch", "torchvision", "cv2", "pymupdf", "docx", "flask", "rich"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

a = Analysis(
    ["webapp.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="book2word",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="book2word",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="book2word.app",
        bundle_identifier="be.book2word.app",
        info_plist={"NSHighResolutionCapable": True},
    )
