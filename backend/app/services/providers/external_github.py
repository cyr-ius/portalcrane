"""Portalcrane - Github REST API Service."""

import asyncio
import logging
from typing import Any

import httpx

from .base import BaseRegistryProvider

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GHCR_V2_BASE = "https://ghcr.io"

_DEFAULT_TIMEOUT = 30.0

_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)

_MANIFEST_LIST_TYPES = frozenset(
    [
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    ]
)


class GithubProvider(BaseRegistryProvider):
    """Github provider"""

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        tls_verify: bool = True,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize provider with registry credentials.

        Args:
            host:       Registry hostname, with or without scheme.
            username:   Registry username or GitHub owner login.
            password:   Registry password or access token.
            use_tls:    Use HTTPS when True (default).
            tls_verify: Validate TLS certificate when True (default).
        """
        super().__init__(
            host=host,
            username=username,
            password=password,
            use_tls=use_tls,
            tls_verify=tls_verify,
        )
        # Configurable default timeout
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "gihub"

    @property
    def owner(self):
        return self.username

    @property
    def token(self):
        return self.password

    def _github_api_headers(self) -> dict[str, str]:
        """Return standard GitHub API request headers with Bearer authentication."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }

    def _ghcr_v2_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Return headers for GHCR OCI Distribution V2 calls.

        GHCR accepts the PAT directly as a Bearer token on ghcr.io/v2/.
        No OAuth token exchange is required unlike standard Docker registries.
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.token}",
        }
        if extra:
            headers.update(extra)
        return headers

    def _get_package_urls(self) -> list[str]:
        """Return urls for GitHub user or organisation."""
        return [
            f"{_GITHUB_API}/users/{self.owner}/packages",
            f"{_GITHUB_API}/orgs/{self.owner}/packages",
        ]

    async def ping(self) -> bool:
        """Return True when the registry responds to the /v2/ ping endpoint."""
        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.get(f"{_GITHUB_API}/octocat/")
                return resp.status_code in (200, 401)
        except Exception:
            return False

    async def test_connection(self) -> dict[str, Any]:
        """Probe a Docker hub registry to check reachability and credentials.
        Returns:
            Dict with keys: reachable (bool), auth_ok (bool), message (str).
        """

        if self.username is None or self.username == "":
            return {
                "reachable": False,
                "auth_ok": False,
                "message": "Username is incorrect",
            }

        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                cred_resp = await client.get(
                    f"{_GITHUB_API}/octocat/", headers=self._github_api_headers()
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
                    "test_dockerhub_connection: returned %s for host=%s; ",
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
            logger.warning(
                "test_dockerhub_connection failed host=%s: %s", self.host, exc
            )
            return {
                "reachable": False,
                "auth_ok": False,
                "message": "Connection failed",
            }

    async def check_catalog(self) -> bool:
        browsable = bool(self.password)
        logger.debug(
            "check_catalog_browsable: GHCR — browsable=%s (token present=%s)",
            browsable,
            browsable,
        )
        return browsable

    async def list_repositories(
        self,
        page_size: int = 1000,
        page: int = 1,
        last: str = "",
        include_empty: bool = False,
    ) -> list[str]:
        """List all repository names from /v2/_catalog.

        This is the ONLY method that directly calls /v2/_catalog. All other
        methods that need a repository list (browse_repositories,
        list_empty_repositories, RegistryService helpers) delegate here.

        Args:
            n:             Maximum repositories per catalog request.
            last:          Pagination cursor (last repository name seen).
            include_empty: When True,  return all repositories including
                           those with no tags.
                           When False (default), exclude tag-less repositories
                           (concurrent tag-presence check performed).

        Returns:
            list[str]: Repository names.
        """
        headers = self._github_api_headers()
        urls_to_try = self._get_package_urls()
        repositories: list[str] = []

        async with httpx.AsyncClient(
            timeout=self.catalog_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            params = {"package_type": "container", "per_page": page_size}
            for url in urls_to_try:
                try:
                    resp = await client.get(url, headers=headers, params=params)
                    resp.raise_for_status()
                    packages = resp.json()
                    repositories = [
                        f"{self.owner}/{pkg['name']}"
                        for pkg in packages
                        if isinstance(pkg, dict) and "name" in pkg
                    ]
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "HTTP %s for host=%s", exc.response.status_code, self.host
                    )
                except Exception as exc:
                    logger.warning("Unknown Error host=%s: %s", self.host, exc)

        if include_empty:
            return repositories

        # Filter out repositories with no tags (concurrent checks for speed).
        tags_results: list[list[str]] = await asyncio.gather(
            *[self.browse_tags(repo) for repo in repositories],
            return_exceptions=False,
        )
        return [repo for repo, tags in zip(repositories, tags_results) if tags]

    async def browse_repositories(
        self, search: str | None, page: int, page_size: int = 20
    ) -> dict:
        """
        List container packages for a GitHub user or organisation via the
        GitHub REST API (GET /users/{owner}/packages or /orgs/{owner}/packages).

        The `owner` field stored in the registry entry is used as the GitHub
        username / org name.  Falls back to the registry `username` when owner
        is not set (personal registries saved before this feature existed).

        Authentication: GitHub personal access token with `read:packages` scope,
        stored as the registry password.
        """
        repositories: list[str] = []
        try:
            repositories = await self.list_repositories(
                page_size == page_size, include_empty=True
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "HTTP %s for host=%s",
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
            logger.warning("Unknown error host=%s: %s", self.host, exc)
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "error": str(exc),
            }

        # Apply search filter
        if search:
            repositories = [r for r in repositories if search.lower() in r.lower()]

        # ── Build paginated response ───────────────────────────────────────────
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

    async def browse_tags(self, repository: str) -> list[str]:
        """List all tags for a GitHub repository.Get package (user or organisation)."""
        headers = self._github_api_headers()
        async with httpx.AsyncClient(
            timeout=self.tags_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            for base_url in self._get_package_urls():
                try:
                    package = repository.split("/", 1)[-1]
                    tag_url = f"{base_url}/container/{package}/versions"
                    resp = await client.get(tag_url, headers=headers)
                    if resp.status_code == 200:
                        versions = resp.json()
                        tags: list[str] = []
                        for v in versions:
                            meta = v.get("metadata", {}).get("container", {})
                            tags.extend(meta.get("tags", []))
                        return tags
                    elif resp.status_code == 404:
                        # Not a user, try org endpoint
                        continue
                    else:
                        logger.warning("GitHub API returned HTTP %s", resp.status_code)
                except Exception as exc:
                    logger.warning("Error to retrieve tag (%s)", exc)
                    pass

        return []

    async def get_tag(self, package: str, version_id: str) -> dict[str, Any]:
        """Get a specific version of a image."""
        headers = self._github_api_headers()
        last_error: str | None = None

        async with httpx.AsyncClient(
            timeout=self.tags_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            for base_url in self._get_package_urls():
                try:
                    version_url = (
                        f"{base_url}/container/{package}/versions/{version_id}"
                    )
                    resp = await client.get(version_url, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
                    elif resp.status_code == 404:
                        # Not a user, try org endpoint
                        continue
                    else:
                        last_error = f"GitHub API returned HTTP {resp.status_code}"
                except Exception as exc:
                    logger.warning("Error to retrieve tag for %s (%s)", package, exc)
                    last_error = "Error to retrieve tag, please view log."

        return {"error": last_error}

    async def get_tags_for_import(self, repository: str) -> list[str]:
        """
        Retrieve all tag names for a package, used by the import job.

        This is a simplified wrapper around browse_github_tag() that always
        returns a list (never a dict), safe to iterate in run_import_job().
        """
        repository = repository.split("/", 1)[-1] if "/" in repository else repository
        result = await self.browse_tags(repository=repository)
        if isinstance(result, list):
            return result
        return []

    async def get_manifest(self, repository: str, reference: str) -> dict[str, Any]:
        """Fetch a manifest from GHCR using the native OCI Distribution V2 API.

        Endpoint: GET https://ghcr.io/v2/{repository}/manifests/{reference}
        Auth:     Authorization: Bearer <PAT>

        No token exchange required — GHCR accepts the PAT directly on /v2/.

        Adds private metadata keys to the returned dict:
            _digest         : Docker-Content-Digest response header.
            _content_type   : Content-Type response header.
            _content_length : Content-Length response header (int).

        Args:
            repository: Repository path e.g. "myuser/myimage".
            reference:  Tag name or digest (sha256:...).

        Returns:
            dict: Manifest payload with private keys; empty dict on 404 or error.
        """
        headers = self._ghcr_v2_headers(extra={"Accept": _MANIFEST_ACCEPT})
        url = f"{_GHCR_V2_BASE}/v2/{repository}/manifests/{reference}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()

                manifest = resp.json()
                manifest["_digest"] = resp.headers.get("Docker-Content-Digest", "")
                manifest["_content_type"] = resp.headers.get("Content-Type", "")
                manifest["_content_length"] = int(resp.headers.get("Content-Length", 0))
                return manifest

        except Exception as exc:
            logger.warning(
                "get_manifest error repo=%s ref=%s: %s", repository, reference, exc
            )
            return {}

    async def get_image_config(self, repository: str, digest: str) -> dict[str, Any]:
        """Fetch an image configuration blob from GHCR native OCI V2 API.

        Endpoint: GET https://ghcr.io/v2/{repository}/blobs/{digest}
        Auth:     Authorization: Bearer <PAT>

        Args:
            repository: Repository path e.g. "myuser/myimage".
            digest:     Config blob digest (sha256:...).

        Returns:
            dict: Image config payload; empty dict on 404 or error.
        """
        headers = self._ghcr_v2_headers()
        url = f"{_GHCR_V2_BASE}/v2/{repository}/blobs/{digest}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 404:
                    return {}
                resp.raise_for_status()
                return resp.json()

        except Exception as exc:
            logger.warning(
                "get_image_config error repo=%s digest=%s: %s",
                repository,
                digest,
                exc,
            )
            return {}

    async def get_tag_detail(self, repository: str, tag: str) -> dict[str, Any]:
        """Fetch detailed metadata for a GHCR image tag using native V2 API.

        Two native GHCR V2 API calls:
          1. GET https://ghcr.io/v2/{repository}/manifests/{tag}
          2. GET https://ghcr.io/v2/{repository}/blobs/{config_digest}

        Handles manifest lists (multi-arch) by resolving the first sub-manifest.

        Args:
            repository: Repository path e.g. "myuser/myimage".
            tag:        Tag name, e.g. "latest".

        Returns:
            dict: ImageDetail-compatible payload; empty dict on 404 or error.
        """
        manifest = await self.get_manifest(repository, tag)
        if not manifest:
            return {}

        digest = manifest.get("_digest", "")
        media_type = manifest.get("mediaType", "")

        # Resolve first sub-manifest for multi-arch manifest lists
        if media_type in _MANIFEST_LIST_TYPES:
            sub_manifests = manifest.get("manifests", [])
            if sub_manifests:
                sub = await self.get_manifest(repository, sub_manifests[0]["digest"])
                if sub:
                    manifest = sub

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
        """Create a new tag by copying a manifest on GHCR native OCI V2 API.

        Fetches raw manifest bytes from the source tag and PUTs them under
        the new tag name. No image data transfer occurs.

        GHCR V2 endpoints used natively:
          GET  https://ghcr.io/v2/{repository}/manifests/{source_tag}
          PUT  https://ghcr.io/v2/{repository}/manifests/{new_tag}

        Args:
            repository: Repository path.
            source_tag: Existing tag to copy from.
            new_tag:    New tag name to create.

        Returns:
            dict: {"success": bool, "message": str}
        """
        get_headers = self._ghcr_v2_headers(extra={"Accept": _MANIFEST_ACCEPT})
        get_url = f"{_GHCR_V2_BASE}/v2/{repository}/manifests/{source_tag}"

        try:
            async with httpx.AsyncClient(
                timeout=self.manifest_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                # Step 1 — fetch raw manifest bytes preserving exact wire format
                get_resp = await client.get(get_url, headers=get_headers)
                if get_resp.status_code == 404:
                    return {
                        "success": False,
                        "message": f"Source tag '{source_tag}' not found",
                    }
                get_resp.raise_for_status()

                content_type = get_resp.headers.get(
                    "Content-Type",
                    "application/vnd.docker.distribution.manifest.v2+json",
                )
                raw_manifest = get_resp.content

                # Step 2 — PUT raw manifest bytes under new tag name
                put_url = f"{_GHCR_V2_BASE}/v2/{repository}/manifests/{new_tag}"
                put_headers = self._ghcr_v2_headers(
                    extra={"Content-Type": content_type}
                )
                put_resp = await client.put(
                    put_url, content=raw_manifest, headers=put_headers
                )

                if put_resp.status_code in (200, 201):
                    return {
                        "success": True,
                        "message": f"Tag '{new_tag}' created from '{source_tag}'",
                    }
                return {
                    "success": False,
                    "message": f"GHCR returned HTTP {put_resp.status_code}",
                }

        except Exception as exc:
            logger.warning(
                "add_tag error repo=%s src=%s new=%s: %s",
                repository,
                source_tag,
                new_tag,
                exc,
            )
            return {"success": False, "message": str(exc)}

    async def delete_tag(self, repository: str, tag: str) -> dict[str, Any]:
        """Delete a single tag from GHCR using the native OCI V2 API.

        Resolves the manifest digest first, then sends DELETE on the digest.
        Deleting by digest removes the manifest (and thus the tag).

        GHCR V2 endpoints used natively:
          GET    https://ghcr.io/v2/{repository}/manifests/{tag}     → digest
          DELETE https://ghcr.io/v2/{repository}/manifests/{digest}  → remove

        Args:
            repository: Repository path.
            tag:        Tag name to delete.

        Returns:
            dict: {"success": bool, "message": str}
        """
        manifest = await self.get_manifest(repository, tag)
        if not manifest:
            return {"success": False, "message": f"Tag '{tag}' not found"}

        digest = manifest.get("_digest", "")
        if not digest:
            return {
                "success": False,
                "message": "GHCR did not return a Docker-Content-Digest header",
            }

        delete_url = f"{_GHCR_V2_BASE}/v2/{repository}/manifests/{digest}"
        headers = self._ghcr_v2_headers()

        try:
            async with httpx.AsyncClient(
                timeout=self.manifest_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.delete(delete_url, headers=headers)
                if resp.status_code in (200, 202):
                    return {"success": True, "message": f"Tag '{tag}' deleted"}
                return {
                    "success": False,
                    "message": f"GHCR returned HTTP {resp.status_code}",
                }

        except Exception as exc:
            logger.warning("delete_tag error repo=%s tag=%s: %s", repository, tag, exc)
            return {"success": False, "message": str(exc)}

    async def delete_repository(self, repository: str) -> str | None:
        """Delete a container package via the GitHub REST API.

        Uses DELETE /users/{owner}/packages/container/{package}
        or   DELETE /orgs/{owner}/packages/container/{package}

        Args:
            repository: Full repository name e.g. "myuser/myimage".

        Returns:
            None on success; error string on failure.
        """
        headers = self._github_api_headers()
        package = repository.split("/", 1)[-1] if "/" in repository else repository
        last_error: str | None = None

        async with httpx.AsyncClient(
            timeout=self.manifest_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            for base_url in self._get_package_urls():
                try:
                    url = f"{base_url}/container/{package}"
                    resp = await client.delete(url, headers=headers)
                    if resp.status_code in (200, 204):
                        logger.info(
                            "delete_repository: deleted %s via %s",
                            repository,
                            base_url,
                        )
                        return None
                    if resp.status_code == 404:
                        continue
                    if resp.status_code == 401:
                        return "GitHub authentication failed."
                    if resp.status_code == 403:
                        return f"Permission denied: cannot delete '{repository}'."
                    last_error = f"GitHub API returned HTTP {resp.status_code}."
                except Exception as exc:
                    logger.warning(
                        "delete_repository error repo=%s url=%s: %s",
                        repository,
                        base_url,
                        exc,
                    )
                    last_error = str(exc)

        return last_error or f"Package '{repository}' not found on GitHub."
