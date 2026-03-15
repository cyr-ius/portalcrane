"""Portalcrane - External Registry Service.

Orchestrates CRUD, connectivity testing, catalog browsing, and skopeo-based
push / sync / import operations for all external registry types.

Registry type routing:
  - ghcr.io            -> external_github   (GitHub Packages REST API)
  - docker.io variants -> external_dockerhub (Docker Hub REST API)
  - everything else    -> external_v2        (OCI Distribution V2 spec)

Changes:
  - tls_verify field: each registry stores whether TLS certificate
    verification should be enforced (defaults True).
  - [NEW] browse_external_images() — list repositories from an external
    registry via its HTTP v2 API (Évolution 1).
  - [NEW] browse_external_tags()   — list tags of a repo from an external
    registry (Évolution 1).
  - [NEW] run_import_job()         — mirror of run_sync_job() with src/dest
    reversed (external -> local).  direction="import" stored in job dict
    (Évolution 2).
  - run_sync_job() jobs now carry direction="export" so the UI can show
    direction badges in the history list (Évolution 2).
  - [FIX] _skopeo_tls_verify() helper: properly derive --tls-verify flag
    from both use_tls AND tls_verify fields. Previously only tls_verify was
    checked, causing skopeo to attempt HTTPS on plain-HTTP registries.
  - [REFACTOR] HTTP logic extracted into external_v2.py (OCI V2),
    external_github.py (GHCR) and external_dockerhub.py (Docker Hub).
    This service is now a pure orchestrator: it resolves the registry
    record from the JSON store, determines the registry type, and delegates
    to the appropriate module.
"""

import asyncio
import json
import logging
import os
import re
from urllib.parse import urlparse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import DATA_DIR, Settings, REGISTRY_HOST
from .external_github import (
    browse_github_packages,
    browse_github_tag,
    delete_github_package,
    get_github_tags_for_import,
    test_github_connection,
)
from .external_dockerhub import (
    browse_dockerhub_repositories,
    browse_dockerhub_tags,
    delete_dockerhub_repository,
    get_dockerhub_tags_for_import,
    test_dockerhub_connection,
)
from .external_v2 import (
    browse_v2_repositories,
    browse_v2_tags,
    check_v2_catalog,
    delete_v2_image,
    get_v2_tags_for_import,
    test_v2_connection,
    get_v2_tag_detail,
    delete_v2_tag,
    add_v2_tag,
)

logger = logging.getLogger(__name__)

_REGISTRIES_FILE = Path(f"{DATA_DIR}/external_registries.json")

_sync_jobs: dict[str, dict] = {}


# ── Registry CRUD helpers ─────────────────────────────────────────────────────


def _load_registries() -> list[dict]:
    """Load registry list from disk. Returns empty list if file is missing."""
    try:
        if _REGISTRIES_FILE.exists():
            return json.loads(_REGISTRIES_FILE.read_text())
    except Exception as exc:
        logger.warning("Failed to load external registries: %s", exc)
    return []


def _save_registries(registries: list[dict]) -> None:
    """Persist registry list to disk, creating directories as needed."""
    try:
        _REGISTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRIES_FILE.write_text(json.dumps(registries, indent=2))
    except Exception as exc:
        logger.error("Failed to save external registries: %s", exc)


