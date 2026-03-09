"""
Portalcrane - External Registry Service
Manages the list of external registries and exposes skopeo-based
push and synchronisation helpers.

Changes vs original:
  - Each registry now has an "owner" field: "global" or a username.
  - get_registries(owner) returns global registries + the user's own registries.
  - Only admins can create global registries (enforced at the router level).
  - [FIX] run_sync_job: replaced skopeo_push(oci_dir="") with skopeo_sync_image()
    which uses docker:// transport on both sides.  The old call produced:
      oci::latest → "open index.json: no such file or directory"
  - [FIX] Namespace rewriting in sync: local image namespace is replaced by
    dest_folder or dest_username so skopeo pushes to the correct namespace.
"""

import asyncio
import json
import logging
import os
import re
from urllib.parse import urlparse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import DATA_DIR, Settings, REGISTRY_HOST

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
    logger.debug(
        "External registry list requested (owner=%s, total=%d)", owner, len(registries)
    )
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
            logger.debug(
                "External registry found by id=%s (host=%s, owner=%s)",
                registry_id,
                r.get("host"),
                r.get("owner", "global"),
            )
            return r
    logger.debug("External registry not found by id=%s", registry_id)
    return None


def _normalize_registry_host(host: str) -> str:
    """Normalise a registry host (strip scheme, path and trailing slash)."""
    value = (host or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlparse(value).netloc or value
    return value.split("/", 1)[0].strip("/")


def find_registry_credentials_for_host(host: str, owner: str) -> tuple[str, str] | None:
    """Return (username, password) for matching host from owner registries, then global."""
    target = _normalize_registry_host(host)
    if not target:
        logger.debug(
            "External registry credential lookup skipped: empty normalized host (input=%s)",
            host,
        )
        return None

    registries = _load_registries()
    owner_matches = [
        r
        for r in registries
        if r.get("owner") == owner
        and _normalize_registry_host(r.get("host", "")) == target
    ]
    global_matches = [
        r
        for r in registries
        if r.get("owner", "global") == "global"
        and _normalize_registry_host(r.get("host", "")) == target
    ]

    logger.debug(
        "External registry credential lookup host=%s owner=%s (owner_matches=%d, global_matches=%d)",
        target,
        owner,
        len(owner_matches),
        len(global_matches),
    )

    for match in [*owner_matches, *global_matches]:
        username = (match.get("username") or "").strip()
        password = match.get("password") or ""
        if username and password:
            logger.debug(
                "External registry credentials resolved host=%s using owner=%s registry_owner=%s",
                target,
                owner,
                match.get("owner", "global"),
            )
            return username, password

    logger.debug(
        "External registry credentials not found host=%s owner=%s", target, owner
    )
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
    logger.debug(
        "External registry created id=%s host=%s owner=%s", entry["id"], host, owner
    )
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
            logger.debug(
                "External registry updated id=%s host=%s owner=%s",
                registry_id,
                r.get("host"),
                r.get("owner", "global"),
            )
            return _redact(r)
    logger.debug("External registry update target not found id=%s", registry_id)
    return None


def delete_registry(registry_id: str) -> bool:
    """Delete a registry entry. Returns True if deleted, False if not found."""
    registries = _load_registries()
    new_list = [r for r in registries if r["id"] != registry_id]
    if len(new_list) == len(registries):
        logger.debug("External registry delete target not found id=%s", registry_id)
        return False
    _save_registries(new_list)
    logger.debug("External registry deleted id=%s", registry_id)
    return True


def delete_registries_for_owner(owner: str) -> int:
    """Delete all personal registries owned by *owner* and return count."""
    registries = _load_registries()
    new_list = [r for r in registries if r.get("owner", "global") != owner]
    deleted_count = len(registries) - len(new_list)
    if deleted_count:
        _save_registries(new_list)
        logger.debug(
            "External registries deleted for owner=%s count=%d", owner, deleted_count
        )
    return deleted_count


# ── Connectivity test ─────────────────────────────────────────────────────────


async def test_registry_connection(host: str, username: str, password: str) -> dict:
    """
    Probe the registry /v2/ endpoint to check reachability and credentials.
    Returns {"reachable": bool, "auth_ok": bool, "message": str}.
    """
    url_base = host if "://" in host else f"https://{host}"
    logger.debug(
        "Testing external registry connectivity host=%s auth=%s",
        host,
        bool(username and password),
    )
    url = f"{url_base.rstrip('/')}/v2/"
    auth = (username, password) if username and password else None
    try:
        async with httpx.AsyncClient(
            timeout=10, verify=False, follow_redirects=True
        ) as client:
            resp = await client.get(url, auth=auth)
        logger.debug(
            "External registry connectivity response host=%s status=%s",
            host,
            resp.status_code,
        )
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
    normalized_host = _normalize_registry_host(registry_host)

    # Docker Hub push expects docker://<namespace>/<image>:<tag> without host.
    if normalized_host in {"docker.io", "index.docker.io", "registry-1.docker.io"}:
        dest_ref = f"docker://{path}:{tag}"
        logger.debug(
            "Docker Hub destination normalized host=%s original_host=%s dest=%s",
            normalized_host,
            registry_host,
            dest_ref,
        )
        return dest_ref

    return f"docker://{registry_host}/{path}:{tag}"


# ── Skopeo helpers ────────────────────────────────────────────────────────────


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

    Used by the Staging pipeline (Pull -> Scan -> Push to local or external
    registry).  The source is always a local OCI directory produced by a
    previous skopeo pull step.

    NOTE: do NOT call this with oci_dir="" — use skopeo_sync_image() instead
    for registry-to-registry copies (Sync feature).

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

    env = {**os.environ, **settings.env_proxy}
    logger.debug(
        "Running skopeo push to external registry dest=%s auth=%s",
        dest_ref,
        bool(dest_username and dest_password),
    )

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


async def skopeo_sync_image(
    src_ref: str,
    dest_ref: str,
    dest_username: str,
    dest_password: str,
    settings: Settings,
    src_username: str = "",
    src_password: str = "",
    src_tls_verify: bool = False,
    dest_tls_verify: bool = False,
) -> tuple[bool, str]:
    """
    Copy an image between two docker:// registries using skopeo copy.

    Used by the Sync feature (Settings -> Sync).  Both source and destination
    use the docker:// transport — no intermediate OCI directory needed.
    This avoids the "oci::latest: open index.json" error that occurs when
    skopeo_push is called with an empty oci_dir.

    Returns (success, message).
    """
    cmd = [
        "skopeo",
        "copy",
        f"--src-tls-verify={'true' if src_tls_verify else 'false'}",
        f"--dest-tls-verify={'true' if dest_tls_verify else 'false'}",
    ]
    if src_username and src_password:
        cmd += ["--src-creds", f"{src_username}:{src_password}"]
    if dest_username and dest_password:
        cmd += ["--dest-creds", f"{dest_username}:{dest_password}"]
    cmd += [src_ref, dest_ref]

    env = {**os.environ, **settings.env_proxy}

    # Log command with credentials masked
    logger.debug(
        "skopeo sync: %s",
        " ".join(
            "***" if i > 0 and cmd[i - 1] in ("--src-creds", "--dest-creds") else a
            for i, a in enumerate(cmd)
        ),
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return True, f"Copied {src_ref} -> {dest_ref}"
    return False, stderr.decode().strip() or stdout.decode().strip()


# ── Sync namespace rewriting ──────────────────────────────────────────────────


def _rewrite_image_name_for_sync(
    img: str,
    dest_folder: str | None,
    dest_username: str,
) -> str:
    """
    Rewrite the image repository name for the destination registry.

    External registries (Docker Hub, GHCR …) use a flat two-level namespace:
      <username>/<image>

    Local images may carry multiple path segments (e.g. "cyrius44/alpine/ansible",
    "production/infra/nginx").  Only the LAST segment is the real image name —
    all intermediate segments are local organisational namespaces that must be
    dropped when syncing to an external registry.

    This mirrors the behaviour of _build_external_target_image() in staging.py
    which uses  image.split("/")[-1]  to extract the bare image name.

    Resolution order:
      1. dest_folder set  → "<dest_folder>/<leaf>"
      2. dest_username    → "<dest_username>/<leaf>"
      3. fallback         → keep image name unchanged

    Examples
    --------
    img="cyrius44/alpine/ansible", dest_username="cyrius44" -> "cyrius44/ansible"
    img="infra/nginx",             dest_username="alice"    -> "alice/nginx"
    img="nginx",                   dest_username="alice"    -> "alice/nginx"
    img="a/b/c",                   dest_folder="myorg"      -> "myorg/c"
    """
    # Always use the last segment as the bare image name — strip all namespaces
    leaf = img.split("/")[-1]

    if dest_folder:
        return f"{dest_folder}/{leaf}"

    if dest_username:
        return f"{dest_username}/{leaf}"

    # No rewrite target — return the leaf alone (no namespace)
    return leaf


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
    """
    Start an asynchronous sync job and return the job ID immediately.

    Copies images from the local registry to an external registry using
    skopeo copy with docker:// transport on both sides.
    No intermediate OCI directory is used.

    Namespace rewriting:
      The local image namespace (first path segment) is replaced by dest_folder
      (priority) or dest_username via _rewrite_image_name_for_sync().
    """
    job_id = str(uuid.uuid4())
    registry = get_registry_by_id(dest_registry_id)
    if not registry:
        raise ValueError(f"Registry {dest_registry_id} not found")

    dest_host = registry["host"]
    dest_username = registry.get("username", "")
    dest_password = registry.get("password", "")

    # Resolve local registry network details before spawning the async task
    src_tls_verify = local_registry_url.startswith("https://")

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
            # Fetch the full list of repositories from the local registry catalog
            catalog_url = f"{local_registry_url}/v2/_catalog?n=1000"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(catalog_url)
                resp.raise_for_status()
                all_images = resp.json().get("repositories", [])

            # Filter to the requested source image when not syncing everything
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
                    tags: list[str] = (
                        tr.json().get("tags") or [] if tr.status_code == 200 else []
                    )

                for tag in tags:
                    # FIX: build docker:// source reference from the local registry.
                    # The previous code called skopeo_push(oci_dir="") which produced
                    # "oci::latest" and caused "open index.json: no such file or directory".
                    src_ref = f"docker://{REGISTRY_HOST}/{img}:{tag}"

                    # Rewrite destination image name to replace the local namespace
                    # with the destination folder or username.
                    dest_image = _rewrite_image_name_for_sync(
                        img=img,
                        dest_folder=dest_folder,
                        dest_username=dest_username,
                    )

                    # build_target_path handles Docker Hub host normalisation.
                    # Pass None as folder: _rewrite_image_name_for_sync already
                    # embedded dest_folder when applicable, avoiding double-prefix.
                    dest_ref = build_target_path(None, dest_image, tag, dest_host)

                    logger.info(
                        "Sync job %s: %s -> %s",
                        job_id,
                        src_ref,
                        dest_ref,
                    )

                    ok, msg = await skopeo_sync_image(
                        src_ref=src_ref,
                        dest_ref=dest_ref,
                        # Local registry has no auth internally
                        src_username="",
                        src_password="",
                        dest_username=dest_username,
                        dest_password=dest_password,
                        settings=settings,
                        src_tls_verify=src_tls_verify,
                        dest_tls_verify=False,
                    )

                    if not ok:
                        errors.append(f"{img}:{tag} — {msg}")
                        logger.warning(
                            "Sync job %s: failed %s:%s — %s",
                            job_id,
                            img,
                            tag,
                            msg,
                        )

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
            logger.exception("Sync job %s failed: %s", job_id, exc)
            _sync_jobs[job_id]["status"] = "error"
            _sync_jobs[job_id]["error"] = str(exc)
            _sync_jobs[job_id]["message"] = f"Sync failed: {exc}"
        finally:
            _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            _sync_jobs[job_id]["progress"] = 100

    import asyncio as _asyncio

    _asyncio.create_task(_run())
    return job_id
