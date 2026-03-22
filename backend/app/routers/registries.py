"""Portalcrane - Registries Router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..core.jwt import (
    UserInfo,
    get_current_user,
    require_pull_access,
    require_push_access,
)
from ..services.registries_service import (
    check_catalog_browsable,
    create_registry,
    delete_registry,
    get_registries,
    get_registry_by_id,
    ping_catalog,
    test,
    update_registry,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Pydantic models ───────────────────────────────────────────────────────────


class CreateRegistryRequest(BaseModel):
    """Payload to create a new external registry entry."""

    name: str
    host: str
    username: str = ""
    password: str = ""
    owner: str | None = None
    use_tls: bool = True
    tls_verify: bool = True


class UpdateRegistryRequest(BaseModel):
    """Payload to update an external registry entry (all fields optional)."""

    name: str | None = None
    host: str | None = None
    username: str | None = None
    password: str | None = None
    owner: str | None = None
    use_tls: bool | None = None
    tls_verify: bool | None = None


class TestConnectionRequest(BaseModel):
    """Payload to test connectivity to a registry without saving it."""

    host: str
    username: str = ""
    password: str = ""
    use_tls: bool = True
    tls_verify: bool = True


@router.get("")
async def list_registries(
    current_user: UserInfo = Depends(get_current_user),
):
    """
    List external registries visible to the current user.
    Admins see all; regular users see global + their own.
    Each entry includes a browsable field indicating /v2/_catalog support.
    """
    owner = None if current_user.is_admin else current_user.username
    return get_registries(owner=owner)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_registry_endpoint(
    request: CreateRegistryRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Create a new external registry entry.

    Admins may create global registries (owner="global").
    Non-admin users may create personal registries only.

    The browsable field is set automatically by probing /v2/_catalog so the
    frontend can immediately filter non-browsable registries from source selectors.
    """
    if request.owner == "global" and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create global registries",
        )
    owner = request.owner if request.owner else current_user.username

    created = await create_registry(
        name=request.name,
        host=request.host,
        username=request.username,
        password=request.password,
        owner=owner,
        use_tls=request.use_tls,
        tls_verify=request.tls_verify,
    )

    if created.get("reachable") is False:
        raise HTTPException(status_code=400, detail="Registry unreachable")
    if created.get("auth_ok") is False:
        raise HTTPException(status_code=403, detail="Authentication error")


@router.patch("/{registry_id}")
async def update_registry_endpoint(
    registry_id: str,
    request: UpdateRegistryRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Update an external registry entry. Owner or admin only.

    Owner resolution follows the same rule as creation:
      - request.owner == "global"  → stored as "global" (admin only)
      - request.owner == anything else (including "personal" sent by the UI)
        → stored as current_user.username

    This ensures that switching global → personal always persists the correct
    username, not the literal string "personal".

    browsable is re-evaluated whenever any connectivity-related field changes
    (host, username, password, use_tls, tls_verify).
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    # Permission check: must be owner or admin
    if not current_user.is_admin and registry.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this registry",
        )

    # Only admins may promote a registry to global
    if request.owner == "global" and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can make a registry global",
        )

    # Resolve the requested owner value:
    #   "global"        → keep "global"
    #   "personal" / *  → resolve to the current user's username
    #   None            → no ownership change (pass None to update_registry)
    resolved_owner: str | None
    if request.owner is None:
        resolved_owner = None  # no change requested
    elif request.owner == "global":
        resolved_owner = "global"
    else:
        # Any non-global value (e.g. "personal") means "assign to current user"
        resolved_owner = current_user.username

    updated = await update_registry(
        registry_id=registry_id,
        name=request.name,
        host=request.host,
        username=request.username,
        password=request.password,
        owner=resolved_owner,
        use_tls=request.use_tls,
        tls_verify=request.tls_verify,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Registry not found")
    if updated.get("reachable") is False:
        raise HTTPException(status_code=400, detail="Registry unreachable")
    if updated.get("auth_ok") is False:
        raise HTTPException(status_code=403, detail="Authentication error")
    return updated


@router.delete("/{registry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_endpoint(
    registry_id: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """Delete a registry entry. Owner or admin only."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    if not current_user.is_admin and registry.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this registry",
        )

    if not delete_registry(registry_id):
        raise HTTPException(status_code=404, detail="Registry not found")


@router.post("/test")
async def test_connection(
    request: TestConnectionRequest,
    _: UserInfo = Depends(get_current_user),
):
    """Test connectivity to a registry using ad-hoc credentials."""
    return await test(
        host=request.host,
        username=request.username,
        password=request.password,
        use_tls=request.use_tls,
        tls_verify=request.tls_verify,
    )


@router.post("/{registry_id}/test")
async def test_saved_connection(
    registry_id: str,
    _: UserInfo = Depends(get_current_user),
):
    """Test connectivity to a saved registry using stored credentials."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    checks = await test(
        host=registry["host"],
        username=registry.get("username", ""),
        password=registry.get("password", ""),
        use_tls=registry.get("use_tls", True),
        tls_verify=registry.get("tls_verify", True),
    )

    if checks.get("reachable") is False:
        raise HTTPException(status_code=400, detail="Registry unreachable")

    if checks.get("auth_ok") is False:
        raise HTTPException(status_code=403, detail="Authentication error")


@router.get("/{registry_id}/catalog-check")
async def catalog_check(
    registry_id: str,
    _: UserInfo = Depends(require_pull_access),
):
    """Check catalog for a specific repository in an external registry."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    check = await check_catalog_browsable(
        host=registry["host"],
        username=registry.get("username", ""),
        password=registry.get("password", ""),
        use_tls=registry.get("use_tls", True),
        tls_verify=registry.get("tls_verify", True),
    )

    return {"available": check, "reason": ""}


@router.get("/{registry_id}/ping")
async def ping(
    registry_id: str,
    _: UserInfo = Depends(require_push_access),
):
    """Check local registry connectivity."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    is_up = await ping_catalog(registry_id=registry_id)
    return {
        "status": "ok" if is_up else "unreachable",
        "name": registry.get("name", ""),
    }
