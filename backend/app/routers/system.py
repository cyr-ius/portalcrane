from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel

from ..services.process_manager import (
    get_all_process_statuses,
)
from ..services.audit_service import get_recent_audit_events
from ..core.jwt import UserInfo, require_admin

router = APIRouter()


class AuditEventsResponse(BaseModel):
    events: list[dict[str, object]]


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
