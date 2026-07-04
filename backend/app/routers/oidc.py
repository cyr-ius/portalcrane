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
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..core.cookies import set_auth_cookie
from ..core.jwt import (
    Token,
    UserInfo,
    create_access_token,
    require_admin,
)
from ..routers.auth import (
    AUTH_SOURCE_LOCAL,
    AUTH_SOURCE_OIDC,
    _load_users,
    _save_users,
    is_oidc_revoked,
)
from ..services.oidc_service import (
    OidcAdminSettings,
    OidcIdentity,
    OidcPublicConfig,
    OidcTestResult,
    build_public_config,
    exchange_code_for_identity,
    has_admin_mapping,
    has_user_restriction,
    is_oidc_admin,
    is_oidc_user_allowed,
    resolve_oidc_settings,
    save_oidc_config,
    test_oidc_connection,
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


def _provision_oidc_user(identity: OidcIdentity, settings: Settings) -> None:
    """Create or refresh an OIDC user entry in local_users.json.

    Order of checks (anti-usurpation first):
    - If the username is in the revocation list (is_oidc_revoked) → 403
      (the admin explicitly deleted this account).
    - If the username collides with the built-in env-admin → 403. Otherwise an
      OIDC provider returning preferred_username="admin" would inherit admin.
    - If the username collides with an existing *local* account → 403. An OIDC
      identity must never bind onto a password-based account.
    - Admin rights are (re)computed from the OIDC config (admin group claim) on
      every login, so promote/demote take effect live. This only applies when
      the admin group mapping is configured; otherwise the role is managed
      manually (via the users panel) and is preserved across logins.
    - When restrict_to_groups is enabled, OIDC access becomes an allowlist: a
      user matching neither the admin nor the user mapping is denied (403)
      instead of being provisioned.
    - First login → a new record is created (auth_source='oidc', no password).
    """
    username = identity.username

    if is_oidc_revoked(username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your account has been revoked. Please contact your administrator."
            ),
        )

    # Anti-usurpation: never let an OIDC identity resolve to the env-admin.
    if username == settings.admin_username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This OIDC username collides with the local admin account and "
                "cannot be used. Please contact your administrator."
            ),
        )

    users = _load_users()
    existing = next((u for u in users if u["username"] == username), None)

    # Anti-usurpation: never let an OIDC identity bind onto a local account.
    if existing is not None and existing.get("auth_source") == AUTH_SOURCE_LOCAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This OIDC username collides with an existing local account and "
                "cannot be used. Please contact your administrator."
            ),
        )

    merged = resolve_oidc_settings(settings)
    admin_mapping = has_admin_mapping(merged)
    is_admin = is_oidc_admin(identity, merged)

    # Access allowlist: when restrict_to_groups is enabled, only admins or users
    # matching the regular-user mapping may log in.
    if (
        not is_admin
        and has_user_restriction(merged)
        and not is_oidc_user_allowed(identity, merged)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your account is not authorized to access Portalcrane. "
                "Please contact your administrator."
            ),
        )

    if existing is None:
        # First SSO login — create the record
        entry = {
            "id": str(uuid.uuid4()),
            "username": username,
            "auth_source": AUTH_SOURCE_OIDC,
            "is_admin": is_admin,
            "created_at": datetime.now(UTC).isoformat(),
        }
        users.append(entry)
        _save_users(users)
    elif admin_mapping and existing.get("is_admin", False) != is_admin:
        # Subsequent login — refresh admin status (live promote/demote).
        # Only when the admin group mapping is configured: without it the role
        # is managed manually and must not be overwritten here.
        existing["is_admin"] = is_admin
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
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    """Exchange an OIDC authorization code for a Portalcrane JWT.

    Flow:
    1. Exchange the authorization code for an identity (username + groups).
    2. Provision (or verify) the user in local_users.json (just-in-time).
       Raises 403 when the account has been revoked, collides with a local
       account, or matches the env-admin username (anti-usurpation).
    3. Issue a local Portalcrane JWT and store it in the HttpOnly auth cookie.
    """
    try:
        identity = await exchange_code_for_identity(code, settings)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"OIDC callback failed: {exc}",
        )

    # Provision, check revocation and anti-usurpation collisions — may raise 403
    _provision_oidc_user(identity, settings)

    access_token = create_access_token({"sub": identity.username}, settings)
    set_auth_cookie(response, request, access_token)
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

    Anti-lockout guard: OIDC-only mode disables every local login (including the
    env-admin), so it can only be enabled when OIDC is itself enabled AND the
    admin group-claim mapping is configured. Otherwise no one could ever obtain
    admin rights again.
    """
    if payload.oidc_only:
        if not payload.enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OIDC-only mode requires OIDC to be enabled.",
            )
        has_admin_group = bool(
            payload.admin_group_claim.strip() and payload.admin_group.strip()
        )
        if not has_admin_group:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "OIDC-only mode requires the admin group mapping: set both "
                    "admin_group_claim and admin_group."
                ),
            )

    save_oidc_config(payload.model_dump())
    return resolve_oidc_settings(settings)


@router.post("/test", response_model=OidcTestResult)
async def test_oidc_settings(
    payload: OidcAdminSettings,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """Run a live connectivity test against the OIDC provider (admin only).

    Validates that the provider is reachable, publishes the required endpoints,
    exposes a coherent issuer and signing keys, and that the client credentials
    are accepted. Uses the submitted (possibly unsaved) form values so an admin
    can validate the configuration before persisting it. An empty client_secret
    falls back to the stored value.
    """
    return await test_oidc_connection(payload, settings)
