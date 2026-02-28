"""
Portalcrane - External Registries Router
CRUD for external registries + synchronisation endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import Settings, get_settings
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
from .auth import UserInfo, require_admin, require_push_access

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────────


class CreateRegistryRequest(BaseModel):
    """Payload to create a new external registry entry."""

    name: str
    host: str
    username: str = ""
    password: str = ""


class UpdateRegistryRequest(BaseModel):
    """Payload to update an external registry entry (all fields optional)."""

    name: str | None = None
    host: str | None = None
    username: str | None = None
    password: str | None = None


class TestConnectionRequest(BaseModel):
    """Payload to test connectivity to a registry without saving it."""

    host: str
    username: str = ""
    password: str = ""


class ExternalPushRequest(BaseModel):
    """
    Push a staged OCI layout to an external registry.
    Used by the staging pipeline when the user selects 'External registry'.
    """

    job_id: str
    registry_id: str | None = None  # ID of a saved registry
    registry_host: str | None = None  # Ad-hoc host (when not saved)
    registry_username: str | None = None
    registry_password: str | None = None
    folder: str | None = None  # Optional destination folder prefix
    image_name: str | None = None  # Override image name
    tag: str | None = None  # Override tag


class SyncRequest(BaseModel):
    """Payload to trigger a registry synchronisation job."""

    source_image: str = "(all)"  # "repo:tag" or "(all)"
    dest_registry_id: str
    dest_folder: str | None = None  # Optional destination folder prefix


# ── Registry CRUD ─────────────────────────────────────────────────────────────


@router.get("/registries")
async def list_registries(
    _: UserInfo = Depends(require_admin),
):
    """List all configured external registries (passwords redacted)."""
    return get_registries()


@router.post("/registries", status_code=status.HTTP_201_CREATED)
async def add_registry(
    payload: CreateRegistryRequest,
    _: UserInfo = Depends(require_admin),
):
    """Create a new external registry entry."""
    return create_registry(
        name=payload.name,
        host=payload.host,
        username=payload.username,
        password=payload.password,
    )


@router.patch("/registries/{registry_id}")
async def edit_registry(
    registry_id: str,
    payload: UpdateRegistryRequest,
    _: UserInfo = Depends(require_admin),
):
    """Update an existing external registry entry."""
    updated = update_registry(
        registry_id=registry_id,
        name=payload.name,
        host=payload.host,
        username=payload.username,
        password=payload.password,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry not found",
        )
    return updated


@router.delete("/registries/{registry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_registry(
    registry_id: str,
    _: UserInfo = Depends(require_admin),
):
    """Delete an external registry entry."""
    if not delete_registry(registry_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry not found",
        )


@router.post("/registries/test")
async def test_registry(
    payload: TestConnectionRequest,
    _: UserInfo = Depends(require_admin),
):
    """Test connectivity to a registry (without saving it)."""
    result = await test_registry_connection(
        host=payload.host,
        username=payload.username,
        password=payload.password,
    )
    return result


@router.post("/registries/{registry_id}/test")
async def test_saved_registry(
    registry_id: str,
    _: UserInfo = Depends(require_admin),
):
    """Test connectivity to a saved registry."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry not found",
        )
    result = await test_registry_connection(
        host=registry["host"],
        username=registry.get("username", ""),
        password=registry.get("password", ""),
    )
    return result


# ── External push ─────────────────────────────────────────────────────────────


@router.post("/push")
async def push_to_external(
    payload: ExternalPushRequest,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_push_access),
):
    """
    Push a staged OCI layout directory to an external registry via skopeo.
    The OCI directory is located at {staging_dir}/{job_id}.
    """
    import os

    # Resolve destination credentials
    if payload.registry_id:
        registry = get_registry_by_id(payload.registry_id)
        if not registry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved registry not found",
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

    # Validate folder path to prevent directory traversal
    try:
        folder = validate_folder_path(payload.folder or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Locate OCI layout directory produced by the pull pipeline
    oci_dir = os.path.join(settings.staging_dir, payload.job_id)
    if not os.path.isdir(oci_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OCI directory not found for job {payload.job_id}",
        )

    # Retrieve original image name and tag from the staging job store
    from ..routers.staging import _jobs  # noqa: PLC0415

    job = _jobs.get(payload.job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staging job not found",
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
    """
    Start an asynchronous sync job: copies images from the local registry
    to the specified external registry via skopeo.
    """
    # Validate folder path
    try:
        folder = validate_folder_path(payload.dest_folder or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
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
        local_registry_url=settings.registry_url,
        local_username=settings.registry_username,
        local_password=settings.registry_password,
        settings=settings,
    )

    return {"job_id": job_id, "message": "Sync job started"}
