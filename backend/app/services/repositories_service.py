"""Portalcrane - Repositories Service."""

import asyncio
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from ..config import Settings
from .providers import resolve_provider_from_registry
from .registries_service import REGISTRY_REPOS_DIR, get_registry_by_id

logger = logging.getLogger(__name__)


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
    repo_filter: Callable[[str], bool] | None = None,
) -> dict:
    """List repositories available in an external registry.

    Routing:
      - GHCR (ghcr.io)   -> external_github.browse_github_packages
      - Docker Hub        -> external_dockerhub.browse_dockerhub_repositories
      - __local__         -> external_v2 on REGISTRY_HOST (local embedded registry)
      - All other V2      -> external_v2.browse_v2_repositories

    When *repo_filter* is provided, only repositories whose name satisfies the
    predicate are kept (applied before pagination, so total / total_pages stay
    consistent). Used to enforce per-user folder access.

    Returns a paginated dict compatible with the local PaginatedImages shape:
      { items, total, page, page_size, total_pages, error? }
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)
    return await provider.browse_repositories(
        search=search, page=page, page_size=page_size, repo_filter=repo_filter
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
