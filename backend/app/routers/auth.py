"""
Portalcrane - Authentication Router
Handles local authentication and user management:
  - POST /token           → OAuth2 password-flow token endpoint
  - POST /login           → JSON login endpoint
  - GET  /me              → current user information
  - GET/PUT /account/dockerhub → per-user Docker Hub credentials
  - GET/POST/PATCH/DELETE /users → local users CRUD

Pull/push permissions are no longer stored on the user account.
They are managed exclusively through folder permissions (see routers/folders.py).
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

router = APIRouter()

_USERS_FILE = Path(f"{DATA_DIR}/local_users.json")
_ACCOUNT_SETTINGS_FILE = Path(f"{DATA_DIR}/account_settings.json")


# ─── Pydantic models ──────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """JSON body for the /login endpoint."""

    username: str
    password: str


class LocalUserPublic(BaseModel):
    """Local user representation returned to the frontend (no password hash).

    can_pull_images and can_push_images have been removed — permissions are
    now managed exclusively through folder rules (see routers/folders.py).
    """

    id: str
    username: str
    is_admin: bool
    created_at: str


class CreateUserRequest(BaseModel):
    """Payload to create a new local user.

    Pull/push permissions are no longer set at creation time.
    Use the Folders API to grant access after creating the user.
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

    Pull/push permissions are no longer managed here — use folder permissions.
    """

    password: str | None = None
    is_admin: bool | None = None


class DockerHubAccountSettings(BaseModel):
    """Docker Hub credentials bound to the current Portalcrane account."""

    username: str = ""
    has_password: bool = False


class UpdateDockerHubAccountSettingsRequest(BaseModel):
    """Payload to update Docker Hub credentials for the authenticated user."""

    username: str
    password: str


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


# ─── Account settings helpers ─────────────────────────────────────────────────


def _load_account_settings() -> dict:
    """Load per-user account settings from disk."""
    try:
        if _ACCOUNT_SETTINGS_FILE.exists():
            return json.loads(_ACCOUNT_SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_account_settings(data: dict) -> None:
    """Persist per-user account settings to disk."""
    _ACCOUNT_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACCOUNT_SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def get_user_dockerhub_credentials(username: str) -> tuple[str, str] | None:
    """Return (hub_username, hub_password) for username, or None when absent."""
    data = _load_account_settings()
    entry = data.get(username, {})
    hub_user = entry.get("dockerhub_username", "")
    hub_pass = entry.get("dockerhub_password", "")
    if hub_user and hub_pass:
        return hub_user, hub_pass
    return None


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
    access_token = create_access_token(
        data={"sub": form_data.username}, settings=settings
    )
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
    access_token = create_access_token(
        data={"sub": payload.username}, settings=settings
    )
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserInfo)
async def read_users_me(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """Return the currently authenticated user's information."""
    return current_user


@router.get("/users", response_model=list[LocalUserPublic])
async def list_local_users(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> list[LocalUserPublic]:
    """Return all local users. The env-based admin is always included."""
    users = _load_users()
    result: list[LocalUserPublic] = [
        LocalUserPublic(
            id="env-admin",
            username=settings.admin_username,
            is_admin=True,
            created_at="",
        )
    ]
    for u in users:
        result.append(
            LocalUserPublic(
                id=u["id"],
                username=u["username"],
                is_admin=u.get("is_admin", False),
                created_at=u.get("created_at", ""),
            )
        )
    return result


@router.post(
    "/users", response_model=LocalUserPublic, status_code=status.HTTP_201_CREATED
)
async def create_local_user(
    payload: CreateUserRequest,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> LocalUserPublic:
    """Create a new local user. Password is stored as a bcrypt hash."""
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    users.append(entry)
    _save_users(users)

    return LocalUserPublic(
        id=entry["id"],
        username=entry["username"],
        is_admin=entry["is_admin"],
        created_at=entry["created_at"],
    )


@router.patch("/users/{user_id}", response_model=LocalUserPublic)
async def update_local_user(
    user_id: str,
    payload: UpdateUserRequest,
    _: UserInfo = Depends(require_admin),
) -> LocalUserPublic:
    """Update a local user's password and/or admin role."""
    if user_id == "env-admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The env-based admin account cannot be modified here",
        )

    users = _load_users()
    for user in users:
        if user["id"] == user_id:
            if payload.password is not None:
                if len(payload.password) < 8:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Password must be at least 8 characters",
                    )
                user["password_hash"] = hash_password(payload.password)
            if payload.is_admin is not None:
                user["is_admin"] = payload.is_admin
            _save_users(users)
            return LocalUserPublic(
                id=user["id"],
                username=user["username"],
                is_admin=user.get("is_admin", False),
                created_at=user.get("created_at", ""),
            )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_local_user(
    user_id: str,
    _: UserInfo = Depends(require_admin),
) -> None:
    """Delete a local user. The env-based admin cannot be deleted."""
    if user_id == "env-admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The env-based admin account cannot be deleted",
        )
    users = _load_users()
    new_list = [u for u in users if u["id"] != user_id]
    if len(new_list) == len(users):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    _save_users(new_list)


@router.get("/account/dockerhub", response_model=DockerHubAccountSettings)
async def get_dockerhub_account(
    current_user: UserInfo = Depends(get_current_user),
) -> DockerHubAccountSettings:
    """Return the Docker Hub credentials stored for the current user."""
    data = _load_account_settings()
    entry = data.get(current_user.username, {})
    return DockerHubAccountSettings(
        username=entry.get("dockerhub_username", ""),
        has_password=bool(entry.get("dockerhub_password")),
    )


@router.put("/account/dockerhub", response_model=DockerHubAccountSettings)
async def update_dockerhub_account(
    payload: UpdateDockerHubAccountSettingsRequest,
    current_user: UserInfo = Depends(get_current_user),
) -> DockerHubAccountSettings:
    """Save Docker Hub credentials for the current user."""
    data = _load_account_settings()
    data.setdefault(current_user.username, {})
    data[current_user.username]["dockerhub_username"] = payload.username
    data[current_user.username]["dockerhub_password"] = payload.password
    _save_account_settings(data)
    return DockerHubAccountSettings(username=payload.username, has_password=True)
