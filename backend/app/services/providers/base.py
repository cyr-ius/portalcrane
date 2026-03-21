"""Portalcrane - Abstract Base Provider for External Registries.

Defines the contract that every registry provider MUST implement.
All three concrete providers (V2Provider, GithubProvider, DockerHubProvider)
must inherit from this class and implement its abstract methods.

Provider hierarchy:
    BaseRegistryProvider  (abstract — this file)
    ├── V2Provider        (OCI Distribution / Docker V2 spec)
    ├── GithubProvider    (GitHub Container Registry — Packages REST API)
    └── DockerHubProvider (Docker Hub — Hub REST API)
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseRegistryProvider(ABC):
    """Abstract base class for all external registry providers.

    Provider hierarchy:
        BaseRegistryProvider  (abstract — this file)
        ├── V2Provider        (OCI Distribution / Docker V2 spec)
        ├── GithubProvider    (GitHub Container Registry — Packages REST API)
        └── DockerHubProvider (Docker Hub — Hub REST API)
    """

    # Default timeouts (seconds)
    probe_timeout = 10.0
    catalog_timeout = 20.0
    tags_timeout = 15.0
    manifest_timeout = 20.0

    def __init__(
        self,
        host: str,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        tls_verify: bool = True,
    ) -> None:
        """Initialize credentials and resolve the httpx verify parameter.

        Args:
            host:       Registry hostname, with or without scheme.
            username:   Registry username (may be empty for anonymous access).
            password:   Registry password or access token.
            use_tls:    When True (default) use HTTPS, otherwise plain HTTP.
            tls_verify: When True (default) validate TLS certificates.
        """
        self.host = host
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.tls_verify = tls_verify
        self.verify: bool = False if not self.use_tls else self.tls_verify

    # ── URL / client helpers ──────────────────────────────────────────────────

    def _build_base_url(self) -> str:
        """Build the base HTTPS/HTTP URL from the host field.

        When the host already carries a scheme (http:// or https://) it is
        preserved unchanged.  Otherwise the scheme is derived from use_tls.

        Returns:
            Base URL string without trailing slash.
        """
        if "://" in self.host:
            return self.host.rstrip("/")
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self.host}"

    # ── Abstract interface — every provider MUST implement these methods ───────

    @property
    def base_url(self) -> str:
        """Convenience property — returns the computed base URL."""
        return self._build_base_url()

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider name"""
        ...

    @abstractmethod
    def ping(self) -> bool:
        """Return True when the registry responds to the ping endpoint."""
        ...

    @abstractmethod
    async def test_connection(self) -> dict[str, Any]:
        """Probe the registry to check reachability and validate credentials.

        Must return a dict with exactly these keys:
            reachable (bool): True when the registry endpoint responded.
            auth_ok   (bool): True when credentials were accepted (or not needed).
            message   (str):  Human-readable status suitable for UI display.

        Implementations should catch httpx.ConnectError, httpx.TimeoutException
        and generic Exception and return reachable=False rather than raising.

        Returns:
            dict[str, Any]: {"reachable": bool, "auth_ok": bool, "message": str}
        """
        ...

    @abstractmethod
    async def check_catalog(self) -> bool:
        """Determine whether this registry supports repository listing.

        Returns:
            True  — the registry exposes a browsable catalog endpoint.
            False — catalog browsing is not supported or access was denied.
        """
        ...

    @abstractmethod
    async def browse_repositories(
        self,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """List repositories/images available in this registry.

        The return value must be a paginated dict compatible with the
        ExternalPaginatedImages frontend model:

            {
                "items":       list[dict],   # Each item has at least "name"
                "total":       int,
                "page":        int,
                "page_size":   int,
                "total_pages": int,
                "error":       str | None,   # Present only on partial failure
            }

        Args:
            search:    Optional substring filter on repository name.
            page:      1-based page number.
            page_size: Number of items per page.
            **kwargs:  Provider-specific parameters (e.g. namespace for DockerHub).

        Returns:
            dict[str, Any]: Paginated repository list.
        """
        ...

    @abstractmethod
    async def browse_tags(self, repository: str) -> list[str] | dict[str, Any]:
        """List tags available for a repository.

        Args:
            repository: Repository path, e.g. "myorg/myimage".

        Returns:
            list[str] on success — tag names in no particular order.
            dict[str, Any] on error — {"error": str, "tags": []}.
        """
        ...

    @abstractmethod
    async def delete_repository(self, repository: str) -> str | None:
        """Delete all tags / a complete repository from this registry.

        Args:
            repository: Repository path, e.g. "myorg/myimage".

        Returns:
            None    — operation succeeded.
            str     — error message when the operation failed.
        """
        ...

    @abstractmethod
    async def get_tags_for_import(self, repository: str) -> list[str]:
        """Retrieve tag names for a repository, used by import jobs.

        Unlike browse_tags() this method ALWAYS returns a plain list[str],
        never a dict, so import job loops can iterate safely without type checks.

        Args:
            repository: Repository path, e.g. "myorg/myimage".

        Returns:
            list[str]: Tag names; empty list on error.
        """
        ...

    async def get_tag_detail(self, repository: str, tag: str) -> dict[str, Any]:
        """Return detailed metadata for a specific tag.

        Default implementation returns an empty dict, meaning the operation is
        not supported by this provider.  V2-compatible registries override this
        to return the full ImageDetail payload.

        Args:
            repository: Repository path.
            tag:        Tag name, e.g. "latest".

        Returns:
            dict[str, Any]: ImageDetail payload, or {} if unsupported / not found.
        """
        logger.debug(
            "%s.get_tag_detail: not supported for this provider type",
            self.__class__.__name__,
        )
        return {}

    async def delete_tag(self, repository: str, tag: str) -> dict[str, Any]:
        """Delete a single tag from a repository.

        Default implementation returns an unsupported error.  Providers that
        support per-tag deletion (V2-spec registries) override this method.

        Args:
            repository: Repository path.
            tag:        Tag name to delete.

        Returns:
            dict[str, Any]: {"success": bool, "message": str}
        """
        logger.debug(
            "%s.delete_tag: not supported for this provider type",
            self.__class__.__name__,
        )
        return {
            "success": False,
            "message": "Tag deletion is not supported for this registry type",
        }

    async def add_tag(
        self, repository: str, source_tag: str, new_tag: str
    ) -> dict[str, Any]:
        """Create a new tag by copying an existing manifest (client-side retag).

        Default implementation returns an unsupported error.  V2 providers that
        support manifest PUT override this method.

        Args:
            repository: Repository path.
            source_tag: Existing tag whose manifest will be copied.
            new_tag:    New tag name to create.

        Returns:
            dict[str, Any]: {"success": bool, "message": str}
        """
        logger.debug(
            "%s.add_tag: not supported for this provider type",
            self.__class__.__name__,
        )
        return {
            "success": False,
            "message": "Tag creation is not supported for this registry type",
        }

    def _log_prefix(self) -> str:
        """Return a log-friendly provider identifier for debug messages."""
        return f"{self.__class__.__name__}(host={self.host!r})"
