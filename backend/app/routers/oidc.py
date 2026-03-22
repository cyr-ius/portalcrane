"""
Portalcrane - OIDC Router
Routes dedicated to the OpenID Connect flow:
  - GET  /oidc/config          → public config for the login page (unauthenticated)
  - POST /oidc/callback        → exchange authorization code for a local JWT
  - GET  /oidc/settings        → full config for the settings page (admin only)
  - PUT  /oidc/settings        → persist config overrides (admin only)

OIDC user provisioning strategy (just-in-time):
  - On first successful SSO login the user is automatically created in
    local_users.json with auth_source="oidc" and no password hash.
  - If an admin has previously deleted the account the callback returns 403
    (access revoked) instead of re-creating it silently.
  - Subsequent logins perform an upsert to keep the record up-to-date.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..core.jwt import (
    Token,
    UserInfo,
    create_access_token,
    require_admin,
)
from ..routers.auth import AUTH_SOURCE_OIDC, _load_users, _save_users, is_oidc_revoked
from ..services.oidc_service import (
    OidcAdminSettings,
    OidcPublicConfig,
    build_public_config,
    exchange_code_for_username,
    resolve_oidc_settings,
    save_oidc_config,
)

router = APIRouter()


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


# ── Just-in-time provisioning ─────────────────────────────────────────────────


def _provision_oidc_user(username: str) -> None:
    """Create or refresh an OIDC user entry in local_users.json.

    - If the username is in the revocation list (is_oidc_revoked) the call
      raises 403 — the admin explicitly deleted this account.
    - If the user already exists (auth_source='oidc') the record is left
      unchanged (no password_hash, is_admin preserved).
    - If the username does not exist a new record is created with
      auth_source='oidc' and no password_hash.
    """
    if is_oidc_revoked(username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your account has been revoked. Please contact your administrator."
            ),
        )

    users = _load_users()
    existing = next((u for u in users if u["username"] == username), None)

    if existing is None:
        # First SSO login — create the record
        entry = {
            "id": str(uuid.uuid4()),
            "username": username,
            "auth_source": AUTH_SOURCE_OIDC,
            "is_admin": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        users.append(entry)
        _save_users(users)


# ── Public endpoint (no authentication required) ──────────────────────────────


@router.get("/config", response_model=OidcPublicConfig)
async def get_oidc_public_config(settings: Settings = Depends(get_settings)):
    """Return OIDC configuration for the frontend login page.

    Merges env-var defaults with persisted overrides and enriches the payload
    with authorization_endpoint and end_session_endpoint fetched from the
    provider's discovery document.  Returns a disabled config when OIDC is off.
    """
    return await build_public_config(settings)


# ── Callback (no authentication required — called by the browser redirect) ───


@router.post("/callback", response_model=Token)
async def oidc_callback(
    code: str,
    settings: Settings = Depends(get_settings),
):
    """Exchange an OIDC authorization code for a Portalcrane JWT.

    Flow:
    1. Exchange the authorization code for a username via the token endpoint.
    2. Provision (or verify) the user in local_users.json (just-in-time).
       Raises 403 when the account has been revoked by an admin.
    3. Issue a local Portalcrane JWT for the authenticated user.
    """
    try:
        username = await exchange_code_for_username(code, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"OIDC callback failed: {exc}",
        )

    # Provision or check revocation — may raise 403
    _provision_oidc_user(username)

    access_token = create_access_token({"sub": username}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


# ── Admin settings (admin role required) ──────────────────────────────────────


@router.get("/settings", response_model=OidcAdminSettings)
async def get_oidc_settings(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """Return full OIDC settings including client_secret (admin only).

    The client_secret is a sensitive credential and must never be exposed to
    regular users. Only administrators may access this endpoint.
    """
    return resolve_oidc_settings(settings)


@router.put("/settings", response_model=OidcAdminSettings)
async def update_oidc_settings(
    payload: OidcAdminSettings,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """Persist OIDC configuration overrides (admin only).

    Saved values take precedence over environment variables on next request.
    """
    save_oidc_config(payload.model_dump())
    return resolve_oidc_settings(settings)
