"""
Portalcrane - Authentication Router
Handles local admin authentication, OIDC flow, local users CRUD,
and OIDC configuration persistence.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, field_validator

from ..config import Settings, get_settings

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def _hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt. Returns a UTF-8 string."""
    # bcrypt has a hard limit of 72 bytes — truncate to stay within spec
    secret = password.encode("utf-8")[:72]
    return bcrypt.hashpw(secret, bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash."""
    secret = plain.encode("utf-8")[:72]
    return bcrypt.checkpw(secret, hashed.encode("utf-8"))


# Persistent storage for local users (additional to the env-based admin)
_USERS_FILE = Path("/var/lib/portalcrane/local_users.json")

# Persistent storage for OIDC configuration overrides
_OIDC_CONFIG_FILE = Path("/var/lib/portalcrane/oidc_config.json")


# ─── Models ──────────────────────────────────────────────────────────────────


class Token(BaseModel):
    """JWT token response model."""

    access_token: str
    token_type: str
    expires_in: int


class TokenData(BaseModel):
    """Decoded token data model."""

    username: str | None = None


class UserInfo(BaseModel):
    """Authenticated user information."""

    username: str
    is_admin: bool = True
    can_pull_images: bool = True
    can_push_images: bool = True


class OIDCConfig(BaseModel):
    """OIDC provider configuration response (used for login page)."""

    enabled: bool
    client_id: str
    issuer: str
    redirect_uri: str
    authorization_endpoint: str = ""


class LoginRequest(BaseModel):
    """Local login request model."""

    username: str
    password: str


# ── Local users models ────────────────────────────────────────────────────────


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
        """Ensure username is non-empty and has no spaces."""
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


# ── OIDC settings models ──────────────────────────────────────────────────────


class OidcSettings(BaseModel):
    """OIDC configuration that can be persisted to the JSON file."""

    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    post_logout_redirect_uri: str = ""
    response_type: str = "code"
    scope: str = "openid profile email"


# ─── Local users helpers ──────────────────────────────────────────────────────


def _load_users() -> list[dict]:
    """Load local users from disk. Returns empty list if file is missing."""
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


# ─── OIDC config helpers ──────────────────────────────────────────────────────


def _load_oidc_config() -> dict:
    """Load persisted OIDC config. Returns empty dict if file is missing."""
    try:
        if _OIDC_CONFIG_FILE.exists():
            return json.loads(_OIDC_CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_oidc_config(data: dict) -> None:
    """Persist OIDC config to disk."""
    _OIDC_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OIDC_CONFIG_FILE.write_text(json.dumps(data, indent=2))


# ─── JWT helpers ─────────────────────────────────────────────────────────────


def create_access_token(data: dict, settings: Settings) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> UserInfo:
    """Validate JWT token and return current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    username = token_data.username or ""
    is_admin = _is_admin_user(username, settings)
    can_pull_images = _can_pull_images(username, settings)
    can_push_images = _can_push_images(username, settings)

    return UserInfo(
        username=username,
        is_admin=is_admin,
        can_pull_images=can_pull_images,
        can_push_images=can_push_images,
    )


def _verify_user(username: str, password: str, settings: Settings) -> bool:
    """
    Verify credentials against the env-based admin account first,
    then against the local users JSON file.
    """
    # Primary: env-based admin
    if username == settings.admin_username and password == settings.admin_password:
        return True
    # Secondary: local users file
    for user in _load_users():
        if user["username"] == username:
            return _verify_password(password, user.get("password_hash", ""))
    return False


def _is_admin_user(username: str, settings: Settings) -> bool:
    """Return True if the user has admin rights."""
    if username == settings.admin_username:
        return True
    for user in _load_users():
        if user["username"] == username:
            return user.get("is_admin", False)
    return False


def _can_pull_images(username: str, settings: Settings) -> bool:
    """Return True if the user can pull images."""
    if username == settings.admin_username:
        return True
    for user in _load_users():
        if user["username"] == username:
            if user.get("is_admin", False):
                return True
            return user.get("can_pull_images", False)
    return False


def _can_push_images(username: str, settings: Settings) -> bool:
    """Return True if the user can push images."""
    if username == settings.admin_username:
        return True
    for user in _load_users():
        if user["username"] == username:
            if user.get("is_admin", False):
                return True
            return user.get("can_push_images", False)
    return False


def require_admin(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """Ensure current user is admin."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def require_pull_access(
    current_user: UserInfo = Depends(get_current_user),
) -> UserInfo:
    """Ensure current user can pull images."""
    if not current_user.can_pull_images:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pull permission required",
        )
    return current_user


def require_push_access(
    current_user: UserInfo = Depends(get_current_user),
) -> UserInfo:
    """Ensure current user can push images."""
    if not current_user.can_push_images:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Push permission required",
        )
    return current_user


# ─── Auth endpoints ───────────────────────────────────────────────────────────


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    settings: Settings = Depends(get_settings),
):
    """OAuth2 compatible token endpoint for local admin authentication."""
    if not _verify_user(form_data.username, form_data.password, settings):
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
    """JSON login endpoint for local admin authentication."""
    if not _verify_user(request.username, request.password, settings):
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


@router.get("/oidc-config", response_model=OIDCConfig)
async def get_oidc_config(settings: Settings = Depends(get_settings)):
    """Return OIDC configuration for the frontend login page."""
    # Merge: persisted file overrides env vars when present
    persisted = _load_oidc_config()

    enabled = persisted.get("enabled", settings.oidc_enabled)
    issuer = persisted.get("issuer", settings.oidc_issuer)
    client_id = persisted.get("client_id", settings.oidc_client_id)
    redirect_uri = persisted.get("redirect_uri", settings.oidc_redirect_uri)

    if not enabled:
        return OIDCConfig(
            enabled=False,
            client_id="",
            issuer="",
            redirect_uri="",
        )

    # Fetch OIDC discovery document to resolve the authorization endpoint
    authorization_endpoint = ""
    try:
        proxy = settings.httpx_proxy
        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.get(
                f"{issuer}/.well-known/openid-configuration",
                timeout=settings.httpx_timeout,
            )
            if response.status_code == 200:
                discovery = response.json()
                authorization_endpoint = discovery.get("authorization_endpoint", "")
    except Exception:
        pass

    return OIDCConfig(
        enabled=True,
        client_id=client_id,
        issuer=issuer,
        redirect_uri=redirect_uri,
        authorization_endpoint=authorization_endpoint,
    )


@router.post("/oidc/callback", response_model=Token)
async def oidc_callback(
    code: str,
    settings: Settings = Depends(get_settings),
):
    """Handle OIDC authorization code callback and exchange for JWT."""
    persisted = _load_oidc_config()
    issuer = persisted.get("issuer", settings.oidc_issuer)
    client_id = persisted.get("client_id", settings.oidc_client_id)
    client_secret = persisted.get("client_secret", settings.oidc_client_secret)
    redirect_uri = persisted.get("redirect_uri", settings.oidc_redirect_uri)

    try:
        proxy = settings.httpx_proxy
        async with httpx.AsyncClient(proxy=proxy) as client:
            discovery_resp = await client.get(
                f"{issuer}/.well-known/openid-configuration",
                timeout=settings.httpx_timeout,
            )
            discovery_resp.raise_for_status()
            token_endpoint = discovery_resp.json().get("token_endpoint", "")

            token_resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=settings.httpx_timeout,
            )
            token_resp.raise_for_status()
            id_token = token_resp.json().get("id_token", "")

            # Decode id_token without verification to extract the username claim
            from jose import jwt as jose_jwt

            claims = jose_jwt.get_unverified_claims(id_token)
            username = claims.get("preferred_username") or claims.get(
                "sub", "oidc-user"
            )
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


# ─── OIDC settings endpoints ──────────────────────────────────────────────────


@router.get("/oidc-settings", response_model=OidcSettings)
async def get_oidc_settings(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Return the full OIDC settings (env defaults merged with persisted overrides).
    Only accessible to authenticated users — used by the Settings page.
    """
    persisted = _load_oidc_config()
    return OidcSettings(
        enabled=persisted.get("enabled", settings.oidc_enabled),
        issuer=persisted.get("issuer", settings.oidc_issuer),
        client_id=persisted.get("client_id", settings.oidc_client_id),
        client_secret=persisted.get("client_secret", settings.oidc_client_secret),
        redirect_uri=persisted.get("redirect_uri", settings.oidc_redirect_uri),
        post_logout_redirect_uri=persisted.get(
            "post_logout_redirect_uri", settings.oidc_post_logout_redirect_uri
        ),
        response_type=persisted.get("response_type", settings.oidc_response_type),
        scope=persisted.get("scope", settings.oidc_scope),
    )


@router.put("/oidc-settings", response_model=OidcSettings)
async def save_oidc_settings(
    payload: OidcSettings,
    _: UserInfo = Depends(require_admin),
):
    """
    Persist OIDC settings to the JSON file.
    These values override env vars at runtime without requiring a restart.
    """
    data = payload.model_dump()
    _save_oidc_config(data)
    return payload


# ─── Local users endpoints ────────────────────────────────────────────────────


@router.get("/users", response_model=list[LocalUserPublic])
async def list_local_users(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    List all local users.
    The env-based admin account is always included as a synthetic entry.
    """
    users = _load_users()
    result: list[LocalUserPublic] = []

    # Synthetic entry for the env-based admin
    result.append(
        LocalUserPublic(
            id="env-admin",
            username=settings.admin_username,
            is_admin=True,
            can_pull_images=True,
            can_push_images=True,
            created_at="",
        )
    )

    for u in users:
        result.append(
            LocalUserPublic(
                id=u["id"],
                username=u["username"],
                is_admin=u.get("is_admin", False),
                can_pull_images=(
                    True
                    if u.get("is_admin", False)
                    else u.get("can_pull_images", False)
                ),
                can_push_images=(
                    True
                    if u.get("is_admin", False)
                    else u.get("can_push_images", False)
                ),
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
    """Create a new local user. The password is stored as a bcrypt hash."""
    users = _load_users()

    # Check for username collision with env admin and existing users
    if payload.username == settings.admin_username:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )
    if any(u["username"] == payload.username for u in users):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    entry = {
        "id": str(uuid.uuid4()),
        "username": payload.username,
        "password_hash": _hash_password(payload.password),
        "is_admin": payload.is_admin,
        "can_pull_images": True if payload.is_admin else payload.can_pull_images,
        "can_push_images": True if payload.is_admin else payload.can_push_images,
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
    """Update a local user's password and/or admin flag."""
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
                user["password_hash"] = _hash_password(payload.password)
            if payload.is_admin is not None:
                user["is_admin"] = payload.is_admin
                if payload.is_admin:
                    user["can_pull_images"] = True
                    user["can_push_images"] = True
            if payload.can_pull_images is not None and not user.get("is_admin", False):
                user["can_pull_images"] = payload.can_pull_images
            if payload.can_push_images is not None and not user.get("is_admin", False):
                user["can_push_images"] = payload.can_push_images
            _save_users(users)
            return LocalUserPublic(
                id=user["id"],
                username=user["username"],
                is_admin=user["is_admin"],
                can_pull_images=(
                    True if user["is_admin"] else user.get("can_pull_images", False)
                ),
                can_push_images=(
                    True if user["is_admin"] else user.get("can_push_images", False)
                ),
                created_at=user.get("created_at", ""),
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="User not found",
    )


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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    _save_users(new_list)
