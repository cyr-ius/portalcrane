"""
Portalcrane - Network Settings Router
======================================
Exposes REST endpoints for managing proxy and syslog overrides.

GET    /api/network/config        → return effective network config  (admin only)
PUT    /api/network/proxy         → save proxy override              (admin only)
DELETE /api/network/proxy         → clear proxy override             (admin only)
PUT    /api/network/syslog        → save syslog config               (admin only)
DELETE /api/network/syslog        → disable syslog forwarding        (admin only)
POST   /api/network/syslog/test   → send a test syslog message       (admin only)
"""

import logging

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..core.jwt import require_admin, UserInfo

from ..services.proxy_service import (
    NetworkConfig,
    ProxySettings,
    SyslogSettings,
    apply_proxy_to_os_environ,
    apply_syslog_config,
    load_proxy_config,
    resolve_network_config,
    save_proxy_config,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET effective config ──────────────────────────────────────────────────────


@router.get("/config", response_model=NetworkConfig)
async def get_network_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> NetworkConfig:
    """
    Return the effective network configuration.

    Priority: persisted admin override > environment variables.
    """
    return resolve_network_config(settings)


# ── Proxy overrides ───────────────────────────────────────────────────────────


@router.put("/proxy", response_model=NetworkConfig)
async def save_proxy_override(
    payload: ProxySettings,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> NetworkConfig:
    """
    Persist proxy override values and apply them immediately to os.environ.

    Skopeo and httpx subprocesses spawned after this call will inherit the
    updated environment variables without requiring a container restart.

    When proxy_override is False, the persisted proxy section is cleared and
    the managed env vars are removed from os.environ.
    """
    persisted = load_proxy_config()

    if not payload.proxy_override:
        # Reset: clear persisted proxy section
        persisted.pop("proxy", None)
    else:
        persisted["proxy"] = payload.model_dump()

    save_proxy_config(persisted)

    # Write (or clear) the proxy values in the running process environment
    apply_proxy_to_os_environ(payload)

    return resolve_network_config(settings)


@router.delete("/proxy", response_model=NetworkConfig)
async def reset_proxy_override(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> NetworkConfig:
    """
    Remove the persisted proxy override and clear managed env vars.

    The container's original env vars were already loaded by pydantic-settings
    at startup; this endpoint reverts the process to relying on those values.
    """
    persisted = load_proxy_config()
    persisted.pop("proxy", None)
    save_proxy_config(persisted)

    # Clear our managed env vars so the process stops using the override
    apply_proxy_to_os_environ(ProxySettings(proxy_override=False))

    return resolve_network_config(settings)


# ── Syslog configuration ──────────────────────────────────────────────────────


@router.put("/syslog", response_model=NetworkConfig)
async def save_syslog_config(
    payload: SyslogSettings,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> NetworkConfig:
    """
    Persist and immediately apply syslog forwarding configuration.

    Python logging handlers are re-attached in-process without restart.
    """
    persisted = load_proxy_config()
    persisted["syslog"] = payload.model_dump()
    save_proxy_config(persisted)

    # Apply immediately to the running loggers
    apply_syslog_config(payload)

    logger.info(
        "Syslog config updated: enabled=%s host=%s:%d protocol=%s",
        payload.enabled,
        payload.host,
        payload.port,
        payload.protocol,
    )
    return resolve_network_config(settings)


@router.delete("/syslog", response_model=NetworkConfig)
async def disable_syslog(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
) -> NetworkConfig:
    """Disable syslog forwarding and detach handlers from all managed loggers."""
    persisted = load_proxy_config()
    syslog_data = persisted.get("syslog", {})
    syslog_data["enabled"] = False
    persisted["syslog"] = syslog_data
    save_proxy_config(persisted)

    apply_syslog_config(SyslogSettings(enabled=False))
    return resolve_network_config(settings)


# ── Test endpoint ─────────────────────────────────────────────────────────────


@router.post("/syslog/test")
async def test_syslog(
    _: UserInfo = Depends(require_admin),
) -> dict:
    """
    Emit a test log message through the active syslog handler.

    Returns success/failure so the frontend can confirm connectivity.
    """
    audit_logger = logging.getLogger("portalcrane.audit")
    try:
        audit_logger.info(
            '{"event": "syslog_test", "message": "Portalcrane syslog connectivity test"}'
        )
        return {"success": True, "message": "Test message sent to syslog"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
