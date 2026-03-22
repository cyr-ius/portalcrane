"""
Portalcrane - Personal Access Tokens Router
Allows any authenticated user to generate long-lived tokens usable with
docker login as a password substitute.

Endpoints:
  - GET    /api/auth/tokens        → list the caller's tokens (hashed, no secret shown)
  - POST   /api/auth/tokens        → create a new token (secret shown once)
  - DELETE /api/auth/tokens/{id}   → revoke a token

Storage: /var/lib/portalcrane/personal_tokens.json
  [
    {
      "id":         "uuid4",
      "username":   "jdupont",
      "name":       "My laptop",
      "token_hash": "bcrypt hash of the raw token",
      "created_at": "ISO-8601",
      "expires_at": "ISO-8601 | null"
    },
    ...
  ]

The raw token is a signed JWT with claim  { "sub": username, "pat": true }.
The registry proxy already accepts Bearer tokens; we extend it to also accept
a PAT supplied as the password field of Basic Auth (docker login flow):

  docker login registry.example.com -u jdupont -p <raw_token>

The proxy will detect that the "password" decodes as a valid JWT and use the
embedded sub claim as the authenticated username.
"""

import json
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from pydantic import BaseModel

from ..config import DATA_DIR, Settings, get_settings
from ..core.jwt import ALGORITHM, UserInfo, get_current_user
from ..core.security import hash_password, verify_password

router = APIRouter()

_TOKENS_FILE = Path(f"{DATA_DIR}/personal_tokens.json")

# Raw token prefix — makes it recognisable in logs and easy to grep.
_TOKEN_PREFIX = "pct_"
_SHORT_TOKEN_LENGTH = 16

# Default validity when no expiry is requested (90 days).
_DEFAULT_EXPIRY_DAYS = 90


# ─── Pydantic models ──────────────────────────────────────────────────────────


class PersonalTokenPublic(BaseModel):
    """Token metadata returned to the frontend (no raw secret, no hash)."""

    id: str
    name: str
    created_at: str
    expires_at: str | None = None
    last_used_at: str | None = None
    short_token_hint: str | None = None


class PersonalTokenCreated(PersonalTokenPublic):
    """Returned only at creation time — contains the raw token shown once."""

    raw_token: str
    short_token: str


class CreateTokenRequest(BaseModel):
    """Payload to create a new personal access token."""

    name: str
    expires_in_days: int | None = None  # None → use default (90 days)


# ─── Storage helpers ──────────────────────────────────────────────────────────


def _load_tokens() -> list[dict]:
    """Load all personal tokens from disk."""
    try:
        if _TOKENS_FILE.exists():
            return json.loads(_TOKENS_FILE.read_text())
    except Exception:
        pass
    return []


def _save_tokens(tokens: list[dict]) -> None:
    """Persist the tokens list to disk."""
    _TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


def _token_to_public(t: dict) -> PersonalTokenPublic:
    """Convert a raw token dict to the public representation."""
    return PersonalTokenPublic(
        id=t["id"],
        name=t["name"],
        created_at=t["created_at"],
        expires_at=t.get("expires_at"),
        last_used_at=t.get("last_used_at"),
        short_token_hint=t.get("short_token_hint"),
    )


def _normalise_short_token(candidate: str) -> str:
    """Normalize user-provided short token input.

    Accepts either the bare 16-char discriminator (recommended) or the
    prefixed form `pct_<discriminator>` and returns only the discriminator.
    """
    token = candidate.strip()
    if token.startswith(_TOKEN_PREFIX):
        token = token.removeprefix(_TOKEN_PREFIX)
    return token


