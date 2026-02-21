# Portalcrane 🔦

**Portalcrane** is a modern web application for managing Docker Registry (CNCF Distribution).
It provides a beautiful, intuitive interface to browse, search, manage images and tags,
with a staging pipeline including antivirus scanning.

## Features

- 🎨 Modern UI with light/dark/auto themes
- 🔐 Local admin authentication + OIDC support
- 📦 Browse, search, paginate images and tags
- 🗑️ Delete images or individual tags
- 🏷️ Add new tags (retag)
- 🚀 Staging pipeline: Pull from Docker Hub → ClamAV scan → Push to registry
- 📊 Dashboard with stats (image count, disk usage, largest image)
- 🔍 Advanced mode for detailed image metadata
- 🐳 Single container deployment

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REGISTRY_URL` | Docker Registry URL | `http://localhost:5000` |
| `REGISTRY_USERNAME` | Registry basic auth username | - |
| `REGISTRY_PASSWORD` | Registry basic auth password | - |
| `ADMIN_USERNAME` | Portalcrane admin username | `admin` |
| `ADMIN_PASSWORD` | Portalcrane admin password | `changeme` |
| `SECRET_KEY` | JWT secret key | random |
| `OIDC_ENABLED` | Enable OIDC authentication | `false` |
| `OIDC_ISSUER` | OIDC issuer URL | - |
| `OIDC_CLIENT_ID` | OIDC client ID | - |
| `OIDC_CLIENT_SECRET` | OIDC client secret | - |
| `OIDC_REDIRECT_URI` | OIDC redirect URI | - |
| `DOCKERHUB_USERNAME` | Docker Hub username (optional) | - |
| `DOCKERHUB_PASSWORD` | Docker Hub password (optional) | - |
| `CLAMAV_HOST` | ClamAV host | `localhost` |
| `CLAMAV_PORT` | ClamAV port | `3310` |
| `STAGING_DIR` | Staging directory for pulled images | `/tmp/staging` |
| `ADVANCED_MODE` | Enable advanced mode by default | `false` |

## Quick Start

```bash
docker run -d \
  -p 8080:8080 \
  -e REGISTRY_URL=http://your-registry:5000 \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=yourpassword \
  -e SECRET_KEY=your-secret-key \
  portalcrane:latest
```

## Development

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
cd frontend
npm install
ng serve
```

## Architecture

- **Backend**: FastAPI + Python (async)
- **Frontend**: Angular 21 (Signals, Zoneless, Signal Forms)
- **Styling**: Bootstrap 5 + Bootstrap Icons
- **Container**: Single Nginx + Uvicorn container
