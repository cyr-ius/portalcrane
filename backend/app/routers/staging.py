"""
Portalcrane - Staging Router
Pipeline: skopeo copy (Docker Hub → OCI layout) → Trivy CVE scan (optional) → skopeo copy (OCI → Registry)
"""

import asyncio
import json
import logging
import os
import shutil
import uuid
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
from ..core.jwt import UserInfo, require_pull_access, require_push_access

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
    error: str | None = None
    vuln_scan_enabled_override: bool | None = None
    vuln_severities_override: str | None = None
    # Username of the user who created this job (used for per-user filtering)
    owner: str = ""


class PullRequest(BaseModel):
    """Request to pull an image from Docker Hub via skopeo."""

    image: str
    tag: str = "latest"
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
    if not oci_dir.is_relative_to(root):
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
        return httpx.BasicAuth(username=creds[0], password=creds[1])
    return None


def _resolve_push_host() -> str:
    """Resolve the registry push host from the configured REGISTRY_URL."""
    from urllib.parse import urlparse

    return urlparse(REGISTRY_URL).netloc


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
) -> None:
    """
    Background task: pull an image from Docker Hub into an OCI layout directory,
    optionally run a Trivy CVE scan, then wait for the user to trigger the push.

    Using skopeo instead of docker pull/save avoids the Docker daemon dependency.
    The OCI layout is stored at: <STAGING_DIR>/<job_id>/
    """
    # Top-level guard: any unhandled exception marks the job as FAILED and
    # logs the full traceback so it appears in container logs (docker logs).
    try:
        await _run_pull_pipeline_inner(
            job_id,
            image,
            tag,
            settings,
            current_user,
            vuln_scan_enabled_override,
            vuln_severities_override,
        )
    except Exception as exc:
        _logger.exception("Unhandled exception in pull pipeline for job %s", job_id)
        if job_id in _jobs:
            _jobs[job_id]["status"] = JobStatus.FAILED
            _jobs[job_id]["error"] = str(exc)
            _jobs[job_id]["message"] = f"Pull pipeline crashed: {exc}"
            _jobs[job_id]["progress"] = 100


