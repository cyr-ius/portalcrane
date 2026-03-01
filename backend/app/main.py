"""
Portalcrane - Docker Registry Management Application
Main FastAPI application entry point
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


from .config import STAGING_DIR, get_settings
from .routers import (
    about,
    auth,
    config_router,
    dashboard,
    external_registries,
    folders,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown events."""
    # Startup: ensure staging directory exists
    os.makedirs(STAGING_DIR, exist_ok=True)
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="Portalcrane API",
    description="Docker Registry Management API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware - restrict in production via env
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(registry.router, prefix="/api/registry", tags=["Registry"])
app.include_router(staging.router, prefix="/api/staging", tags=["Staging"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(config_router.router, prefix="/api/config", tags=["Configuration"])
app.include_router(about.router, prefix="/api", tags=["About"])
app.include_router(
    registry_proxy.router, prefix="/registry-proxy", tags=["Registry Proxy"]
)
app.include_router(registry_proxy.router, prefix="", tags=["Registry Proxy (root v2)"])
app.include_router(folders.router, prefix="/api/folders", tags=["Folders"])
app.include_router(system.router)
app.include_router(
    external_registries.router, prefix="/api/external", tags=["External Registries"]
)


# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": "Portalcrane"}


# Serve Angular frontend static files (production)
if _FRONTEND_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIR / "assets")),
        name="assets",
    )
    # Mount the full browser dir under a dedicated prefix for hashed chunks
    app.mount(
        "/static",
        StaticFiles(directory=str(_FRONTEND_DIR)),
        name="static",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        """
        Catch-all route: serve index.html for any unknown path so that
        Angular's client-side router can handle navigation (SPA fallback).
        Static asset requests (js/css chunks) are handled by the mounts above
        and will never reach this handler.

        Security: path traversal is prevented by resolving the candidate path
        and asserting it stays within _FRONTEND_DIR before serving it.
        """
        # Resolve the candidate path to eliminate any ".." sequences
        candidate = (_FRONTEND_DIR / full_path).resolve()

        # Guard against path traversal: the resolved path must remain inside
        # the frontend directory. is_relative_to() returns False for any path
        # that escapes the root (e.g. /etc/passwd, ../../secret).
        if not candidate.is_relative_to(_FRONTEND_DIR):
            raise HTTPException(status_code=404, detail="Not found")

        # Serve the file only if it physically exists; fall back to index.html
        # so that Angular's client-side router can handle the navigation.
        if candidate.is_file():
            return FileResponse(candidate)

        # Unknown paths are handled client-side by Angular
        return FileResponse(_INDEX_HTML)
