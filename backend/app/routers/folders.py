"""
Portalcrane - Folders Router
Manages registry folders (path prefixes) with per-user pull/push permissions.

A folder is a named prefix applied to image paths in the registry.
Example: folder "production" → images pushed as production/my-image:tag

Access rules:
- Admin users always have full access to all folders.
- Folder permissions are granted to GROUPS, never to individual users. A user's
  effective access is the union of the permissions of every group they belong to.
- The special __root__ folder covers:
    * images with no path prefix       (e.g. "nginx")
    * images whose prefix is unknown   (e.g. "editeur/nginx" when no folder
      named "editeur" exists)
    * only the FIRST segment matters   ("production/editeur/image" → "production")
- A user whose groups have no entry in the matched folder is always denied.

Protection rules for __root__:
- __root__ is created automatically at startup and cannot be deleted.
- Its description can still be edited by admins.
- Its permissions are managed the same way as any other folder.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from ..config import DATA_DIR
from ..core.jwt import UserInfo, get_current_user, require_admin
from .groups import (
    ensure_group_for_username,
    get_group_ids_for_user,
    group_name_for_id,
)

router = APIRouter()

_FOLDERS_FILE = Path(f"{DATA_DIR}/folders.json")

# Reserved name for the catch-all folder — cannot be deleted
ROOT_FOLDER_NAME = "__root__"


# ── Models ────────────────────────────────────────────────────────────────────


class FolderPermission(BaseModel):
    """Permission entry for a single group on a folder.

    group_name is a read-only display field resolved from the group id at read
    time; it is never persisted and may be None if the group was deleted.
    """

    group_id: str
    group_name: str | None = None
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
        # Prevent creation of a folder named __root__ via the API
        if v == ROOT_FOLDER_NAME:
            raise ValueError(f"The name '{ROOT_FOLDER_NAME}' is reserved")
        return v


class UpdateFolderRequest(BaseModel):
    """Payload to update a folder's description."""

    description: str = ""


class SetPermissionRequest(BaseModel):
    """Payload to set or update a group's permissions on a folder."""

    group_id: str
    can_pull: bool = False
    can_push: bool = False

    @field_validator("group_id")
    @classmethod
    def group_id_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Group id must not be empty")
        return v


# ── Storage helpers ───────────────────────────────────────────────────────────


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
                group_id=p["group_id"],
                group_name=group_name_for_id(p["group_id"]),
                can_pull=p.get("can_pull", False),
                can_push=p.get("can_push", False),
            )
            for p in d.get("permissions", [])
            if "group_id" in p
        ],
    )


# ── Migration helper ──────────────────────────────────────────────────────────


def ensure_root_folder_exists() -> None:
    """Ensure the __root__ folder always exists at startup."""
    folders = _load_folders()

    if any(f["name"] == ROOT_FOLDER_NAME for f in folders):
        return  # Already present — nothing to do

    root_entry = {
        "id": str(uuid.uuid4()),
        "name": ROOT_FOLDER_NAME,
        "description": "Default namespace — covers images with no folder prefix (e.g. nginx, ubuntu)",
        "created_at": datetime.now(UTC).isoformat(),
        "permissions": [],
    }
    folders.append(root_entry)
    _save_folders(folders)


def migrate_folder_permissions_to_groups() -> None:
    """Convert legacy per-user folder permissions to per-group permissions.

    Old schema stored ``{"username", "can_pull", "can_push"}`` entries. This
    migration rewrites each such entry to ``{"group_id", "can_pull", "can_push"}``
    by creating (or reusing) an auto-group named ``user-<username>`` that contains
    only that user, so existing access is preserved. Idempotent: entries already
    keyed by ``group_id`` are left untouched.
    """
    folders = _load_folders()
    changed = False

    for folder in folders:
        migrated: list[dict] = []
        for perm in folder.get("permissions", []):
            if "group_id" in perm:
                migrated.append(perm)
                continue
            username = perm.get("username")
            if not username:
                changed = True  # drop malformed legacy entry
                continue
            group_id = ensure_group_for_username(username)
            migrated.append(
                {
                    "group_id": group_id,
                    "can_pull": perm.get("can_pull", False),
                    "can_push": perm.get("can_push", False),
                }
            )
            changed = True
        folder["permissions"] = migrated

    if changed:
        _save_folders(folders)


# ── Public helpers used by registry_proxy and registry routers ────────────────


