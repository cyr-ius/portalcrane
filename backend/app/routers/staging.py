"""
Portalcrane - Staging Router
Pipeline: Pull from Docker Hub → ClamAV Scan → Trivy CVE Scan (optional) → Push to Registry

Vuln scan configuration precedence:
  1. Per-request override (sent by the frontend from localStorage)
  2. Server environment variables (VULN_SCAN_*)
"""

import asyncio
import json
import os
import re
import uuid
from enum import Enum

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from .auth import UserInfo, get_current_user

router = APIRouter()

# In-memory job store (use Redis in production for multi-instance)
_jobs: dict[str, dict] = {}


# ─── Models ──────────────────────────────────────────────────────────────────

VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}


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
    """
    Request to pull an image from Docker Hub.

    Vuln scan fields are optional — if omitted, server env vars are used.
    If provided (sent from the frontend localStorage), they override env vars
    for this specific pipeline run only.
    """

    image: str
    tag: str = "latest"

    # Per-request vuln config overrides (all optional)
    vuln_scan_enabled: bool | None = Field(default=None)
    vuln_scan_severities: list[str] | None = Field(default=None)
    vuln_ignore_unfixed: bool | None = Field(default=None)
    vuln_scan_timeout: str | None = Field(default=None)


class VulnConfigResponse(BaseModel):
    """Vuln scan configuration as exposed from server env vars (read-only)."""

    enabled: bool
    severities: list[str]
    ignore_unfixed: bool
    timeout: str


class PushRequest(BaseModel):
    """Request to push a staged image to the registry."""

    job_id: str
    target_image: str | None = None
    target_tag: str | None = None


class DockerHubSearchResult(BaseModel):
    """Docker Hub search result model."""

    name: str
    description: str
    star_count: int
    pull_count: int
    is_official: bool
    is_automated: bool


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_timeout_seconds(timeout_str: str) -> int:
    """Convert Trivy timeout string (e.g. '5m', '30s', '2h') to seconds."""
    match = re.fullmatch(r"(\d+)(s|m|h)", timeout_str.strip().lower())
    if not match:
        return 300
    value, unit = int(match.group(1)), match.group(2)
    return value * {"s": 1, "m": 60, "h": 3600}[unit]


def _resolve_vuln_config(request: PullRequest, settings: Settings) -> dict:
    """
    Build the effective vuln config for a pipeline run.

    Priority: per-request override > server env vars.
    Sanitizes severities to only accept known values.
    """
    enabled = (
        request.vuln_scan_enabled
        if request.vuln_scan_enabled is not None
        else settings.vuln_scan_enabled
    )

    # Sanitize and deduplicate severities from request
    if request.vuln_scan_severities is not None:
        severities = [
            s.strip().upper()
            for s in request.vuln_scan_severities
            if s.strip().upper() in VALID_SEVERITIES
        ]
        # Fall back to server defaults if list is empty after sanitization
        if not severities:
            severities = settings.vuln_severities
    else:
        severities = settings.vuln_severities

    ignore_unfixed = (
        request.vuln_ignore_unfixed
        if request.vuln_ignore_unfixed is not None
        else settings.vuln_ignore_unfixed
    )

    timeout = (
        request.vuln_scan_timeout
        if request.vuln_scan_timeout is not None
        else settings.vuln_scan_timeout
    )

    return {
        "enabled": enabled,
        "severities": severities,
        "ignore_unfixed": ignore_unfixed,
        "timeout": timeout,
    }


# ─── Background Tasks ─────────────────────────────────────────────────────────


