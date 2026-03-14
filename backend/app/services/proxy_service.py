"""
Portalcrane - Proxy & Syslog Service
======================================
Manages runtime overrides for:
  - OS-level HTTP/HTTPS proxy environment variables
  - Syslog forwarding of audit and uvicorn logs (RFC 5424 / RFC 3164)

Override priority (highest → lowest):
  1. Persisted admin override  (DATA_DIR/proxy_config.json)
  2. Environment variables     (Settings.http_proxy / https_proxy / no_proxy)

When a proxy override is saved, the values are written directly into
os.environ so that every subsequent subprocess (skopeo, trivy) and every
httpx call that reads the environment picks them up immediately — without
requiring a container restart.
"""

import json
import logging
import logging.handlers
import os
import ssl
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel

from ..config import DATA_DIR, Settings

logger = logging.getLogger(__name__)

# ── Persistence file ──────────────────────────────────────────────────────────

_PROXY_CONFIG_FILE = Path(DATA_DIR) / "proxy_config.json"

# ── OS environment variable names managed by this service ────────────────────

_PROXY_ENV_KEYS = [
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
]

# ─── Pydantic models ──────────────────────────────────────────────────────────


class ProxySettings(BaseModel):
    """Proxy override values stored on disk and served via the API."""

    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1"
    # Optional proxy authentication credentials
    proxy_username: str = ""
    proxy_password: str = ""
    # True when these values override the container environment variables
    proxy_override: bool = False


class SyslogSettings(BaseModel):
    """Syslog forwarding configuration stored on disk and served via the API."""

    enabled: bool = False
    host: str = ""
    port: int = 514
    # Protocol: 'udp', 'tcp', or 'tcp+tls'
    protocol: str = "udp"
    # RFC variant: 'rfc3164' or 'rfc5424'
    rfc: str = "rfc5424"
    # Channels to forward
    forward_audit: bool = True
    forward_uvicorn: bool = False
    # TLS options (only meaningful when protocol == 'tcp+tls')
    tls_verify: bool = True
    tls_ca_cert: str = ""
    # Authentication (only supported over TCP connections — RFC 6587 / RELP)
    auth_enabled: bool = False
    auth_username: str = ""
    auth_password: str = ""


class NetworkConfig(BaseModel):
    """Combined network configuration returned by the API."""

    proxy: ProxySettings
    syslog: SyslogSettings


# ── Persistence helpers ───────────────────────────────────────────────────────


def load_proxy_config() -> dict:
    """Load the persisted network config from disk. Returns {} when absent."""
    try:
        if _PROXY_CONFIG_FILE.exists():
            return json.loads(_PROXY_CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


def save_proxy_config(data: dict) -> None:
    """Persist the network configuration override to disk."""
    _PROXY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROXY_CONFIG_FILE.write_text(json.dumps(data, indent=2))


def clear_proxy_config() -> None:
    """Remove the persisted override, reverting to container environment vars."""
    try:
        if _PROXY_CONFIG_FILE.exists():
            _PROXY_CONFIG_FILE.unlink()
    except Exception:
        pass


# ── OS environment injection ──────────────────────────────────────────────────


def apply_proxy_to_os_environ(proxy: ProxySettings) -> None:
    """
    Write proxy values directly into os.environ so that all subsequent
    subprocesses (skopeo, trivy) and httpx calls that read the environment
    pick them up immediately without a container restart.

    When proxy_override is False (reset to env-var defaults), the managed
    keys are removed from os.environ.  The container's original values are
    NOT restored here because Python has no built-in way to retrieve them
    once overwritten; clearing is the safe and predictable behaviour.

    Call this function:
      - At application startup  → re-applies a persisted override after restart
      - After PUT /api/network/proxy  → activates new proxy immediately
      - After DELETE /api/network/proxy  → clears the override from the process
    """
    if not proxy.proxy_override:
        # Reset: remove all managed proxy keys from the process environment.
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        logger.info("Proxy override cleared — reverting to container env vars")
        return

    # Build effective URLs, embedding credentials when provided
    http_url = _embed_credentials(
        proxy.http_proxy, proxy.proxy_username, proxy.proxy_password
    )
    https_url = _embed_credentials(
        proxy.https_proxy or proxy.http_proxy,
        proxy.proxy_username,
        proxy.proxy_password,
    )

    # Write or clear HTTP_PROXY
    if http_url:
        os.environ["HTTP_PROXY"] = http_url
        os.environ["http_proxy"] = http_url
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("http_proxy", None)

    # Write or clear HTTPS_PROXY
    if https_url:
        os.environ["HTTPS_PROXY"] = https_url
        os.environ["https_proxy"] = https_url
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("https_proxy", None)

    # Write or clear NO_PROXY
    if proxy.no_proxy:
        os.environ["NO_PROXY"] = proxy.no_proxy
        os.environ["no_proxy"] = proxy.no_proxy
    else:
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)

    logger.info(
        "Proxy override applied to os.environ: HTTP_PROXY=%r HTTPS_PROXY=%r NO_PROXY=%r",
        _mask(http_url),
        _mask(https_url),
        proxy.no_proxy,
    )


