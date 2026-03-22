"""
Portalcrane - Docker Registry Management Application
Main FastAPI application entry point.

Migration note: registry.py router and RegistryService have been removed.
All registry operations now route through the unified V2 provider layer via
external_registries.py (for browsing/tag management) and system.py (for
maintenance operations: GC, ghost repos, copy, ping).
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .routers import (
    about,
    auth,
    dashboard,
    external_registries,
    folders,
    network,
    oidc,
    personal_tokens,
    registry_proxy,
    staging,
    system,
    trivy,
)
from .routers.folders import ensure_root_folder_exists
from .services.audit_service import log_web_ui_action
from .services.proxy_service import (
    apply_proxy_to_os_environ,
    apply_syslog_config,
    resolve_proxy_settings,
    resolve_syslog_settings,
)
from .services.trivy_service import update_trivy_db

_TRIVY_DB_REFRESH_INTERVAL = 86400
_FRONTEND_DIR = Path("/app/frontend/dist/portalcrane/browser").resolve()
_INDEX_HTML = _FRONTEND_DIR / "index.html"

logger = logging.getLogger(__name__)
settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ── Trivy DB background task ──────────────────────────────────────────────────


async def _trivy_db_updater_loop() -> None:
    """Background task: download the Trivy vulnerability database at startup,
    then refresh it every 24 hours.

    Runs inside the uvicorn process so it inherits os.environ directly —
    including any proxy override applied by apply_proxy_to_os_environ().
    """
    while True:
        logger.info("Trivy DB updater: starting database download...")
        try:
            result = await update_trivy_db()
            if result["success"]:
                logger.info("Trivy DB updater: database updated successfully.")
            else:
                logger.warning(
                    "Trivy DB updater: download failed — %s",
                    result.get("output", "unknown error"),
                )
        except Exception as exc:
            logger.error("Trivy DB updater: unexpected error — %s", exc)

        logger.info(
            "Trivy DB updater: next refresh in %dh.",
            _TRIVY_DB_REFRESH_INTERVAL // 3600,
        )
        await asyncio.sleep(_TRIVY_DB_REFRESH_INTERVAL)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown handler."""
    proxy_cfg = resolve_proxy_settings(settings)
    apply_proxy_to_os_environ(proxy_cfg)
    apply_syslog_config(resolve_syslog_settings())
    ensure_root_folder_exists()
    db_task = asyncio.create_task(_trivy_db_updater_loop())
    yield
    db_task.cancel()
    try:
        await db_task
    except asyncio.CancelledError:
        pass
    logger.info("Trivy DB updater task stopped.")


# ── FastAPI app ───────────────────────────────────────────────────────────────


app = FastAPI(
    title="Portalcrane API",
    description="Docker Registry Management API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def audit_web_ui_actions(request, call_next):
    """Audit middleware: log all non-GET API requests."""
    start = perf_counter()
    response = await call_next(request)
    elapsed = perf_counter() - start

    await log_web_ui_action(
        request=request,
        status_code=response.status_code,
        settings=settings,
        elapsed_s=elapsed,
    )
    return response


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(
    personal_tokens.router, prefix="/api/auth", tags=["Personal Access Tokens"]
)
app.include_router(oidc.router, prefix="/api/oidc", tags=["OIDC"])
app.include_router(staging.router, prefix="/api/staging", tags=["Staging"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(about.router, prefix="/api", tags=["About"])
app.include_router(registry_proxy.router, prefix="", tags=["Registry Proxy"])
app.include_router(folders.router, prefix="/api/folders", tags=["Folders"])
app.include_router(trivy.router, prefix="/api/trivy", tags=["Trivy"])
app.include_router(network.router, prefix="/api/network", tags=["Network"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
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
            candidate.relative_to(_FRONTEND_DIR)
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_INDEX_HTML)
