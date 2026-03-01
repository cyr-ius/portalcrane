# 🐳 Portalcrane

**Portalcrane** is a self-hosted Docker Registry Manager.  
It provides a modern, intuitive interface to browse, search, and manage images and tags,
with a staging pipeline including vulnerability scanning.

<img width="1263" height="725" alt="Dashboard" src="https://github.com/user-attachments/assets/caa4d5e3-99aa-4463-8c4d-d8382aa6622d" />

---

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

## Quick Start

### Docker CLI

```bash
docker run -d \
  --name portalcrane \
  -p 8080:8080 \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=yourpassword \
  -e SECRET_KEY=your-secret-key \
  -v portalcrane_data:/var/lib/portalcrane \
  ghcr.io/cyr-ius/portalcrane:latest
```

Open **http://localhost:8080** and log in with your admin credentials.

### Docker Compose

```yaml
services:
  portalcrane:
    image: ghcr.io/cyr-ius/portalcrane:latest
    container_name: portalcrane
    ports:
      - "8080:8080"
    environment:
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD=changeme
      - SECRET_KEY=change-this-secret-key-in-production
    volumes:
      - portalcrane_data:/var/lib/portalcrane
    restart: unless-stopped

volumes:
  portalcrane_data:
```

---

## Environment Variables

### Authentication

| Variable         | Description                                   | Default                                |
| ---------------- | --------------------------------------------- | -------------------------------------- |
| `ADMIN_USERNAME` | Built-in admin username                       | `admin`                                |
| `ADMIN_PASSWORD` | Built-in admin password                       | `changeme`                             |
| `SECRET_KEY`     | JWT signing secret — **change in production** | `change-this-secret-key-in-production` |

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

### Docker Hub (optional — staging pipeline)

| Variable             | Description                                 | Default |
| -------------------- | ------------------------------------------- | ------- |
| `DOCKERHUB_USERNAME` | Docker Hub username for authenticated pulls | —       |
| `DOCKERHUB_PASSWORD` | Docker Hub password / access token          | —       |

### Vulnerability Scanning (Trivy)

| Variable               | Description                                   | Default         |
| ---------------------- | --------------------------------------------- | --------------- |
| `VULN_SCAN_ENABLED`    | Enable Trivy CVE scan in the staging pipeline | `true`          |
| `VULN_SCAN_SEVERITIES` | Blocking severity levels (comma-separated)    | `CRITICAL,HIGH` |
| `VULN_IGNORE_UNFIXED`  | Ignore CVEs with no available fix             | `false`         |
| `VULN_SCAN_TIMEOUT`    | Trivy scan timeout                            | `5m`            |

### HTTP Proxy (optional)

| Variable      | Description                    | Default               |
| ------------- | ------------------------------ | --------------------- |
| `HTTP_PROXY`  | HTTP proxy URL                 | —                     |
| `HTTPS_PROXY` | HTTPS proxy URL                | —                     |
| `NO_PROXY`    | Comma-separated no-proxy hosts | `localhost,127.0.0.1` |

### Logging

| Variable    | Description                                     | Default |
| ----------- | ----------------------------------------------- | ------- |
| `LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO`  |

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

- **Environment admin** — the built-in account defined via `ADMIN_USERNAME` / `ADMIN_PASSWORD`. It cannot be deleted or modified through the UI.
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
-v portalcrane_data:/var/lib/portalcrane
```

Stored data includes: local users, OIDC configuration, folder permissions, external registries, audit logs, and the registry image data itself.

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

1. Builds a multi-arch image (`linux/amd64`, `linux/arm64`) on every push to `main` and on version tags
2. Publishes to **Docker Hub** (`cyr-ius/portalcrane`) and **GHCR** (`ghcr.io/cyr-ius/portalcrane`)
3. Updates the Docker Hub description from this README

Image tags follow semantic versioning: `latest`, `edge`, `X`, `X.Y`, `X.Y.Z`, `sha-<commit>`.

---

## Screenshots

<img width="1167" height="827" alt="Images list" src="https://github.com/user-attachments/assets/db327776-d66d-4dc1-8900-29a53cb78d74" />

<img width="1263" height="725" alt="Staging pipeline" src="https://github.com/user-attachments/assets/3d0401a9-7c73-4185-a569-7f847db0b704" />

---

## License

MIT — see [LICENSE](LICENSE) for details.  
Author: [@cyr-ius](https://github.com/cyr-ius)
