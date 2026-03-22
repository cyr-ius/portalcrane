"""
Portalcrane - Transfer Router
==============================
REST endpoints for cross-registry image transfer with optional Trivy CVE scan.

Endpoints:
  POST   /api/transfer          → start one or more transfer jobs
  GET    /api/transfer/jobs     → list all transfer jobs
  GET    /api/transfer/jobs/{id} → get a single transfer job
  DELETE /api/transfer/jobs/{id} → cancel / delete a transfer job

The transfer endpoint replaces the Sync import/export feature by unifying
all copy operations (local→local, local→external, external→local,
external→external) under a single API with integrated Trivy scanning.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import Settings, get_settings
from ..core.jwt import UserInfo, get_current_user, require_push_access
from ..routers.folders import check_folder_access
from ..services.transfer_service import (
    TransferRequest,
    delete_transfer_job,
    get_all_transfer_jobs,
    get_transfer_job,
    start_transfer_jobs,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_transfer(
    request: TransferRequest,
    settings: Settings = Depends(get_settings),
    current_user: UserInfo = Depends(require_push_access),
) -> dict:
    """
    Start one or more image transfer jobs.

    Non-admin users must have push access on the destination folder.
    Transfer pipeline: pull → optional Trivy scan → push.

    When multiple images are provided, one job is created per image.
    Scan policy is shared across all jobs in the same request.
    """
    # Validate destination folder access for non-admin users
    if not current_user.is_admin and request.dest_registry_id is None:
        # Destination is local registry — check folder permissions
        folder = request.dest_folder or ""
        for img_ref in request.images:
            dest_name = img_ref.repository.split("/")[-1]
            dest_path = f"{folder}/{dest_name}" if folder else dest_name
            access = check_folder_access(
                current_user.username, dest_path, is_pull=False
            )
            if access is not True:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Push permission denied for folder '{folder}'",
                )

    job_ids = await start_transfer_jobs(
        request=request,
        owner=current_user.username,
        settings=settings,
    )

    return {"job_ids": job_ids, "count": len(job_ids)}


@router.get("/jobs")
async def list_transfer_jobs(
    current_user: UserInfo = Depends(get_current_user),
) -> list[dict]:
    """
    Return all transfer jobs visible to the current user.

    Admins see all jobs. Regular users see only their own jobs.
    Jobs are sorted newest first.
    """
    jobs = get_all_transfer_jobs()
    if current_user.is_admin:
        return jobs
    return [j for j in jobs if j.get("owner") == current_user.username]


@router.get("/jobs/{job_id}")
async def get_transfer_job_status(
    job_id: str,
    current_user: UserInfo = Depends(get_current_user),
) -> dict:
    """Return the status of a specific transfer job."""
    job = get_transfer_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfer job not found",
        )
    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    return job


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_transfer_job(
    job_id: str,
    current_user: UserInfo = Depends(get_current_user),
) -> None:
    """
    Delete a transfer job and clean up its temporary OCI directory.

    Cannot cancel a job that is actively running (pulling/pushing),
    but the job record and any partial OCI directory will be removed.
    """
    job = get_transfer_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfer job not found",
        )
    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    delete_transfer_job(job_id)
