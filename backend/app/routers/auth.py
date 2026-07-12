"""
Portalcrane - Authentication Router
Handles local authentication and user management:
  - POST /login           → JSON login endpoint
  - GET  /me              → current user information
  - GET/POST/PATCH/DELETE /users → local users CRUD

Pull/push permissions are managed exclusively through folder permissions.
OIDC routes are in routers/oidc.py.
JWT helpers and FastAPI dependencies are in core/jwt.py.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator

from ..config import DATA_DIR, Settings, get_settings
from ..core.bootstrap import set_admin_password
from ..core.cookies import clear_auth_cookie, set_auth_cookie
from ..core.jwt import (
    Token,
    UserInfo,
    create_access_token,
    get_current_user,
    is_user_disabled,
    require_admin,
)
from ..core.security import hash_password, verify_user
from ..services.audit_service import log_web_login, log_web_logout
from ..services.oidc_service import resolve_oidc_settings
from ..services.registries_service import delete_registries_for_owner
from .groups import remove_member_from_all_groups
from .personal_tokens import revoke_tokens_for_username

router = APIRouter()

_USERS_FILE = Path(f"{DATA_DIR}/local_users.json")
_REVOKED_OIDC_FILE = Path(f"{DATA_DIR}/oidc_revoked.json")

# Valid auth source values stored in the user record.
AUTH_SOURCE_LOCAL = "local"
AUTH_SOURCE_OIDC = "oidc"


# ─── Pydantic models ──────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """JSON body for the /login endpoint."""

    username: str
    password: str


class LocalUserPublic(BaseModel):
    """Local user representation returned to the frontend (no password hash).

    auth_source distinguishes local accounts (password-based) from OIDC accounts
    (provisioned automatically on first SSO login — no password stored).
    """

    id: str
    username: str
    is_admin: bool
    created_at: str
    auth_source: str = AUTH_SOURCE_LOCAL
    disabled: bool = False


class CreateUserRequest(BaseModel):
    """Payload to create a new local user.

    Only local accounts can be created through this endpoint.
    OIDC accounts are provisioned automatically by the OIDC callback.
    """

    username: str
    password: str
    is_admin: bool = False

    @field_validator("username")
    @classmethod
    def username_not_empty(cls, v: str) -> str:
        """Ensure username is non-empty and contains no spaces."""
        v = v.strip()
        if not v:
            raise ValueError("Username must not be empty")
        if " " in v:
            raise ValueError("Username must not contain spaces")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        """Ensure password is at least 8 characters long."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UpdateUserRequest(BaseModel):
    """Payload to update a local user (all fields optional).

    Changing the password of an OIDC user is rejected at the route level.
    Pull/push permissions are managed via folder rules.
    """

    password: str | None = None
    is_admin: bool | None = None
    disabled: bool | None = None


class DockerHubAccountSettings(BaseModel):
    """Docker Hub credentials bound to the current Portalcrane account."""

    username: str = ""
    has_password: bool = False


# ─── Local users helpers ──────────────────────────────────────────────────────


def _load_users() -> list[dict]:
    """Load local users from disk. Returns empty list when file is absent."""
    try:
        if _USERS_FILE.exists():
            return json.loads(_USERS_FILE.read_text())
    except Exception:
        pass
    return []


def _save_users(users: list[dict]) -> None:
    """Persist local users list to disk."""
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USERS_FILE.write_text(json.dumps(users, indent=2))


# ─── OIDC revocation helpers ──────────────────────────────────────────────────


def _load_revoked() -> set[str]:
    """Load the set of revoked OIDC usernames from disk."""
    try:
        if _REVOKED_OIDC_FILE.exists():
            data = json.loads(_REVOKED_OIDC_FILE.read_text())
            return set(data.get("usernames", []))
    except Exception:
        pass
    return set()


def _save_revoked(usernames: set[str]) -> None:
    """Persist the revoked OIDC usernames set to disk."""
    _REVOKED_OIDC_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REVOKED_OIDC_FILE.write_text(
        json.dumps({"usernames": sorted(usernames)}, indent=2)
    )


def revoke_oidc_username(username: str) -> None:
    """Add a username to the OIDC revocation list.

    Called when an admin deletes an OIDC-provisioned account so that the next
    SSO callback returns 403 instead of silently re-creating the record.
    """
    revoked = _load_revoked()
    revoked.add(username)
    _save_revoked(revoked)


