"""
Portalcrane - OIDC Service
All OIDC business logic: config persistence, discovery-document fetching,
authorization-code exchange, and username extraction.
"""

import json
from pathlib import Path

import httpx
from jose import jwt as jose_jwt
from pydantic import BaseModel

from ..config import DATA_DIR, DEFAULT_TIMEOUT, Settings

# Persistent OIDC configuration file (overrides env vars at runtime)
_OIDC_CONFIG_FILE = Path(f"{DATA_DIR}/oidc_config.json")


# ─── Pydantic models ──────────────────────────────────────────────────────────


class OidcPublicConfig(BaseModel):
    """OIDC configuration exposed to the public login page (no secret)."""

    enabled: bool
    client_id: str
    issuer: str
    redirect_uri: str
    post_logout_redirect_uri: str = ""
    authorization_endpoint: str = ""
    end_session_endpoint: str = ""
    response_type: str = "code"
    scope: str = "openid profile email"


class OidcAdminSettings(BaseModel):
    """Full OIDC configuration (including client_secret) for the settings page."""

    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    post_logout_redirect_uri: str = ""
    response_type: str = "code"
    scope: str = "openid profile email"


# ─── Persistence helpers ──────────────────────────────────────────────────────


def load_oidc_config() -> dict:
    """Load the persisted OIDC config from disk. Returns {} when absent."""
    try:
        if _OIDC_CONFIG_FILE.exists():
            return json.loads(_OIDC_CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


def save_oidc_config(data: dict) -> None:
    """Persist OIDC configuration to disk."""
    _OIDC_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OIDC_CONFIG_FILE.write_text(json.dumps(data, indent=2))


# ─── Config merge helper ──────────────────────────────────────────────────────


def resolve_oidc_settings(settings: Settings) -> OidcAdminSettings:
    """Merge the persisted JSON file over the env-var defaults.

    The JSON file always wins when a key is present, which lets admins change
    OIDC settings at runtime without restarting the container.
    """
    persisted = load_oidc_config()
    return OidcAdminSettings(
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


# ─── Discovery document ───────────────────────────────────────────────────────


async def fetch_oidc_discovery(issuer: str, proxy: str | None) -> dict:
    """Fetch and return the OIDC discovery document for *issuer*.

    Returns an empty dict when the request fails so callers can degrade
    gracefully (e.g. return empty endpoint strings) instead of raising.
    """
    try:
        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.get(
                f"{issuer}/.well-known/openid-configuration",
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return {}


# ─── Public config builder ────────────────────────────────────────────────────


async def build_public_config(settings: Settings) -> OidcPublicConfig:
    """Build the OidcPublicConfig object served to the login page.

    Merges env vars with persisted overrides and enriches the result with
    endpoints from the OIDC discovery document (authorization + end-session).
    Returns a disabled config when OIDC is turned off.
    """
    merged = resolve_oidc_settings(settings)

    if not merged.enabled:
        return OidcPublicConfig(
            enabled=False,
            client_id="",
            issuer="",
            redirect_uri="",
            post_logout_redirect_uri="",
        )

    discovery = await fetch_oidc_discovery(merged.issuer, settings.httpx_proxy)

    return OidcPublicConfig(
        enabled=True,
        client_id=merged.client_id,
        issuer=merged.issuer,
        redirect_uri=merged.redirect_uri,
        post_logout_redirect_uri=merged.post_logout_redirect_uri,
        authorization_endpoint=discovery.get("authorization_endpoint", ""),
        end_session_endpoint=discovery.get("end_session_endpoint", ""),
        response_type=merged.response_type,
        scope=merged.scope,
    )


# ─── Authorization-code exchange ─────────────────────────────────────────────


async def exchange_code_for_username(
    code: str,
    settings: Settings,
) -> str:
    """Exchange an authorization code for a username via the token endpoint.

    Steps:
    1. Fetch the discovery document to get token_endpoint and userinfo_endpoint.
    2. POST the code to token_endpoint (client_credentials in Basic Auth).
    3. Call userinfo_endpoint with the returned access_token.
    4. Fall back to id_token claims when userinfo is unavailable.

    Raises RuntimeError on any failure so the calling route can wrap it in an
    appropriate HTTPException.
    """
    merged = resolve_oidc_settings(settings)

    async with httpx.AsyncClient(proxy=settings.httpx_proxy) as client:
        # Step 1 — discovery
        discovery_resp = await client.get(
            f"{merged.issuer}/.well-known/openid-configuration",
            timeout=DEFAULT_TIMEOUT,
        )
        discovery_resp.raise_for_status()
        discovery = discovery_resp.json()
        token_endpoint: str = discovery.get("token_endpoint", "")
        userinfo_endpoint: str = discovery.get("userinfo_endpoint", "")

        # Step 2 — exchange authorization code for tokens
        token_resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": merged.redirect_uri,
            },
            auth=(merged.client_id, merged.client_secret),
            timeout=DEFAULT_TIMEOUT,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        id_token: str = token_data.get("id_token", "")
        access_token_oidc: str = token_data.get("access_token", "")

        # Step 3 — userinfo endpoint (preferred)
        username = ""
        if userinfo_endpoint and access_token_oidc:
            userinfo_resp = await client.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token_oidc}"},
                timeout=DEFAULT_TIMEOUT,
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
            username = (
                userinfo.get("preferred_username")
                or userinfo.get("name")
                or userinfo.get("email")
                or ""
            )

        # Step 4 — fall back to id_token claims
        if not username and id_token:
            claims = jose_jwt.get_unverified_claims(id_token)
            username = (
                claims.get("preferred_username")
                or claims.get("name")
                or claims.get("email")
                or claims.get("sub", "oidc-user")
            )

    return username or "oidc-user"
