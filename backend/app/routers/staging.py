"""
Portalcrane - Staging Router
Pipeline: Pull from Docker Hub → ClamAV Scan (optional) → Trivy CVE Scan (optional) → Push to Registry
"""

import asyncio
import json
import os
import socket
import uuid
from enum import Enum

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from ..config import Settings, get_settings
from .auth import UserInfo, get_current_user

router = APIRouter()

# In-memory job store (use Redis in production for multi-instance)
_jobs: dict[str, dict] = {}


# ─── Models ──────────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    PULLING = "pulling"
    SCANNING = "scanning"
    SCAN_SKIPPED = "scan_skipped"  # ClamAV disabled for this job
    VULN_SCANNING = "vuln_scanning"
    SCAN_CLEAN = "scan_clean"
    SCAN_VULNERABLE = "scan_vulnerable"
    SCAN_INFECTED = "scan_infected"
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
    # Per-job scan overrides (null = use server default)
    clamav_enabled_override: bool | None = None
    vuln_scan_enabled_override: bool | None = None
    vuln_severities_override: str | None = None


class PullRequest(BaseModel):
    """Request to pull an image from Docker Hub."""

    image: str
    tag: str = "latest"
    # Optional per-job overrides — only honoured when advanced mode is active server-side
    clamav_enabled_override: bool | None = None
    vuln_scan_enabled_override: bool | None = None
    vuln_severities_override: str | None = None


class PushRequest(BaseModel):
    """Request to push a staged image to the registry."""

    job_id: str
    target_image: str | None = None  # Optional rename
    target_tag: str | None = None  # Optional retag


class DockerHubSearchResult(BaseModel):
    """Docker Hub search result model."""

    name: str
    description: str
    star_count: int
    pull_count: int
    is_official: bool
    is_automated: bool


class ClamAVStatus(BaseModel):
    """ClamAV daemon reachability status."""

    enabled: bool  # whether ClamAV scanning is enabled in server config
    reachable: bool  # whether the daemon TCP socket is reachable right now
    host: str
    port: int
    message: str


class DanglingImagesResult(BaseModel):
    """Result of dangling images inspection on the host Docker daemon."""

    images: list[dict]
    count: int


class OrphanTarballsResult(BaseModel):
    """Result of orphan .tar files inspection in the staging directory."""

    files: list[str]
    count: int
    total_size_bytes: int
    total_size_human: str


# ─── Override helpers ────────────────────────────────────────────────────────


def _effective_clamav(settings: Settings, override: bool | None) -> bool:
    """Return the effective ClamAV-enabled flag for a given job."""
    return override if override is not None else settings.clamav_enabled


def _effective_vuln(settings: Settings, override: bool | None) -> bool:
    """Return the effective vulnerability-scan flag for a given job."""
    return override if override is not None else settings.vuln_scan_enabled


def _effective_severities(settings: Settings, override: str | None) -> list[str]:
    """Return the effective severity list for a given job."""
    raw = override if override is not None else settings.vuln_scan_severities
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _human_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


# ─── Background Tasks ─────────────────────────────────────────────────────────


