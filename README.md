# book2word

Transforme un livre PDF illustré (album, BD, livre jeunesse) en document Word : chaque page
devient une image nettoyée (le texte imprimé dessus est effacé) suivie du texte de la page,
prêt à relire ou modifier dans Word.

Pas besoin de connaissances techniques pour l'utiliser : un assistant vous guide pas à pas.

---

## 1. Installation (à faire une seule fois)

### Étape 1 — Installer Python

Vérifiez si Python est déjà installé en ouvrant un terminal et en tapant :

```bash
python3 --version
```

Si une version 3.9 ou plus s'affiche, c'est bon, passez à l'étape 2. Sinon, installez Python
depuis [python.org/downloads](https://www.python.org/downloads/) (choisissez la version pour
votre système, puis suivez l'installateur).

Il faut aussi installer **Tesseract**... non — ce n'est **pas** nécessaire : book2word lit le
texte des images lui-même (voir plus bas), aucun logiciel externe à installer.

### Étape 2 — Récupérer le projet et installer ses dépendances

Ouvrez un terminal dans le dossier `book2word` (celui qui contient ce fichier), puis :

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Sous Windows (invite de commandes) remplacez la 2ᵉ ligne par : `.venv\Scripts\activate`

On utilise `python3 -m pip` plutôt que `pip` tout court : sur certains systèmes (macOS en
particulier), la commande `pip` seule n'existe pas telle quelle même quand pip est bien
installé — `python3 -m pip` fonctionne toujours.

Cette étape télécharge notamment un modèle de reconnaissance de texte (~100 Mo) : elle peut
prendre quelques minutes selon la connexion. Elle ne se refait pas au lancement suivant.

À chaque nouvelle session de terminal, il faut réactiver l'environnement avant d'utiliser
l'outil : `source .venv/bin/activate` (ou `.venv\Scripts\activate` sous Windows).

---

## 2. Utilisation

### Mode assistant (recommandé)

Déposez le(s) PDF à convertir dans le dossier `input/`, puis lancez simplement :

```bash
python3 book2word.py
```

L'outil affiche la liste des PDF trouvés dans `input/` : tapez le numéro de celui à convertir.
Le document Word est déposé automatiquement dans `output/`, avec le même nom que le PDF (ex.
`input/mon_livre.pdf` → `output/mon_livre.docx`). Si ce nom existe déjà, un numéro est ajouté
automatiquement (`mon_livre (2).docx`) — rien n'est jamais écrasé.

Le reste des questions (une valeur par défaut est proposée entre parenthèses — appuyez sur
Entrée pour l'accepter) porte sur la reconnaissance du texte et les options avancées. Une
barre de progression s'affiche pendant le traitement, puis un résumé indique où se trouve
le résultat.

### Mode ligne de commande (utilisateurs avancés / scripts)

```bash
python3 book2word.py input/mon_livre.pdf
python3 book2word.py mon_livre.pdf resultat.docx   # chemin de sortie explicite (optionnel)
```

Sans deuxième argument, le fichier est aussi déposé dans `output/` en suivant la même règle
de nommage qu'en mode assistant.

Options utiles :

| Option | Effet |
|---|---|
| `--force-ocr` | Relit toutes les pages par reconnaissance d'image plutôt que de faire confiance au texte déjà présent dans le PDF. À utiliser si le texte obtenu est incohérent ou tronqué. |
| `--ocr-fallback` | Lit par image seulement les pages qui n'ont pas de texte du tout (activé automatiquement en mode assistant). |
| `--dpi 300` | Qualité des images (plus haut = plus net mais fichier plus lourd et traitement plus long). |
| `--no-crop` | Désactive le recadrage automatique des bordures sombres (pages photographiées). |
| `--debug` | Sauvegarde les images avant/après nettoyage dans un dossier `debug/`, pour vérifier visuellement. |
| `--verbose` | Affiche le détail technique dans le terminal (par défaut, il va uniquement dans un fichier `.log`). |
| `--template modele.docx` | Document Word de base pour la police et la mise en page (voir ci-dessous). |

Voir toutes les options : `python3 book2word.py --help`

### Personnaliser la police du document généré

Si un fichier `template.docx` se trouve à la racine du projet, il est utilisé automatiquement
comme base : le texte généré reprend sa police et sa mise en page (marges, taille de page)
au lieu du style par défaut. Pour changer de police, remplacez ce fichier par un `.docx` dont
le style *Normal* a la police souhaitée (dans Word : clic droit sur "Normal" dans le panneau
des styles → Modifier → choisir la police), ou indiquez un autre fichier avec `--template`.

---

## 3. Comprendre le résultat

Après traitement, vous obtenez dans `output/` :

- **le fichier `.docx`** : une page = une image nettoyée + le texte en dessous.
- **un fichier `.log`** (même nom, extension `.log`) : le détail technique de chaque page —
  utile si quelque chose semble anormal, pas nécessaire pour un usage normal.
- **un dossier `debug/`** (à la racine du projet, uniquement si vous avez activé cette option) :
  les images avant et après nettoyage, pour vérifier page par page.

Le résumé affiché en fin de traitement liste les **pages à vérifier** : celles où le
nettoyage automatique ou le recadrage n'est pas certain à 100 %. Ouvrez le `.docx` et
regardez ces pages en particulier.

---

## 4. Dépannage

**"Aucun PDF trouvé dans input/"** — placez votre fichier dans le dossier `input/` à la
racine du projet, puis relancez `python3 book2word.py`.

**Une dépendance manque / erreur `ImportError`** — relancez
`python3 -m pip install -r requirements.txt` dans le terminal (après avoir activé
l'environnement, étape 2).

**`zsh: command not found: pip`** — utilisez `python3 -m pip install ...` au lieu de
`pip install ...` (voir étape 2). Si l'erreur persiste, l'environnement virtuel n'est
probablement pas activé : relancez `source .venv/bin/activate` d'abord.

**Le texte extrait est incohérent, tronqué ou plein de caractères bizarres** — le PDF contient
probablement une couche de texte native de mauvaise qualité (fréquent sur les scans/photos).
Relancez avec `--force-ocr` : l'outil relira alors les pages comme des images plutôt que de
faire confiance à ce texte.

**Les images sont mal recadrées / gardent une bordure noire** — sur des PDF photographiés de
façon peu homogène (cadrage variable, main ou reflet visible sur certaines pages), le
recadrage automatique n'est pas toujours fiable ; il s'abandonne alors sciemment plutôt que de
mal recadrer (page listée dans le résumé). Une retouche manuelle dans Word reste possible.
Vous pouvez aussi désactiver entièrement le recadrage avec `--no-crop`.

**Le traitement est lent** — la reconnaissance de texte tourne sur le processeur (pas de carte
graphique dédiée requise, mais c'est plus lent) : comptez environ 15–30 secondes par page.
Pour un livre de 14 pages, prévoyez plusieurs minutes lors du premier essai (chargement du
modèle) puis un peu moins ensuite.

**Le fichier `.docx` est très volumineux** — réduisez `--dpi` (ex. `--dpi 200`) : l'image de
chaque page sera plus légère.

---

## 5. Limites à connaître

- Le nettoyage du texte sur l'image (remplissage du fond) n'est pas toujours parfait,
  notamment sur de grandes zones de texte proches d'illustrations très contrastées. Les pages
  concernées sont signalées automatiquement dans le résumé.
- Le recadrage automatique fonctionne bien sur des scans propres, mais peut échouer sur des
  photos peu homogènes (voir Dépannage ci-dessus).
- La reconnaissance de texte (OCR) n'est jamais garantie à 100 %, en particulier sur des
  polices très stylisées ou manuscrites : une relecture reste recommandée pour un usage
  éditorial.
- Cet outil ne contourne aucune protection PDF (mot de passe, DRM) : utilisez-le uniquement
  sur des fichiers dont vous avez le droit d'extraire le contenu.

---

## Pour aller plus loin

Le fonctionnement interne (architecture, choix techniques) est documenté dans
[CLAUDE.md](CLAUDE.md), destiné à quiconque doit faire évoluer le code.
