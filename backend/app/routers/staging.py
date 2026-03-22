"""
Portalcrane - Staging Router
Pipeline: skopeo copy (source registry → OCI layout) → Trivy CVE scan (optional) → skopeo copy (OCI → Registry)
"""

import logging
import shutil
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import DEFAULT_TIMEOUT, REGISTRY_HOST, Settings, get_settings
from ..core.jwt import UserInfo, is_admin_user, require_pull_access, require_push_access
from ..routers.folders import check_folder_access
from ..services.job_service import (
    JobStatus,
    PullRequest,
    PushRequest,
    StagingJob,
    jobs_list,
    run_pull_pipeline,
    run_push_pipeline,
    safe_job_path,
)
from ..services.providers import build_target_path, resolve_provider_from_registry
from ..services.repositories_service import skopeo_copy_oci_image
from .registries import get_registry_by_id

router = APIRouter()

_logger = logging.getLogger(__name__)

_DOCKERHUB_API_URL: str = "https://hub.docker.com"


class DockerHubSearchResult(BaseModel):
    """Docker Hub search result model."""

    name: str
    description: str
    star_count: int
    pull_count: int
    is_official: bool
    is_automated: bool


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _build_external_target_image(image: str, username: str) -> str:
    """Return external target image path, replacing source namespace with destination username."""
    if not username:
        return image

    leaf = image.split("/")[-1]
    return f"{username}/{leaf}"