async def run_pull_pipeline(
    job_id: str,
    image: str,
    tag: str,
    settings: Settings,
    clamav_enabled_override: bool | None = None,
    vuln_scan_enabled_override: bool | None = None,
    vuln_severities_override: str | None = None,
):
    """Background task: pull image, optionally scan with ClamAV then Trivy."""
    staging_dir = settings.staging_dir
    tarball_path = os.path.join(staging_dir, f"{job_id}.tar")

    _jobs[job_id]["status"] = JobStatus.PULLING
    _jobs[job_id]["message"] = f"Pulling {image}:{tag} from Docker Hub..."
    _jobs[job_id]["progress"] = 10

    # Proxy env vars for Docker daemon subprocess
    pull_env = {**os.environ, **settings.docker_env_proxy}

    # Resolve effective scan behaviour for this job
    do_clamav = _effective_clamav(settings, clamav_enabled_override)
    do_vuln = _effective_vuln(settings, vuln_scan_enabled_override)
    severities = _effective_severities(settings, vuln_severities_override)

    try:
        # Build docker pull command
        pull_image = f"{image}:{tag}"
        if settings.dockerhub_username and settings.dockerhub_password:
            # Login first
            login_proc = await asyncio.create_subprocess_exec(
                "docker",
                "login",
                "-u",
                settings.dockerhub_username,
                "-p",
                settings.dockerhub_password,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=pull_env,
            )
            await login_proc.communicate()

        # Pull the image
        pull_proc = await asyncio.create_subprocess_exec(
            "docker",
            "pull",
            pull_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=pull_env,
        )
        stdout, stderr = await pull_proc.communicate()

        if pull_proc.returncode != 0:
            raise Exception(f"Docker pull failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 50

        if do_clamav or do_vuln:
            _jobs[job_id]["message"] = "Image pulled. Saving for scanning..."

            # Export image to tarball for scanning
            save_proc = await asyncio.create_subprocess_exec(
                "docker",
                "save",
                "-o",
                tarball_path,
                pull_image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await save_proc.communicate()
            _jobs[job_id]["message"] = "Image saved. Starting scans..."

        # ── ClamAV scan (optional) ────────────────────────────────────────────
        if do_clamav:
            _jobs[job_id]["progress"] = 70
            _jobs[job_id]["status"] = JobStatus.SCANNING
            _jobs[job_id]["message"] = "Scanning with ClamAV..."

            # ClamAV scan via clamdscan
            scan_result = await _clamav_scan(tarball_path, settings)
            _jobs[job_id]["scan_result"] = scan_result

            if "FOUND" in scan_result or "ERROR" in scan_result:
                _jobs[job_id]["status"] = JobStatus.SCAN_INFECTED
                _jobs[job_id]["message"] = f"⚠️ Scan FAILED: {scan_result}"
                _jobs[job_id]["progress"] = 100
                # Remove infected tarball
                if os.path.exists(tarball_path):
                    os.remove(tarball_path)
                return
        else:
            # ClamAV disabled for this job — skip to vuln or clean state
            _jobs[job_id]["status"] = JobStatus.SCAN_SKIPPED
            _jobs[job_id]["scan_result"] = "ClamAV scan disabled — skipped."
            _jobs[job_id]["progress"] = 70
            _jobs[job_id]["message"] = "ClamAV scan skipped."

        # ── Vulnerability scan (optional) ─────────────────────────────────────
        if do_vuln:
            _jobs[job_id]["status"] = JobStatus.VULN_SCANNING
            _jobs[job_id]["progress"] = 85
            _jobs[job_id]["message"] = (
                "ClamAV clean. Running vulnerability scan..."
                if do_clamav
                else "Running vulnerability scan..."
            )
            vuln_summary = await _vuln_scan_image(tarball_path, settings, severities)
            _jobs[job_id]["vuln_result"] = vuln_summary

            if vuln_summary["blocked"]:
                _jobs[job_id]["status"] = JobStatus.SCAN_VULNERABLE
                _jobs[job_id]["message"] = (
                    "⚠️ Vulnerability policy failed "
                    f"(severities: {', '.join(vuln_summary['severities'])})."
                )
                _jobs[job_id]["progress"] = 100
                if os.path.exists(tarball_path):
                    os.remove(tarball_path)
                return

        _jobs[job_id]["status"] = JobStatus.SCAN_CLEAN
        _jobs[job_id]["message"] = "✅ Scan passed. Ready to push to registry."
        _jobs[job_id]["progress"] = 100

    except Exception as e:
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = str(e)
        _jobs[job_id]["message"] = f"Failed: {str(e)}"
        _jobs[job_id]["progress"] = 100


async def _clamav_scan(path: str, settings: Settings) -> str:
    """Run ClamAV scan on a file. Returns scan output."""
    import struct

    async def _scan_via_clamd_socket(host: str, port: int, file_path: str) -> str:
        """Scan a file using clamd INSTREAM protocol over TCP."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=120
            )

            # Send INSTREAM command
            writer.write(b"zINSTREAM\0")
            await writer.drain()

            # Stream file in chunks (max 4096 bytes per chunk)
            chunk_size = 4096
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    # Each chunk prefixed by its size as 4-byte big-endian int
                    writer.write(struct.pack("!I", len(chunk)) + chunk)
                    await writer.drain()

            # End of stream: 4-byte zero
            writer.write(struct.pack("!I", 0))
            await writer.drain()

            # Read response
            response = await asyncio.wait_for(reader.read(4096), timeout=60)
            writer.close()
            await writer.wait_closed()

            result = response.decode(errors="replace").strip()
            # clamd returns "stream: OK" or "stream: <virus> FOUND"
            return result

        except asyncio.TimeoutError:
            raise ConnectionRefusedError("Timeout connecting to clamd")

    # Try clamd daemon via TCP
    try:
        result = await _scan_via_clamd_socket(
            settings.clamav_host, settings.clamav_port, path
        )
        # Normalize to clamscan-like output for the rest of the pipeline
        if "OK" in result:
            return f"{path}: OK"
        elif "FOUND" in result:
            return f"{path}: {result} FOUND"
        else:
            return f"{path}: {result} ERROR"
    except Exception as e:
        return f"OK (scan skipped: {str(e)})"


async def _vuln_scan_image(
    tarball_path: str, settings: Settings, severities: list[str] | None = None
) -> dict:
    """Run Trivy vulnerability scan on a Docker image."""
    effective_severities = (
        severities if severities is not None else settings.vuln_severities
    )

    cmd = [
        "trivy",
        "image",
        "--quiet",
        "--format",
        "json",
        "--timeout",
        settings.vuln_scan_timeout,
        "--severity",
        ",".join(effective_severities),
    ]
    if settings.vuln_ignore_unfixed:
        cmd.append("--ignore-unfixed")
    cmd += ["--input", tarball_path]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise Exception(
            "Trivy binary not found. Install trivy or disable VULN_SCAN_ENABLED."
        ) from exc

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise Exception(f"Trivy scan failed: {stderr.decode() or stdout.decode()}")

    try:
        payload = json.loads(stdout.decode() or "{}")
    except Exception as exc:
        raise Exception(f"Unable to parse Trivy output: {exc}")

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    for result in payload.get("Results", []) or []:
        for vuln in result.get("Vulnerabilities", []) or []:
            sev = (vuln.get("Severity") or "UNKNOWN").upper()
            counts[sev] = counts.get(sev, 0) + 1

    blocked = any(counts.get(sev, 0) > 0 for sev in effective_severities)
    summary = {
        "enabled": True,
        "blocked": blocked,
        "severities": effective_severities,
        "counts": counts,
    }
    return summary


async def run_push_pipeline(
    job_id: str, target_image: str, target_tag: str, settings: Settings
):
    """Background task: tag and push image to private registry.

    REGISTRY_URL is used by the backend to talk to the registry API (inside Docker network).
    REGISTRY_PUSH_HOST is the address the HOST Docker daemon uses to push images.
    Since the Docker daemon runs on the host (socket mount), it cannot resolve
    Docker-internal hostnames like "registry". Use "localhost:5000" instead,
    which works because the registry port is published on the host.
    """
    _jobs[job_id]["status"] = JobStatus.PUSHING
    _jobs[job_id]["message"] = f"Pushing to registry as {target_image}:{target_tag}..."
    _jobs[job_id]["progress"] = 10

    original_image = _jobs[job_id]["image"]
    original_tag = _jobs[job_id]["tag"]
    source = f"{original_image}:{original_tag}"

    # Build push host: prefer explicit REGISTRY_PUSH_HOST, fallback to REGISTRY_URL stripped.
    push_host = settings.registry_push_host
    if not push_host:
        from urllib.parse import urlparse

        parsed = urlparse(settings.registry_url)
        push_host = parsed.netloc  # e.g. "localhost:5000"

    full_target = f"{push_host}/{target_image}:{target_tag}"
    pull_env = {**os.environ, **settings.docker_env_proxy}

    try:
        # Tag the image for the private registry
        tag_proc = await asyncio.create_subprocess_exec(
            "docker",
            "tag",
            source,
            full_target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await tag_proc.communicate()
        if tag_proc.returncode != 0:
            raise Exception(f"Docker tag failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 40

        # Push to the private registry
        push_proc = await asyncio.create_subprocess_exec(
            "docker",
            "push",
            full_target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=pull_env,
        )
        _, stderr = await push_proc.communicate()
        if push_proc.returncode != 0:
            raise Exception(f"Docker push failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 80

        # Cleanup local tagged image (registry-prefixed copy)
        await asyncio.create_subprocess_exec(
            "docker",
            "rmi",
            full_target,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Cleanup source image (the original pulled image)
        await asyncio.create_subprocess_exec(
            "docker",
            "rmi",
            source,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Remove tarball from staging directory if it exists
        tarball_path = os.path.join(settings.staging_dir, f"{job_id}.tar")
        if os.path.exists(tarball_path):
            os.remove(tarball_path)

        _jobs[job_id]["status"] = JobStatus.DONE
        _jobs[job_id]["message"] = f"✅ Successfully pushed to {full_target}"
        _jobs[job_id]["progress"] = 100
        _jobs[job_id]["target_image"] = target_image
        _jobs[job_id]["target_tag"] = target_tag

    except Exception as e:
        _jobs[job_id]["status"] = JobStatus.FAILED
        _jobs[job_id]["error"] = str(e)
        _jobs[job_id]["message"] = f"Push failed: {str(e)}"
        _jobs[job_id]["progress"] = 100


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/clamav/status", response_model=ClamAVStatus)
async def get_clamav_status(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Check whether ClamAV is enabled in the server configuration and whether
    its daemon TCP socket is reachable right now.
    Used by the frontend to display a live health indicator.
    """
    if not settings.clamav_enabled:
        return ClamAVStatus(
            enabled=False,
            reachable=False,
            host=settings.clamav_host,
            port=settings.clamav_port,
            message="ClamAV is disabled in configuration (CLAMAV_ENABLED=false).",
        )

    # Non-blocking TCP probe
    reachable = False
    try:
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        await loop.run_in_executor(
            None, sock.connect, (settings.clamav_host, settings.clamav_port)
        )
        sock.close()
        reachable = True
    except Exception:
        reachable = False

    return ClamAVStatus(
        enabled=True,
        reachable=reachable,
        host=settings.clamav_host,
        port=settings.clamav_port,
        message=(
            f"ClamAV daemon reachable at {settings.clamav_host}:{settings.clamav_port}"
            if reachable
            else f"ClamAV daemon NOT reachable at {settings.clamav_host}:{settings.clamav_port}"
        ),
    )


@router.post("/pull", response_model=StagingJob)
async def pull_image(
    request: PullRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Start a pull+scan pipeline for a Docker Hub image."""
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
        "clamav_enabled_override": request.clamav_enabled_override,
        "vuln_scan_enabled_override": request.vuln_scan_enabled_override,
        "vuln_severities_override": request.vuln_severities_override,
    }

    background_tasks.add_task(
        run_pull_pipeline,
        job_id,
        request.image,
        request.tag,
        settings,
        request.clamav_enabled_override,
        request.vuln_scan_enabled_override,
        request.vuln_severities_override,
    )
    return StagingJob(**_jobs[job_id])


@router.get("/jobs/{job_id}", response_model=StagingJob)
async def get_job_status(
    job_id: str,
    _: UserInfo = Depends(get_current_user),
):
    """Get the current status of a staging job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return StagingJob(**_jobs[job_id])

@router.get("/jobs", response_model=list[StagingJob])
async def list_jobs(_: UserInfo = Depends(get_current_user)):
    """List all staging jobs, sorted so active jobs appear first,
    then by most recent insertion (reverse insertion order)."""
    active_statuses = {
        JobStatus.PENDING,
        JobStatus.PULLING,
        JobStatus.SCANNING,
        JobStatus.VULN_SCANNING,
        JobStatus.PUSHING,
    }
    all_jobs = [StagingJob(**job) for job in _jobs.values()]
    # Reverse insertion order so newest jobs appear before older finished ones
    all_jobs.reverse()
    # Stable sort: active jobs bubble to the top
    all_jobs.sort(key=lambda j: 0 if j.status in active_statuses else 1)
    return all_jobs

@router.post("/push")
async def push_image(
    request: PushRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Push a scanned image to the private registry (with optional rename)."""
    if request.job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = _jobs[request.job_id]
    # Allow push when scan is clean OR when ClamAV was skipped
    if job["status"] not in (JobStatus.SCAN_CLEAN, JobStatus.SCAN_SKIPPED):
        raise HTTPException(
            status_code=400,
            detail="Image must pass scanning (or have scan skipped) before pushing",
        )

    target_image = request.target_image or job["image"]
    target_tag = request.target_tag or job["tag"]

    background_tasks.add_task(
        run_push_pipeline, request.job_id, target_image, target_tag, settings
    )
    return {"message": "Push pipeline started", "job_id": request.job_id}


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Delete a staging job and its associated files."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    # Validate that the job_id is a well-formed UUID to prevent path traversal
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id")

    # Remove tarball if exists, ensuring the path stays within the staging directory
    staging_root = os.path.realpath(settings.staging_dir)
    tarball_path = os.path.realpath(os.path.join(staging_root, f"{job_id}.tar"))
    # Ensure the resolved tarball_path is within the staging_root to prevent traversal
    if os.path.commonpath([staging_root, tarball_path]) != staging_root:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    if os.path.exists(tarball_path):
        os.remove(tarball_path)

    del _jobs[job_id]
    return {"message": "Job deleted"}


@router.get("/search/dockerhub")
async def search_dockerhub(
    q: str,
    page: int = 1,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Search Docker Hub for images."""
    url = f"{settings.dockerhub_api_url}/search/repositories/?query={q}&page={page}&page_size=10"
    proxy = settings.httpx_proxy
    async with httpx.AsyncClient(proxy=proxy, timeout=10) as client:
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
    _: UserInfo = Depends(get_current_user),
):
    """Get available tags for a Docker Hub image."""
    url = f"{settings.dockerhub_api_url}/repositories/{image}/tags/?page_size=20&ordering=last_updated"
    proxy = settings.httpx_proxy
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        tags = [t["name"] for t in data.get("results", [])]
    except Exception:
        tags = ["latest"]
    return {"tags": tags}


@router.get("/vuln-config")
async def get_vuln_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Return server-side vulnerability scan defaults (from environment variables)."""
    return {
        "enabled": settings.vuln_scan_enabled,
        "severities": [
            s.strip() for s in settings.vuln_scan_severities.split(",") if s.strip()
        ],
        "ignore_unfixed": settings.vuln_ignore_unfixed,
        "timeout": settings.vuln_scan_timeout,
    }


# ─── Quick Actions: Dangling Images ───────────────────────────────────────────


@router.get("/dangling-images", response_model=DanglingImagesResult)
async def list_dangling_images(
    _: UserInfo = Depends(get_current_user),
):
    """
    List dangling Docker images on the host (images with no tag, i.e. <none>:<none>).
    These accumulate over time and waste disk space on the staging host.
    """
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "images",
        "--filter",
        "dangling=true",
        "--format",
        '{"id":"{{.ID}}","repository":"{{.Repository}}","tag":"{{.Tag}}","size":"{{.Size}}","created":"{{.CreatedSince}}"}',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"docker images failed: {stderr.decode().strip()}",
        )

    images = []
    for line in stdout.decode().strip().splitlines():
        line = line.strip()
        if line:
            try:
                images.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return DanglingImagesResult(images=images, count=len(images))


@router.post("/dangling-images/purge")
async def purge_dangling_images(
    _: UserInfo = Depends(get_current_user),
):
    """
    Remove all dangling Docker images from the host.
    Equivalent to: docker image prune -f
    """
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "image",
        "prune",
        "-f",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"docker image prune failed: {stderr.decode().strip()}",
        )
    output = stdout.decode().strip()
    return {"message": "Dangling images purged", "output": output}


# ─── Quick Actions: Orphan Tarballs ───────────────────────────────────────────


@router.get("/orphan-tarballs", response_model=OrphanTarballsResult)
async def list_orphan_tarballs(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    List .tar files in the staging directory that have no corresponding active job.
    These are leftover files from interrupted or failed pipeline runs.
    """
    staging_dir = settings.staging_dir
    active_job_ids = set(_jobs.keys())

    orphans: list[str] = []
    total_size = 0

    if os.path.isdir(staging_dir):
        for fname in os.listdir(staging_dir):
            if not fname.endswith(".tar"):
                continue
            # Extract job_id from filename (format: <uuid>.tar)
            job_id_candidate = fname[:-4]
            try:
                uuid.UUID(job_id_candidate)
            except ValueError:
                # Not a UUID-named file — treat as orphan
                fpath = os.path.join(staging_dir, fname)
                orphans.append(fname)
                total_size += os.path.getsize(fpath)
                continue

            # It's a UUID-named tar: orphan if no active job references it
            if job_id_candidate not in active_job_ids:
                fpath = os.path.join(staging_dir, fname)
                orphans.append(fname)
                total_size += os.path.getsize(fpath)

    return OrphanTarballsResult(
        files=orphans,
        count=len(orphans),
        total_size_bytes=total_size,
        total_size_human=_human_size(total_size),
    )


@router.post("/orphan-tarballs/purge")
async def purge_orphan_tarballs(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Delete all orphan .tar files from the staging directory.
    Only files with no corresponding active job are removed.
    """
    staging_dir = settings.staging_dir
    active_job_ids = set(_jobs.keys())

    deleted: list[str] = []
    errors: list[dict] = []
    freed_bytes = 0

    if os.path.isdir(staging_dir):
        for fname in os.listdir(staging_dir):
            if not fname.endswith(".tar"):
                continue
            job_id_candidate = fname[:-4]

            # Determine if this file is an orphan
            is_orphan = False
            try:
                uuid.UUID(job_id_candidate)
                is_orphan = job_id_candidate not in active_job_ids
            except ValueError:
                is_orphan = True

            if is_orphan:
                fpath = os.path.join(staging_dir, fname)
                try:
                    size = os.path.getsize(fpath)
                    os.remove(fpath)
                    deleted.append(fname)
                    freed_bytes += size
                except OSError as e:
                    errors.append({"file": fname, "error": str(e)})

    return {
        "message": f"Purged {len(deleted)} orphan tarball(s)",
        "deleted": deleted,
        "freed_bytes": freed_bytes,
        "freed_human": _human_size(freed_bytes),
        "errors": errors,
    }
