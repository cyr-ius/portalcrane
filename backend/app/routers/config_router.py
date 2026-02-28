"""
Portalcrane - Config Router
Exposes non-sensitive application configuration to the frontend.
Sensitive values (credentials, secret keys) are never returned here.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import Settings, get_settings
from .auth import UserInfo, require_admin

router = APIRouter()


class PublicConfig(BaseModel):
    """Non-sensitive application configuration exposed to the frontend."""

    vuln_scan_enabled: bool
    vuln_scan_severities: str
    vuln_ignore_unfixed: bool
    vuln_scan_timeout: str


class OidcConfig(BaseModel):
    """Subset of OIDC configuration relevant to the frontend."""

    oidc_enabled: bool
    oidc_authority: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str
    oidc_post_logout_redirect_uri: str
    oidc_response_type: str
    oidc_scope: str


@router.get("/public", response_model=PublicConfig)
async def get_public_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    Return the non-sensitive subset of the server configuration.
    Used by the frontend to initialise defaults for scan toggles.
    """
    return PublicConfig(
        vuln_scan_enabled=settings.vuln_scan_enabled,
        vuln_scan_severities=settings.vuln_scan_severities,
        vuln_ignore_unfixed=settings.vuln_ignore_unfixed,
        vuln_scan_timeout=settings.vuln_scan_timeout,
    )


@router.get("/oidc", response_model=OidcConfig)
async def get_oidc_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    Return the OIDC configuration relevant to the frontend.
    Used to determine whether OIDC login is enabled and to configure the OIDC client.
    """
    return OidcConfig(
        oidc_enabled=settings.oidc_enabled,
        oidc_authority=settings.oidc_issuer,
        oidc_client_id=settings.oidc_client_id,
        oidc_client_secret=settings.oidc_client_secret,
        oidc_redirect_uri=settings.oidc_redirect_uri,
        oidc_post_logout_redirect_uri=settings.oidc_post_logout_redirect_uri,
        oidc_response_type=settings.oidc_response_type,
        oidc_scope=settings.oidc_scope,
    )
