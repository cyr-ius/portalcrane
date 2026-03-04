"""
Portalcrane - JWT helpers
Token creation, decoding, and FastAPI dependency functions used across all routers.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from ..config import ALGORITHM, DATA_DIR, Settings, get_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# Path to the local users JSON file — needed to resolve permissions
_USERS_FILE = Path(f"{DATA_DIR}/local_users.json")


# ─── Pydantic models shared by all routers ───────────────────────────────────


class Token(BaseModel):
    """JWT access-token response returned after a successful login."""

    access_token: str
    token_type: str
    expires_in: int


class TokenData(BaseModel):
    """Decoded JWT payload."""

    username: str | None = None


class UserInfo(BaseModel):
    """Authenticated user information returned by /me and used as dependency."""

    username: str
    is_admin: bool = True
    can_pull_images: bool = True
    can_push_images: bool = True


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _load_users() -> list[dict]:
    """Load local users from disk. Returns empty list when the file is absent."""
    try:
        if _USERS_FILE.exists():
            return json.loads(_USERS_FILE.read_text())
    except Exception:
        pass
    return []


def _is_admin_user(username: str, settings: Settings) -> bool:
    """Return True when the username has admin rights."""
    if username == settings.admin_username:
        return True
    for user in _load_users():
        if user["username"] == username:
            return user.get("is_admin", False)
    return False


def _can_pull_images(username: str, settings: Settings) -> bool:
    """Return True when the username is allowed to pull images."""
    if username == settings.admin_username:
        return True
    for user in _load_users():
        if user["username"] == username:
            return user.get("is_admin", False) or user.get("can_pull_images", False)
    return False


def _can_push_images(username: str, settings: Settings) -> bool:
    """Return True when the username is allowed to push images."""
    if username == settings.admin_username:
        return True
    for user in _load_users():
        if user["username"] == username:
            return user.get("is_admin", False) or user.get("can_push_images", False)
    return False


# ─── Public API ───────────────────────────────────────────────────────────────


def create_access_token(data: dict, settings: Settings) -> str:
    """Sign and return a JWT access token containing *data* as claims."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> UserInfo:
    """FastAPI dependency: validate the Bearer token and return the UserInfo."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    username = token_data.username or ""
    return UserInfo(
        username=username,
        is_admin=_is_admin_user(username, settings),
        can_pull_images=_can_pull_images(username, settings),
        can_push_images=_can_push_images(username, settings),
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
    """FastAPI dependency: raise 403 when the current user cannot pull images."""
    if not current_user.can_pull_images:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pull permission required",
        )
    return current_user


def require_push_access(current_user: UserInfo = Depends(get_current_user)) -> UserInfo:
    """FastAPI dependency: raise 403 when the current user cannot push images."""
    if not current_user.can_push_images:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Push permission required",
        )
    return current_user
