"""
Portalcrane - Dashboard Router
Registry statistics and overview data
"""

import shutil

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..services.registry_service import RegistryService
from .auth import UserInfo, get_current_user

router = APIRouter()


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


def bytes_to_human(size: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def get_registry(settings: Settings = Depends(get_settings)) -> RegistryService:
    return RegistryService(settings)


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    registry: RegistryService = Depends(get_registry),
    settings: Settings = Depends(get_settings),
    _: UserInfo = Depends(get_current_user),
):
    """Get all dashboard statistics."""
    # Registry stats
    registry_status = "ok" if await registry.ping() else "unreachable"
    stats = await registry.get_registry_stats()

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
    )
