"""Portalcrane - Docker Hub REST API Service.

Provides browse, tag listing, and delete operations against the Docker Hub
REST API (hub.docker.com/v2).  This module is the Docker Hub counterpart of
external_github.py: it is called by external_registry_service.py whenever the
target registry host resolves to docker.io / index.docker.io.

Docker Hub does not expose /v2/_catalog, so the standard catalog-based
browse path is replaced by the Hub REST API:
  - GET /v2/repositories/{namespace}/          → list repositories
  - GET /v2/repositories/{namespace}/{name}/tags/ → list tags
  - DELETE /v2/repositories/{namespace}/{name}/ → delete repository

Authentication uses a JWT token obtained from hub.docker.com/v2/users/login.
The stored username and password are used to authenticate each session.
"""

import logging
from typing import Any

import httpx

# ── Constants ─────────────────────────────────────────────────────────────────

_HUB_API = "https://hub.docker.com/v2"
_HUB_AUTH_URL = f"{_HUB_API}/users/login"
_PAGE_SIZE_MAX = 100  # Docker Hub maximum page size for repository listing

logger = logging.getLogger(__name__)


# ── Authentication ────────────────────────────────────────────────────────────


async def _get_hub_token(username: str, password: str) -> str | None:
    """
    Obtain a short-lived JWT from the Docker Hub login endpoint.

    Returns the token string on success, or None when authentication fails.
    The token is valid for ~300 s; it is not cached here because each browse
    request is stateless and the latency is negligible relative to the
    subsequent API calls.
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.post(
                _HUB_AUTH_URL,
                json={"username": username, "password": password},
            )
            if resp.status_code == 200:
                return resp.json().get("token")
            logger.warning(
                "Docker Hub login failed for user=%s status=%s",
                username,
                resp.status_code,
            )
    except Exception as exc:
        logger.warning("Docker Hub login error user=%s: %s", username, exc)
    return None


def _auth_headers(token: str) -> dict[str, str]:
    """Return HTTP headers for an authenticated Docker Hub API request."""
    return {
        "Authorization": f"JWT {token}",
        "Content-Type": "application/json",
    }


# ── Browse repositories ───────────────────────────────────────────────────────


async def browse_dockerhub_repositories(
    username: str,
    password: str,
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
    token = await _get_hub_token(username, password)
    if not token:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": "Docker Hub authentication failed. Check your credentials.",
        }

    headers = _auth_headers(token)

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
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

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "browse_dockerhub_repositories HTTP error namespace=%s: %s", namespace, exc
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

    # ── Build paginated response ───────────────────────────────────────────
    raw_results: list[dict] = data.get("results") or []
    total: int = data.get("count") or 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    items = [
        {
            # Full reference: namespace/name
            "name": f"{r.get('namespace', namespace)}/{r.get('name', '')}",
            # tags are fetched lazily via browse_dockerhub_tags
            "tags": [],
            "tag_count": r.get("tag_count") or 0,
            "total_size": r.get("full_size") or 0,
            # Docker Hub extras — useful for display
            "description": r.get("description") or "",
            "is_private": r.get("is_private", False),
            "star_count": r.get("star_count") or 0,
            "pull_count": r.get("pull_count") or 0,
        }
        for r in raw_results
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "error": None,
    }


# ── Tags ──────────────────────────────────────────────────────────────────────


async def browse_dockerhub_tags(
    username: str,
    password: str,
    repository: str,
) -> list[str]:
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
    token = await _get_hub_token(username, password)
    if not token:
        logger.warning("browse_dockerhub_tags: auth failed for repo=%s", repository)
        return []

    headers = _auth_headers(token)

    # Normalise namespace/name split
    if "/" in repository:
        namespace, name = repository.split("/", 1)
    else:
        namespace, name = "library", repository

    tags: list[str] = []
    url: str | None = f"{_HUB_API}/repositories/{namespace}/{name}/tags/"

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
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


async def get_dockerhub_tags_for_import(
    username: str,
    password: str,
    repository: str,
) -> list[str]:
    """
    Retrieve tag names for a Docker Hub repository, used by the import job.

    Thin wrapper around browse_dockerhub_tags that guarantees a list return.
    """
    return await browse_dockerhub_tags(username, password, repository)


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete_dockerhub_repository(
    username: str,
    password: str,
    repository: str,
) -> str | None:
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
    token = await _get_hub_token(username, password)
    if not token:
        return "Docker Hub authentication failed. Check your credentials."

    headers = _auth_headers(token)

    if "/" in repository:
        namespace, name = repository.split("/", 1)
    else:
        namespace, name = username, repository

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
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
        logger.warning("delete_dockerhub_repository error repo=%s: %s", repository, exc)
        return f"Delete failed: {exc}"