def _redact(r: dict) -> dict:
    """Return a copy of the registry dict with the password redacted.

    tls_verify, use_tls and browsable are preserved as-is; defaults applied
    for old entries that predate these fields.

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
    }


# ── Registry type detection ───────────────────────────────────────────────────


def _normalize_registry_host(host: str) -> str:
    """Normalise a registry host string to bare hostname:port (no protocol, no path)."""
    value = (host or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlparse(value).netloc or value
    return value.split("/", 1)[0].strip("/")


def _is_ghcr(host: str) -> bool:
    """Return True when the registry host is GitHub Container Registry."""
    return _normalize_registry_host(host) == "ghcr.io"


def _is_dockerhub(host: str) -> bool:
    """Return True when the registry host is Docker Hub."""
    return _normalize_registry_host(host) in {
        "docker.io",
        "index.docker.io",
        "registry-1.docker.io",
    }


# ── URL / TLS helpers (shared, used by skopeo callers) ───────────────────────


def _skopeo_tls_verify(use_tls: bool, tls_verify: bool) -> bool:
    """Derive the boolean value for skopeo --src/dest-tls-verify.

    Mapping:
      use_tls=False              -> plain HTTP -> --tls-verify=false
      use_tls=True, verify=False -> HTTPS, skip cert check -> --tls-verify=false
      use_tls=True, verify=True  -> normal HTTPS -> --tls-verify=true
    """
    if not use_tls:
        return False
    return tls_verify


# ── Registry store public API ─────────────────────────────────────────────────


def get_registries(owner: str | None = None) -> list[dict]:
    """Return saved external registries (passwords redacted).

    When *owner* is provided only global + owner registries are returned.
    When *owner* is None (admin) all registries are returned.
    """
    registries = _load_registries()
    logger.debug(
        "External registry list requested (owner=%s, total=%d)", owner, len(registries)
    )
    if owner is None:
        return [_redact(r) for r in registries]
    return [
        _redact(r) for r in registries if r.get("owner", "global") in ("global", owner)
    ]


def get_registry_by_id(registry_id: str) -> dict | None:
    """Return a registry by ID (with real password for internal use)."""
    for r in _load_registries():
        if r["id"] == registry_id:
            if "use_tls" not in r:
                r["use_tls"] = True
            if "tls_verify" not in r:
                r["tls_verify"] = True
            return r
    return None


def delete_registry(registry_id: str) -> bool:
    """Delete a registry entry. Returns True if deleted, False if not found."""
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


def find_registry_credentials_for_host(host: str, owner: str) -> tuple[str, str] | None:
    """Return (username, password) for matching host from owner registries, then global."""
    target = _normalize_registry_host(host)
    if not target:
        return None

    registries = _load_registries()
    owner_matches = [
        r
        for r in registries
        if r.get("owner") == owner
        and _normalize_registry_host(r.get("host", "")) == target
    ]
    global_matches = [
        r
        for r in registries
        if r.get("owner", "global") == "global"
        and _normalize_registry_host(r.get("host", "")) == target
    ]

    for match in [*owner_matches, *global_matches]:
        username = (match.get("username") or "").strip()
        password = match.get("password") or ""
        if username and password:
            return username, password

    return None


# ── Connectivity & catalog ────────────────────────────────────────────────────


async def test_registry_connection(
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

    if _is_ghcr(host):
        return await test_github_connection(
            host=host, username=username, password=password, tls_verify=tls_verify
        )

    if _is_dockerhub(host):
        return await test_dockerhub_connection(
            host=host,
            username=username,
            password=password,
            tls_verify=tls_verify,
        )

    return await test_v2_connection(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )


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
    # Docker Hub: browsable only when credentials exist (Hub REST API path)
    if _is_dockerhub(host):
        has_creds = bool((username or "").strip() and (password or "").strip())
        logger.debug(
            "check_catalog_browsable: Docker Hub — browsable=%s (creds present=%s)",
            has_creds,
            has_creds,
        )
        return has_creds

    # GHCR: browsable when a token is stored (GitHub Packages API path)
    if _is_ghcr(host):
        browsable = bool(password)
        logger.debug(
            "check_catalog_browsable: GHCR — browsable=%s (token present=%s)",
            browsable,
            browsable,
        )
        return browsable

    # Standard V2 registry: probe /v2/_catalog
    return await check_v2_catalog(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )


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

    checks = await test_registry_connection(
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

    use_tls/tls_verify are only changed when explicitly supplied.
    browsable is re-evaluated whenever any connectivity-related field
    (host, username, password, use_tls, tls_verify) changes.
    """
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
            checks = await test_registry_connection(
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


# ── Browse — public orchestration API ─────────────────────────────────────────


async def browse_external_images(
    registry_id: str,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """List repositories available in an external registry.

    Routing:
      - GHCR (ghcr.io)   -> external_github.browse_github_packages
      - Docker Hub        -> external_dockerhub.browse_dockerhub_repositories
      - All other V2      -> external_v2.browse_v2_repositories

    Returns a paginated dict compatible with the local PaginatedImages shape:
      { items, total, page, page_size, total_pages, error? }
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    # ── GHCR: GitHub Packages API ──────────────────────────────────────────
    if _is_ghcr(host) and password:
        return await browse_github_packages(
            username=username,
            token=password,
            owner=username,
            search=search,
            page=page,
            page_size=page_size,
            tls_verify=tls_verify,
        )

    # ── Docker Hub: Hub REST API ───────────────────────────────────────────
    if _is_dockerhub(host) and username and password:
        return await browse_dockerhub_repositories(
            username=username,
            password=password,
            namespace=username,
            search=search,
            page=page,
            page_size=page_size,
            tls_verify=tls_verify,
        )

    # Docker Hub without credentials: browsing not supported
    if _is_dockerhub(host):
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": (
                "Docker Hub browsing requires credentials. "
                "Please add your Docker Hub username and password (or access token) "
                "to this registry entry."
            ),
        }

    # ── Standard V2 registry: OCI Distribution /v2/_catalog ───────────────
    return await browse_v2_repositories(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
        search=search,
        page=page,
        page_size=page_size,
    )


async def browse_external_tags(registry_id: str, repository: str) -> dict:
    """List tags for a repository in an external registry.

    Routing:
      - Docker Hub -> external_dockerhub.browse_dockerhub_tags
      - GHCR       -> external_v2.browse_v2_tags (standard /v2/ tags endpoint)
      - All other  -> external_v2.browse_v2_tags

    Returns {"repository": str, "tags": list[str]}.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    # ── GHCR: REST API ───────────────────────────────────────────
    if _is_ghcr(host):
        tags = await browse_github_tag(
            owner=username, token=password, repository=repository, tls_verify=tls_verify
        )
        return {"repository": repository, "tags": tags}

    # ── Docker Hub: Hub REST API ───────────────────────────────────────────
    if _is_dockerhub(host):
        tags = await browse_dockerhub_tags(
            username=username,
            password=password,
            repository=repository,
            tls_verify=tls_verify,
        )
        return {"repository": repository, "tags": tags}

    # ── GHCR and all standard V2 registries ────────────────────────────────
    tags = await browse_v2_tags(
        host=host,
        username=username,
        password=password,
        repository=repository,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    return {"repository": repository, "tags": tags}


async def delete_external_image(registry_id: str, repository: str) -> dict:
    """Delete all tags for a repository in an external registry.

    Routing:
      - GHCR       -> external_github.delete_github_package
      - Docker Hub -> external_dockerhub.delete_dockerhub_repository
      - All other  -> external_v2.delete_v2_image (manifest-based tag deletion)
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    # ── GHCR: GitHub Packages DELETE API ──────────────────────────────────
    if _is_ghcr(host) and password:
        owner = username
        pkg_name = repository.split("/", 1)[-1] if "/" in repository else repository
        error = await delete_github_package(
            token=password, owner=owner, package=pkg_name, tls_verify=tls_verify
        )
        if error:
            return {
                "deleted_tags": [],
                "failed_tags": [repository],
                "message": f"GitHub delete failed: {error}",
            }
        return {
            "deleted_tags": [repository],
            "failed_tags": [],
            "message": f"Package '{repository}' deleted from GitHub Container Registry",
        }

    # ── Docker Hub: Hub REST API ───────────────────────────────────────────
    if _is_dockerhub(host):
        error = await delete_dockerhub_repository(
            username=username,
            password=password,
            repository=repository,
            tls_verify=tls_verify,
        )
        if error:
            return {
                "deleted_tags": [],
                "failed_tags": [repository],
                "message": f"Docker Hub delete failed: {error}",
            }
        return {
            "deleted_tags": [repository],
            "failed_tags": [],
            "message": f"Repository '{repository}' deleted from Docker Hub.",
        }

    # ── Standard V2: manifest-based tag deletion ───────────────────────────
    return await delete_v2_image(
        host=host,
        username=username,
        password=password,
        repository=repository,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )


# ── Validation helpers ────────────────────────────────────────────────────────


def validate_folder_path(folder: str) -> str | None:
    """Validate an optional folder/prefix path. Returns sanitised path or raises ValueError."""
    if not folder:
        return None
    if ".." in folder.split("/"):
        raise ValueError("Folder path must not contain '..'")
    if folder.startswith("/"):
        raise ValueError("Folder path must not start with '/'")
    if not re.match(r"^[a-zA-Z0-9._\-/]+$", folder):
        raise ValueError(
            "Folder path contains invalid characters (allowed: a-z A-Z 0-9 . - _ /)"
        )
    return folder.strip("/")


def build_target_path(
    folder: str | None, image_name: str, tag: str, registry_host: str
) -> str:
    """Build the full skopeo destination reference."""
    path = f"{folder}/{image_name}" if folder else image_name
    normalized_host = _normalize_registry_host(registry_host)

    if normalized_host in {"docker.io", "index.docker.io", "registry-1.docker.io"}:
        return f"docker://{path}:{tag}"

    return f"docker://{registry_host}/{path}:{tag}"


# ── Skopeo helpers ────────────────────────────────────────────────────────────


async def skopeo_push(
    oci_dir: str,
    dest_ref: str,
    dest_username: str,
    dest_password: str,
    settings: Settings,
    tls_verify: bool = True,
) -> tuple[bool, str]:
    """Push an OCI layout directory to a registry using skopeo copy."""
    cmd = [
        "skopeo",
        "copy",
        f"--dest-tls-verify={'true' if tls_verify else 'false'}",
    ]
    if dest_username and dest_password:
        cmd += ["--dest-creds", f"{dest_username}:{dest_password}"]
    cmd += [f"oci:{oci_dir}:latest", dest_ref]

    env = {**os.environ, **settings.env_proxy}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return True, f"Pushed to {dest_ref}"
    return False, stderr.decode().strip() or stdout.decode().strip()


async def skopeo_sync_image(
    src_ref: str,
    dest_ref: str,
    dest_username: str,
    dest_password: str,
    settings: Settings,
    src_username: str = "",
    src_password: str = "",
    src_tls_verify: bool = False,
    dest_tls_verify: bool = True,
) -> tuple[bool, str]:
    """Copy an image between two docker:// registries using skopeo copy.

    Used by both export (local->external) and import (external->local) jobs.
    """
    cmd = [
        "skopeo",
        "copy",
        f"--src-tls-verify={'true' if src_tls_verify else 'false'}",
        f"--dest-tls-verify={'true' if dest_tls_verify else 'false'}",
    ]
    if src_username and src_password:
        cmd += ["--src-creds", f"{src_username}:{src_password}"]
    if dest_username and dest_password:
        cmd += ["--dest-creds", f"{dest_username}:{dest_password}"]
    cmd += [src_ref, dest_ref]

    env = {**os.environ, **settings.env_proxy}
    logger.debug(
        "skopeo copy: %s",
        " ".join(
            "***" if i > 0 and cmd[i - 1] in ("--src-creds", "--dest-creds") else a
            for i, a in enumerate(cmd)
        ),
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return True, f"Copied {src_ref} -> {dest_ref}"
    return False, stderr.decode().strip() or stdout.decode().strip()


# ── Sync namespace rewriting ──────────────────────────────────────────────────


def _rewrite_image_name_for_sync(
    img: str,
    dest_folder: str | None,
    dest_username: str,
) -> str:
    """Rewrite the image repository name for the destination registry.

    Rules (applied in order):
      1. dest_folder supplied -> replace namespace with folder prefix
      2. dest_username supplied -> prepend username (Docker Hub compat)
      3. No override -> preserve the full source path including namespace
    """
    leaf = img.split("/")[-1]
    if dest_folder:
        return f"{dest_folder}/{leaf}"
    if dest_username:
        return f"{dest_username}/{leaf}"
    return img


# ── Sync / Import jobs ────────────────────────────────────────────────────────


def list_sync_jobs() -> list[dict]:
    """Return all sync/import jobs sorted by start time descending."""
    jobs = list(_sync_jobs.values())
    jobs.sort(key=lambda j: j.get("started_at", ""), reverse=True)
    return jobs


async def run_sync_job(
    source_image: str,
    dest_registry_id: str,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> str:
    """Start an asynchronous export job (local -> external) and return the job ID.

    The job dict carries direction="export" for display in the history list.
    """
    registry = get_registry_by_id(dest_registry_id)
    if not registry:
        raise ValueError(f"Registry {dest_registry_id} not found")

    job_id = str(uuid.uuid4())
    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "export",
        "source_image": source_image,
        "dest_registry_id": dest_registry_id,
        "dest_registry_name": registry.get("name", ""),
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "images_total": 0,
        "images_done": 0,
        "errors": [],
    }

    asyncio.create_task(
        _run_sync_job_task(
            job_id=job_id,
            source_image=source_image,
            registry=registry,
            dest_folder=dest_folder,
            local_registry_url=local_registry_url,
            settings=settings,
        )
    )
    return job_id


async def _run_sync_job_task(
    job_id: str,
    source_image: str,
    registry: dict,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> None:
    """Background task: export images from local registry to an external registry."""
    dest_host = registry["host"]
    dest_username = registry.get("username", "")
    dest_password = registry.get("password", "")
    dest_tls_verify = _skopeo_tls_verify(
        registry.get("use_tls", True), registry.get("tls_verify", True)
    )
    src_tls_verify = not local_registry_url.startswith("http://")

    try:
        # Resolve image list from local registry
        if source_image == "(all)":
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{local_registry_url}/v2/_catalog?n=1000")
                resp.raise_for_status()
                images: list[str] = resp.json().get("repositories", [])
        else:
            images = [source_image.split(":")[0]]

        _sync_jobs[job_id]["images_total"] = len(images)

        errors: list[str] = []
        for img in images:
            # Fetch tags from local registry
            async with httpx.AsyncClient(timeout=30) as client:
                tr = await client.get(f"{local_registry_url}/v2/{img}/tags/list")
                tags: list[str] = (
                    tr.json().get("tags") or [] if tr.status_code == 200 else []
                )

            for tag in tags:
                src_ref = f"docker://{REGISTRY_HOST}/{img}:{tag}"
                dest_image = _rewrite_image_name_for_sync(
                    img=img,
                    dest_folder=dest_folder,
                    dest_username=dest_username,
                )
                dest_ref = build_target_path(None, dest_image, tag, dest_host)

                ok, msg = await skopeo_sync_image(
                    src_ref=src_ref,
                    dest_ref=dest_ref,
                    src_username="",
                    src_password="",
                    dest_username=dest_username,
                    dest_password=dest_password,
                    settings=settings,
                    src_tls_verify=src_tls_verify,
                    dest_tls_verify=dest_tls_verify,
                )
                if not ok:
                    errors.append(f"{img}:{tag} — {msg}")
                    logger.warning("sync export error %s:%s — %s", img, tag, msg)

            _sync_jobs[job_id]["images_done"] = _sync_jobs[job_id]["images_done"] + 1

        _sync_jobs[job_id]["status"] = "done" if not errors else "done_with_errors"
        _sync_jobs[job_id]["errors"] = errors
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        logger.error("sync export job %s failed: %s", job_id, exc)
        _sync_jobs[job_id]["status"] = "failed"
        _sync_jobs[job_id]["errors"] = [str(exc)]
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


async def run_import_job(
    source_registry_id: str,
    source_image: str,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> str:
    """Start an asynchronous import job (external -> local) and return the job ID.

    The job dict carries direction="import" for display in the history list.
    """
    registry = get_registry_by_id(source_registry_id)
    if not registry:
        raise ValueError(f"Registry {source_registry_id} not found")

    job_id = str(uuid.uuid4())
    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "import",
        "source_image": source_image,
        "source_registry_id": source_registry_id,
        "source_registry_name": registry.get("name", ""),
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "images_total": 0,
        "images_done": 0,
        "errors": [],
    }

    asyncio.create_task(
        _run_import_job_task(
            job_id=job_id,
            source_image=source_image,
            registry=registry,
            dest_folder=dest_folder,
            local_registry_url=local_registry_url,
            settings=settings,
        )
    )
    return job_id


async def _run_import_job_task(
    job_id: str,
    source_image: str,
    registry: dict,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> None:
    """Background task: import images from an external registry into the local registry."""
    src_host = registry["host"]
    src_username = registry.get("username", "")
    src_password = registry.get("password", "")
    src_use_tls = registry.get("use_tls", True)
    src_tls_verify_field = registry.get("tls_verify", True)
    src_tls_verify = _skopeo_tls_verify(src_use_tls, src_tls_verify_field)
    dest_tls_verify = not local_registry_url.startswith("http://")

    try:
        # ── Resolve image list from source registry ────────────────────────
        if source_image == "(all)":
            if _is_ghcr(src_host) and src_password:
                result = await browse_github_packages(
                    username=src_username,
                    token=src_password,
                    owner=src_username,
                    search=None,
                    page=1,
                    page_size=200,
                )
                images: list[str] = [item["name"] for item in result.get("items", [])]
            elif _is_dockerhub(src_host) and src_username and src_password:
                result = await browse_dockerhub_repositories(
                    username=src_username,
                    password=src_password,
                    namespace=src_username,
                    search=None,
                    page=1,
                    page_size=200,
                )
                images = [item["name"] for item in result.get("items", [])]
            else:
                # Standard V2: /v2/_catalog via external_v2
                result = await browse_v2_repositories(
                    host=src_host,
                    username=src_username,
                    password=src_password,
                    use_tls=src_use_tls,
                    tls_verify=src_tls_verify_field,
                    page=1,
                    page_size=1000,
                )
                images = [item["name"] for item in result.get("items", [])]
        else:
            images = [source_image.split(":")[0]]

        _sync_jobs[job_id]["images_total"] = len(images)

        errors: list[str] = []
        for img in images:
            # ── Resolve tags per image ─────────────────────────────────────
            if source_image != "(all)" and ":" in source_image:
                tags = [source_image.split(":", 1)[1]]
            elif _is_ghcr(src_host) and src_password:
                tags = await get_github_tags_for_import(
                    token=src_password,
                    owner=src_username,
                    package=img.split("/", 1)[-1] if "/" in img else img,
                )
            elif _is_dockerhub(src_host) and src_username and src_password:
                tags = await get_dockerhub_tags_for_import(
                    username=src_username,
                    password=src_password,
                    repository=img,
                )
            else:
                tags = await get_v2_tags_for_import(
                    host=src_host,
                    username=src_username,
                    password=src_password,
                    repository=img,
                    use_tls=src_use_tls,
                    tls_verify=src_tls_verify_field,
                )

            # ── Rewrite destination image name ─────────────────────────────
            dest_img = _rewrite_image_name_for_sync(
                img=img, dest_folder=dest_folder, dest_username=""
            )
            for tag in tags:
                src_ref = build_target_path(None, img, tag, src_host)
                dest_ref = f"docker://{REGISTRY_HOST}/{dest_img}:{tag}"

                ok, msg = await skopeo_sync_image(
                    src_ref=src_ref,
                    dest_ref=dest_ref,
                    src_username=src_username,
                    src_password=src_password,
                    dest_username="",
                    dest_password="",
                    settings=settings,
                    src_tls_verify=src_tls_verify,
                    dest_tls_verify=dest_tls_verify,
                )
                if not ok:
                    errors.append(f"{img}:{tag} — {msg}")
                    logger.warning("sync import error %s:%s — %s", img, tag, msg)

            _sync_jobs[job_id]["images_done"] = _sync_jobs[job_id]["images_done"] + 1

        _sync_jobs[job_id]["status"] = "done" if not errors else "done_with_errors"
        _sync_jobs[job_id]["errors"] = errors
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        logger.error("sync import job %s failed: %s", job_id, exc)
        _sync_jobs[job_id]["status"] = "failed"
        _sync_jobs[job_id]["errors"] = [str(exc)]
        _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


async def get_external_tag_detail(registry_id: str, repository: str, tag: str) -> dict:
    """Return full image metadata for a specific tag in an external V2 registry.

    Only standard V2 registries are supported (not Docker Hub, not GHCR).
    Returns an empty dict when the registry type is unsupported or on error.

    Args:
        registry_id: ID of the saved external registry.
        repository:  Repository path, e.g. "myorg/myimage".
        tag:         Tag name, e.g. "latest".

    Returns:
        Dict matching the ImageDetail schema (name, tag, digest, size,
        created, architecture, os, layers, labels, env, cmd, entrypoint,
        exposed_ports) or empty dict on failure.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    # Only standard V2 registries support this operation
    if _is_dockerhub(host) or _is_ghcr(host):
        return {}

    return await get_v2_tag_detail(
        host=host,
        username=username,
        password=password,
        repository=repository,
        tag=tag,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )


async def delete_external_tag(registry_id: str, repository: str, tag: str) -> dict:
    """Delete a single tag from an external V2 registry.

    Args:
        registry_id: ID of the saved external registry.
        repository:  Repository path.
        tag:         Tag name to delete.

    Returns:
        Dict with keys: success (bool), message (str).
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    if _is_dockerhub(host) or _is_ghcr(host):
        return {
            "success": False,
            "message": "Single-tag delete is not supported for this registry type",
        }

    return await delete_v2_tag(
        host=host,
        username=username,
        password=password,
        repository=repository,
        tag=tag,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )


async def add_external_tag(
    registry_id: str, repository: str, source_tag: str, new_tag: str
) -> dict:
    """Create a new tag by copying a manifest in an external V2 registry.

    Args:
        registry_id: ID of the saved external registry.
        repository:  Repository path.
        source_tag:  Existing tag whose manifest will be copied.
        new_tag:     New tag name to create.

    Returns:
        Dict with keys: success (bool), message (str).
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    if _is_dockerhub(host) or _is_ghcr(host):
        return {
            "success": False,
            "message": "Tag creation is not supported for this registry type",
        }

    return await add_v2_tag(
        host=host,
        username=username,
        password=password,
        repository=repository,
        source_tag=source_tag,
        new_tag=new_tag,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
