"""
Portalcrane - JWT helpers
Token creation, decoding, and FastAPI dependency functions used across all routers.

Pull/push permission checks have been removed from UserInfo.
All access control is now handled exclusively by folder rules in registry_proxy.py.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from jose import JWTError, jwt
from pydantic import BaseModel

from ..config import DATA_DIR, Settings, get_settings

# Personal Access Token (API key) scheme. Rendered as the "Authorize" entry in
# Swagger UI: paste a raw PAT (pct_…) or a session JWT and it is sent as
# `Authorization: Bearer <token>`. auto_error=False so it stays optional and can
# co-exist with the HttpOnly auth cookie.
api_key_scheme = HTTPBearer(
    scheme_name="Personal Access Token (PAT)",
    description="Paste a personal access token (pct_…) created from your account.",
    bearerFormat="pct",
    auto_error=False,
)

ALGORITHM = "HS256"
_USERS_FILE = Path(f"{DATA_DIR}/local_users.json")


# ─── Pydantic models shared by all routers ───────────────────────────────────


class Token(BaseModel):
    """JWT access-token response returned after a successful login."""

    access_token: str
    token_type: str
    expires_in: int


class UserInfo(BaseModel):
    """Authenticated user information returned by /me and used as dependency.

    can_pull_images and can_push_images have been removed — access control
    is now handled exclusively through folder permissions.
    """

    username: str
    is_admin: bool = False


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _load_users() -> list[dict]:
    """Load local users from disk. Returns empty list when the file is absent."""
    try:
        if _USERS_FILE.exists():
            return json.loads(_USERS_FILE.read_text())
    except Exception:
        pass
    return []


def is_admin_user(username: str, settings: Settings) -> bool:
    """Return True when the username has admin rights."""
    if username == settings.admin_username:
        return True
    for user in _load_users():
        if user["username"] == username:
            return user.get("is_admin", False)
    return False


# ─── Public API ───────────────────────────────────────────────────────────────


def create_access_token(data: dict, settings: Settings) -> str:
    """Sign and return a JWT access token containing *data* as claims."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


async def get_current_user(
    request: Request,
    api_key: HTTPAuthorizationCredentials | None = Depends(api_key_scheme),
    settings: Settings = Depends(get_settings),
) -> UserInfo:
    """FastAPI dependency: validate the caller's credentials and return UserInfo.

    Two credential sources are accepted, in order of preference:
      1. ``Authorization: Bearer`` header — either a short-lived session JWT or
         a personal access token / API key (``pct_…``) created by the user.
      2. The HttpOnly auth cookie (browser sessions set at login).
    Personal access tokens are always validated against the on-disk store so
    that revoked or expired keys are rejected, even though they are signed JWTs.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Prefer an explicit Bearer header (session JWT or API key); fall back to
    # the HttpOnly cookie carried by browser sessions.
    token = (api_key.credentials if api_key else None) or request.cookies.get(
        settings.auth_cookie_name
    )
    if not token:
        raise credentials_exception

    # Personal access token (API key) path — validated against the store so that
    # revocation, expiry and scope are honoured. Only api-scoped tokens are
    # accepted here; docker-scoped tokens are rejected. Imported lazily to avoid
    # a circular import (personal_tokens imports from this module).
    from ..routers.personal_tokens import (
        _TOKEN_PREFIX,
        SCOPE_API,
        verify_personal_token,
    )

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        payload = None

    is_pat = token.startswith(_TOKEN_PREFIX) or bool(payload and payload.get("pat"))
    if is_pat:
        # Reconstruct the prefixed form so verify_personal_token can decode and
        # locate the record even when only the inner JWT was supplied.
        raw = token if token.startswith(_TOKEN_PREFIX) else _TOKEN_PREFIX + token
        username = verify_personal_token(raw, settings, expected_scope=SCOPE_API)
        if not username:
            raise credentials_exception
        return UserInfo(username=username, is_admin=is_admin_user(username, settings))

    # Session JWT path.
    if payload is None:
        raise credentials_exception
    username = payload.get("sub")
    if username is None:
        raise credentials_exception

    return UserInfo(
        username=username,
        is_admin=is_admin_user(username, settings),
    )


def require_admin(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """FastAPI dependency: raise 403 when the current user is not an admin."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def require_pull_access(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """
    Ensures the user is authenticated; does NOT itself check pull permissions.

    Authorization is enforced by each route handler according to its target:
      - Docker pull/push through the registry proxy → _authorize_registry_proxy
        (registry_proxy.py) applies folder-based permissions.
      - REST image routes (routers/repositories.py) apply folder-based
        permissions explicitly via _ensure_folder_permission, plus registry
        ownership via resolve_owned_registry.
      - Sync/export/import routes enforce registry ownership via
        _ensure_registry_access.

    This dependency only guarantees a valid JWT token.
    """
    return current_user


def require_push_access(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """
    Ensures the user is authenticated; does NOT itself check push permissions.

    Authorization is enforced by each route handler according to its target;
    see require_pull_access for the breakdown. This dependency only guarantees
    a valid JWT token.
    """
    return current_user
