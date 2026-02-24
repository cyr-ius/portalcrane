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

import httpx
from fastapi import APIRouter, Request, Response

from ..config import get_settings
from ..services.audit_service import AuditService

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


def _filter_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _proxy(request: Request, v2_path: str) -> Response:
    """Forward request to the internal registry, audit-log the result, return response."""
    settings = get_settings()
    audit = AuditService(settings)

    upstream_url = f"{settings.registry_url.rstrip('/')}/v2/{v2_path}"
    method = request.method
    req_headers = _filter_headers(dict(request.headers))
    body = await request.body()

    auth = None
    if settings.registry_username and settings.registry_password:
        auth = (settings.registry_username, settings.registry_password)

    t0 = time.monotonic()

    try:
        async with httpx.AsyncClient(
            auth=auth, timeout=settings.proxy_timeout, follow_redirects=True
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
            status_code=503,
            media_type="application/json",
        )
    except httpx.TimeoutException as exc:
        logger.error("Registry request timed out: %s", exc)
        return Response(
            content=json.dumps({"detail": "Registry request timed out"}),
            status_code=504,
            media_type="application/json",
        )

    elapsed = time.monotonic() - t0
    ip = _client_ip(request)

    # Audit logging
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

    # Rewrite Location headers so clients follow the proxy, not the internal host
    resp_headers = _filter_headers(dict(upstream.headers))
    if "location" in resp_headers:
        resp_headers["location"] = resp_headers["location"].replace(
            settings.registry_url.rstrip("/"), "/registry-proxy"
        )

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
