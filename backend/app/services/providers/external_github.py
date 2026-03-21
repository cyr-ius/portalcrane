"""Portalcrane - Github REST API Service."""

import asyncio
import logging
from typing import Any

import httpx

from .base import BaseRegistryProvider

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"

_DEFAULT_TIMEOUT = 30.0


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
            host=_GITHUB_API,
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

    def _auth_headers(self) -> dict[str, str]:
        """Return standard GitHub API request headers with Bearer authentication."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }

    def _get_urls(self) -> list[str]:
        """Return urls for GitHub user or organisation."""
        return [
            f"{self.base_url}/users/{self.owner}/packages",
            f"{self.base_url}/orgs/{self.owner}/packages",
        ]

    async def ping(self) -> bool:
        """Return True when the registry responds to the /v2/ ping endpoint."""
        try:
            async with httpx.AsyncClient(
                timeout=self.probe_timeout, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.get(f"{self.base_url}/octocat/")
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
                    f"{self.base_url}/octocat/", headers=self._auth_headers()
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
        headers = self._auth_headers()
        urls_to_try = self._get_urls()
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
        headers = self._auth_headers()
        async with httpx.AsyncClient(
            timeout=self.tags_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            for base_url in self._get_urls():
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
        headers = self._auth_headers()
        last_error: str | None = None

        async with httpx.AsyncClient(
            timeout=self.tags_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            for base_url in self._get_urls():
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

    async def delete_repository(self, repository: str) -> str | None:
        """
        Delete a container package for a GitHub user or organisation.

        Tries the user endpoint first, then the org endpoint.
        Returns None on success, or an error string on failure.
        """
        headers = self._auth_headers()
        last_error: str | None = None
        repository = repository.split("/", 1)[-1] if "/" in repository else repository

        async with httpx.AsyncClient(
            timeout=self.manifest_timeout, verify=self.verify, follow_redirects=True
        ) as client:
            for base_url in self._get_urls():
                try:
                    full_url = f"{base_url}/container/{repository}"
                    resp = await client.delete(full_url, headers=headers)
                    if resp.status_code in (200, 204):
                        return None  # success
                    elif resp.status_code == 404:
                        # Not a user, try org endpoint
                        continue
                    else:
                        last_error = f"GitHub API returned HTTP {resp.status_code}"
                except Exception as exc:
                    last_error = str(exc)

        return last_error
