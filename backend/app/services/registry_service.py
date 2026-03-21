"""Portalcrane - Internal Registry Service.

High-level service for the embedded Docker Registry (OCI Distribution v2).

Design: composition over inheritance.
RegistryService holds a V2Provider instance (_v2) and delegates all
low-level HTTP calls to it.  This keeps a clear "has-a" relationship:
  - RegistryService  → orchestrates business logic for the internal registry
  - V2Provider       → handles raw OCI Distribution v2 HTTP calls

Delegation strategy:
  Every method in RegistryService that maps 1-to-1 with a V2Provider method
  is a thin wrapper.  Only operations that are unique to the internal registry
  (aggregate statistics, empty-repository listing with filesystem cleanup,
  registry ping) add logic here.

No V2 HTTP calls are duplicated between this class and V2Provider.
"""

import asyncio
import logging

from ..config import Settings, REGISTRY_URL, PROXY_TIMEOUT
from ..services.providers.external_v2 import V2Provider

logger = logging.getLogger(__name__)


class RegistryService:
    """Async client for the embedded Portalcrane Docker Registry.

    Uses a V2Provider instance internally to handle all OCI Distribution v2
    HTTP calls.  Adds aggregate statistics methods specific to the internal
    registry context.

    Args:
        settings: Application settings — stored for future use by callers.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # Internal V2Provider instance configured for the embedded registry.
        # The registry runs on the local network — no proxy, plain HTTP.
        # PROXY_TIMEOUT replaces the default 30 s for long-running operations.
        self._v2 = V2Provider(
            host=REGISTRY_URL,
            username="",
            password="",
            use_tls=REGISTRY_URL.startswith("https://"),
            tls_verify=True,
            timeout=PROXY_TIMEOUT,
        )

        # Expose base_url for callers that need it (e.g. router for logging).
        self.base_url = self._v2.base_url

    # ── Connectivity ──────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Check registry connectivity."""
        return await self._v2.ping()

    # ── Repository listing ────────────────────────────────────────────────────

    async def list_repositories(
        self, n: int = 1000, last: str = "", include_empty: bool = False
    ) -> list[str]:
        """List all repositories.

        Delegates directly to V2Provider.list_repositories() — single source.

        Args:
            n:             Maximum repositories per catalog request.
            last:          Pagination cursor.
            include_empty: When True, include repositories with no tags.
        """
        return await self._v2.list_repositories(
            n=n, last=last, include_empty=include_empty
        )

    async def list_empty_repositories(self) -> list[str]:
        """Return repositories that have no tags (ghost entries)."""
        return await self._v2.list_empty_repositories()

    # ── Tag operations ────────────────────────────────────────────────────────

    async def list_tags(self, repository: str) -> list[str]:
        """List all tags for a repository."""
        return await self._v2.browse_tags(repository)

    async def get_tag_detail(self, repository: str, tag: str) -> dict:
        """Fetch detailed metadata for a specific tag.

        Delegates to V2Provider.get_tag_detail() which builds on
        get_manifest() + get_image_config() — no duplication.
        """
        return await self._v2.get_tag_detail(repository, tag)

    async def add_tag(self, repository: str, source_tag: str, new_tag: str) -> dict:
        """Create a new tag by copying an existing manifest."""
        return await self._v2.add_tag(repository, source_tag, new_tag)

    async def delete_tag(self, repository: str, tag: str) -> bool:
        """Delete a specific tag (resolves the manifest digest first).

        Adapts the dict return of V2Provider.delete_tag() to the bool
        expected by the existing registry router.
        """
        result = await self._v2.delete_tag(repository, tag)
        return result.get("success", False)

    # ── Manifest operations ───────────────────────────────────────────────────

    async def get_manifest(self, repository: str, reference: str) -> dict:
        """Fetch a manifest by tag or digest."""
        return await self._v2.get_manifest(repository, reference)

    async def get_image_config(self, repository: str, digest: str) -> dict:
        """Fetch an image configuration blob."""
        return await self._v2.get_image_config(repository, digest)

    async def delete_manifest(self, repository: str, digest: str) -> bool:
        """Delete an image manifest by digest."""
        return await self._v2.delete_manifest(repository, digest)

    async def put_manifest(
        self,
        repository: str,
        reference: str,
        manifest: dict,
        content_type: str,
    ) -> bool:
        """Push a manifest to create or update a tag."""
        return await self._v2.put_manifest(
            repository, reference, manifest, content_type
        )

    # ── Internal-registry-specific statistics ─────────────────────────────────

    async def _get_repo_stats(self, repo: str) -> dict:
        """Fetch all tag sizes for a single repository in parallel.

        Returns a summary dict:
          - repo (str)      : repository name
          - tags (list)     : all tag names
          - total_size (int): sum of all tag sizes in bytes
          - largest (dict)  : {"name": "repo:tag", "size": int}
        """
        tags = await self.list_tags(repo)
        if not tags:
            return {
                "repo": repo,
                "tags": [],
                "total_size": 0,
                "largest": {"name": "", "size": 0},
            }

        # Fetch all tag sizes concurrently using V2Provider.get_image_size()
        sizes: list[int] = await asyncio.gather(
            *[self._v2.get_image_size(repo, tag) for tag in tags],
            return_exceptions=False,
        )

        total_size = sum(sizes)

        largest_size = 0
        largest_name = ""
        for tag, size in zip(tags, sizes):
            if size > largest_size:
                largest_size = size
                largest_name = f"{repo}:{tag}"

        return {
            "repo": repo,
            "tags": tags,
            "total_size": total_size,
            "largest": {"name": largest_name, "size": largest_size},
        }

    async def get_registry_stats(self) -> dict:
        """Compute registry-wide statistics.

        All repositories are queried concurrently and within each repository
        all tag sizes are fetched concurrently, reducing wall-clock time from
        O(repos × tags) to approximately O(max_tags_per_repo).

        Returns:
            dict with keys:
                total_images (int)     : number of non-empty repositories
                total_tags (int)       : total number of tags across all repos
                total_size_bytes (int) : combined size of all images in bytes
                largest_image (dict)   : {"name": str, "size": int}
        """
        repositories = await self.list_repositories()

        if not repositories:
            return {
                "total_images": 0,
                "total_tags": 0,
                "total_size_bytes": 0,
                "largest_image": {"name": "", "size": 0},
            }

        repo_results: list[dict] = await asyncio.gather(
            *[self._get_repo_stats(repo) for repo in repositories],
            return_exceptions=False,
        )

        total_size = 0
        total_tags = 0
        largest_image: dict = {"name": "", "size": 0}

        for result in repo_results:
            total_size += result["total_size"]
            total_tags += len(result["tags"])

            if result["largest"]["size"] > largest_image["size"]:
                largest_image = result["largest"]

        return {
            "total_images": len(repositories),
            "total_tags": total_tags,
            "total_size_bytes": total_size,
            "largest_image": largest_image,
        }
