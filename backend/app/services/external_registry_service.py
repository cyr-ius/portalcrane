"""
Portalcrane - External Registry Service
Manages the list of external registries and exposes skopeo-based
push, synchronisation, browse and import helpers.

Changes:
  - tls_verify field: each registry stores whether TLS certificate
    verification should be enforced (defaults True).
  - [NEW] browse_external_images() — list repositories from an external
    registry via its HTTP v2 API (Évolution 1).
  - [NEW] browse_external_tags()   — list tags of a repo from an external
    registry (Évolution 1).
  - [NEW] run_import_job()         — mirror of run_sync_job() with src/dest
    reversed (external → local).  direction="import" stored in job dict
    (Évolution 2).
  - run_sync_job() jobs now carry direction="export" so the UI can show
    direction badges in the history list (Évolution 2).
  - [FIX] _skopeo_tls_verify() helper: properly derive --tls-verify flag
    from both use_tls AND tls_verify fields. Previously only tls_verify was
    checked, causing skopeo to attempt HTTPS on plain-HTTP registries.
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

    tls_verify and use_tls are preserved as-is; defaults applied for old entries
    that predate these fields.
    """
    return {
        **r,
        "password": "••••••••" if r.get("password") else "",
        "use_tls": r.get("use_tls", True),
        "tls_verify": r.get("tls_verify", True),
    }


