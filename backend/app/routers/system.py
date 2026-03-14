import shutil

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from pathlib import Path

from ..services.process_manager import (
    get_all_process_statuses,
)
from ..services.audit_service import get_recent_audit_events
from ..core.jwt import UserInfo, require_admin
from ..config import STAGING_DIR
from ..services.job_service import jobs_list

router = APIRouter()


class AuditEventsResponse(BaseModel):
    events: list[dict[str, object]]


class OrphanOCIResult(BaseModel):
    """Result of orphan OCI layout directories inspection in the staging directory."""

    dirs: list[str]
    count: int
    total_size_bytes: int
    total_size_human: str


def _staging_root() -> Path:
    """Return the resolved absolute path to the staging root directory."""
    return Path(STAGING_DIR).resolve()


def _human_size(size_bytes: float) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


@router.get("/processes")
async def list_processes(_: UserInfo = Depends(require_admin)):
    """Returns runtime status of all supervised processes."""
    return await get_all_process_statuses()


@router.get("/audit/logs", response_model=AuditEventsResponse)
async def get_audit_logs(
    limit: int = Query(default=200, ge=1, le=500, description="Max number of events"),
    _: UserInfo = Depends(require_admin),
):
    """Returns the most recent in-memory audit log events (newest first)."""
    return {"events": get_recent_audit_events(limit=limit)}


@router.get("/orphan-oci", response_model=OrphanOCIResult)
async def get_orphan_oci(_: UserInfo = Depends(require_admin)):
    """List OCI layout directories in the staging area with no matching job."""
    root = _staging_root()
    orphans = []
    total_bytes = 0
    for entry in root.iterdir():
        if entry.is_dir() and entry.name not in jobs_list:
            size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            orphans.append(entry.name)
            total_bytes += size
    return OrphanOCIResult(
        dirs=orphans,
        count=len(orphans),
        total_size_bytes=total_bytes,
        total_size_human=_human_size(total_bytes),
    )


@router.delete("/orphan-oci")
async def purge_orphan_oci(_: UserInfo = Depends(require_admin)):
    """Delete all orphan OCI layout directories."""
    root = _staging_root()
    purged = []
    for entry in root.iterdir():
        if entry.is_dir() and entry.name not in jobs_list:
            shutil.rmtree(entry, ignore_errors=True)
            purged.append(entry.name)
    return {"message": f"Purged {len(purged)} orphan directories", "purged": purged}