async def run_pull_pipeline(
    job_id: str,
    image: str,
    tag: str,
    settings: Settings,
    vuln_cfg: dict,
):
    """Background task: pull image, ClamAV scan, optional Trivy scan."""
    staging_dir = settings.staging_dir
    tarball_path = os.path.join(staging_dir, f"{job_id}.tar")

    _jobs[job_id]["status"] = JobStatus.PULLING
    _jobs[job_id]["message"] = f"Pulling {image}:{tag} from Docker Hub..."
    _jobs[job_id]["progress"] = 10

    pull_env = {**os.environ, **settings.docker_env_proxy}

    try:
        pull_image = f"{image}:{tag}"

        if settings.dockerhub_username and settings.dockerhub_password:
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

        pull_proc = await asyncio.create_subprocess_exec(
            "docker",
            "pull",
            pull_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=pull_env,
        )
        _, stderr = await pull_proc.communicate()
        if pull_proc.returncode != 0:
            raise Exception(f"Docker pull failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 50
        _jobs[job_id]["message"] = "Image pulled. Exporting for antivirus scan..."

        save_proc = await asyncio.create_subprocess_exec(
            "docker",
            "save",
            "-o",
            tarball_path,
            pull_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, save_stderr = await save_proc.communicate()

        if save_proc.returncode != 0:
            raise Exception(
                f"docker save failed (rc={save_proc.returncode}): {save_stderr.decode().strip()}"
            )

        if not os.path.exists(tarball_path):
            raise Exception(
                f"docker save succeeded but tarball not found at {tarball_path}"
            )

        tarball_size = os.path.getsize(tarball_path)
        if tarball_size == 0:
            os.remove(tarball_path)
            raise Exception(f"docker save produced an empty tarball at {tarball_path}")

        _jobs[job_id]["progress"] = 70
        _jobs[job_id]["status"] = JobStatus.SCANNING
        _jobs[job_id]["message"] = "Scanning with ClamAV..."

        scan_result = await _clamav_scan(tarball_path, settings)
        _jobs[job_id]["scan_result"] = scan_result

        if "FOUND" in scan_result or "ERROR" in scan_result:
            _jobs[job_id]["status"] = JobStatus.SCAN_INFECTED
            _jobs[job_id]["message"] = f"⚠️ Scan FAILED: {scan_result}"
            _jobs[job_id]["progress"] = 100
            os.remove(tarball_path)
        else:
            if vuln_cfg["enabled"]:
                _jobs[job_id]["status"] = JobStatus.VULN_SCANNING
                _jobs[job_id]["progress"] = 85
                _jobs[job_id]["message"] = (
                    f"ClamAV clean. Running Trivy scan "
                    f"(severities: {', '.join(vuln_cfg['severities'])})..."
                )

                vuln_summary = await _vuln_scan_tarball(tarball_path, vuln_cfg)
                _jobs[job_id]["vuln_result"] = vuln_summary

                if vuln_summary["blocked"]:
                    _jobs[job_id]["status"] = JobStatus.SCAN_VULNERABLE
                    _jobs[job_id]["message"] = (
                        f"⚠️ Vulnerability policy failed "
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
    """Run ClamAV scan on a file via clamd protocol (INSTREAM) or fallback to clamscan."""
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
                    # writer.write(len(chunk).to_bytes(4, "big") + chunk)
                    await writer.drain()

            # End of stream: 4-byte zero
            writer.write(struct.pack("!I", 0))
            # writer.write((0).to_bytes(4, "big"))
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


async def _vuln_scan_tarball(tarball_path: str, vuln_cfg: dict) -> dict:
    """
    Run Trivy vulnerability scan on a local image tarball.
    Uses the resolved vuln_cfg (already merged env + request overrides).
    """
    timeout_str = vuln_cfg["timeout"]
    severities = vuln_cfg["severities"]
    ignore_unfixed = vuln_cfg["ignore_unfixed"]

    cmd = [
        "trivy",
        "image",
        "--quiet",
        "--format",
        "json",
        "--timeout",
        timeout_str,
        "--severity",
        ",".join(severities),
    ]
    if ignore_unfixed:
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

    timeout_seconds = _parse_timeout_seconds(timeout_str)
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise Exception(
            f"Trivy scan timed out after {timeout_str}. "
            "Consider increasing VULN_SCAN_TIMEOUT."
        )

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

    blocked = any(counts.get(sev, 0) > 0 for sev in severities)
    return {
        "enabled": True,
        "blocked": blocked,
        "severities": severities,
        "counts": counts,
    }


async def run_push_pipeline(
    job_id: str, target_image: str, target_tag: str, settings: Settings
):
    """Background task: tag and push image to private registry."""
    _jobs[job_id]["status"] = JobStatus.PUSHING
    _jobs[job_id]["message"] = f"Pushing to registry as {target_image}:{target_tag}..."
    _jobs[job_id]["progress"] = 10

    original_image = _jobs[job_id]["image"]
    original_tag = _jobs[job_id]["tag"]
    source = f"{original_image}:{original_tag}"

    if settings.registry_push_host:
        push_host = settings.registry_push_host.strip("/")
    else:
        push_host = (
            settings.registry_url.replace("https://", "")
            .replace("http://", "")
            .strip("/")
        )

    full_target = f"{push_host}/{target_image}:{target_tag}"

    try:
        if settings.registry_username and settings.registry_password:
            _jobs[job_id]["message"] = f"Authenticating against {push_host}..."
            login_proc = await asyncio.create_subprocess_exec(
                "docker",
                "login",
                push_host,
                "--username",
                settings.registry_username,
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

        _jobs[job_id]["message"] = f"Tagging as {full_target}..."
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
            raise Exception(f"Tag failed: {stderr.decode()}")

        _jobs[job_id]["progress"] = 40
        _jobs[job_id]["message"] = f"Pushing {full_target}..."

        push_proc = await asyncio.create_subprocess_exec(
            "docker",
            "push",
            full_target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await push_proc.communicate()
        if push_proc.returncode != 0:
            raise Exception(f"Push failed: {stderr.decode() or stdout.decode()}")

        await asyncio.create_subprocess_exec(
            "docker",
            "rmi",
            full_target,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.create_subprocess_exec(
            "docker",
            "rmi",
            source,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

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


@router.get("/vuln-config", response_model=VulnConfigResponse)
async def get_vuln_config(
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Return the server-side vuln scan defaults (from environment variables).
    The frontend uses this as the base to display and potentially override
    per-browser via localStorage.
    """
    return VulnConfigResponse(
        enabled=settings.vuln_scan_enabled,
        severities=settings.vuln_severities,
        ignore_unfixed=settings.vuln_ignore_unfixed,
        timeout=settings.vuln_scan_timeout,
    )


@router.post("/pull", response_model=StagingJob)
async def pull_image(
    request: PullRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Start a pull+scan pipeline.
    Vuln scan config is resolved from request overrides + server env vars.
    """
    # Resolve effective vuln config (request overrides > env vars)
    vuln_cfg = _resolve_vuln_config(request, settings)

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

    background_tasks.add_task(
        run_pull_pipeline, job_id, request.image, request.tag, settings, vuln_cfg
    )
    return StagingJob(**_jobs[job_id])


@router.get("/jobs/{job_id}", response_model=StagingJob)
async def get_job_status(job_id: str, _: UserInfo = Depends(get_current_user)):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return StagingJob(**_jobs[job_id])


@router.get("/jobs", response_model=list[StagingJob])
async def list_jobs(_: UserInfo = Depends(get_current_user)):
    return [StagingJob(**job) for job in _jobs.values()]


@router.post("/push")
async def push_image(
    request: PushRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    if request.job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = _jobs[request.job_id]
    if job["status"] != JobStatus.SCAN_CLEAN:
        raise HTTPException(
            status_code=400, detail="Image must be scanned and clean before pushing"
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
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id")

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
    async with httpx.AsyncClient(
        timeout=15.0, proxy=settings.httpx_proxy.get("https://") or None
    ) as client:
        try:
            response = await client.get(
                "https://hub.docker.com/v2/search/repositories/",
                params={"query": q, "page": page, "page_size": 20},
            )
            response.raise_for_status()
            data = response.json()
            results = [
                {
                    "name": item.get("repo_name", ""),
                    "description": item.get("short_description", ""),
                    "star_count": item.get("star_count", 0),
                    "pull_count": item.get("pull_count", 0),
                    "is_official": item.get("is_official", False),
                    "is_automated": item.get("is_automated", False),
                }
                for item in data.get("results", [])
            ]
            return {"results": results, "count": data.get("count", 0)}
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Docker Hub search failed: {str(e)}"
            )


@router.get("/search/dockerhub/tags")
async def get_dockerhub_tags(
    image: str,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    repo = image if "/" in image else f"library/{image}"
    async with httpx.AsyncClient(
        timeout=15.0, proxy=settings.httpx_proxy.get("https://") or None
    ) as client:
        try:
            response = await client.get(
                f"https://hub.docker.com/v2/repositories/{repo}/tags",
                params={"page_size": 50, "ordering": "last_updated"},
            )
            response.raise_for_status()
            data = response.json()
            return {
                "image": image,
                "tags": [item["name"] for item in data.get("results", [])],
            }
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Failed to fetch tags: {str(e)}"
            )