async def _run_pull_pipeline_inner(
    job_id: str,
    image: str,
    tag: str,
    settings: Settings,
    current_user: UserInfo,
    vuln_scan_enabled_override: bool | None = None,
    vuln_severities_override: str | None = None,
) -> None:
    """Inner implementation of the pull pipeline — called by run_pull_pipeline."""
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
    _jobs[job_id]["message"] = f"Pulling {image}:{tag} from Docker Hub..."
    _jobs[job_id]["progress"] = 10

    # Build skopeo environment (proxy variables)
    skopeo_env = {**os.environ, **settings.env_proxy}

    do_vuln = _effective_vuln(settings, vuln_scan_enabled_override)
    severities = _effective_severities(settings, vuln_severities_override)

    # Resolve user Docker Hub credentials for authenticated pulls
    src_creds = _build_skopeo_src_creds(current_user)

    _logger.info("Starting pull pipeline for job %s: %s:%s", job_id, image, tag)
    _logger.debug("skopeo src_creds present: %s", bool(src_creds))

    try:
        # ── Pull: Docker Hub → OCI layout directory ───────────────────────────
        skopeo_pull_cmd = [
            "skopeo",
            "copy",
            "--override-os",
            "linux",
            *_build_skopeo_src_creds(current_user),
            f"docker://{image}:{tag}",
            f"oci:{oci_dir}:latest",
        ]

        _logger.info("Executing skopeo: %s", " ".join(skopeo_pull_cmd))

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
        _jobs[job_id]["message"] = "Image pulled. Running scan..."
        _logger.info(
            "Pull OK for job %s — do_vuln=%s severities=%s", job_id, do_vuln, severities
        )

        # ── Vulnerability scan (Trivy) ────────────────────────────────────────
        if do_vuln:
            _jobs[job_id]["status"] = JobStatus.VULN_SCANNING
            _jobs[job_id]["message"] = "Running Trivy vulnerability scan..."
            _jobs[job_id]["progress"] = 60

            severity_arg = ",".join(severities)
            trivy_cmd = [
                "trivy",
                "image",
                "--format",
                "json",
                "--server",
                TRIVY_SERVER_URL,
                "--severity",
                severity_arg,
                "--cache-dir",
                TRIVY_CACHE_DIR,
                "--timeout",
                settings.vuln_scan_timeout,
                "--input",
                # Pass as string — trivy CLI does not accept Path objects
                str(oci_dir),
            ]

            if settings.vuln_ignore_unfixed:
                trivy_cmd.append("--ignore-unfixed")

            trivy_proc = await asyncio.create_subprocess_exec(
                *trivy_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            trivy_out, trivy_err = await trivy_proc.communicate()

            if trivy_proc.returncode not in (0, 1):
                raise Exception(
                    f"Trivy scan failed (exit {trivy_proc.returncode}): "
                    f"{trivy_err.decode() or trivy_out.decode()}"
                )

            vuln_result = _parse_trivy_output(trivy_out, severities)
            _jobs[job_id]["vuln_result"] = vuln_result

            if vuln_result["blocked"]:
                _jobs[job_id]["status"] = JobStatus.SCAN_VULNERABLE
                _jobs[job_id]["message"] = "⚠️ Vulnerabilities found: " + ", ".join(
                    f"{k}:{v}" for k, v in vuln_result["counts"].items()
                )
            else:
                _jobs[job_id]["status"] = JobStatus.SCAN_CLEAN
                _jobs[job_id]["message"] = (
                    "Trivy scan passed — no blocking vulnerabilities."
                )
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
) -> None:
    """
    Background task: push an OCI layout directory to the private registry via skopeo.
    The source OCI directory is cleaned up after a successful push.
    """
    _jobs[job_id]["status"] = JobStatus.PUSHING
    _jobs[job_id]["message"] = f"Pushing to registry as {target_image}:{target_tag}..."
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
    dest = f"docker://{push_host}/{target_image}:{target_tag}"

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
    """Start a pull+scan pipeline for a Docker Hub image using skopeo."""
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
        "error": None,
        "vuln_scan_enabled_override": request.vuln_scan_enabled_override,
        "vuln_severities_override": request.vuln_severities_override,
        # Tag the job with the requesting user so the list can be filtered per user
        "owner": current_user.username,
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

    if not current_user.is_admin and job.get("owner") != current_user.username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    try:
        oci_dir = _safe_job_path(request.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    target_image = request.target_image or job["image"].split("/")[-1]
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
            host = request.external_registry_host
            username = request.external_registry_username or ""
            password = request.external_registry_password or ""

        if not oci_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"OCI directory not found for job {request.job_id}",
            )

        if not host:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Host registry empty {request.job_id}",
            )

        dest_ref = build_target_path(folder, target_image, target_tag, host)

        async def _ext_push() -> None:
            _jobs[request.job_id]["status"] = JobStatus.PUSHING
            _jobs[request.job_id]["message"] = f"Pushing to {dest_ref}…"
            _jobs[request.job_id]["progress"] = 10
            success, message = await skopeo_push(
                oci_dir=str(oci_dir),
                dest_ref=dest_ref,
                dest_username=username,
                dest_password=password,
                settings=settings,
            )
            if success:
                _jobs[request.job_id]["status"] = JobStatus.DONE
                _jobs[request.job_id]["message"] = f"✅ Pushed to {dest_ref}"
                _jobs[request.job_id]["target_image"] = f"{host}/{target_image}"
                _jobs[request.job_id]["target_tag"] = target_tag
            else:
                _jobs[request.job_id]["status"] = JobStatus.FAILED
                _jobs[request.job_id]["message"] = f"Push failed: {message}"
            _jobs[request.job_id]["progress"] = 100

        background_tasks.add_task(_ext_push)
        return {"message": "External push started", "job_id": request.job_id}

    full_image = f"{folder}/{target_image}" if folder else target_image

    background_tasks.add_task(
        run_push_pipeline, request.job_id, full_image, target_tag, settings
    )
    return {"message": "Push pipeline started", "job_id": request.job_id}


