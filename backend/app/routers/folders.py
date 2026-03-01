"""
Portalcrane - Folders Router
Manages registry folders (path prefixes) with per-user pull/push permissions.

A folder is a named prefix applied to image paths in the registry.
Example: folder "production" → images pushed as production/my-image:tag

Access rules:
- Admin users always have full access to all folders.
- For non-admin users, folder permissions take priority over global permissions.
- A user without an explicit folder entry is denied, even if can_pull_images=True globally.
- Images pushed without a folder prefix are only allowed for admin users.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from .auth import UserInfo, require_admin
from ..config import get_settings

router = APIRouter()
settings = get_settings()

# Persistent storage for folders
_FOLDERS_FILE = Path(f"{settings.data_dir}/folders.json")


# ─── Models ──────────────────────────────────────────────────────────────────


class FolderPermission(BaseModel):
    """Permission entry for a single user on a folder."""

    username: str
    can_pull: bool = False
    can_push: bool = False


class Folder(BaseModel):
    """A registry folder with its associated user permissions."""

    id: str
    name: str
    description: str = ""
    created_at: str
    permissions: list[FolderPermission] = []


class CreateFolderRequest(BaseModel):
    """Payload to create a new folder."""

    name: str
    description: str = ""

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        """Ensure folder name is non-empty, lowercase, no spaces or slashes."""
        v = v.strip().lower()
        if not v:
            raise ValueError("Folder name must not be empty")
        if any(c in v for c in " /\\"):
            raise ValueError("Folder name must not contain spaces or slashes")
        return v


class UpdateFolderRequest(BaseModel):
    """Payload to update a folder's description."""

    description: str = ""


class SetPermissionRequest(BaseModel):
    """Payload to set or update a user's permissions on a folder."""

    username: str
    can_pull: bool = False
    can_push: bool = False

    @field_validator("username")
    @classmethod
    def username_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username must not be empty")
        return v


# ─── Storage helpers ──────────────────────────────────────────────────────────


def _load_folders() -> list[dict]:
    """Load folders from disk. Returns empty list if file is missing."""
    try:
        if _FOLDERS_FILE.exists():
            return json.loads(_FOLDERS_FILE.read_text())
    except Exception:
        pass
    return []


def _save_folders(folders: list[dict]) -> None:
    """Persist folders list to disk."""
    _FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _FOLDERS_FILE.write_text(json.dumps(folders, indent=2))


def _dict_to_folder(d: dict) -> Folder:
    """Convert a raw dict (from JSON) to a Folder model."""
    return Folder(
        id=d["id"],
        name=d["name"],
        description=d.get("description", ""),
        created_at=d.get("created_at", ""),
        permissions=[
            FolderPermission(
                username=p["username"],
                can_pull=p.get("can_pull", False),
                can_push=p.get("can_push", False),
            )
            for p in d.get("permissions", [])
        ],
    )


# ─── Public helper used by registry_proxy ────────────────────────────────────


def get_folder_for_path(image_path: str) -> dict | None:
    """
    Return the folder dict whose name matches the first path segment of image_path.
    Returns None if no folder matches (image has no folder prefix).
    Example: "production/nginx" → looks for folder named "production".
    """
    if not image_path or "/" not in image_path:
        return None
    prefix = image_path.split("/")[0]
    for folder in _load_folders():
        if folder["name"] == prefix:
            return folder
    return None


def check_folder_access(username: str, image_path: str, is_pull: bool) -> bool | None:
    """
    Check whether a user is allowed to pull or push an image path.

    Returns:
        True  — access granted by folder rules
        False — access denied by folder rules
        None  — no folder applies, caller should fall back to global rules
                (only relevant for pull on paths without folder; push without
                 folder is always denied for non-admins by the proxy layer)
    """
    folder = get_folder_for_path(image_path)
    if folder is None:
        # No folder prefix — return None so the proxy can apply its own logic
        return None

    for perm in folder.get("permissions", []):
        if perm["username"] == username:
            return (
                perm.get("can_pull", False) if is_pull else perm.get("can_push", False)
            )

    # User has no explicit entry in this folder → denied
    return False


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("", response_model=list[Folder])
async def list_folders(_: UserInfo = Depends(require_admin)) -> list[Folder]:
    """Return all folders. Requires admin."""
    return [_dict_to_folder(f) for f in _load_folders()]


@router.post("", response_model=Folder, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: CreateFolderRequest,
    _: UserInfo = Depends(require_admin),
) -> Folder:
    """Create a new folder. Requires admin."""
    folders = _load_folders()

    if any(f["name"] == payload.name for f in folders):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Folder '{payload.name}' already exists",
        )

    entry = {
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "description": payload.description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "permissions": [],
    }
    folders.append(entry)
    _save_folders(folders)
    return _dict_to_folder(entry)


@router.patch("/{folder_id}", response_model=Folder)
async def update_folder(
    folder_id: str,
    payload: UpdateFolderRequest,
    _: UserInfo = Depends(require_admin),
) -> Folder:
    """Update a folder's description. Requires admin."""
    folders = _load_folders()
    for f in folders:
        if f["id"] == folder_id:
            f["description"] = payload.description
            _save_folders(folders)
            return _dict_to_folder(f)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
    )


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    _: UserInfo = Depends(require_admin),
) -> None:
    """Delete a folder and all its permissions. Requires admin."""
    folders = _load_folders()
    new_list = [f for f in folders if f["id"] != folder_id]
    if len(new_list) == len(folders):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        )
    _save_folders(new_list)


@router.put("/{folder_id}/permissions", response_model=Folder)
async def set_permission(
    folder_id: str,
    payload: SetPermissionRequest,
    _: UserInfo = Depends(require_admin),
) -> Folder:
    """
    Set or update a user's pull/push permissions on a folder.
    Creates the entry if it does not exist. Requires admin.
    """
    folders = _load_folders()
    for f in folders:
        if f["id"] == folder_id:
            perms: list[dict] = f.setdefault("permissions", [])
            for p in perms:
                if p["username"] == payload.username:
                    p["can_pull"] = payload.can_pull
                    p["can_push"] = payload.can_push
                    _save_folders(folders)
                    return _dict_to_folder(f)
            # New permission entry
            perms.append(
                {
                    "username": payload.username,
                    "can_pull": payload.can_pull,
                    "can_push": payload.can_push,
                }
            )
            _save_folders(folders)
            return _dict_to_folder(f)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
    )


@router.delete(
    "/{folder_id}/permissions/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_permission(
    folder_id: str,
    username: str,
    _: UserInfo = Depends(require_admin),
) -> None:
    """Remove a user's permissions from a folder. Requires admin."""
    folders = _load_folders()
    for f in folders:
        if f["id"] == folder_id:
            before = len(f.get("permissions", []))
            f["permissions"] = [
                p for p in f.get("permissions", []) if p["username"] != username
            ]
            if len(f["permissions"]) == before:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Permission entry not found",
                )
            _save_folders(folders)
            return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
    )
