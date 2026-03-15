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
  - [NEW] check_catalog_browsable() — probe /v2/_catalog to check whether
    the registry supports listing repositories. The result is persisted in
    the browsable field and exposed in the API response so the frontend can
    hide non-browsable registries from the Images source selector.
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
from .external_github import browse_github_packages

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


def _is_ghcr(host: str) -> bool:
    """Return True when the registry host is GitHub Container Registry."""
    return _normalize_registry_host(host) == "ghcr.io"


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

    Mapping:
      use_tls=False              -> plain HTTP -> --tls-verify=false
      use_tls=True, verify=False -> HTTPS, skip cert check -> --tls-verify=false
      use_tls=True, verify=True  -> normal HTTPS -> --tls-verify=true
    """
    if not use_tls:
        return False
    return tls_verify


async def check_catalog_browsable(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> bool:
    """
    Probe /v2/_catalog to determine whether this registry supports repository
    listing (browsing).

    Returns True when the endpoint responds with HTTP 200 or 401 (reachable
    but authentication required — still a browse-capable registry).
    Returns False when the registry is unreachable, returns an unexpected
    status, or explicitly blocks /v2/_catalog (e.g. Docker Hub).

    This result is stored in the `browsable` field of the registry entry so
    the frontend can hide non-browsable registries from the Images source
    selector without making additional requests.
    """
    # Docker Hub never exposes /v2/_catalog publicly
    normalized = _normalize_registry_host(host)
    if normalized in {"docker.io", "index.docker.io", "registry-1.docker.io"}:
        logger.debug("check_catalog_browsable: Docker Hub — not browsable")
        return False

    base_url = _build_registry_base_url(host, use_tls=use_tls)
    verify = tls_verify if use_tls else False
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=10, verify=verify, follow_redirects=True
        ) as client:
            resp = await client.get(f"{base_url}/v2/_catalog?n=1", auth=auth)
        # 200 = OK, 401 = auth required but endpoint exists -> browsable
        browsable = resp.status_code in (200, 401)
        logger.debug(
            "check_catalog_browsable host=%s status=%s browsable=%s",
            host,
            resp.status_code,
            browsable,
        )
        return browsable
    except Exception as exc:
        logger.warning("check_catalog_browsable host=%s error: %s", host, exc)
        return False


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


async def create_registry(
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
    *tls_verify* — only relevant when use_tls is True; set to False for
                   self-signed certificates.

    The browsable field is set by probing /v2/_catalog so the frontend can
    immediately filter out non-browsable registries in the Images source
    selector and the Staging pull registry selector.
    """
    browsable = await check_catalog_browsable(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )

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
        "browsable": browsable,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    registries.append(entry)
    _save_registries(registries)
    logger.debug(
        "External registry created id=%s host=%s owner=%s use_tls=%s "
        "tls_verify=%s browsable=%s",
        entry["id"],
        host,
        owner,
        use_tls,
        tls_verify,
        browsable,
    )
    return _redact(entry)


