"""Portalcrane - External Registry Service.

Orchestrates CRUD, connectivity testing, catalog browsing, and skopeo-based
push / sync / import operations for all external registry types.

Registry type routing:
  - ghcr.io            -> external_github   (GitHub Packages REST API)
  - docker.io variants -> external_dockerhub (Docker Hub REST API)
  - everything else    -> external_v2        (OCI Distribution V2 spec)

Local registry system entry:
  A hidden system registry with ID "__local__" is injected into the registry
  list so the frontend can use the unified V2 browse / tag-detail infrastructure
  for the local embedded registry without it appearing in the External Registries
  settings panel (filtered by the system=True flag).
"""

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import DATA_DIR, REGISTRY_HOST, Settings
from .providers import (
    build_target_path,
    resolve_provider,
    resolve_provider_from_registry,
)

logger = logging.getLogger(__name__)

_REGISTRY_DATA_DIR = f"{DATA_DIR}/registry"
_REGISTRY_REPOS_DIR = f"{_REGISTRY_DATA_DIR}/docker/registry/v2/repositories"
_REGISTRIES_FILE = Path(f"{DATA_DIR}/external_registries.json")

_sync_jobs: dict[str, dict] = {}

# ── Local registry system entry ────────────────────────────────────────────────
# Reserved ID for the embedded local registry exposed as a hidden V2 source.
# This entry is injected into the registry list at runtime so the frontend
# can use the unified V2 browse/tag-detail infrastructure for the local registry
# without it appearing in the External Registries settings panel.
LOCAL_REGISTRY_SYSTEM_ID = "__local__"


def _get_local_registry_entry() -> dict:
    """Return the embedded local registry as a hidden system registry entry.

    This entry is marked with system=True so the frontend can filter it out
    of the External Registries settings panel while still using it as a source
    in the Images browser and Staging pipeline.

    The local registry runs on plain HTTP (no TLS) on localhost:5000 inside
    the container, managed by supervisord.
    """
    return {
        "id": LOCAL_REGISTRY_SYSTEM_ID,
        "name": "Local Registry",
        "host": REGISTRY_HOST,
        "username": "",
        "password": "",
        "owner": "global",
        "use_tls": False,
        "tls_verify": False,
        "browsable": True,
        "system": True,
    }


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
    """Return a copy of the registry dict with the password redacted.

    tls_verify, use_tls, browsable and system fields are preserved as-is;
    defaults applied for old entries that predate these fields.

    browsable defaults to True for backward compatibility: existing entries
    continue to appear in source selectors until they are saved again, at
    which point check_catalog_browsable() re-evaluates the field.
    """
    return {
        **r,
        "password": "••••••••" if r.get("password") else "",
        "use_tls": r.get("use_tls", True),
        "tls_verify": r.get("tls_verify", True),
        "browsable": r.get("browsable", True),
        "system": r.get("system", False),
    }


# ── Registry store public API ─────────────────────────────────────────────────


def get_registries(owner: str | None = None, include_system: bool = True) -> list[dict]:
    """Return saved external registries (passwords redacted).

    When *owner* is provided only global + owner registries are returned.
    When *owner* is None (admin) all registries are returned.

    The hidden local registry system entry is prepended when include_system=True
    so the frontend can use it as a source without it persisting to disk.

    Args:
        owner:          Filter by owner username; None returns all registries.
        include_system: When True (default), prepend the local system registry.
    """
    registries = _load_registries()
    logger.debug(
        "External registry list requested (owner=%s, total=%d)", owner, len(registries)
    )

    result: list[dict] = []

    # Prepend the hidden local system registry entry
    if include_system:
        result.append(_get_local_registry_entry())

    if owner is None:
        result += [_redact(r) for r in registries]
    else:
        result += [
            _redact(r)
            for r in registries
            if r.get("owner", "global") in ("global", owner)
        ]

    return result


def get_registry_by_id(registry_id: str) -> dict | None:
    """Return a registry by ID (with real password for internal use).

    The special LOCAL_REGISTRY_SYSTEM_ID returns the local system entry
    without any disk lookup.
    """
    # Intercept the reserved local registry ID
    if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
        return _get_local_registry_entry()

    for r in _load_registries():
        if r["id"] == registry_id:
            if "use_tls" not in r:
                r["use_tls"] = True
            if "tls_verify" not in r:
                r["tls_verify"] = True
            return r
    return None


def delete_registry(registry_id: str) -> bool:
    """Delete a registry entry. Returns True if deleted, False if not found.

    The local system registry cannot be deleted.
    """
    if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
        return False
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


