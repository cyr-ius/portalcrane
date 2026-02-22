"""
Portalcrane - Application Configuration
All settings loaded from environment variables
"""

import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Registry configuration
    registry_url: str = "http://localhost:5000"
    registry_username: str = ""
    registry_password: str = ""
    # Address used by the Docker daemon (on the HOST) to push images.
    # Needed when REGISTRY_URL uses a Docker-internal hostname (e.g. "registry")
    # that the host Docker daemon cannot resolve.
    # Example: "localhost:5000" or "192.168.1.10:5000"
    # Defaults to REGISTRY_URL's host:port if not set.
    registry_push_host: str = ""

    # Admin credentials (local auth)
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # JWT configuration
    secret_key: str = secrets.token_hex(32)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours

    # OIDC configuration
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""

    # Docker Hub configuration
    dockerhub_username: str = ""
    dockerhub_password: str = ""

    # ── HTTP Proxy ────────────────────────────────────────────────────────────
    # Used for outbound HTTP calls initiated BY Portalcrane:
    #   - Docker Hub API (search, tags)
    #   - OIDC discovery & token exchange
    #   - docker pull / docker push (staging pipeline only)
    #
    # The Docker daemon itself is NOT configured — pulls triggered outside
    # of Portalcrane (e.g. direct `docker pull` on the host) are unaffected.
    #
    # Format: http://[user:password@]host:port
    # Example: http://squid:3128  or  http://alice:secret@proxy.corp:8080
    http_proxy: str = ""
    https_proxy: str = ""
    # Comma-separated list of hosts that bypass the proxy.
    # Example: "localhost,127.0.0.1,registry.corp"
    no_proxy: str = "localhost,127.0.0.1"

    # ── Docker Pull Proxy ─────────────────────────────────────────────────────
    # Overrides http_proxy / https_proxy specifically for docker pull/push subprocesses.
    # Useful when the Docker CLI needs a different proxy than the backend HTTP client.
    # If empty, falls back to https_proxy / http_proxy.
    docker_pull_proxy: str = ""

    # ── ClamAV configuration ──────────────────────────────────────────────────
    # Set CLAMAV_ENABLED=false to skip ClamAV scanning entirely in the pipeline.
    clamav_enabled: bool = True
    clamav_host: str = "localhost"
    clamav_port: int = 3310

    # Vulnerability scanning configuration (complementary to ClamAV malware scan)
    vuln_scan_enabled: bool = True
    vuln_scan_severities: str = "CRITICAL,HIGH"
    vuln_ignore_unfixed: bool = False
    vuln_scan_timeout: str = "5m"

    # Staging configuration
    staging_dir: str = "/tmp/staging"

    # Advanced mode
    advanced_mode: bool = False

    # Docker Hub API v2 endpoint (for search/tags).
    dockerhub_api_url: str = "https://hub.docker.com/v2"

    # Application version exposed via GET /api/about for GitHub update checks.
    # Override with the APP_VERSION environment variable at container startup.
    # The Dockerfile / CI pipeline should set this to the git tag (e.g. "1.2.3").
    app_version: str = "1.0.0"

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
    def docker_env_proxy(self) -> dict:
        """Build env vars to inject into docker pull subprocess."""
        env = {}
        proxy = self.docker_pull_proxy or self.http_proxy
        if proxy:
            env["HTTP_PROXY"] = proxy
            env["http_proxy"] = proxy
        proxy_s = self.docker_pull_proxy or self.https_proxy or self.http_proxy
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


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
