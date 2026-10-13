"""Génère l'icône de l'app : livre ouvert d'où se lève un soleil (ASBL La Lumière).

Usage (depuis la racine du projet) : python assets/make_icon.py
Produit dans assets/ : icon.png (1024 px), icon.icns (macOS), icon.ico (Windows), et
book2word/static/favicon.png (onglet du navigateur + en-tête de l'interface web).
Les fichiers générés sont versionnés : la build n'a pas besoin de relancer ce script.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

S = 4  # sur-échantillonnage pour l'anticrénelage
W = 1024 * S
HERE = os.path.dirname(os.path.abspath(__file__))

BG_TOP, BG_BOTTOM = (38, 96, 80), (20, 58, 48)
YELLOW = (255, 210, 63)
PAGE = (255, 255, 255)
INK = (28, 70, 59)

img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

# Fond : carré arrondi (gabarit macOS : 824 px utiles sur 1024, rayon ~185).
m, side, r = 100 * S, 824 * S, 185 * S
grad = Image.new("RGBA", (W, W))
gd = ImageDraw.Draw(grad)
for y in range(W):
    t = min(max((y - m) / side, 0), 1)
    gd.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,))
mask = Image.new("L", (W, W), 0)
ImageDraw.Draw(mask).rounded_rectangle([m, m, m + side, m + side], radius=r, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
cx = W // 2
spine_top, spine_bottom = 560 * S, 800 * S

# Lumière : halo + rayons épais partant du haut du livre.
halo = Image.new("RGBA", (W, W), (0, 0, 0, 0))
ImageDraw.Draw(halo).ellipse([cx - 230 * S, spine_top - 290 * S, cx + 230 * S, spine_top + 170 * S], fill=YELLOW + (110,))
halo = halo.filter(ImageFilter.GaussianBlur(60 * S))
img.alpha_composite(Image.composite(halo, Image.new("RGBA", (W, W), (0, 0, 0, 0)), mask))
d = ImageDraw.Draw(img)

sun_c = (cx, spine_top + 10 * S)
# Rayons : capsules régulières (rectangle arrondi vertical, tourné autour du soleil).
for angle in range(-75, 76, 25):
    ray = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(ray).rounded_rectangle(
        [sun_c[0] - 22 * S, sun_c[1] - 300 * S, sun_c[0] + 22 * S, sun_c[1] - 175 * S], radius=22 * S, fill=YELLOW
    )
    img.alpha_composite(ray.rotate(-angle, center=sun_c, resample=Image.BICUBIC))
d = ImageDraw.Draw(img)
# Soleil : disque entier, sa moitié basse est cachée par le livre.
d.ellipse([sun_c[0] - 135 * S, sun_c[1] - 135 * S, sun_c[0] + 135 * S, sun_c[1] + 135 * S], fill=YELLOW)

# Livre ouvert : deux pages pleines, bord supérieur courbe.
def page(sign):
    pts = []
    outer_x = cx + sign * 330 * S
    for i in range(41):  # bord supérieur, du dos vers l'extérieur
        t = i / 40
        x = cx + sign * 330 * S * t
        y = spine_top + 10 * S - math.sin(t * math.pi) * 55 * S + t * 10 * S
        pts.append((x, y))
    pts += [(outer_x, spine_bottom - 20 * S)]
    for i in range(41):  # bord inférieur, de l'extérieur vers le dos
        t = 1 - i / 40
        x = cx + sign * 330 * S * t
        y = spine_bottom - 20 * S - math.sin(t * math.pi) * 40 * S + (1 - t) * 20 * S
        pts.append((x, y))
    return pts

# Tranche du livre (épaisseur jaune sous les pages, renforce la silhouette).
for sign in (-1, 1):
    base_pts = page(sign)
    d.polygon([(x, y + 26 * S) for x, y in base_pts], fill=YELLOW)
for sign in (-1, 1):
    d.polygon(page(sign), fill=PAGE)
# Dos du livre.
d.line([(cx, spine_top + 10 * S), (cx, spine_bottom)], fill=INK, width=14 * S)
# Lignes de texte épaisses (suggèrent le document), arrondies.
for sign in (-1, 1):
    for k, y in enumerate((640, 695, 750)):
        x1 = cx + sign * 70 * S
        x2 = cx + sign * (270 - (60 if k == 2 else 0)) * S
        y0 = y * S - math.sin(0.5 * math.pi) * 10 * S
        lo, hi = sorted((x1, x2))
        d.rounded_rectangle([lo, y0 - 11 * S, hi, y0 + 11 * S], radius=11 * S, fill=INK)

img = img.resize((1024, 1024), Image.LANCZOS)
img.save(os.path.join(HERE, "icon.png"))
img.save(os.path.join(HERE, "icon.icns"))
img.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((128, 128), Image.LANCZOS).save(os.path.join(HERE, "..", "book2word", "static", "favicon.png"))
