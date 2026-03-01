"""
Portalcrane - Application Configuration
All settings loaded from environment variables
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Default data directory (can be overridden by DATA_DIR env variable for debugging)
DATA_DIR = os.getenv("DATA_DIR", "/var/lib/portalcrane")
STAGING_DIR = f"{DATA_DIR}/cache/staging"

# GitHub repository coordinates (owner/repo)
GITHUB_OWNER = "cyr-ius"
GITHUB_REPO = "portalcrane"

# Application metadata shown in the Settings page
APP_AUTHOR = GITHUB_OWNER
APP_AI_GENERATOR = "Claude (Anthropic)"

# GitHub API endpoint to fetch the latest published release
GITHUB_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)

# GitHub repository HTML URL displayed as a clickable link in the UI
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"

# JWT configuration
ALGORITHM = "HS256"

# Trivy server URL (used for vulnerability scanning)
TRIVY_SERVER_URL: str = "http://127.0.0.1:4954"
TRIVY_CACHE_DIR = f"{DATA_DIR}/cache/trivy"
TRIVY_BINARY = "/usr/local/bin/trivy"
TRIVY_DB_METADATA = Path(TRIVY_CACHE_DIR) / "db" / "metadata.json"

# Container registry URL (used for skopeo copy operations)
REGISTRY_URL: str = "http://localhost:5000"

# HTTP client timeout for GitHub API calls (in seconds)
HTTPX_TIMEOUT: float = 10.0
PROXY_TIMEOUT: float = 300.0

# Docker Hub API v2 endpoint (for search/tags).
DOCKERHUB_API_URL: str = "https://hub.docker.com/v2"


# ── Settings ─────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Registry configuration
    registry_proxy_auth_enabled: bool = True

    # Admin credentials (local auth)
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # JWT configuration
    secret_key: str = "change-this-secret-key-in-production"
    access_token_expire_minutes: int = 480  # 8 hours

    # OIDC configuration
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_post_logout_redirect_uri: str = ""
    oidc_response_type: str = "code"
    oidc_scope: str = "openid profile email"

    # HTTP Proxy
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1"

    # Vulnerability scanning configuration
    vuln_scan_enabled: bool = True
    vuln_scan_severities: str = "CRITICAL,HIGH"
    vuln_ignore_unfixed: bool = False
    vuln_scan_timeout: str = "5m"

    # Logging level (DEBUG, INFO, WARNING, ERROR)
    log_level: str = "INFO"

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

    @model_validator(mode="after")
    def check_secret_key(self) -> "Settings":
        if (
            not self.secret_key
            or self.secret_key == "change-this-secret-key-in-production"
        ):
            raise ValueError("SECRET_KEY environment variable must be set")
        return self


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
