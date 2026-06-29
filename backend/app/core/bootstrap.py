"""
Portalcrane - Startup bootstrap
First-launch initialization of the built-in admin credentials and the JWT
signing secret.

Admin password: a secure one-time password is generated on first launch, its
bcrypt hash is persisted under DATA_DIR, and the plain-text password is printed
once in the logs so the operator can perform the initial login. On every
subsequent start the persisted hash is reused — the password is therefore *not*
rotated across restarts and is no longer configurable through the environment.

Secret key: when SECRET_KEY is left at its default, a random value is generated
and persisted under DATA_DIR so JWTs survive restarts. In the container the
entrypoint generates and exports the same file before any process starts (the
embedded registry needs it too); this Python-side resolution is the fallback
for direct/dev runs.

The default user is "admin"; deployments no longer need to define ADMIN_USERNAME,
ADMIN_PASSWORD or SECRET_KEY.
"""

import logging
import secrets
import string
from pathlib import Path

from ..config import DATA_DIR, Settings
from .security import hash_password

logger = logging.getLogger(__name__)

# Persisted bcrypt hash of the auto-generated admin password.
_ADMIN_HASH_FILE = Path(f"{DATA_DIR}/admin_password.hash")

# Persisted JWT signing secret (shared with the container entrypoint).
_SECRET_KEY_FILE = Path(f"{DATA_DIR}/secret_key")

# Placeholder shipped in config.py — treated as "no secret configured".
_DEFAULT_SECRET_KEY = "change-this-secret-key-in-production"

# Length of the generated one-time admin password (alphanumeric).
_GENERATED_PASSWORD_LENGTH = 24


def _generate_password(length: int = _GENERATED_PASSWORD_LENGTH) -> str:
    """Return a cryptographically secure alphanumeric password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _write_secret_file(path: Path, content: str) -> bool:
    """Persist *content* to *path* with owner-only permissions.

    Returns True on success, False when the filesystem rejects the write
    (e.g. a read-only volume) — the caller decides how to degrade.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o600)
        return True
    except OSError as exc:
        logger.error("Could not persist %s: %s", path, exc)
        return False


def _log_generated_password(username: str, password: str) -> None:
    """Print the one-time admin password as a prominent, hard-to-miss banner."""
    banner = "=" * 72
    logger.warning(
        "\n%s\n"
        " Portalcrane — initial admin account created (first launch)\n"
        "   username : %s\n"
        "   password : %s\n"
        " This password is shown only once. Store it now.\n"
        "%s",
        banner,
        username,
        password,
        banner,
    )


def ensure_secret_key(settings: Settings) -> None:
    """Resolve the JWT signing secret, generating & persisting one on first run.

    Precedence:
      1. SECRET_KEY set to a non-default value → used as-is.
      2. A persisted secret exists under DATA_DIR → reused.
      3. Neither → generate, persist, and continue.

    In the container the entrypoint already writes the persisted file and
    exports SECRET_KEY, so case 1 normally applies; this covers direct runs.
    """
    if settings.secret_key and settings.secret_key != _DEFAULT_SECRET_KEY:
        return

    try:
        if _SECRET_KEY_FILE.exists():
            stored = _SECRET_KEY_FILE.read_text().strip()
            if stored:
                settings.secret_key = stored
                logger.info("SECRET_KEY loaded from %s.", _SECRET_KEY_FILE)
                return
    except OSError as exc:
        logger.warning("Could not read persisted SECRET_KEY: %s", exc)

    key = secrets.token_hex(32)
    settings.secret_key = key
    if _write_secret_file(_SECRET_KEY_FILE, key):
        logger.warning(
            "Generated a new SECRET_KEY and stored it in %s.", _SECRET_KEY_FILE
        )
    else:
        logger.error(
            "Generated a transient SECRET_KEY — it could not be persisted, so "
            "all sessions will be invalidated on the next restart."
        )


def ensure_admin_credentials(settings: Settings) -> None:
    """Resolve the admin password hash, bootstrapping it on first launch.

    Precedence:
      1. A persisted hash exists under DATA_DIR → reused as-is.
      2. None                                  → generate, persist, log once.

    The resulting bcrypt hash is stored on ``settings.admin_password_hash`` so
    that ``verify_user`` can authenticate the built-in admin. The password is
    never read from the environment.
    """
    # 1. Reuse a previously persisted hash (stable across restarts).
    try:
        if _ADMIN_HASH_FILE.exists():
            stored = _ADMIN_HASH_FILE.read_text().strip()
            if stored:
                settings.set_admin_password_hash(stored)
                logger.info("Admin password loaded from %s.", _ADMIN_HASH_FILE)
                return
    except OSError as exc:
        logger.warning("Could not read persisted admin hash: %s", exc)

    # 2. First launch — generate a one-time password, persist its hash, log it.
    password = _generate_password()
    hashed = hash_password(password)
    settings.set_admin_password_hash(hashed)

    if not _write_secret_file(_ADMIN_HASH_FILE, hashed):
        logger.error(
            "Could not persist the admin password hash — the generated password "
            "will change on the next restart."
        )

    _log_generated_password(settings.admin_username, password)
