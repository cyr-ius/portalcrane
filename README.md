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

**English** · [Français](README.fr.md) · [Español](README.es.md)

**Portalcrane** is a self-hosted Docker registry manager.
It offers a modern and intuitive interface for browsing, searching, and managing images and tags,
with a preparation process that includes vulnerability scanning.
It also allows you to declare external registries and perform transfers between them.
Portalcrane's internal registry allows you to organize images into directories. An RBAC model allows you to control image usage.

<img width="1432" height="942" alt="image" src="https://github.com/user-attachments/assets/a6fa3b39-e603-4562-b784-2fb5483b795c" />

---

## Features

- 🎨 Modern UI with light / dark / auto themes
- 🔐 Local authentication (admin + per-user accounts) with optional OIDC support
- 👥 Multi-user management with granular pull / push permissions
- 📁 Folder-based access control (per-folder local and external pull/push permissions on image namespaces)
- 📦 Browse, search, and paginate images and tags
- 🗑️ Delete images or individual tags
- 🏷️ Retag: add new tags to existing images
- 🚀 Staging pipeline: Search Docker Hub → Pull → Trivy CVE scan (optional) → Push to registry
- 📊 Dashboard with live stats (image count, disk usage, largest image, user & admin counts)
- 🔍 Advanced mode: detailed image metadata (layers, labels, env vars, architecture…)
- 🌐 External registries: CRUD management + connectivity test
- 🔄 Sync: push local images to external registries (full or per-image)
- 📡 Syslog support in the Network tab
- 📋 Audit logs: full history of API operations
- 🔒 Registry proxy with authentication enforcement
- ℹ️ About panel with version check against the latest GitHub release
- 🐳 Single-container deployment (frontend + backend + registry in one image)

Single image, no external database, `linux/amd64`/`linux/arm64` — see the
[Architecture](https://cyr-ius.github.io/portalcrane/architecture/) page for
the full technology stack and how the pieces fit together.

---

## Quick Start

```bash
docker run -d \
  --name portalcrane \
  -p 8000:8000 \
  -v /portalcrane_data:/var/lib/portalcrane \
  cyrius44/portalcrane:latest
```

No credentials need to be provided. On first launch a secure admin password is
auto-generated and **printed once in the container logs** — the default user is `admin`:

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Open **http://localhost:8000** and log in with `admin` and that password.

> **Note:** mounting a persistent volume on `/var/lib/portalcrane` is required —
> the generated password and JWT secret key are stored there. Without it, both are
> regenerated on every restart.

Or with Docker Compose:

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

Once running, use the web UI or the Docker CLI directly through the registry proxy:

```bash
docker login <host>:8000
docker pull <image>:<tag>
docker push <image>:<tag>
docker logout
```

> **Note:** `docker login` requires a Docker-scoped Personal Access Token as
> the password — a real account password is rejected. Generate one from the
> account menu → Personal Access Tokens.

For the full installation guide (TLS, reverse proxies, the dev stack, security
notes) see **[Getting Started](https://cyr-ius.github.io/portalcrane/getting-started/installation/)**.

---

## Documentation

Full documentation — configuration reference, every feature explained with
examples, the REST API, and how to contribute — is published at
**[cyr-ius.github.io/portalcrane](https://cyr-ius.github.io/portalcrane/)**.

| Looking for...                                          | Go to                                                                                                   |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Every environment variable                              | [Environment Variables](https://cyr-ius.github.io/portalcrane/configuration/environment-variables/)     |
| OIDC / SSO setup                                        | [Authentication & OIDC](https://cyr-ius.github.io/portalcrane/configuration/authentication/)            |
| Users, groups, folder permissions                       | [Users, Groups & Permissions](https://cyr-ius.github.io/portalcrane/features/users-groups-permissions/) |
| The staging pipeline & vulnerability scanning           | [Staging Pipeline](https://cyr-ius.github.io/portalcrane/features/staging-pipeline/)                    |
| External registries & cross-registry transfers          | [External Registries & Transfers](https://cyr-ius.github.io/portalcrane/features/external-registries/)  |
| REST API endpoints                                      | [API Reference](https://cyr-ius.github.io/portalcrane/api-reference/)                                   |
| Running the frontend/backend locally, cutting a release | [Development](https://cyr-ius.github.io/portalcrane/development/)                                       |

---

## License

MIT — see [LICENSE](LICENSE) for details.

## About

Author: [@cyr-ius](https://github.com/cyr-ius) — Sponsor: [GitHub Sponsors](https://github.com/sponsors/cyr-ius)
