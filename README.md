# book2word

Transforme un livre PDF en document Word : chaque page devient une image suivie du texte de
la page, prêt à relire ou modifier dans Word. Deux types de livres sont pris en charge :

- **Livre avec deux images et texte mélangé** (album illustré) : le texte imprimé sur l'image
  est effacé, l'image pleine page nettoyée est conservée.
- **Livre habituel** (mise en page classique) : l'image se trouve sur la moitié droite de la
  page (elle est conservée, rognée), le texte sur la moitié gauche (il est uniquement extrait,
  rien de cette moitié n'est gardé dans le document).

Tout se fait depuis une page dans votre navigateur, sans connaissances techniques. Rien n'est
envoyé sur internet : tout reste sur votre ordinateur.

---

## 1. Installation (une seule fois)

1. Allez sur la page des [dernières versions](https://github.com/virgill-e/book2word/releases/latest).
2. Téléchargez `book2word-mac.zip` (Mac) ou `book2word-windows.zip` (Windows).
3. Dézippez le fichier téléchargé (double-clic dessus), puis :
   - **Mac** : double-cliquez sur `book2word.app`. La première fois, macOS bloque l'ouverture
     (« développeur non identifié » ou « book2word ne peut pas être ouvert »). Allez alors dans
     **Réglages Système → Confidentialité et sécurité**, faites défiler vers le bas jusqu'à
     « book2word a été bloqué… », cliquez sur **Ouvrir quand même**, confirmez avec votre mot
     de passe ou Touch ID, puis double-cliquez à nouveau sur `book2word.app`.
     Conseil : glissez `book2word.app` dans votre dossier **Applications** pour le retrouver
     facilement.
   - **Windows** : ouvrez le dossier dézippé et double-cliquez sur `book2word.exe`. Si Windows
     affiche « Windows a protégé votre ordinateur », cliquez sur **Informations
     complémentaires** puis **Exécuter quand même**. Gardez bien tout le dossier : l'application
     ne fonctionne pas si on copie le fichier `.exe` seul ailleurs.
4. Une page s'ouvre dans votre navigateur : c'est prêt.

Ces avertissements n'apparaissent qu'au premier lancement. Ils sont normaux : l'application
n'est pas enregistrée auprès d'Apple ou de Microsoft (démarche payante, inutile pour un usage
associatif).

**Première conversion :** l'application télécharge une seule fois son moteur de
reconnaissance de texte (environ 90 Mo, quelques minutes selon la connexion). Une connexion
internet est donc nécessaire ce jour-là ; la page affiche la progression. Si l'application est
fermée ou la connexion coupée entre-temps, relancez simplement une conversion : le
téléchargement reprend là où il s'était arrêté. Ensuite, tout fonctionne hors ligne.

---

## 2. Convertir un livre

1. Lancez l'application (double-clic sur `book2word.app` ou `book2word.exe`). La page
   s'ouvre dans votre navigateur.
2. Choisissez le **type de livre** (voir plus haut).
3. Sous **Importer un nouveau PDF**, choisissez votre fichier — ou, s'il a déjà été importé,
   sélectionnez-le dans **Fichier déjà importé**.
4. Cliquez sur **Convertir**.
5. Patientez : la page affiche la page en cours et une barre de progression. La durée dépend
   de l'ordinateur et du livre (plusieurs minutes est normal). Si vous fermez l'onglet par
   erreur, la conversion continue :
   relancez l'application (double-clic), un bandeau **Conversion en cours** permet de
   reprendre le suivi.
6. À la fin, cliquez sur **Afficher dans le dossier** pour ouvrir le document Word généré.

Vos fichiers sont rangés dans le dossier **Documents/book2word** :

- `input` : les copies des PDF importés (vos fichiers d'origine ne sont jamais modifiés) ;
- `output` : les documents Word générés, nommés comme le PDF. Si ce nom existe déjà, un
  numéro est ajouté (`mon livre (2).docx`) : rien n'est jamais écrasé.

### Quitter l'application

Cliquez sur **Quitter l'application** en haut à droite. Si vous fermez simplement l'onglet,
l'application s'arrête toute seule après 20 minutes sans utilisation (jamais pendant une
conversion).

### Faire de la place

Les PDF importés et les images de vérification prennent de la place avec le temps. Depuis la
page d'accueil :

- **Supprimer tous les PDF importés** (sous la liste « Fichiers importés ») : supprime les
  copies gardées par l'application, pas vos fichiers d'origine ni les documents Word.
- **Supprimer ces images** (dans « Options avancées », si vous avez enregistré des images de
  vérification).

Les documents Word se suppriment un par un, volontairement, pour éviter de perdre un travail
par erreur.

---

## 3. Options

**Forcer la reconnaissance par image (OCR) sur toutes les pages** (cochée par défaut) :
l'application lit le texte directement sur l'image de chaque page. Vous pouvez la décocher si
le PDF contient déjà un texte fiable (PDF créé par ordinateur, pas un scan) : le traitement
est alors beaucoup plus rapide.

Dans **Options avancées** :

- **Résolution des images** : 300 par défaut. Plus haut = images plus nettes mais document
  plus lourd et traitement plus long ; 200 suffit souvent si le fichier Word est trop gros.
- **Langue du livre** : français (par défaut), néerlandais, allemand ou anglais. Choisissez
  la langue dans laquelle le livre est écrit : la reconnaissance du texte (accents, lettres
  spécifiques) en dépend. La première conversion en anglais télécharge un petit complément
  (environ 15 Mo, connexion internet nécessaire une seule fois).
- **Recadrer automatiquement les bordures sombres** : retire les bords noirs des pages
  photographiées ou scannées.
- **Sauvegarder les images avant/après nettoyage** : garde une image de chaque page avant et
  après effacement du texte, pour vérifier le résultat de près.
- **Modèle de mise en page** : le document Word généré reprend la police et la mise en page
  (marges, taille de page) du fichier `template.docx`. Pour les changer, cliquez sur **Afficher
  le modèle dans le dossier**, ouvrez ce fichier dans Word et modifiez le style *Normal* (dans
  Word : clic droit sur « Normal » dans les styles → Modifier → choisir la police), puis
  enregistrez.

---

## 4. Comprendre le résultat

Le document Word contient, pour chaque page du livre, l'image nettoyée suivie du texte.

En fin de conversion, la page indique les **pages à vérifier** : celles où l'effacement du
texte sur l'image n'est pas certain à 100 %. Ouvrez le document Word et regardez ces pages en
particulier.

---

## 5. En cas de problème

**La page reste longtemps sur « Téléchargement du moteur de reconnaissance de texte »** —
c'est la première conversion (voir Installation) : le téléchargement dépend de votre
connexion. Le compteur en Mo doit avancer.

**« Impossible de télécharger le moteur de reconnaissance de texte »** — vérifiez votre
connexion internet, puis relancez la conversion. Le téléchargement reprend là où il s'était
arrêté.

**Le texte obtenu est incohérent, tronqué ou plein de caractères bizarres** — relancez la
conversion en laissant cochée **Forcer la reconnaissance par image (OCR)**.

**Les images gardent une bordure noire ou sont mal recadrées** — sur des pages photographiées
de façon irrégulière (main ou reflet visible), l'application préfère ne pas recadrer plutôt
que de mal recadrer. Retouchez l'image dans Word, ou décochez **Recadrer automatiquement**
dans les Options avancées.

**Le document Word est très lourd** — relancez avec une **Résolution des images** plus basse
(par exemple 200) dans les Options avancées.

**L'application ne s'ouvre pas** — au premier lancement, patientez une trentaine de secondes
(l'application est volumineuse). Sur Mac, vérifiez l'étape « Ouvrir quand même » de
l'installation. Si rien ne se passe, relancez-la.

**Un message d'erreur s'affiche pendant une conversion** — un fichier `.log` portant le nom
du livre se trouve dans **Documents/book2word/output** : transmettez-le à la personne qui
s'occupe de l'outil, il contient le détail technique.

---

## 6. Limites à connaître

- L'effacement du texte sur l'image n'est pas toujours parfait, notamment sur de grandes zones
  de texte proches d'illustrations très contrastées. Les pages concernées sont signalées en
  fin de conversion.
- La reconnaissance de texte n'est jamais garantie à 100 %, en particulier sur des polices très
  stylisées ou manuscrites : une relecture reste recommandée.
- L'application ne contourne aucune protection PDF (mot de passe, DRM) : utilisez-la uniquement
  sur des fichiers dont vous avez le droit d'extraire le contenu.

---

## Pour les développeurs

Installation depuis les sources, ligne de commande et fonctionnement interne :
voir [CLAUDE.md](CLAUDE.md).
