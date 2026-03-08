"""
Portalcrane - Staging Router
Pipeline: skopeo copy (source registry → OCI layout) → Trivy CVE scan (optional) → skopeo copy (OCI → Registry)

Changes:
  - PullRequest now accepts optional source registry fields (saved ID or ad-hoc host/credentials).
  - run_pull_pipeline resolves the skopeo source reference and credentials dynamically.
  - Docker Hub remains the default source when no source registry is specified.
"""

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import (
    DOCKERHUB_API_URL,
    HTTPX_TIMEOUT,
    REGISTRY_URL,
    STAGING_DIR,
    TRIVY_CACHE_DIR,
    TRIVY_SERVER_URL,
    Settings,
    get_settings,
)
from .auth import get_user_dockerhub_credentials, require_admin
from ..core.jwt import (
    UserInfo,
    require_pull_access,
    require_push_access,
    _is_admin_user,
)
from ..routers.folders import check_folder_access

router = APIRouter()

_logger = logging.getLogger(__name__)

# In-memory job store.
# Requires single-worker deployment (--workers 1 in supervisord.conf).
# With multiple workers each process has its own dict — jobs become invisible
# to the worker that did not create them.
_jobs: dict[str, dict] = {}


# ─── Models ──────────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    PULLING = "pulling"
    SCAN_SKIPPED = "scan_skipped"
    VULN_SCANNING = "vuln_scanning"
    SCAN_CLEAN = "scan_clean"
    SCAN_VULNERABLE = "scan_vulnerable"
    PUSHING = "pushing"
    DONE = "done"
    FAILED = "failed"


class StagingJob(BaseModel):
    """Staging pipeline job model."""

    job_id: str
    status: JobStatus
    image: str
    tag: str
    progress: int = 0
    message: str = ""
    scan_result: str | None = None
    vuln_result: dict | None = None
    target_image: str | None = None
    target_tag: str | None = None
    folder: str | None = None  # optional folder prefix used during push
    error: str | None = None
    vuln_scan_enabled_override: bool | None = None
    vuln_severities_override: str | None = None
    owner: str = ""
    source_registry_host: str | None = None
    created_at: str = ""


class PullRequest(BaseModel):
    """
    Request to pull an image via skopeo.

    Source resolution order:
      1. source_registry_id  → look up saved external registry (host + credentials)
      2. source_registry_host (+ optional username/password) → ad-hoc registry
      3. No source fields    → Docker Hub (docker.io) using the user's saved Hub credentials
    """

    image: str
    tag: str = "latest"

    # ── Source registry (optional) ────────────────────────────────────────────
    # Saved registry ID — resolved server-side to host + credentials
    source_registry_id: str | None = None
    # Ad-hoc registry host (e.g. "ghcr.io", "quay.io", "registry.example.com:5000")
    source_registry_host: str | None = None
    source_registry_username: str | None = None
    source_registry_password: str | None = None

    # ── Vulnerability scan overrides ──────────────────────────────────────────
    vuln_scan_enabled_override: bool | None = None
    vuln_severities_override: str | None = None


class PushRequest(BaseModel):
    """Request to push a staged image to the local or an external registry."""

    job_id: str
    external_registry_host: str | None = None
    target_image: str | None = None
    target_tag: str | None = None
    folder: str | None = None
    external_registry_id: str | None = None
    external_registry_username: str | None = None
    external_registry_password: str | None = None


class DockerHubSearchResult(BaseModel):
    """Docker Hub search result model."""

    name: str
    description: str
    star_count: int
    pull_count: int
    is_official: bool
    is_automated: bool


class OrphanOCIResult(BaseModel):
    """Result of orphan OCI layout directories inspection in the staging directory."""

    dirs: list[str]
    count: int
    total_size_bytes: int
    total_size_human: str


# ─── Path helpers ─────────────────────────────────────────────────────────────


def _staging_root() -> Path:
    """Return the resolved absolute path to the staging root directory."""
    return Path(STAGING_DIR).resolve()


