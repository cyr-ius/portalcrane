"""Portalcrane - Repositories Service."""

import asyncio
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import REGISTRY_HOST, Settings
from .providers import build_target_path, resolve_provider_from_registry
from .registries_service import REGISTRY_REPOS_DIR, get_registry_by_id

logger = logging.getLogger(__name__)

_sync_jobs: dict[str, dict] = {}


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
        repo_path = Path(REGISTRY_REPOS_DIR) / repo
        try:
            resolved = repo_path.resolve()
            base = Path(REGISTRY_REPOS_DIR).resolve()
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