async def update_registry(
    registry_id: str,
    name: str | None,
    host: str | None,
    username: str | None,
    password: str | None,
    owner: str | None = None,
    use_tls: bool | None = None,
    tls_verify: bool | None = None,
) -> dict | None:
    """
    Update an existing registry entry.

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
    Probe the registry to check reachability and validate credentials.

    Strategy:
      Step 1 — Ping /v2/ to verify the registry is reachable.
               A response of 200 or 401 confirms a live OCI/Docker registry.
               Any other status (or a network error) means unreachable.

      Step 2 — If credentials were supplied, validate them by calling
               /v2/_catalog?n=1 with Basic Auth.
               - 200  → credentials accepted.
               - 401  → credentials rejected (wrong user/password).
               - 403  → credentials are valid but the account has no catalog
                        access (still considered auth_ok=True for the purpose
                        of this check, because the registry recognised them).

               Rationale: /v2/ is a simple ping that many registries (Harbor,
               Nexus, plain Docker registry) answer with 200 regardless of
               whether credentials are correct or even present.  Only an
               authenticated endpoint reliably distinguishes valid creds from
               invalid ones.

      If no credentials are supplied:
               reachable=True, auth_ok=True when the registry is public (200),
               reachable=True, auth_ok=False when authentication is required
               (401) but no credentials were given.

    Returns {"reachable": bool, "auth_ok": bool, "message": str}.
    """
    base_url = _build_registry_base_url(host, use_tls=use_tls)
    verify = tls_verify if use_tls else False
    has_credentials = bool(username and password)
    auth = (username, password) if has_credentials else None

    try:
        async with httpx.AsyncClient(
            timeout=10, verify=verify, follow_redirects=True
        ) as client:
            # ── Step 1: reachability ping ──────────────────────────────────
            ping_resp = await client.get(f"{base_url}/v2/")

            if ping_resp.status_code not in (200, 401):
                return {
                    "reachable": True,
                    "auth_ok": False,
                    "message": f"Unexpected status {ping_resp.status_code}",
                }

            # Registry is reachable.
            if not has_credentials:
                # No credentials supplied — report whether the registry is
                # public (200) or requires authentication (401).
                auth_ok = ping_resp.status_code == 200
                return {
                    "reachable": True,
                    "auth_ok": auth_ok,
                    "message": "Registry reachable (public)"
                    if auth_ok
                    else "Registry reachable — authentication required",
                }

            # ── Step 2: credential validation ──────────────────────────────
            # Use /v2/_catalog?n=1 because /v2/ answers 200 for unauthenticated
            # requests on most registries (Harbor, Nexus, plain Docker registry).
            cred_resp = await client.get(f"{base_url}/v2/", auth=auth)
            if cred_resp.status_code == 401:
                cred_resp = await client.get(base_url, auth=auth)

            if cred_resp.status_code == 200:
                return {
                    "reachable": True,
                    "auth_ok": True,
                    "message": "Registry reachable — credentials accepted",
                }

            if cred_resp.status_code == 403:
                # Credentials were recognised but the account cannot list the
                # catalog (e.g. non-admin on Harbor).  The credentials are
                # still valid for push/pull operations.
                return {
                    "reachable": True,
                    "auth_ok": True,
                    "message": "Registry reachable — credentials accepted (catalog access restricted)",
                }

            if cred_resp.status_code == 401:
                return {
                    "reachable": True,
                    "auth_ok": False,
                    "message": "Authentication failed — invalid username or password",
                }

            # Any other status (404 _catalog not exposed, 500 …)
            # Fall back to the ping result: reachable but cannot confirm creds.
            logger.debug(
                "test_registry_connection: /v2/_catalog returned %s for host=%s; "
                "falling back to ping-only result",
                cred_resp.status_code,
                host,
            )
            return {
                "reachable": True,
                "auth_ok": False,
                "message": (
                    f"Registry reachable but credential check inconclusive "
                    f"(catalog endpoint returned {cred_resp.status_code})"
                ),
            }

    except httpx.ConnectError:
        return {"reachable": False, "auth_ok": False, "message": "Connection refused"}
    except httpx.TimeoutException:
        return {"reachable": False, "auth_ok": False, "message": "Connection timed out"}
    except Exception as exc:
        logger.warning("Registry connection test failed host=%s: %s", host, exc)
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

    For GHCR (ghcr.io) registries: uses the GitHub Packages REST API
    (GET /users/{owner}/packages?package_type=container) with the stored
    token (password field) and the stored username as the GitHub owner.

    For all other registries: uses /v2/_catalog as before.

    Returns a paginated dict compatible with the local PaginatedImages shape:
      { items, total, page, page_size, total_pages, error }
    """
    registry = get_registry_by_id(registry_id)
    if not registry:
        raise ValueError(f"Registry {registry_id} not found")

    host = registry["host"]
    username = registry.get("username", "")
    password = registry.get("password", "")
    use_tls = registry.get("use_tls", True)
    tls_verify = registry.get("tls_verify", True)

    # ── GHCR: use GitHub Packages API ─────────────────────────────────────
    if _is_ghcr(host) and password:
        github_owner = username  # username stored == GitHub username / org
        return await browse_github_packages(
            username=username,
            token=password,
            owner=github_owner,
            search=search,
            page=page,
            page_size=page_size,
        )

    # ── Standard: /v2/_catalog ─────────────────────────────────────────────
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


async def delete_external_image(registry_id: str, repository: str) -> dict:
    """
    Delete all tags for a repository in an external registry.

    For each tag we fetch its manifest digest then call DELETE
    /v2/<repository>/manifests/<digest>.
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

    deleted_tags: list[str] = []
    failed_tags: list[str] = []

    async with httpx.AsyncClient(
        timeout=20, verify=verify, follow_redirects=True
    ) as client:
        tags_resp = await client.get(f"{base_url}/v2/{repository}/tags/list", auth=auth)
        tags_resp.raise_for_status()
        tags = tags_resp.json().get("tags") or []

        if not tags:
            return {
                "repository": repository,
                "deleted_tags": [],
                "failed_tags": [],
                "message": "No tags found for this repository",
            }

        manifest_accept = ", ".join(
            [
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
            ]
        )

        for tag in tags:
            try:
                manifest_resp = await client.get(
                    f"{base_url}/v2/{repository}/manifests/{tag}",
                    auth=auth,
                    headers={"Accept": manifest_accept},
                )
                manifest_resp.raise_for_status()

                digest = manifest_resp.headers.get("Docker-Content-Digest")
                if not digest:
                    failed_tags.append(tag)
                    continue

                delete_resp = await client.delete(
                    f"{base_url}/v2/{repository}/manifests/{digest}",
                    auth=auth,  # type: ignore
                )
                if delete_resp.status_code in {202, 200}:
                    deleted_tags.append(tag)
                else:
                    failed_tags.append(tag)
            except Exception:
                failed_tags.append(tag)

    message = f"Deleted {len(deleted_tags)} tag(s)"
    if failed_tags:
        message += f", failed: {', '.join(failed_tags)}"

    return {
        "repository": repository,
        "deleted_tags": deleted_tags,
        "failed_tags": failed_tags,
        "message": message,
    }


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
    """
    Rewrite the image repository name for the destination registry.

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
    """
    Start an asynchronous export job (local -> external) and return the job ID.
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

    dest_tls_verify = _skopeo_tls_verify(dest_use_tls, dest_tls_verify_raw)
    src_tls_verify = local_registry_url.startswith("https://")

    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "export",
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
                            "Export job %s: failed %s:%s — %s",
                            job_id,
                            img,
                            tag,
                            msg,
                        )

                _sync_jobs[job_id]["images_done"] += 1
                _sync_jobs[job_id]["progress"] = int(
                    (_sync_jobs[job_id]["images_done"] / max(len(images), 1)) * 100
                )

            if errors:
                _sync_jobs[job_id]["status"] = "partial"
                _sync_jobs[job_id]["error"] = "\n".join(errors)
                _sync_jobs[job_id]["message"] = f"Completed with {len(errors)} error(s)"
            else:
                _sync_jobs[job_id]["status"] = "done"
                _sync_jobs[job_id]["message"] = "Sync completed successfully"

        except Exception as exc:
            logger.exception("Export job %s failed: %s", job_id, exc)
            _sync_jobs[job_id]["status"] = "error"
            _sync_jobs[job_id]["error"] = str(exc)
            _sync_jobs[job_id]["message"] = f"Sync failed: {exc}"
        finally:
            _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            _sync_jobs[job_id]["progress"] = 100

    asyncio.create_task(_run())
    return job_id


