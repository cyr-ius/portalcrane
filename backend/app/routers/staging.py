"""
Portalcrane - Staging Router
Pipeline: skopeo copy (Docker Hub → OCI layout) → Trivy CVE scan (optional) → skopeo copy (OCI → Registry)
Replacing Docker CLI with skopeo removes the need for a Docker daemon socket.
"""

import asyncio
import json
import os
import shutil
import uuid
from enum import Enum

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from ..config import (
    DOCKERHUB_API_URL,
    HTTPX_TIMEOUT,
    REGISTRY_URL,
    STAGING_DIR,
    TRIVY_SERVER_URL,
    Settings,
    get_settings,
)
from .auth import (
    UserInfo,
    get_user_dockerhub_credentials,
    require_admin,
    require_pull_access,
    require_push_access,
)

router = APIRouter()

# In-memory job store (use Redis in production for multi-instance deployments)
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


class PullRequest(BaseModel):
    """Request to pull an image from Docker Hub via skopeo."""

    image: str
    tag: str = "latest"
    vuln_scan_enabled_override: bool | None = None
    vuln_severities_override: str | None = None


class PushRequest(BaseModel):
    """Request to push a staged image to the local or an external registry."""

    job_id: str
    target_image: str | None = None
    target_tag: str | None = None
    folder: str | None = None
    external_registry_id: str | None = None
    external_registry_host: str | None = None
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
    """Convert bytes to human-readable string."""
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


def _build_skopeo_dest_creds(settings: Settings) -> list[str]:
    """
    Return skopeo --dest-creds argument list when registry credentials are set.
    Returns an empty list when no credentials are configured.
    """
    if settings.registry_username and settings.registry_password:
        return [
            "--dest-creds",
            f"{settings.registry_username}:{settings.registry_password}",
        ]
    return []


def _resolve_push_host() -> str:
    """Resolve the registry push host."""
    from urllib.parse import urlparse

    return urlparse(REGISTRY_URL).netloc


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
    oci_dir = os.path.join(STAGING_DIR, job_id)

    _jobs[job_id]["status"] = JobStatus.PULLING
    _jobs[job_id]["message"] = f"Pulling {image}:{tag} from Docker Hub..."
    _jobs[job_id]["progress"] = 10

    # Build skopeo environment (proxy variables)
    skopeo_env = {**os.environ, **settings.env_proxy}

    do_vuln = _effective_vuln(settings, vuln_scan_enabled_override)
    severities = _effective_severities(settings, vuln_severities_override)

    try:
        # ── Pull: Docker Hub → OCI layout directory ───────────────────────────
        # skopeo copy stores the image as a standard OCI Image Layout.
        # This avoids a local Docker daemon entirely.
        skopeo_pull_cmd = [
            "skopeo",
            "copy",
            "--override-os",
            "linux",  # ensure a linux image is pulled
            *_build_skopeo_src_creds(current_user),
            # Destination is an OCI layout directory (no daemon needed)
            f"docker://{image}:{tag}",
            f"oci:{oci_dir}:latest",
        ]

        pull_proc = await asyncio.create_subprocess_exec(
            *skopeo_pull_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=skopeo_env,
        )
        _, stderr = await pull_proc.communicate()

        if pull_proc.returncode != 0:
            raise Exception(f"skopeo copy (pull) failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 50

        # ── Trivy CVE scan on the OCI layout directory ────────────────────────
        if do_vuln:
            _jobs[job_id]["status"] = JobStatus.VULN_SCANNING
            _jobs[job_id]["message"] = "Running Trivy CVE scan on OCI layout..."

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
                "/var/cache/trivy",
                "--timeout",
                settings.vuln_scan_timeout,
                "--input",
                oci_dir,
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

            if vuln_result.get("blocked"):
                _jobs[job_id]["status"] = JobStatus.SCAN_VULNERABLE
                _jobs[job_id]["message"] = (
                    f"Vulnerabilities found: {vuln_result['counts']}. "
                )
            else:
                _jobs[job_id]["status"] = JobStatus.SCAN_CLEAN
                _jobs[job_id]["message"] = (
                    "Trivy scan passed — no blocking vulnerabilities."
                )
        else:
            _jobs[job_id]["status"] = JobStatus.SCAN_SKIPPED
            _jobs[job_id]["message"] = "Vulnerability scan disabled. Ready to push."

        _jobs[job_id]["progress"] = 100

    except Exception as exc:
        # Cleanup the OCI directory on failure
        if os.path.exists(oci_dir):
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

    oci_dir = os.path.join(STAGING_DIR, job_id)
    push_host = _resolve_push_host()
    dest = f"docker://{push_host}/{target_image}:{target_tag}"

    # Build skopeo environment (proxy variables)
    skopeo_env = {**os.environ, **settings.env_proxy}

    # Determine whether the registry uses plain HTTP (e.g. localhost:5000)
    dest_tls_flag = (
        ["--dest-tls-verify=false"] if REGISTRY_URL.startswith("http://") else []
    )

    try:
        # ── Push: OCI layout directory → private registry ─────────────────────
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


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/pull", response_model=StagingJob)
async def pull_image(
    request: PullRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_pull_access),
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
    }
    background_tasks.add_task(
        run_pull_pipeline,
        job_id,
        request.image,
        request.tag,
        settings,
        _,
        request.vuln_scan_enabled_override,
        request.vuln_severities_override,
    )
    return StagingJob(**_jobs[job_id])