def _embed_credentials(url: str, username: str, password: str) -> str:
    """
    Embed username:password into a proxy URL.

    http://proxy.corp:3128  +  user  +  secret
    → http://user:secret@proxy.corp:3128
    """
    if not url:
        return ""
    if username and password:
        scheme, sep, rest = url.partition("://")
        if sep:
            return f"{scheme}://{username}:{password}@{rest}"
    return url


def _mask(url: str) -> str:
    """Return a log-safe version of a proxy URL with the password replaced by ***."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.password:
            masked_netloc = f"{parsed.username}:***@{parsed.hostname}" + (
                f":{parsed.port}" if parsed.port else ""
            )
            return urlunparse(parsed._replace(netloc=masked_netloc))
    except Exception:
        pass
    return url


# ── Resolution helpers ────────────────────────────────────────────────────────


def resolve_proxy_settings(settings: Settings) -> ProxySettings:
    """
    Return the effective proxy configuration.

    When a persisted override exists, its values win over env vars — the same
    pattern used by resolve_oidc_settings and resolve_vuln_config.
    """
    persisted = load_proxy_config()
    proxy_data = persisted.get("proxy", {})

    if proxy_data.get("proxy_override"):
        return ProxySettings(**proxy_data)

    # Fall back to container environment variables (already parsed by Settings)
    return ProxySettings(
        http_proxy=settings.http_proxy,
        https_proxy=settings.https_proxy,
        no_proxy=settings.no_proxy,
        proxy_username="",
        proxy_password="",
        proxy_override=False,
    )


def resolve_syslog_settings() -> SyslogSettings:
    """Return the effective syslog configuration from the persisted override."""
    persisted = load_proxy_config()
    syslog_data = persisted.get("syslog", {})
    if syslog_data:
        return SyslogSettings(**syslog_data)
    return SyslogSettings()


def resolve_network_config(settings: Settings) -> NetworkConfig:
    """Return the full effective network configuration."""
    return NetworkConfig(
        proxy=resolve_proxy_settings(settings),
        syslog=resolve_syslog_settings(),
    )


# ── Syslog handler management ─────────────────────────────────────────────────

_syslog_handlers: list[logging.Handler] = []


class _TlsSysLogHandler(logging.handlers.SysLogHandler):
    """
    SysLogHandler subclass that wraps the TCP socket with TLS (RFC 5425).

    The stdlib SysLogHandler does not expose its socket as a public attribute,
    so monkey-patching handler.socket is not type-safe.  Instead we override
    makeSocket() — the documented extension point used to create the transport
    socket — and return a TLS-wrapped socket from there.
    """

    def __init__(self, address: tuple[str, int], ssl_context: ssl.SSLContext) -> None:
        self._ssl_context = ssl_context
        # Store host and port separately so makeSocket() has strongly-typed
        # values to pass to socket.create_connection(), avoiding the ambiguous
        # self.address type (tuple[str | None, int] | str) inherited from
        # SysLogHandler which also supports Unix socket path strings.
        self._tls_host: str = address[0]
        self._tls_port: int = address[1]
        # Pass socktype=SOCK_STREAM so the parent opens a TCP connection.
        super().__init__(address=address, socktype=__import__("socket").SOCK_STREAM)

    def makeSocket(self, timeout: float = 1) -> ssl.SSLSocket:  # type: ignore[override]
        """Return a TLS-wrapped TCP socket connected to the syslog server."""
        import socket as _socket

        # Use the strongly-typed (_tls_host, _tls_port) stored in __init__
        # instead of self.address whose type includes `str` (Unix path),
        # which is incompatible with socket.create_connection().
        plain_sock = _socket.create_connection(
            (self._tls_host, self._tls_port), timeout=timeout
        )
        return self._ssl_context.wrap_socket(plain_sock, server_hostname=self._tls_host)


def _build_syslog_handler(cfg: SyslogSettings) -> Optional[logging.Handler]:
    """
    Build a logging handler that forwards to a remote syslog server.

    Supports:
      - UDP  (RFC 3164 / 5424) — no auth, no TLS
      - TCP plain (RFC 5424 framing)
      - TCP + TLS (RFC 5425) via _TlsSysLogHandler

    RFC 6587 / RELP authentication credentials are stored in the config for
    future use; Python's stdlib SysLogHandler does not expose SASL natively.
    """
    import socket as _socket

    address = (cfg.host, cfg.port)

    if cfg.protocol == "udp":
        handler: logging.Handler = logging.handlers.SysLogHandler(
            address=address,
            socktype=_socket.SOCK_DGRAM,
        )
    elif cfg.protocol == "tcp":
        handler = logging.handlers.SysLogHandler(
            address=address,
            socktype=_socket.SOCK_STREAM,
        )
    elif cfg.protocol == "tcp+tls":
        # Build an SSL context for encrypted syslog (RFC 5425)
        ctx = ssl.create_default_context()
        if not cfg.tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if cfg.tls_ca_cert:
            ctx.load_verify_locations(cafile=cfg.tls_ca_cert)

        # Use the subclass that overrides makeSocket() — no attribute hacks
        handler = _TlsSysLogHandler(address=address, ssl_context=ctx)
    else:
        logger.warning("Unknown syslog protocol: %s", cfg.protocol)
        return None

    # Choose formatter based on RFC variant
    if cfg.rfc == "rfc5424":
        fmt = logging.Formatter(
            "1 %(asctime)s portalcrane %(process)d - - %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S+00:00",
        )
    else:
        # RFC 3164 BSD syslog format
        fmt = logging.Formatter(
            "%(asctime)s portalcrane[%(process)d]: %(message)s",
            datefmt="%b %d %H:%M:%S",
        )

    handler.setFormatter(fmt)
    return handler


def apply_syslog_config(cfg: SyslogSettings) -> None:
    """
    Attach or detach syslog handlers on the relevant loggers.

    Called on startup and whenever the admin saves new syslog settings.
    Idempotent: removes any previously installed handlers before adding new ones.
    """
    global _syslog_handlers

    # Remove previously installed handlers from all managed loggers
    for old_handler in _syslog_handlers:
        for log_name in ("portalcrane.audit", "uvicorn", "uvicorn.access"):
            logging.getLogger(log_name).removeHandler(old_handler)
    _syslog_handlers = []

    if not cfg.enabled or not cfg.host:
        return

    handler = _build_syslog_handler(cfg)
    if handler is None:
        return

    _syslog_handlers.append(handler)

    if cfg.forward_audit:
        logging.getLogger("portalcrane.audit").addHandler(handler)
        logger.info(
            "Syslog handler attached to portalcrane.audit → %s:%d",
            cfg.host,
            cfg.port,
        )

    if cfg.forward_uvicorn:
        for name in ("uvicorn", "uvicorn.access"):
            logging.getLogger(name).addHandler(handler)
        logger.info("Syslog handler attached to uvicorn → %s:%d", cfg.host, cfg.port)