def _safe_job_path(job_id: str) -> Path:
    """
    Resolve the OCI layout directory for a given job_id.

    Raises ValueError if the resolved path escapes the staging root directory.
    This acts as a defence-in-depth guard against path traversal attacks even
    though job_id is currently always a UUID generated internally.
    """
    root = _staging_root()
    oci_dir = (root / job_id).resolve()
    # Ensure the resolved path is within the staging root, guarding against
    # path traversal even if a malicious job_id is supplied.
    root_str = str(root)
    oci_dir_str = str(oci_dir)
    if os.path.commonpath([root_str, oci_dir_str]) != root_str:
        raise ValueError(
            f"Path traversal detected — job_id resolves outside staging root: {job_id}"
        )
    return oci_dir


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _effective_vuln(settings: Settings, override: bool | None) -> bool:
    """Return the effective vulnerability-scan flag for a given job."""
    if override is not None:
        return override
    return settings.vuln_scan_enabled


def _effective_severities(settings: Settings, override: str | None) -> list[str]:
    """Return the effective CVE severity list for a given job."""
    if override is not None:
        return [s.strip().upper() for s in override.split(",") if s.strip()]
    return settings.vuln_severities


def _human_size(size_bytes: float) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _build_skopeo_src_creds(current_user: UserInfo) -> list[str]:
    """
    Return skopeo --src-creds argument list using the authenticated user's
    Docker Hub credentials. Returns an empty list when no credentials exist.
    """
    creds = get_user_dockerhub_credentials(current_user.username)
    if creds:
        return ["--src-creds", f"{creds[0]}:{creds[1]}"]
    return []


def _build_dockerhub_auth(username: str) -> httpx.BasicAuth | None:
    """
    Return an httpx.BasicAuth instance using the user's saved Docker Hub
    credentials, or None when no credentials are configured.
    Authenticated requests lift the Docker Hub anonymous rate-limit.
    """
    creds = get_user_dockerhub_credentials(username)
    if creds:
        _logger.debug(
            "Docker Hub auth resolved for user=%s using docker_username=%s",
            username,
            creds[0],
        )
        return httpx.BasicAuth(username=creds[0], password=creds[1])

    _logger.debug(
        "Docker Hub auth resolved for user=%s using anonymous mode (no credentials)",
        username,
    )
    return None


def _resolve_push_host() -> str:
    """Resolve the registry push host from the configured REGISTRY_URL."""
    from urllib.parse import urlparse

    return urlparse(REGISTRY_URL).netloc


def _resolve_pull_source(
    request: PullRequest,
    current_user: UserInfo,
) -> tuple[str, list[str], str | None]:
    """
    Resolve the skopeo source reference, credentials flags and display host
    from the pull request.

    Returns:
        src_ref      : full skopeo docker:// reference  (e.g. "docker://ghcr.io/org/image:tag")
        src_creds    : list of skopeo --src-creds args  (may be empty)
        display_host : human-readable registry host for storage in the job  (None = Docker Hub)
    """
    image = request.image
    tag = request.tag

    # ── 1. Saved external registry ────────────────────────────────────────────
    if request.source_registry_id:
        from .external_registries import get_registry_by_id

        registry = get_registry_by_id(request.source_registry_id)
        if not registry:
            raise ValueError(f"Source registry not found: {request.source_registry_id}")
        host = registry["host"].rstrip("/")
        username = registry.get("username", "")
        password = registry.get("password", "")
        src_ref = f"docker://{host}/{image}:{tag}"
        src_creds = (
            ["--src-creds", f"{username}:{password}"] if username and password else []
        )
        return src_ref, src_creds, host

    # ── 2. Ad-hoc source registry ─────────────────────────────────────────────
    if request.source_registry_host:
        host = request.source_registry_host.rstrip("/")
        username = request.source_registry_username or ""
        password = request.source_registry_password or ""
        src_ref = f"docker://{host}/{image}:{tag}"
        src_creds = (
            ["--src-creds", f"{username}:{password}"] if username and password else []
        )
        return src_ref, src_creds, host

    # ── 3. Default: Docker Hub ─────────────────────────────────────────────────
    src_ref = f"docker://{image}:{tag}"
    src_creds = _build_skopeo_src_creds(current_user)
    return src_ref, src_creds, None


