"""
Portalcrane - Registry Router
All endpoints for browsing and managing Docker Registry images and tags
"""

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..config import DATA_DIR, REGISTRY_URL, Settings, get_settings
from ..services.registry_service import RegistryService
from .auth import (
    UserInfo,
    require_admin,
    require_pull_access,
    require_push_access,
)

router = APIRouter()

REGISTRY_BINARY = "/usr/local/bin/registry"
REGISTRY_CONFIG = "/etc/registry/config.yml"
REGISTRY_DATA_DIR = f"{DATA_DIR}/registry"
REGISTRY_REPOS_DIR = f"{REGISTRY_DATA_DIR}/docker/registry/v2/repositories"

SUPERVISORD_RPC_URL = "http://127.0.0.1:9001/RPC2"


# ─── Models ──────────────────────────────────────────────────────────────────


class TagInfo(BaseModel):
    """Tag information model."""

    name: str
    digest: str = ""
    size: int = 0
    created: str = ""
    architecture: str = ""
    os: str = ""


class ImageInfo(BaseModel):
    """Image/repository information model."""

    name: str
    tags: list[str] = []
    tag_count: int = 0
    total_size: int = 0


class ImageDetail(BaseModel):
    """Detailed image information for advanced mode."""

    name: str
    tag: str
    digest: str
    size: int
    created: str
    architecture: str
    os: str
    layers: list[dict] = []
    labels: dict = {}
    env: list[str] = []
    cmd: list[str] = []
    entrypoint: list[str] = []
    exposed_ports: dict = {}


class PaginatedImages(BaseModel):
    """Paginated list of images."""

    items: list[ImageInfo]
    total: int
    page: int
    page_size: int
    total_pages: int


class AddTagRequest(BaseModel):
    """Request to add a new tag to an existing image."""

    source_tag: str
    new_tag: str


class RenameImageRequest(BaseModel):
    """Request to retag an image to a new repository/tag."""

    new_repository: str
    new_tag: str


# ─── Dependency ──────────────────────────────────────────────────────────────


def get_registry(settings: Settings = Depends(get_settings)) -> RegistryService:
    """Dependency to get registry service instance."""
    return RegistryService(settings)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/images", response_model=PaginatedImages)
async def list_images(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=100),
    search: str | None = Query(None),
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_pull_access),
):
    """List all images with pagination and optional search filter."""
    repositories = await registry.list_repositories()

    # Filter by search term
    if search:
        repositories = [r for r in repositories if search.lower() in r.lower()]

    total = len(repositories)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    page_repos = repositories[start:end]

    # Fetch tag info for current page
    tags_list = await asyncio.gather(*[registry.list_tags(r) for r in page_repos])
    items = [
        ImageInfo(name=repo, tags=tags, tag_count=len(tags), total_size=0)
        for repo, tags in zip(page_repos, tags_list)
    ]

    return PaginatedImages(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/images/{repository:path}/tags")
async def get_image_tags(
    repository: str,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_pull_access),
):
    """Get all tags for a specific image repository."""
    tags = await registry.list_tags(repository)
    return {"repository": repository, "tags": tags}


@router.get("/images/{repository:path}/tags/{tag}/detail", response_model=ImageDetail)
async def get_tag_detail(
    repository: str,
    tag: str,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_pull_access),
):
    """Get detailed information about a specific image tag (advanced mode)."""
    manifest = await registry.get_manifest(repository, tag)
    if not manifest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    config_digest = manifest.get("config", {}).get("digest", "")
    config = {}
    if config_digest:
        config = await registry.get_image_config(repository, config_digest)

    container_config = config.get("config", config.get("container_config", {}))

    # Extract architecture and OS from platform or config
    architecture = config.get("architecture", "")
    os_name = config.get("os", "")

    # Calculate total size
    layers = manifest.get("layers", [])
    total_size = sum(layer.get("size", 0) for layer in layers)

    return ImageDetail(
        name=repository,
        tag=tag,
        digest=manifest.get("_digest", ""),
        size=total_size,
        created=config.get("created", ""),
        architecture=architecture,
        os=os_name,
        layers=layers,
        labels=container_config.get("Labels", {}) or {},
        env=container_config.get("Env", []) or [],
        cmd=container_config.get("Cmd", []) or [],
        entrypoint=container_config.get("Entrypoint", []) or [],
        exposed_ports=container_config.get("ExposedPorts", {}) or {},
    )


