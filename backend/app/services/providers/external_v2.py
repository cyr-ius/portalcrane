"""Portalcrane - OCI / Docker Distribution V2 Registry Provider.

This class is the single authoritative implementation of the OCI Distribution
Specification v2 HTTP API. It is used as:
  1. A standalone provider for external V2-compatible registries.
  2. The internal provider held by RegistryService via composition.

Architecture decisions:
  - list_repositories() is the single data-fetching entry point for catalog
    operations. browse_repositories() delegates to it instead of having its
    own /v2/_catalog call.
  - get_manifest(), delete_manifest(), put_manifest() and get_image_config()
    are the four low-level V2 building blocks. Higher-level methods
    (get_tag_detail, add_tag, delete_tag, delete_repository) build on them.
  - get_tag_detail() no longer duplicates manifest fetching logic — it calls
    get_manifest() and get_image_config() directly.
  - RegistryService receives a V2Provider instance and delegates all V2 calls
    to it; no duplication exists between the two layers.
  - All methods catch httpx.ConnectError / httpx.TimeoutException so that a
    temporarily unavailable registry never propagates an unhandled exception
    up to the ASGI layer.
"""

import asyncio
import json as _json
import logging
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

# Exceptions that indicate the registry is temporarily unreachable.
_REGISTRY_CONNECT_ERRORS = (httpx.ConnectError, httpx.TimeoutException)


