"""
Portalcrane - Application Configuration
All settings loaded from environment variables
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Default data directory (can be overridden by DATA_DIR env variable for debugging)
DATA_DIR = os.getenv("DATA_DIR", "/var/lib/portalcrane")
STAGING_DIR = f"{DATA_DIR}/cache/staging"

# Default directory for ui
FRONTEND_DIR = Path("/app/ui").resolve()
INDEX_HTML = FRONTEND_DIR / "index.html"

# Container Trivy URL
TRIVY_SERVER_URL: str = "http://localhost:4954"

# Container registry URL (used for skopeo copy operations)
REGISTRY_URL: str = "http://localhost:5000"
REGISTRY_HOST: str = urlparse(REGISTRY_URL).netloc

# HTTP client timeout for GitHub API calls (in seconds)
DEFAULT_TIMEOUT: float = 10.0


# ── Settings ─────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Registry configuration
    registry_proxy_auth_enabled: bool = True

    # Admin credentials (local auth)
    # The admin password is always managed by the application: a secure one-time
    # password is auto-generated on first launch and printed in the logs, then
    # its bcrypt hash is persisted under DATA_DIR. See core/bootstrap.py.
    admin_username: str = "admin"

    # Resolved bcrypt hash of the admin password. A private attribute on purpose:
    # it is set at startup by core/bootstrap.py and must NOT be loadable from the
    # environment. Read it through the admin_password_hash property below.
    _admin_password_hash: str = PrivateAttr(default="")

    # JWT configuration
    # secret_key is auto-generated and persisted under DATA_DIR on first launch
    # when left at the default. See core/bootstrap.py.
    secret_key: str = "change-this-secret-key-in-production"
    access_token_expire_minutes: int = 480  # 8 hours

    # Name of the HttpOnly cookie carrying the session JWT for browser sessions.
    # The token is never exposed to JavaScript, which neutralises XSS token theft.
    auth_cookie_name: str = "pc_token"

    # OIDC configuration
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_post_logout_redirect_uri: str = ""
    oidc_response_type: str = "code"
    oidc_scope: str = "openid profile email"

    # OIDC-only mode: when True, local username/password login (including the
    # built-in env-admin) is disabled and authentication is delegated entirely
    # to the OIDC provider. The admin group-claim mapping below must be
    # configured to avoid a lockout.
    oidc_only: bool = False

    # Admin bootstrap for OIDC users via group-claim mapping: when an OIDC user
    # carries oidc_admin_group in the claim named oidc_admin_group_claim (e.g.
    # "groups"), they get admin. Admin status is re-evaluated on every SSO login
    # (live promote/demote).
    oidc_admin_group_claim: str = ""
    oidc_admin_group: str = ""

    # Regular-user mapping for OIDC via group-claim mapping. Allow regular-user
    # access when oidc_user_group is present in the claim named
    # oidc_user_group_claim (e.g. "groups").
    oidc_user_group_claim: str = ""
    oidc_user_group: str = ""

    # Restrict OIDC access to mapped groups. When True, OIDC access becomes an
    # allowlist: only users matching the admin OR the regular-user group mapping
    # are allowed in; everyone else is denied (403) and never provisioned. When
    # False (default), every authenticated OIDC user is provisioned as a regular
    # user regardless of their groups.
    oidc_restrict_to_groups: bool = False

    # HTTP Proxy
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1"

    # Custom CA bundle (PEM) used to verify TLS for outbound OIDC calls.
    # Point this at a mounted file containing the private CA chain
    # (intermediate + root, concatenated). Falls back to the standard
    # SSL_CERT_FILE / REQUESTS_CA_BUNDLE env vars when left empty.
    oidc_ca_bundle: str = ""

    # Vulnerability scanning configuration
    # Master kill-switch: when TRIVY_ENABLED=false the embedded Trivy server is
    # not started by supervisord (see docker/entrypoint.sh). Mirror that here so
    # the backend degrades gracefully instead of hammering an absent server.
    trivy_enabled: bool = True
    vuln_scan_enabled: bool = True
    vuln_scan_severities: str = "CRITICAL,HIGH"
    vuln_ignore_unfixed: bool = False
    vuln_scan_timeout: str = "5m"

    # Logging level (DEBUG, INFO, WARNING, ERROR)
    log_level: str = "INFO"

    # Audit retention
    audit_max_events: int = 100

    # Swagger UI
    SWAGGER_ENABLE: bool = False

    # ── Internal helpers ─────────────────────────────────────────────────────────

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def admin_password_hash(self) -> str:
        """Bcrypt hash of the admin password, resolved at startup.

        Read-only public accessor. The value is set by core/bootstrap.py via
        set_admin_password_hash(); it is never read from the environment.
        """
        return self._admin_password_hash

    def set_admin_password_hash(self, hashed: str) -> None:
        """Set the resolved admin password hash (called once at startup)."""
        self._admin_password_hash = hashed

    @property
    def httpx_proxy(self) -> str | None:
        """
        Return a single proxy URL string for httpx >= 0.28.

        httpx 0.28 removed the legacy `proxies` dict argument in favour of
        a single `proxy` string (or `mounts` for fine-grained control).
        We prefer HTTPS_PROXY for outbound HTTPS calls (Docker Hub, OIDC),
        falling back to HTTP_PROXY when only the latter is set.
        Returns None when no proxy is configured.
        """
        return self.https_proxy or self.http_proxy or None

    @property
    def httpx_verify(self) -> str | bool:
        """Return the `verify` argument for outbound httpx clients (OIDC).

        When a custom CA bundle is configured (OIDC_CA_BUNDLE, or the standard
        SSL_CERT_FILE / REQUESTS_CA_BUNDLE env vars) and the file exists, its
        path is returned so httpx trusts a private CA chain (intermediate +
        root). Otherwise returns True to keep the default certifi verification.
        """
        candidate = (
            self.oidc_ca_bundle
            or os.environ.get("SSL_CERT_FILE", "")
            or os.environ.get("REQUESTS_CA_BUNDLE", "")
        )
        if candidate and Path(candidate).is_file():
            return candidate
        return True

    @property
    def env_proxy(self) -> dict:
        """
        Build environment variables injected into every skopeo subprocess.
        skopeo reads the standard HTTP_PROXY / HTTPS_PROXY variables.
        """
        env: dict = {}
        proxy = self.http_proxy
        if proxy:
            env["HTTP_PROXY"] = proxy
            env["http_proxy"] = proxy
        proxy_s = self.https_proxy or self.http_proxy
        if proxy_s:
            env["HTTPS_PROXY"] = proxy_s
            env["https_proxy"] = proxy_s
        if self.no_proxy:
            env["NO_PROXY"] = self.no_proxy
            env["no_proxy"] = self.no_proxy
        return env

    @property
    def vuln_severities(self) -> list[str]:
        """Normalized vulnerability severities list."""
        return [
            s.strip().upper() for s in self.vuln_scan_severities.split(",") if s.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


def staging_root() -> Path:
    """Return the resolved absolute path to the staging root directory."""
    return Path(STAGING_DIR).resolve()


app_settings = get_settings()
