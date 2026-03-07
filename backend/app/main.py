"""
Portalcrane - Docker Registry Management Application
Main FastAPI application entry point.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routers import (
    about,
    auth,
    config_router,
    dashboard,
    external_registries,
    folders,
    oidc,
    registry,
    registry_proxy,
    staging,
    system,
)

_FRONTEND_DIR = Path("/app/frontend/dist/portalcrane/browser").resolve()
_INDEX_HTML = _FRONTEND_DIR / "index.html"

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


app = FastAPI(
    title="Portalcrane API",
    description="Docker Registry Management API",
    version="1.0.0",
)


# ── Routers ───────────────────────────────────────────────────────────────────

# Local authentication + user management
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])

# OIDC flow (public config, callback, admin settings)
app.include_router(oidc.router, prefix="/api/oidc", tags=["OIDC"])

app.include_router(registry.router, prefix="/api/registry", tags=["Registry"])
app.include_router(staging.router, prefix="/api/staging", tags=["Staging"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(config_router.router, prefix="/api/config", tags=["Configuration"])
app.include_router(about.router, prefix="/api", tags=["About"])
app.include_router(registry_proxy.router, prefix="", tags=["Registry Proxy"])
app.include_router(folders.router, prefix="/api/folders", tags=["Folders"])
app.include_router(system.router)
app.include_router(
    external_registries.router, prefix="/api/external", tags=["External Registries"]
)


# ── Health check ──────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": "Portalcrane"}


# ── Angular SPA fallback ──────────────────────────────────────────────────────

if _FRONTEND_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIR / "assets")),
        name="assets",
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(_FRONTEND_DIR)),
        name="static",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        """Catch-all: serve index.html so Angular's router can handle navigation."""
        candidate = (_FRONTEND_DIR / full_path).resolve()
        try:
            # Ensure the resolved candidate path is within the frontend directory
            candidate.relative_to(_FRONTEND_DIR)
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_INDEX_HTML)
