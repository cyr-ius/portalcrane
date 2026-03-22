import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..config import REGISTRY_HOST, REGISTRY_URL, Settings, get_settings
from ..core.jwt import (
    UserInfo,
    get_current_user,
    require_admin,
    require_pull_access,
    require_push_access,
)
from ..services.job_service import normalize_sync_job
from ..services.registries_service import get_registry_by_id
from ..services.repositories_service import (
    append_tag,
    browse_images,
    browse_tags,
    empty_tags,
    list_sync_jobs,
    metadata_by_tag,
    purge_registry,
    remove_image,
    remove_tag,
    run_export_job,
    run_import_job,
    skopeo_copy_image_image,
    validate_folder_path,
)
from .folders import check_folder_access

router = APIRouter()
logger = logging.getLogger(__name__)


class AddExternalTagRequest(BaseModel):
    source_tag: str
    new_tag: str


class CopyImageRequest(BaseModel):
    """Copy an image to a new repository path within the local registry."""

    source_repository: str
    source_tag: str
    dest_repository: str
    dest_tag: str | None = None


class ImportRequest(BaseModel):
    """Payload to trigger an import job (external -> local)."""

    source_registry_id: str
    source_image: str = "(all)"
    dest_folder: str | None = None


class SyncRequest(BaseModel):
    """Payload to trigger a registry synchronisation job (local -> external)."""

    source_image: str = "(all)"
    dest_registry_id: str
    dest_folder: str | None = None


@router.get("/{registry_id}")
async def list_images(
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

    result = await browse_images(
        registry_id=registry_id,
        search=search,
        page=page,
        page_size=page_size,
    )
    return result


@router.delete("/{registry_id}")
async def delete_image(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    _: UserInfo = Depends(require_push_access),
):
    """Delete all tags of a repository in an external registry."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    result = await remove_image(registry_id=registry_id, repository=repository)
    if result.get("failed_tags") and not result.get("deleted_tags"):
        raise HTTPException(
            status_code=502, detail=result.get("message", "Delete failed")
        )
    return result


@router.get("/{registry_id}/tags/detail")
async def get_tag_detail(
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

    detail = await metadata_by_tag(
        registry_id=registry_id, repository=repository, tag=tag
    )
    if not detail:
        raise HTTPException(
            status_code=404, detail="Tag not found or registry type unsupported"
        )
    return detail


@router.get("/{registry_id}/tags")
async def get_tags(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    _: UserInfo = Depends(require_pull_access),
):
    """List tags for a specific repository in an external registry."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    return await browse_tags(registry_id=registry_id, repository=repository)


@router.delete("/{registry_id}/tags")
async def delete_tag(
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

    result = await remove_tag(registry_id=registry_id, repository=repository, tag=tag)
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("message", "Delete tag failed")
        )
    return result


@router.post("/{registry_id}/tags")
async def add_tag(
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

    result = await append_tag(
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


@router.get("/{registry_id}/empty")
async def list_empty(registry_id: str, _: UserInfo = Depends(require_admin)):
    """List repositories that have no tags (ghost entries).

    Uses the local V2 provider which delegates to V2Provider.list_empty_repositories().
    This replaces the former GET /api/registry/empty-repositories endpoint.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    empty = await empty_tags(registry_id=registry_id)
    return {"empty_repositories": empty, "count": len(empty)}


@router.delete("/{registry_id}/empty")
async def purge_empty(registry_id: str, _: UserInfo = Depends(require_admin)):
    """Purge ghost repositories directly from the local filesystem.

    Resolves the list from the V2 provider then removes directories on disk.
    This replaces the former DELETE /api/registry/empty-repositories endpoint.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    purged, errors = await purge_registry(registry_id=registry_id)

    return {
        "message": f"Purged {len(purged)} empty repositories",
        "purged": purged,
        "errors": errors,
    }


@router.post("/copy")
async def copy_image(
    request: CopyImageRequest,
    current_user: UserInfo = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Copy an image to a new repository path within the local registry via skopeo.

    Non-admin users must have pull access on the source folder and push access
    on the destination folder.

    This replaces the former POST /api/registry/images/copy endpoint.
    """
    _ensure_folder_permission(
        current_user=current_user,
        image_path=request.source_repository,
        is_pull=True,
    )
    _ensure_folder_permission(
        current_user=current_user,
        image_path=request.dest_repository,
        is_pull=False,
    )

    dest_tag = request.dest_tag or request.source_tag
    source = (
        f"docker://{REGISTRY_HOST}/{request.source_repository}:{request.source_tag}"
    )
    dest = f"docker://{REGISTRY_HOST}/{request.dest_repository}:{dest_tag}"

    state, message = await skopeo_copy_image_image(
        src_ref=source,
        settings=settings,
        dest_ref=dest,
        dest_username="",
        dest_password="",
        dest_tls_verify=False,
    )

    if state is False:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Copy failed: {message}",
        )

    return {"message": message}


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


def _ensure_folder_permission(
    *, current_user: UserInfo, image_path: str, is_pull: bool
) -> None:
    """Enforce folder pull/push permission on a repository path for non-admins."""
    if current_user.is_admin:
        return
    has_access = check_folder_access(
        current_user.username,
        image_path,
        is_pull=is_pull,
    )
    if has_access:
        return
    action = "pull" if is_pull else "push"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"No {action} access on folder for '{image_path}'",
    )