@router.get("/jobs/{job_id}", response_model=StagingJob)
async def get_job_status(
    job_id: str,
    current_user: UserInfo = Depends(require_pull_access),
):
    """
    Get the current status of a staging job.
    Non-admin users can only query their own jobs.
    """
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


@router.get("/jobs", response_model=list[StagingJob])
async def list_jobs(current_user: UserInfo = Depends(require_pull_access)):
    """
    List staging jobs: active first, then most recent.
    Admins see all jobs; regular users see only their own.
    """
    active = {"pending", "pulling", "vuln_scanning", "pushing"}
    jobs = list(_jobs.values())

    if not current_user.is_admin:
        jobs = [j for j in jobs if j.get("owner") == current_user.username]

    jobs.sort(key=lambda j: (j["status"] not in active,), reverse=False)
    return [StagingJob(**j) for j in reversed(jobs)]


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user: UserInfo = Depends(require_pull_access),
):
    """
    Delete a staging job and its associated OCI layout directory.
    Admins can delete any job; regular users can only delete their own.
    """
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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if oci_dir.exists():
        shutil.rmtree(oci_dir, ignore_errors=True)
    del _jobs[job_id]
    return {"message": "Job deleted"}


@router.get("/search/dockerhub")
async def search_dockerhub(
    q: str,
    page: int = 1,
    settings: Settings = Depends(get_settings),
    current_user: UserInfo = Depends(require_pull_access),
):
    """
    Search Docker Hub for images matching the query string.
    Uses the requesting user's Docker Hub credentials when configured.
    """
    url = f"{DOCKERHUB_API_URL}/search/repositories/?query={q}&page={page}&page_size=10"
    auth = _build_dockerhub_auth(current_user.username)
    async with httpx.AsyncClient(
        proxy=settings.httpx_proxy, timeout=HTTPX_TIMEOUT, auth=auth
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
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
    settings: Settings = Depends(get_settings),
    current_user: UserInfo = Depends(require_pull_access),
):
    """
    Return available tags for a Docker Hub image, sorted by last update date.
    Uses the requesting user's Docker Hub credentials when configured.
    """
    hub_image = f"library/{image}" if "/" not in image else image
    url = (
        f"{DOCKERHUB_API_URL}/repositories/{hub_image}/tags"
        f"?page_size=50&ordering=last_updated"
    )
    auth = _build_dockerhub_auth(current_user.username)
    try:
        async with httpx.AsyncClient(
            proxy=settings.httpx_proxy, timeout=HTTPX_TIMEOUT, auth=auth
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return {"image": image, "tags": []}
            resp.raise_for_status()
            data = resp.json()
        tags = [t["name"] for t in data.get("results", []) if t.get("name")]
    except Exception:
        tags = ["latest"]
    return {"image": image, "tags": tags}


@router.get("/orphan-oci", response_model=OrphanOCIResult)
async def list_orphan_oci(
    _: UserInfo = Depends(require_admin),
):
    """
    List orphan OCI layout directories in the staging directory.
    These are directories without a corresponding in-memory job
    (e.g. left behind after a restart).
    """
    orphans: list[str] = []
    total_size = 0
    staging_root = _staging_root()
    if staging_root.is_dir():
        for entry in staging_root.iterdir():
            if entry.is_dir() and entry.name not in _jobs:
                orphans.append(entry.name)
                total_size += sum(
                    f.stat().st_size for f in entry.rglob("*") if f.is_file()
                )
    return OrphanOCIResult(
        dirs=orphans,
        count=len(orphans),
        total_size_bytes=total_size,
        total_size_human=_human_size(total_size),
    )


@router.delete("/orphan-oci")
async def purge_orphan_oci(
    _: UserInfo = Depends(require_admin),
):
    """Purge orphan OCI layout directories from the staging directory."""
    purged: list[str] = []
    staging_root = _staging_root()
    if staging_root.is_dir():
        for entry in staging_root.iterdir():
            if entry.is_dir() and entry.name not in _jobs:
                shutil.rmtree(entry, ignore_errors=True)
                purged.append(entry.name)
    return {"message": f"Purged {len(purged)} orphan OCI directories", "purged": purged}
