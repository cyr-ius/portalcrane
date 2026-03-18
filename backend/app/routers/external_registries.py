"""
Portalcrane - External Registries Router
CRUD for external registries + synchronisation endpoints.

Changes:
  - tls_verify field added to create/update/test payloads.
  - create_registry and update_registry are now async (they probe
    /v2/_catalog to populate the browsable field). Router endpoints await them.
  - [NEW] GET  /registries/{id}/browse
  - [NEW] GET  /registries/{id}/browse/tags
  - [NEW] POST /import  (external -> local, Évolution 2)
  - SyncJob response model exposes direction field (Évolution 2)
"""

import logging
import shutil

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..config import Settings, get_settings, REGISTRY_URL
from ..services.job_service import jobs_list, normalize_sync_job
from ..services.external_registry import (
    browse_external_images,
    browse_external_tags,
    delete_external_image,
    check_catalog_browsable,
    create_registry,
    delete_registry,
    get_registries,
    get_registry_by_id,
    list_sync_jobs,
    run_import_job,
    run_export_job,
    skopeo_copy_oci_image,
    test_registry_connection,
    update_registry,
    validate_folder_path,
    get_external_tag_detail,
    delete_external_tag,
    add_external_tag,
)
from ..services.providers import build_target_path, resolve_provider_from_registry
from ..services.job_service import safe_job_path
from ..core.jwt import (
    UserInfo,
    get_current_user,
    require_push_access,
    require_pull_access,
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


class ExternalPushRequest(BaseModel):
    """Push a staged OCI layout to an external registry."""

    job_id: str
    registry_id: str | None = None
    registry_host: str | None = None
    registry_username: str | None = None
    registry_password: str | None = None
    folder: str | None = None
    image_name: str | None = None
    tag: str | None = None


class SyncRequest(BaseModel):
    """Payload to trigger a registry synchronisation job (local -> external)."""

    source_image: str = "(all)"
    dest_registry_id: str
    dest_folder: str | None = None


class ImportRequest(BaseModel):
    """Payload to trigger an import job (external -> local)."""

    source_registry_id: str
    source_image: str = "(all)"
    dest_folder: str | None = None


class AddExternalTagRequest(BaseModel):
    source_tag: str
    new_tag: str


# ── Registry CRUD ─────────────────────────────────────────────────────────────


@router.get("/registries")
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


@router.post("/registries", status_code=status.HTTP_201_CREATED)
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


@router.patch("/registries/{registry_id}")
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


@router.delete("/registries/{registry_id}", status_code=status.HTTP_204_NO_CONTENT)
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


# ── Connectivity test ─────────────────────────────────────────────────────────


@router.post("/registries/test")
async def test_connection(
    request: TestConnectionRequest,
    _: UserInfo = Depends(get_current_user),
):
    """Test connectivity to a registry using ad-hoc credentials."""
    return await test_registry_connection(
        host=request.host,
        username=request.username,
        password=request.password,
        use_tls=request.use_tls,
        tls_verify=request.tls_verify,
    )


@router.post("/registries/{registry_id}/test")
async def test_saved_connection(
    registry_id: str,
    _: UserInfo = Depends(get_current_user),
):
    """Test connectivity to a saved registry using stored credentials."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")
    checks = await test_registry_connection(
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


# ── Browse external registry  ─────────────────────────────────────────────────


@router.get("/registries/{registry_id}/browse")
async def browse_registry_images(
    registry_id: str,
    search: str | None = Query(None, description="Filter repositories by name"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=200),
    _: UserInfo = Depends(require_pull_access),
):
    """
    List repositories available in an external registry via /v2/_catalog.
    Results are paginated and optionally filtered by search keyword.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    result = await browse_external_images(
        registry_id=registry_id,
        search=search,
        page=page,
        page_size=page_size,
    )
    return result


@router.get("/registries/{registry_id}/browse/tags")
async def browse_registry_tags(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    _: UserInfo = Depends(require_pull_access),
):
    """List tags for a specific repository in an external registry."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    return await browse_external_tags(registry_id=registry_id, repository=repository)


@router.delete("/registries/{registry_id}/browse/image")
async def delete_registry_image(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    _: UserInfo = Depends(require_push_access),
):
    """Delete all tags of a repository in an external registry."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    result = await delete_external_image(registry_id=registry_id, repository=repository)
    if result.get("failed_tags") and not result.get("deleted_tags"):
        raise HTTPException(
            status_code=502, detail=result.get("message", "Delete failed")
        )
    return result


@router.get("/registries/{registry_id}/catalog-check")
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


@router.get("/registries/{registry_id}/browse/tags/detail")
async def browse_registry_tag_detail(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    tag: str = Query(..., description="Tag name, e.g. latest"),
    _: UserInfo = Depends(require_pull_access),
):
    """Return detailed metadata for a specific tag in an external V2 registry.

    Only available for standard V2 registries (not Docker Hub, not GHCR).
    Returns HTTP 404 when the registry or tag is not found.
    Returns HTTP 422 when the registry type does not support this operation.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    detail = await get_external_tag_detail(
        registry_id=registry_id, repository=repository, tag=tag
    )
    if not detail:
        raise HTTPException(
            status_code=404, detail="Tag not found or registry type unsupported"
        )
    return detail


@router.delete("/registries/{registry_id}/browse/tags")
async def delete_registry_tag(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    tag: str = Query(..., description="Tag name to delete"),
    _: UserInfo = Depends(require_push_access),
):
    """Delete a single tag from an external V2 registry.

    The registry must have manifest delete enabled.
    Returns HTTP 400 when the operation fails on the remote registry.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    result = await delete_external_tag(
        registry_id=registry_id, repository=repository, tag=tag
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("message", "Delete tag failed")
        )
    return result


@router.post("/registries/{registry_id}/browse/tags")
async def add_registry_tag(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    request: AddExternalTagRequest = Body(...),
    _: UserInfo = Depends(require_push_access),
):
    """Create a new tag by copying a manifest in an external V2 registry.

    The source tag manifest is fetched and PUT under the new tag name.
    No data transfer occurs; only the manifest reference is created.
    Returns HTTP 400 when the operation fails on the remote registry.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    result = await add_external_tag(
        registry_id=registry_id,
        repository=repository,
        source_tag=request.source_tag,
        new_tag=request.new_tag,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("message", "Add tag failed")
        )
    return result


# ── Push to external registry ─────────────────────────────────────────────────


@router.post("/push")
async def push_to_external(
    request: ExternalPushRequest,
    current_user: UserInfo = Depends(require_push_access),
    settings: Settings = Depends(get_settings),
):
    """Push a staged OCI layout to an external registry."""
    if request.job_id not in jobs_list:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs_list[request.job_id]
    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        oci_dir = safe_job_path(request.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if request.registry_id:
        registry = get_registry_by_id(request.registry_id)
        if not registry:
            raise HTTPException(status_code=404, detail="Saved registry not found")
        provider = resolve_provider_from_registry(registry)
        host = provider.host
        username = provider.username
        password = provider.password
        effective_tls_verify = provider.verify
    else:
        host = request.registry_host or ""
        username = request.registry_username or ""
        password = request.registry_password or ""
        effective_tls_verify = True

    image_name = request.image_name or job.get("image", "")
    tag = request.tag or job.get("tag", "latest")
    folder = request.folder or ""
    dest_ref = build_target_path(folder or None, image_name, tag, host)

    ok, message = await skopeo_copy_oci_image(
        oci_dir=str(oci_dir),
        dest_ref=dest_ref,
        dest_username=username,
        dest_password=password,
        settings=settings,
        tls_verify=effective_tls_verify,
    )
    return {"success": ok, "message": message, "dest_ref": dest_ref}


# ── List Sync Jobs ──────────────────────────────────────────────────


@router.get("/sync/jobs")
async def list_sync_jobs_endpoint(
    _: UserInfo = Depends(require_pull_access),
):
    """List all sync/import jobs sorted by start time descending."""
    return [normalize_sync_job(j) for j in list_sync_jobs()]


# ── Export (local -> external) ────────────────────────────────────────────────


@router.post("/export")
async def start_sync(
    request: SyncRequest,
    _: UserInfo = Depends(require_push_access),
    settings: Settings = Depends(get_settings),
):
    """
    Trigger a sync job (local -> external). Returns job_id immediately.
    """
    try:
        folder = validate_folder_path(request.dest_folder or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        job_id = await run_export_job(
            source_image=request.source_image,
            dest_registry_id=request.dest_registry_id,
            dest_folder=folder,
            local_registry_url=REGISTRY_URL,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"job_id": job_id, "status": "running"}


# ── Import (external -> local) ────────────────────────────────────────────────


@router.post("/import")
async def start_import(
    request: ImportRequest,
    _: UserInfo = Depends(require_push_access),
    settings: Settings = Depends(get_settings),
):
    """
    Trigger an import job (external -> local). Returns job_id immediately.
    """
    try:
        folder = validate_folder_path(request.dest_folder or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        job_id = await run_import_job(
            source_registry_id=request.source_registry_id,
            source_image=request.source_image,
            dest_folder=folder,
            local_registry_url=REGISTRY_URL,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"job_id": job_id, "status": "running"}


# ── Staging OCI cleanup ───────────────────────────────────────────────────────


@router.delete("/staging/{job_id}")
async def delete_staging_job(
    job_id: str,
    _: UserInfo = Depends(require_pull_access),
):
    """Delete an orphaned staging OCI directory."""

    try:
        oci_dir = safe_job_path(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not oci_dir.exists():
        raise HTTPException(status_code=404, detail="OCI directory not found")

    shutil.rmtree(oci_dir, ignore_errors=True)
    return {"message": f"Staging directory {job_id} deleted"}
