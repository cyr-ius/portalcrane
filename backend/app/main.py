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
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import DATA_DIR, STAGING_DIR, app_settings
from .core.bootstrap import ensure_admin_credentials, ensure_secret_key
from .routers import (
    about,
    auth,
    dashboard,
    folders,
    network,
    oidc,
    personal_tokens,
    registries,
    registry_proxy,
    repositories,
    staging,
    system,
    transfer,
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
from .services.trivy_service import db_updater_loop

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=app_settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Resolve once at module load — avoids repeated filesystem calls per request.
project_root = Path(__file__).resolve().parents[2]
frontend_dist = (project_root / "frontend").resolve()
frontend_index = frontend_dist / "index.html"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response."""

    # Build CSP once at class level — one directive per list entry, auditable.
    _CSP_DIRECTIVES: list[str] = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",  # Angular + Swagger UI (self-hosted)
        "style-src 'self' 'unsafe-inline'",  # Bootstrap + Swagger UI (self-hosted)
        "img-src 'self' data: https:",  # logos, QR codes base64
        "font-src 'self' data:",  # Bootstrap Icons embedded font
        f"connect-src 'self' {app_settings.oidc_issuer}",  # API calls + Azure endpoints
        "worker-src 'self'",  # Angular Service Worker (PWA)
        "frame-ancestors 'none'",  # replaces X-Frame-Options
    ]
    _CSP: str = "; ".join(_CSP_DIRECTIVES) + ";"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = self._CSP
        return response


def _resolve_safe_path(full_path: str) -> Path | None:
    """Resolve a URL path to a filesystem path safely."""
    # Reject empty paths and dot-only segments immediately.
    stripped = full_path.strip()
    if not stripped or stripped in (".", ".."):
        return None

    # Resolve to absolute path — collapses all '..' and symlinks.
    candidate = (frontend_dist / stripped).resolve()

    # The candidate must be strictly inside frontend_dist (not equal to it).
    # is_relative_to() returns True even when candidate == frontend_dist,
    # so we add an explicit equality check to block directory root access.
    if candidate == frontend_dist:
        return None

    if not candidate.is_relative_to(frontend_dist):
        logger.warning(
            "Path traversal attempt blocked: raw=%r resolved=%s",
            full_path,
            candidate,
        )
        return None

    # Only serve regular files, never directories or special files.
    if not candidate.is_file():
        return None

    return candidate


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown handler."""
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(STAGING_DIR).mkdir(parents=True, exist_ok=True)
    ensure_secret_key(app_settings)
    ensure_admin_credentials(app_settings)
    proxy_cfg = resolve_proxy_settings(app_settings)
    apply_proxy_to_os_environ(proxy_cfg)
    apply_syslog_config(resolve_syslog_settings())
    ensure_root_folder_exists()
    # Only run the Trivy DB updater when Trivy is enabled; otherwise the embedded
    # server is not started (see docker/entrypoint.sh) and DB downloads are moot.
    db_task = (
        asyncio.create_task(db_updater_loop()) if app_settings.trivy_enabled else None
    )
    yield
    if db_task is not None:
        db_task.cancel()
        try:
            await db_task
        except asyncio.CancelledError:
            pass
        logger.info("Trivy DB updater task stopped.")


app = FastAPI(
    title="Portalcrane API",
    description="Docker Registry Management API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if app_settings.SWAGGER_ENABLE else None,
)
app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def audit_web_ui_actions(request, call_next):
    """Audit middleware: log all non-GET API requests."""
    start = perf_counter()
    response = await call_next(request)
    elapsed = perf_counter() - start

    await log_web_ui_action(
        request=request,
        status_code=response.status_code,
        settings=app_settings,
        elapsed_s=elapsed,
    )
    return response


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(about.router, prefix="/api", tags=["About"])
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(folders.router, prefix="/api/folders", tags=["Folders"])
app.include_router(repositories.router, prefix="/api/images", tags=["Images"])
app.include_router(network.router, prefix="/api/network", tags=["Network"])
app.include_router(oidc.router, prefix="/api/oidc", tags=["OIDC"])
app.include_router(
    personal_tokens.router, prefix="/api/auth", tags=["Personal Access Tokens"]
)
app.include_router(registries.router, prefix="/api/registries", tags=["Registries"])
app.include_router(registry_proxy.router, prefix="", tags=["Registry Proxy"])
app.include_router(staging.router, prefix="/api/staging", tags=["Staging"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(transfer.router, prefix="/api/transfer", tags=["Transfer"])
app.include_router(trivy.router, prefix="/api/trivy", tags=["Trivy"])


# ── Self-hosted static assets (Swagger UI, no Internet dependency) ─────────────
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/api/static", StaticFiles(directory=static_dir), name="static")


@app.get("/api/docs", include_in_schema=False)
async def swagger_ui():
    if not app_settings.SWAGGER_ENABLE:
        raise HTTPException(status_code=404, detail="Not Found")
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title="Portalcrane API",
        swagger_js_url="/api/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/api/static/swagger/swagger-ui.css",
        swagger_favicon_url="/favicon.ico",
    )


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": "Portalcrane"}


# ── Angular SPA fallback ──────────────────────────────────────────────────────


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str) -> FileResponse:
    """
    Serve Angular static files with path traversal protection.

    Requests for existing static assets (JS, CSS, images) are served directly.
    All other paths fall back to index.html to support client-side SPA routing.
    Unknown or unsafe paths also fall back to index.html rather than 404-ing,
    letting the Angular router handle the error page.
    """
    if not frontend_index.is_file():
        logger.error("SPA index.html not found at %s", frontend_index)
        raise HTTPException(status_code=503, detail="Frontend not available.")

    safe = _resolve_safe_path(full_path)
    if safe is not None:
        return FileResponse(safe)

    # SPA fallback: Angular router handles unknown client-side routes.
    return FileResponse(frontend_index)