def _generate_short_token() -> str:
    """Generate a 16-char high-entropy discriminator for PAT quick login."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(_SHORT_TOKEN_LENGTH))


# ─── Token verification (used by registry_proxy) ─────────────────────────────


def verify_personal_token(raw_token: str, settings: Settings) -> str | None:
    """Verify a raw PAT string and return the associated username.

    Steps:
    1. Decode the JWT to extract the username (sub) and the pat=true claim.
    2. Look up the token record by username and verify the bcrypt hash.
    3. Check that the token has not expired.
    4. Update last_used_at in the store.

    Returns the username string on success, None on any failure.
    """
    tokens = _load_tokens()
    now = datetime.now(timezone.utc)

    # Path A: full PAT (legacy and current format)
    try:
        token_to_decode = raw_token
        if raw_token.startswith(_TOKEN_PREFIX):
            token_to_decode = raw_token[len(_TOKEN_PREFIX) :]
        payload = jwt.decode(
            token_to_decode, settings.secret_key, algorithms=[ALGORITHM]
        )
    except Exception:
        payload = None

    if payload and payload.get("pat"):
        username: str = payload.get("sub", "")
        if username:
            for token in tokens:
                if token["username"] != username:
                    continue
                if not verify_password(raw_token, token.get("token_hash", "")):
                    continue
                # Check expiry stored in the record (belt-and-suspenders with JWT exp)
                if token.get("expires_at"):
                    exp = datetime.fromisoformat(token["expires_at"])
                    if now > exp:
                        continue
                token["last_used_at"] = now.isoformat()
                _save_tokens(tokens)
                return username

    # Path B: short 16-char discriminator (or pct_<discriminator>)
    short_candidate = _normalise_short_token(raw_token)
    if len(short_candidate) != _SHORT_TOKEN_LENGTH:
        return None

    for token in tokens:
        short_hash = token.get("short_token_hash", "")
        if not short_hash:
            continue
        if not verify_password(short_candidate, short_hash):
            continue
        if token.get("expires_at"):
            exp = datetime.fromisoformat(token["expires_at"])
            if now > exp:
                continue

        token["last_used_at"] = now.isoformat()
        _save_tokens(tokens)
        return token["username"]

    return None


def revoke_tokens_for_username(username: str) -> int:
    """Revoke all PATs owned by *username* and return the number removed."""
    tokens = _load_tokens()
    filtered = [t for t in tokens if t.get("username") != username]
    removed_count = len(tokens) - len(filtered)
    if removed_count:
        _save_tokens(filtered)
    return removed_count


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/tokens", response_model=list[PersonalTokenPublic])
async def list_tokens(
    current_user: UserInfo = Depends(get_current_user),
) -> list[PersonalTokenPublic]:
    """Return all personal access tokens owned by the current user."""
    tokens = _load_tokens()
    return [
        _token_to_public(t) for t in tokens if t["username"] == current_user.username
    ]


@router.post(
    "/tokens",
    response_model=PersonalTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    payload: CreateTokenRequest,
    current_user: UserInfo = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> PersonalTokenCreated:
    """Create a personal access token for the current user.

    The raw token is returned only once in the response — it cannot be
    retrieved again.  Store it securely (e.g. in a secrets manager).

    Usage with docker login:
        docker login <registry-host> -u <username> -p <raw_token>
    """
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Token name must not be empty",
        )

    expiry_days = payload.expires_in_days or _DEFAULT_EXPIRY_DAYS
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expiry_days)

    # Build a signed JWT that carries the pat=true claim
    claims = {
        "sub": current_user.username,
        "pat": True,
        "exp": expires_at,
        "iat": now,
        "jti": str(uuid.uuid4()),  # Unique JWT ID for each token
    }
    raw_token = _TOKEN_PREFIX + jwt.encode(
        claims, settings.secret_key, algorithm=ALGORITHM
    )
    short_token = _generate_short_token()

    token_id = str(uuid.uuid4())
    entry = {
        "id": token_id,
        "username": current_user.username,
        "name": name,
        "token_hash": hash_password(raw_token),
        "short_token_hash": hash_password(short_token),
        "short_token_hint": f"{short_token[:4]}…{short_token[-4:]}",
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "last_used_at": None,
    }

    tokens = _load_tokens()
    tokens.append(entry)
    _save_tokens(tokens)

    return PersonalTokenCreated(
        id=token_id,
        name=name,
        created_at=now.isoformat(),
        expires_at=expires_at.isoformat(),
        raw_token=raw_token,
        short_token=short_token,
        short_token_hint=entry["short_token_hint"],
    )


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: str,
    current_user: UserInfo = Depends(get_current_user),
) -> None:
    """Revoke a personal access token.

    Users can only revoke their own tokens; admins can revoke any token.
    """
    tokens = _load_tokens()
    target = next((t for t in tokens if t["id"] == token_id), None)

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )

    # Non-admin users can only revoke their own tokens
    if not current_user.is_admin and target["username"] != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only revoke your own tokens",
        )

    _save_tokens([t for t in tokens if t["id"] != token_id])
