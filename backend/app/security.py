import ipaddress
import logging
import time
from collections import defaultdict, deque
from functools import lru_cache
from threading import Lock
from time import perf_counter

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import app_settings
from .services.audit_service import log_web_ui_action

logger = logging.getLogger(__name__)

_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=16)
def _trusted_networks(raw: str) -> tuple[_IPNetwork, ...]:
    """Parse the comma-separated ``trusted_proxies`` setting into networks.

    Cached on the raw string: the value is stable for the process lifetime, so
    parsing runs once. Invalid entries are logged and skipped rather than
    aborting startup.
    """
    networks: list[_IPNetwork] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid trusted_proxies entry: %r", entry)
    return tuple(networks)


def _is_trusted(ip: str, networks: tuple[_IPNetwork, ...]) -> bool:
    """Return True when ``ip`` falls inside any trusted-proxy network."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _forwarded_chain(value: str) -> list[str]:
    """Extract ordered client→proxy IPs from an RFC 7239 ``Forwarded`` header.

    Handles the ``for=`` node forms allowed by the RFC: bare IPv4, quoted
    ``IPv4:port``, and bracketed ``[IPv6]`` with an optional ``:port``. Obfuscated
    identifiers (``for=unknown``, ``for=_hidden``) fail IP validation and are
    skipped.
    """
    chain: list[str] = []
    for part in value.split(","):
        for item in part.split(";"):
            key, sep, val = item.strip().partition("=")
            if not (sep and key.lower() == "for" and val):
                continue
            candidate = val.strip().strip('"')
            if candidate.startswith("["):
                # Bracketed IPv6, optional ":port" after the closing bracket.
                end = candidate.find("]")
                if end != -1:
                    candidate = candidate[1:end]
            elif candidate.count(":") == 1:
                # IPv4:port — an unbracketed IPv6 has >1 colon and no port.
                host, port = candidate.rsplit(":", 1)
                if port.isdigit():
                    candidate = host
            try:
                chain.append(str(ipaddress.ip_address(candidate)))
            except ValueError:
                continue
    return chain


def _comma_chain(value: str) -> list[str]:
    """Extract ordered IPs from a comma-separated header (X-Forwarded-For)."""
    chain: list[str] = []
    for ip in value.split(","):
        candidate = ip.strip()
        try:
            chain.append(str(ipaddress.ip_address(candidate)))
        except ValueError:
            continue
    return chain


def client_ip(request: Request) -> str:
    """Resolve the real client IP, honouring proxy headers only when trusted.

    Forwarding headers (``Forwarded`` / ``X-Forwarded-For`` / ``X-Real-IP``) are
    attacker-controlled and are consulted ONLY when the direct TCP peer is a
    configured trusted proxy (see ``Settings.trusted_proxies`` / the
    ``TRUSTED_PROXIES`` Docker env var). This keeps the per-IP rate limiter and
    audit log from being trivially bypassed by a spoofed header. When no proxy
    is trusted (default) the raw peer address is returned unchanged.
    """
    peer = request.client.host if request.client else "unknown"
    networks = _trusted_networks(app_settings.trusted_proxies)
    if not networks or not _is_trusted(peer, networks):
        return peer

    forwarded = request.headers.get("forwarded")
    x_forwarded_for = request.headers.get("x-forwarded-for")
    x_real_ip = request.headers.get("x-real-ip")
    if forwarded:
        chain = _forwarded_chain(forwarded)
    elif x_forwarded_for:
        chain = _comma_chain(x_forwarded_for)
    elif x_real_ip:
        chain = _comma_chain(x_real_ip)
    else:
        chain = []

    # Walk the chain right-to-left (closest proxy first) and return the first
    # address that is NOT itself a trusted proxy — that is the real client.
    for candidate in reversed(chain):
        if not _is_trusted(candidate, networks):
            return candidate

    # Whole chain is trusted proxies (or empty): best-effort leftmost, else peer.
    return chain[0] if chain else peer


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response."""

    # Build CSP once at class level — one directive per list entry, auditable.
    _CSP_DIRECTIVES: list[str] = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",  # Angular + Swagger UI (self-hosted)
        "style-src 'self' 'unsafe-inline'",  # Bootstrap + Swagger UI (self-hosted)
        "img-src 'self' data: https:",  # logos, QR codes base64
        "font-src 'self' data:",  # Bootstrap Icons embedded font
        f"connect-src 'self' {app_settings.oidc_issuer}",  # API calls + Azure endpoints
        "worker-src 'self'",  # Angular Service Worker (PWA)
        "frame-ancestors 'none'",  # replaces X-Frame-Options
    ]
    _CSP: str = "; ".join(_CSP_DIRECTIVES) + ";"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = self._CSP
        return response


