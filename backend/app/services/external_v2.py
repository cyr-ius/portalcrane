"""Portalcrane - OCI / Docker Distribution V2 Registry Service.

Provides browse, tag listing, connectivity test, catalog check, and
manifest-based delete operations for any registry that implements the
OCI Distribution Specification v1 / Docker Registry HTTP API V2.

This module is the V2-standard counterpart of external_github.py and
external_dockerhub.py: it is called by external_registry_service.py
whenever the target registry host is neither ghcr.io nor docker.io.

Supported registries (non-exhaustive):
  - Harbor (VMware)
  - Quay.io (Red Hat)
  - GitLab Container Registry
  - Nexus Repository (Sonatype)
  - Amazon ECR (public endpoint)
  - Azure Container Registry (ACR)
  - Google Artifact Registry (GAR)
  - Any self-hosted Docker Distribution (registry:2) instance

All public functions receive already-resolved connection parameters
(host, username, password, use_tls, tls_verify) so this module has
no dependency on the JSON registry store.
"""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Accept header covering all OCI and Docker manifest media types ────────────

_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _build_base_url(host: str, use_tls: bool = True) -> str:
    """Build the base HTTPS/HTTP URL for a registry host.

    When the host already contains a scheme (http:// or https://) it is
    preserved as-is so callers that stored the full URL are unaffected.
    When *use_tls* is False the scheme is http://, otherwise https://.

    Args:
        host:    Registry hostname, optionally prefixed with a scheme.
        use_tls: When True (default) use https://, otherwise http://.

    Returns:
        Base URL string without trailing slash.
    """
    if "://" in host:
        return host.rstrip("/")
    scheme = "https" if use_tls else "http"
    return f"{scheme}://{host}"


def _httpx_verify(use_tls: bool, tls_verify: bool) -> bool:
    """Derive the httpx *verify* parameter from TLS settings.

    Mapping:
      use_tls=False               -> plain HTTP, verify irrelevant -> False
      use_tls=True, verify=False  -> HTTPS without cert check      -> False
      use_tls=True, verify=True   -> standard HTTPS verification   -> True

    Args:
        use_tls:    Whether HTTPS is used at all.
        tls_verify: Whether TLS certificate verification is enforced.

    Returns:
        Boolean suitable for httpx AsyncClient(verify=...).
    """
    if not use_tls:
        return False
    return tls_verify


# ── Connectivity ──────────────────────────────────────────────────────────────


async def test_v2_connection(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict[str, Any]:
    """Probe a V2-compatible registry to check reachability and credentials.

    Strategy:
      Step 1 — GET /v2/ to verify the registry speaks the V2 protocol.
               HTTP 200 or 401 confirms a live OCI/Docker registry.
      Step 2 — When credentials are provided, re-request /v2/ with Basic
               Auth to validate them.  HTTP 200 = accepted; 401 = rejected;
               403 = accepted but catalog access is restricted.

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username (may be empty for anonymous access).
        password:   Registry password or access token.
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        Dict with keys: reachable (bool), auth_ok (bool), message (str).
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
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

            if not has_credentials:
                auth_ok = ping_resp.status_code == 200
                return {
                    "reachable": True,
                    "auth_ok": auth_ok,
                    "message": (
                        "Registry reachable (public)"
                        if auth_ok
                        else "Registry reachable — authentication required"
                    ),
                }

            # ── Step 2: credential validation ──────────────────────────────
            cred_resp = await client.get(f"{base_url}/v2/", auth=auth)

            if cred_resp.status_code == 200:
                return {
                    "reachable": True,
                    "auth_ok": True,
                    "message": "Registry reachable — credentials accepted",
                }

            if cred_resp.status_code == 403:
                return {
                    "reachable": True,
                    "auth_ok": True,
                    "message": (
                        "Registry reachable — credentials accepted"
                        " (catalog access restricted)"
                    ),
                }

            if cred_resp.status_code == 401:
                return {
                    "reachable": True,
                    "auth_ok": False,
                    "message": "Authentication failed — invalid username or password",
                }

            logger.debug(
                "test_v2_connection: /v2/ returned %s for host=%s; "
                "falling back to ping-only result",
                cred_resp.status_code,
                host,
            )
            return {
                "reachable": True,
                "auth_ok": False,
                "message": (
                    f"Registry reachable but credential check inconclusive"
                    f" (status {cred_resp.status_code})"
                ),
            }

    except httpx.ConnectError:
        return {"reachable": False, "auth_ok": False, "message": "Connection refused"}
    except httpx.TimeoutException:
        return {"reachable": False, "auth_ok": False, "message": "Connection timed out"}
    except Exception as exc:
        logger.warning("test_v2_connection failed host=%s: %s", host, exc)
        return {"reachable": False, "auth_ok": False, "message": "Connection failed"}


async def check_v2_catalog(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> bool:
    """Probe /v2/_catalog to determine whether this registry supports listing.

    HTTP 200 (OK) or 401 (auth required but endpoint exists) are both
    treated as "browsable" because the endpoint is reachable.
    HTTP 403, 404, or network errors indicate the registry does not expose
    its catalog and browsing will not be possible.

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username (may be empty).
        password:   Registry password or access token.
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        True when the registry exposes /v2/_catalog, False otherwise.
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=10, verify=verify, follow_redirects=True
        ) as client:
            resp = await client.get(f"{base_url}/v2/_catalog?n=1", auth=auth)
        browsable = resp.status_code in (200, 401)
        logger.debug(
            "check_v2_catalog host=%s status=%s browsable=%s",
            host,
            resp.status_code,
            browsable,
        )
        return browsable
    except Exception as exc:
        logger.warning("check_v2_catalog host=%s error: %s", host, exc)
        return False


