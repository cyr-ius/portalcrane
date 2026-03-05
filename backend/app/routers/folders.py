"""
Portalcrane - Folders Router
Manages registry folders (path prefixes) with per-user pull/push permissions.

A folder is a named prefix applied to image paths in the registry.
Example: folder "production" → images pushed as production/my-image:tag

Access rules:
- Admin users always have full access to all folders.
- All access decisions for non-admin users go through folder permissions only.
- The special __root__ folder covers:
    * images with no path prefix       (e.g. "nginx")
    * images whose prefix is unknown   (e.g. "editeur/nginx" when no folder
      named "editeur" exists)
    * only the FIRST segment matters   ("production/editeur/image" → "production")
- A user without an explicit entry in the matched folder is always denied.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from ..config import DATA_DIR
from ..core.jwt import get_current_user, UserInfo, require_admin

router = APIRouter()

_FOLDERS_FILE = Path(f"{DATA_DIR}/folders.json")

# Reserved name for the catch-all folder
ROOT_FOLDER_NAME = "__root__"


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


# ─── Migration helper ─────────────────────────────────────────────────────────


def migrate_root_folder(users: list[dict]) -> None:
    """
    One-time migration: create the __root__ folder and populate it from the
    legacy can_pull_images / can_push_images flags stored on each user account.

    Called at application startup via main.py lifespan.
    Safe to call multiple times — does nothing when __root__ already exists.
    """
    folders = _load_folders()

    if any(f["name"] == ROOT_FOLDER_NAME for f in folders):
        return  # Already migrated

    perms: list[dict] = []
    for user in users:
        if user.get("is_admin"):
            continue  # Admins bypass all folder checks, skip them
        can_pull = user.get("can_pull_images", False)
        can_push = user.get("can_push_images", False)
        if can_pull or can_push:
            perms.append(
                {
                    "username": user["username"],
                    "can_pull": can_pull,
                    "can_push": can_push,
                }
            )

    root_entry = {
        "id": str(uuid.uuid4()),
        "name": ROOT_FOLDER_NAME,
        "description": "Default folder — covers images with no known namespace prefix",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "permissions": perms,
    }
    folders.append(root_entry)
    _save_folders(folders)


# ─── Public helpers used by registry_proxy and registry routers ───────────────


def get_folder_for_path(image_path: str) -> dict | None:
    """
    Return the folder dict that governs access to image_path.

    Resolution order:
    1. If image_path contains '/', extract the first segment and look for
       an exact folder name match.
    2. If no explicit folder matches (unknown prefix) or there is no '/',
       fall back to the __root__ folder.
    3. Return None only when __root__ itself is not configured.

    Examples:
        "nginx"                    → no slash          → __root__
        "editeur/nginx"            → "editeur" unknown → __root__
        "production/nginx"         → folder "production"
        "production/editeur/image" → folder "production"
    """
    folders = _load_folders()

    if image_path and "/" in image_path:
        prefix = image_path.split("/")[0]
        for folder in folders:
            if folder["name"] == prefix:
                return folder

    # No explicit folder matched — fall back to __root__
    for folder in folders:
        if folder["name"] == ROOT_FOLDER_NAME:
            return folder

    return None  # __root__ not configured — no rule applies


def check_folder_access(username: str, image_path: str, is_pull: bool) -> bool | None:
    """
    Check whether username is allowed to pull or push image_path.

    Returns:
        True  — access granted by folder rules
        False — access denied by folder rules
        None  — no folder rule applies at all (__root__ not configured)
    """
    folder = get_folder_for_path(image_path)
    if folder is None:
        return None

    for perm in folder.get("permissions", []):
        if perm["username"] == username:
            return (
                perm.get("can_pull", False) if is_pull else perm.get("can_push", False)
            )

    # User has no explicit entry in the matched folder → denied
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
    """Set or update a user's pull/push permissions on a folder. Requires admin."""
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


@router.get("/mine", response_model=list[str])
async def list_my_folders(
    current_user: UserInfo = Depends(get_current_user),
) -> list[str]:
    """
    Return folder names the current user can pull from.
    Admins receive an empty list meaning 'all folders allowed'.
    __root__ is excluded — it is implicit and has no display name in the UI.
    """
    if current_user.is_admin:
        return []
    allowed: list[str] = []
    for folder in _load_folders():
        if folder["name"] == ROOT_FOLDER_NAME:
            continue
        for perm in folder.get("permissions", []):
            if perm["username"] == current_user.username and perm.get("can_pull"):
                allowed.append(folder["name"])
                break
    return allowed


@router.get("/pushable", response_model=list[str])
async def list_pushable_folders(
    current_user: UserInfo = Depends(get_current_user),
) -> list[str]:
    """
    Return folder names the user can push to.
    Empty list means admin (all folders allowed).
    __root__ is excluded — pushing to root namespace has no folder prefix.
    """
    if current_user.is_admin:
        return []
    allowed: list[str] = []
    for folder in _load_folders():
        if folder["name"] == ROOT_FOLDER_NAME:
            continue
        for perm in folder.get("permissions", []):
            if perm["username"] == current_user.username and perm.get("can_push"):
                allowed.append(folder["name"])
                break
    return allowed


@router.get("/names", response_model=list[str])
async def list_folder_names(
    _: UserInfo = Depends(get_current_user),
) -> list[str]:
    """
    Return all configured folder names (excluding __root__).
    Accessible to any authenticated user.

    Used by the frontend to determine which visual tree nodes map to a real
    Portalcrane folder vs the __root__ catch-all, so the folder tree reflects
    the actual permission boundaries rather than the raw image path segments.
    """
    return [f["name"] for f in _load_folders() if f["name"] != ROOT_FOLDER_NAME]
