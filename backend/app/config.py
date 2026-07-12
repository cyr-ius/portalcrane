"""
Portalcrane - Application Configuration
All settings loaded from environment variables
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DATA_DIR = os.getenv("DATA_DIR", "/var/lib/portalcrane")
STAGING_DIR = f"{DATA_DIR}/cache/staging"
FRONTEND_DIR = Path("/app/ui").resolve()
INDEX_HTML = FRONTEND_DIR / "index.html"
TRIVY_SERVER_URL: str = "http://localhost:4954"
REGISTRY_URL: str = "http://localhost:5000"
REGISTRY_HOST: str = urlparse(REGISTRY_URL).netloc
DEFAULT_TIMEOUT: float = 10.0


# ── Settings ─────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    access_token_expire_minutes: int = 480  # 8 hours
    admin_username: str = "admin"
    api_keys_enabled: bool = True
    app_version: str = "Development"
    auth_cookie_name: str = "pc_token"
    log_level: str = "INFO"
    # Left empty on purpose: core/bootstrap.py then generates and persists a
    # random secret under DATA_DIR on first launch.
    secret_key: str = ""
    swagger_enabled: bool = False

    # Audit log configuration
    audit_max_events: int = 100

    # OIDC configuration
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_post_logout_redirect_uri: str = ""
    oidc_response_type: str = "code"
    oidc_scope: str = "openid profile email groups"
    oidc_only: bool = False
    oidc_admin_group_claim: str = ""
    oidc_admin_group: str = ""
    oidc_user_group_claim: str = ""
    oidc_user_group: str = ""
    oidc_restrict_to_groups: bool = False

    # HTTP Proxy
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1"

    # Syslog forwarding (env-var defaults, overridable via the Network UI).
    # A persisted admin override in DATA_DIR/proxy_config.json always wins over
    # these values — same precedence as the proxy settings above.
    syslog_enabled: bool = False
    syslog_host: str = ""
    syslog_port: int = 514
    syslog_protocol: str = "udp"  # 'udp' | 'tcp' | 'tcp+tls'
    syslog_rfc: str = "rfc5424"  # 'rfc3164' | 'rfc5424'
    syslog_forward_audit: bool = True
    syslog_forward_uvicorn: bool = False
    syslog_tls_verify: bool = True
    syslog_tls_ca_cert: str = ""
    syslog_auth_enabled: bool = False
    syslog_auth_username: str = ""
    syslog_auth_password: str = ""

    # Audit-log email delivery (env-var defaults, overridable via the Network UI).
    # A persisted admin override always wins over these values.
    email_enabled: bool = False
    email_host: str = ""
    email_port: int = 587
    email_security: str = "starttls"  # 'none' | 'starttls' | 'ssl'
    email_username: str = ""
    email_password: str = ""
    email_from_address: str = ""
    email_to_addresses: str = ""  # comma-separated recipients
    email_subject: str = "Portalcrane audit log"
    email_notify_login: bool = False
    email_notify_audit: bool = False

    # Registry configuration
    registry_proxy_auth_enabled: bool = True

    # Vulnerability scanning configuration
    # Master kill-switch: when TRIVY_ENABLED=false the embedded Trivy server is
    # not started by supervisord (see docker/entrypoint.sh). Mirror that here so
    # the backend degrades gracefully instead of hammering an absent server.
    trivy_enabled: bool = True
    vuln_scan_enabled: bool = True
    vuln_scan_severities: str = "CRITICAL,HIGH"
    vuln_ignore_unfixed: bool = False
    vuln_scan_timeout: str = "5m"

    # Reverse-proxy trust boundary. Comma-separated CIDR ranges (or bare IPs) of
    # the reverse proxies in front of the app. Forwarded client IPs
    # (Forwarded / X-Forwarded-For / X-Real-IP) are honoured ONLY when the direct
    # TCP peer matches one of these ranges; otherwise the headers are treated as
    # spoofable and ignored, falling back to the real peer address. This feeds
    # both the audit log and the per-IP rate limiter, so leaving it empty
    # (default) keys every request by the real peer — safe, but behind a proxy
    # all clients then share the proxy's IP. Docker env var: TRUSTED_PROXIES
    # (e.g. "10.0.0.0/8,172.16.0.0/12").
    trusted_proxies: str = ""
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 100  # per IP per window, all /api/* routes
    # The login endpoint gets its own bucket: a stricter budget over a longer
    # window, so a password brute-force is throttled without starving the
    # general API budget.
    rate_limit_login_path: str = "/api/auth/login"
    rate_limit_login_window_seconds: int = 300
    rate_limit_login_max_attempts: int = 5  # per IP per login window

    # ── Internal helpers ─────────────────────────────────────────────────────────

    class Config:
        env_file = ".env"
        case_sensitive = False

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

        When a custom CA bundle is configured via the standard
        SSL_CERT_FILE / REQUESTS_CA_BUNDLE env vars and the file exists, its
        path is returned so httpx trusts a private CA chain (intermediate +
        root). Otherwise returns True to keep the default certifi verification.
        """
        candidate = os.environ.get("SSL_CERT_FILE", "") or os.environ.get(
            "REQUESTS_CA_BUNDLE", ""
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