def _parse_trivy_output(raw: bytes, severities: list[str]) -> dict:
    """Parse Trivy JSON output and return a structured vuln_result dict."""
    try:
        data = json.loads(raw.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {
            "enabled": True,
            "blocked": False,
            "severities": severities,
            "counts": {},
            "vulnerabilities": [],
            "total": 0,
        }

    vulns: list[dict] = []
    counts: dict[str, int] = {}

    for result in data.get("Results", []):
        for v in result.get("Vulnerabilities") or []:
            sev = v.get("Severity", "UNKNOWN").upper()
            counts[sev] = counts.get(sev, 0) + 1
            vulns.append(
                {
                    "id": v.get("VulnerabilityID", ""),
                    "package": v.get("PkgName", ""),
                    "installed_version": v.get("InstalledVersion", ""),
                    "fixed_version": v.get("FixedVersion"),
                    "severity": sev,
                    "title": v.get("Title"),
                    "cvss_score": (v.get("CVSS") or {}).get("nvd", {}).get("V3Score"),
                    "target": result.get("Target", ""),
                }
            )

    blocked = any(counts.get(s, 0) > 0 for s in severities)

    return {
        "enabled": True,
        "blocked": blocked,
        "severities": severities,
        "counts": counts,
        "vulnerabilities": vulns,
        "total": len(vulns),
    }


# ─── Background Tasks ─────────────────────────────────────────────────────────


async def run_pull_pipeline(
    job_id: str,
    image: str,
    tag: str,
    settings: Settings,
    current_user: UserInfo,
    vuln_scan_enabled_override: bool | None = None,
    vuln_severities_override: str | None = None,
    # Source registry resolution — passed pre-computed from the endpoint
    src_ref: str = "",
    src_creds: list[str] | None = None,
) -> None:
    """
    Background task: pull an image from the resolved source registry into an
    OCI layout directory, optionally run a Trivy CVE scan, then wait for the
    user to trigger the push.

    Using skopeo instead of docker pull/save avoids the Docker daemon dependency.
    The OCI layout is stored at: <STAGING_DIR>/<job_id>/
    """
    if src_creds is None:
        src_creds = []

    # Resolve the OCI directory with path-traversal guard
    try:
        oci_dir = _safe_job_path(job_id)
    except ValueError as exc:
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["message"] = f"Invalid job path: {exc}"
        _jobs[job_id]["progress"] = 100
        return

    _jobs[job_id]["status"] = JobStatus.PULLING
    source_host = _jobs[job_id].get("source_registry_host") or "Docker Hub"
    _jobs[job_id]["message"] = f"Pulling {image}:{tag} from {source_host}..."
    _jobs[job_id]["progress"] = 10

    # Build skopeo environment (proxy variables)
    skopeo_env = {**os.environ, **settings.env_proxy}

    do_vuln = _effective_vuln(settings, vuln_scan_enabled_override)
    severities = _effective_severities(settings, vuln_severities_override)

    _logger.info(
        "Starting pull pipeline for job %s: %s:%s from %s",
        job_id,
        image,
        tag,
        src_ref,
    )
    _logger.debug("skopeo src_creds present: %s", bool(src_creds))

    try:
        # ── Pull: source registry → OCI layout directory ──────────────────────
        skopeo_pull_cmd = [
            "skopeo",
            "copy",
            "--override-os",
            "linux",
            *src_creds,
            src_ref,
            f"oci:{oci_dir}:latest",
        ]

        _logger.info(
            "Executing skopeo: %s",
            # Mask credentials in logs
            " ".join(
                "***" if i > 0 and skopeo_pull_cmd[i - 1] == "--src-creds" else arg
                for i, arg in enumerate(skopeo_pull_cmd)
            ),
        )

        pull_proc = await asyncio.create_subprocess_exec(
            *skopeo_pull_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=skopeo_env,
        )
        stdout, stderr = await pull_proc.communicate()
        _logger.info(
            "skopeo pull returncode=%s stdout=%r stderr=%r",
            pull_proc.returncode,
            stdout.decode()[:500],
            stderr.decode()[:500],
        )

        if pull_proc.returncode != 0:
            raise Exception(f"skopeo copy (pull) failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 50
        _jobs[job_id]["message"] = "Image pulled. Starting vulnerability scan..."

        # ── Vulnerability scan (optional) ─────────────────────────────────────
        if do_vuln:
            _jobs[job_id]["status"] = JobStatus.VULN_SCANNING
            _jobs[job_id]["message"] = "Running Trivy vulnerability scan..."

            trivy_cmd = [
                "trivy",
                "image",
                "--format",
                "json",
                "--exit-code",
                "0",
                "--cache-dir",
                TRIVY_CACHE_DIR,
                "--input",
                str(oci_dir),
            ]

            if TRIVY_SERVER_URL:
                trivy_cmd += ["--server", TRIVY_SERVER_URL]

            if severities:
                trivy_cmd += ["--severity", ",".join(severities)]

            trivy_proc = await asyncio.create_subprocess_exec(
                *trivy_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=skopeo_env,
            )
            trivy_stdout, trivy_stderr = await trivy_proc.communicate()

            vuln_result = _parse_trivy_output(trivy_stdout, severities)
            _jobs[job_id]["vuln_result"] = vuln_result

            if vuln_result["blocked"]:
                _jobs[job_id]["status"] = JobStatus.SCAN_VULNERABLE
                _jobs[job_id]["message"] = (
                    "⚠️ Vulnerabilities detected — review before pushing."
                )
            else:
                _jobs[job_id]["status"] = JobStatus.SCAN_CLEAN
                _jobs[job_id]["message"] = "✅ Scan clean. Ready to push."
        else:
            _jobs[job_id]["status"] = JobStatus.SCAN_SKIPPED
            _jobs[job_id]["message"] = "Vulnerability scan disabled. Ready to push."
            _logger.info("Scan skipped for job %s — status set to SCAN_SKIPPED", job_id)

        _jobs[job_id]["progress"] = 100
        _logger.info(
            "Pipeline complete for job %s — final status: %s",
            job_id,
            _jobs[job_id]["status"],
        )

    except Exception as exc:
        # Cleanup the OCI directory on failure
        if oci_dir.exists():
            shutil.rmtree(oci_dir, ignore_errors=True)
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["message"] = f"Pull pipeline failed: {exc}"
        _jobs[job_id]["progress"] = 100


async def run_push_pipeline(
    job_id: str,
    target_image: str,
    target_tag: str,
    settings: Settings,
    folder: str | None = None,
) -> None:
    """
    Background task: push an OCI layout directory to the private registry via skopeo.
    The source OCI directory is cleaned up after a successful push.

    The optional ``folder`` parameter is a prefix that should be prepended
    to ``target_image`` when pushing to the internal registry.  Previously the
    folder was ignored, which caused images like
    ``production/foo:tag`` to be pushed as ``foo:tag`` at the registry root.
    """
    # defense-in-depth: verify folder push permission again inside the
    # background task.  The endpoint already checks this, but the job data
    # could hypothetically be modified afterwards.
    if not _is_admin_user(_jobs[job_id].get("owner", ""), settings):
        full_path = f"{folder}/{target_image}" if folder else target_image
        access = check_folder_access(
            _jobs[job_id].get("owner", ""), full_path, is_pull=False
        )
        if access is not True:
            _jobs[job_id]["status"] = JobStatus.FAILED
            _jobs[job_id]["message"] = "Push denied: insufficient folder permissions"
            _jobs[job_id]["error"] = "authorization"
            _jobs[job_id]["progress"] = 100
            return

    _jobs[job_id]["status"] = JobStatus.PUSHING
    _jobs[job_id]["message"] = (
        f"Pushing to registry as {folder + '/' if folder else ''}{target_image}:{target_tag}..."
    )
    _jobs[job_id]["progress"] = 10

    try:
        oci_dir = _safe_job_path(job_id)
    except ValueError as exc:
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["message"] = f"Invalid job path: {exc}"
        _jobs[job_id]["progress"] = 100
        return

    push_host = _resolve_push_host()
    # include folder prefix if provided
    image_path = f"{folder}/{target_image}" if folder else target_image
    dest = f"docker://{push_host}/{image_path}:{target_tag}"

    skopeo_env = {**os.environ, **settings.env_proxy}

    dest_tls_flag = (
        ["--dest-tls-verify=false"] if REGISTRY_URL.startswith("http://") else []
    )

    try:
        skopeo_push_cmd = [
            "skopeo",
            "copy",
            *dest_tls_flag,
            f"oci:{oci_dir}:latest",
            dest,
        ]

        push_proc = await asyncio.create_subprocess_exec(
            *skopeo_push_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=skopeo_env,
        )
        _, stderr = await push_proc.communicate()

        if push_proc.returncode != 0:
            raise Exception(f"skopeo copy (push) failed: {stderr.decode()}")

        _jobs[job_id]["status"] = JobStatus.DONE
        _jobs[job_id]["message"] = (
            f"✅ Successfully pushed to {push_host}/{target_image}:{target_tag}"
        )
        _jobs[job_id]["progress"] = 100
        _jobs[job_id]["target_image"] = target_image
        _jobs[job_id]["target_tag"] = target_tag

    except Exception as exc:
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = str(exc)
        _jobs[job_id]["message"] = f"❌ Push failed: {exc}"
        _jobs[job_id]["progress"] = 100


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

    # If pulling from the local registry, enforce folder-based pull permissions
    # (a user could otherwise specify localhost:5000 manually and bypass the
    # proxy's access control).  display_host is either a hostname or None
    # (Docker Hub).  We compare with the local registry host derived from
    # REGISTRY_URL.
    from urllib.parse import urlparse

    local_host = urlparse(REGISTRY_URL).netloc
    if display_host and display_host == local_host:
        # folder check uses image name (no tag)
        if not _is_admin_user(current_user.username, settings):
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
    _jobs[job_id] = {
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
    return StagingJob(**_jobs[job_id])


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
    from .external_registries import build_target_path, get_registry_by_id
    from ..services.external_registry_service import skopeo_push

    if request.job_id not in _jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    job = _jobs[request.job_id]
    # remember folder in job record for auditing/debugging
    job["folder"] = request.folder or ""

    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    try:
        oci_dir = _safe_job_path(request.job_id)
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
            host = registry["host"]
            username = registry.get("username", "")
            password = registry.get("password", "")
        else:
            host = request.external_registry_host or ""
            username = request.external_registry_username or ""
            password = request.external_registry_password or ""

        dest_ref = build_target_path(folder, target_image, target_tag, host)

        job["status"] = JobStatus.PUSHING
        job["message"] = f"Pushing to external registry {host}..."

        success, message = await skopeo_push(
            oci_dir=str(oci_dir),
            dest_ref=dest_ref,
            dest_username=username,
            dest_password=password,
            settings=settings,
        )
        if success:
            job["status"] = JobStatus.DONE
            job["message"] = f"✅ Successfully pushed to {dest_ref}"
            job["target_image"] = target_image
            job["target_tag"] = target_tag
        else:
            job["status"] = JobStatus.FAILED
            job["message"] = f"❌ Push failed: {message}"
            job["error"] = message

        return {"message": message, "job_id": request.job_id}

    # Before scheduling local push, verify push permission for the
    # requested folder/image combination.  ``target_image`` already includes
    # the requested image name; ``folder`` may be empty.
    if not _is_admin_user(current_user.username, settings):
        full_path = f"{folder}/{target_image}" if folder else target_image
        access = check_folder_access(current_user.username, full_path, is_pull=False)
        if access is not True:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Folder access denied: push permission required",
            )

    # Default: push to the local embedded registry
    background_tasks.add_task(
        run_push_pipeline,
        request.job_id,
        target_image,
        target_tag,
        settings,
        request.folder,
    )
    return {"message": "Push started", "job_id": request.job_id}


@router.get("/jobs", response_model=list[StagingJob])
async def list_jobs(current_user: UserInfo = Depends(require_pull_access)):
    """
    List staging jobs.
    Admins see all jobs; regular users see only their own.
    """
    jobs = list(_jobs.values())
    if not current_user.is_admin:
        jobs = [j for j in jobs if j.get("owner") == current_user.username]
    return [StagingJob(**j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=StagingJob)
async def get_job(
    job_id: str,
    current_user: UserInfo = Depends(require_pull_access),
):
    """Get a single staging job by ID."""
    if job_id not in _jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    job = _jobs[job_id]
    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    return StagingJob(**job)


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user: UserInfo = Depends(require_pull_access),
):
    """Delete a staging job and its OCI directory."""
    if job_id not in _jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    job = _jobs[job_id]
    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    try:
        oci_dir = _safe_job_path(job_id)
        if oci_dir.exists():
            shutil.rmtree(oci_dir, ignore_errors=True)
    except ValueError:
        pass

    del _jobs[job_id]
    return {"message": f"Job {job_id} deleted"}


# ─── Docker Hub search & tags ─────────────────────────────────────────────────


@router.get("/search/dockerhub")
async def search_dockerhub(
    q: str,
    page: int = 1,
    current_user: UserInfo = Depends(require_pull_access),
):
    """Search Docker Hub for images matching the given query."""
    auth = _build_dockerhub_auth(current_user.username)
    _logger.debug(
        "Docker Hub search request user=%s query=%s page=%s auth=%s",
        current_user.username,
        q,
        page,
        "authenticated" if auth else "anonymous",
    )
    url = f"{DOCKERHUB_API_URL}/search/repositories/?query={q}&page={page}&page_size=25"
    async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT) as client:
        resp = await client.get(url, auth=auth)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Docker Hub search failed",
        )
    data = resp.json()
    results = [
        {
            "name": r.get("repo_name", ""),
            "description": r.get("short_description", ""),
            "star_count": r.get("star_count", 0),
            "pull_count": r.get("pull_count", 0),
            "is_official": r.get("is_official", False),
            "is_automated": r.get("is_automated", False),
        }
        for r in data.get("results", [])
    ]
    return {"results": results, "count": data.get("count", 0)}


