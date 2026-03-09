"""
Portalcrane - Registry Router
All endpoints for browsing and managing Docker Registry images and tags.

Repository names containing slashes (e.g. "biocontainers/swarm") are passed
as query parameters (?repository=...) instead of path segments to avoid
%2F encoding issues with reverse proxies (Traefik, HAProxy, Nginx, Caddy).
"""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    status,
)
from pydantic import BaseModel

from ..config import DATA_DIR, REGISTRY_URL, REGISTRY_HOST, Settings, get_settings
from ..core.jwt import (
    UserInfo,
    require_admin,
    require_pull_access,
    require_push_access,
    get_current_user,
)
from ..services.registry_service import RegistryService
from .folders import check_folder_access

router = APIRouter()
logger = logging.getLogger(__name__)

REGISTRY_BINARY = "/usr/local/bin/registry"
REGISTRY_CONFIG = "/etc/registry/config.yml"
REGISTRY_DATA_DIR = f"{DATA_DIR}/registry"
REGISTRY_REPOS_DIR = f"{REGISTRY_DATA_DIR}/docker/registry/v2/repositories"
SUPERVISORD_RPC_URL = "http://127.0.0.1:9001/RPC2"


# ─── Models ───────────────────────────────────────────────────────────────────


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


class GCStatus(BaseModel):
    """Garbage collection job status."""

    status: str
    started_at: str | None
    finished_at: str | None
    output: str
    freed_bytes: int
    freed_human: str
    error: str | None


class CopyImageRequest(BaseModel):
    """Copy an image to a new repository path (with optional tag rename)."""

    source_repository: str
    source_tag: str
    dest_repository: str  # Full path e.g. "infra/nginx" or "prod/nginx"
    dest_tag: str | None = None  # Defaults to source_tag


# ─── Dependency ───────────────────────────────────────────────────────────────


def get_registry(settings: Settings = Depends(get_settings)) -> RegistryService:
    """Dependency: return an authenticated RegistryService instance."""
    return RegistryService(settings)


# ─── In-memory GC state ───────────────────────────────────────────────────────

_gc_state: dict = GCStatus(
    status="idle",
    started_at=None,
    finished_at=None,
    output="",
    freed_bytes=0,
    freed_human="0 B",
    error=None,
).model_dump()


