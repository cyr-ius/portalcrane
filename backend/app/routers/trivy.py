from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..services.trivy_service import (
    get_trivy_db_info,
    has_explicit_tag_or_digest,
    scan_image,
    update_trivy_db,
)
from ..core.jwt import UserInfo, require_admin, get_current_user

router = APIRouter()


class VulnConfig(BaseModel):
    """Non-sensitive application configuration exposed to the frontend."""

    vuln_scan_enabled: bool
    vuln_scan_severities: str
    vuln_ignore_unfixed: bool
    vuln_scan_timeout: str


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


@router.get("/state", response_model=VulnConfig)
async def get_public_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    Return the non-sensitive subset of the server configuration.
    Used by the frontend to initialise defaults for scan toggles.
    """
    return VulnConfig(
        vuln_scan_enabled=settings.vuln_scan_enabled,
        vuln_scan_severities=settings.vuln_scan_severities,
        vuln_ignore_unfixed=settings.vuln_ignore_unfixed,
        vuln_scan_timeout=settings.vuln_scan_timeout,
    )
