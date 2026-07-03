"""
Portalcrane - OIDC Service
All OIDC business logic: config persistence, discovery-document fetching,
authorization-code exchange, and username extraction.
"""

import json
import logging
from pathlib import Path
from typing import Any, cast

import httpx
from jose import jwt as jose_jwt
from pydantic import BaseModel

from ..config import DATA_DIR, DEFAULT_TIMEOUT, Settings

logger = logging.getLogger(__name__)

# Persistent OIDC configuration file (overrides env vars at runtime)
_OIDC_CONFIG_FILE = Path(f"{DATA_DIR}/oidc_config.json")


# ─── Pydantic models ──────────────────────────────────────────────────────────


class OidcPublicConfig(BaseModel):
    """OIDC configuration exposed to the public login page (no secret).

    oidc_only is published so the login page can hide the local credential
    form entirely. The admin mappings (admin_group*) are NEVER exposed here —
    they live only in OidcAdminSettings (admin-gated).
    """

    enabled: bool
    client_id: str
    issuer: str
    redirect_uri: str
    post_logout_redirect_uri: str = ""
    authorization_endpoint: str = ""
    end_session_endpoint: str = ""
    response_type: str = "code"
    scope: str = "openid profile email"
    oidc_only: bool = False


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
    # OIDC-only mode and admin bootstrap (see config.Settings for semantics).
    oidc_only: bool = False
    admin_group_claim: str = ""
    admin_group: str = ""
    # Regular-user mapping. When set, OIDC access becomes an allowlist (see
    # config.Settings and is_oidc_user_allowed for semantics).
    user_group_claim: str = ""
    user_group: str = ""


class OidcIdentity(BaseModel):
    """Identity resolved from an OIDC authorization-code exchange.

    groups carries the values of the configured admin group claim (when any),
    used to decide whether the user should be granted admin rights.
    """

    username: str
    groups: list[str] = []


# ─── Persistence helpers ──────────────────────────────────────────────────────


def load_oidc_config() -> dict[str, Any]:
    """Load the persisted OIDC config from disk. Returns {} when absent."""
    try:
        if _OIDC_CONFIG_FILE.exists():
            return cast(dict[str, Any], json.loads(_OIDC_CONFIG_FILE.read_text()))
    except Exception:
        pass
    return {}


def save_oidc_config(data: dict[str, Any]) -> None:
    """Persist OIDC configuration to disk."""
    _OIDC_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OIDC_CONFIG_FILE.write_text(json.dumps(data, indent=2))


# ─── Admin mapping helpers ────────────────────────────────────────────────────


def _matches_mapping(identity: OidcIdentity, group_claim: str, group: str) -> bool:
    """Return True when *identity* matches a group mapping.

    A match is granted when *group* is present in identity.groups (only when
    both the claim name and the expected group value are configured).
    """
    if group_claim and group:
        groups = {g.casefold() for g in identity.groups}
        if group.casefold() in groups:
            return True

    return False


def is_oidc_admin(identity: OidcIdentity, merged: OidcAdminSettings) -> bool:
    """Return True when *identity* should be granted admin rights.

    Admin is granted when admin_group is present in identity.groups (only when
    both the claim name and the expected group value are configured).
    """
    return _matches_mapping(identity, merged.admin_group_claim, merged.admin_group)


def has_user_restriction(merged: OidcAdminSettings) -> bool:
    """Return True when a regular-user mapping is configured.

    When True, OIDC access is restricted to an allowlist: only users matching an
    admin mapping OR the regular-user mapping are allowed in (see
    is_oidc_user_allowed). When False, every authenticated OIDC user is admitted.
    """
    return bool(merged.user_group_claim and merged.user_group)


def is_oidc_user_allowed(identity: OidcIdentity, merged: OidcAdminSettings) -> bool:
    """Return True when *identity* matches the regular-user mapping.

    Mirrors is_oidc_admin but against user_group. Only meaningful when
    has_user_restriction is True.
    """
    return _matches_mapping(identity, merged.user_group_claim, merged.user_group)


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
        oidc_only=persisted.get("oidc_only", settings.oidc_only),
        admin_group_claim=persisted.get(
            "admin_group_claim", settings.oidc_admin_group_claim
        ),
        admin_group=persisted.get("admin_group", settings.oidc_admin_group),
        user_group_claim=persisted.get(
            "user_group_claim", settings.oidc_user_group_claim
        ),
        user_group=persisted.get("user_group", settings.oidc_user_group),
    )


