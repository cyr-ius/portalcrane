"""
Portalcrane - Transfer Service
==============================
Handles image transfer (copy) operations between any combination of registries
(local <-> local, local <-> external, external <-> external) with optional
Trivy CVE scanning.

Transfer workflow:
  1. Pull source image into a temporary OCI layout directory (via skopeo).
  2. Optionally run Trivy CVE scan on the OCI layout.
  3. If scan passes (or is disabled), push to destination registry via skopeo.
  4. Clean up the temporary OCI layout directory.

Each transfer is tracked as a job in an in-memory dict so the frontend
can poll for status. Job IDs are UUIDs.
"""

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from ..config import REGISTRY_HOST, REGISTRY_URL, STAGING_DIR, Settings
from ..services.providers import resolve_provider_from_registry
from ..services.registries_service import get_registry_by_id
from ..services.trivy_service import (
    TRIVY_BINARY,
    TRIVY_CACHE_DIR,
    TRIVY_SERVER_URL,
    parse_trivy_output,
    resolve_vuln_config,
)

logger = logging.getLogger(__name__)

# ── In-memory transfer job store ──────────────────────────────────────────────

_transfer_jobs: dict[str, dict] = {}


class TransferStatus(str, Enum):
    """Transfer job status values."""

    PENDING = "pending"
    PULLING = "pulling"
    SCANNING = "scanning"
    SCAN_CLEAN = "scan_clean"
    SCAN_VULNERABLE = "scan_vulnerable"
    SCAN_SKIPPED = "scan_skipped"
    PUSHING = "pushing"
    DONE = "done"
    FAILED = "failed"


# ── Pydantic models ───────────────────────────────────────────────────────────


class TransferImageRef(BaseModel):
    """Reference to a single image (repository + tag) to transfer."""

    repository: str
    tag: str


class TransferRequest(BaseModel):
    """
    Request payload to start one or more image transfer jobs.

    Source resolution:
      - source_registry_id = None → local embedded registry (__local__)
      - source_registry_id = "<id>" → saved external registry

    Destination resolution:
      - dest_registry_id = None → local embedded registry
      - dest_registry_id = "<id>" → saved external registry

    dest_folder: optional path prefix for destination images.
    dest_name_override: optional name override (only valid for single-image transfers).
    dest_tag_override: optional tag override (only valid for single-image transfers).
    vuln_scan_enabled_override: None → use server settings.
    vuln_severities_override: None → use server settings.
    """

    images: list[TransferImageRef]
    source_registry_id: str | None = None
    dest_registry_id: str | None = None
    dest_folder: str | None = None
    dest_name_override: str | None = None
    dest_tag_override: str | None = None
    vuln_scan_enabled_override: bool | None = None
    vuln_severities_override: str | None = None


class TransferJob(BaseModel):
    """Single image transfer job state."""

    job_id: str
    status: TransferStatus
    source_registry_id: str | None
    dest_registry_id: str | None
    repository: str
    tag: str
    dest_repository: str
    dest_tag: str
    progress: int = 0
    message: str = ""
    vuln_result: dict | None = None
    error: str | None = None
    created_at: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _staging_root() -> Path:
    """Return the resolved absolute path to the staging root directory."""
    return Path(STAGING_DIR).resolve()


def safe_transfer_path(job_id: str) -> Path:
    """Resolve and validate the OCI layout path for a transfer job."""
    root = _staging_root()
    oci_dir = (root / f"transfer_{job_id}").resolve()
    root_str = str(root)
    oci_str = str(oci_dir)
    if os.path.commonpath([root_str, oci_str]) != root_str:
        raise ValueError(f"Path traversal detected for transfer job: {job_id}")
    return oci_dir


def _get_registry_info(registry_id: str | None) -> tuple[str, str, str, bool]:
    """
    Return (host, username, password, tls_verify) for a registry ID.

    When registry_id is None, returns the local embedded registry coordinates.
    """
    if registry_id is None:
        return (
            REGISTRY_HOST,
            "",
            "",
            not REGISTRY_URL.startswith("http://"),
        )
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry not found: {registry_id}")
    provider = resolve_provider_from_registry(registry)
    return (
        provider.host,
        provider.username,
        provider.password or "",
        provider.verify,
    )


def get_all_transfer_jobs() -> list[dict]:
    """Return all transfer jobs sorted newest first."""
    jobs = list(_transfer_jobs.values())
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs


def get_transfer_job(job_id: str) -> dict | None:
    """Return a single transfer job by ID."""
    return _transfer_jobs.get(job_id)


