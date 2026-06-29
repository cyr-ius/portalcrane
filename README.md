# 🐳 Portalcrane

**Portalcrane** is a self-hosted Docker registry manager.
It offers a modern and intuitive interface for browsing, searching, and managing images and tags,
with a preparation process that includes vulnerability scanning.
It also allows you to declare external registries and perform transfers between them.
Portalcrane's internal registry allows you to organize images into directories. An RBAC model allows you to control image usage.

<img width="1432" height="942" alt="image" src="https://github.com/user-attachments/assets/a6fa3b39-e603-4562-b784-2fb5483b795c" />

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Ports](#ports)
- [Healthcheck](#healthcheck)
- [Security Notes](#security-notes)
- [Environment Variables](#environment-variables)
- [Dashboard](#dashboard)
- [User Management](#user-management)
- [Folder-Based Access Control](#folder-based-access-control)
- [Staging Pipeline](#staging-pipeline)
- [External Registries & Sync](#external-registries--sync)
- [Data Persistence](#data-persistence)
- [Development](#development)
- [CI / CD](#ci--cd)
- [Screenshots](#screenshots)
- [License](#license)

## Features

- 🎨 Modern UI with light / dark / auto themes
- 🔐 Local authentication (admin + per-user accounts) with optional OIDC support
- 👥 Multi-user management with granular pull / push permissions
- 📁 Folder-based access control (per-user pull/push permissions on image namespaces)
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

---

## Architecture

| Layer                      | Technology                                                          |
| -------------------------- | ------------------------------------------------------------------- |
| **Frontend**               | Angular 21 — Signals, Signal Forms, Zoneless, standalone components |
| **Styling**                | Bootstrap 5 + Bootstrap Icons                                       |
| **Backend**                | FastAPI + Python 3.14 (fully async)                                 |
| **Validation**             | Pydantic v2                                                         |
| **Registry**               | Distribution (CNCF) v3 embedded                                     |
| **Vulnerability scanning** | Trivy (embedded)                                                    |
| **Image transfer**         | skopeo (daemon-less pull/push/copy)                                 |
| **Container**              | Single image — supervisord orchestrates all processes               |
| **Platforms**              | `linux/amd64`, `linux/arm64` (Raspberry Pi, Apple Silicon)          |

---

## Prerequisites

- Docker 24+ (or compatible)
- Docker Compose v2 (optional, for compose usage)

## Quick Start

### Docker CLI

```bash
docker run -d \
  --name portalcrane \
  -p 8080:8080 \
  -v /portalcrane_data:/var/lib/portalcrane \
  cyrius44/portalcrane:latest
```

No credentials need to be provided. On first launch a secure admin password and
a JWT `SECRET_KEY` are auto-generated and persisted in the data volume. The
admin password is **printed once in the container logs** — the default user is
`admin`:

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Open **http://localhost:8080** and log in with `admin` and that password.

> **Note:** mounting a persistent volume on `/var/lib/portalcrane` is required —
> the generated password and secret key are stored there. Without it, both are
> regenerated on every restart.

### Docker Compose

```yaml
services:
  portalcrane:
    image: cyrius44/portalcrane:latest
    container_name: portalcrane
    ports:
      - "8080:8080"
    # No credentials required: the admin password and SECRET_KEY are
    # auto-generated on first launch and persisted in the data volume.
    # The admin password is printed once in the logs (default user: admin).
    volumes:
      - portalcrane_data:/var/lib/portalcrane
    restart: unless-stopped

volumes:
  portalcrane_data:
```

### Usage

Access the web interface (`http(s)://<ip or name>:8080`) to control, pull or push your images
or directly with Docker commands via the registry proxy.

```bash
docker login <host>:8080
docker pull <image>:<tag>
docker push <image>:<tag>
docker logout
```

For full access without authentication, set the REGISTRY_PROXY_AUTH_ENABLED variable to `false`.
If you are using the dev stack and want direct registry access, use `<host>:5000`.

### Docker Compose (dev stack, from this repo)

This stack builds the local image and also starts a dedicated registry on port `5000`.
Registry data is stored in the `registry_data` volume.

```bash
docker compose up -d
```

---

## Ports

- `8080` — Portalcrane UI + API + registry proxy
- `5000` — Docker registry (dev stack only)

## Healthcheck

`GET /api/health` returns a JSON status payload.

## Security Notes

- The admin password is generated and printed in the logs on first launch
  (default user: `admin`). It is persisted under `DATA_DIR` and reused across
  restarts. Retrieve it from the logs for the first login — it cannot be set
  through the environment. To rotate it, delete `DATA_DIR/admin_password.hash`
  and restart.
- `SECRET_KEY` is auto-generated and persisted under `DATA_DIR` on first launch.
  Set it explicitly only to share a fixed secret across multiple instances.
- Mount a persistent volume on `DATA_DIR` (`/var/lib/portalcrane`) so the
  generated password and secret key survive restarts.
- If you expose the UI publicly, enable HTTPS — either at the reverse proxy level
  or natively by setting `PRIVATE_KEY` / `PUBLIC_KEY` (see TLS / SSL below).

## Environment Variables

### Authentication

| Variable         | Description                                              | Default       |
| ---------------- | -------------------------------------------------------- | ------------- |
| `ADMIN_USERNAME` | Built-in admin username                                  | `admin`       |
| `SECRET_KEY`     | JWT signing secret — auto-generated & persisted if unset | _(generated)_ |

> The admin password is not an environment variable: it is auto-generated on
> first launch and printed in the logs (see [Security Notes](#security-notes)).

### TLS / SSL (optional)

Both variables must point to PEM files mounted inside the container. When set,
the FastAPI backend (uvicorn) serves HTTPS directly using `--ssl-keyfile` /
`--ssl-certfile`. Leave them unset to serve plain HTTP and terminate TLS at a
reverse proxy instead.

| Variable      | Description                                         | Default |
| ------------- | --------------------------------------------------- | ------- |
| `PRIVATE_KEY` | Path to the TLS private key file (`--ssl-keyfile`)  | —       |
| `PUBLIC_KEY`  | Path to the TLS certificate file (`--ssl-certfile`) | —       |

### Registry

| Variable                      | Description                                         | Default |
| ----------------------------- | --------------------------------------------------- | ------- |
| `REGISTRY_PROXY_AUTH_ENABLED` | Enforce authentication on the `/v2/` registry proxy | `true`  |

### OIDC (optional)

| Variable                        | Description              | Default                |
| ------------------------------- | ------------------------ | ---------------------- |
| `OIDC_ENABLED`                  | Enable OIDC login        | `false`                |
| `OIDC_ISSUER`                   | OIDC issuer URL          | —                      |
| `OIDC_CLIENT_ID`                | OIDC client ID           | —                      |
| `OIDC_CLIENT_SECRET`            | OIDC client secret       | —                      |
| `OIDC_REDIRECT_URI`             | OIDC redirect URI        | —                      |
| `OIDC_POST_LOGOUT_REDIRECT_URI` | Post-logout redirect URI | —                      |
| `OIDC_RESPONSE_TYPE`            | OIDC response type       | `code`                 |
| `OIDC_SCOPE`                    | OIDC scopes              | `openid profile email` |

These values ​​can be overridden by the UI.

### Vulnerability Scanning (Trivy)

| Variable               | Description                                   | Default         |
| ---------------------- | --------------------------------------------- | --------------- |
| `VULN_SCAN_ENABLED`    | Enable Trivy CVE scan in the staging pipeline | `true`          |
| `VULN_SCAN_SEVERITIES` | Blocking severity levels (comma-separated)    | `CRITICAL,HIGH` |
| `VULN_IGNORE_UNFIXED`  | Ignore CVEs with no available fix             | `false`         |
| `VULN_SCAN_TIMEOUT`    | Trivy scan timeout                            | `5m`            |

These values ​​can be overridden by the UI.

### HTTP Proxy (optional)

| Variable      | Description                    | Default               |
| ------------- | ------------------------------ | --------------------- |
| `HTTP_PROXY`  | HTTP proxy URL                 | —                     |
| `HTTPS_PROXY` | HTTPS proxy URL                | —                     |
| `NO_PROXY`    | Comma-separated no-proxy hosts | `localhost,127.0.0.1` |

### Logging

| Variable           | Description                                     | Default |
| ------------------ | ----------------------------------------------- | ------- |
| `LOG_LEVEL`        | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO`  |
| `AUDIT_MAX_EVENTS` | Maximum number of audit events retained on disk | `100`   |

### Storage (debug)

| Variable   | Description                              | Default                |
| ---------- | ---------------------------------------- | ---------------------- |
| `DATA_DIR` | Base data directory inside the container | `/var/lib/portalcrane` |

---

## Dashboard

The dashboard displays a real-time overview of the registry:

- **Total Images** — number of repositories in the registry
- **Registry Size** — total stored data (human-readable)
- **Disk Usage** — disk usage percentage with a colour-coded progress bar
- **Users** — total account count and number of administrators (admin count turns red when > 5)

It also exposes quick actions (browse images, pull from Docker Hub) and maintenance operations (Garbage Collection).

---

## User Management

Portalcrane supports two types of accounts:

- **Built-in admin** — the `admin` account whose password is auto-generated and printed in the logs on first launch. It cannot be deleted or modified through the UI.
- **Local users** — created via the **Settings → Accounts** panel. Each user can be assigned:
  - Admin role (full access)
  - Pull permission (read images from the registry)
  - Push permission (write / delete images in the registry)

---

## Folder-Based Access Control

Administrators can define **folders** (image namespace prefixes, e.g. `production/`) and assign per-user pull/push permissions on each folder. Non-admin users can only access images whose path matches a folder they have been granted access to.

---

## Staging Pipeline

1. **Search** Docker Hub for an image
2. **Select** an image and a tag
3. **Pull** — skopeo downloads the image as an OCI layout (no Docker daemon required)
4. **Scan** — Trivy analyses the image for CVEs (optional, configurable severity thresholds)
5. **Push** — skopeo copies the OCI layout to the private registry under a chosen name and tag

---

## External Registries & Sync

- Add external Docker-compatible registries (Docker Hub, GHCR, Quay, self-hosted…)
- Test connectivity and authentication directly from the UI
- Sync local images to an external registry (full registry or per-image/folder)

---

## Data Persistence

All persistent data is stored in **/var/lib/portalcrane** inside the container. Mount a volume to this path to retain data across container restarts:

```
-v /portalcrane_data:/var/lib/portalcrane
```

Stored data includes: the generated admin password hash (`admin_password.hash`), the JWT signing secret (`secret_key`), local users, OIDC configuration, folder permissions, external registries, audit logs, and the registry image data itself.

---

## Development

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

The GitHub Actions workflow (`.github/workflows/docker-publish.yml`) automatically:

1. Builds a image (`linux/amd64`) on every push to `main` and on version tags
2. Publishes to **Docker Hub** (`cyrius44/portalcrane`) and **GHCR** (`ghcr.io/cyr-ius/portalcrane`)
3. Updates the Docker Hub description from this README

Image tags follow semantic versioning: `latest`, `edge`, `X`, `X.Y`, `X.Y.Z`, `sha-<commit>`.

---

## Screenshots

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/18e00fb2-76e2-4ece-8ece-fc11fad16eff" />

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/ebf83322-14ba-4331-a9d3-72d04737d9a5" />

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/7ba30cdf-ee0f-4693-a796-bec6a17f692e" />

---

## License

MIT — see [LICENSE](LICENSE) for details.

## About

Author: [@cyr-ius](https://github.com/cyr-ius) — Sponsor: [GitHub Sponsors](https://github.com/sponsors/cyr-ius)
