# CLAUDE.md

Vue d'ensemble technique de `book2word`, pour quiconque (humain ou agent) doit faire évoluer
le code. Le [README.md](README.md) est le document utilisateur ; celui-ci est le document
mainteneur.

## Objectif

CLI qui transforme un PDF illustré (album, BD) en `.docx` : chaque page devient une image
nettoyée (texte imprimé effacé) suivie du texte extrait. Doit rester réutilisable pour
n'importe quel PDF de ce type — aucune valeur spécifique à un livre précis ne doit être
codée en dur ; les cas particuliers passent par des paramètres CLI, pas par du code.

## Architecture

```
book2word.py            point d'entrée, garde-fou ImportError
book2word/
  cli.py                orchestration : process_pdf() (logique) + argparse + logging fichier
  ui.py                 présentation console (rich) : assistant interactif + rendu classique
  text_extract.py       extraction texte natif (PyMuPDF) + fallback OCR (EasyOCR)
  image_clean.py        rasterisation page, recadrage auto, suppression du texte sur l'image
  docx_builder.py       construction du .docx (image + texte, page par page)
```

Flux par page (dans `cli.process_pdf`) :
1. `extract_page_text` (PyMuPDF) → blocs de texte natif + bbox, en points PDF.
2. `render_page` → rasterisation RGB au DPI demandé.
3. `autocrop_page` → recadrage des bordures sombres, ou abandon signalé (voir plus bas).
4. Texte : natif si présent et `--force-ocr` absent, sinon `ocr_page_blocks` (EasyOCR) si
   `--ocr-fallback`/`--force-ocr`, sinon page laissée sans texte.
5. `clean_page_image` → efface chaque bbox de texte sur l'image (couleur médiane locale ou
   `cv2.inpaint`), avec détection de résultat suspect.
6. `reading_order` → ordonne les blocs de texte pour la sortie (colonnes gauche→droite,
   haut→bas dans chaque colonne).
7. `docx_builder.build_docx` → image puis texte, page par page.

`cli.process_pdf` ne fait **aucun print** : il journalise (`logging.getLogger("book2word")`)
et retourne un `Report`/`PageReport` structuré. `ui.py` est seul responsable de l'affichage
(assistant interactif ou barre de progression classique) — cette séparation doit être
préservée pour garder l'outil scriptable sans dépendre du rendu console.

## Décisions techniques et pourquoi

- **EasyOCR plutôt que Tesseract** (`text_extract.ocr_page_blocks`) : sur des pages avec du
  texte stylisé sur fond illustré/photographié, Tesseract en segmentation automatique (`--psm
  3`) ne détecte souvent rien, et en mode forcé (`--psm 6`) confond des traits d'illustration
  avec du texte. EasyOCR isole correctement le texte réel sans ce bruit sur ce type de page
  (testé sur du matériel réel, voir historique du projet). Contrepartie : dépendance PyTorch
  (lourde à installer, lente sur CPU — environ 15–30 s/page).
