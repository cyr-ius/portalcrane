"""
Portalcrane - Dashboard Router
================================
Registry statistics and overview data.

Migration note: RegistryService has been removed. Dashboard statistics now use
V2Provider directly via a thin local helper, consistent with the rest of the
codebase which routes all registry operations through the unified provider layer.
"""

import asyncio
import shutil

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.jwt import UserInfo, get_current_user
from ..helpers import bytes_to_human
from ..routers.auth import _load_users
from ..services.providers import local_provider
from ..services.providers.external_v2 import V2Provider

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────────


class DashboardStats(BaseModel):
    """Dashboard statistics model."""

    total_images: int
    total_tags: int
    total_size_bytes: int
    total_size_human: str
    largest_image: dict
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    disk_usage_percent: float
    registry_status: str
    total_users: int
    total_admins: int


# ── Registry stats helpers ────────────────────────────────────────────────────


async def _get_repo_stats(provider: V2Provider, repo: str) -> dict:
    """Fetch all tag sizes for a single repository in parallel."""
    tags = await provider.browse_tags(repo)
    if not tags:
        return {
            "repo": repo,
            "tags": [],
            "total_size": 0,
            "largest": {"name": "", "size": 0},
        }

    sizes: list[int] = await asyncio.gather(
        *[provider.get_image_size(repo, tag) for tag in tags],
        return_exceptions=False,
    )

    total_size = sum(sizes)
    largest_size = 0
    largest_name = ""
    for tag, size in zip(tags, sizes):
        if size > largest_size:
            largest_size = size
            largest_name = f"{repo}:{tag}"

    return {
        "repo": repo,
        "tags": tags,
        "total_size": total_size,
        "largest": {"name": largest_name, "size": largest_size},
    }


async def _get_registry_stats(provider: V2Provider) -> dict:
    """Compute registry-wide statistics using the V2 provider directly."""
    repositories = await provider.list_repositories()

    if not repositories:
        return {
            "total_images": 0,
            "total_tags": 0,
            "total_size_bytes": 0,
            "largest_image": {"name": "", "size": 0},
        }

    repo_results: list[dict] = await asyncio.gather(
        *[_get_repo_stats(provider, repo) for repo in repositories],
        return_exceptions=False,
    )

    total_size = 0
    total_tags = 0
    largest_image: dict = {"name": "", "size": 0}

    for result in repo_results:
        total_size += result["total_size"]
        total_tags += len(result["tags"])
        if result["largest"]["size"] > largest_image["size"]:
            largest_image = result["largest"]

    return {
        "total_images": len(repositories),
        "total_tags": total_tags,
        "total_size_bytes": total_size,
        "largest_image": largest_image,
    }


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    _: UserInfo = Depends(get_current_user),
):
    """Return all dashboard statistics.

    Uses V2Provider directly — no dependency on the removed RegistryService.
    """
    provider = local_provider()

    # Registry connectivity and stats
    registry_status = "ok" if await provider.ping() else "unreachable"
    stats = await _get_registry_stats(provider)

    # Disk usage
    try:
        disk = shutil.disk_usage("/")
        disk_total = disk.total
        disk_used = disk.used
        disk_free = disk.free
        disk_percent = (disk_used / disk_total) * 100 if disk_total > 0 else 0
    except Exception:
        disk_total = disk_used = disk_free = 0
        disk_percent = 0.0

    # User counts — env-admin always counts as 1 admin + 1 user
    local_users = _load_users()
    total_users = 1 + len(local_users)
    total_admins = 1 + sum(1 for u in local_users if u.get("is_admin", False))

    total_size = stats["total_size_bytes"]
    largest = stats["largest_image"]

    return DashboardStats(
        total_images=stats["total_images"],
        total_tags=stats["total_tags"],
        total_size_bytes=total_size,
        total_size_human=bytes_to_human(total_size),
        largest_image={
            "name": largest["name"],
            "size": largest["size"],
            "size_human": bytes_to_human(largest["size"]),
        },
        disk_total_bytes=disk_total,
        disk_used_bytes=disk_used,
        disk_free_bytes=disk_free,
        disk_usage_percent=round(disk_percent, 1),
        registry_status=registry_status,
        total_users=total_users,
        total_admins=total_admins,
    )
