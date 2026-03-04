"""
Portalcrane - Authentication Router
Handles local authentication and user management:
  - POST /token           → OAuth2 password-flow token endpoint
  - POST /login           → JSON login endpoint
  - GET  /me              → current user information
  - GET/PUT /account/dockerhub → per-user Docker Hub credentials
  - GET/POST/PATCH/DELETE /users → local users CRUD

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

# ── Storage file paths ────────────────────────────────────────────────────────

_USERS_FILE = Path(f"{DATA_DIR}/local_users.json")
_ACCOUNT_SETTINGS_FILE = Path(f"{DATA_DIR}/account_settings.json")


# ─── Pydantic models ──────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """JSON body for the /login endpoint."""

    username: str
    password: str


class LocalUser(BaseModel):
    """Local user as stored in the JSON file (password is bcrypt hashed)."""

    id: str
    username: str
    is_admin: bool = False
    created_at: str
    can_pull_images: bool = False
    can_push_images: bool = False


class LocalUserPublic(BaseModel):
    """Local user representation returned to the frontend (no password hash)."""

    id: str
    username: str
    is_admin: bool
    created_at: str
    can_pull_images: bool
    can_push_images: bool


class CreateUserRequest(BaseModel):
    """Payload to create a new local user."""

    username: str
    password: str
    is_admin: bool = False
    can_pull_images: bool = False
    can_push_images: bool = False

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
    """Payload to update a local user (all fields optional)."""

    password: str | None = None
    is_admin: bool | None = None
    can_pull_images: bool | None = None
    can_push_images: bool | None = None


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
    """Return (hub_username, hub_password) for *username*, or None when absent."""
    data = _load_account_settings()
    account = data.get(username) or {}
    hub = account.get("dockerhub") or {}
    hub_username = (hub.get("username") or "").strip()
    hub_password = hub.get("password") or ""
    if hub_username and hub_password:
        return hub_username, hub_password
    return None


# ─── Auth endpoints ───────────────────────────────────────────────────────────


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    settings: Settings = Depends(get_settings),
):
    """OAuth2 compatible token endpoint (used by Swagger UI and API clients)."""
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
    request: LoginRequest,
    settings: Settings = Depends(get_settings),
):
    """JSON login endpoint used by the Angular frontend."""
    if not verify_user(request.username, request.password, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    access_token = create_access_token({"sub": request.username}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserInfo)
async def read_users_me(current_user: UserInfo = Depends(get_current_user)):
    """Return current authenticated user information."""
    return current_user


# ─── Docker Hub account settings ─────────────────────────────────────────────


@router.get("/account/dockerhub", response_model=DockerHubAccountSettings)
async def get_dockerhub_account_settings(
    current_user: UserInfo = Depends(get_current_user),
):
    """Return Docker Hub account settings for the authenticated user."""
    creds = get_user_dockerhub_credentials(current_user.username)
    if not creds:
        return DockerHubAccountSettings(username="", has_password=False)
    return DockerHubAccountSettings(username=creds[0], has_password=True)


@router.put("/account/dockerhub", response_model=DockerHubAccountSettings)
async def update_dockerhub_account_settings(
    payload: UpdateDockerHubAccountSettingsRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Create or update Docker Hub credentials for the authenticated user."""
    username = payload.username.strip()
    password = payload.password

    data = _load_account_settings()
    user_cfg = data.get(current_user.username, {})

    if not username and not password:
        user_cfg.pop("dockerhub", None)
    else:
        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Docker Hub username and password are both required",
            )
        user_cfg["dockerhub"] = {"username": username, "password": password}

    if user_cfg:
        data[current_user.username] = user_cfg
    else:
        data.pop(current_user.username, None)
    _save_account_settings(data)

    creds = get_user_dockerhub_credentials(current_user.username)
    if not creds:
        return DockerHubAccountSettings(username="", has_password=False)
    return DockerHubAccountSettings(username=creds[0], has_password=True)


# ─── Local users CRUD ─────────────────────────────────────────────────────────


@router.get("/users", response_model=list[LocalUserPublic])
async def list_local_users(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """List all local users. The env-based admin is always included as a synthetic entry."""
    users = _load_users()
    result: list[LocalUserPublic] = [
        LocalUserPublic(
            id="env-admin",
            username=settings.admin_username,
            is_admin=True,
            can_pull_images=True,
            can_push_images=True,
            created_at="",
        )
    ]
    for u in users:
        is_admin = u.get("is_admin", False)
        result.append(
            LocalUserPublic(
                id=u["id"],
                username=u["username"],
                is_admin=is_admin,
                can_pull_images=True if is_admin else u.get("can_pull_images", False),
                can_push_images=True if is_admin else u.get("can_push_images", False),
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
):
    """Create a new local user. Password is stored as a bcrypt hash."""
    users = _load_users()

    if payload.username == settings.admin_username or any(
        u["username"] == payload.username for u in users
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    is_admin = payload.is_admin
    entry = {
        "id": str(uuid.uuid4()),
        "username": payload.username,
        "password_hash": hash_password(payload.password),
        "is_admin": is_admin,
        "can_pull_images": True if is_admin else payload.can_pull_images,
        "can_push_images": True if is_admin else payload.can_push_images,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    users.append(entry)
    _save_users(users)

    return LocalUserPublic(
        id=entry["id"],
        username=entry["username"],
        is_admin=entry["is_admin"],
        can_pull_images=entry["can_pull_images"],
        can_push_images=entry["can_push_images"],
        created_at=entry["created_at"],
    )


@router.patch("/users/{user_id}", response_model=LocalUserPublic)
async def update_local_user(
    user_id: str,
    payload: UpdateUserRequest,
    _: UserInfo = Depends(require_admin),
):
    """Update a local user's password and/or permissions."""
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
                if payload.is_admin:
                    user["can_pull_images"] = True
                    user["can_push_images"] = True
            is_admin = user.get("is_admin", False)
            if payload.can_pull_images is not None and not is_admin:
                user["can_pull_images"] = payload.can_pull_images
            if payload.can_push_images is not None and not is_admin:
                user["can_push_images"] = payload.can_push_images
            _save_users(users)
            return LocalUserPublic(
                id=user["id"],
                username=user["username"],
                is_admin=user["is_admin"],
                can_pull_images=True
                if user["is_admin"]
                else user.get("can_pull_images", False),
                can_push_images=True
                if user["is_admin"]
                else user.get("can_push_images", False),
                created_at=user.get("created_at", ""),
            )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_local_user(
    user_id: str,
    _: UserInfo = Depends(require_admin),
):
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
