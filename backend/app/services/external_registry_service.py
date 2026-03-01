"""
Portalcrane - External Registry Service
Manages the list of external registries and exposes skopeo-based
push and synchronisation helpers.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import DATA_DIR, Settings

logger = logging.getLogger(__name__)

# Persistent storage file for user-defined external registries
_REGISTRIES_FILE = Path(f"{DATA_DIR}/external_registries.json")

# In-memory sync job store  {job_id: dict}
_sync_jobs: dict[str, dict] = {}


# ── Registry CRUD helpers ─────────────────────────────────────────────────────


def _load_registries() -> list[dict]:
    """Load registry list from disk. Returns empty list if file is missing."""
    try:
        if _REGISTRIES_FILE.exists():
            return json.loads(_REGISTRIES_FILE.read_text())
    except Exception as exc:
        logger.warning("Failed to load external registries: %s", exc)
    return []


def _save_registries(registries: list[dict]) -> None:
    """Persist registry list to disk, creating directories as needed."""
    try:
        _REGISTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRIES_FILE.write_text(json.dumps(registries, indent=2))
    except Exception as exc:
        logger.error("Failed to save external registries: %s", exc)


def get_registries() -> list[dict]:
    """Return all saved external registries (passwords redacted)."""
    registries = _load_registries()
    # Redact passwords before returning to the frontend
    return [
        {**r, "password": "••••••••" if r.get("password") else ""} for r in registries
    ]


def get_registry_by_id(registry_id: str) -> dict | None:
    """Return a registry by ID (with real password for internal use)."""
    for r in _load_registries():
        if r["id"] == registry_id:
            return r
    return None


def create_registry(name: str, host: str, username: str, password: str) -> dict:
    """Create and persist a new external registry entry."""
    registries = _load_registries()
    entry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "host": host,
        "username": username,
        "password": password,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    registries.append(entry)
    _save_registries(registries)
    return {**entry, "password": "••••••••" if password else ""}


def update_registry(
    registry_id: str,
    name: str | None,
    host: str | None,
    username: str | None,
    password: str | None,
) -> dict | None:
    """Update an existing registry entry. Returns updated entry or None if not found."""
    registries = _load_registries()
    for r in registries:
        if r["id"] == registry_id:
            if name is not None:
                r["name"] = name
            if host is not None:
                r["host"] = host
            if username is not None:
                r["username"] = username
            # Only update password when a non-empty value is provided
            if password:
                r["password"] = password
            _save_registries(registries)
            return {**r, "password": "••••••••" if r.get("password") else ""}
    return None


def delete_registry(registry_id: str) -> bool:
    """Delete a registry entry. Returns True if deleted, False if not found."""
    registries = _load_registries()
    new_list = [r for r in registries if r["id"] != registry_id]
    if len(new_list) == len(registries):
        return False
    _save_registries(new_list)
    return True


# ── Connectivity test ─────────────────────────────────────────────────────────


async def test_registry_connection(host: str, username: str, password: str) -> dict:
    """
    Probe the registry /v2/ endpoint to check reachability and credentials.
    Returns {"reachable": bool, "auth_ok": bool, "message": str}.
    """
    # Normalise host: add https:// if no scheme is present
    url_base = host if "://" in host else f"https://{host}"
    url = f"{url_base.rstrip('/')}/v2/"

    auth = (username, password) if username and password else None
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.get(url, auth=auth)
        if resp.status_code in (200, 401):
            auth_ok = resp.status_code == 200 or (resp.status_code == 401 and not auth)
            return {
                "reachable": True,
                "auth_ok": auth_ok,
                "message": "Registry reachable"
                if auth_ok
                else "Authentication required",
            }
        return {
            "reachable": True,
            "auth_ok": False,
            "message": f"Unexpected status {resp.status_code}",
        }
    except httpx.ConnectError:
        return {"reachable": False, "auth_ok": False, "message": "Connection refused"}
    except Exception as exc:
        logger.warning("Registry connection test failed: %s", exc)
        return {"reachable": False, "auth_ok": False, "message": "Connection failed"}


# ── Validation helpers ────────────────────────────────────────────────────────


def validate_folder_path(folder: str) -> str | None:
    """
    Validate an optional folder/prefix path for image storage.
    Returns the sanitised path or raises ValueError on invalid input.
    - Must not contain '..' (directory traversal)
    - Must not start with '/'
    - Allowed characters: alphanumeric, '-', '_', '.'
    """
    if not folder:
        return None
    if ".." in folder.split("/"):
        raise ValueError("Folder path must not contain '..'")
    if folder.startswith("/"):
        raise ValueError("Folder path must not start with '/'")
    if not re.match(r"^[a-zA-Z0-9._\-/]+$", folder):
        raise ValueError(
            "Folder path contains invalid characters (allowed: a-z A-Z 0-9 . - _ /)"
        )
    return folder.strip("/")


def build_target_path(
    folder: str | None, image_name: str, tag: str, registry_host: str
) -> str:
    """Build the full skopeo destination reference."""
    path = f"{folder}/{image_name}" if folder else image_name
    return f"docker://{registry_host}/{path}:{tag}"


# ── Skopeo push helper ────────────────────────────────────────────────────────


async def skopeo_push(
    oci_dir: str,
    dest_ref: str,
    dest_username: str,
    dest_password: str,
    settings: Settings,
    tls_verify: bool = False,
) -> tuple[bool, str]:
    """
    Push an OCI layout directory to a registry using skopeo.
    Returns (success, message).
    """
    cmd = [
        "skopeo",
        "copy",
        f"--dest-tls-verify={'true' if tls_verify else 'false'}",
    ]
    if dest_username and dest_password:
        cmd += ["--dest-creds", f"{dest_username}:{dest_password}"]
    cmd += [f"oci:{oci_dir}:latest", dest_ref]

    env = {**__import__("os").environ, **settings.env_proxy}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        return True, f"Pushed to {dest_ref}"
    return False, stderr.decode().strip() or stdout.decode().strip()


# ── Sync jobs ─────────────────────────────────────────────────────────────────


def list_sync_jobs() -> list[dict]:
    """Return all sync jobs sorted by start time descending."""
    jobs = list(_sync_jobs.values())
    jobs.sort(key=lambda j: j.get("started_at", ""), reverse=True)
    return jobs


async def run_sync_job(
    source_image: str,
    dest_registry_id: str,
    dest_folder: str | None,
    local_registry_url: str,
    local_username: str,
    local_password: str,
    settings: Settings,
) -> str:
    """
    Start an async sync job. Returns the job ID immediately.
    The actual work runs as a background coroutine.
    """
    job_id = str(uuid.uuid4())
    _sync_jobs[job_id] = {
        "id": job_id,
        "source": source_image,
        "dest_registry_id": dest_registry_id,
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "message": "Starting synchronisation…",
        "error": None,
        "progress": 0,
        "images_total": 0,
        "images_done": 0,
    }

    asyncio.create_task(
        _execute_sync(
            job_id,
            source_image,
            dest_registry_id,
            dest_folder,
            local_registry_url,
            local_username,
            local_password,
            settings,
        )
    )
    return job_id


async def _execute_sync(
    job_id: str,
    source_image: str,
    dest_registry_id: str,
    dest_folder: str | None,
    local_registry_url: str,
    local_username: str,
    local_password: str,
    settings: Settings,
) -> None:
    """
    Internal coroutine: builds the list of (source, destination) pairs and
    copies each image from the local registry to the external one using skopeo.
    """
    dest_registry = get_registry_by_id(dest_registry_id)
    if not dest_registry:
        _sync_jobs[job_id]["status"] = "error"
        _sync_jobs[job_id]["error"] = "Destination registry not found"
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        return

    dest_host = dest_registry["host"]
    dest_user = dest_registry.get("username", "")
    dest_pass = dest_registry.get("password", "")

    # Build the list of image:tag pairs to synchronise
    pairs: list[tuple[str, str]] = []  # [(src_ref, dest_ref)]

    if source_image and source_image != "(all)":
        # Single image — source_image is "repo:tag"
        if ":" in source_image:
            repo, tag = source_image.rsplit(":", 1)
        else:
            repo, tag = source_image, "latest"
        src_host = local_registry_url.replace("http://", "").replace("https://", "")
        src_ref = f"docker://{src_host}/{repo}:{tag}"
        image_name = repo.split("/")[-1] if "/" in repo else repo
        dest_ref = build_target_path(dest_folder, image_name, tag, dest_host)
        pairs.append((src_ref, dest_ref))
    else:
        # All images — enumerate catalog from the local registry
        try:
            from ..services.registry_service import RegistryService

            svc = RegistryService(settings)
            repos = await svc.list_repositories()
            for repo in repos:
                tags = await svc.list_tags(repo)
                src_host = local_registry_url.replace("http://", "").replace(
                    "https://", ""
                )
                for tag in tags:
                    src_ref = f"docker://{src_host}/{repo}:{tag}"
                    image_name = repo.split("/")[-1] if "/" in repo else repo
                    dest_ref = build_target_path(
                        dest_folder, image_name, tag, dest_host
                    )
                    pairs.append((src_ref, dest_ref))
        except Exception as exc:
            _sync_jobs[job_id]["status"] = "error"
            _sync_jobs[job_id]["error"] = f"Failed to enumerate local images: {exc}"
            _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            return

    total = len(pairs)
    _sync_jobs[job_id]["images_total"] = total
    _sync_jobs[job_id]["message"] = f"Syncing {total} image(s)…"

    errors: list[str] = []

    for idx, (src_ref, dest_ref) in enumerate(pairs, start=1):
        _sync_jobs[job_id]["message"] = (
            f"Copying {src_ref} → {dest_ref} ({idx}/{total})"
        )
        _sync_jobs[job_id]["progress"] = int((idx - 1) / max(total, 1) * 100)

        # Build skopeo copy command (registry to registry, no intermediate storage)
        cmd = [
            "skopeo",
            "copy",
            "--dest-tls-verify=false",
            "--src-tls-verify=false",
        ]
        if local_username and local_password:
            cmd += ["--src-creds", f"{local_username}:{local_password}"]
        if dest_user and dest_pass:
            cmd += ["--dest-creds", f"{dest_user}:{dest_pass}"]
        cmd += [src_ref, dest_ref]

        env = {**__import__("os").environ, **settings.env_proxy}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode().strip() or stdout.decode().strip()
            errors.append(f"{src_ref}: {err}")
            logger.warning("Sync failed for %s: %s", src_ref, err)

        _sync_jobs[job_id]["images_done"] = idx

    _sync_jobs[job_id]["progress"] = 100
    _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    if errors:
        _sync_jobs[job_id]["status"] = "partial"
        _sync_jobs[job_id]["error"] = "; ".join(errors[:5])
        _sync_jobs[job_id]["message"] = (
            f"Completed with {len(errors)} error(s) out of {total}"
        )
    else:
        _sync_jobs[job_id]["status"] = "done"
        _sync_jobs[job_id]["message"] = f"✅ {total} image(s) synced successfully"
