from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services.process_manager import (
    get_all_process_statuses,
    run_registry_garbage_collect,
)
from ..services.trivy_service import get_trivy_db_info, scan_image, update_trivy_db

router = APIRouter(prefix="/api/system", tags=["system"])


class GCResult(BaseModel):
    success: bool
    output: str
    dry_run: bool
    return_code: int | None = None


@router.get("/processes")
async def list_processes():
    """Returns runtime status of all supervised processes."""
    return await get_all_process_statuses()


@router.get("/trivy/db")
async def trivy_db_status():
    """Returns Trivy vulnerability database info and freshness status."""
    return await get_trivy_db_info()


@router.post("/trivy/db/update")
async def force_trivy_update():
    """Forces an immediate Trivy DB update."""
    result = await update_trivy_db()
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["output"])
    return result


@router.get("/trivy/scan")
async def scan(
    image: str = Query(
        ..., description="Full image ref, e.g. localhost:5000/myapp:1.0"
    ),
    severity: list[str] = Query(default=["HIGH", "CRITICAL"]),
    ignore_unfixed: bool = Query(default=False),
):
    """
    Scans a specific image from the local registry with Trivy.
    Returns grouped vulnerabilities with CVSS scores.
    """
    result = await scan_image(image, severity=severity, ignore_unfixed=ignore_unfixed)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@router.post("/gc", response_model=GCResult)
async def garbage_collect(dry_run: bool = False):
    """
    Triggers registry garbage collection.
    Stops the registry, runs GC, restarts it.
    Use dry_run=true to preview what would be deleted.
    """
    result = await run_registry_garbage_collect(dry_run=dry_run)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["output"])
    return result
