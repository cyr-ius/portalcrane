"""
Portalcrane - External Registries Router
CRUD for external registries + synchronisation endpoints.

Changes vs previous version:
  - tls_verify field added to create/update/test payloads.
  - [NEW] GET  /registries/{id}/browse   — browse repositories in an external
    registry via its /v2/_catalog API (Évolution 1).
  - [NEW] GET  /registries/{id}/tags     — list tags of a repo in an external
    registry (Évolution 1).
  - [NEW] POST /import                   — start an import job
    (external → local, Évolution 2).
  - SyncJob response model now exposes direction field (Évolution 2).
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..config import Settings, get_settings, REGISTRY_URL, STAGING_DIR
from ..services.job_service import jobs_list
from ..services.external_registry_service import (
    browse_external_images,
    browse_external_tags,
    build_target_path,
    create_registry,
    delete_registry,
    get_registries,
    get_registry_by_id,
    list_sync_jobs,
    run_import_job,
    run_sync_job,
    skopeo_push,
    test_registry_connection,
    update_registry,
    validate_folder_path,
)
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
    # "global" (admin only) or omitted → defaults to requesting user
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
    """Payload to trigger a registry synchronisation job (local → external)."""

    source_image: str = "(all)"
    dest_registry_id: str
    dest_folder: str | None = None


class ImportRequest(BaseModel):
    """Payload to trigger an import job (external → local)."""

    source_registry_id: str
    source_image: str = "(all)"
    dest_folder: str | None = None


# ── Registry CRUD ─────────────────────────────────────────────────────────────


@router.get("/registries")
async def list_registries(
    current_user: UserInfo = Depends(get_current_user),
):
    """
    List external registries visible to the current user.
    Admins see all registries; regular users see global + their own.
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
    Non-admin users with push access may create personal registries only.
    """
    # Only admins can create global registries
    if request.owner == "global" and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create global registries",
        )
    owner = request.owner if request.owner else current_user.username
    return create_registry(
        name=request.name,
        host=request.host,
        username=request.username,
        password=request.password,
        owner=owner,
        use_tls=request.use_tls,
        tls_verify=request.tls_verify,
    )


@router.patch("/registries/{registry_id}")
async def update_registry_endpoint(
    registry_id: str,
    request: UpdateRegistryRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Update an external registry entry. Owner or admin only."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    if not current_user.is_admin and registry.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this registry",
        )

    updated = update_registry(
        registry_id=registry_id,
        name=request.name,
        host=request.host,
        username=request.username,
        password=request.password,
        owner=request.owner,
        use_tls=request.use_tls,
        tls_verify=request.tls_verify,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Registry not found")
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
    return await test_registry_connection(
        host=registry["host"],
        username=registry.get("username", ""),
        password=registry.get("password", ""),
        use_tls=registry.get("use_tls", True),
        tls_verify=registry.get("tls_verify", True),
    )


# ── Browse external registry (Évolution 1) ───────────────────────────────────


@router.get("/registries/{registry_id}/browse")
async def browse_registry_images(
    registry_id: str,
    search: str | None = Query(None, description="Filter repositories by name"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=200),
    # require_pull_access ensures a valid JWT; actual folder-based access
    # control is handled by the registry proxy, not here.
    _: UserInfo = Depends(require_pull_access),
):
    """
    List repositories available in an external registry.

    Uses the standard Docker Distribution v2 /v2/_catalog endpoint.
    Results are paginated and optionally filtered by search keyword.
    Requires a valid authentication token (pull access).
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
    """
    List tags for a specific repository in an external registry.
    Requires a valid authentication token.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    return await browse_external_tags(registry_id=registry_id, repository=repository)


# ── Push to external registry ─────────────────────────────────────────────────


@router.post("/push")
async def push_to_external(
    request: ExternalPushRequest,
    current_user: UserInfo = Depends(require_push_access),
    settings: Settings = Depends(get_settings),
):
    """Push a staged OCI layout to an external registry."""
    # Resolve credentials: saved registry takes priority over ad-hoc fields
    if request.registry_id:
        registry = get_registry_by_id(request.registry_id)
        if not registry:
            raise HTTPException(status_code=404, detail="Registry not found")
        host = registry["host"]
        username = registry.get("username", "")
        password = registry.get("password", "")
        tls_verify = registry.get("tls_verify", True)
    else:
        host = request.registry_host or ""
        username = request.registry_username or ""
        password = request.registry_password or ""
        tls_verify = True

    # Validate and resolve the OCI staging directory
    job_id = request.job_id
    if job_id not in jobs_list:
        raise HTTPException(status_code=404, detail="Staging job not found")

    oci_dir = str(Path(STAGING_DIR) / job_id)
    image_name = request.image_name or job_id
    tag = request.tag or "latest"

    folder: str | None = None
    if request.folder:
        try:
            folder = validate_folder_path(request.folder)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    dest_ref = build_target_path(folder, image_name, tag, host)
    ok, msg = await skopeo_push(
        oci_dir=oci_dir,
        dest_ref=dest_ref,
        dest_username=username,
        dest_password=password,
        settings=settings,
        tls_verify=tls_verify,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg
        )
    return {"message": msg, "dest_ref": dest_ref}


# ── Sync (local → external) ───────────────────────────────────────────────────


@router.post("/sync")
async def start_sync(
    request: SyncRequest,
    current_user: UserInfo = Depends(require_push_access),
    settings: Settings = Depends(get_settings),
):
    """
    Start an asynchronous export job (local registry → external registry).
    Requires push access.
    """
    registry = get_registry_by_id(request.dest_registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Destination registry not found")

    dest_folder: str | None = None
    if request.dest_folder:
        try:
            dest_folder = validate_folder_path(request.dest_folder)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    job_id = await run_sync_job(
        source_image=request.source_image,
        dest_registry_id=request.dest_registry_id,
        dest_folder=dest_folder,
        local_registry_url=REGISTRY_URL,
        settings=settings,
    )
    return {"job_id": job_id, "status": "started"}


# ── Import (external → local, Évolution 2) ───────────────────────────────────


@router.post("/import", status_code=status.HTTP_202_ACCEPTED)
async def start_import(
    request: ImportRequest,
    current_user: UserInfo = Depends(require_push_access),
    settings: Settings = Depends(get_settings),
):
    """
    Start an asynchronous import job (external registry → local registry).

    Mirrors the export endpoint with source and destination reversed:
      source: external registry identified by source_registry_id
      dest:   local Portalcrane registry, optionally under dest_folder

    Requires push access because the operation writes to the local registry.
    """
    registry = get_registry_by_id(request.source_registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Source registry not found")

    dest_folder: str | None = None
    if request.dest_folder:
        try:
            dest_folder = validate_folder_path(request.dest_folder)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    logger.info(
        "Import job requested by %s: registry=%s image=%s dest_folder=%s",
        current_user.username,
        request.source_registry_id,
        request.source_image,
        dest_folder,
    )

    job_id = await run_import_job(
        source_registry_id=request.source_registry_id,
        source_image=request.source_image,
        dest_folder=dest_folder,
        local_registry_url=REGISTRY_URL,
        settings=settings,
    )
    return {"job_id": job_id, "status": "started"}


# ── Sync job history ──────────────────────────────────────────────────────────


@router.get("/sync/jobs")
async def get_sync_jobs(
    _: UserInfo = Depends(require_push_access),
):
    """
    Return all sync/import job history sorted by start time descending.
    The direction field ("export" or "import") indicates the transfer direction.
    """
    return list_sync_jobs()