@router.delete("/images/{repository:path}/tags/{tag}")
async def delete_tag(
    repository: str,
    tag: str,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_push_access),
):
    """Delete a specific tag from an image repository."""
    success = await registry.delete_tag(repository, tag)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete tag",
        )
    return {"message": f"Tag '{tag}' deleted from '{repository}'"}


@router.delete("/images/{repository:path}")
async def delete_image(
    repository: str,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_push_access),
):
    """Delete all tags (and the image) from a repository."""
    tags = await registry.list_tags(repository)
    if not tags:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found or has no tags",
        )

    errors = []
    for tag in tags:
        success = await registry.delete_tag(repository, tag)
        if not success:
            errors.append(tag)

    if errors:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete tags: {errors}",
        )

    return {"message": f"Image '{repository}' and all its tags deleted"}


@router.post("/images/{repository:path}/tags")
async def add_tag(
    repository: str,
    request: AddTagRequest,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_push_access),
):
    """Add a new tag to an existing image (retag)."""
    # Get the source manifest
    manifest = await registry.get_manifest(repository, request.source_tag)
    if not manifest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source tag '{request.source_tag}' not found",
        )

    content_type = manifest.get(
        "mediaType", "application/vnd.docker.distribution.manifest.v2+json"
    )

    # Remove internal fields before re-pushing
    clean_manifest = {k: v for k, v in manifest.items() if not k.startswith("_")}

    success = await registry.put_manifest(
        repository, request.new_tag, clean_manifest, content_type
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create new tag",
        )

    return {
        "message": f"Tag '{request.new_tag}' created from '{request.source_tag}' in '{repository}'"
    }


@router.get("/ping")
async def ping_registry(
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_pull_access),
):
    """Check registry connectivity."""
    is_up = await registry.ping()
    return {"status": "ok" if is_up else "unreachable", "url": registry.base_url}


@router.post("/images/{repository:path}/rename")
async def rename_image(
    repository: str,
    request: RenameImageRequest,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_push_access),
):
    """
    Retag an image to a new repository/name using skopeo copy.
    skopeo copies the manifest directly between two registry locations
    without pulling the full image layers to disk.
    """
    from urllib.parse import urlparse

    registry_host = urlparse(REGISTRY_URL).netloc
    source = f"docker://{registry_host}/{repository}:{request.new_tag}"
    dest = f"docker://{registry_host}/{request.new_repository}:{request.new_tag}"

    # Disable TLS verification for plain HTTP registries (e.g. localhost:5000)
    tls_flags = (
        ["--src-tls-verify=false", "--dest-tls-verify=false"]
        if REGISTRY_URL.startswith("http://")
        else []
    )

    proc = await asyncio.create_subprocess_exec(
        "skopeo",
        "copy",
        *tls_flags,
        source,
        dest,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"skopeo copy failed: {stderr.decode()}",
        )

    return {
        "message": (
            f"Image '{repository}' retagged to "
            f"'{request.new_repository}:{request.new_tag}'"
        )
    }


# ─── Garbage Collection ───────────────────────────────────────────────────────


class GCStatus(BaseModel):
    """Garbage collection job status."""

    status: str
    started_at: str | None
    finished_at: str | None
    output: str
    freed_bytes: int
    freed_human: str
    error: str | None


# In-memory GC job state
_gc_state = GCStatus(
    status="idle",
    started_at=None,
    finished_at=None,
    output="",
    freed_bytes=0,
    freed_human="0 B",
    error=None,
).model_dump()