class _SlidingWindowLimiter:
    """Fixed-window-free sliding rate limiter backed by per-key timestamp logs.

    Each key maps to a deque of request timestamps (monotonic clock). On every
    check, timestamps older than the window are evicted, and the request is
    allowed only when the remaining count is below the limit. A periodic sweep
    drops empty buckets so memory stays bounded under churn of distinct IPs.

    A plain ``threading.Lock`` guards the shared state: Starlette runs the
    middleware on the event loop, but the check is synchronous (no ``await``
    inside the critical section), so contention is negligible and correctness
    holds even if the app is ever served with a threaded worker.
    """

    # Sweep empty buckets at most once per this many seconds.
    _CLEANUP_INTERVAL_S: float = 300.0

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._last_cleanup = 0.0

    def check(self, key: str, max_requests: int, window_s: int) -> tuple[bool, int]:
        """Register a hit for ``key`` and report whether it is allowed.

        Returns ``(allowed, retry_after_seconds)``. When the request is denied,
        ``retry_after_seconds`` is the whole number of seconds until the oldest
        recorded hit leaves the window; otherwise it is ``0``.
        """
        now = time.monotonic()
        boundary = now - window_s
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= boundary:
                hits.popleft()

            if len(hits) >= max_requests:
                retry_after = int(hits[0] + window_s - now) + 1
                return False, max(retry_after, 1)

            hits.append(now)
            self._sweep(now, window_s)
            return True, 0

    def _sweep(self, now: float, window_s: int) -> None:
        """Drop buckets whose newest hit has fully left the window.

        Called under the lock. Runs at most once per ``_CLEANUP_INTERVAL_S`` to
        keep the amortised cost of the scan out of the hot path.
        """
        if now - self._last_cleanup < self._CLEANUP_INTERVAL_S:
            return
        self._last_cleanup = now
        boundary = now - window_s
        stale = [
            key for key, hits in self._hits.items() if not hits or hits[-1] <= boundary
        ]
        for key in stale:
            del self._hits[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Throttle requests per client IP with an in-memory sliding window.

    Only ``/api/*`` routes are limited (static SPA assets are served
    elsewhere). The login endpoint (RATE_LIMIT_LOGIN_PATH) gets its own bucket,
    with a stricter budget over its own window, so a password brute-force is
    throttled without starving the general API budget. The health probe is
    exempt so container orchestration is never blocked.
    """

    _EXEMPT_PATHS: frozenset[str] = frozenset({"/api/health"})

    def __init__(self, app) -> None:
        super().__init__(app)
        self._limiter = _SlidingWindowLimiter()

    async def dispatch(self, request: Request, call_next):
        settings = app_settings
        path = request.url.path
        if (
            not settings.rate_limit_enabled
            or not path.startswith("/api/")
            or path in self._EXEMPT_PATHS
        ):
            return await call_next(request)

        if path == settings.rate_limit_login_path:
            bucket = "auth"
            limit = settings.rate_limit_login_max_attempts
            window = settings.rate_limit_login_window_seconds
        else:
            bucket = "global"
            limit = settings.rate_limit_max_requests
            window = settings.rate_limit_window_seconds

        ip = client_ip(request)
        allowed, retry_after = self._limiter.check(f"{bucket}:{ip}", limit, window)
        if not allowed:
            logger.warning(
                "Rate limit exceeded: ip=%s path=%s bucket=%s limit=%d/%ds",
                ip,
                path,
                bucket,
                limit,
                window,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)


class AuditMiddleware(BaseHTTPMiddleware):
    """Log every request to the audit log with a bounded ring buffer.

    The audit log is a fixed-size in-memory ring buffer of the most recent
    requests. It is not persisted, so it is only useful for short-term
    investigation of recent activity. The audit log is not a security control:
    it does not prevent abuse, and it can be trivially bypassed by an attacker
    who can flood the buffer with noise.
    """

    async def dispatch(self, request: Request, call_next):
        start = perf_counter()
        response = await call_next(request)
        elapsed = perf_counter() - start

        await log_web_ui_action(
            request=request,
            status_code=response.status_code,
            settings=app_settings,
            elapsed_s=elapsed,
        )
        return response