# ── Browse repositories ───────────────────────────────────────────────────────


async def browse_v2_repositories(
    host: str,
    username: str,
    password: str,
    use_tls: bool = True,
    tls_verify: bool = True,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """List repositories available in a V2-compatible registry via /v2/_catalog.

    The catalog endpoint is fetched with n=1000 (maximum supported by most
    registries).  Pagination and optional keyword filtering are applied
    client-side because the V2 spec does not mandate server-side search.

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username (may be empty for public registries).
        password:   Registry password or access token.
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).
        search:     Optional substring filter applied to repository names.
        page:       1-based page number (default 1).
        page_size:  Number of items per page (default 20).

    Returns:
        Paginated dict compatible with ExternalPaginatedImages:
        { items, total, page, page_size, total_pages, error? }
        Each item has at least a ``name`` key containing the repository path.
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=20, verify=verify, follow_redirects=True
        ) as client:
            resp = await client.get(f"{base_url}/v2/_catalog?n=1000", auth=auth)
            resp.raise_for_status()
            repositories: list[str] = resp.json().get("repositories") or []
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "browse_v2_repositories: HTTP %s for host=%s",
            exc.response.status_code,
            host,
        )
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": f"Registry returned HTTP {exc.response.status_code}",
        }
    except Exception as exc:
        logger.warning("browse_v2_repositories: error host=%s: %s", host, exc)
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": str(exc),
        }

    # Apply optional client-side keyword filter
    if search:
        repositories = [r for r in repositories if search.lower() in r.lower()]

    total = len(repositories)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_repos = repositories[start : start + page_size]

    # Fetch tags via GitHub API for each package
    async def _fetch_github_tags(repo: str) -> list[str]:
        """Fetch versions/tags for a GitHub package."""
        try:
            return await browse_v2_tags(
                host, username, password, repo, use_tls, tls_verify
            )
        except Exception:
            pass
        return []

    tags_results = await asyncio.gather(*[_fetch_github_tags(r) for r in page_repos])

    items = [
        {
            "name": repo,
            "tags": tags,
            "tag_count": len(tags),
            "total_size": 0,
        }
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


# ── Tag listing ───────────────────────────────────────────────────────────────


async def browse_v2_tags(
    host: str,
    username: str,
    password: str,
    repository: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> list[str]:
    """List all tags for a repository in a V2-compatible registry.

    Uses GET /v2/{repository}/tags/list as specified by the OCI Distribution
    Specification.

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username (may be empty).
        password:   Registry password or access token.
        repository: Repository path, e.g. "myorg/myimage".
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        Dict with keys: repository (str), tags (list[str]).
        The tags list is empty when the repository does not exist or
        an error occurs (errors are logged, not raised).
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=15, verify=verify, follow_redirects=True
        ) as client:
            resp = await client.get(f"{base_url}/v2/{repository}/tags/list", auth=auth)
            resp.raise_for_status()
            tags: list[str] = resp.json().get("tags") or []
    except Exception as exc:
        logger.warning(
            "browse_v2_tags error host=%s repo=%s: %s", host, repository, exc
        )
        tags = []

    return tags


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete_v2_image(
    host: str,
    username: str,
    password: str,
    repository: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict[str, Any]:
    """Delete all tags of a repository in a V2-compatible registry.

    For each tag the manifest digest is resolved via
    GET /v2/{repository}/manifests/{tag} (reading the Docker-Content-Digest
    response header), then the manifest is deleted via
    DELETE /v2/{repository}/manifests/{digest}.

    The registry must have ``delete`` enabled in its configuration
    (REGISTRY_STORAGE_DELETE_ENABLED=true for Docker Distribution).

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username.
        password:   Registry password or access token.
        repository: Repository path, e.g. "myorg/myimage".
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        Dict with keys: deleted_tags (list), failed_tags (list), message (str).
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
    auth = (username, password) if username and password else None

    deleted_tags: list[str] = []
    failed_tags: list[str] = []

    try:
        async with httpx.AsyncClient(
            timeout=20, verify=verify, follow_redirects=True
        ) as client:
            # Retrieve tag list first
            tags_resp = await client.get(
                f"{base_url}/v2/{repository}/tags/list", auth=auth
            )
            tags_resp.raise_for_status()
            tags: list[str] = tags_resp.json().get("tags") or []

            if not tags:
                return {
                    "repository": repository,
                    "deleted_tags": [],
                    "failed_tags": [],
                    "message": "No tags found for this repository",
                }

            # For each tag: resolve digest then delete by digest
            for tag in tags:
                try:
                    manifest_resp = await client.get(
                        f"{base_url}/v2/{repository}/manifests/{tag}",
                        auth=auth,
                        headers={"Accept": _MANIFEST_ACCEPT},
                    )
                    manifest_resp.raise_for_status()

                    digest = manifest_resp.headers.get("Docker-Content-Digest")
                    if not digest:
                        logger.warning(
                            "delete_v2_image: no digest header for %s:%s",
                            repository,
                            tag,
                        )
                        failed_tags.append(tag)
                        continue

                    delete_resp = await client.delete(
                        f"{base_url}/v2/{repository}/manifests/{digest}",
                        auth=auth,  # type: ignore[arg-type]
                    )
                    if delete_resp.status_code in (200, 202):
                        deleted_tags.append(tag)
                    else:
                        logger.warning(
                            "delete_v2_image: DELETE returned %s for %s:%s",
                            delete_resp.status_code,
                            repository,
                            tag,
                        )
                        failed_tags.append(tag)
                except Exception as exc:
                    logger.warning(
                        "delete_v2_image: error deleting %s:%s — %s",
                        repository,
                        tag,
                        exc,
                    )
                    failed_tags.append(tag)

    except Exception as exc:
        logger.warning("delete_v2_image: error repo=%s: %s", repository, exc)
        return {
            "deleted_tags": [],
            "failed_tags": [repository],
            "message": "Delete v2 image error, please view log",
        }

    return {
        "deleted_tags": deleted_tags,
        "failed_tags": failed_tags,
        "message": (
            f"Deleted {len(deleted_tags)} tag(s)"
            + (f", {len(failed_tags)} failed" if failed_tags else "")
        ),
    }


async def get_v2_tags_for_import(
    host: str,
    username: str,
    password: str,
    repository: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> list[str]:
    """Retrieve tag names for a V2 repository, used by import jobs.

    Thin wrapper around browse_v2_tags that guarantees a list[str] return
    suitable for use inside run_import_job() tag resolution loops.

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username (may be empty).
        password:   Registry password or access token.
        repository: Repository path, e.g. "myorg/myimage".
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        List of tag name strings (may be empty on error).
    """
    tags = await browse_v2_tags(
        host=host,
        username=username,
        password=password,
        repository=repository,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )
    return tags or []


# ── Tag detail (manifest + config blob) ──────────────────────────────────────


async def get_v2_tag_detail(
    host: str,
    username: str,
    password: str,
    repository: str,
    tag: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict[str, Any]:
    """Fetch detailed metadata for a specific tag in a V2 registry.

    Resolves the image manifest and then the config blob to extract
    architecture, OS, creation date, labels, environment variables,
    exposed ports, entrypoint, cmd and layer list.

    This mirrors RegistryService.get_manifest / get_image_config for the
    local registry but targets an arbitrary external V2 endpoint.

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username (may be empty).
        password:   Registry password or access token.
        repository: Repository path, e.g. "myorg/myimage".
        tag:        Tag name, e.g. "latest".
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        Dict matching the ImageDetail schema used by the local registry router:
        name, tag, digest, size, created, architecture, os, layers,
        labels, env, cmd, entrypoint, exposed_ports.
        Returns an empty dict when the tag is not found.
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=20, verify=verify, follow_redirects=True
        ) as client:
            # ── Step 1: fetch manifest ─────────────────────────────────────
            manifest_resp = await client.get(
                f"{base_url}/v2/{repository}/manifests/{tag}",
                auth=auth,
                headers={"Accept": _MANIFEST_ACCEPT},
            )
            if manifest_resp.status_code == 404:
                return {}
            manifest_resp.raise_for_status()

            digest = manifest_resp.headers.get("Docker-Content-Digest", "")
            manifest: dict[str, Any] = manifest_resp.json()

            # Handle manifest list (multi-arch): resolve first platform manifest
            media_type = manifest.get("mediaType", "")
            if media_type in (
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.oci.image.index.v1+json",
            ):
                sub_manifests = manifest.get("manifests", [])
                if sub_manifests:
                    sub_digest = sub_manifests[0]["digest"]
                    sub_resp = await client.get(
                        f"{base_url}/v2/{repository}/manifests/{sub_digest}",
                        auth=auth,
                        headers={"Accept": _MANIFEST_ACCEPT},
                    )
                    sub_resp.raise_for_status()
                    manifest = sub_resp.json()

            layers: list[dict[str, Any]] = manifest.get("layers", [])
            total_size: int = sum(int(layer.get("size", 0)) for layer in layers)

            # ── Step 2: fetch config blob ──────────────────────────────────
            config_digest: str = manifest.get("config", {}).get("digest", "")
            config: dict[str, Any] = {}
            if config_digest:
                blob_resp = await client.get(
                    f"{base_url}/v2/{repository}/blobs/{config_digest}",
                    auth=auth,
                )
                if blob_resp.status_code == 200:
                    config = blob_resp.json()

            container_config: dict[str, Any] = config.get(
                "config", config.get("container_config", {})
            )

            return {
                "name": repository,
                "tag": tag,
                "digest": digest,
                "size": total_size,
                "created": str(config.get("created", "")),
                "architecture": str(config.get("architecture", "")),
                "os": str(config.get("os", "")),
                "layers": layers,
                "labels": container_config.get("Labels", {}) or {},
                "env": container_config.get("Env", []) or [],
                "cmd": container_config.get("Cmd", []) or [],
                "entrypoint": container_config.get("Entrypoint", []) or [],
                "exposed_ports": container_config.get("ExposedPorts", {}) or {},
            }

    except Exception as exc:
        logger.warning(
            "get_v2_tag_detail error host=%s repo=%s tag=%s: %s",
            host,
            repository,
            tag,
            exc,
        )
        return {}