# ─── Discovery document ───────────────────────────────────────────────────────


async def fetch_oidc_discovery(issuer: str, proxy: str | None) -> dict[str, Any]:
    """Fetch and return the OIDC discovery document for *issuer*.

    Returns an empty dict when the request fails so callers can degrade
    gracefully (e.g. return empty endpoint strings) instead of raising.
    """
    normalized_issuer = issuer.rstrip("/")
    if not normalized_issuer:
        return {}

    try:
        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.get(
                f"{normalized_issuer}/.well-known/openid-configuration",
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                return cast(dict[str, Any], response.json())
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
        oidc_only=merged.oidc_only,
    )


# ─── Authorization-code exchange ─────────────────────────────────────────────


def _extract_username(source: dict[str, Any]) -> str:
    """Pick the best username candidate from a userinfo/claims mapping."""
    return (
        source.get("preferred_username")
        or source.get("name")
        or source.get("email")
        or ""
    )


def _extract_groups(source: dict[str, Any], claim: str) -> list[str]:
    """Read the configured group claim and normalise it to a list of strings.

    Providers expose groups/roles either as a JSON array or as a single string;
    both shapes are normalised here. Returns an empty list when no claim name is
    configured or the value is absent.
    """
    if not claim:
        return []
    value = source.get(claim)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def _collect_groups(source: dict[str, Any], merged: OidcAdminSettings) -> list[str]:
    """Collect group values from every configured group claim (admin + user).

    Admin and regular-user mappings may point at different claims (e.g. "groups"
    and "roles"); the values of all configured claims are merged into a single
    de-duplicated list used by both is_oidc_admin and is_oidc_user_allowed.
    """
    claims: list[str] = []
    for claim in (merged.admin_group_claim, merged.user_group_claim):
        if claim and claim not in claims:
            claims.append(claim)

    result: list[str] = []
    for claim in claims:
        for group in _extract_groups(source, claim):
            if group not in result:
                result.append(group)
    return result


async def exchange_code_for_identity(
    code: str,
    settings: Settings,
) -> OidcIdentity:
    """Exchange an authorization code for an OIDC identity (username + groups).

    Steps:
    1. Fetch the discovery document to get token_endpoint and userinfo_endpoint.
    2. POST the code to token_endpoint (client_credentials in Basic Auth).
    3. Call userinfo_endpoint with the returned access_token.
    4. Fall back to id_token claims when userinfo is unavailable.

    The username and the configured admin group claim are merged from both the
    userinfo response and the id_token claims (userinfo wins for the username).

    Raises on any HTTP failure so the calling route can wrap it in an
    appropriate HTTPException.
    """
    merged = resolve_oidc_settings(settings)

    async with httpx.AsyncClient(proxy=settings.httpx_proxy) as client:
        # Step 1 — discovery
        normalized_issuer = merged.issuer.rstrip("/")
        discovery_resp = await client.get(
            f"{normalized_issuer}/.well-known/openid-configuration",
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

        username = ""
        groups: list[str] = []

        # Step 3 — userinfo endpoint (preferred)
        if userinfo_endpoint and access_token_oidc:
            userinfo_resp = await client.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token_oidc}"},
                timeout=DEFAULT_TIMEOUT,
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
            username = _extract_username(userinfo)
            groups = _collect_groups(userinfo, merged)

        # Step 4 — fall back to id_token claims (for username and/or groups)
        if id_token and (not username or not groups):
            claims = jose_jwt.get_unverified_claims(id_token)
            if not username:
                username = _extract_username(claims) or claims.get("sub", "oidc-user")
            if not groups:
                groups = _collect_groups(claims, merged)

    logger.debug(
        "OIDC identity resolved: username=%s groups=%s",
        username or "oidc-user",
        groups,
    )
    return OidcIdentity(username=username or "oidc-user", groups=groups)
