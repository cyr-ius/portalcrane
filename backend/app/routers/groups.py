"""
Portalcrane - Groups Router
Manages user groups used for role-based access control on registry folders.

A group is a named set of usernames. Folder pull/push permissions are granted to
groups (never to individual users): a user's effective access to a folder is the
union of the permissions of every group they belong to.

Access rules:
- Group management is admin-only.
- Deleting a group cascades: its permission entries are purged from every folder.
- Deleting a user removes them from every group (handled by auth.py).
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from ..config import DATA_DIR
from ..core.jwt import UserInfo, require_admin

router = APIRouter()

_GROUPS_FILE = Path(f"{DATA_DIR}/groups.json")


# ── Models ────────────────────────────────────────────────────────────────────


class Group(BaseModel):
    """A named set of usernames used to grant folder permissions."""

    id: str
    name: str
    description: str = ""
    created_at: str
    members: list[str] = []


class CreateGroupRequest(BaseModel):
    """Payload to create a new group."""

    name: str
    description: str = ""

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        """Ensure group name is non-empty and trimmed."""
        v = v.strip()
        if not v:
            raise ValueError("Group name must not be empty")
        return v


class UpdateGroupRequest(BaseModel):
    """Payload to update a group's name and/or description (all optional)."""

    name: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Group name must not be empty")
        return v


class AddMemberRequest(BaseModel):
    """Payload to add a username to a group."""

    username: str

    @field_validator("username")
    @classmethod
    def username_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username must not be empty")
        return v


# ── Storage helpers ───────────────────────────────────────────────────────────


def _load_groups() -> list[dict]:
    """Load groups from disk. Returns empty list if file is missing."""
    try:
        if _GROUPS_FILE.exists():
            return json.loads(_GROUPS_FILE.read_text())
    except Exception:
        pass
    return []


def _save_groups(groups: list[dict]) -> None:
    """Persist groups list to disk."""
    _GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GROUPS_FILE.write_text(json.dumps(groups, indent=2))


def _dict_to_group(d: dict) -> Group:
    """Convert a raw dict (from JSON) to a Group model."""
    return Group(
        id=d["id"],
        name=d["name"],
        description=d.get("description", ""),
        created_at=d.get("created_at", ""),
        members=list(d.get("members", [])),
    )


# ── Public helpers used by folders and auth routers ───────────────────────────


def get_group_ids_for_user(username: str) -> set[str]:
    """Return the set of group ids the given username belongs to."""
    return {g["id"] for g in _load_groups() if username in g.get("members", [])}


def remove_member_from_all_groups(username: str) -> int:
    """Remove a username from every group's member list. Returns removals."""
    groups = _load_groups()
    removed = 0

    for group in groups:
        members = group.get("members", [])
        if username in members:
            group["members"] = [m for m in members if m != username]
            removed += 1

    if removed:
        _save_groups(groups)

    return removed


def group_name_for_id(group_id: str) -> str | None:
    """Return the display name of a group id, or None when it no longer exists."""
    for g in _load_groups():
        if g["id"] == group_id:
            return g["name"]
    return None


def ensure_group_for_username(username: str) -> str:
    """Return the id of the auto-group backing a single username, creating it if
    needed. Used by the legacy per-user permission migration in folders.py.
    """
    auto_name = f"user-{username}"
    groups = _load_groups()

    for g in groups:
        if g["name"] == auto_name:
            if username not in g.get("members", []):
                g.setdefault("members", []).append(username)
                _save_groups(groups)
            return g["id"]

    entry = {
        "id": str(uuid.uuid4()),
        "name": auto_name,
        "description": f"Auto-created group migrated from user '{username}'",
        "created_at": datetime.now(UTC).isoformat(),
        "members": [username],
    }
    groups.append(entry)
    _save_groups(groups)
    return entry["id"]


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=list[Group])
async def list_groups(_: UserInfo = Depends(require_admin)) -> list[Group]:
    """Return all groups. Requires admin."""
    return [_dict_to_group(g) for g in _load_groups()]


@router.post("", response_model=Group, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: CreateGroupRequest,
    _: UserInfo = Depends(require_admin),
) -> Group:
    """Create a new group. Requires admin."""
    groups = _load_groups()
    if any(g["name"] == payload.name for g in groups):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{payload.name}' already exists",
        )
    entry = {
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "description": payload.description,
        "created_at": datetime.now(UTC).isoformat(),
        "members": [],
    }
    groups.append(entry)
    _save_groups(groups)
    return _dict_to_group(entry)


@router.patch("/{group_id}", response_model=Group)
async def update_group(
    group_id: str,
    payload: UpdateGroupRequest,
    _: UserInfo = Depends(require_admin),
) -> Group:
    """Update a group's name and/or description. Requires admin."""
    groups = _load_groups()
    target = next((g for g in groups if g["id"] == group_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    if payload.name is not None and payload.name != target["name"]:
        if any(g["name"] == payload.name and g["id"] != group_id for g in groups):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group '{payload.name}' already exists",
            )
        target["name"] = payload.name
    if payload.description is not None:
        target["description"] = payload.description

    _save_groups(groups)
    return _dict_to_group(target)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    _: UserInfo = Depends(require_admin),
) -> None:
    """Delete a group and purge its folder permissions. Requires admin."""
    groups = _load_groups()
    if not any(g["id"] == group_id for g in groups):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )

    # Cascade: remove this group's permission entries from every folder.
    # Local import avoids a circular import at module load time.
    from .folders import remove_permissions_for_group

    remove_permissions_for_group(group_id)

    _save_groups([g for g in groups if g["id"] != group_id])


@router.put("/{group_id}/members", response_model=Group)
async def add_member(
    group_id: str,
    payload: AddMemberRequest,
    _: UserInfo = Depends(require_admin),
) -> Group:
    """Add a username to a group (idempotent). Requires admin."""
    groups = _load_groups()
    for group in groups:
        if group["id"] == group_id:
            members: list[str] = group.setdefault("members", [])
            if payload.username not in members:
                members.append(payload.username)
                _save_groups(groups)
            return _dict_to_group(group)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")


@router.delete(
    "/{group_id}/members/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    group_id: str,
    username: str,
    _: UserInfo = Depends(require_admin),
) -> None:
    """Remove a username from a group. Requires admin."""
    groups = _load_groups()
    for group in groups:
        if group["id"] == group_id:
            members = group.get("members", [])
            if username not in members:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Member not found in group",
                )
            group["members"] = [m for m in members if m != username]
            _save_groups(groups)
            return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