# ── Single-tag delete ─────────────────────────────────────────────────────────


async def delete_v2_tag(
    host: str,
    username: str,
    password: str,
    repository: str,
    tag: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict[str, Any]:
    """Delete a single tag from a V2 registry by resolving its manifest digest.

    The registry must have delete enabled
    (REGISTRY_STORAGE_DELETE_ENABLED=true for Docker Distribution).

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username.
        password:   Registry password or access token.
        repository: Repository path, e.g. "myorg/myimage".
        tag:        Tag name to delete, e.g. "v1.0.0".
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        Dict with keys: success (bool), message (str).
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=20, verify=verify, follow_redirects=True
        ) as client:
            # Resolve digest from tag
            manifest_resp = await client.get(
                f"{base_url}/v2/{repository}/manifests/{tag}",
                auth=auth,
                headers={"Accept": _MANIFEST_ACCEPT},
            )
            if manifest_resp.status_code == 404:
                return {"success": False, "message": f"Tag '{tag}' not found"}
            manifest_resp.raise_for_status()

            digest = manifest_resp.headers.get("Docker-Content-Digest")
            if not digest:
                return {
                    "success": False,
                    "message": "Registry did not return a Docker-Content-Digest header",
                }

            # Delete by digest
            delete_resp = await client.delete(
                f"{base_url}/v2/{repository}/manifests/{digest}",
                auth=auth,  # type: ignore[arg-type]
            )
            if delete_resp.status_code in (200, 202):
                return {"success": True, "message": f"Tag '{tag}' deleted"}

            return {
                "success": False,
                "message": f"Registry returned HTTP {delete_resp.status_code}",
            }

    except Exception as exc:
        logger.warning(
            "delete_v2_tag error host=%s repo=%s tag=%s: %s",
            host,
            repository,
            tag,
            exc,
        )
        return {"success": False, "message": str(exc)}


# ── Add tag (manifest copy) ───────────────────────────────────────────────────


async def add_v2_tag(
    host: str,
    username: str,
    password: str,
    repository: str,
    source_tag: str,
    new_tag: str,
    use_tls: bool = True,
    tls_verify: bool = True,
) -> dict[str, Any]:
    """Create a new tag by copying the manifest of an existing tag.

    Implements a client-side retag: the manifest of *source_tag* is fetched
    and then PUT under *new_tag*.  No data transfer is involved; only the
    manifest reference changes.

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username.
        password:   Registry password or access token.
        repository: Repository path, e.g. "myorg/myimage".
        source_tag: Existing tag whose manifest will be copied.
        new_tag:    New tag name to create.
        use_tls:    Use HTTPS (default True).
        tls_verify: Enforce TLS certificate validation (default True).

    Returns:
        Dict with keys: success (bool), message (str).
    """
    base_url = _build_base_url(host, use_tls)
    verify = _httpx_verify(use_tls, tls_verify)
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(
            timeout=20, verify=verify, follow_redirects=True
        ) as client:
            # Fetch source manifest (raw bytes to preserve exact JSON)
            manifest_resp = await client.get(
                f"{base_url}/v2/{repository}/manifests/{source_tag}",
                auth=auth,
                headers={"Accept": _MANIFEST_ACCEPT},
            )
            if manifest_resp.status_code == 404:
                return {
                    "success": False,
                    "message": f"Source tag '{source_tag}' not found",
                }
            manifest_resp.raise_for_status()

            content_type = manifest_resp.headers.get(
                "Content-Type",
                "application/vnd.docker.distribution.manifest.v2+json",
            )
            # Use the raw response body to avoid JSON re-serialisation drift
            raw_manifest = manifest_resp.content

            # PUT manifest under new tag
            put_resp = await client.put(
                f"{base_url}/v2/{repository}/manifests/{new_tag}",
                auth=auth,  # type: ignore[arg-type]
                content=raw_manifest,
                headers={"Content-Type": content_type},
            )
            if put_resp.status_code in (200, 201):
                return {
                    "success": True,
                    "message": f"Tag '{new_tag}' created from '{source_tag}'",
                }

            return {
                "success": False,
                "message": f"Registry returned HTTP {put_resp.status_code}",
            }

    except Exception as exc:
        logger.warning(
            "add_v2_tag error host=%s repo=%s src=%s new=%s: %s",
            host,
            repository,
            source_tag,
            new_tag,
            exc,
        )
        return {"success": False, "message": str(exc)}
