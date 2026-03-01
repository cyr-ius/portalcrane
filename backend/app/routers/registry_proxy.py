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

Docker daemon configuration
---------------------------
Use the Portalcrane address as the registry:

  docker tag  my-image <portalcrane-host>:8080/registry-proxy/v2/my-image:tag
  docker push <portalcrane-host>:8080/registry-proxy/v2/my-image:tag
  docker pull <portalcrane-host>:8080/registry-proxy/v2/my-image:tag

Set REGISTRY_PUSH_HOST=<portalcrane-host>:8080/registry-proxy so the staging
pipeline uses the same address automatically.
"""

import json
import logging
import time
import base64

import httpx
from fastapi import APIRouter, Request, Response, status
from jose import JWTError, jwt

from ..config import get_settings, ALGORITHM, REGISTRY_URL, PROXY_TIMEOUT
from ..services.audit_service import AuditService
from .auth import _can_pull_images, _can_push_images, _verify_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Registry Proxy"])

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


def _unauthorized_response(detail: str = "Authentication required") -> Response:
    return Response(
        content=json.dumps({"detail": detail}),
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
        headers={"WWW-Authenticate": "Basic realm=portalcrane-registry"},
    )


def _forbidden_response(detail: str) -> Response:
    return Response(
        content=json.dumps({"detail": detail}),
        status_code=status.HTTP_403_FORBIDDEN,
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


def _authorize_registry_proxy(request: Request, method: str) -> Response | None:
    settings = get_settings()
    if not settings.registry_proxy_auth_enabled:
        return None

    auth_header = request.headers.get("authorization", "")
    if not auth_header:
        return _unauthorized_response()

    username: str | None = None

    basic = _decode_basic_auth(auth_header)
    if basic is not None:
        user, pwd = basic
        if not _verify_user(user, pwd, settings):
            return _unauthorized_response("Invalid credentials")
        username = user
    else:
        username = _decode_bearer_username(auth_header)
        if not username:
            return _unauthorized_response("Invalid bearer token")

    if method in _PULL_METHODS and not _can_pull_images(username, settings):
        return _forbidden_response("Pull permission required")

    if method in _PUSH_METHODS and not _can_push_images(username, settings):
        return _forbidden_response("Push permission required")

    return None


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _proxy(request: Request, v2_path: str) -> Response:
    """Forward request to the internal registry, audit-log the result, return response."""
    settings = get_settings()
    audit = AuditService(settings)

    upstream_url = f"{REGISTRY_URL.rstrip('/')}/v2/{v2_path}"
    method = request.method

    authz_error = _authorize_registry_proxy(request, method)
    if authz_error is not None:
        return authz_error

    # Filter hop-by-hop headers AND remove the original Host header.
    # The Host header MUST match the registry's own address for _state JWT
    # validation to succeed on blob upload sessions (PATCH after POST).
    req_headers = _filter_headers(dict(request.headers))
    req_headers.pop("host", None)  # httpx will set the correct Host automatically
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
            content=json.dumps({"detail": "Registry unreachable", "error": str(exc)}),
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
    ip = _client_ip(request)

    if method in _PULL_METHODS:
        await audit.log_pull(
            path=v2_path,
            method=method,
            status=upstream.status_code,
            size=len(upstream.content),
            elapsed=elapsed,
            client_ip=ip,
        )
    elif method in _PUSH_METHODS:
        await audit.log_push(
            path=v2_path,
            method=method,
            status=upstream.status_code,
            size=len(body),
            elapsed=elapsed,
            client_ip=ip,
        )

    resp_headers = _filter_headers(dict(upstream.headers))

    # Rewrite Location: replace internal registry URL with the proxy prefix
    # so Docker follows the correct path on the next request.
    if "location" in resp_headers:
        loc = resp_headers["location"]
        resp_headers["location"] = loc
        logger.debug("Rewrote Location: %s", loc)

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
