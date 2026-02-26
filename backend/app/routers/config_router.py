"""
Portalcrane - Config Router
Exposes non-sensitive application configuration to the frontend.
Sensitive values (credentials, secret keys) are never returned here.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import Settings, get_settings
from .auth import UserInfo, get_current_user

router = APIRouter()


class PublicConfig(BaseModel):
    """Non-sensitive application configuration exposed to the frontend."""

    advanced_mode: bool
    # Vulnerability scanning
    vuln_scan_enabled: bool
    vuln_scan_severities: str
    vuln_ignore_unfixed: bool
    vuln_scan_timeout: str


@router.get("/public", response_model=PublicConfig)
async def get_public_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Return the non-sensitive subset of the server configuration.
    Used by the frontend to initialise defaults for scan toggles
    and to determine whether advanced mode is enabled server-side.
    """
    return PublicConfig(
        advanced_mode=settings.advanced_mode,
        vuln_scan_enabled=settings.vuln_scan_enabled,
        vuln_scan_severities=settings.vuln_scan_severities,
        vuln_ignore_unfixed=settings.vuln_ignore_unfixed,
        vuln_scan_timeout=settings.vuln_scan_timeout,
    )