def delete_transfer_job(job_id: str) -> bool:
    """Delete a transfer job and its OCI directory. Returns True if found."""
    if job_id not in _transfer_jobs:
        return False
    try:
        oci_dir = safe_transfer_path(job_id)
        if oci_dir.exists():
            shutil.rmtree(oci_dir)
    except Exception as exc:
        logger.warning(
            "Failed to clean up OCI dir for transfer job %s: %s", job_id, exc
        )
    del _transfer_jobs[job_id]
    return True


# ── Core pipeline ─────────────────────────────────────────────────────────────


async def _run_transfer_pipeline(
    job_id: str,
    settings: Settings,
    source_registry_id: str | None,
    dest_registry_id: str | None,
    repository: str,
    tag: str,
    dest_repository: str,
    dest_tag: str,
    vuln_scan_enabled_override: bool | None,
    vuln_severities_override: str | None,
) -> None:
    """
    Background task: pull → optional Trivy scan → push.

    Cleans up the temporary OCI layout directory on completion (success or failure).
    """
    oci_dir = safe_transfer_path(job_id)
    skopeo_env = {**os.environ, **settings.env_proxy}

    # Resolve effective scan configuration.
    # Priority: persisted admin override (vuln_override.json) > env vars.
    # vuln_scan_enabled_override / vuln_severities_override from the request
    # are currently always None (the modal inherits the server policy), but
    # the fields are kept for future per-transfer overrides.
    vuln_cfg = resolve_vuln_config(settings)
    do_vuln = (
        vuln_scan_enabled_override
        if vuln_scan_enabled_override is not None
        else vuln_cfg["vuln_scan_enabled"]
    )
    severities: list[str] = (
        [s.strip().upper() for s in vuln_severities_override.split(",") if s.strip()]
        if vuln_severities_override is not None
        else [
            s.strip().upper()
            for s in vuln_cfg["vuln_scan_severities"].split(",")
            if s.strip()
        ]
    )

    def _update(status: TransferStatus, message: str, progress: int = 0) -> None:
        _transfer_jobs[job_id]["status"] = status
        _transfer_jobs[job_id]["message"] = message
        _transfer_jobs[job_id]["progress"] = progress

    try:
        # ── Step 1: Pull source image into OCI layout ─────────────────────────
        _update(TransferStatus.PULLING, f"Pulling {repository}:{tag}...", 10)

        src_host, src_user, src_pass, src_tls = _get_registry_info(source_registry_id)

        src_ref = f"docker://{src_host}/{repository}:{tag}"
        src_creds: list[str] = [f"--src-tls-verify={'true' if src_tls else 'false'}"]
        if src_user and src_pass:
            src_creds += ["--src-creds", f"{src_user}:{src_pass}"]

        pull_cmd = [
            "skopeo",
            "copy",
            "--override-os",
            "linux",
            *src_creds,
            src_ref,
            f"oci:{oci_dir}:latest",
        ]

        pull_proc = await asyncio.create_subprocess_exec(
            *pull_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=skopeo_env,
        )
        _, pull_stderr = await pull_proc.communicate()

        if pull_proc.returncode != 0:
            raise RuntimeError(f"skopeo pull failed: {pull_stderr.decode()}")

        _transfer_jobs[job_id]["progress"] = 40

        # ── Step 2: Optional Trivy vulnerability scan ─────────────────────────
        if do_vuln:
            _update(TransferStatus.SCANNING, "Running Trivy vulnerability scan...", 50)

            trivy_cmd = [
                TRIVY_BINARY,
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
            trivy_stdout, _ = await trivy_proc.communicate()

            vuln_result = parse_trivy_output(trivy_stdout, severities)
            _transfer_jobs[job_id]["vuln_result"] = vuln_result

            if vuln_result["blocked"]:
                # Vulnerabilities found — stop here, do NOT push
                _update(
                    TransferStatus.SCAN_VULNERABLE,
                    "⚠️ Vulnerabilities detected — transfer blocked.",
                    100,
                )
                return
            else:
                _update(TransferStatus.SCAN_CLEAN, "Scan clean. Pushing...", 70)
        else:
            _update(TransferStatus.SCAN_SKIPPED, "Scan skipped. Pushing...", 70)

        # ── Step 3: Push to destination registry ──────────────────────────────
        _update(
            TransferStatus.PUSHING, f"Pushing to {dest_repository}:{dest_tag}...", 80
        )

        dest_host, dest_user, dest_pass, dest_tls = _get_registry_info(dest_registry_id)

        dest_ref = f"docker://{dest_host}/{dest_repository}:{dest_tag}"
        dest_tls_flag = f"--dest-tls-verify={'true' if dest_tls else 'false'}"

        push_cmd = [
            "skopeo",
            "copy",
            dest_tls_flag,
        ]
        if dest_user and dest_pass:
            push_cmd += ["--dest-creds", f"{dest_user}:{dest_pass}"]

        push_cmd += [f"oci:{oci_dir}:latest", dest_ref]

        push_proc = await asyncio.create_subprocess_exec(
            *push_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=skopeo_env,
        )
        _, push_stderr = await push_proc.communicate()

        if push_proc.returncode != 0:
            raise RuntimeError(f"skopeo push failed: {push_stderr.decode()}")

        _update(
            TransferStatus.DONE,
            f"✅ Transferred to {dest_repository}:{dest_tag}",
            100,
        )
        logger.info(
            "Transfer job %s completed: %s:%s → %s:%s",
            job_id,
            repository,
            tag,
            dest_repository,
            dest_tag,
        )

    except Exception as exc:
        logger.error("Transfer job %s failed: %s", job_id, exc)
        _transfer_jobs[job_id]["status"] = TransferStatus.FAILED
        _transfer_jobs[job_id]["error"] = str(exc)
        _transfer_jobs[job_id]["message"] = f"❌ Transfer failed: {exc}"
        _transfer_jobs[job_id]["progress"] = 100

    finally:
        # Always clean up the temporary OCI directory
        if oci_dir.exists():
            shutil.rmtree(oci_dir, ignore_errors=True)


# ── Public API ────────────────────────────────────────────────────────────────


async def start_transfer_jobs(
    request: TransferRequest,
    owner: str,
    settings: Settings,
) -> list[str]:
    """
    Create and start transfer jobs for all requested images.

    Returns the list of created job IDs.
    """
    job_ids: list[str] = []

    for img_ref in request.images:
        repository = img_ref.repository
        tag = img_ref.tag
        job_id = str(uuid.uuid4())

        # Compute destination repository name.
        #
        # Rules (in priority order):
        #   1. dest_name_override (single-image transfer only) → use as-is
        #   2. Destination is the local embedded registry → preserve the full
        #      source path including any namespace (traefik/whoami → traefik/whoami)
        #   3. Destination is an external registry with a username configured →
        #      replace the source namespace with the registry username so the
        #      image is pushed under the owner's account
        #      (traefik/whoami + username=myuser → myuser/whoami)
        #   4. Destination is an external registry without a username →
        #      preserve the full source path
        if request.dest_name_override and len(request.images) == 1:
            dest_name = request.dest_name_override
        elif request.dest_registry_id is None:
            # Local embedded registry → keep the full source path
            dest_name = repository
        else:
            # External registry: replace namespace with the registry username
            # when one is configured; otherwise keep the full source path.
            dest_registry = get_registry_by_id(request.dest_registry_id)
            dest_username = (
                dest_registry.get("username", "").strip() if dest_registry else ""
            )
            leaf = repository.split("/")[-1] if "/" in repository else repository
            dest_name = f"{dest_username}/{leaf}" if dest_username else repository

        dest_repo = (
            f"{request.dest_folder}/{dest_name}" if request.dest_folder else dest_name
        )

        # Tag override only valid for single image
        dest_tag = (
            request.dest_tag_override
            if request.dest_tag_override and len(request.images) == 1
            else tag
        )

        _transfer_jobs[job_id] = {
            "job_id": job_id,
            "status": TransferStatus.PENDING,
            "source_registry_id": request.source_registry_id,
            "dest_registry_id": request.dest_registry_id,
            "repository": repository,
            "tag": tag,
            "dest_repository": dest_repo,
            "dest_tag": dest_tag,
            "progress": 0,
            "message": "Job queued...",
            "vuln_result": None,
            "error": None,
            "owner": owner,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        asyncio.create_task(
            _run_transfer_pipeline(
                job_id=job_id,
                settings=settings,
                source_registry_id=request.source_registry_id,
                dest_registry_id=request.dest_registry_id,
                repository=repository,
                tag=tag,
                dest_repository=dest_repo,
                dest_tag=dest_tag,
                vuln_scan_enabled_override=request.vuln_scan_enabled_override,
                vuln_severities_override=request.vuln_severities_override,
            )
        )

        job_ids.append(job_id)
        logger.info(
            "Transfer job %s created: %s:%s → %s:%s",
            job_id,
            repository,
            tag,
            dest_repo,
            dest_tag,
        )

    return job_ids
