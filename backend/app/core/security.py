"""
Portalcrane - Security helpers
Low-level password hashing, verification, and user credential checking.
Shared by routers/auth.py (login endpoints) and routers/registry_proxy.py
(Basic Auth on the registry proxy).
"""

import json
from pathlib import Path

import bcrypt

from ..config import DATA_DIR, Settings

_USERS_FILE = Path(f"{DATA_DIR}/local_users.json")

# Bcrypt hash of the built-in admin password, resolved at startup by
# core/bootstrap.py. Never read from the environment.
_admin_password_hash: str = ""


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _load_users() -> list[dict]:
    """Load local users from disk. Returns empty list when the file is absent."""
    try:
        if _USERS_FILE.exists():
            return json.loads(_USERS_FILE.read_text())
    except Exception:
        pass
    return []


# ─── Public API ───────────────────────────────────────────────────────────────


def set_admin_password_hash(hashed: str) -> None:
    """Set the resolved admin password hash (called by core/bootstrap.py)."""
    global _admin_password_hash
    _admin_password_hash = hashed


def get_admin_password_hash() -> str:
    """Return the bcrypt hash of the built-in admin password."""
    return _admin_password_hash


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt. Returns a UTF-8 string.

    bcrypt has a hard limit of 72 bytes — the input is truncated before hashing.
    """
    secret = password.encode("utf-8")[:72]
    return bcrypt.hashpw(secret, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash.

    Returns False immediately when hashed is empty or blank — this covers
    OIDC-provisioned accounts that have no password_hash stored, preventing
    a ValueError: Invalid salt from bcrypt.
    """
    if not hashed or not hashed.strip():
        return False
    try:
        secret = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(secret, hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash stored on disk — treat as invalid rather than crashing
        return False


def verify_user(username: str, password: str, settings: Settings) -> bool:
    """Verify credentials against the built-in admin, then the local users file.

    OIDC-provisioned users have no password_hash; verify_password returns False
    for them, so they can only authenticate via PAT (registry proxy) or SSO.

    Used by both the login endpoint and the registry proxy Basic Auth handler.
    """
    # Primary: built-in admin — verified against the bootstrapped bcrypt hash
    # of the auto-generated first-launch password (see core/bootstrap.py).
    if username == settings.admin_username:
        return verify_password(password, get_admin_password_hash())
    # Secondary: local users stored as bcrypt hashes
    for user in _load_users():
        if user["username"] == username:
            return verify_password(password, user.get("password_hash", ""))
    return False
