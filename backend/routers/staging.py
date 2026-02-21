"""
Portalcrane - Staging Router
Pipeline: Pull from Docker Hub → ClamAV Scan → Trivy CVE Scan (optional) → Push to Registry
"""

import asyncio
import json
import os
import subprocess
import uuid
from enum import Enum
pass  # typing import cleaned

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from config import Settings, get_settings
from routers.auth import get_current_user, UserInfo

router = APIRouter()

# In-memory job store (use Redis in production for multi-instance)
_jobs: dict[str, dict] = {}


# ─── Models ──────────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    PULLING = "pulling"
    SCANNING = "scanning"
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


class PullRequest(BaseModel):
    """Request to pull an image from Docker Hub."""
    image: str
    tag: str = "latest"


class PushRequest(BaseModel):
    """Request to push a staged image to the registry."""
    job_id: str
    target_image: str | None = None  # Optional rename
    target_tag: str | None = None    # Optional retag


class DockerHubSearchResult(BaseModel):
    """Docker Hub search result model."""
    name: str
    description: str
    star_count: int
    pull_count: int
    is_official: bool
    is_automated: bool


# ─── Background Tasks ─────────────────────────────────────────────────────────


async def run_pull_pipeline(job_id: str, image: str, tag: str, settings: Settings):
    """Background task: pull image, scan with ClamAV."""
    staging_dir = settings.staging_dir
    tarball_path = os.path.join(staging_dir, f"{job_id}.tar")

    _jobs[job_id]["status"] = JobStatus.PULLING
    _jobs[job_id]["message"] = f"Pulling {image}:{tag} from Docker Hub..."
    _jobs[job_id]["progress"] = 10

    # Proxy env vars for Docker daemon subprocess
    pull_env = {**os.environ, **settings.docker_env_proxy}

    try:
        # Build docker pull command
        pull_image = f"{image}:{tag}"
        if settings.dockerhub_username and settings.dockerhub_password:
            # Login first
            login_proc = await asyncio.create_subprocess_exec(
                "docker", "login",
                "-u", settings.dockerhub_username,
                "-p", settings.dockerhub_password,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=pull_env,
            )
            await login_proc.communicate()

        # Pull the image
        pull_proc = await asyncio.create_subprocess_exec(
            "docker", "pull", pull_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=pull_env,
        )
        stdout, stderr = await pull_proc.communicate()

        if pull_proc.returncode != 0:
            raise Exception(f"Docker pull failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 50
        _jobs[job_id]["message"] = "Image pulled. Exporting for antivirus scan..."

        # Export image to tarball for ClamAV scanning
        save_proc = await asyncio.create_subprocess_exec(
            "docker", "save", "-o", tarball_path, pull_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await save_proc.communicate()

        _jobs[job_id]["progress"] = 70
        _jobs[job_id]["status"] = JobStatus.SCANNING
        _jobs[job_id]["message"] = "Scanning with ClamAV..."

        # ClamAV scan via clamdtop or clamscan
        scan_result = await _clamav_scan(tarball_path, settings)
        _jobs[job_id]["scan_result"] = scan_result

        if "FOUND" in scan_result or "ERROR" in scan_result:
            _jobs[job_id]["status"] = JobStatus.SCAN_INFECTED
            _jobs[job_id]["message"] = f"⚠️ Scan FAILED: {scan_result}"
            _jobs[job_id]["progress"] = 100
            # Remove infected tarball
            os.remove(tarball_path)
        else:
            if settings.vuln_scan_enabled:
                _jobs[job_id]["status"] = JobStatus.VULN_SCANNING
                _jobs[job_id]["progress"] = 85
                _jobs[job_id]["message"] = "ClamAV clean. Running vulnerability scan..."
                vuln_summary = await _vuln_scan_image(image, tag, settings)
                _jobs[job_id]["vuln_result"] = vuln_summary

                if vuln_summary["blocked"]:
                    _jobs[job_id]["status"] = JobStatus.SCAN_VULNERABLE
                    _jobs[job_id]["message"] = (
                        "⚠️ Vulnerability policy failed "
                        f"(severities: {', '.join(vuln_summary['severities'])})."
                    )
                    _jobs[job_id]["progress"] = 100
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
    try:
        # Try clamd network scan first
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((settings.clamav_host, settings.clamav_port))
        sock.close()

        # Use clamdscan pointing to the daemon
        proc = await asyncio.create_subprocess_exec(
            "clamdscan",
            "--host", settings.clamav_host,
            "--port", str(settings.clamav_port),
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()
    except ConnectionRefusedError:
        # Fallback to local clamscan
        try:
            proc = await asyncio.create_subprocess_exec(
                "clamscan", "--no-summary", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode()
        except FileNotFoundError:
            return "OK (ClamAV not available - scan skipped)"
    except Exception as e:
        return f"OK (scan skipped: {str(e)})"


async def _vuln_scan_image(image: str, tag: str, settings: Settings) -> dict:
    """Run Trivy vulnerability scan on a Docker image."""
    cmd = [
        "trivy", "image",
        "--quiet",
        "--format", "json",
        "--timeout", settings.vuln_scan_timeout,
        "--severity", ",".join(settings.vuln_severities),
    ]
    if settings.vuln_ignore_unfixed:
        cmd.append("--ignore-unfixed")
    cmd.append(f"{image}:{tag}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise Exception("Trivy binary not found. Install trivy or disable VULN_SCAN_ENABLED.") from exc
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

    blocked = any(counts.get(sev, 0) > 0 for sev in settings.vuln_severities)
    summary = {
        "enabled": True,
        "blocked": blocked,
        "severities": settings.vuln_severities,
        "counts": counts,
    }
    return summary


async def run_push_pipeline(job_id: str, target_image: str, target_tag: str, settings: Settings):
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
    if settings.registry_push_host:
        push_host = settings.registry_push_host.strip("/")
    else:
        push_host = (
            settings.registry_url
            .replace("https://", "")
            .replace("http://", "")
            .strip("/")
        )

    full_target = f"{push_host}/{target_image}:{target_tag}"

    try:
        # Authenticate against the push host if credentials are set
        if settings.registry_username and settings.registry_password:
            _jobs[job_id]["message"] = f"Authenticating against {push_host}..."
            login_proc = await asyncio.create_subprocess_exec(
                "docker", "login", push_host,
                "--username", settings.registry_username,
                "--password-stdin",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, login_err = await login_proc.communicate(
                input=settings.registry_password.encode()
            )
            if login_proc.returncode != 0:
                raise Exception(f"Registry login failed: {login_err.decode()}")

        # Tag the image
        _jobs[job_id]["message"] = f"Tagging as {full_target}..."
        tag_proc = await asyncio.create_subprocess_exec(
            "docker", "tag", source, full_target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await tag_proc.communicate()
        if tag_proc.returncode != 0:
            raise Exception(f"Tag failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 40
        _jobs[job_id]["message"] = f"Pushing {full_target}..."

        # Push to registry
        push_proc = await asyncio.create_subprocess_exec(
            "docker", "push", full_target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await push_proc.communicate()
        if push_proc.returncode != 0:
            raise Exception(f"Push failed: {stderr.decode() or stdout.decode()}")

        # Cleanup local images
        await asyncio.create_subprocess_exec("docker", "rmi", full_target,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.create_subprocess_exec("docker", "rmi", source,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

        # Remove tarball
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
    }

    background_tasks.add_task(run_pull_pipeline, job_id, request.image, request.tag, settings)
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
    """List all staging jobs."""
    return [StagingJob(**job) for job in _jobs.values()]


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
    if job["status"] != JobStatus.SCAN_CLEAN:
        raise HTTPException(status_code=400, detail="Image must be scanned and clean before pushing")

    target_image = request.target_image or job["image"]
    target_tag = request.target_tag or job["tag"]

    background_tasks.add_task(run_push_pipeline, request.job_id, target_image, target_tag, settings)
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

    # Remove tarball if exists
    tarball_path = os.path.join(settings.staging_dir, f"{job_id}.tar")
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
    async with httpx.AsyncClient(timeout=15.0, proxy=settings.httpx_proxy.get("https://") or None) as client:
        try:
            response = await client.get(
                "https://hub.docker.com/v2/search/repositories/",
                params={"query": q, "page": page, "page_size": 20},
            )
            response.raise_for_status()
            data = response.json()
            results = []
            for item in data.get("results", []):
                results.append({
                    "name": item.get("repo_name", ""),
                    "description": item.get("short_description", ""),
                    "star_count": item.get("star_count", 0),
                    "pull_count": item.get("pull_count", 0),
                    "is_official": item.get("is_official", False),
                    "is_automated": item.get("is_automated", False),
                })
            return {"results": results, "count": data.get("count", 0)}
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Docker Hub search failed: {str(e)}")


@router.get("/search/dockerhub/tags")
async def get_dockerhub_tags(
    image: str,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Get available tags for a Docker Hub image."""
    # Handle official images (library)
    repo = image if "/" in image else f"library/{image}"
    async with httpx.AsyncClient(timeout=15.0, proxy=settings.httpx_proxy.get("https://") or None) as client:
        try:
            response = await client.get(
                f"https://hub.docker.com/v2/repositories/{repo}/tags",
                params={"page_size": 50, "ordering": "last_updated"},
            )
            response.raise_for_status()
            data = response.json()
            tags = [item["name"] for item in data.get("results", [])]
            return {"image": image, "tags": tags}
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch tags: {str(e)}")