async def purge_registry(registry_id: str) -> tuple[list[str], list[dict]]:
    """Purge ghost repositories directly from the local filesystem.

    Returns a tuple (purged, errors)
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    purged: list[str] = []
    errors: list[dict] = []

    empty = await empty_tags(registry_id=registry_id)

    if not empty:
        errors.append({"repo": "", "error": "No empty repositories found"})

    for repo in empty:
        repo_path = Path(_REGISTRY_REPOS_DIR) / repo
        try:
            resolved = repo_path.resolve()
            base = Path(_REGISTRY_REPOS_DIR).resolve()
            if not str(resolved).startswith(str(base)):
                errors.append({"repo": repo, "error": "Path traversal attempt blocked"})
                continue
            if resolved.exists():
                shutil.rmtree(resolved)
            purged.append(repo)
        except OSError as exc:
            logger.error("Failed to purge repository %s: %s", repo, exc)
            errors.append({"repo": repo, "error": "Deletion failed"})
        except Exception:
            logger.exception("Unexpected error purging repository %s", repo)
            errors.append({"repo": repo, "error": "Unexpected error"})

    return purged, errors


# ── Registry create / update ──────────────────────────────────────────────────


async def create_registry(
    name: str,
    host: str,
    username: str,
    password: str,
    owner: str = "global",
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict:
    """Create and persist a new external registry entry.

    *use_tls*    — when False, all HTTP connections use plain http://.
    *tls_verify* — when False, TLS certificate errors are ignored (HTTPS only).

    The *browsable* field is probed immediately after creation so the UI
    can show/hide the registry in the Images source selector without an
    additional catalog-check request.
    """

    checks = await test_registry_connection(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )

    if not checks["reachable"] or not checks["auth_ok"]:
        return checks

    browsable = await check_catalog_browsable(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    registry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "host": host,
        "username": username,
        "password": password,
        "owner": owner,
        "use_tls": use_tls,
        "tls_verify": tls_verify,
        "browsable": browsable,
        "system": False,
    }
    registries = _load_registries()
    registries.append(registry)
    _save_registries(registries)
    logger.debug(
        "External registry created id=%s host=%s owner=%s browsable=%s",
        registry["id"],
        host,
        owner,
        browsable,
    )
    return _redact(registry)


async def update_registry(
    registry_id: str,
    name: str | None = None,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    owner: str | None = None,
    use_tls: bool | None = None,
    tls_verify: bool | None = None,
) -> dict | None:
    """Update a registry entry (partial update — only supplied fields are changed).

    The local system registry cannot be updated via this function.

    use_tls/tls_verify are only changed when explicitly supplied.
    browsable is re-evaluated whenever any connectivity-related field
    (host, username, password, use_tls, tls_verify) changes.
    """
    # Protect the local system registry from accidental modification
    if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
        return None

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
            if use_tls is not None:
                r["use_tls"] = use_tls
            if tls_verify is not None:
                r["tls_verify"] = tls_verify

            # Re-check authentication
            checks = await test_registry_connection(
                host=r["host"],
                username=r.get("username", ""),
                password=r.get("password", ""),
                use_tls=r.get("use_tls", True),
                tls_verify=r.get("tls_verify", True),
            )

            if not checks["reachable"] or not checks["auth_ok"]:
                return checks

            # Re-check browsability when any connectivity parameter changes
            connectivity_changed = any(
                v is not None for v in [host, username, password, use_tls, tls_verify]
            )
            if connectivity_changed:
                r["browsable"] = await check_catalog_browsable(
                    host=r["host"],
                    username=r.get("username", ""),
                    password=r.get("password", ""),
                    use_tls=r.get("use_tls", True),
                    tls_verify=r.get("tls_verify", True),
                )

            _save_registries(registries)
            logger.debug(
                "External registry updated id=%s host=%s owner=%s use_tls=%s "
                "tls_verify=%s browsable=%s",
                registry_id,
                r.get("host"),
                r.get("owner", "global"),
                r.get("use_tls", True),
                r.get("tls_verify", True),
                r.get("browsable"),
            )
            return _redact(r)
    return None


# ── Connectivity & catalog ────────────────────────────────────────────────────


async def test_registry_connection(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict:
    """Probe the registry to check reachability and validate credentials.

    Delegates to external_v2.test_v2_connection for all registry types
    (GHCR and Docker Hub also expose the standard /v2/ ping endpoint).

    Returns {"reachable": bool, "auth_ok": bool, "message": str}.
    """

    provider = resolve_provider(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    return await provider.test_connection()


async def check_catalog_browsable(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> bool:
    """Determine whether this registry supports repository listing.

    Routing:
      - Docker Hub (docker.io) — never exposes /v2/_catalog; browsable only
        when credentials are present (Hub REST API is used instead).
      - GHCR (ghcr.io) — always considered browsable when a token is stored
        (GitHub Packages API is used).
      - All other registries — probe /v2/_catalog via external_v2.check_v2_catalog.

    The result is stored in the ``browsable`` field of the registry entry so
    the frontend can hide non-browsable registries from the Images source
    selector without making additional requests.
    """

    provider = resolve_provider(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    return await provider.check_catalog()


async def ping_catalog(registry_id: str) -> bool:
    """Check local registry connectivity."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)

    return await provider.ping()