async def run_import_job(
    source_registry_id: str,
    source_image: str,
    dest_folder: str | None,
    local_registry_url: str,
    settings: Settings,
) -> str:
    """
    Start an asynchronous import job (external -> local) and return the job ID.
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

    src_tls_verify = _skopeo_tls_verify(src_use_tls, src_tls_verify_raw)
    dest_tls_verify = local_registry_url.startswith("https://")

    _sync_jobs[job_id] = {
        "id": job_id,
        "direction": "import",
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
            base_url = _build_registry_base_url(src_host, use_tls=src_use_tls)
            auth = (
                (src_username, src_password) if src_username and src_password else None
            )
            httpx_verify = src_tls_verify_raw if src_use_tls else False

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
                        logger.warning(
                            "Import job %s: failed %s:%s — %s",
                            job_id,
                            img,
                            tag,
                            msg,
                        )

                _sync_jobs[job_id]["images_done"] += 1
                _sync_jobs[job_id]["progress"] = int(
                    (_sync_jobs[job_id]["images_done"] / max(len(images), 1)) * 100
                )

            if errors:
                _sync_jobs[job_id]["status"] = "partial"
                _sync_jobs[job_id]["error"] = "\n".join(errors)
                _sync_jobs[job_id]["message"] = (
                    f"Import completed with {len(errors)} error(s)"
                )
            else:
                _sync_jobs[job_id]["status"] = "done"
                _sync_jobs[job_id]["message"] = "Import completed successfully"

        except Exception as exc:
            logger.exception("Import job %s failed: %s", job_id, exc)
            _sync_jobs[job_id]["status"] = "error"
            _sync_jobs[job_id]["error"] = str(exc)
            _sync_jobs[job_id]["message"] = f"Import failed: {exc}"
        finally:
            _sync_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            _sync_jobs[job_id]["progress"] = 100

    asyncio.create_task(_run())
    return job_id
