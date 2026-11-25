# Espace contributeurs

## Essai local

Depuis la racine du dépôt, dans l’environnement Python du projet, créer un
compte puis lancer l’application.

```sh
.venv/bin/python apps/contributors/app.py --create-user admin@example.org --name "Administration du corpus" --admin
.venv/bin/python apps/contributors/app.py
```

Pour la démo, la création demande un mot de passe d’au moins 8 caractères
sans l’afficher.
Ouvrir [l’espace contributeurs](http://127.0.0.1:8503) et choisir **Se connecter**.
Le serveur Python et le fichier SQLite sont alors sur le poste local.

## Distribution aux contributeurs

1. **Préparer un serveur unique** avec le dépôt, l’environnement Python, le
   registre des jeux de données et les artefacts acceptés. Copier les artefacts
   nécessaires avec le déploiement. Placer SQLite sur un disque persistant
   et sauvegardé. Réserver l’accès au fichier au compte de service.
2. **Publier une URL HTTPS privée** via un proxy inverse sur le même serveur.
   Le proxy termine TLS, conserve l’en-tête `Host` public et relaie vers
   `127.0.0.1:8503`. Configurer le proxy pour accepter les dépôts de 15 Mio
   transmis en base64. L’origine passée à l’application doit correspondre à
   l’URL distribuée.
3. **Créer un compte nominatif par contributeur** avec `--create-user` et
   `--name` depuis le compte de service, avec le même fichier SQLite. Réserver
   `--admin` aux personnes qui gèrent les contributions de l’ensemble du groupe.
   Communiquer l’URL HTTPS et les identifiants par un canal approprié. Les
   contributeurs accèdent à l’application dans leur navigateur.

```sh
.venv/bin/python apps/contributors/app.py --store /srv/corpus-lcb-ft/contributions.sqlite3 --create-user camille@example.org --name "Camille Durand"
.venv/bin/python apps/contributors/app.py --store /srv/corpus-lcb-ft/contributions.sqlite3 --public-origin https://corpus.example.org
```

Pour le pilote partagé, **le serveur Python et le fichier SQLite résident
sur le même serveur**. SQLite est un fichier utilisé par l’application. Il
contient les comptes, les dépôts, les avis et les propositions. En local,
l’application utilise par défaut
`artifacts/contributions/contributions.sqlite3`. L’option `--port` change le
port d’écoute.

Le serveur HTTP actuel convient aux essais et au pilote à accès restreint.
Pour une ouverture publique, prévoyez un serveur d’application de production.
[Documentation Python](https://docs.python.org/3.12/library/http.server.html)

L’onglet **Historique** affiche les contributions et leurs changements d’état
par date et par utilisateur. Les entrées antérieures aux comptes portent
la mention **Non renseigné**. Un contributeur peut retirer ou rétablir ses
propres contributions. Un administrateur peut aussi le faire pour les
contributions historiques et celles des autres comptes.

Le partage peut démarrer avec les dépôts au statut **Enregistré · traitement
non lancé**. L’ingestion des URL, la structuration, la génération et l’évaluation
restent gérées par le [pipeline existant](../../doc/aml-cft-pipeline.md).
