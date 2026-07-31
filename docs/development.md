# Development

This page covers running Portalcrane's frontend and backend locally,
outside the single production container — useful for contributing or
debugging.

!!! info "This differs from the top-level README"
    The backend is managed with [`uv`](https://docs.astral.sh/uv/) and
    `pyproject.toml`/`uv.lock`, not `pip` and a `requirements.txt`. This
    page follows the project's own [`AGENTS.md`](https://github.com/cyr-ius/portalcrane/blob/main/AGENTS.md),
    which is the authoritative source for local tooling.

## System requirements

- Python `>= 3.14`
- [`uv`](https://docs.astral.sh/uv/) installed
- Node.js `>= 22`
- npm

## Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`uv sync` installs from `pyproject.toml` + `uv.lock` into an isolated
virtualenv — don't use `pip install -r requirements.txt`, there is no such
file. The backend expects the embedded registry to be reachable at
`localhost:5000` for most features to work end-to-end; use the
[dev Docker Compose stack](#full-stack-dev-compose-stack) below if you need
a real registry alongside a locally-running backend.

### Linting, formatting & type-checking

```bash
cd backend
uv run ruff check app
uv run ruff format --check app
uv run mypy app
```

These are the checks CI runs — run them before opening a PR. Ruff is
configured for an 88-character line length with `E, F, I, N, W, UP` rule
sets (`E501` — line too long — is explicitly ignored).

## Frontend

```bash
cd frontend
npm ci
npm start
```

`npm start` runs `ng serve --host 0.0.0.0`. The dev server proxies `/api`
and `/v2` requests to `http://localhost:8000` (see `frontend/proxy.conf.json`),
so run the backend first (or the full Docker Compose stack) for the UI to
have something to talk to.

Prefer `npm ci` over `npm install` — it installs exactly what
`package-lock.json` pins, which is what CI does too.

### Build

```bash
cd frontend
npm run build
```

Produces a production build (`ng build --configuration production`) under
`frontend/dist/`.

## Full-stack dev Compose stack

The `docker-compose.yml` at the repository root builds the local image and
also starts a **dedicated registry on port `5000`**, storing its data in the
`registry_data` volume — useful when you want a realistic registry backend
without running the whole Portalcrane container:

```bash
docker compose up -d
```

If you're running the backend locally (not in the container) and want
direct registry access for debugging, use `<host>:5000` — that's the raw
Distribution registry, unauthenticated, bypassing Portalcrane's proxy
entirely.

## Project layout

```text
.
├── backend/          # FastAPI backend (Python, uv/pyproject.toml)
│   └── app/
│       ├── routers/      # One module per API area (auth, folders, staging, ...)
│       ├── services/     # Business logic (registries, transfers, Trivy, email...)
│       ├── core/          # JWT, cookies, crypto, bootstrap, security
│       └── static/        # Self-hosted Swagger UI assets
├── frontend/         # Angular 22 frontend (Signals, Zoneless, standalone components)
├── docker/           # supervisord.conf template, registry config template, entrypoint.sh
├── scripts/          # release.sh and other automation
└── docs/             # This documentation site (MkDocs)
```

## Cutting a release

Releases are produced by a helper script, not a manual tag push:

```bash
scripts/release.sh patch     # or minor / major / an explicit X.Y.Z
```

It bumps the frontend's version, commits that bump, tags **that** commit,
and pushes — so the version embedded in a release always matches its git
tag. Use `--no-push` to prepare the commit and tag locally without pushing
immediately.

Pushing the resulting `X.Y.Z` tag triggers the GitHub Actions release and
image-build workflow, which:

1. Builds the image (`linux/amd64`, `linux/arm64`) for that tag and for
   `main` pushes.
2. Publishes to **Docker Hub** (`cyrius44/portalcrane`) and **GHCR**
   (`ghcr.io/cyr-ius/portalcrane`).
3. Updates the Docker Hub description from the repository's README.

## Serving this documentation locally

If you want to preview changes to these docs:

```bash
pip install -r docs/requirements.txt
mkdocs serve
```

Then open `http://localhost:8000` (mkdocs' own dev server — stop the
Portalcrane container first, or pick a different port with `mkdocs serve -a
localhost:8001`, to avoid a port clash).