class V2Provider(BaseRegistryProvider):
    """OCI Distribution Specification v2 provider.

    All public methods catch httpx.ConnectError and httpx.TimeoutException
    and return safe empty values instead of propagating them. This prevents
    a temporarily unavailable registry (e.g. the embedded local registry
    managed by supervisord) from crashing the ASGI application with a 500.
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
        """Create an authenticated async HTTP client for registry calls."""
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

    # ── Abstract implementations — connectivity ───────────────────────────────

    async def ping(self) -> bool:
        """Return True when the registry responds to the /v2/ ping endpoint."""
        try:
            async with self._client(timeout=self.probe_timeout) as client:
                resp = await client.get(f"{self.base_url}/v2/")
                return resp.status_code in (200, 401)
        except Exception:
            return False

    async def test_connection(self) -> dict[str, Any]:
        """Probe the registry to check reachability and validate credentials."""
        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
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
        """Return True when /v2/_catalog is accessible."""
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

    async def list_repositories(
        self,
        page_size: int = 1000,
        page: int = 1,
        last: str = "",
        include_empty: bool = False,
    ) -> list[str]:
        """List all repository names from /v2/_catalog.

        Returns an empty list when the registry is unreachable instead of
        raising httpx.ConnectError, so callers never receive an unhandled
        exception when the embedded registry is temporarily down.
        """
        url = f"{self.base_url}/v2/_catalog?n={page_size}"
        if last:
            url += f"&last={last}"

        try:
            async with self._client(timeout=self.catalog_timeout) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                repositories: list[str] = resp.json().get("repositories", [])
        except _REGISTRY_CONNECT_ERRORS as exc:
            logger.warning(
                "list_repositories: registry unreachable host=%s: %s", self.host, exc
            )
            return []
        except Exception as exc:
            logger.warning("list_repositories: error host=%s: %s", self.host, exc)
            return []

        if include_empty:
            return repositories

        # Filter out repositories with no tags (concurrent checks for speed).
        try:
            tags_results: list[list[str]] = await asyncio.gather(
                *[self.browse_tags(repo) for repo in repositories],
                return_exceptions=False,
            )
        except Exception as exc:
            logger.warning(
                "list_repositories: error filtering empty repos host=%s: %s",
                self.host,
                exc,
            )
            return repositories

        return [repo for repo, tags in zip(repositories, tags_results) if tags]

    async def browse_tags(self, repository: str) -> list[str]:
        """List all tags for a repository via /v2/{repository}/tags/list."""
        try:
            async with self._client(timeout=self.tags_timeout) as client:
                resp = await client.get(f"{self.base_url}/v2/{repository}/tags/list")
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                return resp.json().get("tags", []) or []
        except _REGISTRY_CONNECT_ERRORS as exc:
            logger.warning(
                "browse_tags: registry unreachable host=%s repo=%s: %s",
                self.host,
                repository,
                exc,
            )
            return []
        except Exception as exc:
            logger.warning(
                "browse_tags error host=%s repo=%s: %s", self.host, repository, exc
            )
            return []

    async def get_tags_for_import(self, repository: str) -> list[str]:
        """Return tag list for import jobs (always a plain list[str])."""
        return await self.browse_tags(repository=repository)

    async def get_manifest(self, repository: str, reference: str) -> dict[str, Any]:
        """Fetch a manifest by tag or digest."""
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
                manifest["_content_type"] = resp.headers.get(
                    "Content-Type",
                    "application/vnd.docker.distribution.manifest.v2+json",
                )
                return manifest
        except _REGISTRY_CONNECT_ERRORS as exc:
            logger.warning(
                "get_manifest: registry unreachable host=%s repo=%s ref=%s: %s",
                self.host,
                repository,
                reference,
                exc,
            )
            return {}
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
        """Delete an image manifest by digest."""
        try:
            async with self._client(timeout=self.manifest_timeout) as client:
                resp = await client.delete(
                    f"{self.base_url}/v2/{repository}/manifests/{digest}"
                )
                return resp.status_code in (200, 202)
        except Exception as exc:
            logger.warning(
                "delete_manifest error host=%s repo=%s digest=%s: %s",
                self.host,
                repository,
                digest,
                exc,
            )
            return False

    async def put_manifest(
        self,
        repository: str,
        reference: str,
        manifest: dict[str, Any],
        content_type: str,
    ) -> bool:
        """Push a manifest to create or update a tag."""
        clean = {k: v for k, v in manifest.items() if not k.startswith("_")}
        try:
            async with self._client(timeout=self.timeout) as client:
                resp = await client.put(
                    f"{self.base_url}/v2/{repository}/manifests/{reference}",
                    content=_json.dumps(clean),
                    headers={"Content-Type": content_type},
                )
                return resp.status_code in (200, 201)
        except Exception as exc:
            logger.warning(
                "put_manifest error host=%s repo=%s ref=%s: %s",
                self.host,
                repository,
                reference,
                exc,
            )
            return False

    async def get_image_config(self, repository: str, digest: str) -> dict[str, Any]:
        """Fetch an image configuration blob."""
        try:
            async with self._client(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/v2/{repository}/blobs/{digest}"
                )
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                return resp.json()
        except _REGISTRY_CONNECT_ERRORS as exc:
            logger.warning(
                "get_image_config: registry unreachable host=%s repo=%s digest=%s: %s",
                self.host,
                repository,
                digest,
                exc,
            )
            return {}
        except Exception as exc:
            logger.warning(
                "get_image_config error host=%s repo=%s digest=%s: %s",
                self.host,
                repository,
                digest,
                exc,
            )
            return {}

    async def get_tag_detail(self, repository: str, tag: str) -> dict[str, Any]:
        """Fetch detailed metadata for a specific tag."""
        manifest = await self.get_manifest(repository, tag)
        if not manifest:
            return {}

        digest = manifest.get("_digest", "")

        media_type = manifest.get("mediaType", "")
        if media_type in _MANIFEST_LIST_TYPES:
            sub_manifests = manifest.get("manifests", [])
            if sub_manifests:
                sub_manifest = await self.get_manifest(
                    repository, sub_manifests[0]["digest"]
                )
                if sub_manifest:
                    manifest = sub_manifest

        layers: list[dict[str, Any]] = manifest.get("layers", [])
        total_size: int = sum(int(layer.get("size", 0)) for layer in layers)

        config_digest: str = manifest.get("config", {}).get("digest", "")
        config: dict[str, Any] = {}
        if config_digest:
            config = await self.get_image_config(repository, config_digest)

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

    async def add_tag(
        self, repository: str, source_tag: str, new_tag: str
    ) -> dict[str, Any]:
        """Create a new tag by copying the raw manifest of an existing tag."""
        try:
            async with self._client(timeout=self.manifest_timeout) as client:
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
        except _REGISTRY_CONNECT_ERRORS as exc:
            logger.warning(
                "add_tag: registry unreachable host=%s repo=%s src=%s new=%s: %s",
                self.host,
                repository,
                source_tag,
                new_tag,
                exc,
            )
            return {"success": False, "message": "Registry unreachable"}
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
        """Delete a specific tag by resolving its digest then calling delete_manifest."""
        manifest = await self.get_manifest(repository, tag)
        if not manifest:
            return {"success": False, "message": f"Tag '{tag}' not found"}

        digest = manifest.get("_digest", "")
        if not digest:
            return {
                "success": False,
                "message": "Registry did not return a Docker-Content-Digest header",
            }

        success = await self.delete_manifest(repository, digest)
        if success:
            return {"success": True, "message": f"Tag '{tag}' deleted"}
        return {
            "success": False,
            "message": f"Failed to delete manifest for tag '{tag}'",
        }

    async def delete_repository(self, repository: str) -> str | None:
        """Delete all tags of a repository."""
        try:
            tags = await self.browse_tags(repository)
        except Exception as exc:
            return f"Failed to list tags: {exc}"

        if not tags:
            return None

        failed: list[str] = []

        for tag in tags:
            result = await self.delete_tag(repository, tag)
            if not result.get("success"):
                failed.append(tag)
                logger.warning(
                    "delete_repository: error deleting %s:%s — %s",
                    repository,
                    tag,
                    result.get("message"),
                )

        return f"Failed to delete tags: {', '.join(failed)}" if failed else None

    async def get_image_size(self, repository: str, tag: str) -> int:
        """Calculate total image size in bytes by summing layer sizes."""
        manifest = await self.get_manifest(repository, tag)
        if not manifest:
            return 0

        if manifest.get("mediaType") in _MANIFEST_LIST_TYPES:
            manifests = manifest.get("manifests", [])
            if manifests:
                sub_manifest = await self.get_manifest(
                    repository, manifests[0]["digest"]
                )
                layers = sub_manifest.get("layers", []) if sub_manifest else []
            else:
                layers = []
        else:
            layers = manifest.get("layers", [])

        return sum(layer.get("size", 0) for layer in layers)