- **Ordre de lecture par colonnes** (`text_extract._cluster_columns`/`reading_order`) : un tri
  global par position verticale seule interclasse à tort deux textes de colonnes différentes
  (page gauche/droite d'une double page) quand ils sont à des hauteurs presque égales. La
  fonction regroupe d'abord par chevauchement horizontal transitif (union-find), classe les
  colonnes de gauche à droite, puis trie chaque colonne de haut en bas. Utilisé à la fois pour
  l'ordre final des blocs et pour fusionner les lignes OCR en paragraphes.
- **Recadrage automatique qui s'abandonne plutôt que de mal recadrer**
  (`image_clean.autocrop_page`) : détecte le plus grand contour non sombre et recadre sur sa
  bounding box. Le ratio de sécurité (`max_area_ratio`) compare l'aire de la **bounding box**
  (pas l'aire du contour) à l'image totale : un objet qui touche la page (main, reflet) étire
  la bbox jusqu'aux bords même si sa propre surface reste modeste — c'est ce cas qu'il faut
  rejeter. Sur du matériel très hétérogène (PDF issus de captures vidéo/photo mal cadrées,
  contenu touchant des bords différents selon la page), ce garde-fou peut s'activer sur la
  quasi-totalité des pages : c'est le comportement voulu (mieux vaut ne pas recadrer que mal
  recadrer), pas un bug à corriger en assouplissant le seuil sans réflexion.
- **`--force-ocr`** : certains PDF (scan/photo/export de mauvaise qualité) ont une couche de
  texte natif *présente* mais sans rapport avec le texte visible (caractères isolés, police
  générique, positions aléatoires). `page_text.has_text` est alors vrai à tort et l'OCR ne se
  déclenche jamais par défaut. Ce flag ignore complètement le texte natif.
- **Nettoyage adaptatif** (`image_clean.clean_page_image`) : couleur médiane locale si le fond
  autour de la bbox est uniforme (écart-type faible), sinon `cv2.inpaint`. Une vérification
  post-nettoyage (écart-type résiduel dans la zone traitée) signale les zones suspectes au
  lieu d'échouer silencieusement — c'est une exigence produit, pas un détail : ne jamais
  supprimer cette vérification pour "nettoyer" les logs.

## Limites connues (ne pas re-découvrir à chaque fois)

- Le recadrage automatique ne peut pas séparer une page d'un objet qui la touche directement
  (main, reflet) par simple analyse couleur/luminosité — testé avec exclusion de teinte peau,
  détection de contours, projection par ligne/colonne : aucune approche classique ne résout ce
  cas de façon fiable sur du matériel très hétérogène. Une vraie amélioration demanderait soit
  une détection de rectangle de document robuste (type "scanner d'appli mobile", coûteux à
  développer et tester), soit accepter le recadrage manuel pour ces pages.
- Aucune suite de tests automatisés pour l'instant : la validation s'est faite par inspection
  visuelle des images `debug/*_before.png`/`*_after.png` et relecture du texte extrait dans le
  `.docx` généré, sur du matériel réel. Si des tests sont ajoutés, prévoir des PDF de fixture
  courts (2–3 pages) couvrant : page sans texte, page avec texte natif propre, page avec texte
  natif corrompu, page nécessitant l'OCR, double page à deux colonnes.
- `reading_order` suppose une mise en page en colonnes verticales distinctes ; une mise en
  page plus complexe (bulles de BD non linéaires, texte en diagonale) n'est pas modélisée.
- Pas de support GPU explicite pour EasyOCR (`gpu=False` en dur dans
  `text_extract._get_easyocr_reader`) — à activer si le public cible a des GPU disponibles et
  que la vitesse devient un problème réel.

## Pour étendre

- **Nouveau moteur OCR** : implémenter une fonction `ocr_page_blocks`-compatible (même
  signature de retour : `List[TextBlock]` en pixels) dans `text_extract.py`, brancher au même
  endroit que `_get_easyocr_reader`.
- **Ajuster la qualité du nettoyage** : les seuils sont des paramètres de
  `clean_page_image` (`pad`, `ring`, `uniform_std_threshold`, `quality_std_threshold`) — pas
  de valeurs cachées ailleurs.
- **Ajuster le recadrage** : paramètres de `autocrop_page` (`dark_threshold`,
  `min_area_ratio`, `max_area_ratio`).
- **Nouvelle option CLI** : ajouter l'argument dans `cli._build_arg_parser`, le paramètre
  correspondant dans `process_pdf`, et si pertinent la question associée dans
  `ui.run_wizard` (mode avancé) — les deux modes (flags et assistant) doivent rester
  cohérents.
- **Tests locaux rapides** : extraire 1–2 pages d'un PDF réel avec PyMuPDF
  (`doc.insert_pdf(src, from_page=i, to_page=i)`) plutôt que de retraiter un livre entier à
  chaque itération (le traitement complet avec OCR prend plusieurs minutes).
