import asyncio
import logging
import os
import shutil
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from ..config import REGISTRY_HOST, REGISTRY_URL, Settings, staging_root
from ..core.jwt import UserInfo, is_admin_user
from ..routers.folders import check_folder_access
from ..services.trivy_service import (
    effective_severities,
    effective_vuln,
    parse_trivy_output,
    trivy_raw_scan,
)

_logger = logging.getLogger(__name__)

jobs_list: dict[str, dict] = {}


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


def safe_job_path(job_id: str) -> Path:
    """
    Resolve the OCI layout directory for a given job_id.

    Raises ValueError if the resolved path escapes the staging root directory.
    This acts as a defence-in-depth guard against path traversal attacks even
    though job_id is currently always a UUID generated internally.
    """
    root = staging_root()
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


async def run_pull_pipeline(
    job_id: str,
    image: str,
    tag: str,
    settings: Settings,
    _: UserInfo,
    vuln_scan_enabled_override: bool | None = None,
    vuln_severities_override: str | None = None,
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
        oci_dir = safe_job_path(job_id)
    except ValueError as exc:
        jobs_list[job_id]["status"] = JobStatus.FAILED
        jobs_list[job_id]["error"] = str(exc)
        jobs_list[job_id]["message"] = f"Invalid job path: {exc}"
        jobs_list[job_id]["progress"] = 100
        return

    jobs_list[job_id]["status"] = JobStatus.PULLING
    source_host = jobs_list[job_id].get("source_registry_host") or "Docker Hub"
    jobs_list[job_id]["message"] = f"Pulling {image}:{tag} from {source_host}..."
    jobs_list[job_id]["progress"] = 10

    # Build skopeo environment (proxy variables)
    skopeo_env = {**os.environ, **settings.env_proxy}

    do_vuln = effective_vuln(settings, vuln_scan_enabled_override)
    severities = effective_severities(settings, vuln_severities_override)

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

        jobs_list[job_id]["progress"] = 50
        jobs_list[job_id]["message"] = "Image pulled. Starting vulnerability scan..."

        # ── Vulnerability scan (optional) ─────────────────────────────────────
        if do_vuln:
            jobs_list[job_id]["status"] = JobStatus.VULN_SCANNING
            jobs_list[job_id]["message"] = "Running Trivy vulnerability scan..."

            trivy_stdout, trivy_stderr, trivy_returncode = await trivy_raw_scan(
                str(oci_dir), severities
            )
            if trivy_returncode != 0:
                raise RuntimeError(
                    f"trivy scan failed: {trivy_stderr.decode(errors='replace')[:500]}"
                )

            vuln_result = parse_trivy_output(trivy_stdout, severities)
            jobs_list[job_id]["vuln_result"] = vuln_result

            if vuln_result["blocked"]:
                jobs_list[job_id]["status"] = JobStatus.SCAN_VULNERABLE
                jobs_list[job_id]["message"] = (
                    "⚠️ Vulnerabilities detected — review before pushing."
                )
            else:
                jobs_list[job_id]["status"] = JobStatus.SCAN_CLEAN
                jobs_list[job_id]["message"] = "✅ Scan clean. Ready to push."
        else:
            jobs_list[job_id]["status"] = JobStatus.SCAN_SKIPPED
            jobs_list[job_id]["message"] = "Vulnerability scan disabled. Ready to push."
            _logger.info("Scan skipped for job %s — status set to SCAN_SKIPPED", job_id)

        jobs_list[job_id]["progress"] = 100
        _logger.info(
            "Pipeline complete for job %s — final status: %s",
            job_id,
            jobs_list[job_id]["status"],
        )

    except Exception as exc:
        # Cleanup the OCI directory on failure
        if oci_dir.exists():
            shutil.rmtree(oci_dir, ignore_errors=True)
        jobs_list[job_id]["status"] = JobStatus.FAILED
        jobs_list[job_id]["error"] = str(exc)
        jobs_list[job_id]["message"] = f"Pull pipeline failed: {exc}"
        jobs_list[job_id]["progress"] = 100


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
    if not is_admin_user(jobs_list[job_id].get("owner", ""), settings):
        full_path = f"{folder}/{target_image}" if folder else target_image
        access = check_folder_access(
            jobs_list[job_id].get("owner", ""), full_path, is_pull=False
        )
        if access is not True:
            jobs_list[job_id]["status"] = JobStatus.FAILED
            jobs_list[job_id]["message"] = (
                "Push denied: insufficient folder permissions"
            )
            jobs_list[job_id]["error"] = "authorization"
            jobs_list[job_id]["progress"] = 100
            return

    jobs_list[job_id]["status"] = JobStatus.PUSHING
    jobs_list[job_id]["message"] = (
        f"Pushing to registry as {folder + '/' if folder else ''}{target_image}:{target_tag}..."
    )
    jobs_list[job_id]["progress"] = 10

    try:
        oci_dir = safe_job_path(job_id)
    except ValueError as exc:
        jobs_list[job_id]["status"] = JobStatus.FAILED
        jobs_list[job_id]["error"] = str(exc)
        jobs_list[job_id]["message"] = f"Invalid job path: {exc}"
        jobs_list[job_id]["progress"] = 100
        return

    # include folder prefix if provided
    image_path = f"{folder}/{target_image}" if folder else target_image
    dest = f"docker://{REGISTRY_HOST}/{image_path}:{target_tag}"

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

        jobs_list[job_id]["status"] = JobStatus.DONE
        jobs_list[job_id]["message"] = (
            f"✅ Successfully pushed to {folder + '/' if folder else ''}{target_image}:{target_tag}"
        )
        jobs_list[job_id]["progress"] = 100
        jobs_list[job_id]["target_image"] = target_image
        jobs_list[job_id]["target_tag"] = target_tag

    except Exception as exc:
        jobs_list[job_id]["status"] = JobStatus.FAILED
        jobs_list[job_id]["error"] = str(exc)
        jobs_list[job_id]["message"] = f"❌ Push failed: {exc}"
        jobs_list[job_id]["progress"] = 100


def normalize_sync_job(job: dict) -> dict:
    """
    Normalize a raw sync/import job dict for the frontend SyncJob interface.

    Mappings applied:
      - source_image -> source      (frontend key name)
      - errors: list -> error: str  (join list into single display string)
      - errors: list -> errors      (kept for multi-error display)
    """
    errors: list[str] = job.get("errors") or []
    return {
        "id": job.get("id", ""),
        "direction": job.get("direction", "export"),
        # Map internal 'source_image' to the frontend-expected 'source' field
        "source": job.get("source_image", ""),
        "source_registry_id": job.get("source_registry_id"),
        "dest_registry_id": job.get("dest_registry_id"),
        "dest_folder": job.get("dest_folder"),
        "status": job.get("status", "running"),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at"),
        "message": job.get("message", ""),
        # Join error list into a single display string; None when no errors
        "error": "\n".join(errors) if errors else None,
        # Keep full error list for multi-error rendering
        "errors": errors,
        "progress": job.get("progress", 0),
        "images_total": job.get("images_total", 0),
        "images_done": job.get("images_done", 0),
    }
