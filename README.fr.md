# 🐳 Portalcrane

![License](https://img.shields.io/badge/License-MIT-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![Angular](https://img.shields.io/badge/Angular-22-green)
[![ci::status]][ci::github]
[![docker::pulls]][docker::hub]
[![documentation::badge]][documentation::web]

[ci::status]: https://img.shields.io/github/actions/workflow/status/cyr-ius/portalcrane/docker-publish.yml?color=blue&logo=github
[ci::github]: https://github.com/cyr-ius/portalcrane/actions
[docker::pulls]: https://img.shields.io/docker/pulls/cyrius44/portalcrane.svg?logo=docker
[docker::hub]: https://hub.docker.com/r/cyrius44/portalcrane
[documentation::badge]: https://img.shields.io/badge/DOCUMENTATION-GH%20PAGES-0078D4?logo=googledocs
[documentation::web]: https://cyr-ius.github.io/portalcrane/

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

Image unique, aucune base de données externe, `linux/amd64`/`linux/arm64` — voir
la page [Architecture](https://cyr-ius.github.io/portalcrane/architecture/)
(en anglais) pour le détail de la stack technique.

---

## Démarrage rapide

```bash
docker run -d \
  --name portalcrane \
  -p 8000:8000 \
  -v /portalcrane_data:/var/lib/portalcrane \
  cyrius44/portalcrane:latest
```

Aucun identifiant n'a besoin d'être fourni. Au premier lancement, un mot de passe
admin sécurisé est généré automatiquement et **affiché une seule fois dans les
logs du conteneur** — l'utilisateur par défaut est `admin` :

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Ouvrez **http://localhost:8000** et connectez-vous avec `admin` et ce mot de passe.

> **Note :** monter un volume persistant sur `/var/lib/portalcrane` est requis —
> le mot de passe généré et la clé secrète JWT y sont stockés. Sans cela, les deux
> sont régénérés à chaque redémarrage.

Ou avec Docker Compose :

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

Une fois lancé, utilisez l'interface web ou directement le CLI Docker via le
proxy de registre :

```bash
docker login <host>:8000
docker pull <image>:<tag>
docker push <image>:<tag>
docker logout
```

> **Note :** `docker login` nécessite un jeton d'accès personnel (scope
> Docker) comme mot de passe — le vrai mot de passe du compte est refusé.
> Générez-en un depuis le menu du compte → Jetons d'accès personnels.

Pour le guide d'installation complet (TLS, reverse proxy, stack de dev, notes
de sécurité), voir **[Getting Started](https://cyr-ius.github.io/portalcrane/getting-started/installation/)**
(en anglais).

---

## Documentation

La documentation complète — référence de configuration, chaque fonctionnalité
expliquée avec des exemples, l'API REST, et comment contribuer — est publiée
sur **[cyr-ius.github.io/portalcrane](https://cyr-ius.github.io/portalcrane/)**
(en anglais).

| Vous cherchez...                                                | Voir                                                                                                    |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Toutes les variables d'environnement                            | [Environment Variables](https://cyr-ius.github.io/portalcrane/configuration/environment-variables/)     |
| La configuration OIDC / SSO                                     | [Authentication & OIDC](https://cyr-ius.github.io/portalcrane/configuration/authentication/)            |
| Utilisateurs, groupes, permissions par répertoire               | [Users, Groups & Permissions](https://cyr-ius.github.io/portalcrane/features/users-groups-permissions/) |
| Le pipeline de préparation et l'analyse des vulnérabilités      | [Staging Pipeline](https://cyr-ius.github.io/portalcrane/features/staging-pipeline/)                    |
| Les registres externes et les transferts entre registres        | [External Registries & Transfers](https://cyr-ius.github.io/portalcrane/features/external-registries/)  |
| Les endpoints de l'API REST                                     | [API Reference](https://cyr-ius.github.io/portalcrane/api-reference/)                                   |
| Faire tourner le frontend/backend en local, publier une release | [Development](https://cyr-ius.github.io/portalcrane/development/)                                       |

---

## Licence

MIT — voir [LICENSE](LICENSE) pour les détails.

## À propos

Auteur : [@cyr-ius](https://github.com/cyr-ius) — Sponsor : [GitHub Sponsors](https://github.com/sponsors/cyr-ius)