def get_folder_for_path(image_path: str) -> dict | None:
    """Return the folder dict that governs access to image_path.

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
    """Check whether username is allowed to pull or push image_path.

    Returns:
        True  — access granted by folder rules
        False — access denied by folder rules
        None  — no folder rule applies at all (__root__ not configured)
    """
    folder = get_folder_for_path(image_path)
    if folder is None:
        return None

    group_ids = get_group_ids_for_user(username)

    # Effective access is the union over every group the user belongs to.
    for perm in folder.get("permissions", []):
        if perm.get("group_id") in group_ids:
            granted = (
                perm.get("can_pull", False) if is_pull else perm.get("can_push", False)
            )
            if granted:
                return True

    # None of the user's groups grant the requested permission → denied
    return False


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", response_model=list[Folder])
async def list_folders(_: UserInfo = Depends(require_admin)) -> list[Folder]:
    """Return all folders. Requires admin."""
    return [_dict_to_folder(f) for f in _load_folders()]


@router.post("", response_model=Folder, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: CreateFolderRequest,
    _: UserInfo = Depends(require_admin),
) -> Folder:
    """Create a new folder. Requires admin.

    The name '__root__' is reserved and cannot be created via this endpoint.
    """
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
        "created_at": datetime.now(UTC).isoformat(),
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
    """Update a folder's description. Requires admin.

    The __root__ folder description can be updated.
    """
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
    """Delete a folder and all its permissions. Requires admin.

    The system folder '__root__' is protected and cannot be deleted,
    even by an administrator. This is enforced at the backend level.
    """
    folders = _load_folders()

    # Locate the target folder first to check if it is __root__
    target = next((f for f in folders if f["id"] == folder_id), None)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        )

    # Guard: __root__ cannot be deleted — it is a system folder
    if target["name"] == ROOT_FOLDER_NAME:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The root namespace folder cannot be deleted. "
                "It is a system folder required for access control."
            ),
        )

    _save_folders([f for f in folders if f["id"] != folder_id])


@router.put("/{folder_id}/permissions", response_model=Folder)
async def set_permission(
    folder_id: str,
    payload: SetPermissionRequest,
    _: UserInfo = Depends(require_admin),
) -> Folder:
    """Set or update a group's pull/push permissions on a folder. Requires admin."""
    folders = _load_folders()
    for f in folders:
        if f["id"] == folder_id:
            perms: list[dict] = f.setdefault("permissions", [])
            for p in perms:
                if p.get("group_id") == payload.group_id:
                    p["can_pull"] = payload.can_pull
                    p["can_push"] = payload.can_push
                    _save_folders(folders)
                    return _dict_to_folder(f)
            perms.append(
                {
                    "group_id": payload.group_id,
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
    "/{folder_id}/permissions/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_permission(
    folder_id: str,
    group_id: str,
    _: UserInfo = Depends(require_admin),
) -> None:
    """Remove a group's permissions from a folder. Requires admin."""
    folders = _load_folders()
    for f in folders:
        if f["id"] == folder_id:
            before = len(f.get("permissions", []))
            f["permissions"] = [
                p for p in f.get("permissions", []) if p.get("group_id") != group_id
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
    """Return folder names the current user can pull from.

    Admins receive an empty list meaning 'all folders allowed'.
    __root__ is excluded — it is implicit and has no display name in the UI.
    """
    if current_user.is_admin:
        return []
    group_ids = get_group_ids_for_user(current_user.username)
    allowed: list[str] = []
    for folder in _load_folders():
        if folder["name"] == ROOT_FOLDER_NAME:
            continue
        for perm in folder.get("permissions", []):
            if perm.get("group_id") in group_ids and perm.get("can_pull"):
                allowed.append(folder["name"])
                break
    return allowed


@router.get("/pushable", response_model=list[str])
async def list_pushable_folders(
    current_user: UserInfo = Depends(get_current_user),
) -> list[str]:
    """Return folder names the user can push to.

    Empty list means admin (all folders allowed).
    __root__ is excluded — pushing to root namespace has no folder prefix.
    """
    if current_user.is_admin:
        return []
    group_ids = get_group_ids_for_user(current_user.username)
    allowed: list[str] = []
    for folder in _load_folders():
        if folder["name"] == ROOT_FOLDER_NAME:
            continue
        for perm in folder.get("permissions", []):
            if perm.get("group_id") in group_ids and perm.get("can_push"):
                allowed.append(folder["name"])
                break
    return allowed


@router.get("/names", response_model=list[str])
async def list_folder_names(
    _: UserInfo = Depends(get_current_user),
) -> list[str]:
    """Return all configured folder names (excluding __root__).

    Accessible to any authenticated user.

    Used by the frontend to determine which visual tree nodes map to a real
    Portalcrane folder vs the __root__ catch-all, so the folder tree reflects
    the actual permission boundaries rather than the raw image path segments.
    """
    return [f["name"] for f in _load_folders() if f["name"] != ROOT_FOLDER_NAME]


def remove_permissions_for_group(group_id: str) -> int:
    """Remove a group from all folder permission lists and return removals."""
    folders = _load_folders()
    removed_count = 0

    for folder in folders:
        perms = folder.get("permissions", [])
        filtered = [p for p in perms if p.get("group_id") != group_id]
        removed_count += len(perms) - len(filtered)
        folder["permissions"] = filtered

    if removed_count:
        _save_folders(folders)

    return removed_count
