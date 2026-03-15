"""
Portalcrane - Authentication Router
Handles local authentication and user management:
  - POST /token           → OAuth2 password-flow token endpoint
  - POST /login           → JSON login endpoint
  - GET  /me              → current user information
  - GET /account/dockerhub → Docker Hub credentials source (external registry)
  - GET/POST/PATCH/DELETE /users → local users CRUD

Pull/push permissions are managed exclusively through folder permissions.
OIDC routes are in routers/oidc.py.
JWT helpers and FastAPI dependencies are in core/jwt.py.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, field_validator

from ..config import DATA_DIR, Settings, get_settings
from ..core.jwt import (
    Token,
    UserInfo,
    create_access_token,
    get_current_user,
    require_admin,
)
from ..core.security import hash_password, verify_user
from ..services.external_registry import (
    delete_registries_for_owner,
    find_registry_credentials_for_host,
)
from .folders import remove_permissions_for_username
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


def _user_to_public(u: dict) -> LocalUserPublic:
    """Convert a raw user dict to LocalUserPublic, preserving auth_source."""
    return LocalUserPublic(
        id=u["id"],
        username=u["username"],
        is_admin=u.get("is_admin", False),
        created_at=u.get("created_at", ""),
        auth_source=u.get("auth_source", AUTH_SOURCE_LOCAL),
    )


# ─── Docker Hub helpers ───────────────────────────────────────────────────────


def get_user_dockerhub_credentials(username: str) -> tuple[str, str] | None:
    """Return Docker Hub credentials from external registries for *username*."""
    return find_registry_credentials_for_host("docker.io", owner=username)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    settings: Settings = Depends(get_settings),
):
    """OAuth2 password-flow token endpoint used by the Swagger UI."""
    if not verify_user(form_data.username, form_data.password, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token({"sub": form_data.username}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=Token)
async def login(
    payload: LoginRequest,
    settings: Settings = Depends(get_settings),
):
    """JSON login endpoint used by the Angular frontend."""
    if not verify_user(payload.username, payload.password, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    access_token = create_access_token({"sub": payload.username}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserInfo)
async def read_current_user(current_user: UserInfo = Depends(get_current_user)):
    """Return information about the currently authenticated user."""
    return current_user


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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    users.append(entry)
    _save_users(users)

    return _user_to_public(entry)


@router.patch("/users/{user_id}", response_model=LocalUserPublic)
async def update_local_user(
    user_id: str,
    payload: UpdateUserRequest,
    _: UserInfo = Depends(require_admin),
) -> LocalUserPublic:
    """Update a local user's password and/or admin role.

    Password changes are rejected for OIDC users — their identity is managed
    exclusively by the external provider.
    """
    if user_id == "env-admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The env-based admin account cannot be modified here",
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

    # Cascade cleanup for resources tied to this account
    remove_permissions_for_username(username)
    delete_registries_for_owner(username)
    revoke_tokens_for_username(username)

    _save_users([u for u in users if u["id"] != user_id])


@router.get("/account/dockerhub", response_model=DockerHubAccountSettings)
async def get_dockerhub_account(
    current_user: UserInfo = Depends(get_current_user),
) -> DockerHubAccountSettings:
    """Return the Docker Hub credentials stored for the current user."""
    creds = find_registry_credentials_for_host(
        "docker.io",
        owner=current_user.username,
    )
    if creds:
        username, password = creds
        return DockerHubAccountSettings(
            username=username,
            has_password=bool(password),
        )

    return DockerHubAccountSettings(username="", has_password=False)
