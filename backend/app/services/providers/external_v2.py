"""Portalcrane - OCI / Docker Distribution V2 Registry Provider.

This class is the single authoritative implementation of the OCI Distribution
Specification v2 HTTP API. It is used as:
  1. A standalone provider for external V2-compatible registries.
  2. The internal provider held by RegistryService via composition.

All V2 operations — ping, catalog listing, tag listing, manifest fetch/push/
delete, config blob fetch, tag detail, tag copy, tag delete — live here.
Higher-level logic (statistics, internal-registry-specific operations) lives
in RegistryService which delegates to an instance of this class.
"""

import asyncio
import logging
import json as _json

from typing import Any

import httpx

from .base import BaseRegistryProvider

logger = logging.getLogger(__name__)

# Accept header covering all common manifest media types.
_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)

# Media types that indicate a manifest list / image index (multi-arch).
_MANIFEST_LIST_TYPES = frozenset(
    [
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    ]
)

_DEFAULT_TIMEOUT = 30.0


class V2Provider(BaseRegistryProvider):
    """OCI Distribution Specification v2 provider.

    Implements every standard registry operation defined by the OCI
    Distribution Spec: ping, catalog, tags, manifests, blobs, and
    manifest management (push, delete, retag).

    Args:
        host:       Registry hostname with or without scheme.
        username:   Optional username for Basic Auth.
        password:   Optional password for Basic Auth.
        use_tls:    Use HTTPS when True (default True).
        tls_verify: Validate TLS certificates when True (default True).
        timeout:    Default HTTP timeout in seconds; overrides the per-method
                    defaults for long-running operations (manifest fetch, blob
                    download).  Probe and catalog timeouts are not affected.
    """

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        tls_verify: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(
            host=host,
            username=username,
            password=password,
            use_tls=use_tls,
            tls_verify=tls_verify,
        )
        # Configurable default timeout used for manifest and blob operations.
        self.timeout = timeout

    # ── Provider identity ─────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        return "v2"

    @property
    def _auth(self) -> tuple[str, str] | None:
        """Return a Basic Auth tuple when credentials are configured."""
        if self.username and self.password:
            return (self.username, self.password)
        return None

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        """Create an authenticated async HTTP client for registry calls.

        Args:
            timeout: Override the instance timeout for this specific client.
                     Falls back to self.timeout when not provided.
        """
        headers = {
            "Accept": (
                "application/vnd.docker.distribution.manifest.v2+json,application/json"
            )
        }
        return httpx.AsyncClient(
            auth=self._auth,
            headers=headers,
            timeout=timeout if timeout is not None else self.timeout,
            follow_redirects=True,
            verify=self.verify,
        )

    # ── BaseRegistryProvider abstract implementations ─────────────────────────

    async def ping(self) -> bool:
        """Return True when the registry responds to the /v2/ ping endpoint."""
        try:
            async with self._client(timeout=self.probe_timeout) as client:
                resp = await client.get(f"{self.base_url}/v2/")
                return resp.status_code in (200, 401)
        except Exception:
            return False

    async def test_connection(self) -> dict[str, Any]:
        """Probe the registry to check reachability and validate credentials.

        Strategy:
          Step 1 — GET /v2/ without auth to verify the endpoint is alive.
          Step 2 — GET /v2/ with Basic Auth to validate credentials when supplied.

        Returns:
            dict with keys: reachable (bool), auth_ok (bool), message (str).
        """

        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                # Step 1: reachability ping (no auth)
                ping_resp = await client.get(f"{self.base_url}/v2/")

                if ping_resp.status_code not in (200, 401):
                    return {
                        "reachable": True,
                        "auth_ok": False,
                        "message": f"Unexpected status {ping_resp.status_code}",
                    }

                if not self.has_credentials:
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

                # Step 2: credential validation
                cred_resp = await client.get(
                    f"{self.base_url}/v2/",
                    auth=(self.username, self.password),
                )

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
                    "test_connection: /v2/ returned %s for host=%s",
                    cred_resp.status_code,
                    self.host,
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
            return {
                "reachable": False,
                "auth_ok": False,
                "message": "Connection refused",
            }
        except httpx.TimeoutException:
            return {
                "reachable": False,
                "auth_ok": False,
                "message": "Connection timed out",
            }
        except Exception as exc:
            logger.warning("test_connection failed host=%s: %s", self.host, exc)
            return {
                "reachable": False,
                "auth_ok": False,
                "message": "Connection failed",
            }

    async def check_catalog(self) -> bool:
        """Return True when /v2/_catalog is accessible (HTTP 200 or 401).

        HTTP 403, 404, or network errors all map to False (not browsable).
        """
        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.get(
                    f"{self.base_url}/v2/_catalog?n=1", auth=self._auth
                )
            browsable = resp.status_code in (200, 401)
            logger.debug(
                "check_catalog host=%s status=%s browsable=%s",
                self.host,
                resp.status_code,
                browsable,
            )
            return browsable
        except Exception as exc:
            logger.warning("check_catalog host=%s error: %s", self.host, exc)
            return False

    async def browse_repositories(
        self,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """List repositories via /v2/_catalog with optional filtering and pagination.

        Args:
            search:    Optional substring filter on repository names.
            page:      1-based page number.
            page_size: Number of items per page.

        Returns:
            Paginated dict compatible with ExternalPaginatedImages frontend model:
            { items, total, page, page_size, total_pages, error? }
        """

        try:
            async with self._client(timeout=self.catalog_timeout) as client:
                resp = await client.get(f"{self.base_url}/v2/_catalog?n=1000")
                resp.raise_for_status()
                repositories: list[str] = resp.json().get("repositories") or []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "browse_repositories: HTTP %s for host=%s",
                exc.response.status_code,
                self.host,
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
            logger.warning("browse_repositories: error host=%s: %s", self.host, exc)
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

        tags_results: list[list[str]] = await asyncio.gather(
            *[self.browse_tags(r) for r in page_repos]
        )

        items = [
            {
                "name": repo,
                "tags": tags if isinstance(tags, list) else [],
                "tag_count": len(tags) if isinstance(tags, list) else 0,
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

    async def browse_tags(self, repository: str) -> list[str]:
        """List all tags for a repository via /v2/{repository}/tags/list.

        Args:
            repository: Repository path, e.g. "myorg/myimage".

        Returns:
            List of tag name strings; empty list on error or 404.
        """
        try:
            async with self._client(timeout=self.tags_timeout) as client:
                resp = await client.get(f"{self.base_url}/v2/{repository}/tags/list")
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                return resp.json().get("tags", []) or []
        except Exception as exc:
            logger.warning(
                "browse_tags error host=%s repo=%s: %s", self.host, repository, exc
            )
            return []

    async def delete_repository(self, repository: str) -> str | None:
        """Delete all tags of a repository in this V2 registry.

        Resolves each tag to its manifest digest then issues DELETE by digest,
        as required by the OCI Distribution Spec.

        Args:
            repository: Repository path, e.g. "myorg/myimage".

        Returns:
            None on success; an error string describing the failure.
        """
        tags = await self.browse_tags(repository)
        if not tags:
            return None  # Nothing to delete — treat as success

        failed: list[str] = []

        try:
            async with self._client(timeout=self.manifest_timeout) as client:
                for tag in tags:
                    try:
                        manifest_resp = await client.get(
                            f"{self.base_url}/v2/{repository}/manifests/{tag}",
                            headers={"Accept": _MANIFEST_ACCEPT},
                        )
                        manifest_resp.raise_for_status()
                        digest = manifest_resp.headers.get("Docker-Content-Digest")
                        if not digest:
                            failed.append(tag)
                            continue

                        del_resp = await client.delete(
                            f"{self.base_url}/v2/{repository}/manifests/{digest}"
                        )
                        if del_resp.status_code not in (200, 202):
                            failed.append(tag)
                    except Exception as exc:
                        logger.warning(
                            "delete_repository: error deleting %s:%s — %s",
                            repository,
                            tag,
                            exc,
                        )
                        failed.append(tag)
        except Exception as exc:
            logger.warning(
                "delete_repository: client error repo=%s: %s", repository, exc
            )
            return str(exc)

        return f"Failed to delete tags: {', '.join(failed)}" if failed else None

    async def get_tags_for_import(self, repository: str) -> list[str]:
        """Return tag list for import jobs (always a plain list[str])."""
        return await self.browse_tags(repository=repository)

    # ── Core V2 operations ────────────────────────────────────────────────────

    async def list_repositories(
        self, n: int = 1000, last: str = "", include_empty: bool = False
    ) -> list[str]:
        """List all repository names from /v2/_catalog.

        By default, repositories with no tags are excluded.
        Tag-presence checks are performed concurrently for performance.

        Args:
            n:             Maximum repositories per catalog request.
            last:          Pagination cursor (last repository name seen).
            include_empty: When True, include repositories with no tags.
        """
        url = f"{self.base_url}/v2/_catalog?n={n}"
        if last:
            url += f"&last={last}"

        async with self._client(timeout=self.catalog_timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            repositories: list[str] = resp.json().get("repositories", [])

        if include_empty:
            return repositories

        # Check all repositories concurrently for the presence of at least one tag
        tags_results: list[list[str]] = await asyncio.gather(
            *[self.browse_tags(repo) for repo in repositories],
            return_exceptions=False,
        )
        return [repo for repo, tags in zip(repositories, tags_results) if tags]

    async def list_empty_repositories(self) -> list[str]:
        """Return repositories that have no tags (ghost entries)."""
        url = f"{self.base_url}/v2/_catalog?n=1000"
        async with self._client(timeout=self.catalog_timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            all_repos: list[str] = resp.json().get("repositories", [])

        if not all_repos:
            return []

        tags_results: list[list[str]] = await asyncio.gather(
            *[self.browse_tags(repo) for repo in all_repos],
            return_exceptions=False,
        )
        return [repo for repo, tags in zip(all_repos, tags_results) if not tags]

    async def get_manifest(self, repository: str, reference: str) -> dict[str, Any]:
        """Fetch a manifest by tag or digest, enriched with private metadata keys.

        Adds two private keys to the returned dict:
            _digest         : Docker-Content-Digest response header.
            _content_length : Content-Length response header (int).

        Returns an empty dict on HTTP 404.
        """
        try:
            async with self._client(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/v2/{repository}/manifests/{reference}",
                    headers={"Accept": _MANIFEST_ACCEPT},
                )
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                manifest = resp.json()
                manifest["_digest"] = resp.headers.get("Docker-Content-Digest", "")
                manifest["_content_length"] = int(resp.headers.get("Content-Length", 0))
                return manifest
        except Exception as exc:
            logger.warning(
                "get_manifest error host=%s repo=%s ref=%s: %s",
                self.host,
                repository,
                reference,
                exc,
            )
            return {}

    async def delete_manifest(self, repository: str, digest: str) -> bool:
        """Delete an image manifest by digest.

        Returns True when the delete succeeded (HTTP 200 or 202).
        """
        async with self._client(timeout=self.manifest_timeout) as client:
            resp = await client.delete(
                f"{self.base_url}/v2/{repository}/manifests/{digest}"
            )
            return resp.status_code in (200, 202)

    async def put_manifest(
        self,
        repository: str,
        reference: str,
        manifest: dict[str, Any],
        content_type: str,
    ) -> bool:
        """Push a manifest to create or update a tag.

        Returns True when the push succeeded (HTTP 200 or 201).
        """
        async with self._client(timeout=self.timeout) as client:
            resp = await client.put(
                f"{self.base_url}/v2/{repository}/manifests/{reference}",
                content=_json.dumps(manifest),
                headers={"Content-Type": content_type},
            )
            return resp.status_code in (200, 201)

    async def add_tag(
        self, repository: str, source_tag: str, new_tag: str
    ) -> dict[str, Any]:
        """Create a new tag by copying the raw manifest of an existing tag.

        No data transfer occurs — only the manifest reference changes.

        Returns:
            dict with keys: success (bool), message (str).
        """
        try:
            async with self._client(timeout=self.manifest_timeout) as client:
                # Fetch raw manifest bytes to avoid JSON re-serialisation drift
                manifest_resp = await client.get(
                    f"{self.base_url}/v2/{repository}/manifests/{source_tag}",
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
                raw_manifest = manifest_resp.content

                put_resp = await client.put(
                    f"{self.base_url}/v2/{repository}/manifests/{new_tag}",
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
                "add_tag error host=%s repo=%s src=%s new=%s: %s",
                self.host,
                repository,
                source_tag,
                new_tag,
                exc,
            )
            return {"success": False, "message": str(exc)}

    async def delete_tag(self, repository: str, tag: str) -> dict[str, Any]:
        """Delete a specific tag by resolving its digest then deleting the manifest.

        Returns:
            dict with keys: success (bool), message (str).
        """
        try:
            async with self._client(timeout=self.manifest_timeout) as client:
                # Resolve the digest from the tag name
                manifest_resp = await client.get(
                    f"{self.base_url}/v2/{repository}/manifests/{tag}",
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

                del_resp = await client.delete(
                    f"{self.base_url}/v2/{repository}/manifests/{digest}"
                )
                if del_resp.status_code in (200, 202):
                    return {"success": True, "message": f"Tag '{tag}' deleted"}

                return {
                    "success": False,
                    "message": f"Registry returned HTTP {del_resp.status_code}",
                }
        except Exception as exc:
            logger.warning(
                "delete_tag error host=%s repo=%s tag=%s: %s",
                self.host,
                repository,
                tag,
                exc,
            )
            return {"success": False, "message": str(exc)}

    async def get_tag_detail(self, repository: str, tag: str) -> dict[str, Any]:
        """Fetch detailed metadata for a specific tag.

        Resolves manifest → config blob → extracts architecture, OS,
        creation date, labels, env vars, exposed ports, entrypoint,
        cmd and layer list.  Handles manifest lists (multi-arch) by
        resolving the first platform manifest before fetching the config.

        Returns:
            Dict matching the ImageDetail schema or empty dict on 404 / error.
        """
        try:
            async with self._client(timeout=self.timeout) as client:
                # Fetch manifest
                manifest_resp = await client.get(
                    f"{self.base_url}/v2/{repository}/manifests/{tag}",
                    headers={"Accept": _MANIFEST_ACCEPT},
                )
                if manifest_resp.status_code == 404:
                    return {}
                manifest_resp.raise_for_status()

                digest = manifest_resp.headers.get("Docker-Content-Digest", "")
                manifest: dict[str, Any] = manifest_resp.json()

                # Resolve first sub-manifest for manifest lists (multi-arch)
                media_type = manifest.get("mediaType", "")
                if media_type in _MANIFEST_LIST_TYPES:
                    sub_manifests = manifest.get("manifests", [])
                    if sub_manifests:
                        sub_digest = sub_manifests[0]["digest"]
                        sub_resp = await client.get(
                            f"{self.base_url}/v2/{repository}/manifests/{sub_digest}",
                            headers={"Accept": _MANIFEST_ACCEPT},
                        )
                        sub_resp.raise_for_status()
                        manifest = sub_resp.json()

                layers: list[dict[str, Any]] = manifest.get("layers", [])
                total_size: int = sum(int(layer.get("size", 0)) for layer in layers)

                # Fetch config blob
                config_digest: str = manifest.get("config", {}).get("digest", "")
                config: dict[str, Any] = {}
                if config_digest:
                    blob_resp = await client.get(
                        f"{self.base_url}/v2/{repository}/blobs/{config_digest}"
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
                "get_tag_detail error host=%s repo=%s tag=%s: %s",
                self.host,
                repository,
                tag,
                exc,
            )
            return {}

    async def get_image_config(self, repository: str, digest: str) -> dict[str, Any]:
        """Fetch an image configuration blob (labels, env vars, creation date, etc.)."""
        async with self._client(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}/v2/{repository}/blobs/{digest}")
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    async def get_image_size(self, repository: str, tag: str) -> int:
        """Calculate total image size in bytes by summing layer sizes.

        Handles manifest lists (multi-arch) by using the first sub-manifest.

        Returns:
            Total size in bytes; 0 on error or missing manifest.
        """
        manifest = await self.get_manifest(repository, tag)
        if not manifest:
            return 0

        total_size = 0

        if manifest.get("mediaType") in _MANIFEST_LIST_TYPES:
            manifests = manifest.get("manifests", [])
            if manifests:
                sub_manifest = await self.get_manifest(
                    repository, manifests[0]["digest"]
                )
                layers = sub_manifest.get("layers", [])
                total_size = sum(layer.get("size", 0) for layer in layers)
        else:
            layers = manifest.get("layers", [])
            total_size = sum(layer.get("size", 0) for layer in layers)

        return total_size
