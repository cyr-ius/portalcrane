"""
Portalcrane - External Registries Router
CRUD for external registries + synchronisation endpoints.

Changes vs original:
  - GET  /registries → accessible to any authenticated user (returns global + own)
  - POST /registries → admin creates global; any push-enabled user creates personal
  - PATCH/DELETE /registries/{id} → owner or admin only
  - ExternalRegistry model now includes "owner" field
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import Settings, get_settings, REGISTRY_URL, STAGING_DIR
from ..services.external_registry_service import (
    build_target_path,
    create_registry,
    delete_registry,
    get_registries,
    get_registry_by_id,
    list_sync_jobs,
    run_sync_job,
    skopeo_push,
    test_registry_connection,
    update_registry,
    validate_folder_path,
)
from ..core.jwt import UserInfo, get_current_user, require_admin, require_push_access

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────────


class CreateRegistryRequest(BaseModel):
    """Payload to create a new external registry entry."""

    name: str
    host: str
    username: str = ""
    password: str = ""
    # "global" (admin only) or omitted → defaults to requesting user
    owner: str | None = None


class UpdateRegistryRequest(BaseModel):
    """Payload to update an external registry entry (all fields optional)."""

    name: str | None = None
    host: str | None = None
    username: str | None = None
    password: str | None = None
    owner: str | None = None


class TestConnectionRequest(BaseModel):
    """Payload to test connectivity to a registry without saving it."""

    host: str
    username: str = ""
    password: str = ""


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
    """Payload to trigger a registry synchronisation job."""

    source_image: str = "(all)"
    dest_registry_id: str
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
    if current_user.is_admin:
        return get_registries(owner=None)
    return get_registries(owner=current_user.username)


@router.post("/registries", status_code=status.HTTP_201_CREATED)
async def add_registry(
    payload: CreateRegistryRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Create a new external registry entry.
    - Admins can create global registries (owner="global") or personal ones.
    - Regular users can only create personal registries (owner=their username).
      Attempting to set owner="global" without admin rights raises 403.
    """
    # Resolve effective owner
    requested_owner = (payload.owner or "").strip()

    if requested_owner == "global":
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can create global registries",
            )
        effective_owner = "global"
    else:
        # Default: personal registry owned by the requesting user
        effective_owner = current_user.username

    return create_registry(
        name=payload.name,
        host=payload.host,
        username=payload.username,
        password=payload.password,
        owner=effective_owner,
    )


@router.patch("/registries/{registry_id}")
async def edit_registry(
    registry_id: str,
    payload: UpdateRegistryRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Update an existing external registry entry.
    Admins can edit any registry; regular users can only edit their own.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Registry not found"
        )

    # Non-admin users may only edit registries they own
    if not current_user.is_admin and registry.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    # Non-admin users cannot promote a registry to global
    new_owner = payload.owner
    if new_owner == "global" and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can make a registry global",
        )

    updated = update_registry(
        registry_id=registry_id,
        name=payload.name,
        host=payload.host,
        username=payload.username,
        password=payload.password,
        owner=new_owner,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Registry not found"
        )
    return updated


@router.delete("/registries/{registry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_registry(
    registry_id: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Delete an external registry entry.
    Admins can delete any registry; regular users can only delete their own.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Registry not found"
        )

    if not current_user.is_admin and registry.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    if not delete_registry(registry_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Registry not found"
        )


@router.post("/registries/test")
async def test_registry(
    payload: TestConnectionRequest,
    _: UserInfo = Depends(get_current_user),
):
    """Test connectivity to a registry (without saving it)."""
    return await test_registry_connection(
        host=payload.host,
        username=payload.username,
        password=payload.password,
    )


@router.post("/registries/{registry_id}/test")
async def test_saved_registry(
    registry_id: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """Test connectivity to a saved registry."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Registry not found"
        )

    # Users can only test registries they can see
    if not current_user.is_admin and registry.get("owner") not in (
        "global",
        current_user.username,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    return await test_registry_connection(
        host=registry["host"],
        username=registry.get("username", ""),
        password=registry.get("password", ""),
    )


# ── External push ─────────────────────────────────────────────────────────────


@router.post("/push")
async def push_to_external(
    payload: ExternalPushRequest,
    settings: Settings = Depends(get_settings),
    current_user: UserInfo = Depends(require_push_access),
):
    """Push a staged OCI layout directory to an external registry via skopeo."""
    import os

    if payload.registry_id:
        registry = get_registry_by_id(payload.registry_id)
        if not registry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Saved registry not found"
            )
        # Verify the user has access to this registry
        if not current_user.is_admin and registry.get("owner") not in (
            "global",
            current_user.username,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )
        host = registry["host"]
        username = registry.get("username", "")
        password = registry.get("password", "")
    elif payload.registry_host:
        host = payload.registry_host
        username = payload.registry_username or ""
        password = payload.registry_password or ""
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either registry_id or registry_host must be provided",
        )

    try:
        folder = validate_folder_path(payload.folder or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    oci_dir = os.path.join(STAGING_DIR, payload.job_id)
    if not os.path.isdir(oci_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OCI directory not found for job {payload.job_id}",
        )

    from ..routers.staging import _jobs  # noqa: PLC0415

    job = _jobs.get(payload.job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Staging job not found"
        )

    image_name = payload.image_name or job["image"].split("/")[-1]
    tag = payload.tag or job["tag"]
    dest_ref = build_target_path(folder, image_name, tag, host)

    success, message = await skopeo_push(
        oci_dir=oci_dir,
        dest_ref=dest_ref,
        dest_username=username,
        dest_password=password,
        settings=settings,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"skopeo push failed: {message}",
        )
    return {"message": f"Successfully pushed to {dest_ref}", "dest": dest_ref}


# ── Sync ──────────────────────────────────────────────────────────────────────


@router.get("/sync/jobs")
async def get_sync_jobs(
    _: UserInfo = Depends(require_admin),
):
    """List all sync jobs (most recent first)."""
    return list_sync_jobs()


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def start_sync(
    payload: SyncRequest,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """Start an asynchronous sync job."""
    try:
        folder = validate_folder_path(payload.dest_folder or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    dest_registry = get_registry_by_id(payload.dest_registry_id)
    if not dest_registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Destination registry not found",
        )

    job_id = await run_sync_job(
        source_image=payload.source_image,
        dest_registry_id=payload.dest_registry_id,
        dest_folder=folder,
        local_registry_url=REGISTRY_URL,
        settings=settings,
    )
    return {"job_id": job_id, "message": "Sync job started"}
