"""
Portalcrane - Trivy Router

Endpoints:
  GET  /api/trivy/db               — DB info (admin)
  POST /api/trivy/db/update        — Force DB refresh (admin)
  GET  /api/trivy/scan             — Scan an image (any authenticated user)
  GET  /api/trivy/state            — Effective vuln config (any authenticated user)
  PUT  /api/trivy/override         — Persist admin override (admin only)
  DELETE /api/trivy/override       — Remove admin override, revert to env vars (admin only)
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..services.trivy_service import (
    clear_vuln_override,
    get_trivy_db_info,
    has_explicit_tag_or_digest,
    resolve_vuln_config,
    save_vuln_override,
    scan_image,
    update_trivy_db,
)
from ..core.jwt import UserInfo, require_admin, get_current_user

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────────


class VulnConfig(BaseModel):
    """
    Effective vuln configuration returned to the frontend.

    vuln_scan_override = True  → a persisted admin override is active.
    vuln_scan_override = False → values come straight from env vars.
    """

    vuln_scan_override: bool
    vuln_scan_enabled: bool
    vuln_scan_severities: str
    vuln_ignore_unfixed: bool
    vuln_scan_timeout: str


class VulnOverridePayload(BaseModel):
    """Body for PUT /api/trivy/override."""

    vuln_scan_enabled: bool
    vuln_scan_severities: str
    vuln_ignore_unfixed: bool
    vuln_scan_timeout: str


# ── Trivy DB ──────────────────────────────────────────────────────────────────


@router.get("/db")
async def trivy_db_status(_: UserInfo = Depends(require_admin)):
    """Returns Trivy vulnerability database info and freshness status."""
    return await get_trivy_db_info()


@router.post("/db/update")
async def force_trivy_update(_: UserInfo = Depends(require_admin)):
    """Forces an immediate Trivy DB update."""
    result = await update_trivy_db()
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["output"])
    return result


# ── Scan ──────────────────────────────────────────────────────────────────────


@router.get("/scan")
async def scan(
    image: str = Query(..., description="Full image ref with explicit tag or digest"),
    severity: list[str] = Query(default=["HIGH", "CRITICAL"]),
    ignore_unfixed: bool = Query(default=False),
    _: UserInfo = Depends(get_current_user),
):
    """
    Scans a specific image from the local registry with Trivy.
    Returns grouped vulnerabilities with CVSS scores.
    """
    if not has_explicit_tag_or_digest(image):
        raise HTTPException(
            status_code=400,
            detail=(
                "Image reference must include an explicit tag or digest "
                "(example: production/redis:7.2 or production/redis@sha256:...)."
            ),
        )

    result = await scan_image(image, severity=severity, ignore_unfixed=ignore_unfixed)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


# ── State (effective config) ──────────────────────────────────────────────────


@router.get("/state", response_model=VulnConfig)
async def get_public_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Return the effective vuln configuration.

    Any authenticated user may call this endpoint — the frontend uses it to
    initialise scan toggles for all users, not just admins.

    Priority: persisted admin override > environment variables.
    """
    return VulnConfig(**resolve_vuln_config(settings))


# ── Override management (admin only) ─────────────────────────────────────────


@router.put("/override", response_model=VulnConfig)
async def set_vuln_override(
    payload: VulnOverridePayload,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    Persist a global vuln scan override that applies to ALL users.

    Writes DATA_DIR/vuln_override.json.  The next call to GET /state (by any
    user) will reflect these values instead of the env-var defaults.
    """
    save_vuln_override(
        {
            "vuln_scan_enabled": payload.vuln_scan_enabled,
            "vuln_scan_severities": payload.vuln_scan_severities,
            "vuln_ignore_unfixed": payload.vuln_ignore_unfixed,
            "vuln_scan_timeout": payload.vuln_scan_timeout,
        }
    )
    return VulnConfig(**resolve_vuln_config(settings))


@router.delete("/override", response_model=VulnConfig)
async def delete_vuln_override(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    Remove the persisted vuln override.  All users will revert to the
    environment-variable defaults on their next GET /state call.
    """
    clear_vuln_override()
    return VulnConfig(**resolve_vuln_config(settings))
