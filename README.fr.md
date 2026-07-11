# 🐳 Portalcrane

[English](README.md) · **Français** · [Español](README.es.md)

**Portalcrane** est un gestionnaire de registre Docker auto-hébergé.
Il offre une interface moderne et intuitive pour parcourir, rechercher et gérer
les images et les tags, avec un processus de préparation incluant l'analyse des
vulnérabilités.
Il permet également de déclarer des registres externes et d'effectuer des
transferts entre eux.
Le registre interne de Portalcrane permet d'organiser les images en répertoires.
Un modèle RBAC permet de contrôler l'utilisation des images.

<img width="1432" height="942" alt="image" src="https://github.com/user-attachments/assets/a6fa3b39-e603-4562-b784-2fb5483b795c" />

---

## Table des matières

- [Fonctionnalités](#fonctionnalités)
- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Démarrage rapide](#démarrage-rapide)
- [Ports](#ports)
- [Healthcheck](#healthcheck)
- [Notes de sécurité](#notes-de-sécurité)
- [Variables d'environnement](#variables-denvironnement)
- [Tableau de bord](#tableau-de-bord)
- [Gestion des utilisateurs](#gestion-des-utilisateurs)
- [Jetons d'accès personnels](#jetons-daccès-personnels)
- [Contrôle d'accès par répertoire](#contrôle-daccès-par-répertoire)
- [Pipeline de préparation](#pipeline-de-préparation)
- [Registres externes et synchronisation](#registres-externes-et-synchronisation)
- [Persistance des données](#persistance-des-données)
- [Développement](#développement)
- [CI / CD](#ci--cd)
- [Captures d'écran](#captures-décran)
- [Licence](#licence)

## Fonctionnalités

- 🎨 Interface moderne avec thèmes clair / sombre / auto
- 🔐 Authentification locale (admin + comptes par utilisateur) avec support OIDC optionnel
- 👥 Gestion multi-utilisateurs avec permissions granulaires pull / push
- 📁 Contrôle d'accès par répertoire (permissions pull/push locales et externes par répertoire sur les espaces de noms d'images)
- 📦 Parcourir, rechercher et paginer les images et les tags
- 🗑️ Supprimer des images ou des tags individuels
- 🏷️ Retag : ajouter de nouveaux tags à des images existantes
- 🚀 Pipeline de préparation : recherche Docker Hub → Pull → analyse CVE Trivy (optionnelle) → Push vers le registre
- 📊 Tableau de bord avec statistiques en temps réel (nombre d'images, utilisation disque, image la plus volumineuse, nombre d'utilisateurs et d'admins)
- 🔍 Mode avancé : métadonnées détaillées des images (couches, labels, variables d'environnement, architecture…)
- 🌐 Registres externes : gestion CRUD + test de connectivité
- 🔄 Synchronisation : pousser les images locales vers des registres externes (complète ou par image)
- 📡 Support syslog dans l'onglet Réseau
- 📋 Journaux d'audit : historique complet des opérations API
- 🔒 Proxy de registre avec application de l'authentification
- ℹ️ Panneau À propos avec vérification de version face à la dernière release GitHub
- 🐳 Déploiement en conteneur unique (frontend + backend + registre dans une seule image)

---

## Architecture

| Couche                         | Technologie                                                         |
| ------------------------------ | ------------------------------------------------------------------- |
| **Frontend**                   | Angular 22 — Signals, Signal Forms, Zoneless, composants standalone |
| **Style**                      | Bootstrap 5 + Bootstrap Icons                                       |
| **Backend**                    | FastAPI + Python 3.14 (entièrement asynchrone)                      |
| **Validation**                 | Pydantic v2                                                         |
| **Registre**                   | Distribution (CNCF) v3 embarqué                                     |
| **Analyse des vulnérabilités** | Trivy (embarqué)                                                    |
| **Transfert d'images**         | skopeo (pull/push/copy sans démon)                                  |
| **Conteneur**                  | Image unique — supervisord orchestre tous les processus             |
| **Plateformes**                | `linux/amd64`, `linux/arm64` (Raspberry Pi, Apple Silicon)          |

---

## Prérequis

- Docker 24+ (ou compatible)
- Docker Compose v2 (optionnel, pour l'usage avec compose)

## Démarrage rapide

### Docker CLI

```bash
docker run -d \
  --name portalcrane \
  -p 8000:8000 \
  -v /portalcrane_data:/var/lib/portalcrane \
  cyrius44/portalcrane:latest
```

Aucun identifiant n'a besoin d'être fourni. Au premier lancement, un mot de passe
admin sécurisé et une clé JWT `SECRET_KEY` sont générés automatiquement et
persistés dans le volume de données. Le mot de passe admin est **affiché une seule
fois dans les logs du conteneur** — l'utilisateur par défaut est `admin` :

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Ouvrez **http://localhost:8080** et connectez-vous avec `admin` et ce mot de passe.

> **Note :** monter un volume persistant sur `/var/lib/portalcrane` est requis —
> le mot de passe généré et la clé secrète y sont stockés. Sans cela, les deux
> sont régénérés à chaque redémarrage.

### Docker Compose

```yaml
services:
  portalcrane:
    image: cyrius44/portalcrane:latest
    container_name: portalcrane
    ports:
      - "8000:8000"
    volumes:
      - portalcrane_data:/var/lib/portalcrane
    restart: unless-stopped

volumes:
  portalcrane_data:
```

### Utilisation

Accédez à l'interface web (`http(s)://<ip ou nom>:8000`) pour contrôler, tirer ou
pousser vos images, ou directement avec les commandes Docker via le proxy de registre.

```bash
docker login <host>:8000
docker pull <image>:<tag>
docker push <image>:<tag>
docker logout
```

Pour un accès complet sans authentification, définissez la variable
`REGISTRY_PROXY_AUTH_ENABLED` sur `false`.
Si vous utilisez la stack de dev et souhaitez un accès direct au registre, utilisez
`<host>:5000`.

### Docker Compose (stack de dev, depuis ce dépôt)

Cette stack construit l'image locale et démarre également un registre dédié sur le
port `5000`. Les données du registre sont stockées dans le volume `registry_data`.

```bash
docker compose up -d
```

---

## Ports

- `8000` — Interface + API Portalcrane + proxy de registre
- `5000` — Registre Docker (stack de dev uniquement)

## Healthcheck

`GET /api/health` renvoie une charge utile JSON de statut.

## Notes de sécurité

- Le mot de passe admin est généré et affiché dans les logs au premier lancement
  (utilisateur par défaut : `admin`). Il est persisté sous `DATA_DIR` et réutilisé
  entre les redémarrages. Récupérez-le dans les logs pour la première connexion —
  il ne peut pas être défini via l'environnement. Pour le renouveler, supprimez
  `DATA_DIR/admin_password.hash` et redémarrez.
- `SECRET_KEY` est générée automatiquement et persistée sous `DATA_DIR` au premier
  lancement. Ne la définissez explicitement que pour partager un secret fixe entre
  plusieurs instances.
- Montez un volume persistant sur `DATA_DIR` (`/var/lib/portalcrane`) afin que le
  mot de passe généré et la clé secrète survivent aux redémarrages.
- Si vous exposez l'interface publiquement, activez HTTPS — soit au niveau du reverse
  proxy, soit nativement en définissant `PRIVATE_KEY` / `PUBLIC_KEY` (voir TLS / SSL
  ci-dessous).

## Variables d'environnement

### Authentification

| Variable         | Description                                                | Défaut     |
| ---------------- | ---------------------------------------------------------- | ---------- |
| `ADMIN_USERNAME` | Nom d'utilisateur admin intégré                            | `admin`    |
| `SECRET_KEY`     | Secret de signature JWT — généré et persisté si non défini | _(généré)_ |

> Le mot de passe admin n'est pas une variable d'environnement : il est généré
> automatiquement au premier lancement et affiché dans les logs (voir
> [Notes de sécurité](#notes-de-sécurité)).

### TLS / SSL (optionnel)

Les deux variables doivent pointer vers des fichiers PEM montés dans le conteneur.
Lorsqu'elles sont définies, le backend FastAPI (uvicorn) sert directement en HTTPS
via `--ssl-keyfile` / `--ssl-certfile`. Laissez-les non définies pour servir en
HTTP simple et terminer TLS au niveau d'un reverse proxy.

| Variable      | Description                                            | Défaut |
| ------------- | ------------------------------------------------------ | ------ |
| `PRIVATE_KEY` | Chemin du fichier de clé privée TLS (`--ssl-keyfile`)  | —      |
| `PUBLIC_KEY`  | Chemin du fichier de certificat TLS (`--ssl-certfile`) | —      |

### Registre

| Variable                      | Description                                                | Défaut |
| ----------------------------- | ---------------------------------------------------------- | ------ |
| `REGISTRY_PROXY_AUTH_ENABLED` | Imposer l'authentification sur le proxy de registre `/v2/` | `true` |

### API, Swagger & jetons d'accès personnels

| Variable           | Description                                          | Défaut  |
| ------------------ | ---------------------------------------------------- | ------- |
| `SWAGGER_ENABLED`  | Exposer l'interface Swagger sur `/api/docs`          | `false` |
| `API_KEYS_ENABLED` | Activer la fonctionnalité de jetons personnels (PAT) | `true`  |

Avec `API_KEYS_ENABLED=false`, les PAT sont entièrement désarmés : les endpoints
de jetons renvoient `403`, les clés existantes de scope API sont rejetées sur
l'API REST, et le panneau de génération est masqué dans le profil utilisateur.

### Reverse proxy & limitation de débit

| Variable                        | Description                                                                       | Défaut |
| ------------------------------- | --------------------------------------------------------------------------------- | ------ |
| `TRUSTED_PROXIES`               | Plages CIDR (ou IP simples), séparées par des virgules, des reverse proxies amont | —      |
| `RATE_LIMIT_ENABLED`            | Activer la limitation de débit par IP sur les routes `/api/*`                     | `true` |
| `RATE_LIMIT_WINDOW_SECONDS`     | Durée de la fenêtre glissante, en secondes                                        | `60`   |
| `RATE_LIMIT_MAX_REQUESTS`       | Requêtes max par IP et par fenêtre, toutes routes `/api/*`                        | `100`  |
| `RATE_LIMIT_LOGIN_MAX_ATTEMPTS` | Budget plus strict par IP et par fenêtre pour les endpoints login/token           | `5`    |

`TRUSTED_PROXIES` définit la frontière de confiance des reverse proxies. Les IP
client transmises (`Forwarded` / `X-Forwarded-For` / `X-Real-IP`) ne sont prises
en compte **que** si le pair TCP direct appartient à l'une de ces plages ; sinon
les en-têtes sont considérés comme falsifiables et ignorés, avec repli sur
l'adresse réelle du pair. Cette IP client résolue alimente **à la fois** le
journal d'audit et le limiteur de débit par IP.

> Laissée vide (défaut), chaque requête est indexée par le pair TCP réel — sûr,
> mais lorsque l'application est derrière un reverse proxy **tous** les clients
> partagent alors l'IP du proxy, donc un unique compteur de limitation. Réglez-la
> sur le réseau de votre proxy (ex. `TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12`)
> pour que la limitation par client et l'audit voient l'adresse réelle du client.
> Ne listez que les proxies que vous contrôlez réellement — faire confiance à une
> plage permet à tout ce qui s'y trouve d'usurper les IP client.

L'état de la limitation réside dans la mémoire du processus : adapté au
déploiement mono-conteneur (un seul processus Uvicorn), il n'est **pas** partagé
entre workers ou réplicas.

### OIDC (optionnel)

| Variable                        | Description                                             | Défaut                 |
| ------------------------------- | ------------------------------------------------------- | ---------------------- |
| `OIDC_ENABLED`                  | Activer la connexion OIDC                               | `false`                |
| `OIDC_ISSUER`                   | URL de l'émetteur OIDC                                  | —                      |
| `OIDC_CLIENT_ID`                | Identifiant client OIDC                                 | —                      |
| `OIDC_CLIENT_SECRET`            | Secret client OIDC                                      | —                      |
| `OIDC_REDIRECT_URI`             | URI de redirection OIDC                                 | —                      |
| `OIDC_POST_LOGOUT_REDIRECT_URI` | URI de redirection après déconnexion                    | —                      |
| `OIDC_RESPONSE_TYPE`            | Type de réponse OIDC                                    | `code`                 |
| `OIDC_SCOPE`                    | Scopes OIDC                                             | `openid profile email` |
| `OIDC_ONLY`                     | Désactiver toute connexion locale (mode OIDC seul)      | `false`                |
| `OIDC_ADMIN_GROUP_CLAIM`        | Claim OIDC portant les groupes/rôles de l'utilisateur   | —                      |
| `OIDC_ADMIN_GROUP`              | Valeur de groupe accordant les droits admin             | —                      |
| `OIDC_USER_GROUP_CLAIM`         | Claim OIDC portant les groupes/rôles de l'utilisateur   | —                      |
| `OIDC_USER_GROUP`               | Valeur de groupe accordant l'accès utilisateur standard | —                      |
| `OIDC_RESTRICT_TO_GROUPS`       | Restreindre l'accès aux groupes mappés (liste blanche)  | `false`                |

Ces valeurs peuvent être surchargées par l'interface (**Paramètres → OIDC**).

#### Bundle CA personnalisé (PKI privée)

Lorsque votre fournisseur OIDC est protégé par une CA privée (auto-signée ou PKI
interne), fournissez la chaîne CA (intermédiaire + racine, concaténés dans un
seul fichier PEM) via la variable d'environnement standard `SSL_CERT_FILE` ou
`REQUESTS_CA_BUNDLE` pointant vers un fichier monté. Les appels OIDC sortants font
alors confiance à cette chaîne au lieu du bundle certifi par défaut. Laissez les
deux vides pour conserver la vérification par défaut.

#### Mode OIDC seul & amorçage admin

Par défaut, la connexion OIDC est proposée **en parallèle** de la connexion locale
admin/utilisateur. Définissez `OIDC_ONLY=true` pour **désactiver toute connexion
locale par mot de passe — y compris l'admin d'environnement intégré** — et vous
authentifier uniquement via votre fournisseur.

Comme il n'y a pas de solution de secours, le mode OIDC seul requiert le mappage du
groupe admin pour éviter de vous verrouiller dehors. Les droits admin sont
réévalués à **chaque** connexion SSO (promotion/rétrogradation en direct), via le
mappage de claim de groupe :

- **Mappage de claim de groupe** — `OIDC_ADMIN_GROUP_CLAIM=groups` et
  `OIDC_ADMIN_GROUP=registry-admins` (assurez-vous que le scope expose ce claim).

L'interface refuse d'activer le mode OIDC seul tant que ceci n'est pas configuré.

#### Restreindre l'accès à des utilisateurs spécifiques (liste blanche)

Par défaut, **chaque** utilisateur SSO authentifié est provisionné comme utilisateur
standard. Pour restreindre l'accès, mappez les utilisateurs standard de la même
manière que les admins — via un mappage de claim de groupe :

- **Mappage de claim de groupe** — `OIDC_USER_GROUP_CLAIM=groups` et
  `OIDC_USER_GROUP=registry-users`.

Définissez ensuite `OIDC_RESTRICT_TO_GROUPS=true` (ou cochez **Restreindre l'accès
aux groupes mappés** dans l'interface) pour transformer l'accès OIDC en liste
blanche : seuls les admins et utilisateurs correspondant aux mappages peuvent se
connecter. Tout utilisateur SSO n'appartenant **ni** au groupe admin **ni** au
groupe utilisateur se voit **refuser l'accès** (`403`) et son compte **n'est pas
créé**. Les claims des groupes admin et utilisateur peuvent pointer vers des claims
différents ; les deux sont lus à la connexion. L'accès est réévalué à **chaque**
connexion SSO.

#### Protection anti-usurpation

Les identités OIDC sont maintenues strictement séparées des comptes locaux pour
empêcher l'élévation de privilèges : une connexion SSO est **rejetée (403)** lorsque
le nom d'utilisateur résolu correspond à l'admin d'environnement intégré ou à tout
compte local existant (basé sur mot de passe). Cela empêche un fournisseur qui
renvoie `preferred_username=admin` — ou tout nom entrant en collision avec un
utilisateur local — d'hériter des droits de ce compte.

### Analyse des vulnérabilités (Trivy)

| Variable               | Description                                                              | Défaut          |
| ---------------------- | ------------------------------------------------------------------------ | --------------- |
| `TRIVY_ENABLED`        | Interrupteur maître — démarre le serveur Trivy embarqué et l'analyse CVE | `true`          |
| `VULN_SCAN_ENABLED`    | Activer l'analyse CVE Trivy dans le pipeline de préparation              | `true`          |
| `VULN_SCAN_SEVERITIES` | Niveaux de sévérité bloquants (séparés par des virgules)                 | `CRITICAL,HIGH` |
| `VULN_IGNORE_UNFIXED`  | Ignorer les CVE sans correctif disponible                                | `false`         |
| `VULN_SCAN_TIMEOUT`    | Délai d'expiration de l'analyse Trivy                                    | `5m`            |

> Définissez `TRIVY_ENABLED=false` (accepte aussi `0`, `no`, `off`) pour désarmer
> complètement Trivy : le serveur embarqué n'est pas démarré par supervisord, le
> rafraîchissement de la base est ignoré, aucune analyse n'est lancée et les
> endpoints de scan renvoient `503`. Les autres valeurs `VULN_*` sont alors sans effet.

Ces valeurs peuvent être surchargées par l'interface.

### Proxy HTTP (optionnel)

| Variable      | Description                             | Défaut                |
| ------------- | --------------------------------------- | --------------------- |
| `HTTP_PROXY`  | URL du proxy HTTP                       | —                     |
| `HTTPS_PROXY` | URL du proxy HTTPS                      | —                     |
| `NO_PROXY`    | Hôtes no-proxy séparés par des virgules | `localhost,127.0.0.1` |

### Journalisation

| Variable           | Description                                              | Défaut |
| ------------------ | -------------------------------------------------------- | ------ |
| `LOG_LEVEL`        | Niveau de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`)      | `INFO` |
| `AUDIT_MAX_EVENTS` | Nombre maximal d'événements d'audit conservés sur disque | `100`  |

### E-mail (optionnel)

Envoie le journal d'audit par e-mail via SMTP. Toutes ces valeurs sont
surchargeables depuis l'interface (**Paramètres → Réseau → E-mail**), qui expose
également un bouton de **test**.

| Variable             | Description                                          | Défaut                  |
| -------------------- | ---------------------------------------------------- | ----------------------- |
| `EMAIL_ENABLED`      | Activer l'envoi d'e-mails                            | `false`                 |
| `EMAIL_HOST`         | Hôte du serveur SMTP                                 | —                       |
| `EMAIL_PORT`         | Port du serveur SMTP                                 | `587`                   |
| `EMAIL_SECURITY`     | Sécurité de la connexion (`none`, `starttls`, `ssl`) | `starttls`              |
| `EMAIL_USERNAME`     | Identifiant SMTP (vide pour anonyme)                 | —                       |
| `EMAIL_PASSWORD`     | Mot de passe SMTP                                    | —                       |
| `EMAIL_FROM_ADDRESS` | Adresse d'expédition                                 | —                       |
| `EMAIL_TO_ADDRESSES` | Destinataires séparés par des virgules               | —                       |
| `EMAIL_SUBJECT`      | Préfixe de l'objet                                   | `Portalcrane audit log` |
| `EMAIL_NOTIFY_LOGIN` | Envoyer un e-mail à chaque connexion / déconnexion   | `false`                 |
| `EMAIL_NOTIFY_AUDIT` | Envoyer un e-mail à chaque autre événement d'audit   | `false`                 |

`EMAIL_NOTIFY_LOGIN` et `EMAIL_NOTIFY_AUDIT` sont deux options indépendantes
d'envoi **automatique par événement** ; elles sont distinctes de l'export du
journal d'audit à la demande, qui reste disponible depuis le panneau Réseau.

### Stockage (debug)

| Variable   | Description                                     | Défaut                 |
| ---------- | ----------------------------------------------- | ---------------------- |
| `DATA_DIR` | Répertoire de données de base dans le conteneur | `/var/lib/portalcrane` |

---

## Tableau de bord

Le tableau de bord affiche un aperçu en temps réel du registre :

- **Total des images** — nombre de dépôts dans le registre
- **Taille du registre** — total des données stockées (lisible par l'humain)
- **Utilisation disque** — pourcentage d'utilisation disque avec une barre de progression codée par couleur
- **Utilisateurs** — nombre total de comptes et nombre d'administrateurs (le compteur d'admins devient rouge au-delà de 5)

Il expose aussi des actions rapides (parcourir les images, tirer depuis Docker Hub)
et des opérations de maintenance (Garbage Collection).

---

## Gestion des utilisateurs

Portalcrane prend en charge deux types de comptes :

- **Admin intégré** — le compte `admin` dont le mot de passe est généré
  automatiquement et affiché dans les logs au premier lancement. Il ne peut être ni
  supprimé ni modifié via l'interface.
- **Utilisateurs locaux** — créés via le panneau **Paramètres → Comptes**. Chaque
  utilisateur peut se voir attribuer :
  - Le rôle admin (accès complet)
  - La permission pull (lire les images du registre)
  - La permission push (écrire / supprimer des images dans le registre)

---

## Jetons d'accès personnels

Chaque utilisateur authentifié peut générer des **jetons d'accès personnels** depuis le panneau **menu du compte → Jetons d'accès personnels**. Ils sont particulièrement utiles pour les utilisateurs OIDC qui n'ont pas de mot de passe local. La valeur brute du jeton n'est affichée **qu'une seule fois** à la création (elle est stockée sous forme de hachage bcrypt et identifiée en interne par un `jti` unique et signé).

Chaque jeton est créé avec **l'un des deux scopes mutuellement exclusifs** :

| Scope      | `docker login` | API REST / Swagger | Jeton court 16 car. |
| ---------- | :------------: | :----------------: | :-----------------: |
| **Docker** |       ✅       |         ❌         |         ✅          |
| **API**    |       ❌       |         ✅         |         ❌          |

Un jeton créé pour un scope est refusé pour l'autre : ainsi une identité Docker de CI ne peut jamais atteindre l'API REST, et une clé API ne peut jamais servir à tirer/pousser des images.

### Jetons de scope Docker

Utilisez le jeton comme mot de passe pour `docker login` — soit le jeton complet, soit le jeton court de 16 caractères affiché à la création :

```bash
docker login <host>:8000 -u <username> -p <jeton>
```

### Jetons de scope API (Swagger / API REST)

Utilisez le jeton comme clé API en l'envoyant dans l'en-tête `Authorization` :

```bash
curl -H "Authorization: Bearer <jeton>" http(s)://<host>:8000/api/auth/me
```

Dans **Swagger UI** (`/api/docs`, activé avec `SWAGGER_ENABLED=true`) : cliquez sur **Authorize**, choisissez **PersonalAccessToken**, collez le jeton, et toutes les requêtes sont alors authentifiées en votre nom.

Les jetons peuvent être révoqués à tout moment depuis le même panneau ; les jetons expirés ou révoqués sont refusés immédiatement. La suppression d'un utilisateur révoque également tous ses jetons.

> Les endpoints du proxy de registre (`/v2/...`) implémentent l'API HTTP du Docker Registry et sont volontairement masqués de Swagger, car ils ne sont pertinents que pour le CLI Docker.

---

## Contrôle d'accès par répertoire

Les administrateurs peuvent définir des **répertoires** (préfixes d'espaces de noms
d'images, ex. `production/`) et attribuer des permissions sur chaque répertoire. Les
utilisateurs non-admin ne peuvent accéder qu'aux images dont le chemin correspond à
un répertoire auquel ils ont reçu l'accès.

Quatre permissions indépendantes peuvent être accordées par répertoire :

- **Pull** — lire les images du registre **local** embarqué.
- **Push** — écrire / supprimer des images dans le registre **local** embarqué.
- **Pull externe** — récupérer des images **dans** Portalcrane **depuis** un registre
  réellement externe (Docker Hub, registres enregistrés ou ad-hoc) via la page de préparation.
- **Push externe** — pousser des images **hors** de Portalcrane **vers** un registre
  réellement externe via la page de préparation.

Les permissions pull / push externes sont indépendantes de leurs équivalents locaux :
elles régissent les transferts avec les registres externes, et non les lectures et
écritures sur le registre local.

---

## Pipeline de préparation

1. **Rechercher** une image sur Docker Hub
2. **Sélectionner** une image et un tag
3. **Pull** — skopeo télécharge l'image sous forme de layout OCI (aucun démon Docker requis)
4. **Analyse** — Trivy analyse l'image à la recherche de CVE (optionnel, seuils de sévérité configurables)
5. **Push** — skopeo copie le layout OCI vers le registre privé sous un nom et un tag choisis

---

## Registres externes et synchronisation

- Ajouter des registres externes compatibles Docker (Docker Hub, GHCR, Quay, auto-hébergés…)
- Tester la connectivité et l'authentification directement depuis l'interface
- Synchroniser les images locales vers un registre externe (registre complet ou par image/répertoire)

---

## Persistance des données

Toutes les données persistantes sont stockées dans **/var/lib/portalcrane** à
l'intérieur du conteneur. Montez un volume sur ce chemin pour conserver les données
entre les redémarrages du conteneur :

```
-v /portalcrane_data:/var/lib/portalcrane
```

Les données stockées incluent : le hash du mot de passe admin généré
(`admin_password.hash`), le secret de signature JWT (`secret_key`), les utilisateurs
locaux, la configuration OIDC, les permissions de répertoires, les registres
externes, les journaux d'audit et les données d'image du registre lui-même.

---

## Développement

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
ng serve --port 4200 --proxy-config proxy.conf.json
```

---

## CI / CD

Le workflow GitHub Actions (`.github/workflows/docker-publish.yml`) automatiquement :

1. Construit une image (`linux/amd64`) à chaque push sur `main` et sur les tags de version
2. Publie sur **Docker Hub** (`cyrius44/portalcrane`) et **GHCR** (`ghcr.io/cyr-ius/portalcrane`)
3. Met à jour la description Docker Hub à partir de ce README

Les tags d'image suivent le versionnage sémantique : `latest`, `edge`, `X`, `X.Y`, `X.Y.Z`, `sha-<commit>`.

### Publier une release

Utilisez le script d'aide — il incrémente la version frontend, la committe, tague **ce** commit puis pousse, afin que le tag contienne toujours une version cohérente :

```bash
scripts/release.sh patch     # ou minor / major / une version explicite X.Y.Z
```

Le push du tag `X.Y.Z` déclenche la release et la construction de l'image. Utilisez `--no-push` pour préparer le commit et le tag localement sans pousser.

---

## Captures d'écran

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/18e00fb2-76e2-4ece-8ece-fc11fad16eff" />

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/ebf83322-14ba-4331-a9d3-72d04737d9a5" />

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/7ba30cdf-ee0f-4693-a796-bec6a17f692e" />

---

## Licence

MIT — voir [LICENSE](LICENSE) pour les détails.

## À propos

Auteur : [@cyr-ius](https://github.com/cyr-ius) — Sponsor : [GitHub Sponsors](https://github.com/sponsors/cyr-ius)
