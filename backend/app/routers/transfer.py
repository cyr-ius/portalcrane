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
from ..helpers import is_local_registry_host
from ..routers.folders import (
    check_folder_access,
    has_external_pull_access,
    has_external_push_access,
)
from ..services.providers import resolve_provider_from_registry
from ..services.registries_service import get_registry_for_user
from ..services.transfer_service import (
    TransferRequest,
    delete_transfer_job,
    get_all_transfer_jobs,
    get_transfer_job,
    start_transfer_jobs,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _resolves_to_local_registry(
    registry_id: str | None, current_user: UserInfo
) -> bool:
    """Return True when a transfer end resolves to the embedded local registry.

    ``registry_id is None`` is the explicit local-registry selector. A non-None
    id is a saved registry whose host is resolved and matched against the local
    registry host — this catches the ``__local__`` system entry and any ad-hoc
    saved registry pointing back at the internal registry, so those ends are
    governed by the local can_pull / can_push permissions rather than the
    external ones. Ownership of the id is already validated by the caller.
    """
    if registry_id is None:
        return True
    registry = get_registry_for_user(
        registry_id, current_user.username, current_user.is_admin
    )
    if not registry:
        return False
    provider = resolve_provider_from_registry(registry)
    return is_local_registry_host(provider.host)


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
    # Enforce registry ownership for any saved external registry referenced by
    # this request. Without this a non-admin could pass another user's
    # registry_id and have the pipeline authenticate with their stored
    # credentials (browse, pull, and push as that user).
    for registry_id in (request.source_registry_id, request.dest_registry_id):
        if registry_id is not None and not get_registry_for_user(
            registry_id, current_user.username, current_user.is_admin
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Registry not found",
            )

    # Validate folder access for non-admin users.
    #
    # The transfer pipeline talks to the local embedded registry directly
    # (localhost:5000), bypassing the registry auth proxy, so folder permissions
    # must be enforced here. Which permission applies depends on whether each end
    # resolves to the local registry or a genuinely external one — a saved
    # registry_id may itself point at the local registry (the __local__ system
    # entry or an ad-hoc host resolving to localhost:5000), so the id being
    # non-None is not sufficient to treat the end as external.
    #
    #   source local    → can_pull on each source repository (per-folder)
    #   source external → can_pull_external capability (any folder grant)
    #   dest   local    → can_push on each destination folder (per-folder)
    #   dest   external → can_push_external capability (any folder grant)
    #
    # The external ends use capability checks rather than per-folder ones: a
    # foreign repository path does not map to a Portalcrane folder, so scoping
    # the right to the resolved folder would collapse onto __root__.
    if not current_user.is_admin:
        source_is_local = _resolves_to_local_registry(
            request.source_registry_id, current_user
        )
        dest_is_local = _resolves_to_local_registry(
            request.dest_registry_id, current_user
        )

        if source_is_local:
            for img_ref in request.images:
                # Source repository full path (namespace included).
                access = check_folder_access(
                    current_user.username, img_ref.repository, is_pull=True
                )
                if access is not True:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=(
                            f"Pull permission denied for source '{img_ref.repository}'"
                        ),
                    )
        elif not has_external_pull_access(current_user.username):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="External pull permission denied",
            )

        if dest_is_local:
            folder = request.dest_folder or ""
            single = len(request.images) == 1
            for img_ref in request.images:
                # Honour the per-image rename (or the single-image request-level
                # override) so the permission check matches the repository that
                # is actually pushed, not the untouched source name.
                override = (img_ref.dest_name or "").strip() or (
                    request.dest_name_override if single else None
                )
                dest_name = (override or img_ref.repository).split("/")[-1]
                dest_path = f"{folder}/{dest_name}" if folder else dest_name
                access = check_folder_access(
                    current_user.username, dest_path, is_pull=False
                )
                if access is not True:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Push permission denied for folder '{folder}'",
                    )
        elif not has_external_push_access(current_user.username):
            # Genuinely external destination — governed by the can_push_external
            # capability (granted on any folder).
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="External push permission denied",
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