@router.post("/push")
async def push_image(
    request: PushRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_push_access),
):
    """
    Push a scanned image to the local registry or to an external registry.

    When external_registry_id or external_registry_host is set the image is
    pushed to the external destination via skopeo.  Otherwise it is pushed to
    the local registry (default behaviour).
    """
    if request.job_id not in _jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    job = _jobs[request.job_id]
    if job["status"] not in (
        JobStatus.SCAN_CLEAN,
        JobStatus.SCAN_SKIPPED,
        JobStatus.DONE,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image must pass scanning (or have scan skipped) before pushing",
        )

    # Validate folder path to prevent directory traversal
    from ..services.external_registry_service import (
        validate_folder_path,  # noqa: PLC0415
    )

    try:
        folder = validate_folder_path(request.folder or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    target_image = request.target_image or job["image"]
    target_tag = request.target_tag or job["tag"]

    is_external = bool(request.external_registry_id or request.external_registry_host)

    if is_external:
        # Delegate to the external registry push endpoint logic
        import os  # noqa: PLC0415

        from ..services.external_registry_service import (  # noqa: PLC0415
            build_target_path,
            get_registry_by_id,
            skopeo_push,
        )

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

        oci_dir = os.path.join(STAGING_DIR, request.job_id)
        if not os.path.isdir(oci_dir):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"OCI directory not found for job {request.job_id}",
            )

        dest_ref = build_target_path(folder, target_image, target_tag, host)

        # Run push in background so the endpoint returns immediately
        async def _ext_push():
            _jobs[request.job_id]["status"] = JobStatus.PUSHING
            _jobs[request.job_id]["message"] = f"Pushing to {dest_ref}…"
            _jobs[request.job_id]["progress"] = 10
            success, message = await skopeo_push(
                oci_dir=oci_dir,
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

    # Default: push to local registry
    # Build full target path including optional folder prefix
    if folder:
        full_image = f"{folder}/{target_image}"
    else:
        full_image = target_image

    background_tasks.add_task(
        run_push_pipeline, request.job_id, full_image, target_tag, settings
    )
    return {"message": "Push pipeline started", "job_id": request.job_id}


@router.get("/jobs/{job_id}", response_model=StagingJob)
async def get_job_status(
    job_id: str,
    _: UserInfo = Depends(require_pull_access),
):
    """Get the current status of a staging job."""
    if job_id not in _jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    return StagingJob(**_jobs[job_id])


@router.get("/jobs", response_model=list[StagingJob])
async def list_jobs(_: UserInfo = Depends(require_pull_access)):
    """List all staging jobs sorted: active first, then most recent."""
    active = {"pending", "pulling", "vuln_scanning", "pushing"}
    jobs = list(_jobs.values())
    jobs.sort(key=lambda j: (j["status"] not in active,), reverse=False)
    return [StagingJob(**j) for j in reversed(jobs)]


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    _: UserInfo = Depends(require_admin),
):
    """Delete a staging job and its associated OCI layout directory."""
    if job_id not in _jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    oci_dir = os.path.join(STAGING_DIR, job_id)
    if os.path.exists(oci_dir):
        shutil.rmtree(oci_dir, ignore_errors=True)

    del _jobs[job_id]
    return {"message": "Job deleted"}


@router.get("/search/dockerhub")
async def search_dockerhub(
    q: str,
    page: int = 1,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_pull_access),
):
    """
    Search Docker Hub for images matching the query string.

    Query params:
      q    -- search term (e.g. "nginx", "postgres")
      page -- pagination index, 1-based (default: 1)

    Returns:
      { results: [...], count: <total> }
    """
    url = f"{DOCKERHUB_API_URL}/search/repositories/?query={q}&page={page}&page_size=10"
    async with httpx.AsyncClient(
        proxy=settings.httpx_proxy, timeout=HTTPX_TIMEOUT
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
    _: UserInfo = Depends(require_pull_access),
):
    """
    Return available tags for a Docker Hub image, sorted by last update date.

    Path param:
      image -- image name, e.g. "nginx" or "library/nginx" or "myorg/myimage"

    Returns:
      { image: "<image>", tags: ["latest", "1.25", ...] }

    Falls back to ["latest"] on any Hub API error (image may not exist yet,
    or Hub may be temporarily unavailable).
    """
    # Official images are stored under the "library" namespace on Docker Hub
    if "/" not in image:
        hub_image = f"library/{image}"
    else:
        hub_image = image

    url = (
        f"{DOCKERHUB_API_URL}/repositories/{hub_image}/tags"
        f"?page_size=50&ordering=last_updated"
    )

    try:
        async with httpx.AsyncClient(
            proxy=settings.httpx_proxy, timeout=HTTPX_TIMEOUT
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                # Image not found on Docker Hub — return empty tag list
                return {"image": image, "tags": []}
            resp.raise_for_status()
            data = resp.json()

        tags = [t["name"] for t in data.get("results", []) if t.get("name")]
    except Exception:
        # Graceful degradation: always give the user something to work with
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

    if os.path.isdir(STAGING_DIR):
        for entry in os.scandir(STAGING_DIR):
            if entry.is_dir() and entry.name not in _jobs:
                orphans.append(entry.name)
                # Calculate directory size
                for dirpath, _, filenames in os.walk(entry.path):
                    for fname in filenames:
                        try:
                            total_size += os.path.getsize(os.path.join(dirpath, fname))
                        except OSError:
                            pass

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

    if os.path.isdir(STAGING_DIR):
        for entry in os.scandir(STAGING_DIR):
            if entry.is_dir() and entry.name not in _jobs:
                shutil.rmtree(entry.path, ignore_errors=True)
                purged.append(entry.name)

    return {"message": f"Purged {len(purged)} orphan OCI directories", "purged": purged}