def get_registries(owner: str | None = None) -> list[dict]:
    """
    Return saved external registries (passwords redacted).

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


def _normalize_registry_host(host: str) -> str:
    """Normalise a registry host string to bare hostname:port (no protocol, no path)."""
    value = (host or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlparse(value).netloc or value
    return value.split("/", 1)[0].strip("/")


def _build_registry_base_url(host: str, use_tls: bool = True) -> str:
    """
    Build the base URL for a registry host string.

    When the host already contains a scheme (http:// or https://) it is used
    as-is so callers who stored the full URL are unaffected.
    When *use_tls* is False the scheme is http://, otherwise https://.
    """
    if "://" in host:
        return host.rstrip("/")
    scheme = "https" if use_tls else "http"
    return f"{scheme}://{host}"


def _skopeo_tls_verify(use_tls: bool, tls_verify: bool) -> bool:
    """
    Derive the boolean value for skopeo --src/dest-tls-verify.

    The two registry fields have distinct semantics:
      - use_tls    : whether the registry uses TLS at all (HTTP vs HTTPS)
      - tls_verify : whether to validate the TLS certificate (self-signed)

    Mapping:
      use_tls=False              → plain HTTP → --tls-verify=false
      use_tls=True, verify=False → HTTPS, skip cert check → --tls-verify=false
      use_tls=True, verify=True  → normal HTTPS → --tls-verify=true

    This fix resolves the "http: server gave HTTP response to HTTPS client"
    error that occurred when skopeo received --dest-tls-verify=true for a
    plain-HTTP registry (use_tls=False was ignored).
    """
    if not use_tls:
        return False
    return tls_verify


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


def create_registry(
    name: str,
    host: str,
    username: str,
    password: str,
    owner: str = "global",
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict:
    """
    Create and persist a new external registry entry.

    *use_tls*    — when False, all HTTP connections use plain http://.
                   When True (default), https:// is used.
    *tls_verify* — only relevant when use_tls is True; controls whether the
                   TLS certificate is validated.  Set to False for self-signed
                   certificates on HTTPS registries.
    """
    registries = _load_registries()
    entry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "host": host,
        "username": username,
        "password": password,
        "owner": owner,
        "use_tls": use_tls,
        "tls_verify": tls_verify,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    registries.append(entry)
    _save_registries(registries)
    logger.debug(
        "External registry created id=%s host=%s owner=%s use_tls=%s tls_verify=%s",
        entry["id"],
        host,
        owner,
        use_tls,
        tls_verify,
    )
    return _redact(entry)


def update_registry(
    registry_id: str,
    name: str | None,
    host: str | None,
    username: str | None,
    password: str | None,
    owner: str | None = None,
    use_tls: bool | None = None,
    tls_verify: bool | None = None,
) -> dict | None:
    """Update an existing registry entry. use_tls/tls_verify only changed when explicitly supplied."""
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
            _save_registries(registries)
            logger.debug(
                "External registry updated id=%s host=%s owner=%s use_tls=%s tls_verify=%s",
                registry_id,
                r.get("host"),
                r.get("owner", "global"),
                r.get("use_tls", True),
                r.get("tls_verify", True),
            )
            return _redact(r)
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


# ── Connectivity test ─────────────────────────────────────────────────────────


async def test_registry_connection(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict:
    """
    Probe the registry /v2/ endpoint to check reachability and credentials.

    *use_tls*    — when False, connects over plain HTTP (http://).
    *tls_verify* — only relevant when use_tls is True; set to False for
                   self-signed certificates.

    Returns {"reachable": bool, "auth_ok": bool, "message": str}.
    """
    base_url = _build_registry_base_url(host, use_tls=use_tls)
    url = f"{base_url}/v2/"
    # httpx verify= only applies to HTTPS; for HTTP it is ignored
    verify = tls_verify if use_tls else False
    auth = (username, password) if username and password else None
    try:
        async with httpx.AsyncClient(
            timeout=10, verify=verify, follow_redirects=True
        ) as client:
            resp = await client.get(url, auth=auth)
        if resp.status_code in (200, 401):
            auth_ok = resp.status_code == 200 or (resp.status_code == 401 and not auth)
            return {
                "reachable": True,
                "auth_ok": auth_ok,
                "message": "Registry reachable"
                if auth_ok
                else "Authentication required",
            }
        return {
            "reachable": True,
            "auth_ok": False,
            "message": f"Unexpected status {resp.status_code}",
        }
    except httpx.ConnectError:
        return {"reachable": False, "auth_ok": False, "message": "Connection refused"}
    except Exception as exc:
        logger.warning("Registry connection test failed: %s", exc)
        return {"reachable": False, "auth_ok": False, "message": "Connection failed"}


# ── Browse external registry (Évolution 1) ───────────────────────────────────


async def browse_external_images(
    registry_id: str,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    List repositories available in an external registry using /v2/_catalog.

    Authentication and TLS settings are read from the saved registry entry.
    Returns a paginated dict compatible with the local PaginatedImages shape:
      { items, total, page, page_size, total_pages, error }

    Docker Hub does not expose /v2/_catalog publicly; for that host an empty
    result is returned with an explanatory error message.
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    normalized = _normalize_registry_host(host)
    if normalized in {"docker.io", "index.docker.io", "registry-1.docker.io"}:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": "Docker Hub does not expose a public catalog endpoint.",
        }

    base_url = _build_registry_base_url(host, use_tls=use_tls)
    verify = tls_verify if use_tls else False
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=30, verify=verify, follow_redirects=True
        ) as client:
            resp = await client.get(f"{base_url}/v2/_catalog?n=1000", auth=auth)
            resp.raise_for_status()
            repositories: list[str] = resp.json().get("repositories", [])
    except httpx.HTTPStatusError as exc:
        logger.warning("browse_external_images catalog error: %s", exc)
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": f"Registry returned HTTP {exc.response.status_code}",
        }
    except Exception as exc:
        logger.warning("browse_external_images connection error: %s", exc)
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": str(exc),
        }

    if search:
        repositories = [r for r in repositories if search.lower() in r.lower()]

    total = len(repositories)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_repos = repositories[start : start + page_size]

    async def _fetch_tags(repo: str) -> list[str]:
        try:
            async with httpx.AsyncClient(
                timeout=15, verify=verify, follow_redirects=True
            ) as client:
                r = await client.get(f"{base_url}/v2/{repo}/tags/list", auth=auth)
                if r.status_code == 200:
                    return r.json().get("tags") or []
        except Exception:
            pass
        return []

    tags_results = await asyncio.gather(*[_fetch_tags(repo) for repo in page_repos])

    items = [
        {"name": repo, "tags": tags, "tag_count": len(tags), "total_size": 0}
        for repo, tags in zip(page_repos, tags_results)
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "error": None,
    }


async def browse_external_tags(registry_id: str, repository: str) -> dict:
    """
    List tags for a specific repository in an external registry.
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

    base_url = _build_registry_base_url(host, use_tls=use_tls)
    verify = tls_verify if use_tls else False
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=15, verify=verify, follow_redirects=True
        ) as client:
            resp = await client.get(f"{base_url}/v2/{repository}/tags/list", auth=auth)
            resp.raise_for_status()
            tags = resp.json().get("tags") or []
    except Exception as exc:
        logger.warning("browse_external_tags error repo=%s: %s", repository, exc)
        tags = []

    return {"repository": repository, "tags": tags}


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
    """
    Copy an image between two docker:// registries using skopeo copy.

    Used by both export (local→external) and import (external→local) jobs.
    No intermediate OCI directory is needed.
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
    """
    Rewrite the image repository name for the destination registry.
    """
    leaf = img.split("/")[-1]
    if dest_folder:
        # Explicit folder: replace namespace, keep only leaf
        return f"{dest_folder}/{leaf}"
    if dest_username:
        # Docker Hub / username-scoped registry: leaf under username
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
    """
    Start an asynchronous export job (local → external) and return the job ID.

    Copies images from the local registry to an external registry via skopeo.
    The job dict carries direction="export" for display in the history list.
    """
    job_id = str(uuid.uuid4())
    registry = get_registry_by_id(dest_registry_id)
    if not registry:
        raise ValueError(f"Registry {dest_registry_id} not found")

    dest_host = registry["host"]
    dest_username = registry.get("username", "")
    dest_password = registry.get("password", "")
    dest_use_tls = registry.get("use_tls", True)
    dest_tls_verify_raw = registry.get("tls_verify", True)

    # FIX: derive the skopeo --dest-tls-verify flag from BOTH use_tls and
    # tls_verify. Previously only tls_verify was read, causing skopeo to
    # attempt HTTPS on plain-HTTP registries (use_tls=False was ignored).
    dest_tls_verify = _skopeo_tls_verify(dest_use_tls, dest_tls_verify_raw)

    # The local registry is the source; use its URL scheme to decide TLS.
    src_tls_verify = local_registry_url.startswith("https://")

    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "export",  # local → external
        "source": source_image,
        "source_registry_id": None,
        "dest_registry_id": dest_registry_id,
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "message": "Sync started",
        "error": None,
        "progress": 0,
        "images_total": 0,
        "images_done": 0,
    }

    async def _run() -> None:
        try:
            catalog_url = f"{local_registry_url}/v2/_catalog?n=1000"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(catalog_url)
                resp.raise_for_status()
                all_images = resp.json().get("repositories", [])

            images = (
                all_images
                if source_image == "(all)"
                else [i for i in all_images if source_image.split(":")[0] in i]
            )

            _sync_jobs[job_id]["images_total"] = len(images)

            errors: list[str] = []
            for img in images:
                tags_url = f"{local_registry_url}/v2/{img}/tags/list"
                async with httpx.AsyncClient(timeout=30) as client:
                    tr = await client.get(tags_url)
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
                        logger.warning(
                            "Export job %s: failed %s:%s — %s", job_id, img, tag, msg
                        )

                _sync_jobs[job_id]["images_done"] += 1
                _sync_jobs[job_id]["progress"] = int(
                    100 * _sync_jobs[job_id]["images_done"] / max(len(images), 1)
                )

            _sync_jobs[job_id]["status"] = "partial" if errors else "done"
            _sync_jobs[job_id]["message"] = (
                f"Completed with {len(errors)} error(s)" if errors else "Sync complete"
            )
            _sync_jobs[job_id]["error"] = "\n".join(errors) if errors else None
        except Exception as exc:
            logger.exception("Export job %s failed: %s", job_id, exc)
            _sync_jobs[job_id]["status"] = "error"
            _sync_jobs[job_id]["error"] = str(exc)
            _sync_jobs[job_id]["message"] = f"Sync failed: {exc}"
        finally:
            _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            _sync_jobs[job_id]["progress"] = 100

    import asyncio as _asyncio

    _asyncio.create_task(_run())
    return job_id


async def run_import_job(
    source_registry_id: str,
    source_image: str,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> str:
    """
    Start an asynchronous import job (external → local) and return the job ID.

    Mirrors run_sync_job() with source and destination inverted:
      - source: docker://<external_host>/<image>:<tag>  with external credentials
      - dest:   docker://<REGISTRY_HOST>/<dest_folder>/<leaf>:<tag>  (local registry)

    The leaf image name (last path segment) is preserved; dest_folder is prepended.
    The job dict carries direction="import" for display in the history list.
    """
    job_id = str(uuid.uuid4())
    registry = get_registry_by_id(source_registry_id)
    if not registry:
        raise ValueError(f"Registry {source_registry_id} not found")

    src_host = registry["host"]
    src_username = registry.get("username", "")
    src_password = registry.get("password", "")
    src_use_tls = registry.get("use_tls", True)
    src_tls_verify_raw = registry.get("tls_verify", True)

    # FIX: same fix as run_sync_job — derive --src-tls-verify from both
    # use_tls and tls_verify so plain-HTTP source registries work correctly.
    src_tls_verify = _skopeo_tls_verify(src_use_tls, src_tls_verify_raw)

    dest_tls_verify = local_registry_url.startswith("https://")

    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "import",  # external → local
        "source": source_image,
        "source_registry_id": source_registry_id,
        "dest_registry_id": None,
        "dest_folder": dest_folder,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "message": "Import started",
        "error": None,
        "progress": 0,
        "images_total": 0,
        "images_done": 0,
    }

    async def _run() -> None:
        try:
            # FIX: pass use_tls to _build_registry_base_url so the catalog
            # HTTP request also uses http:// for plain-HTTP source registries.
            base_url = _build_registry_base_url(src_host, use_tls=src_use_tls)
            auth = (
                (src_username, src_password) if src_username and src_password else None
            )
            # httpx verify= is only meaningful for HTTPS; False for HTTP.
            httpx_verify = src_tls_verify_raw if src_use_tls else False

            # Resolve the list of repositories to import
            if source_image == "(all)":
                async with httpx.AsyncClient(
                    timeout=30, verify=httpx_verify, follow_redirects=True
                ) as client:
                    resp = await client.get(f"{base_url}/v2/_catalog?n=1000", auth=auth)
                    resp.raise_for_status()
                    images: list[str] = resp.json().get("repositories", [])
            else:
                images = [source_image.split(":")[0]]

            _sync_jobs[job_id]["images_total"] = len(images)

            errors: list[str] = []
            for img in images:
                # Use the specified tag when source_image carries one
                if source_image != "(all)" and ":" in source_image:
                    tags = [source_image.split(":", 1)[1]]
                else:
                    async with httpx.AsyncClient(
                        timeout=15, verify=httpx_verify, follow_redirects=True
                    ) as client:
                        tr = await client.get(
                            f"{base_url}/v2/{img}/tags/list", auth=auth
                        )
                        tags = (
                            tr.json().get("tags") or [] if tr.status_code == 200 else []
                        )

                for tag in tags:
                    src_ref = (
                        f"docker://{_normalize_registry_host(src_host)}/{img}:{tag}"
                    )

                    # Keep the leaf image name; apply dest_folder prefix if given
                    leaf = img.split("/")[-1]
                    dest_image = f"{dest_folder}/{leaf}" if dest_folder else leaf
                    dest_ref = f"docker://{REGISTRY_HOST}/{dest_image}:{tag}"

                    logger.info("Import job %s: %s -> %s", job_id, src_ref, dest_ref)

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
                        logger.warning(
                            "Import job %s: failed %s:%s — %s", job_id, img, tag, msg
                        )

                _sync_jobs[job_id]["images_done"] += 1
                _sync_jobs[job_id]["progress"] = int(
                    100 * _sync_jobs[job_id]["images_done"] / max(len(images), 1)
                )

            _sync_jobs[job_id]["status"] = "partial" if errors else "done"
            _sync_jobs[job_id]["message"] = (
                f"Completed with {len(errors)} error(s)"
                if errors
                else "Import complete"
            )
            _sync_jobs[job_id]["error"] = "\n".join(errors) if errors else None
        except Exception as exc:
            logger.exception("Import job %s failed: %s", job_id, exc)
            _sync_jobs[job_id]["status"] = "error"
            _sync_jobs[job_id]["error"] = str(exc)
            _sync_jobs[job_id]["message"] = f"Import failed: {exc}"
        finally:
            _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            _sync_jobs[job_id]["progress"] = 100

    import asyncio as _asyncio

    _asyncio.create_task(_run())
    return job_id
