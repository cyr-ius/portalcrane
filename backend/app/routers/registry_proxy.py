"""
Portalcrane - Registry Reverse Proxy
=====================================
Proxies all Docker Registry v2 API calls through Portalcrane's own HTTPS endpoint.

Why this exists
---------------
Docker requires the registry to be reachable over TLS (or explicitly marked as
insecure in /etc/docker/daemon.json). Rather than:
  - generating a self-signed certificate on the registry container, or
  - publishing port 5000 on the host unprotected,

we proxy every /registry-proxy/v2/* request through Portalcrane, which already
terminates TLS (via a reverse-proxy or native Uvicorn TLS).

Benefits
--------
- Port 5000 is never published on the host.
- A single certificate covers both the UI and the registry.
- Every pull AND push is logged -> full download/upload traceability.
- Per-user access control can be implemented based on the authenticated user.
"""

import base64
import json
import logging
import time

import httpx
from fastapi import APIRouter, Request, Response, status
from jose import JWTError, jwt

from ..config import ALGORITHM, PROXY_TIMEOUT, REGISTRY_URL, get_settings
from ..services.audit_service import AuditService
from .auth import _can_pull_images, _is_admin_user, _verify_user
from .folders import check_folder_access

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Registry Proxy"])
settings = get_settings()
audit = AuditService(settings)

# HTTP hop-by-hop headers that must not be forwarded
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


def _filter_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _ensure_oci_accept_for_manifests(v2_path: str, method: str, headers: dict) -> None:
    """Ensure manifest requests advertise OCI media types to upstream registry."""
    if method not in _PULL_METHODS or "/manifests/" not in v2_path:
        return

    accept_value = headers.get("accept")
    if not accept_value:
        headers["accept"] = ", ".join(_OCI_ACCEPT_TYPES)
        return

    lower_accept = accept_value.lower()
    missing = [media for media in _OCI_ACCEPT_TYPES if media not in lower_accept]
    if missing:
        headers["accept"] = f"{accept_value}, {', '.join(missing)}"


async def _unauthorized_response(detail: str = "Authentication required") -> Response:
    status_code = status.HTTP_401_UNAUTHORIZED
    await audit.log(subject="registry_authorize", status=status_code)
    return Response(
        content=json.dumps({"detail": detail}),
        status_code=status_code,
        media_type="application/json",
        headers={"WWW-Authenticate": "Basic realm=portalcrane-registry"},
    )


async def _forbidden_response(detail: str) -> Response:
    status_code = status.HTTP_403_FORBIDDEN
    await audit.log(subject="registry_authorize", status=status_code)
    return Response(
        content=json.dumps({"detail": detail}),
        status_code=status_code,
        media_type="application/json",
    )


def _decode_basic_auth(auth_header: str) -> tuple[str, str] | None:
    if not auth_header.lower().startswith("basic "):
        return None
    encoded = auth_header.split(" ", 1)[1].strip()
    try:
        raw = base64.b64decode(encoded).decode("utf-8")
        username, password = raw.split(":", 1)
        return username, password
    except Exception:
        return None


def _decode_bearer_username(auth_header: str) -> str | None:
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    return payload.get("sub")


def _extract_image_path(v2_path: str) -> str:
    """
    Extract the image name (without tag/digest/endpoint suffix) from a v2 path.

    Examples:
        "production/nginx/manifests/latest" → "production/nginx"
        "nginx/blobs/sha256:abc"            → "nginx"
        ""                                  → ""
    """
    # Strip known terminal segments: manifests, blobs, tags, uploads
    for marker in ("/manifests/", "/blobs/", "/tags/", "/uploads/", "/uploads"):
        idx = v2_path.find(marker)
        if idx != -1:
            return v2_path[:idx]
    return v2_path


async def _authorize_registry_proxy(
    request: Request, method: str, v2_path: str = ""
) -> Response | None:
    if not settings.registry_proxy_auth_enabled:
        return None

    auth_header = request.headers.get("authorization", "")
    if not auth_header:
        return await _unauthorized_response()

    username: str | None = None

    basic = _decode_basic_auth(auth_header)
    if basic is not None:
        user, pwd = basic
        if not _verify_user(user, pwd, settings):
            return await _unauthorized_response("Invalid credentials")
        username = user
    else:
        username = _decode_bearer_username(auth_header)
        if not username:
            return await _unauthorized_response("Invalid bearer token")

    audit.username = username

    if _is_admin_user(username, settings):
        return None

    is_pull = method in _PULL_METHODS
    is_push = method in _PUSH_METHODS

    image_path = _extract_image_path(v2_path)
    folder_result = check_folder_access(username, image_path, is_pull=is_pull)

    if folder_result is not None:
        if not folder_result:
            action = "pull" if is_pull else "push"
            return await _forbidden_response(
                f"Folder access denied: {action} permission required"
            )
        return None

    if is_push:
        return await _forbidden_response(
            "Push to root namespace is restricted to administrators"
        )

    if is_pull and not _can_pull_images(username, settings):
        return await _forbidden_response("Pull permission required")

    await audit.log(subject="registry_authorize")

    return None


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _proxy(request: Request, v2_path: str) -> Response:
    """Forward request to the internal registry, audit-log the result, return response."""

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
            timeout=PROXY_TIMEOUT,
            follow_redirects=False,
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

    if "location" in resp_headers:
        loc = resp_headers["location"]
        public_base = str(request.base_url).rstrip("/")
        internal_base = REGISTRY_URL.rstrip("/")
        rewritten = loc.replace(internal_base, public_base)
        resp_headers["location"] = rewritten
        logger.debug("Rewrote Location: %s → %s", loc, rewritten)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=resp_headers.get("content-type"),
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.api_route(
    "/v2/",
    methods=["GET", "HEAD"],
    summary="Registry v2 ping",
)
async def registry_proxy_ping(request: Request) -> Response:
    """Proxied /v2/ ping — Docker clients call this to verify registry reachability."""
    return await _proxy(request, "")


@router.api_route(
    "/v2/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"],
    summary="Registry v2 proxy",
)
async def registry_proxy_v2(request: Request, path: str) -> Response:
    """
    Transparent proxy for all Docker Registry Distribution API v2 calls.
    Covers manifests, blobs, tags, and upload sessions.
    Every GET/HEAD (pull) and PUT/POST/PATCH/DELETE (push/delete) is audit-logged.
    """
    return await _proxy(request, path)