def is_oidc_revoked(username: str) -> bool:
    """Return True when the username is in the OIDC revocation list."""
    return username in _load_revoked()


def _active_admin_exists(users: list[dict], settings: Settings) -> bool:
    """Return True when at least one administrator can still authenticate.

    The built-in env-admin always counts as an active admin unless OIDC-only mode
    disables local login; in that case an enabled admin account (local or OIDC)
    must remain. Guards demote/disable operations against a full admin lockout.
    """
    if not resolve_oidc_settings(settings).oidc_only:
        return True
    return any(u.get("is_admin") and not u.get("disabled") for u in users)


def _user_to_public(u: dict) -> LocalUserPublic:
    """Convert a raw user dict to LocalUserPublic, preserving auth_source."""
    return LocalUserPublic(
        id=u["id"],
        username=u["username"],
        is_admin=u.get("is_admin", False),
        created_at=u.get("created_at", ""),
        auth_source=u.get("auth_source", AUTH_SOURCE_LOCAL),
        disabled=u.get("disabled", False),
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


def _ensure_local_auth_enabled(settings: Settings) -> None:
    """Reject local credential login when OIDC-only mode is active.

    In OIDC-only mode every password-based login (including the env-admin) is
    disabled and authentication is delegated entirely to the OIDC provider.
    """
    if resolve_oidc_settings(settings).oidc_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local authentication is disabled (OIDC-only mode).",
        )


