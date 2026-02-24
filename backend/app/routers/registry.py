"""
Portalcrane - Registry Router
All endpoints for browsing and managing Docker Registry images and tags
"""

import asyncio
import re
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..services.registry_service import RegistryService
from .auth import UserInfo, get_current_user

router = APIRouter()

# In-memory GC job state
_gc_state: dict = {
    "status": "idle",  # idle | running | done | failed
    "started_at": None,
    "finished_at": None,
    "output": "",
    "freed_bytes": 0,
    "error": None,
}


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
    _: UserInfo = Depends(get_current_user),
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
    _: UserInfo = Depends(get_current_user),
):
    """Get all tags for a specific image repository."""
    tags = await registry.list_tags(repository)
    return {"repository": repository, "tags": tags}


@router.get("/images/{repository:path}/tags/{tag}/detail", response_model=ImageDetail)
async def get_tag_detail(
    repository: str,
    tag: str,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(get_current_user),
):
    """Get detailed information about a specific image tag (advanced mode)."""
    manifest = await registry.get_manifest(repository, tag)
    if not manifest:
        raise HTTPException(status_code=404, detail="Tag not found")

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
    _: UserInfo = Depends(get_current_user),
):
    """Delete a specific tag from an image repository."""
    success = await registry.delete_tag(repository, tag)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete tag")
    return {"message": f"Tag '{tag}' deleted from '{repository}'"}


@router.delete("/images/{repository:path}")
async def delete_image(
    repository: str,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(get_current_user),
):
    """Delete all tags (and the image) from a repository."""
    tags = await registry.list_tags(repository)
    if not tags:
        raise HTTPException(
            status_code=404, detail="Repository not found or has no tags"
        )

    errors = []
    for tag in tags:
        success = await registry.delete_tag(repository, tag)
        if not success:
            errors.append(tag)

    if errors:
        raise HTTPException(status_code=500, detail=f"Failed to delete tags: {errors}")

    return {"message": f"Image '{repository}' and all its tags deleted"}


