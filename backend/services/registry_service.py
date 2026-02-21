"""
Portalcrane - Registry Service
Async service layer for Docker Registry API (OCI Distribution Spec)
"""

import httpx
from ..config import Settings


class RegistryService:
    """Async client for Docker Registry HTTP API v2."""

    def __init__(self, settings: Settings):
        self.base_url = settings.registry_url.rstrip("/")
        self.auth = None
        if settings.registry_username and settings.registry_password:
            self.auth = (settings.registry_username, settings.registry_password)
        self._proxies = settings.httpx_proxy

    def _client(self) -> httpx.AsyncClient:
        """Create authenticated async HTTP client."""
        headers = {
            "Accept": "application/vnd.docker.distribution.manifest.v2+json, application/json"
        }
        return httpx.AsyncClient(
            auth=self.auth,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
            # No proxy for registry — it runs on the internal network
        )

    async def ping(self) -> bool:
        """Check registry connectivity."""
        try:
            async with self._client() as client:
                response = await client.get(f"{self.base_url}/v2/")
                return response.status_code in (200, 401)
        except Exception:
            return False

    async def list_repositories(
        self, n: int = 1000, last: str = "", include_empty: bool = False
    ) -> list[str]:
        """List all repositories in the registry.

        By default, repositories with no tags are excluded — they are ghost entries
        left behind after all tags were deleted. The registry catalog API has no way
        to remove them; only a GC + filesystem cleanup can do so.
        """
        url = f"{self.base_url}/v2/_catalog?n={n}"
        if last:
            url += f"&last={last}"

        async with self._client() as client:
            response = await client.get(url)
            response.raise_for_status()
            repositories = response.json().get("repositories", [])

        if include_empty:
            return repositories

        # Filter out repositories that have no tags (ghost repos after tag deletion)
        non_empty = []
        for repo in repositories:
            tags = await self.list_tags(repo)
            if tags:
                non_empty.append(repo)
        return non_empty

    async def list_empty_repositories(self) -> list[str]:
        """Return repositories that have no tags (ghost entries)."""
        url = f"{self.base_url}/v2/_catalog?n=1000"
        async with self._client() as client:
            response = await client.get(url)
            response.raise_for_status()
            all_repos = response.json().get("repositories", [])

        empty = []
        for repo in all_repos:
            tags = await self.list_tags(repo)
            if not tags:
                empty.append(repo)
        return empty

    async def list_tags(self, repository: str) -> list[str]:
        """List all tags for a repository."""
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/v2/{repository}/tags/list")
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return response.json().get("tags", []) or []

    async def get_manifest(self, repository: str, reference: str) -> dict:
        """Get image manifest for a repository:tag or digest."""
        async with self._client() as client:
            headers = {
                "Accept": (
                    "application/vnd.docker.distribution.manifest.v2+json,"
                    "application/vnd.docker.distribution.manifest.list.v2+json,"
                    "application/vnd.oci.image.manifest.v1+json,"
                    "application/vnd.oci.image.index.v1+json"
                )
            }
            response = await client.get(
                f"{self.base_url}/v2/{repository}/manifests/{reference}",
                headers=headers,
            )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            digest = response.headers.get("Docker-Content-Digest", "")
            manifest = response.json()
            manifest["_digest"] = digest
            manifest["_content_length"] = int(response.headers.get("Content-Length", 0))
            return manifest

    async def get_image_config(self, repository: str, digest: str) -> dict:
        """Get image configuration blob (labels, env, created date, etc.)."""
        async with self._client() as client:
            response = await client.get(
                f"{self.base_url}/v2/{repository}/blobs/{digest}"
            )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def get_image_size(self, repository: str, tag: str) -> int:
        """Calculate total image size in bytes from manifest layers."""
        manifest = await self.get_manifest(repository, tag)
        if not manifest:
            return 0

        total_size = 0

        # Handle manifest list (multi-arch)
        if manifest.get("mediaType") in (
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.index.v1+json",
        ):
            # Use first manifest
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

    async def delete_manifest(self, repository: str, digest: str) -> bool:
        """Delete an image manifest by digest (deletes the image/tag)."""
        async with self._client() as client:
            response = await client.delete(
                f"{self.base_url}/v2/{repository}/manifests/{digest}"
            )
            return response.status_code in (200, 202)

    async def delete_tag(self, repository: str, tag: str) -> bool:
        """Delete a specific tag by getting its digest first."""
        manifest = await self.get_manifest(repository, tag)
        digest = manifest.get("_digest")
        if not digest:
            return False
        return await self.delete_manifest(repository, digest)

    async def put_manifest(
        self, repository: str, reference: str, manifest: dict, content_type: str
    ) -> bool:
        """Push a manifest to create/update a tag."""
        import json

        async with self._client() as client:
            headers = {"Content-Type": content_type}
            response = await client.put(
                f"{self.base_url}/v2/{repository}/manifests/{reference}",
                content=json.dumps(manifest),
                headers=headers,
            )
            return response.status_code in (200, 201)

    async def get_registry_stats(self) -> dict:
        """Compute registry-wide statistics."""
        repositories = await self.list_repositories()
        total_images = len(repositories)
        total_size = 0
        largest_image = {"name": "", "size": 0}
        tag_count = 0

        for repo in repositories:
            tags = await self.list_tags(repo)
            tag_count += len(tags)
            for tag in tags:
                size = await self.get_image_size(repo, tag)
                total_size += size
                if size > largest_image["size"]:
                    largest_image = {"name": f"{repo}:{tag}", "size": size}

        return {
            "total_images": total_images,
            "total_tags": tag_count,
            "total_size_bytes": total_size,
            "largest_image": largest_image,
        }