def _resolve_pull_source(
    request: PullRequest,
    _: UserInfo,
) -> tuple[str, list[str], str | None]:
    """
    Resolve the skopeo source reference, credentials flags and display host
    from the pull request.

    Returns:
        src_ref      : full skopeo docker:// reference
        src_creds    : list of skopeo flags (--src-tls-verify and optionally --src-creds)
        display_host : human-readable registry host for storage in the job (None = Docker Hub)

    FIX: for saved and ad-hoc external registries, --src-tls-verify is now
    included in src_creds based on the registry's use_tls / tls_verify fields.
    Previously the flag was never set, causing skopeo to default to HTTPS even
    for plain-HTTP registries (use_tls=False).
    """
    image = request.image
    tag = request.tag

    # ── 1. Saved external registry ────────────────────────────────────────────
    if request.source_registry_id:
        registry = get_registry_by_id(request.source_registry_id)
        if not registry:
            raise ValueError(f"Source registry not found: {request.source_registry_id}")

        provider = resolve_provider_from_registry(registry)
        src_ref = build_target_path(None, image, tag, provider.host)
        src_creds = [f"--src-tls-verify={'true' if provider.verify else 'false'}"]
        if provider.username and provider.password:
            src_creds += ["--src-creds", f"{provider.username}:{provider.password}"]

        _logger.debug(
            "Pull source resolved: saved registry id=%s host=%s use_tls=%s tls_verify=%s "
            "→ --src-tls-verify=%s",
            request.source_registry_id,
            provider.host,
            provider.use_tls,
            provider.tls_verify,
            provider.verify,
        )
        return src_ref, src_creds, provider.host

    # ── 2. Ad-hoc source registry ─────────────────────────────────────────────
    if request.source_registry_host:
        host = request.source_registry_host.rstrip("/")
        username = request.source_registry_username or ""
        password = request.source_registry_password or ""

        # For ad-hoc registries the caller has no use_tls field, but the host
        # itself may carry a scheme (http:// or https://).  When the host starts
        # with http:// we know TLS should be disabled; otherwise we leave it at
        # the skopeo default (true).  Strip the scheme before building src_ref.
        if host.startswith("http://"):
            bare_host = host[len("http://") :]
            src_ref = f"docker://{bare_host}/{image}:{tag}"
            src_creds = ["--src-tls-verify=false"]
            _logger.debug(
                "Pull source resolved: ad-hoc HTTP registry host=%s → --src-tls-verify=false",
                bare_host,
            )
        elif host.startswith("https://"):
            bare_host = host[len("https://") :]
            src_ref = f"docker://{bare_host}/{image}:{tag}"
            src_creds = ["--src-tls-verify=true"]
        else:
            # No scheme provided: keep as-is, use skopeo default (HTTPS)
            src_ref = f"docker://{host}/{image}:{tag}"
            src_creds = []

        if username and password:
            src_creds += ["--src-creds", f"{username}:{password}"]

        return src_ref, src_creds, host

    # ── 3. Default: Docker Hub ─────────────────────────────────────────────────
    src_ref = f"docker://{image}:{tag}"
    src_ref = build_target_path(None, image, tag, None)
    src_creds = []
    return src_ref, src_creds, None


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/pull", response_model=StagingJob)
async def pull_image(
    request: PullRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    current_user: UserInfo = Depends(require_pull_access),
):
    """
    Start a pull+scan pipeline for an image.

    The source registry is resolved in this order:
      1. source_registry_id  → saved external registry
      2. source_registry_host → ad-hoc registry with optional credentials
      3. (default)           → Docker Hub using the user's saved Hub credentials
    """
    # Resolve source reference before creating the job so we can return 404
    # immediately if the saved registry ID is invalid.
    try:
        src_ref, src_creds, display_host = _resolve_pull_source(request, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if display_host and display_host == REGISTRY_HOST:
        # folder check uses image name (no tag)
        if not is_admin_user(current_user.username, settings):
            access = check_folder_access(
                current_user.username, request.image, is_pull=True
            )
            if access is not True:
                # False or None both treated as forbidden
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Folder access denied: pull permission required",
                )

    job_id = str(uuid.uuid4())
    jobs_list[job_id] = {
        "job_id": job_id,
        "status": JobStatus.PENDING,
        "image": request.image,
        "tag": request.tag,
        "progress": 0,
        "message": "Job queued...",
        "scan_result": None,
        "vuln_result": None,
        "target_image": None,
        "target_tag": None,
        "folder": None,
        "error": None,
        "vuln_scan_enabled_override": request.vuln_scan_enabled_override,
        "vuln_severities_override": request.vuln_severities_override,
        "owner": current_user.username,
        "source_registry_host": display_host,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    background_tasks.add_task(
        run_pull_pipeline,
        job_id,
        request.image,
        request.tag,
        settings,
        current_user,
        request.vuln_scan_enabled_override,
        request.vuln_severities_override,
        src_ref,
        src_creds,
    )
    return StagingJob(**jobs_list[job_id])


@router.post("/push")
async def push_image(
    request: PushRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    current_user: UserInfo = Depends(require_push_access),
):
    """
    Push a scanned image to the local registry or to an external registry.
    Non-admin users can only push their own jobs.
    """
    if request.job_id not in jobs_list:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    job = jobs_list[request.job_id]
    # remember folder in job record for auditing/debugging
    job["folder"] = request.folder or ""

    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    try:
        oci_dir = safe_job_path(request.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    target_image = request.target_image or job["image"]
    target_tag = request.target_tag or job["tag"]
    folder = request.folder or ""

    if request.external_registry_host or request.external_registry_id:
        if request.external_registry_id:
            registry = get_registry_by_id(request.external_registry_id)
            if not registry:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Saved registry not found",
                )
            provider = resolve_provider_from_registry(registry)
            host = provider.host
            username = provider.username
            password = provider.password
            effective_tls_verify = provider.verify
        else:
            host = request.external_registry_host or ""
            username = request.external_registry_username or ""
            password = request.external_registry_password or ""
            effective_tls_verify = True

        external_target_image = _build_external_target_image(target_image, username)
        _logger.debug(
            "External push image mapping source=%s username=%s mapped=%s",
            target_image,
            username,
            external_target_image,
        )
        dest_ref = build_target_path(folder, external_target_image, target_tag, host)

        job["status"] = JobStatus.PUSHING
        job["message"] = f"Pushing to external registry {host}..."

        success, message = await skopeo_copy_oci_image(
            oci_dir=str(oci_dir),
            dest_ref=dest_ref,
            dest_username=username,
            dest_password=password,
            settings=settings,
            tls_verify=effective_tls_verify,
        )
        if success:
            job["status"] = JobStatus.DONE
            job["message"] = f"✅ Successfully pushed to {dest_ref}"
            job["target_image"] = external_target_image
            job["target_tag"] = target_tag
        else:
            job["status"] = JobStatus.FAILED
            job["message"] = f"❌ Push failed: {message}"
            job["error"] = message

        return {"message": message, "job_id": request.job_id}

    # Before scheduling local push, verify push permission for the
    # requested folder/image combination.
    if not is_admin_user(current_user.username, settings):
        full_path = f"{folder}/{target_image}" if folder else target_image
        access = check_folder_access(current_user.username, full_path, is_pull=False)
        if access is not True:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Folder access denied: push permission required",
            )

    background_tasks.add_task(
        run_push_pipeline,
        request.job_id,
        target_image,
        target_tag,
        settings,
        folder,
    )
    return {"message": "Push started", "job_id": request.job_id}


