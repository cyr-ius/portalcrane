"""
Portalcrane - OIDC Router
Routes dedicated to the OpenID Connect flow:
  - GET  /oidc/config          → public config for the login page (unauthenticated)
  - POST /oidc/callback        → exchange authorization code for a local JWT
  - GET  /oidc/settings        → full config for the settings page (admin)
  - PUT  /oidc/settings        → persist config overrides (admin)
"""

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings, get_settings
from ..core.jwt import (
    Token,
    UserInfo,
    create_access_token,
    get_current_user,
    require_admin,
)
from ..services.oidc_service import (
    OidcAdminSettings,
    OidcPublicConfig,
    build_public_config,
    exchange_code_for_username,
    resolve_oidc_settings,
    save_oidc_config,
)

router = APIRouter()


# ─── Public endpoint (no authentication required) ────────────────────────────


@router.get("/config", response_model=OidcPublicConfig)
async def get_oidc_public_config(settings: Settings = Depends(get_settings)):
    """Return OIDC configuration for the frontend login page.

    Merges env-var defaults with persisted overrides and enriches the payload
    with authorization_endpoint and end_session_endpoint fetched from the
    provider's discovery document.  Returns a disabled config when OIDC is off.
    """
    return await build_public_config(settings)


# ─── Callback (no authentication required — called by the browser redirect) ──


@router.post("/callback", response_model=Token)
async def oidc_callback(
    code: str,
    settings: Settings = Depends(get_settings),
):
    """Exchange an OIDC authorization code for a Portalcrane JWT.

    The browser is redirected here by the OIDC provider after a successful
    login.  The code is exchanged for an id_token / access_token, the username
    is extracted (userinfo endpoint → id_token claims fallback), and a local
    JWT is issued.
    """
    try:
        username = await exchange_code_for_username(code, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"OIDC callback failed: {exc}",
        )

    access_token = create_access_token({"sub": username}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


# ─── Admin settings (authentication required) ────────────────────────────────


@router.get("/settings", response_model=OidcAdminSettings)
async def get_oidc_settings(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Return full OIDC settings (env defaults merged with persisted overrides).

    Accessible to any authenticated user so the Settings page can display them.
    The client_secret is returned here because the page needs to allow editing.
    """
    return resolve_oidc_settings(settings)


@router.put("/settings", response_model=OidcAdminSettings)
async def save_oidc_settings(
    payload: OidcAdminSettings,
    _: UserInfo = Depends(require_admin),
):
    """Persist OIDC settings to the JSON override file.

    Values saved here override env vars at runtime without requiring a restart.
    Only admins can call this endpoint.
    """
    save_oidc_config(payload.model_dump())
    return payload
