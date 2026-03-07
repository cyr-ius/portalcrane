"""
Portalcrane - Registry Reverse Proxy
Proxies all Docker Registry v2 API calls, enforces per-user access control,
and audit-logs every pull and push operation.
"""

import base64
import ipaddress
import json
import logging
import time

import httpx
from fastapi import APIRouter, Request, Response, status
from jose import JWTError, jwt

from ..config import ALGORITHM, PROXY_TIMEOUT, REGISTRY_URL, get_settings
from ..core.jwt import _is_admin_user
from ..core.security import verify_user
from ..routers.folders import check_folder_access
from ..services.audit_service import AuditService

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
audit = AuditService(settings)

_HOP_BY_HOP = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    ]
)

_PULL_METHODS = frozenset(["GET", "HEAD"])
_PUSH_METHODS = frozenset(["POST", "PUT", "PATCH", "DELETE"])
_OCI_ACCEPT_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
)


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _filter_headers(headers: dict) -> dict:
    """Remove HTTP hop-by-hop headers before forwarding."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _ensure_oci_accept_for_manifests(v2_path: str, method: str, headers: dict) -> None:
    """Ensure manifest requests advertise OCI media types to the upstream registry."""
    if method not in _PULL_METHODS or "/manifests/" not in v2_path:
        return
    accept_value = headers.get("accept")
    if not accept_value:
        headers["accept"] = ", ".join(_OCI_ACCEPT_TYPES)
        return
    missing = [m for m in _OCI_ACCEPT_TYPES if m not in accept_value.lower()]
    if missing:
        headers["accept"] = f"{accept_value}, {', '.join(missing)}"


async def _unauthorized_response(detail: str = "Authentication required") -> Response:
    await audit.log(subject="registry_authorize", status=status.HTTP_401_UNAUTHORIZED)
    return Response(
        content=json.dumps({"detail": detail}),
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
        headers={"WWW-Authenticate": "Basic realm=portalcrane-registry"},
    )


async def _forbidden_response(detail: str) -> Response:
    await audit.log(subject="registry_authorize", status=status.HTTP_403_FORBIDDEN)
    return Response(
        content=json.dumps({"detail": detail}),
        status_code=status.HTTP_403_FORBIDDEN,
        media_type="application/json",
    )


def _decode_basic_auth(auth_header: str) -> tuple[str, str] | None:
    """Decode a Basic Authorization header. Returns (username, password) or None."""
    if not auth_header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(auth_header.split(" ", 1)[1].strip()).decode("utf-8")
        username, password = raw.split(":", 1)
        return username, password
    except Exception:
        return None


def _decode_bearer_username(auth_header: str) -> str | None:
    """Extract the username (sub claim) from a Bearer JWT. Returns None on failure."""
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def _extract_image_path(v2_path: str) -> str:
    """Extract the image repository name from a v2 API path.

    Examples:
        "production/nginx/manifests/latest" → "production/nginx"
        "nginx/blobs/sha256:abc"            → "nginx"
    """
    for marker in ("/manifests/", "/blobs/", "/tags/", "/uploads/", "/uploads"):
        idx = v2_path.find(marker)
        if idx != -1:
            return v2_path[:idx]
    return v2_path


def _client_ip(request: Request) -> str:
    """Get ip address."""

    forwarded = request.headers.get("forwarded")
    if forwarded:
        for part in forwarded.split(","):
            for item in part.split(";"):
                key, sep, value = item.strip().partition("=")
                if sep and key.lower() == "for" and value:
                    candidate = (
                        value.strip().strip('"').removeprefix("[").removesuffix("]")
                    )
                    # IPv6 values can include a :port suffix in the Forwarded header.
                    if candidate.count(":") > 1 and "]:" in value:
                        candidate = candidate.rsplit(":", 1)[0]
                    elif candidate.count(":") == 1:
                        host, port = candidate.rsplit(":", 1)
                        if port.isdigit():
                            candidate = host
                    try:
                        return str(ipaddress.ip_address(candidate))
                    except ValueError:
                        continue

    # De-facto reverse-proxy header, ex: X-Forwarded-For: client, proxy1, proxy2
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        for ip in x_forwarded_for.split(","):
            candidate = ip.strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue

    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        candidate = x_real_ip.strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass

    return request.client.host if request.client else "unknown"


# ─── Authorization ────────────────────────────────────────────────────────────


async def _authorize_registry_proxy(
    request: Request, method: str, v2_path: str = ""
) -> Response | None:
    """
    Authorize an incoming registry request.

    Decision flow:
    1. Auth disabled globally → allow.
    2. No credentials → 401.
    3. Invalid credentials → 401.
    4. Admin user → allow.
    5. Folder check (includes __root__ fallback):
       - True  → allow.
       - False → 403.
       - None  → __root__ not configured → deny push, deny pull (fail-secure).

    Returns None when access is granted, a Response (401/403) to abort.
    """
    if not settings.registry_proxy_auth_enabled:
        return None

    auth_header = request.headers.get("authorization", "")
    if not auth_header:
        return await _unauthorized_response()

    username: str | None = None

    basic = _decode_basic_auth(auth_header)
    if basic is not None:
        user, pwd = basic
        audit.username = user
        if not verify_user(user, pwd, settings):
            return await _unauthorized_response("Invalid credentials")
        username = user
    else:
        username = _decode_bearer_username(auth_header)
        audit.username = username
        if not username:
            return await _unauthorized_response("Invalid bearer token")

    await audit.log(subject="registry_authorize", status=status.HTTP_200_OK)

    # Admins bypass all folder checks
    if _is_admin_user(username, settings):
        return None

    # Allow authenticated users to ping/login (v2_path == "")
    if not v2_path:
        return None

    is_pull = method in _PULL_METHODS
    image_path = _extract_image_path(v2_path)
    folder_result = check_folder_access(username, image_path, is_pull=is_pull)

    if folder_result is True:
        return None  # Explicitly granted by a folder rule

    if folder_result is False:
        action = "pull" if is_pull else "push"
        return await _forbidden_response(
            f"Folder access denied: {action} permission required"
        )

    # folder_result is None → __root__ not configured
    # Fail-secure: deny everything rather than accidentally opening access
    action = "pull" if is_pull else "push"
    return await _forbidden_response(
        f"Access denied: no folder rule applies for this image "
        f"(configure __root__ to grant {action} access)"
    )


# ─── Core proxy ───────────────────────────────────────────────────────────────


async def _proxy(request: Request, v2_path: str) -> Response:
    """Forward the request to the internal registry and audit-log the result."""
    upstream_url = f"{REGISTRY_URL.rstrip('/')}/v2/{v2_path}"
    query_string = request.url.query
    method = request.method

    audit.path = v2_path
    audit.client_ip = _client_ip(request)
    audit.method = method

    if query_string:
        upstream_url = f"{upstream_url}?{query_string}"

    authz_error = await _authorize_registry_proxy(request, method, v2_path)
    if authz_error is not None:
        return authz_error

    req_headers = _filter_headers(dict(request.headers))
    req_headers.pop("host", None)
    _ensure_oci_accept_for_manifests(
        v2_path=v2_path, method=method, headers=req_headers
    )

    body = await request.body()
    t0 = time.monotonic()

    try:
        async with httpx.AsyncClient(
            timeout=PROXY_TIMEOUT, follow_redirects=False
        ) as client:
            upstream = await client.request(
                method=method,
                url=upstream_url,
                headers=req_headers,
                content=body,
            )
    except httpx.ConnectError as exc:
        logger.error("Registry unreachable at %s: %s", upstream_url, exc)
        return Response(
            content=json.dumps({"detail": "Registry unreachable"}),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
    except httpx.TimeoutException as exc:
        logger.error("Registry request timed out: %s", exc)
        return Response(
            content=json.dumps({"detail": "Registry request timed out"}),
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            media_type="application/json",
        )

    elapsed = time.monotonic() - t0

    if method in _PULL_METHODS:
        await audit.log(
            subject="registry_pull",
            status=upstream.status_code,
            size=len(upstream.content),
            elapsed=elapsed,
        )
    elif method in _PUSH_METHODS:
        await audit.log(
            subject="registry_push",
            status=upstream.status_code,
            size=len(body),
            elapsed=elapsed,
        )

    resp_headers = _filter_headers(dict(upstream.headers))

    # Rewrite Location header so redirects point to the public host
    if "location" in resp_headers:
        loc = resp_headers["location"]
        public_base = str(request.base_url).rstrip("/")
        internal_base = REGISTRY_URL.rstrip("/")
        resp_headers["location"] = loc.replace(internal_base, public_base)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=resp_headers.get("content-type"),
    )


# ─── Routes ───────────────────────────────────────────────────────────────────


@router.api_route("/v2/", methods=["GET", "HEAD"], summary="Registry v2 ping")
async def registry_proxy_ping(request: Request) -> Response:
    """Proxied /v2/ ping — Docker clients call this to verify registry reachability."""
    return await _proxy(request, "")


@router.api_route(
    "/v2/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"],
    summary="Registry v2 proxy",
)
async def registry_proxy_v2(request: Request, path: str) -> Response:
    """Transparent proxy for all Docker Registry Distribution API v2 calls."""
    return await _proxy(request, path)