# ── Browse — public orchestration API ─────────────────────────────────────────


async def browse_images(
    registry_id: str,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """List repositories available in an external registry.

    Routing:
      - GHCR (ghcr.io)   -> external_github.browse_github_packages
      - Docker Hub        -> external_dockerhub.browse_dockerhub_repositories
      - __local__         -> external_v2 on REGISTRY_HOST (local embedded registry)
      - All other V2      -> external_v2.browse_v2_repositories

    Returns a paginated dict compatible with the local PaginatedImages shape:
      { items, total, page, page_size, total_pages, error? }
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    return await provider.browse_repositories(
        search=search, page=page, page_size=page_size
    )


async def browse_tags(registry_id: str, repository: str) -> dict:
    """List tags for a repository in an external registry.

    Routing:
      - Docker Hub -> external_dockerhub.browse_dockerhub_tags
      - GHCR       -> external_v2.browse_v2_tags (standard /v2/ tags endpoint)
      - __local__  -> external_v2.browse_v2_tags on local registry
      - All other  -> external_v2.browse_v2_tags

    Returns {"repository": str, "tags": list[str]}.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    tags = await provider.browse_tags(repository=repository)

    return {"repository": repository, "tags": tags}


async def remove_image(registry_id: str, repository: str) -> dict:
    """Delete all tags for a repository in an external registry.

    The local system registry (__local__) is protected — deletion is delegated
    to the standard V2Provider which enforces registry-level delete permission.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    error = await provider.delete_repository(repository)

    if error:
        return {
            "deleted_tags": [],
            "failed_tags": [repository],
            "message": f"Delete failed: {error}",
        }
    return {
        "deleted_tags": [repository],
        "failed_tags": [],
        "message": f"Package '{repository}' deleted from {provider.host}",
    }


async def metadata_by_tag(registry_id: str, repository: str, tag: str) -> dict:
    """Return full image metadata for a specific tag in an external V2 registry.

    Works for both external registries and the local system registry (__local__).
    Returns an empty dict when the registry type is unsupported or on error.

    Args:
        registry_id: ID of the saved external registry or "__local__".
        repository:  Repository path, e.g. "myorg/myimage".
        tag:         Tag name, e.g. "latest".

    Returns:
        Dict matching the ImageDetail schema (name, tag, digest, size,
        created, architecture, os, layers, labels, env, cmd, entrypoint,
        exposed_ports) or empty dict on failure.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    return await provider.get_tag_detail(repository, tag)


async def remove_tag(registry_id: str, repository: str, tag: str) -> dict:
    """Delete a single tag from an external V2 registry or the local registry.

    Args:
        registry_id: ID of the saved external registry or "__local__".
        repository:  Repository path.
        tag:         Tag name to delete.

    Returns:
        Dict with keys: success (bool), message (str).
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    return await provider.delete_tag(repository, tag)


async def append_tag(
    registry_id: str, repository: str, source_tag: str, new_tag: str
) -> dict:
    """Create a new tag by copying a manifest in an external V2 registry.

    Works for both external registries and the local system registry (__local__).

    Args:
        registry_id: ID of the saved external registry or "__local__".
        repository:  Repository path.
        source_tag:  Existing tag whose manifest will be copied.
        new_tag:     New tag name to create.

    Returns:
        Dict with keys: success (bool), message (str).
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    return await provider.add_tag(repository, source_tag, new_tag)


async def empty_tags(registry_id: str) -> list[str]:
    """List repositories that have no tags."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    return await provider.list_empty_repositories()


# ── Validation helpers ────────────────────────────────────────────────────────


def validate_folder_path(folder: str) -> str | None:
    """Validate an optional folder/prefix path. Returns sanitised path or raises ValueError."""

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


# ── Skopeo helpers ────────────────────────────────────────────────────────────


async def skopeo_copy_oci_image(
    oci_dir: str,
    dest_ref: str,
    dest_username: str,
    dest_password: str,
    settings: Settings,
    tls_verify: bool = True,
) -> tuple[bool, str]:
    """Push an OCI layout directory to a registry using skopeo copy."""
    cmd = [
        "skopeo",
        "copy",
        f"--dest-tls-verify={'true' if tls_verify else 'false'}",
    ]
    if dest_username and dest_password:
        cmd += ["--dest-creds", f"{dest_username}:{dest_password}"]
    cmd += [f"oci:{oci_dir}:latest", dest_ref]

    env = {**os.environ, **settings.env_proxy}
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


async def skopeo_copy_image_image(
    src_ref: str,
    dest_ref: str,
    dest_username: str,
    dest_password: str,
    settings: Settings,
    src_username: str = "",
    src_password: str = "",
    src_tls_verify: bool = False,
    dest_tls_verify: bool = True,
) -> tuple[bool, str]:
    """Copy an image between two docker:// registries using skopeo copy.

    Used by both export (local->external) and import (external->local) jobs.
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
    logger.debug(
        "skopeo copy: %s",
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
        return True, f"Copied {src_ref} → {dest_ref}"
    return False, stderr.decode().strip() or stdout.decode().strip()


# ── Sync namespace rewriting ──────────────────────────────────────────────────


def _rewrite_image_name_for_sync(
    img: str,
    dest_folder: str | None,
    dest_username: str,
) -> str:
    """Rewrite the image repository name for the destination registry.

    Rules (applied in order):
      1. dest_folder supplied -> replace namespace with folder prefix
      2. dest_username supplied -> prepend username (Docker Hub compat)
      3. No override -> preserve the full source path including namespace
    """
    leaf = img.split("/")[-1]
    if dest_folder:
        return f"{dest_folder}/{leaf}"
    if dest_username:
        return f"{dest_username}/{leaf}"
    return img


# ── Sync / Import jobs ────────────────────────────────────────────────────────


def list_sync_jobs() -> list[dict]:
    """Return all sync/import jobs sorted by start time descending."""
    jobs = list(_sync_jobs.values())
    jobs.sort(key=lambda j: j.get("started_at", ""), reverse=True)
    return jobs


async def run_export_job(
    source_image: str,
    dest_registry_id: str,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> str:
    """Start an asynchronous export job (local -> external) and return the job ID.

    The job dict carries direction="export" for display in the history list.
    """
    registry = get_registry_by_id(dest_registry_id)
    if not registry:
        raise ValueError(f"Registry {dest_registry_id} not found")

    job_id = str(uuid.uuid4())
    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "export",
        "source_image": source_image,
        "dest_registry_id": dest_registry_id,
        "dest_registry_name": registry.get("name", ""),
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "images_total": 0,
        "images_done": 0,
        "errors": [],
    }

    asyncio.create_task(
        _run_export_job_task(
            job_id=job_id,
            source_image=source_image,
            registry=registry,
            dest_folder=dest_folder,
            local_registry_url=local_registry_url,
            settings=settings,
        )
    )
    return job_id


async def _run_export_job_task(
    job_id: str,
    source_image: str,
    registry: dict,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> None:
    """Background task: export images from local registry to an external registry."""
    provider = resolve_provider_from_registry(registry)

    dest_host = provider.host
    dest_username = provider.username
    dest_password = provider.password or ""
    dest_tls_verify = provider.verify

    src_tls_verify = not local_registry_url.startswith("http://")

    try:
        # Resolve image list from local registry
        if source_image == "(all)":
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{local_registry_url}/v2/_catalog?n=1000")
                resp.raise_for_status()
                images: list[str] = resp.json().get("repositories", [])
        else:
            images = [source_image.split(":")[0]]

        _sync_jobs[job_id]["images_total"] = len(images)

        errors: list[str] = []
        for img in images:
            # Fetch tags from local registry
            async with httpx.AsyncClient(timeout=30) as client:
                tr = await client.get(f"{local_registry_url}/v2/{img}/tags/list")
                tags: list[str] = (
                    tr.json().get("tags") or [] if tr.status_code == 200 else []
                )

            for tag in tags:
                src_ref = build_target_path(None, img, tag, REGISTRY_HOST)
                dest_image = _rewrite_image_name_for_sync(
                    img=img,
                    dest_folder=dest_folder,
                    dest_username=dest_username,
                )
                dest_ref = build_target_path(None, dest_image, tag, dest_host)

                ok, msg = await skopeo_copy_image_image(
                    src_ref=src_ref,
                    dest_ref=dest_ref,
                    src_username="",
                    src_password="",
                    dest_username=dest_username,
                    dest_password=dest_password,
                    settings=settings,
                    src_tls_verify=src_tls_verify,
                    dest_tls_verify=dest_tls_verify,
                )
                if not ok:
                    errors.append(f"{img}:{tag} — {msg}")
                    logger.warning("sync export error %s:%s — %s", img, tag, msg)

            _sync_jobs[job_id]["images_done"] = _sync_jobs[job_id]["images_done"] + 1

        _sync_jobs[job_id]["status"] = "done" if not errors else "done_with_errors"
        _sync_jobs[job_id]["errors"] = errors
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        logger.error("sync export job %s failed: %s", job_id, exc)
        _sync_jobs[job_id]["status"] = "failed"
        _sync_jobs[job_id]["errors"] = [str(exc)]
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


async def run_import_job(
    source_registry_id: str,
    source_image: str,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> str:
    """Start an asynchronous import job (external -> local) and return the job ID.

    The job dict carries direction="import" for display in the history list.
    """
    registry = get_registry_by_id(source_registry_id)
    if not registry:
        raise ValueError(f"Registry {source_registry_id} not found")

    job_id = str(uuid.uuid4())
    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "import",
        "source_image": source_image,
        "source_registry_id": source_registry_id,
        "source_registry_name": registry.get("name", ""),
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "images_total": 0,
        "images_done": 0,
        "errors": [],
    }

    asyncio.create_task(
        _run_import_job_task(
            job_id=job_id,
            source_image=source_image,
            registry=registry,
            dest_folder=dest_folder,
            local_registry_url=local_registry_url,
            settings=settings,
        )
    )
    return job_id


async def _run_import_job_task(
    job_id: str,
    source_image: str,
    registry: dict,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> None:
    """Background task: import images from an external registry into the local registry."""
    src_host = registry["host"]
    src_username = registry.get("username", "")
    src_password = registry.get("password", "")
    src_use_tls = registry.get("use_tls", True)
    src_tls_verify_field = registry.get("tls_verify", True)
    src_tls_verify = False if not src_use_tls else src_tls_verify_field
    dest_tls_verify = not local_registry_url.startswith("http://")

    provider = resolve_provider_from_registry(registry=registry)
    result = await provider.browse_repositories(search=None, page=1, page_size=200)

    try:
        # ── Resolve image list from source registry ────────────────────────
        if source_image == "(all)":
            images: list[str] = [item["name"] for item in result.get("items", [])]
        else:
            images = [source_image.split(":")[0]]

        _sync_jobs[job_id]["images_total"] = len(images)

        errors: list[str] = []
        for img in images:
            # ── Resolve tags per image ─────────────────────────────────────
            if source_image != "(all)" and ":" in source_image:
                tags = [source_image.split(":", 1)[1]]
            else:
                tags = await provider.get_tags_for_import(repository=img)

            # ── Rewrite destination image name ─────────────────────────────
            dest_img = _rewrite_image_name_for_sync(
                img=img, dest_folder=dest_folder, dest_username=""
            )
            for tag in tags:
                src_ref = build_target_path(None, img, tag, src_host)
                dest_ref = build_target_path(None, dest_img, tag, REGISTRY_HOST)

                ok, msg = await skopeo_copy_image_image(
                    src_ref=src_ref,
                    dest_ref=dest_ref,
                    src_username=src_username,
                    src_password=src_password,
                    dest_username="",
                    dest_password="",
                    settings=settings,
                    src_tls_verify=src_tls_verify,
                    dest_tls_verify=dest_tls_verify,
                )
                if not ok:
                    errors.append(f"{img}:{tag} — {msg}")
                    logger.warning("sync import error %s:%s — %s", img, tag, msg)

            _sync_jobs[job_id]["images_done"] = _sync_jobs[job_id]["images_done"] + 1

        _sync_jobs[job_id]["status"] = "done" if not errors else "done_with_errors"
        _sync_jobs[job_id]["errors"] = errors
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        logger.error("sync import job %s failed: %s", job_id, exc)
        _sync_jobs[job_id]["status"] = "failed"
        _sync_jobs[job_id]["errors"] = [str(exc)]
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