# ─── Search ────────────────────────────────────────────────────────────────────


@router.get("/search/dockerhub")
async def search_dockerhub(
    q: str,
    page: int = 1,
    _: UserInfo = Depends(require_pull_access),
):
    """Search Docker Hub images (only anonymous)."""
    # Docker Hub search has used both `q` and `query` over time depending on
    # endpoint generation and account context. Send both keys for compatibility.
    params = {"q": q, "query": q, "page": page, "page_size": 25}
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, follow_redirects=True
        ) as client:

            async def _search() -> httpx.Response:
                return await client.get(
                    f"{_DOCKERHUB_API_URL}/v2/search/repositories/",
                    params=params,
                )

            resp = await _search()
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Docker Hub search failed: {exc}",
        )

    raw_results = data.get("results") or data.get("summaries") or []

    results = [
        DockerHubSearchResult(
            name=(
                r.get("repo_name")
                or (
                    f"{r.get('namespace')}/{r.get('name')}"
                    if r.get("namespace") and r.get("name")
                    else r.get("name", "")
                )
            ),
            description=r.get("short_description") or r.get("description", ""),
            star_count=r.get("star_count", 0),
            pull_count=r.get("pull_count", 0),
            is_official=r.get("is_official", False),
            is_automated=r.get("is_automated", False),
        )
        for r in raw_results
    ]
    return {"results": results, "count": data.get("count", len(results))}


@router.get("/dockerhub/tags/{image:path}")
async def get_dockerhub_tags(
    image: str,
    _: UserInfo = Depends(require_pull_access),
):
    """Fetch available tags for a Docker Hub image."""
    namespace, name = image.split("/", 1) if "/" in image else ("library", image)
    url = f"{_DOCKERHUB_API_URL}/v2/repositories/{namespace}/{name}/tags/"
    try:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(url, params={"page_size": 50})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Docker Hub tags fetch failed: {exc}",
        )

    tags = [r["name"] for r in data.get("results", []) if r.get("name")]
    return {"image": image, "tags": tags}


# ─── Staging job status ────────────────────────────────────────────────────────


@router.get("/jobs", response_model=list[StagingJob])
async def list_jobs(current_user: UserInfo = Depends(require_pull_access)):
    """Return all staging jobs visible to the current user."""
    if current_user.is_admin:
        return [StagingJob(**j) for j in jobs_list.values()]
    return [
        StagingJob(**j)
        for j in jobs_list.values()
        if j.get("owner") == current_user.username
    ]


@router.get("/jobs/{job_id}", response_model=StagingJob)
async def get_job(job_id: str, current_user: UserInfo = Depends(require_pull_access)):
    """Return a specific staging job."""
    if job_id not in jobs_list:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    job = jobs_list[job_id]
    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    return StagingJob(**job)


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str, current_user: UserInfo = Depends(require_pull_access)
):
    """Delete a staging job and its OCI layout directory."""
    if job_id not in jobs_list:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    job = jobs_list[job_id]
    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    try:
        oci_dir = safe_job_path(job_id)
        if oci_dir.exists():
            shutil.rmtree(oci_dir)
    except Exception as exc:
        _logger.warning("Failed to delete OCI dir for job %s: %s", job_id, exc)

    del jobs_list[job_id]
    return {"message": f"Job {job_id} deleted"}