def _bytes_to_human(size: int) -> str:
    """Convert a byte count to a human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size //= 1024
    return f"{size:.2f} PB"


def _ensure_folder_permission(
    *, current_user: UserInfo, image_path: str, is_pull: bool
) -> None:
    """Enforce folder pull/push permission on a repository path for non-admins."""
    if current_user.is_admin:
        return

    has_access = check_folder_access(
        current_user.username,
        image_path,
        is_pull=is_pull,
    )
    if has_access:
        return

    action = "pull" if is_pull else "push"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"No {action} access on folder for '{image_path}'",
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/images", response_model=PaginatedImages)
async def list_images(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=500),
    search: str | None = Query(None),
    registry: RegistryService = Depends(get_registry),
    _user: UserInfo = Depends(require_pull_access),
):
    """List all images with pagination and optional search filter."""
    repositories = await registry.list_repositories()

    if search:
        repositories = [r for r in repositories if search.lower() in r.lower()]

    total = len(repositories)
    total_pages: int = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_repos = repositories[start : start + page_size]

    tags_results: list[list[str]] = await asyncio.gather(
        *[registry.list_tags(r) for r in page_repos]
    )
    items: list[ImageInfo] = [
        ImageInfo(name=repo, tags=tags, tag_count=len(tags), total_size=0)
        for repo, tags in zip(page_repos, tags_results)
    ]

    return PaginatedImages(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/images/tags")
async def get_image_tags(
    repository: str = Query(
        ..., description="Repository name, e.g. biocontainers/swarm"
    ),
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_pull_access),
):
    """Get all tags for a specific image repository."""
    tags = await registry.list_tags(repository)
    return {"repository": repository, "tags": tags}


@router.get("/images/tags/detail", response_model=ImageDetail)
async def get_tag_detail(
    repository: str = Query(
        ..., description="Repository name, e.g. biocontainers/swarm"
    ),
    tag: str = Query(..., description="Tag name, e.g. latest"),
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_pull_access),
):
    """Get detailed information about a specific image tag."""
    manifest = await registry.get_manifest(repository, tag)
    if not manifest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    config_digest: str = manifest.get("config", {}).get("digest", "")
    config: dict = {}
    if config_digest:
        config = await registry.get_image_config(repository, config_digest)

    container_config: dict = config.get("config", config.get("container_config", {}))
    layers: list[dict] = manifest.get("layers", [])
    total_size: int = sum(int(layer.get("size", 0)) for layer in layers)

    return ImageDetail(
        name=repository,
        tag=tag,
        digest=str(manifest.get("_digest", "")),
        size=total_size,
        created=str(config.get("created", "")),
        architecture=str(config.get("architecture", "")),
        os=str(config.get("os", "")),
        layers=layers,
        labels=container_config.get("Labels", {}) or {},
        env=container_config.get("Env", []) or [],
        cmd=container_config.get("Cmd", []) or [],
        entrypoint=container_config.get("Entrypoint", []) or [],
        exposed_ports=container_config.get("ExposedPorts", {}) or {},
    )


@router.delete("/images/tags")
async def delete_tag(
    repository: str = Query(..., description="Repository name"),
    tag: str = Query(..., description="Tag name to delete"),
    registry: RegistryService = Depends(get_registry),
    current_user: UserInfo = Depends(require_push_access),
):
    """Delete a specific tag from an image repository."""
    _ensure_folder_permission(
        current_user=current_user,
        image_path=repository,
        is_pull=False,
    )
    success = await registry.delete_tag(repository, tag)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete tag",
        )
    return {"message": f"Tag '{tag}' deleted from '{repository}'"}


@router.delete("/images")
async def delete_image(
    repository: str = Query(..., description="Repository name to delete entirely"),
    registry: RegistryService = Depends(get_registry),
    current_user: UserInfo = Depends(require_push_access),
):
    """Delete all tags (and the image) from a repository."""
    _ensure_folder_permission(
        current_user=current_user,
        image_path=repository,
        is_pull=False,
    )
    tags = await registry.list_tags(repository)
    if not tags:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found or has no tags",
        )

    errors: list[str] = []
    for tag in tags:
        if not await registry.delete_tag(repository, tag):
            errors.append(tag)

    if errors:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete tags: {errors}",
        )
    return {"message": f"Image '{repository}' and all its tags deleted"}


@router.post("/images/tags")
async def add_tag(
    repository: str = Query(..., description="Repository name"),
    request: AddTagRequest = Body(...),
    registry: RegistryService = Depends(get_registry),
    current_user: UserInfo = Depends(require_push_access),
):
    """Add a new tag to an existing image (retag via manifest copy)."""
    _ensure_folder_permission(
        current_user=current_user,
        image_path=repository,
        is_pull=False,
    )
    manifest = await registry.get_manifest(repository, request.source_tag)
    if not manifest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source tag '{request.source_tag}' not found",
        )

    content_type: str = manifest.get(
        "mediaType", "application/vnd.docker.distribution.manifest.v2+json"
    )
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
        "message": (
            f"Tag '{request.new_tag}' created from '{request.source_tag}' "
            f"in '{repository}'"
        )
    }


@router.get("/ping")
async def ping_registry(
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_pull_access),
):
    """Check registry connectivity."""
    is_up = await registry.ping()
    return {"status": "ok" if is_up else "unreachable", "url": registry.base_url}


@router.post("/images/rename")
async def rename_image(
    repository: str = Query(..., description="Source repository name"),
    request: RenameImageRequest = Body(...),
    current_user: UserInfo = Depends(require_push_access),
):
    """Retag an image to a new repository/name using skopeo copy."""
    _ensure_folder_permission(
        current_user=current_user,
        image_path=repository,
        is_pull=True,
    )
    _ensure_folder_permission(
        current_user=current_user,
        image_path=request.new_repository,
        is_pull=False,
    )

    source = f"docker://{REGISTRY_HOST}/{repository}:{request.new_tag}"
    dest = f"docker://{REGISTRY_HOST}/{request.new_repository}:{request.new_tag}"

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
    _, stderr = await proc.communicate()  # pyright: ignore[reportAssignmentType]

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


async def _run_gc(dry_run: bool, settings: Settings) -> None:
    """Run registry garbage-collect inside the container via supervisord."""
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
        try:
            size_before: int = shutil.disk_usage(REGISTRY_DATA_DIR).used
        except Exception:
            size_before = 0

        proxy = xmlrpc.client.ServerProxy(SUPERVISORD_RPC_URL)
        output_lines.append("Stopping registry process via supervisord...")
        try:
            proxy.supervisor.stopProcess("registry")
            await asyncio.sleep(2)
            output_lines.append("Registry stopped.")
        except Exception as exc:
            output_lines.append(f"Warning: could not stop registry cleanly: {exc}")

        try:
            cmd = [REGISTRY_BINARY, "garbage-collect", REGISTRY_CONFIG]
            if dry_run:
                cmd.append("--dry-run")

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
                raise RuntimeError(
                    f"garbage-collect exited with code {proc.returncode}"
                )
            output_lines.append("Garbage collection completed.")

        finally:
            try:
                proxy.supervisor.startProcess("registry")
                output_lines.append("Registry restarted.")
            except Exception as exc:
                output_lines.append(f"Warning: could not restart registry: {exc}")

        try:
            size_after: int = shutil.disk_usage(REGISTRY_DATA_DIR).used
            freed: int = max(0, size_before - size_after)
        except Exception:
            freed = 0

        _gc_state["freed_bytes"] = freed
        _gc_state["freed_human"] = _bytes_to_human(freed)
        _gc_state["output"] = "\n".join(output_lines).strip()
        _gc_state["status"] = "done"
        _gc_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _gc_state = GCStatus.model_validate(_gc_state).model_dump()

    except Exception:
        logger.exception("GC failed")
        _gc_state["status"] = "failed"
        _gc_state["error"] = "Garbage collection failed — check server logs"
        _gc_state["output"] = "\n".join(output_lines).strip()
        _gc_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _gc_state = GCStatus.model_validate(_gc_state).model_dump()


@router.post("/gc", response_model=GCStatus)
async def start_garbage_collect(
    background_tasks: BackgroundTasks,
    dry_run: bool = False,
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """Trigger a registry garbage-collect run (one job at a time)."""
    if _gc_state["status"] == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A garbage-collect is already running",
        )

    background_tasks.add_task(_run_gc, dry_run, settings)
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
        freed_bytes=int(_gc_state["freed_bytes"]),
        freed_human=_bytes_to_human(int(_gc_state["freed_bytes"])),
        error=_gc_state["error"],
    )


# ─── Empty / Ghost Repository Cleanup ────────────────────────────────────────


@router.get("/empty-repositories")
async def list_empty_repositories(
    registry: RegistryService = Depends(get_registry),
    _: UserInfo = Depends(require_admin),
):
    """List repositories that have no tags (ghost entries)."""
    empty = await registry.list_empty_repositories()
    return {"empty_repositories": empty, "count": len(empty)}


@router.delete("/empty-repositories")
async def purge_empty_repositories(
    registry: RegistryService = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(require_admin),
):
    """Purge ghost repositories directly from the local filesystem."""
    empty = await registry.list_empty_repositories()
    if not empty:
        return {"message": "No empty repositories found", "purged": []}

    purged: list[str] = []
    errors: list[dict] = []

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

    return {
        "message": f"Purged {len(purged)} empty repositories",
        "purged": purged,
        "errors": errors,
    }


@router.post("/images/copy")
async def copy_image(
    request: CopyImageRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Copy an image to a new repository path via skopeo.
    Non-admins must have push access on the destination folder.
    """

    _ensure_folder_permission(
        current_user=current_user,
        image_path=request.source_repository,
        is_pull=True,
    )
    _ensure_folder_permission(
        current_user=current_user,
        image_path=request.dest_repository,
        is_pull=False,
    )

    dest_tag = request.dest_tag or request.source_tag
    source = (
        f"docker://{REGISTRY_HOST}/{request.source_repository}:{request.source_tag}"
    )
    dest = f"docker://{REGISTRY_HOST}/{request.dest_repository}:{dest_tag}"

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
            detail=f"Copy failed: {stderr.decode()}",
        )

    return {
        "message": f"Copied {request.source_repository}:{request.source_tag} → {request.dest_repository}:{dest_tag}"
    }
