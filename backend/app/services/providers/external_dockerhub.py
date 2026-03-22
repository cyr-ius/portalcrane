"""Portalcrane - Docker Hub REST API Service."""

import asyncio
import logging
from typing import Any

import httpx

from .base import BaseRegistryProvider

logger = logging.getLogger(__name__)

_HUB_API = "https://hub.docker.com"
_HUB_REGISTRY = "https://registry-1.docker.io"
_AUTH_SERVICE = "registry.docker.io"
_AUTH_URL = "https://auth.docker.io/token"
_PAGE_SIZE_MAX = 100  # Docker Hub maximum page size for repository listing
_DEFAULT_TIMEOUT = 30.0

# Accept header covering all common manifest media types
_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)

# Media types that indicate a manifest list / image index (multi-arch)
_MANIFEST_LIST_TYPES = frozenset(
    [
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.index.v1+json",
    ]
)


class DockerHubProvider(BaseRegistryProvider):
    """Docker Hub provider."""

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
        return "dockerhub"

    async def _get_hub_jwt(self) -> str | None:
        """
        Obtain a short-lived JWT from the Docker Hub login endpoint.

        Returns the token string on success, or None when authentication fails.
        The token is valid for ~300 s; it is not cached here because each browse
        request is stateless and the latency is negligible relative to the
        subsequent API calls.
        """
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.post(
                    f"{_HUB_API}/v2/users/login",
                    json={"username": self.username, "password": self.password},
                )
                if resp.status_code == 200:
                    return resp.json().get("token")
                logger.warning(
                    "Docker Hub login failed for user=%s status=%s",
                    self.username,
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning("Docker Hub login error user=%s: %s", self.username, exc)
        return None

    async def _get_v2_token(self, repository: str, actions: str = "pull") -> str | None:
        """Obtain a Bearer token for registry-1.docker.io V2 API calls.

        Docker Hub V2 requires a scoped token obtained from auth.docker.io.
        The token scope is per-repository and per-action (pull / push / *).

        Endpoint: GET https://auth.docker.io/token
        Params:
            service = registry.docker.io
            scope   = repository:{repository}:{actions}
        Auth:   Basic Auth with username:password (or anonymous)

        Args:
            repository: Full repository path e.g. "library/nginx" or "myuser/myimage".
            actions:    Comma-separated action list e.g. "pull" or "pull,push".

        Returns:
            Bearer token string on success; None on failure.
        """
        params = {
            "service": _AUTH_SERVICE,
            "scope": f"repository:{repository}:{actions}",
        }
        auth = (self.username, self.password) if self.has_credentials else None

        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.get(_AUTH_URL, params=params, auth=auth)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("token") or data.get("access_token")
                logger.warning(
                    "_get_v2_token: status=%s repo=%s", resp.status_code, repository
                )
        except Exception as exc:
            logger.warning("_get_v2_token error repo=%s: %s", repository, exc)
        return None

    def _hub_auth_headers(self, token: str) -> dict[str, str]:
        """Return HTTP headers for an authenticated Docker Hub API request."""
        return {
            "Authorization": f"JWT {token}",
            "Content-Type": "application/json",
        }

    def _v2_headers(
        self, token: str, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Return headers for registry-1.docker.io V2 API calls."""
        headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
        if extra:
            headers.update(extra)
        return headers

    def _normalize_repository(self, repository: str) -> str:
        """Ensure official images are prefixed with 'library/'.

        Docker Hub stores official images under the 'library' namespace
        (e.g. 'nginx' → 'library/nginx') for V2 API calls.
        User images already have a namespace (e.g. 'myuser/myimage').
        """
        if "/" not in repository:
            return f"library/{repository}"
        return repository

    async def ping(self) -> bool:
        """Return True when the registry responds to the /v2/ ping endpoint."""
        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.get(_HUB_API)
                return resp.status_code in (200, 401)
        except Exception:
            return False

    async def test_connection(self) -> dict[str, Any]:
        """Probe a Docker hub registry to check reachability and credentials.
        Returns:
            Dict with keys: reachable (bool), auth_ok (bool), message (str).
        """
        has_credentials = bool(self.username and self.password)
        auth = (
            {"username": self.username, "password": self.password}
            if has_credentials
            else None
        )

        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                cred_resp = await client.post(f"{_HUB_API}/v2/users/login", json=auth)
                if cred_resp.status_code == 200:
                    if not has_credentials:
                        return {
                            "reachable": True,
                            "auth_ok": True,
                            "message": "Registry reachable (public)",
                        }

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
        has_creds = bool(
            (self.username or "").strip() and (self.password or "").strip()
        )
        logger.debug(
            "check_catalog_browsable: Docker Hub — browsable=%s (creds present=%s)",
            has_creds,
            has_creds,
        )
        return has_creds

    async def list_repositories(
        self,
        page_size: int = 1000,
        page: int = 1,
        last: str = "",
        include_empty: bool = False,
    ) -> list[str]:
        """List all repository names.

        Returns:
            list[str]: Repository names.
        """
        repositories: list[str] = []
        namespace = self.username
        token = await self._get_hub_jwt()
        if not token:
            return repositories
        headers = self._hub_auth_headers(token)

        async with httpx.AsyncClient(
            timeout=self.catalog_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            try:
                params = {"page": page, "page_size": min(page_size, 1000)}
                resp = await client.get(
                    f"{_HUB_API}/v2/repositories/{namespace}/",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                packages = resp.json()
                repositories = [
                    f"{self.username}/{pkg['name']}"
                    for pkg in packages.get("results", {})
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

    async def browse_tags(self, repository: str) -> list[str]:
        """
        List all tags for a Docker Hub repository.

        Args:
            username:   Docker Hub account username.
            password:   Docker Hub account password or access token.
            repository: Full repository reference, e.g. "myuser/myimage" or
                        "library/nginx" for official images.

        Returns:
            List of tag name strings (may be empty on error).
        """
        token = await self._get_hub_jwt()
        if not token:
            logger.warning("browse_dockerhub_tags: auth failed for repo=%s", repository)
            return []

        headers = self._hub_auth_headers(token)

        # Normalise namespace/name split
        if "/" in repository:
            namespace, name = repository.split("/", 1)
        else:
            namespace, name = "library", repository

        tags: list[str] = []
        url: str | None = f"{_HUB_API}/v2/repositories/{namespace}/{name}/tags/"

        try:
            async with httpx.AsyncClient(
                timeout=self.tags_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                while url:
                    resp = await client.get(
                        url,
                        headers=headers,
                        params={"page_size": _PAGE_SIZE_MAX},
                    )
                    if resp.status_code != 200:
                        logger.warning(
                            "browse_dockerhub_tags: HTTP %s for repo=%s",
                            resp.status_code,
                            repository,
                        )
                        break
                    data = resp.json()
                    for result in data.get("results") or []:
                        tag_name = result.get("name")
                        if tag_name:
                            tags.append(tag_name)
                    # Follow pagination until exhausted
                    url = data.get("next")
        except Exception as exc:
            logger.warning("browse_dockerhub_tags error repo=%s: %s", repository, exc)

        return tags

    async def get_tags_for_import(self, repository: str) -> list[str]:
        """
        Retrieve tag names for a Docker Hub repository, used by the import job.

        Thin wrapper around browse_dockerhub_tags that guarantees a list return.
        """
        return await self.browse_tags(repository)

    async def get_manifest(self, repository: str, reference: str) -> dict[str, Any]:
        """Fetch a manifest from registry-1.docker.io using the native V2 API.

        Docker Hub V2 authentication flow:
          1. GET auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull
             with Basic Auth → returns a scoped Bearer token
          2. GET registry-1.docker.io/v2/{repository}/manifests/{reference}
             with Authorization: Bearer <token>

        Official images must be prefixed with "library/" for the V2 API.

        Adds private metadata keys:
            _digest        : Docker-Content-Digest header.
            _content_type  : Content-Type header.
            _content_length: Content-Length header (int).

        Args:
            repository: Repository path e.g. "nginx" or "myuser/myimage".
            reference:  Tag name or digest (sha256:...).

        Returns:
            dict: Manifest payload with private keys; empty dict on 404 or error.
        """
        normalized = self._normalize_repository(repository)
        token = await self._get_v2_token(normalized, actions="pull")
        if not token:
            logger.warning(
                "get_manifest: V2 token acquisition failed repo=%s", repository
            )
            return {}

        headers = self._v2_headers(token, extra={"Accept": _MANIFEST_ACCEPT})
        url = f"{_HUB_REGISTRY}/v2/{normalized}/manifests/{reference}"

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
        """Fetch an image configuration blob from registry-1.docker.io.

        Docker Hub V2 blob endpoint:
          GET registry-1.docker.io/v2/{repository}/blobs/{digest}
        Auth: Bearer token scoped to pull for this repository.

        Args:
            repository: Repository path e.g. "nginx" or "myuser/myimage".
            digest:     Config blob digest (sha256:...).

        Returns:
            dict: Image config payload; empty dict on 404 or error.
        """
        normalized = self._normalize_repository(repository)
        token = await self._get_v2_token(normalized, actions="pull")
        if not token:
            logger.warning(
                "get_image_config: V2 token acquisition failed repo=%s", repository
            )
            return {}

        headers = self._v2_headers(token)
        url = f"{_HUB_REGISTRY}/v2/{normalized}/blobs/{digest}"

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
        """Fetch detailed metadata for a Docker Hub image tag via native V2 API.

        Two native registry-1.docker.io V2 API calls:
          1. GET /v2/{repository}/manifests/{tag}      → layers, config digest
          2. GET /v2/{repository}/blobs/{config_digest} → env, cmd, labels…

        Each call requires its own scoped Bearer token from auth.docker.io.
        Handles manifest lists (multi-arch) by resolving the first sub-manifest.

        Args:
            repository: Repository path e.g. "nginx" or "myuser/myimage".
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
        """Create a new tag by copying a manifest on registry-1.docker.io.

        Requires a push-scoped token from auth.docker.io.

        Native V2 endpoints on registry-1.docker.io:
          GET  /v2/{repository}/manifests/{source_tag} (pull scope)
          PUT  /v2/{repository}/manifests/{new_tag}    (push scope)

        Args:
            repository: Repository path.
            source_tag: Existing tag to copy from.
            new_tag:    New tag name to create.

        Returns:
            dict: {"success": bool, "message": str}
        """
        normalized = self._normalize_repository(repository)

        # Pull token to fetch the source manifest
        pull_token = await self._get_v2_token(normalized, actions="pull")
        if not pull_token:
            return {
                "success": False,
                "message": "Failed to obtain pull token from auth.docker.io",
            }

        # Push token to write the new manifest
        push_token = await self._get_v2_token(normalized, actions="pull,push")
        if not push_token:
            return {
                "success": False,
                "message": "Failed to obtain push token from auth.docker.io",
            }

        get_url = f"{_HUB_REGISTRY}/v2/{normalized}/manifests/{source_tag}"
        get_headers = self._v2_headers(pull_token, extra={"Accept": _MANIFEST_ACCEPT})

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

                # Step 2 — PUT raw manifest bytes under the new tag name
                put_url = f"{_HUB_REGISTRY}/v2/{normalized}/manifests/{new_tag}"
                put_headers = self._v2_headers(
                    push_token, extra={"Content-Type": content_type}
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
                    "message": f"registry-1.docker.io returned HTTP {put_resp.status_code}",
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
        """Delete a single tag from Docker Hub via native registry V2 API.

        Docker Hub requires manifests to be deleted by digest, not by tag.
        Steps:
          1. GET /v2/{repository}/manifests/{tag}      → resolve digest
          2. DELETE /v2/{repository}/manifests/{digest} → remove manifest

        Both calls use tokens scoped to pull,push (or * for delete).

        Note: Docker Hub requires "Delete" permission on the repository.
        Personal access tokens need "Read & Write" or "Admin" access.

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
                "message": "registry-1.docker.io did not return a Docker-Content-Digest header",
            }

        normalized = self._normalize_repository(repository)

        # Delete requires a token with delete scope
        token = await self._get_v2_token(normalized, actions="pull,push,delete")
        if not token:
            return {
                "success": False,
                "message": "Failed to obtain delete token from auth.docker.io",
            }

        delete_url = f"{_HUB_REGISTRY}/v2/{normalized}/manifests/{digest}"
        headers = self._v2_headers(token)

        try:
            async with httpx.AsyncClient(
                timeout=self.manifest_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.delete(delete_url, headers=headers)
                if resp.status_code in (200, 202):
                    return {"success": True, "message": f"Tag '{tag}' deleted"}
                return {
                    "success": False,
                    "message": f"registry-1.docker.io returned HTTP {resp.status_code}",
                }

        except Exception as exc:
            logger.warning("delete_tag error repo=%s tag=%s: %s", repository, tag, exc)
            return {"success": False, "message": str(exc)}

    async def delete_repository(self, repository: str) -> str | None:
        """Delete a Docker Hub repository via the Hub REST API.

        Endpoint: DELETE https://hub.docker.com/v2/repositories/{namespace}/{name}/
        Auth:     JWT token from Hub login.

        Args:
            repository: Full repository name e.g. "myuser/myimage".

        Returns:
            None on success; error string on failure.
        """
        token = await self._get_hub_jwt()
        if not token:
            return "Docker Hub authentication failed. Check your credentials."

        headers = self._hub_auth_headers(token)

        if "/" in repository:
            namespace, name = repository.split("/", 1)
        else:
            namespace, name = self.username, repository

        try:
            async with httpx.AsyncClient(
                timeout=self.tags_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.delete(
                    f"{_HUB_API}/v2/repositories/{namespace}/{name}/",
                    headers=headers,
                )
                if resp.status_code in (200, 202, 204):
                    logger.info("delete_repository: deleted %s/%s", namespace, name)
                    return None
                if resp.status_code == 401:
                    return "Docker Hub authentication failed."
                if resp.status_code == 403:
                    return f"Permission denied: cannot delete '{repository}' on Docker Hub."
                if resp.status_code == 404:
                    return f"Repository '{repository}' not found on Docker Hub."
                return f"Docker Hub API returned HTTP {resp.status_code}."

        except Exception as exc:
            logger.warning("delete_repository error repo=%s: %s", repository, exc)
            return f"Delete failed: {exc}"
