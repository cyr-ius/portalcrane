"""
Portalcrane - External Registry Service
Manages the list of external registries and exposes skopeo-based
push and synchronisation helpers.

Changes vs original:
  - Each registry now has an "owner" field: "global" or a username.
  - get_registries(owner) returns global registries + the user's own registries.
  - Only admins can create global registries (enforced at the router level).
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

_REGISTRIES_FILE = Path(f"{DATA_DIR}/external_registries.json")
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


def _redact(r: dict) -> dict:
    """Return a copy of the registry dict with the password redacted."""
    return {**r, "password": "••••••••" if r.get("password") else ""}


def get_registries(owner: str | None = None) -> list[dict]:
    """
    Return saved external registries (passwords redacted).

    When *owner* is provided (non-admin user), only global registries and
    the user's own registries are returned.
    When *owner* is None (admin), all registries are returned.
    """
    registries = _load_registries()
    if owner is None:
        # Admin: return everything
        return [_redact(r) for r in registries]
    # Regular user: global entries + own entries
    return [
        _redact(r) for r in registries if r.get("owner", "global") in ("global", owner)
    ]


def get_registry_by_id(registry_id: str) -> dict | None:
    """Return a registry by ID (with real password for internal use)."""
    for r in _load_registries():
        if r["id"] == registry_id:
            return r
    return None


def create_registry(
    name: str,
    host: str,
    username: str,
    password: str,
    owner: str = "global",
) -> dict:
    """
    Create and persist a new external registry entry.

    *owner* is "global" for admin-created shared registries, or a username
    for personal registries accessible only to that user.
    """
    registries = _load_registries()
    entry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "host": host,
        "username": username,
        "password": password,
        "owner": owner,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    registries.append(entry)
    _save_registries(registries)
    return _redact(entry)


def update_registry(
    registry_id: str,
    name: str | None,
    host: str | None,
    username: str | None,
    password: str | None,
    owner: str | None = None,
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
            if password:
                r["password"] = password
            if owner is not None:
                r["owner"] = owner
            _save_registries(registries)
            return _redact(r)
    return None


def delete_registry(registry_id: str) -> bool:
    """Delete a registry entry. Returns True if deleted, False if not found."""
    registries = _load_registries()
    new_list = [r for r in registries if r["id"] != registry_id]
    if len(new_list) == len(registries):
        return False
    _save_registries(new_list)
    return True


def delete_registries_for_owner(owner: str) -> int:
    """Delete all personal registries owned by *owner* and return count."""
    registries = _load_registries()
    new_list = [r for r in registries if r.get("owner", "global") != owner]
    deleted_count = len(registries) - len(new_list)
    if deleted_count:
        _save_registries(new_list)
    return deleted_count


# ── Connectivity test ─────────────────────────────────────────────────────────


async def test_registry_connection(host: str, username: str, password: str) -> dict:
    """
    Probe the registry /v2/ endpoint to check reachability and credentials.
    Returns {"reachable": bool, "auth_ok": bool, "message": str}.
    """
    url_base = host if "://" in host else f"https://{host}"
    url = f"{url_base.rstrip('/')}/v2/"
    auth = (username, password) if username and password else None
    try:
        async with httpx.AsyncClient(
            timeout=10, verify=False, follow_redirects=True
        ) as client:
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
    settings: Settings,
) -> str:
    """Start an async sync job. Returns the job ID immediately."""

    job_id = str(uuid.uuid4())
    registry = get_registry_by_id(dest_registry_id)
    if not registry:
        raise ValueError(f"Registry {dest_registry_id} not found")

    dest_host = registry["host"]
    dest_username = registry.get("username", "")
    dest_password = registry.get("password", "")

    _sync_jobs[job_id] = {
        "id": job_id,
        "source": source_image,
        "dest_registry_id": dest_registry_id,
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "message": "Sync started",
        "error": None,
        "progress": 0,
        "images_total": 0,
        "images_done": 0,
    }

    async def _run() -> None:
        try:
            catalog_url = f"{local_registry_url}/v2/_catalog?n=1000"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(catalog_url)
                resp.raise_for_status()
                all_images = resp.json().get("repositories", [])

            images = (
                all_images
                if source_image == "(all)"
                else [i for i in all_images if source_image.split(":")[0] in i]
            )
            _sync_jobs[job_id]["images_total"] = len(images)

            errors: list[str] = []
            for img in images:
                tags_url = f"{local_registry_url}/v2/{img}/tags/list"
                async with httpx.AsyncClient(timeout=30) as client:
                    tr = await client.get(tags_url)
                    tags = tr.json().get("tags") or [] if tr.status_code == 200 else []

                for tag in tags:
                    dest = build_target_path(dest_folder, img, tag, dest_host)
                    ok, msg = await skopeo_push(
                        oci_dir="",
                        dest_ref=dest,
                        dest_username=dest_username,
                        dest_password=dest_password,
                        settings=settings,
                    )
                    if not ok:
                        errors.append(f"{img}:{tag} — {msg}")

                _sync_jobs[job_id]["images_done"] += 1
                _sync_jobs[job_id]["progress"] = int(
                    100 * _sync_jobs[job_id]["images_done"] / max(len(images), 1)
                )

            _sync_jobs[job_id]["status"] = "partial" if errors else "done"
            _sync_jobs[job_id]["message"] = (
                f"Completed with {len(errors)} error(s)" if errors else "Sync complete"
            )
            _sync_jobs[job_id]["error"] = "\n".join(errors) if errors else None
        except Exception as exc:
            _sync_jobs[job_id]["status"] = "error"
            _sync_jobs[job_id]["error"] = str(exc)
            _sync_jobs[job_id]["message"] = f"Sync failed: {exc}"
        finally:
            _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            _sync_jobs[job_id]["progress"] = 100

    import asyncio as _asyncio

    _asyncio.create_task(_run())
    return job_id