def _bytes_to_human(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


async def _run_gc(settings: Settings):
    """
    Run registry garbage-collect directly inside the container.
    Uses supervisord XML-RPC to stop/start the registry process,
    then runs the registry binary directly (no docker exec needed).
    """
    import xmlrpc.client

    global _gc_state
    _gc_state = GCStatus(
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        output="Garbage collection started...",
        freed_bytes=0,
        freed_human="0 B",
        error=None,
    ).model_dump()

    output_lines: list[str] = []

    try:
        # Measure disk usage before GC
        try:
            size_before = shutil.disk_usage(REGISTRY_DATA_DIR).used
        except Exception:
            size_before = 0

        # Stop registry via supervisord RPC
        proxy = xmlrpc.client.ServerProxy(SUPERVISORD_RPC_URL)
        output_lines.append("Stopping registry process via supervisord...")
        try:
            proxy.supervisor.stopProcess("registry")
            await asyncio.sleep(2)
            output_lines.append("Registry stopped.")
        except Exception as e:
            output_lines.append(f"Warning: could not stop registry cleanly: {e}")

        try:
            # Run garbage-collect directly with the local binary
            cmd = [REGISTRY_BINARY, "garbage-collect", REGISTRY_CONFIG]
            output_lines.append(f"Running: {' '.join(cmd)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            gc_out, gc_err = await proc.communicate()
            output_lines.append(gc_out.decode())
            if gc_err.decode().strip():
                output_lines.append(gc_err.decode())

            if proc.returncode != 0:
                raise Exception(f"garbage-collect exited with code {proc.returncode}")

            output_lines.append("Garbage collection completed.")

        finally:
            # Always restart registry
            try:
                proxy.supervisor.startProcess("registry")
                output_lines.append("Registry restarted.")
            except Exception as e:
                output_lines.append(f"Warning: could not restart registry: {e}")

        # Measure after
        try:
            size_after = shutil.disk_usage(REGISTRY_DATA_DIR).used
            freed = max(0, size_before - size_after)
        except Exception:
            freed = 0

        _gc_state["freed_bytes"] = freed
        _gc_state["freed_human"] = _bytes_to_human(freed)
        _gc_state["output"] = "\n".join(output_lines).strip()
        _gc_state["status"] = "done"
        _gc_state["finished_at"] = datetime.now(timezone.utc).isoformat()

        _gc_state = GCStatus.model_validate(_gc_state).model_dump()

    except Exception as e:
        _gc_state["status"] = "failed"
        _gc_state["error"] = str(e)
        _gc_state["output"] = "\n".join(output_lines).strip()
        _gc_state["finished_at"] = datetime.now(timezone.utc).isoformat()

        _gc_state = GCStatus.model_validate(_gc_state).model_dump()

    except Exception as e:
        _gc_state["status"] = "failed"
        _gc_state["error"] = str(e)
        _gc_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _gc_state = GCStatus.model_validate(_gc_state).model_dump()


@router.post("/gc", response_model=GCStatus)
async def start_garbage_collect(
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    Trigger a registry garbage-collect run.
    Removes unreferenced layers and blobs to reclaim disk space.
    Only one GC job can run at a time.
    """
    if _gc_state["status"] == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A garbage-collect is already running",
        )

    background_tasks.add_task(_run_gc, settings)

    return GCStatus(
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        output="Garbage collection started...",
        freed_bytes=0,
        freed_human="0 B",
        error=None,
    )


@router.get("/gc", response_model=GCStatus)
async def get_gc_status(_: UserInfo = Depends(require_admin)):
    """Get the current or last garbage-collect job status."""
    return GCStatus(
        status=_gc_state["status"],
        started_at=_gc_state["started_at"],
        finished_at=_gc_state["finished_at"],
        output=_gc_state["output"],
        freed_bytes=_gc_state["freed_bytes"],
        freed_human=_bytes_to_human(_gc_state["freed_bytes"]),
        error=_gc_state["error"],
    )


# ─── Empty / Ghost Repository Cleanup ────────────────────────────────────────


@router.get("/empty-repositories")
async def list_empty_repositories(
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_admin),
):
    """
    List repositories that have no tags.

    These ghost entries appear in the catalog after all tags of a repository
    have been deleted. The Distribution Registry has no API to remove them;
    they persist until a full GC + filesystem cleanup is performed.
    """
    empty = await registry.list_empty_repositories()
    return {"empty_repositories": empty, "count": len(empty)}


@router.delete("/empty-repositories")
async def purge_empty_repositories(
    registry: RegistryService = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """
    Purge ghost repositories directly from the local filesystem.
    In registry_inside mode, the registry data is at /var/lib/registry
    inside the same container — no docker exec needed.
    """
    empty = await registry.list_empty_repositories()
    if not empty:
        return {"message": "No empty repositories found", "purged": []}

    purged = []
    errors = []

    for repo in empty:
        # Safely resolve path to prevent directory traversal
        repo_path = Path(REGISTRY_REPOS_DIR) / repo
        try:
            # Ensure the resolved path stays within the repositories directory
            resolved = repo_path.resolve()
            base = Path(REGISTRY_REPOS_DIR).resolve()
            if not str(resolved).startswith(str(base)):
                errors.append({"repo": repo, "error": "Path traversal attempt blocked"})
                continue

            if resolved.exists():
                shutil.rmtree(resolved)
                purged.append(repo)
            else:
                # Directory already gone — consider it purged
                purged.append(repo)
        except Exception as e:
            errors.append({"repo": repo, "error": str(e)})

    return {
        "message": f"Purged {len(purged)} empty repositories",
        "purged": purged,
        "errors": errors,
    }