@router.post("/images/{repository:path}/tags")
async def add_tag(
    repository: str,
    request: AddTagRequest,
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(get_current_user),
):
    """Add a new tag to an existing image (retag)."""
    # Get the source manifest
    manifest = await registry.get_manifest(repository, request.source_tag)
    if not manifest:
        raise HTTPException(
            status_code=404, detail=f"Source tag '{request.source_tag}' not found"
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
        raise HTTPException(status_code=500, detail="Failed to create new tag")

    return {
        "message": f"Tag '{request.new_tag}' created from '{request.source_tag}' in '{repository}'"
    }


@router.get("/ping")
async def ping_registry(
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(get_current_user),
):
    """Check registry connectivity."""
    is_up = await registry.ping()
    return {"status": "ok" if is_up else "unreachable", "url": registry.base_url}


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


def _bytes_to_human(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


async def _run_gc(settings: Settings):
    """
    Background task: run registry garbage-collect.

    Strategy:
      1. Try to exec `registry garbage-collect` inside the registry container
         via Docker CLI (most reliable when Docker socket is mounted).
      2. Fallback: call the registry API storage delete endpoint if available.
      3. Measure disk usage before/after to compute freed space.
    """
    global _gc_state
    _gc_state["status"] = "running"
    _gc_state["started_at"] = datetime.now(timezone.utc).isoformat()
    _gc_state["output"] = ""
    _gc_state["error"] = None
    _gc_state["freed_bytes"] = 0

    registry_dir = "/var/lib/registry"

    try:
        # Measure disk usage before GC
        try:
            before = shutil.disk_usage(registry_dir)
            size_before = before.used
        except Exception:
            size_before = 0

        output_lines: list[str] = []

        # ── Strategy 1: docker exec into the registry container ──────────────
        container_name = await _find_registry_container_name()

        if container_name:
            output_lines.append(f"Found registry container: {container_name}")
            gc_proc, gc_out, gc_err = await _run_registry_gc(container_name)
            output_lines.append(gc_out)
            if gc_err:
                output_lines.append(gc_err)

            if gc_proc.returncode != 0:
                cleaned_ghosts = await _cleanup_ghosts_from_gc_error(
                    container_name, gc_out, gc_err
                )
                if cleaned_ghosts:
                    output_lines.append(
                        "Detected ghost repository filesystem issues. "
                        f"Cleaned {len(cleaned_ghosts)} ghost path(s): "
                        + ", ".join(cleaned_ghosts)
                    )
                    output_lines.append(
                        "Retrying garbage-collect after ghost cleanup..."
                    )
                    retry_proc, retry_out, retry_err = await _run_registry_gc(
                        container_name
                    )
                    output_lines.append(retry_out)
                    if retry_err:
                        output_lines.append(retry_err)
                    if retry_proc.returncode != 0:
                        raise Exception(
                            f"garbage-collect exited with code {retry_proc.returncode}"
                        )
                else:
                    raise Exception(
                        f"garbage-collect exited with code {gc_proc.returncode}"
                    )

        else:
            # ── Strategy 2: run a temporary registry container for GC ─────────
            # This works when the registry data volume is accessible
            output_lines.append("No running registry container found.")
            output_lines.append("Attempting GC via temporary container...")

            gc_proc = await asyncio.create_subprocess_exec(
                "docker",
                "run",
                "--rm",
                "-v",
                f"{registry_dir}:/var/lib/registry",
                "registry:3",
                "garbage-collect",
                "--delete-untagged=true",
                "/etc/distribution/config.yml",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            gc_out, gc_err = await gc_proc.communicate()
            output_lines.append(gc_out.decode())
            if gc_err:
                output_lines.append(gc_err.decode())

            if gc_proc.returncode != 0:
                raise Exception(
                    f"GC temporary container failed (code {gc_proc.returncode}). "
                    "Ensure Docker socket is mounted and registry data volume is accessible."
                )

        # Measure disk usage after GC
        try:
            after = shutil.disk_usage(registry_dir)
            size_after = after.used
            freed = max(0, size_before - size_after)
        except Exception:
            freed = 0

        _gc_state["freed_bytes"] = freed
        _gc_state["output"] = "\n".join(output_lines).strip()
        _gc_state["status"] = "done"
        _gc_state["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        _gc_state["status"] = "failed"
        _gc_state["error"] = str(e)
        _gc_state["output"] = "\n".join(output_lines) if "output_lines" in dir() else ""
        _gc_state["finished_at"] = datetime.now(timezone.utc).isoformat()


@router.post("/gc", response_model=GCStatus)
async def start_garbage_collect(
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """
    Trigger a registry garbage-collect run.
    Removes unreferenced layers and blobs to reclaim disk space.
    Only one GC job can run at a time.
    """
    if _gc_state["status"] == "running":
        raise HTTPException(
            status_code=409, detail="A garbage-collect is already running"
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
async def get_gc_status(_: UserInfo = Depends(get_current_user)):
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
    _: UserInfo = Depends(get_current_user),
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
    _: UserInfo = Depends(get_current_user),
):
    """
    Purge all ghost repositories (repositories with no tags) from the registry
    filesystem.

    The Distribution Registry stores repository metadata in the filesystem under
    /var/lib/registry/docker/registry/v2/repositories/<name>/. Since the HTTP API
    offers no endpoint to delete a repository entry, we remove the directory
    directly via docker exec into the registry container.
    """
    empty = await registry.list_empty_repositories()
    if not empty:
        return {"message": "No empty repositories found", "purged": []}

    # Find the registry container — try by name first, then by image
    container_name = await _find_registry_container_name()

    if not container_name:
        raise HTTPException(
            status_code=503,
            detail="Registry container not found. Cannot purge directories without docker exec access.",
        )

    purged = []
    errors = []

    for repo in empty:
        repo_path = f"/var/lib/registry/docker/registry/v2/repositories/{repo}"
        rm_proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_name,
            "rm",
            "-rf",
            repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await rm_proc.communicate()
        if rm_proc.returncode == 0:
            purged.append(repo)
        else:
            errors.append({"repo": repo, "error": stderr.decode().strip()})

    return {
        "message": f"Purged {len(purged)} empty repositories",
        "purged": purged,
        "errors": errors,
    }


async def _find_registry_container_name() -> str:
    """Return the running registry container name if available."""
    container_name = ""
    for docker_filter in [
        ("--filter", "name=portalcrane-registry"),
        ("--filter", "ancestor=registry:3"),
    ]:
        find_proc = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            *docker_filter,
            "--filter",
            "status=running",
            "--format",
            "{{.Names}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await find_proc.communicate()
        container_name = stdout.decode().strip().split("\n")[0]
        if container_name:
            break
    return container_name


async def _run_registry_gc(container_name: str):
    """Run `registry garbage-collect` inside the given container."""
    gc_proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container_name,
        "registry",
        "garbage-collect",
        "--delete-untagged=true",
        "/etc/distribution/config.yml",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    gc_out, gc_err = await gc_proc.communicate()
    return gc_proc, gc_out.decode(), gc_err.decode()


async def _cleanup_ghosts_from_gc_error(
    container_name: str, gc_stdout: str, gc_stderr: str
) -> list[str]:
    """Cleanup ghost repository directories when GC fails on missing _layers paths."""
    ghost_paths = set(
        re.findall(
            r"Path not found: (/docker/registry/v2/repositories/[^\s]+/_layers)",
            f"{gc_stdout}\n{gc_stderr}",
        )
    )
    if not ghost_paths:
        return []

    cleaned: list[str] = []
    for ghost_path in sorted(ghost_paths):
        repo_path = ghost_path.rsplit("/_layers", 1)[0]
        rm_proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            container_name,
            "rm",
            "-rf",
            f"/var/lib/registry{repo_path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await rm_proc.communicate()
        if rm_proc.returncode == 0:
            cleaned.append(repo_path.replace("/docker/registry/v2/repositories/", ""))
    return cleaned