@router.post("/login", response_model=Token)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
):
    """JSON login endpoint used by the Angular frontend.

    On success the session JWT is stored in an HttpOnly cookie (browser session)
    in addition to being returned in the body (API clients).
    """
    _ensure_local_auth_enabled(settings)
    if not verify_user(payload.username, payload.password, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    if is_user_disabled(payload.username, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been disabled",
        )
    access_token = create_access_token({"sub": payload.username}, settings)
    set_auth_cookie(response, request, access_token)
    await log_web_login(request, payload.username, settings, AUTH_SOURCE_LOCAL)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> None:
    """Clear the HttpOnly auth cookie (browser logout).

    Idempotent and unauthenticated: it only deletes the session cookie. OIDC
    end-session (provider logout) is handled separately by the frontend.

    A web_logout audit event is emitted first, reading the username from the
    session cookie still present on the request (skipped when already logged
    out).
    """
    await log_web_logout(request, settings)
    clear_auth_cookie(response)


class ChangePasswordRequest(BaseModel):
    """Payload for a user changing their own password."""

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_min_length(cls, v: str) -> str:
        """Ensure the new password is at least 8 characters long."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    payload: ChangePasswordRequest,
    current_user: UserInfo = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> None:
    """Let the authenticated user change their own password.

    OIDC accounts have no local password (authentication is delegated to the
    provider) and are therefore rejected. The current password is always
    re-verified before the change is applied.
    """
    # Verify the current password against the same source used at login.
    if not verify_user(current_user.username, payload.current_password, settings):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Built-in env-admin: persist the new hash via the bootstrap helper.
    if current_user.username == settings.admin_username:
        if not set_admin_password(payload.new_password):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not persist the new admin password",
            )
        return

    users = _load_users()
    for user in users:
        if user["username"] == current_user.username:
            if user.get("auth_source") == AUTH_SOURCE_OIDC:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Password cannot be changed for OIDC accounts. "
                        "Authentication is managed by the external provider."
                    ),
                )
            user["password_hash"] = hash_password(payload.new_password)
            _save_users(users)
            return

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


class CurrentUserResponse(UserInfo):
    """/me payload: the authenticated user plus feature flags the UI needs."""

    # Whether the Personal Access Token feature is enabled (API_KEYS_ENABLED).
    # The account panel hides token generation when False.
    api_keys_enabled: bool = True

    # Whether this account has a local password it can change from its profile.
    # False for OIDC-provisioned accounts (managed by the external provider).
    can_change_password: bool = True


@router.get("/me", response_model=CurrentUserResponse)
async def read_current_user(
    current_user: UserInfo = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> CurrentUserResponse:
    """Return information about the currently authenticated user."""
    can_change_password = True
    if current_user.username != settings.admin_username:
        record = next(
            (u for u in _load_users() if u["username"] == current_user.username),
            None,
        )
        # A local record with an OIDC source (or no local password) cannot self-
        # change; an authenticated user with no local record is OIDC-only too.
        if record is None or record.get("auth_source") == AUTH_SOURCE_OIDC:
            can_change_password = False
    return CurrentUserResponse(
        **current_user.model_dump(),
        api_keys_enabled=settings.api_keys_enabled,
        can_change_password=can_change_password,
    )


@router.get("/users", response_model=list[LocalUserPublic])
async def list_local_users(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> list[LocalUserPublic]:
    """Return all local users. The env-based admin is always included first."""
    users = _load_users()
    result: list[LocalUserPublic] = [
        LocalUserPublic(
            id="env-admin",
            username=settings.admin_username,
            is_admin=True,
            created_at="",
            auth_source=AUTH_SOURCE_LOCAL,
        )
    ]
    for u in users:
        result.append(_user_to_public(u))
    return result


@router.post(
    "/users", response_model=LocalUserPublic, status_code=status.HTTP_201_CREATED
)
async def create_local_user(
    payload: CreateUserRequest,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> LocalUserPublic:
    """Create a new local user. Password is stored as a bcrypt hash.

    Only local (password-based) accounts can be created here.
    OIDC accounts are provisioned automatically via the OIDC callback.
    """
    users = _load_users()

    if payload.username == settings.admin_username or any(
        u["username"] == payload.username for u in users
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    entry = {
        "id": str(uuid.uuid4()),
        "username": payload.username,
        "password_hash": hash_password(payload.password),
        "is_admin": payload.is_admin,
        "auth_source": AUTH_SOURCE_LOCAL,
        "created_at": datetime.now(UTC).isoformat(),
    }
    users.append(entry)
    _save_users(users)

    return _user_to_public(entry)


@router.patch("/users/{user_id}", response_model=LocalUserPublic)
async def update_local_user(
    user_id: str,
    payload: UpdateUserRequest,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> LocalUserPublic:
    """Update a local user's password and/or admin role.

    Password changes are rejected for OIDC users — their identity is managed
    exclusively by the external provider.

    The built-in env-admin can only have its password changed (it is always an
    administrator and cannot be renamed or demoted). The new hash is persisted
    under DATA_DIR so it survives restarts.
    """
    if user_id == "env-admin":
        if payload.password is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only the password can be changed for the admin account",
            )
        if len(payload.password) < 8:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Password must be at least 8 characters",
            )
        if not set_admin_password(payload.password):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not persist the new admin password",
            )
        return LocalUserPublic(
            id="env-admin",
            username=settings.admin_username,
            is_admin=True,
            created_at="",
            auth_source=AUTH_SOURCE_LOCAL,
        )

    users = _load_users()
    for user in users:
        if user["id"] == user_id:
            # Block password changes for OIDC-provisioned accounts
            if payload.password is not None:
                if user.get("auth_source") == AUTH_SOURCE_OIDC:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "Password cannot be changed for OIDC accounts. "
                            "Authentication is managed by the external provider."
                        ),
                    )
                if len(payload.password) < 8:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Password must be at least 8 characters",
                    )
                user["password_hash"] = hash_password(payload.password)
            if payload.is_admin is not None:
                user["is_admin"] = payload.is_admin
            if payload.disabled is not None:
                user["disabled"] = payload.disabled
            # Guard: demoting or disabling must never remove the last active admin.
            if not _active_admin_exists(users, settings):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one active administrator account is required",
                )
            _save_users(users)
            return _user_to_public(user)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_local_user(
    user_id: str,
    _: UserInfo = Depends(require_admin),
) -> None:
    """Delete a local or OIDC-provisioned user.

    The env-based admin cannot be deleted.
    For OIDC accounts the username is added to the revocation list so the next
    SSO callback returns 403 instead of silently re-provisioning the account.
    """
    if user_id == "env-admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The env-based admin account cannot be deleted",
        )
    users = _load_users()
    target = next((u for u in users if u["id"] == user_id), None)

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    username = target["username"]

    # Persist revocation before removing the record for OIDC accounts
    if target.get("auth_source") == AUTH_SOURCE_OIDC:
        revoke_oidc_username(username)

    # Cascade cleanup for resources tied to this account.
    # Folder permissions are group-based, so removing the user from every group
    # is enough to revoke their inherited access.
    remove_member_from_all_groups(username)
    delete_registries_for_owner(username)
    revoke_tokens_for_username(username)

    _save_users([u for u in users if u["id"] != user_id])
