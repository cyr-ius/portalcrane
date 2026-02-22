"""
Portalcrane - Docker Registry Management Application
Main FastAPI application entry point
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routers import auth, dashboard, registry, registry_proxy, staging

# ── Logging configuration ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logging.getLogger("portalcrane.audit").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown events."""
    # Startup: ensure staging directory exists
    staging_dir = os.getenv("STAGING_DIR", "/tmp/staging")
    os.makedirs(staging_dir, exist_ok=True)
    yield


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
app.include_router(
    registry_proxy.router, prefix="/registry-proxy", tags=["Registry Proxy"]
)


# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": "Portalcrane"}


# Serve Angular frontend static files (production)
_FRONTEND_DIR = "/app/frontend/dist/portalcrane/browser"
if os.path.exists(_FRONTEND_DIR):
    app.mount(
        "/",
        StaticFiles(directory=_FRONTEND_DIR, html=True),
        name="frontend",
    )
