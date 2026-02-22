"""
Portalcrane - Docker Registry Management Application
Main FastAPI application entry point
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routers import (
    about,
    auth,
    config_router,
    dashboard,
    registry,
    registry_proxy,
    staging,
)

_FRONTEND_DIR = "/app/frontend/dist/portalcrane/browser"
_INDEX_HTML = os.path.join(_FRONTEND_DIR, "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown events."""
    # Startup: ensure staging directory exists
    staging_dir = os.getenv("STAGING_DIR", "/tmp/staging")
    os.makedirs(staging_dir, exist_ok=True)
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


# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": "Portalcrane"}


# Serve Angular frontend static files (production)
if os.path.exists(_FRONTEND_DIR):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIR, "assets")),
        name="assets",
    )
    # Mount the full browser dir under a dedicated prefix for hashed chunks
    app.mount(
        "/static",
        StaticFiles(directory=_FRONTEND_DIR),
        name="static",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        """
        Catch-all route: serve index.html for any unknown path so that
        Angular's client-side router can handle navigation (SPA fallback).
        Static asset requests (js/css chunks) are handled by the mounts above
        and will never reach this handler.
        """
        # If the requested file physically exists within the frontend dir, serve it directly
        base = Path(_FRONTEND_DIR).resolve()
        candidate = (base / full_path).resolve()

        # Ensure the resolved candidate path is still inside the frontend directory
        is_within_base = False
        if hasattr(candidate, "is_relative_to"):
            # Python 3.9+
            is_within_base = candidate.is_relative_to(base)
        else:
            try:
                candidate.relative_to(base)
                is_within_base = True
            except ValueError:
                is_within_base = False

        if is_within_base and candidate.is_file():
            return FileResponse(candidate)
        # Otherwise fall back to index.html (Angular will handle the route)
        return FileResponse(_INDEX_HTML)
