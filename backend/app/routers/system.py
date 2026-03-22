"""
Portalcrane - System Router
============================
System-level operations: process status, audit logs, garbage collection,
orphan OCI cleanup, ghost repository management, registry ping, and image copy.

All endpoints previously scattered across registry.py that relate to
infrastructure concerns (not image browsing) have been consolidated here.

Endpoints:
  GET    /api/system/processes               — supervised process statuses (admin)
  GET    /api/system/audit/logs              — recent audit events (admin)
  GET    /api/system/gc                      — GC job status (admin)
  POST   /api/system/gc                      — trigger GC run (admin)
  GET    /api/system/orphan-oci              — list orphan OCI dirs (admin)
  DELETE /api/system/orphan-oci              — purge orphan OCI dirs (admin)
"""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..config import DATA_DIR, STAGING_DIR
from ..core.jwt import UserInfo, require_admin
from ..helpers import bytes_to_human
from ..services.audit_service import get_recent_audit_events
from ..services.job_service import jobs_list
from ..services.process_manager import get_all_process_statuses

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

REGISTRY_BINARY = "/usr/local/bin/registry"
REGISTRY_CONFIG = "/etc/registry/config.yml"
REGISTRY_DATA_DIR = f"{DATA_DIR}/registry"
SUPERVISORD_RPC_URL = "http://127.0.0.1:9001/RPC2"


def _staging_root() -> Path:
    """Return the resolved absolute path to the staging root directory."""
    return Path(STAGING_DIR).resolve()


# ── Pydantic models ───────────────────────────────────────────────────────────


class AuditEventsResponse(BaseModel):
    """Response model for audit log events."""

    events: list[dict[str, object]]


class OrphanOCIResult(BaseModel):
    """Result of orphan OCI layout directories inspection in the staging directory."""

    dirs: list[str]
    count: int
    total_size_bytes: int
    total_size_human: str


class GCStatus(BaseModel):
    """Garbage collection job status."""

    status: str
    started_at: str | None
    finished_at: str | None
    output: str
    freed_bytes: int
    freed_human: str
    error: str | None


# ── In-memory GC state ────────────────────────────────────────────────────────

_gc_state: dict = GCStatus(
    status="idle",
    started_at=None,
    finished_at=None,
    output="",
    freed_bytes=0,
    freed_human="0 B",
    error=None,
).model_dump()


# ── Process status ─────────────────────────────────────────────────────────────


@router.get("/processes")
async def list_processes(_: UserInfo = Depends(require_admin)):
    """Return runtime status of all supervised processes."""
    return await get_all_process_statuses()


# ── Audit logs ─────────────────────────────────────────────────────────────────


@router.get("/audit/logs", response_model=AuditEventsResponse)
async def get_audit_logs(
    limit: int = Query(default=200, ge=1, le=500, description="Max number of events"),
    _: UserInfo = Depends(require_admin),
):
    """Return the most recent in-memory audit log events (newest first)."""
    return {"events": get_recent_audit_events(limit=limit)}


# ── Orphan OCI ─────────────────────────────────────────────────────────────────


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
        total_size_human=bytes_to_human(total_bytes),
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


# ── Garbage collection ─────────────────────────────────────────────────────────


async def _run_gc(dry_run: bool) -> None:
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
        _gc_state["freed_human"] = bytes_to_human(freed)
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
    _: UserInfo = Depends(require_admin),
):
    """Trigger a registry garbage-collect run (one job at a time)."""
    if _gc_state["status"] == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A garbage-collect is already running",
        )
    background_tasks.add_task(_run_gc, dry_run)
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
        freed_human=bytes_to_human(int(_gc_state["freed_bytes"])),
        error=_gc_state["error"],
    )