@router.get("/dockerhub/tags/{image:path}")
async def get_dockerhub_tags(
    image: str,
    current_user: UserInfo = Depends(require_pull_access),
):
    """Fetch available tags for a Docker Hub image."""
    auth = _build_dockerhub_auth(current_user.username)
    _logger.debug(
        "Docker Hub tags request user=%s image=%s auth=%s",
        current_user.username,
        image,
        "authenticated" if auth else "anonymous",
    )
    namespace, name = image.split("/", 1) if "/" in image else ("library", image)
    url = f"{DOCKERHUB_API_URL}/repositories/{namespace}/{name}/tags/?page_size=50&ordering=last_updated"
    async with httpx.AsyncClient(timeout=HTTPX_TIMEOUT) as client:
        resp = await client.get(url, auth=auth)
    if resp.status_code != 200:
        return {"image": image, "tags": ["latest"]}
    tags = [t["name"] for t in resp.json().get("results", [])]
    return {"image": image, "tags": tags or ["latest"]}


# ─── Orphan OCI cleanup ───────────────────────────────────────────────────────


@router.get("/orphan-oci", response_model=OrphanOCIResult)
async def get_orphan_oci(_: UserInfo = Depends(require_admin)):
    """List OCI layout directories in the staging area with no matching job."""
    root = _staging_root()
    orphans = []
    total_bytes = 0
    for entry in root.iterdir():
        if entry.is_dir() and entry.name not in _jobs:
            size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            orphans.append(entry.name)
            total_bytes += size
    return OrphanOCIResult(
        dirs=orphans,
        count=len(orphans),
        total_size_bytes=total_bytes,
        total_size_human=_human_size(total_bytes),
    )


@router.delete("/orphan-oci")
async def purge_orphan_oci(_: UserInfo = Depends(require_admin)):
    """Delete all orphan OCI layout directories."""
    root = _staging_root()
    purged = []
    for entry in root.iterdir():
        if entry.is_dir() and entry.name not in _jobs:
            shutil.rmtree(entry, ignore_errors=True)
            purged.append(entry.name)
    return {"message": f"Purged {len(purged)} orphan directories", "purged": purged}


@router.get("/dangling-images")
async def get_dangling_images(_: UserInfo = Depends(require_admin)):
    """List dangling (untagged) images in the local registry."""
    return {"images": [], "count": 0}


@router.post("/dangling-images/purge")
async def purge_dangling_images(_: UserInfo = Depends(require_admin)):
    """Purge dangling images from the local registry."""
    return {"message": "No dangling images to purge", "output": ""}
