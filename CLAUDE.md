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
book2word.py            point d'entrée CLI/assistant terminal, garde-fou ImportError
webapp.py               point d'entrée interface web locale, garde-fou ImportError
book2word/
  cli.py                orchestration : process_pdf() (logique) + argparse + logging fichier
  ui.py                 présentation console (rich) : assistant interactif + rendu classique
  web.py                interface web locale (Flask, 127.0.0.1 uniquement) : mêmes options,
                        pilotées par formulaire ; jobs de conversion en thread + polling JSON
  templates/, static/   pages HTML et CSS de l'interface web
  text_extract.py       extraction texte natif (PyMuPDF) + fallback OCR (EasyOCR)
  image_clean.py        rasterisation page, recadrage auto, suppression du texte sur l'image
  docx_builder.py       construction du .docx (image + texte, page par page)
input/                  PDF à convertir, listés et choisis par numéro (assistant/web)
output/                 .docx + .log générés, nommés d'après le PDF (incrémenté si collision)
template.docx           police/mise en page par défaut du .docx généré (optionnel)
assets/                 icône de l'app (icon.png/.icns/.ico) + make_icon.py qui les génère
```

`web.py` ne duplique aucune logique : il appelle `cli.process_pdf`/`resolve_output_path`/
`resolve_template_path` exactement comme `ui.py`, seul le rendu diffère (HTML + JSON au lieu
de rich). Un nouveau mode de pilotage (CLI, terminal riche, web, futur GUI natif) doit toujours
passer par ces mêmes fonctions de `cli.py` plutôt que ré-implémenter le flux de traitement.

Le serveur Flask n'écoute que sur `127.0.0.1` (jamais `0.0.0.0`) — c'est une exigence produit,
pas un détail : les livres traités peuvent être sous droits d'auteur, rien ne doit être
accessible depuis le réseau. Un job de conversion tourne dans un `threading.Thread` séparé
(le traitement dure plusieurs minutes) ; l'état est dans `JOBS` (dict protégé par `JOBS_LOCK`)
et interrogé par le navigateur via `/api/status/<job_id>` (polling JS toutes les secondes,
pas de websocket). `TEMPLATES_AUTO_RELOAD=True` est activé explicitement : sans ça, Flask ne
recharge pas les templates quand `debug=False`, ce qui a piégé le développement initial
(modification de `base.html` invisible sans redémarrage du serveur — les changements dans les
fichiers `.py`, eux, nécessitent toujours un redémarrage).

Choix d'interface pour un public non technique (ne pas les défaire sans raison) :
- Langue OCR : liste fermée `cli.OCR_LANGUAGES` (web et assistant), jamais un champ libre — une
  saisie comme "français" faisait échouer EasyOCR avec un message incompréhensible. La CLI à
  flags (`--ocr-lang`) reste libre pour les usages avancés. `text_extract` garde un `Reader` par
  combinaison de langues (le jeu de caractères en dépend).
- Noms des PDF importés : `web._clean_upload_name`, pas `secure_filename()` de Werkzeug, qui
  supprimait accents, espaces et apostrophes ("Le Père Noël" → "Le_Pere_Noel"), et le `.docx`
  généré héritait de ce nom. Seuls chemins, caractères interdits sous Windows/macOS et noms
  réservés Windows sont neutralisés.
- Listes de fichiers (`web._list_dir_entries`) : les verrous Word `~$nom.docx` (présents tant
  qu'un document est ouvert) et les fichiers cachés (`._nom` de macOS) sont exclus.
- Les noms de fichiers ne sont jamais insérés dans du JavaScript inline (`'{{ f.name }}'`) : une
  apostrophe cassait le bouton "Afficher" et faisait sauter la confirmation de suppression. On
  passe par `data-name` + `this.dataset.name`.
- Progression : `process_pdf` appelle `on_page_done(0, total)` avant la première page, pour
  afficher "Page 1/N" dès le début. Volontairement **aucune estimation de temps restant** ni de
  durée par page : elle dépend trop de l'ordinateur (CPU, OCR) et s'est révélée trompeuse.
- Espace disque : `debug/<nom du livre>/` (vidé à chaque nouvelle conversion de ce livre) et
  boutons de suppression groupée (`/clear/debug`, `/clear/inputs`) avec l'espace occupé affiché.
  Volontairement **pas** de suppression groupée des `.docx` générés (travail de l'utilisateur,
  souvent sa seule copie). Le bouton "Supprimer ces images" est dans le `<form>` de conversion :
  il passe par l'attribut `form="clear-debug-form"` (formulaires imbriqués invalides en HTML).
- Le chemin de `template.docx` est toujours affiché (avec "Afficher le modèle dans le dossier") :
  l'app en copie un au premier lancement, c'est donc surtout pour le modifier qu'on le cherche.
- "Quitter" interroge `/api/running` au moment du clic et prévient si une conversion tourne ;
  l'accueil liste les conversions en cours (onglet fermé par erreur → on peut y revenir).

Pas de bouton "télécharger" : comme le serveur et le navigateur tournent sur la même machine
(contrainte produit, pas juste choix technique — voir plus haut), `web.reveal_in_file_manager`
appelle directement `open -R` (macOS) / `explorer /select,` (Windows) / `xdg-open` (Linux) en
sous-processus pour ouvrir le gestionnaire de fichiers natif sur le fichier généré. Le chemin
complet est de toute façon toujours affiché en clair dans la page (repli si la commande échoue
ou sur un OS non couvert) — ne jamais compter uniquement sur la commande native qui peut varier
selon la configuration du poste.

`input/`, `output/` et le contenu de `debug/` ne sont jamais versionnés (`.gitignore`) — seuls
des `.gitkeep` gardent les deux premiers dossiers présents après un clone. `cli.list_input_pdfs`,
`cli.resolve_output_path` et `cli.DEBUG_DIR` résolvent ces chemins via `_PROJECT_ROOT`, pas depuis
le répertoire courant (une app lancée depuis le Finder a `/`, en lecture seule, comme répertoire
courant : un `debug/` relatif y faisait échouer la conversion) — l'outil se comporte donc pareil quel que soit l'endroit d'où il est lancé. Ce chemin
dépend du mode d'exécution (`cli._is_frozen()`) : à côté du code source en développement, mais
dans `~/Documents/book2word/` pour une build empaquetée (voir Empaquetage plus bas) — une app
double-cliquable peut être lancée depuis n'importe où, y compris des dossiers en lecture seule.

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
7. `docx_builder.build_docx` → image puis texte, page par page. Si un `template.docx` existe
   à la racine du projet (ou `--template <chemin>`), il sert de document de base : le style
   "Normal" du modèle fournit la police/mise en page, sans qu'aucun code de style ne soit
   nécessaire côté `docx_builder` (héritage natif de python-docx). Résolu par
   `cli.resolve_template_path`.

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

## Empaquetage (app de bureau Mac/Windows)

`webapp.spec` (PyInstaller) construit une app de bureau autour de `webapp.py` : mode "onedir"
(pas "onefile") volontairement — plus fiable et plus rapide au démarrage qu'un onefile avec
une pile aussi lourde que torch/easyocr (build ~800 Mo). Sur macOS ça produit un vrai bundle
`.app` (bloc `BUNDLE` du spec, actif seulement si `sys.platform == "darwin"`) ; sur Windows,
un dossier `book2word/` contenant `book2word.exe` et ses dépendances — c'est ce dossier qu'il
faut distribuer en entier (zippé), pas juste l'exe seul.

`.github/workflows/build.yml` construit les deux (`macos-latest`/`windows-latest`), zippe
chaque résultat et republie une release GitHub nommée `latest` (supprimée/recréée à chaque
run — pas de gestion de versions pour l'instant, une seule release "dernière version" à jour
sur `main`). Déclenchement : manuel (`workflow_dispatch`, ou `gh workflow run build.yml`) ou
push d'un tag `v*`.

Points qui ont nécessité un vrai travail (ne pas les défaire par erreur) :
- **Pas de fenêtre de terminal sur macOS, et c'est volontaire.** Un script `.command` qui
  lance l'exécutable pour donner une fenêtre "à fermer pour quitter" a été essayé puis
  abandonné : testé avec un vrai flag de quarantaine (`xattr -w com.apple.quarantine`,
  simulant un téléchargement réel) + `spctl -a -vv` et exécution directe, Gatekeeper bloque
  silencieusement ce chemin (pas le dialogue habituel "développeur non identifié" qu'on a sur
  un vrai `.app` ouvert depuis Finder). Le bundle `.app` (bloc `BUNDLE`, actif seulement si
  `sys.platform == "darwin"`) reste donc le seul chemin macOS fiable, sans console visible.
- **`_watch_inactivity`** (dans `web.py`) résout le vrai problème à la place d'une fenêtre à
  fermer : le serveur s'arrête seul après `INACTIVITY_TIMEOUT_SECONDS` (20 min) sans requête
  HTTP et sans job de conversion en cours — vérifié qu'aucune requête ne réinitialise le
  minuteur sauf via `before_request`, et que la vérification (toutes les 60s) ignore bien un
  job actif même si personne n'interagit avec la page pendant qu'il tourne. Bouton "Quitter"
  toujours disponible en plus, pour un arrêt immédiat et volontaire.
- **Icône** : `assets/icon.icns` (bundle macOS) / `assets/icon.ico` (exe Windows), branchées via
  `icon=` dans `EXE` et `BUNDLE`. Générées par `assets/make_icon.py` (Pillow, dessin vectoriel
  sur-échantillonné) qui produit aussi `book2word/static/favicon.png` (onglet + en-tête web).
  Les fichiers générés sont versionnés : la CI ne relance pas le script. Pour modifier l'icône,
  éditer le script, le relancer (`python assets/make_icon.py`) et committer les sorties.
  Motif : livre ouvert + soleil levant, pour l'ASBL La Lumière (utilisée par ses membres).
- `console=True` dans l'EXE du spec ne change rien sur macOS (Finder ne rattache jamais de
  terminal à un `.app`, cette option ne pilote que Windows/Linux) mais donne une console
  visible sur Windows — laissé tel quel, ça ne coûte rien et peut aider au diagnostic.
- `webapp.py` intercepte toute exception fatale au démarrage et l'affiche via une boîte de
  dialogue Tkinter (`_show_fatal_error`) : utile en particulier sur macOS où il n'y a pas de
  console visible pour voir un traceback.
- `web._already_running` : si le port 127.0.0.1:5057 répond déjà, on rouvre juste le navigateur
  au lieu de planter sur "port déjà utilisé" — cas fréquent (double-clic accidentel deux fois).
- Les modèles EasyOCR (détecteur CRAFT ~80 Mo + reconnaissance `latin_g2` ~15 Mo) ne sont
  **pas** embarqués dans le build : ils se téléchargent au premier lancement de l'OCR, comme
  en mode source. Ne pas présenter ça comme un bug — c'est documenté au README comme un besoin
  de connexion internet ponctuel, pas une régression.
- **Téléchargement des modèles EasyOCR remplacé** (`text_extract._download_and_unzip_resumable`,
  substitué à `easyocr.easyocr.download_and_unzip` le temps de construire le `Reader`) :
  l'implémentation d'origine (`urlretrieve`) n'a aucun délai d'expiration (connexion figée =
  app bloquée indéfiniment), repart de zéro après chaque interruption, et n'affiche sa
  progression que dans un terminal — sur l'app de bureau, l'utilisateur voyait une page figée
  plusieurs minutes et pensait que l'app avait planté. La version de remplacement : délai
  d'expiration, nouvelles tentatives, reprise via en-tête `Range` depuis un `<modèle>.zip.part`
  (y compris après fermeture de l'app), extraction atomique, et rappel de progression affiché
  par `web.py`/`ui.py`. Elle dépend du nom interne `easyocr.easyocr.download_and_unzip` : à
  revérifier en cas de montée de version majeure d'EasyOCR.
- Testé localement avant tout push CI (`pyinstaller webapp.spec --noconfirm` puis lancer
  `dist/book2word.app/Contents/MacOS/book2word` directement pour voir les logs) : la build
  fonctionne de bout en bout sur macOS, OCR compris — c'est le point le plus susceptible de
  casser silencieusement (imports cachés manquants pour torch/easyocr) si on modifie le spec.
  Toujours refaire ce test après une modification du spec, avant de déclencher la CI.

## Installation depuis les sources et ligne de commande

Le README est volontairement limité à l'app téléchargée et à l'interface web (public non
technique) ; cette section regroupe ce qui concerne le développement.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
python3 -m pip install -r requirements.txt   # télécharge PyTorch (plusieurs centaines de Mo)
```

Python 3.9+. Aucun logiciel externe (pas de Tesseract). `python3 -m pip` plutôt que `pip` :
sur macOS, `pip` seul n'existe souvent pas. Les modèles EasyOCR (~90 Mo) se téléchargent à la
première conversion OCR.

En mode source, `input/`, `output/`, `debug/` et `template.docx` sont à la racine du dépôt
(pas dans `~/Documents/book2word/`, réservé à la build empaquetée).

- Interface web : `python3 webapp.py` (http://127.0.0.1:5057).
- Assistant terminal : `python3 book2word.py` (liste les PDF de `input/`, pose les questions).
- Ligne de commande : `python3 book2word.py input/livre.pdf [sortie.docx]`. Options principales :
  `--book-type standard` (image à droite / texte à gauche ; défaut `mixed`), `--force-ocr`,
  `--ocr-fallback` (OCR seulement pour les pages sans texte natif), `--dpi 300`, `--no-crop`,
  `--debug`, `--verbose`, `--template modele.docx`, `--ocr-lang fr`. Liste complète : `--help`.

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
