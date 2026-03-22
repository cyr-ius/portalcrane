"""Portalcrane - External Registry Service.

Orchestrates CRUD, connectivity testing, catalog browsing, and skopeo-based
push / sync / import operations for all external registry types.

Registry type routing:
  - ghcr.io            -> external_github   (GitHub Packages REST API)
  - docker.io variants -> external_dockerhub (Docker Hub REST API)
  - everything else    -> external_v2        (OCI Distribution V2 spec)

Local registry system entry:
  A hidden system registry with ID "__local__" is injected into the registry
  list so the frontend can use the unified V2 browse / tag-detail infrastructure
  for the local embedded registry without it appearing in the External Registries
  settings panel (filtered by the system=True flag).
"""

import json
import logging
import shutil
import uuid
from pathlib import Path

from ..config import DATA_DIR, REGISTRY_HOST
from .providers import resolve_provider, resolve_provider_from_registry

logger = logging.getLogger(__name__)

REGISTRY_DATA_DIR = f"{DATA_DIR}/registry"
REGISTRY_REPOS_DIR = f"{REGISTRY_DATA_DIR}/docker/registry/v2/repositories"
REGISTRIES_FILE = Path(f"{DATA_DIR}/external_registries.json")

# ── Local registry system entry ────────────────────────────────────────────────
# Reserved ID for the embedded local registry exposed as a hidden V2 source.
# This entry is injected into the registry list at runtime so the frontend
# can use the unified V2 browse/tag-detail infrastructure for the local registry
# without it appearing in the External Registries settings panel.
LOCAL_REGISTRY_SYSTEM_ID = "__local__"


def _get_local_registry_entry() -> dict:
    """Return the embedded local registry as a hidden system registry entry.

    This entry is marked with system=True so the frontend can filter it out
    of the External Registries settings panel while still using it as a source
    in the Images browser and Staging pipeline.

    The local registry runs on plain HTTP (no TLS) on localhost:5000 inside
    the container, managed by supervisord.
    """
    return {
        "id": LOCAL_REGISTRY_SYSTEM_ID,
        "name": "Local Registry",
        "host": REGISTRY_HOST,
        "username": "",
        "password": "",
        "owner": "global",
        "use_tls": False,
        "tls_verify": False,
        "browsable": True,
        "system": True,
    }


# ── Registry CRUD helpers ─────────────────────────────────────────────────────


def _load_registries() -> list[dict]:
    """Load registry list from disk. Returns empty list if file is missing."""
    try:
        if REGISTRIES_FILE.exists():
            return json.loads(REGISTRIES_FILE.read_text())
    except Exception as exc:
        logger.warning("Failed to load external registries: %s", exc)
    return []


def _save_registries(registries: list[dict]) -> None:
    """Persist registry list to disk, creating directories as needed."""
    try:
        REGISTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        REGISTRIES_FILE.write_text(json.dumps(registries, indent=2))
    except Exception as exc:
        logger.error("Failed to save external registries: %s", exc)


def _redact(r: dict) -> dict:
    """Return a copy of the registry dict with the password redacted.

    tls_verify, use_tls, browsable and system fields are preserved as-is;
    defaults applied for old entries that predate these fields.

    browsable defaults to True for backward compatibility: existing entries
    continue to appear in source selectors until they are saved again, at
    which point check_catalog_browsable() re-evaluates the field.
    """
    return {
        **r,
        "password": "••••••••" if r.get("password") else "",
        "use_tls": r.get("use_tls", True),
        "tls_verify": r.get("tls_verify", True),
        "browsable": r.get("browsable", True),
        "system": r.get("system", False),
    }


# ── Registry store public API ─────────────────────────────────────────────────


def get_registries(owner: str | None = None, include_system: bool = True) -> list[dict]:
    """Return saved external registries (passwords redacted).

    When *owner* is provided only global + owner registries are returned.
    When *owner* is None (admin) all registries are returned.

    The hidden local registry system entry is prepended when include_system=True
    so the frontend can use it as a source without it persisting to disk.

    Args:
        owner:          Filter by owner username; None returns all registries.
        include_system: When True (default), prepend the local system registry.
    """
    registries = _load_registries()
    logger.debug(
        "External registry list requested (owner=%s, total=%d)", owner, len(registries)
    )

    result: list[dict] = []

    # Prepend the hidden local system registry entry
    if include_system:
        result.append(_get_local_registry_entry())

    if owner is None:
        result += [_redact(r) for r in registries]
    else:
        result += [
            _redact(r)
            for r in registries
            if r.get("owner", "global") in ("global", owner)
        ]

    return result


def get_registry_by_id(registry_id: str) -> dict | None:
    """Return a registry by ID (with real password for internal use).

    The special LOCAL_REGISTRY_SYSTEM_ID returns the local system entry
    without any disk lookup.
    """
    # Intercept the reserved local registry ID
    if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
        return _get_local_registry_entry()

    for r in _load_registries():
        if r["id"] == registry_id:
            if "use_tls" not in r:
                r["use_tls"] = True
            if "tls_verify" not in r:
                r["tls_verify"] = True
            return r
    return None


def delete_registry(registry_id: str) -> bool:
    """Delete a registry entry. Returns True if deleted, False if not found.

    The local system registry cannot be deleted.
    """
    if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
        return False
    registries = _load_registries()
    new_list = [r for r in registries if r["id"] != registry_id]
    if len(new_list) == len(registries):
        return False
    _save_registries(new_list)
    return True


def delete_registries_for_owner(owner: str) -> int:
    """Delete all personal registries owned by *owner* and return count."""
    registries = _load_registries()
    new_list = [r for r in registries if r.get("owner", "global") != owner]
    deleted_count = len(registries) - len(new_list)
    if deleted_count:
        _save_registries(new_list)
    return deleted_count


