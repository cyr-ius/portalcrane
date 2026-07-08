"""Portalcrane - Repositories Router.

All endpoints that contact the local or external registry catch
httpx.ConnectError / httpx.TimeoutException at the router level and return
a proper HTTP 503 instead of crashing the ASGI application with a 500.
"""

import logging

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..config import REGISTRY_HOST, Settings, get_settings
from ..core.jwt import (
    UserInfo,
    get_current_user,
    require_admin,
)
from ..services.registries_service import (
    LOCAL_REGISTRY_SYSTEM_ID,
    get_registry_by_id,
)
from ..services.repositories_service import (
    append_tag,
    browse_images,
    browse_tags,
    empty_tags,
    metadata_by_tag,
    purge_registry,
    remove_image,
    remove_tag,
    skopeo_copy_image_image,
)
from .folders import (
    check_folder_access,
    has_external_pull_access,
)
from .registries import resolve_owned_registry

router = APIRouter()
logger = logging.getLogger(__name__)

# Exceptions indicating the registry is temporarily unavailable
_REGISTRY_ERRORS = (httpx.ConnectError, httpx.TimeoutException)


class AddExternalTagRequest(BaseModel):
    source_tag: str
    new_tag: str


class CopyImageRequest(BaseModel):
    """Copy an image to a new repository path within the local registry."""

    source_repository: str
    source_tag: str
    dest_repository: str
    dest_tag: str | None = None


@router.get("/{registry_id}")
async def list_images(
    registry_id: str,
    search: str | None = Query(None, description="Filter repositories by name"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=200),
    current_user: UserInfo = Depends(get_current_user),
    _: dict = Depends(resolve_owned_registry),
):
    """List repositories available in an external registry via /v2/_catalog."""
    # Visibility rules for non-admins (applied before pagination inside the
    # provider so total / total_pages stay consistent with the visible items):
    #   * Local registry  -> per-folder ``can_pull`` on each repository path.
    #   * External registry -> the coarse ``can_pull_external`` capability.
    #     Foreign paths (e.g. Docker Hub ``library/nginx``) don't map to a
    #     Portalcrane folder — they'd collapse onto __root__ — so external
    #     visibility is governed by the capability, not the folder resolved
    #     from the path. See has_external_pull_access() for the rationale.
    repo_filter = None
    if not current_user.is_admin:
        if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
            repo_filter = lambda name: (  # noqa: E731
                check_folder_access(current_user.username, name, is_pull=True) is True
            )
        else:
            allowed = has_external_pull_access(current_user.username)
            repo_filter = lambda name: allowed  # noqa: E731

    try:
        result = await browse_images(
            registry_id=registry_id,
            search=search,
            page=page,
            page_size=page_size,
            repo_filter=repo_filter,
        )
    except _REGISTRY_ERRORS as exc:
        logger.warning("list_images: registry unreachable id=%s: %s", registry_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
        )
    return result


@router.delete("/{registry_id}")
async def delete_image(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    current_user: UserInfo = Depends(get_current_user),
    _: dict = Depends(resolve_owned_registry),
):
    """Delete all tags of a repository in an external registry."""
    _ensure_folder_permission(
        current_user=current_user, image_path=repository, is_pull=False
    )
    try:
        result = await remove_image(registry_id=registry_id, repository=repository)
    except _REGISTRY_ERRORS as exc:
        logger.warning("delete_image: registry unreachable id=%s: %s", registry_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
        )

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
    current_user: UserInfo = Depends(get_current_user),
    _: dict = Depends(resolve_owned_registry),
):
    """Return detailed metadata for a specific tag in an external V2 registry."""
    _ensure_read_access(
        current_user=current_user, registry_id=registry_id, repository=repository
    )
    try:
        detail = await metadata_by_tag(
            registry_id=registry_id, repository=repository, tag=tag
        )
    except _REGISTRY_ERRORS as exc:
        logger.warning(
            "get_tag_detail: registry unreachable id=%s: %s", registry_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
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
    current_user: UserInfo = Depends(get_current_user),
    _: dict = Depends(resolve_owned_registry),
):
    """List tags for a specific repository in an external registry."""
    _ensure_read_access(
        current_user=current_user, registry_id=registry_id, repository=repository
    )
    try:
        return await browse_tags(registry_id=registry_id, repository=repository)
    except _REGISTRY_ERRORS as exc:
        logger.warning("get_tags: registry unreachable id=%s: %s", registry_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
        )


@router.delete("/{registry_id}/tags")
async def delete_tag(
    registry_id: str,
    repository: str = Query(..., description="Repository name, e.g. myorg/myimage"),
    tag: str = Query(..., description="Tag name to delete"),
    current_user: UserInfo = Depends(get_current_user),
    _: dict = Depends(resolve_owned_registry),
):
    """Delete a single tag from an external V2 registry."""
    _ensure_folder_permission(
        current_user=current_user, image_path=repository, is_pull=False
    )
    try:
        result = await remove_tag(
            registry_id=registry_id, repository=repository, tag=tag
        )
    except _REGISTRY_ERRORS as exc:
        logger.warning("delete_tag: registry unreachable id=%s: %s", registry_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
        )

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
    current_user: UserInfo = Depends(get_current_user),
    _: dict = Depends(resolve_owned_registry),
):
    """Create a new tag by copying a manifest in an external V2 registry."""
    _ensure_folder_permission(
        current_user=current_user, image_path=repository, is_pull=False
    )
    try:
        result = await append_tag(
            registry_id=registry_id,
            repository=repository,
            source_tag=request.source_tag,
            new_tag=request.new_tag,
        )
    except _REGISTRY_ERRORS as exc:
        logger.warning("add_tag: registry unreachable id=%s: %s", registry_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
        )

    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("message", "Add tag failed")
        )
    return result


@router.get("/{registry_id}/empty")
async def list_empty(registry_id: str, _: UserInfo = Depends(require_admin)):
    """List repositories that have no tags (ghost entries)."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    try:
        empty = await empty_tags(registry_id=registry_id)
    except _REGISTRY_ERRORS as exc:
        logger.warning("list_empty: registry unreachable id=%s: %s", registry_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
        )

    return {"empty_repositories": empty, "count": len(empty)}


@router.delete("/{registry_id}/empty")
async def purge_empty(registry_id: str, _: UserInfo = Depends(require_admin)):
    """Purge ghost repositories directly from the local filesystem."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")

    try:
        purged, errors = await purge_registry(registry_id=registry_id)
    except _REGISTRY_ERRORS as exc:
        logger.warning("purge_empty: registry unreachable id=%s: %s", registry_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Registry temporarily unreachable",
        )

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
    """Copy an image to a new repository path within the local registry via skopeo."""
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


def _ensure_read_access(
    *, current_user: UserInfo, registry_id: str, repository: str
) -> None:
    """Enforce read (pull) access for a repository in the given registry.

    Local registry: per-folder ``can_pull`` on the repository path.
    External registry: the coarse ``can_pull_external`` capability, because
    foreign paths don't map to a Portalcrane folder (they'd collapse onto
    __root__). This mirrors the visibility rule applied when listing images.
    """
    if current_user.is_admin:
        return
    if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
        _ensure_folder_permission(
            current_user=current_user, image_path=repository, is_pull=True
        )
        return
    if not has_external_pull_access(current_user.username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No external pull access",
        )
