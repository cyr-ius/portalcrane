# Installation

Portalcrane ships as a single Docker image containing the frontend, the
backend API, the embedded registry, and the Trivy vulnerability scanner.
There is nothing else to install.

## Prerequisites

- Docker 24+ (or a compatible container runtime)
- Docker Compose v2 (optional, only if you prefer Compose over the CLI)
- A persistent volume for `/var/lib/portalcrane` — **strongly recommended**,
  see [Data Persistence](#data-persistence) below

## Docker CLI

```bash
docker run -d \
  --name portalcrane \
  -p 8000:8000 \
  -v /portalcrane_data:/var/lib/portalcrane \
  cyrius44/portalcrane:latest
```

No credentials need to be provided up front. On first launch Portalcrane
auto-generates a secure admin password and a JWT `SECRET_KEY`, both persisted
under the data volume. The admin password is **printed once in the container
logs** — retrieve it with:

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Then open **http://localhost:8000** and log in as `admin` with that password.

!!! warning "Mount a persistent volume"
Without a volume on `/var/lib/portalcrane`, the generated admin password
and JWT secret key are **regenerated on every container restart** — you
would be locked out and every previously issued session/token would be
invalidated. See [Security Notes](#security-notes) for details.

## Docker Compose

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

```bash
docker compose up -d
```

## Images and tags

Images are published to both **Docker Hub** (`cyrius44/portalcrane`) and
**GHCR** (`ghcr.io/cyr-ius/portalcrane`), for `linux/amd64` and `linux/arm64`
(Raspberry Pi, Apple Silicon). Available tags follow semantic versioning:

| Tag                 | Meaning                               |
| ------------------- | ------------------------------------- |
| `latest`            | Most recent tagged release            |
| `edge`              | Latest build from `main` (unreleased) |
| `X`, `X.Y`, `X.Y.Z` | A specific semantic version           |
| `sha-<commit>`      | A specific commit build               |

## Ports

| Port   | Purpose                                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------------------ |
| `8000` | Portalcrane UI + REST API + registry proxy (`docker login`/`pull`/`push` go through here)                    |
| `5000` | Embedded Docker registry — only exposed directly in the **dev stack** (see [Development](../development.md)) |

## Healthcheck

`GET /api/health` returns a JSON status payload and is what the built-in
Docker healthcheck polls.

## Data persistence

All persistent state lives under **`/var/lib/portalcrane`** (configurable via
`DATA_DIR`). Mount a volume there to retain data across restarts:

```bash
-v /portalcrane_data:/var/lib/portalcrane
```

This directory holds:

- the generated admin password hash (`admin_password.hash`)
- the JWT signing secret (`secret_key`)
- local users (`local_users.json`) and groups (`groups.json`)
- folder permissions (`folders.json`)
- OIDC configuration overrides
- external registry definitions (including stored credentials)
- personal access tokens (`personal_tokens.json`)
- audit logs
- the embedded registry's image data (`registry/`)
- the Trivy vulnerability database cache (`cache/trivy/`)
- the staging area used by the pull/push pipeline (`cache/staging/`)

## Security notes

- The admin password is generated and printed in the logs on first launch
  (default user: `admin`). It cannot be set through an environment variable —
  this is intentional, so a plaintext password never sits in your compose
  file or shell history. To rotate it, delete `DATA_DIR/admin_password.hash`
  and restart the container; a new password is generated and printed again.
- `SECRET_KEY` is auto-generated and persisted under `DATA_DIR` on first
  launch. Only set it explicitly if you need to **share a fixed secret
  across multiple instances** (e.g. an HA setup behind a shared volume).
- If you expose the UI publicly, enable HTTPS — either at a reverse proxy in
  front of Portalcrane, or natively via `PRIVATE_KEY` / `PUBLIC_KEY` (see
  [TLS & Reverse Proxy](../configuration/tls-reverse-proxy.md)).

## Next steps

Continue with the [Quick Start](quick-start.md) to log in, pull your first
image, and try the registry proxy with the Docker CLI.