async def purge_registry(registry_id: str) -> tuple[list[str], list[dict]]:
    """Purge ghost repositories directly from the local filesystem.

    Returns a tuple (purged, errors)
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    purged: list[str] = []
    errors: list[dict] = []

    provider = resolve_provider_from_registry(registry)
    empty = await provider.list_empty_repositories()
    if not empty:
        errors.append({"repo": "", "error": "No empty repositories found"})

    for repo in empty:
        repo_path = Path(REGISTRY_REPOS_DIR) / repo
        try:
            resolved = repo_path.resolve()
            base = Path(REGISTRY_REPOS_DIR).resolve()
            if not str(resolved).startswith(str(base)):
                errors.append({"repo": repo, "error": "Path traversal attempt blocked"})
                continue
            if resolved.exists():
                shutil.rmtree(resolved)
            purged.append(repo)
        except OSError as exc:
            logger.error("Failed to purge repository %s: %s", repo, exc)
            errors.append({"repo": repo, "error": "Deletion failed"})
        except Exception:
            logger.exception("Unexpected error purging repository %s", repo)
            errors.append({"repo": repo, "error": "Unexpected error"})

    return purged, errors


# ── Registry create / update ──────────────────────────────────────────────────


async def create_registry(
    name: str,
    host: str,
    username: str,
    password: str,
    owner: str = "global",
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict:
    """Create and persist a new external registry entry.

    *use_tls*    — when False, all HTTP connections use plain http://.
    *tls_verify* — when False, TLS certificate errors are ignored (HTTPS only).

    The *browsable* field is probed immediately after creation so the UI
    can show/hide the registry in the Images source selector without an
    additional catalog-check request.
    """

    checks = await test(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )

    if not checks["reachable"] or not checks["auth_ok"]:
        return checks

    browsable = await check_catalog_browsable(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    registry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "host": host,
        "username": username,
        "password": password,
        "owner": owner,
        "use_tls": use_tls,
        "tls_verify": tls_verify,
        "browsable": browsable,
        "system": False,
    }
    registries = _load_registries()
    registries.append(registry)
    _save_registries(registries)
    logger.debug(
        "External registry created id=%s host=%s owner=%s browsable=%s",
        registry["id"],
        host,
        owner,
        browsable,
    )
    return _redact(registry)


async def update_registry(
    registry_id: str,
    name: str | None = None,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    owner: str | None = None,
    use_tls: bool | None = None,
    tls_verify: bool | None = None,
) -> dict | None:
    """Update a registry entry (partial update — only supplied fields are changed).

    The local system registry cannot be updated via this function.

    use_tls/tls_verify are only changed when explicitly supplied.
    browsable is re-evaluated whenever any connectivity-related field
    (host, username, password, use_tls, tls_verify) changes.
    """
    # Protect the local system registry from accidental modification
    if registry_id == LOCAL_REGISTRY_SYSTEM_ID:
        return None

    registries = _load_registries()
    for r in registries:
        if r["id"] == registry_id:
            if name is not None:
                r["name"] = name
            if host is not None:
                r["host"] = host
            if username is not None:
                r["username"] = username
            if password:
                r["password"] = password
            if owner is not None:
                r["owner"] = owner
            if use_tls is not None:
                r["use_tls"] = use_tls
            if tls_verify is not None:
                r["tls_verify"] = tls_verify

            # Re-check authentication
            checks = await test(
                host=r["host"],
                username=r.get("username", ""),
                password=r.get("password", ""),
                use_tls=r.get("use_tls", True),
                tls_verify=r.get("tls_verify", True),
            )

            if not checks["reachable"] or not checks["auth_ok"]:
                return checks

            # Re-check browsability when any connectivity parameter changes
            connectivity_changed = any(
                v is not None for v in [host, username, password, use_tls, tls_verify]
            )
            if connectivity_changed:
                r["browsable"] = await check_catalog_browsable(
                    host=r["host"],
                    username=r.get("username", ""),
                    password=r.get("password", ""),
                    use_tls=r.get("use_tls", True),
                    tls_verify=r.get("tls_verify", True),
                )

            _save_registries(registries)
            logger.debug(
                "External registry updated id=%s host=%s owner=%s use_tls=%s "
                "tls_verify=%s browsable=%s",
                registry_id,
                r.get("host"),
                r.get("owner", "global"),
                r.get("use_tls", True),
                r.get("tls_verify", True),
                r.get("browsable"),
            )
            return _redact(r)
    return None


# ── Connectivity & catalog ────────────────────────────────────────────────────


async def test(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict:
    """Probe the registry to check reachability and validate credentials.

    Delegates to external_v2.test_v2_connection for all registry types
    (GHCR and Docker Hub also expose the standard /v2/ ping endpoint).

    Returns {"reachable": bool, "auth_ok": bool, "message": str}.
    """

    provider = resolve_provider(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    return await provider.test_connection()


async def check_catalog_browsable(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> bool:
    """Determine whether this registry supports repository listing.

    Routing:
      - Docker Hub (docker.io) — never exposes /v2/_catalog; browsable only
        when credentials are present (Hub REST API is used instead).
      - GHCR (ghcr.io) — always considered browsable when a token is stored
        (GitHub Packages API is used).
      - All other registries — probe /v2/_catalog via external_v2.check_v2_catalog.

    The result is stored in the ``browsable`` field of the registry entry so
    the frontend can hide non-browsable registries from the Images source
    selector without making additional requests.
    """

    provider = resolve_provider(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    return await provider.check_catalog()


async def ping_catalog(registry_id: str) -> bool:
    """Check local registry connectivity."""
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    provider = resolve_provider_from_registry(registry)

    return await provider.ping()
