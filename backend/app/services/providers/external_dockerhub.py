"""Portalcrane - Docker Hub REST API Service."""

import asyncio
import logging
from typing import Any

import httpx
from .base import BaseRegistryProvider

logger = logging.getLogger(__name__)

_HUB_API = "https://hub.docker.com/v2"
_HUB_AUTH_URL = f"{_HUB_API}/users/login"
_PAGE_SIZE_MAX = 100  # Docker Hub maximum page size for repository listing


class DockerHubProvider(BaseRegistryProvider):
    """Docker Hub provider."""

    host: str
    username: str
    password: str
    use_tls: bool = True
    tls_verify: bool = True

    def __init__(self):
        """Initialize."""
        self.verify = False if not self.use_tls else self.tls_verify

    @property
    def provider_name(self) -> str:
        return "dockerhub"

    async def _get_token(self) -> str | None:
        """
        Obtain a short-lived JWT from the Docker Hub login endpoint.

        Returns the token string on success, or None when authentication fails.
        The token is valid for ~300 s; it is not cached here because each browse
        request is stateless and the latency is negligible relative to the
        subsequent API calls.
        """
        try:
            async with httpx.AsyncClient(
                timeout=15, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.post(
                    _HUB_AUTH_URL,
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

    def _auth_headers(self, token: str) -> dict[str, str]:
        """Return HTTP headers for an authenticated Docker Hub API request."""
        return {
            "Authorization": f"JWT {token}",
            "Content-Type": "application/json",
        }

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
                timeout=15, verify=self.verify, follow_redirects=True
            ) as client:
                cred_resp = await client.post(_HUB_AUTH_URL, json=auth)
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

    async def browse_repositories(
        self,
        namespace: str,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        List container repositories for a Docker Hub namespace (user or organisation).

        Uses GET /v2/repositories/{namespace}/?page={page}&page_size={page_size}.

        The `namespace` stored in the registry entry corresponds to the Docker Hub
        username or organisation name.  When the registry was created with
        username=john, namespace defaults to john.

        Args:
            username:  Docker Hub account username (used for authentication).
            password:  Docker Hub account password or access token.
            namespace: Docker Hub namespace to browse (user or org name).
            search:    Optional substring filter applied client-side on repo name.
            page:      1-based page number.
            page_size: Number of items per page.

        Returns:
            Paginated dict compatible with ExternalPaginatedImages:
            { items, total, page, page_size, total_pages, error }
        """
        token = await self._get_token()
        if not token:
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "error": "Docker Hub authentication failed. Check your credentials.",
            }

        headers = self._auth_headers(token)
        repositories: list[str] = []

        try:
            async with httpx.AsyncClient(
                timeout=20, verify=self.verify, follow_redirects=True
            ) as client:
                # The Hub API supports server-side search via the `name` query param
                params: dict[str, Any] = {
                    "page": page,
                    "page_size": min(page_size, _PAGE_SIZE_MAX),
                }
                if search:
                    params["name"] = search

                resp = await client.get(
                    f"{_HUB_API}/repositories/{namespace}/",
                    headers=headers,
                    params=params,
                )

                if resp.status_code == 401:
                    return {
                        "items": [],
                        "total": 0,
                        "page": page,
                        "page_size": page_size,
                        "total_pages": 1,
                        "error": "Docker Hub authentication failed. Check your credentials.",
                    }

                if resp.status_code == 404:
                    return {
                        "items": [],
                        "total": 0,
                        "page": page,
                        "page_size": page_size,
                        "total_pages": 1,
                        "error": f"Docker Hub namespace '{namespace}' not found.",
                    }

                resp.raise_for_status()
                data = resp.json()
                packages = data.get("results", {})
                repositories = [
                    f"{self.username}/{pkg['name']}"
                    for pkg in packages
                    if isinstance(pkg, dict) and "name" in pkg
                ]

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "browse_dockerhub_repositories HTTP error namespace=%s: %s",
                namespace,
                exc,
            )
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "error": f"Docker Hub API error: {exc.response.status_code}",
            }
        except Exception as exc:
            logger.warning(
                "browse_dockerhub_repositories error namespace=%s: %s", namespace, exc
            )
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 1,
                "error": "Docker Hub API unreachable.",
            }

        # Apply search filter
        if search:
            repositories = [r for r in repositories if search.lower() in r.lower()]

        # ── Build paginated response ───────────────────────────────────────────
        total = len(repositories)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        page_repos = repositories[start : start + page_size]

        # Fetch tags for each package
        async def _fetch_tags(repo: str) -> list[str]:
            """Fetch versions/tags for a GitHub package."""
            try:
                return await self.browse_tags(repo)
            except Exception:
                pass
            return []

        tags_results = await asyncio.gather(*[_fetch_tags(r) for r in page_repos])

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
        token = await self._get_token()
        if not token:
            logger.warning("browse_dockerhub_tags: auth failed for repo=%s", repository)
            return []

        headers = self._auth_headers(token)

        # Normalise namespace/name split
        if "/" in repository:
            namespace, name = repository.split("/", 1)
        else:
            namespace, name = "library", repository

        tags: list[str] = []
        url: str | None = f"{_HUB_API}/repositories/{namespace}/{name}/tags/"

        try:
            async with httpx.AsyncClient(
                timeout=20, verify=self.verify, follow_redirects=True
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

    async def delete_repository(self, repository: str) -> str | None:
        """
        Delete a Docker Hub repository via the Hub REST API.

        Requires the account to be the owner of the repository, or have admin
        access to the organisation that owns it.

        Args:
            username:   Docker Hub account username.
            password:   Docker Hub account password or access token.
            repository: Full repository reference, e.g. "myuser/myimage".

        Returns:
            None on success, or an error string on failure.
        """
        token = await self._get_token()
        if not token:
            return "Docker Hub authentication failed. Check your credentials."

        headers = self._auth_headers(token)

        if "/" in repository:
            namespace, name = repository.split("/", 1)
        else:
            namespace, name = self.username, repository

        try:
            async with httpx.AsyncClient(
                timeout=20, verify=self.verify, follow_redirects=True
            ) as client:
                resp = await client.delete(
                    f"{_HUB_API}/repositories/{namespace}/{name}/",
                    headers=headers,
                )
                if resp.status_code in (200, 202, 204):
                    logger.info(
                        "delete_dockerhub_repository: deleted %s/%s", namespace, name
                    )
                    return None  # success
                if resp.status_code == 401:
                    return "Docker Hub authentication failed."
                if resp.status_code == 403:
                    return f"Permission denied: cannot delete '{repository}' on Docker Hub."
                if resp.status_code == 404:
                    return f"Repository '{repository}' not found on Docker Hub."
                return f"Docker Hub API returned HTTP {resp.status_code}."
        except Exception as exc:
            logger.warning(
                "delete_dockerhub_repository error repo=%s: %s", repository, exc
            )
            return f"Delete failed: {exc}"
