"""
Portalcrane - Secret encryption at rest.

Symmetric (Fernet) encryption for sensitive values persisted on disk —
currently the passwords/tokens of external registries stored in
``external_registries.json``.

The Fernet key is *derived* from the JWT ``SECRET_KEY`` via HKDF-SHA256, so
there is no extra key material to manage: the same secret that already protects
sessions also protects secrets at rest, and rotating ``SECRET_KEY`` rotates the
encryption key (old ciphertexts then become undecryptable — see ``decrypt``).
"""

import base64
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings

logger = logging.getLogger(__name__)

# Marker prefixing every ciphertext we write. It lets decrypt() distinguish
# encrypted values from legacy plaintext entries written before encryption was
# introduced, enabling transparent, lazy migration on the next save.
_ENC_PREFIX = "enc:v1:"

# Fixed, non-secret context string binding the derived key to its purpose.
_HKDF_INFO = b"portalcrane:secret-at-rest:v1"


@lru_cache(maxsize=4)
def _fernet_for(secret_key: str) -> Fernet:
    """Derive (and cache) a Fernet instance from the JWT secret key."""
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def _fernet() -> Fernet:
    """Return the Fernet instance for the currently resolved SECRET_KEY.

    SECRET_KEY is resolved at startup by core/bootstrap.py and mutated on the
    cached Settings object, so it is read lazily here rather than at import time.
    """
    return _fernet_for(get_settings().secret_key)


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext*, returning a prefixed ciphertext string.

    Empty / falsy values are returned unchanged — there is nothing to protect
    and we avoid storing a ciphertext for an empty secret.
    """
    if not plaintext:
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt a value produced by :func:`encrypt`.

    Values without the encryption prefix are returned as-is — this transparently
    supports legacy plaintext entries written before encryption existed; they are
    re-encrypted the next time the record is saved.

    A prefixed value that cannot be decrypted (e.g. SECRET_KEY was rotated)
    yields an empty string rather than crashing the caller; the operator must
    re-enter the affected credentials.
    """
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    token = value[len(_ENC_PREFIX) :].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        logger.error(
            "Failed to decrypt a stored secret — SECRET_KEY may have changed. "
            "Returning an empty value; the credential must be re-entered."
        )
        return ""
