"""Portalcrane - Github REST API Service."""

import asyncio
from typing import Any
import httpx

# ── GitHub Packages browse (GHCR-specific) ────────────────────────────────────

_GITHUB_API = "https://api.github.com"


def get_urls(owner: str) -> list[str]:
    """Return urls for GitHub user or organisation."""
    return [
        f"{_GITHUB_API}/users/{owner}/packages",
        f"{_GITHUB_API}/orgs/{owner}/packages",
    ]


def get_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }


async def browse_github_packages(
    username: str,
    token: str,
    owner: str,
    search: str | None,
    page: int,
    page_size: int,
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
    headers = get_headers(token)

    # Try user packages first, fall back to org packages
    urls_to_try = get_urls(owner=owner)

    repositories: list[str] = []
    last_error: str | None = None

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in urls_to_try:
            try:
                params = {"package_type": "container", "per_page": 100}
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    packages = resp.json()
                    repositories = [
                        f"{owner}/{pkg['name']}"
                        for pkg in packages
                        if isinstance(pkg, dict) and "name" in pkg
                    ]
                    last_error = None
                    break
                elif resp.status_code == 404:
                    # Not a user, try org endpoint
                    continue
                else:
                    last_error = f"GitHub API returned HTTP {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)

    if last_error and not repositories:
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "error": last_error,
        }

    # Apply search filter
    if search:
        repositories = [r for r in repositories if search.lower() in r.lower()]

    total = len(repositories)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_repos = repositories[start : start + page_size]

    # Fetch tags via GitHub API for each package
    async def _fetch_github_tags(repo: str) -> list[str]:
        """Fetch versions/tags for a GitHub package."""
        pkg_name = repo.split("/", 1)[-1]
        tag_url = f"{_GITHUB_API}/users/{owner}/packages/container/{pkg_name}/versions"
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get(tag_url, headers=headers)
                if r.status_code == 200:
                    versions = r.json()
                    tags = []
                    for v in versions:
                        meta = v.get("metadata", {}).get("container", {})
                        tags.extend(meta.get("tags", []))
                    return tags
        except Exception:
            pass
        return []

    tags_results = await asyncio.gather(*[_fetch_github_tags(r) for r in page_repos])

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


async def delete_github_package(
    token: str,
    owner: str,
    package: str,
) -> str | None:
    """Delete package for a GitHub user or organisation."""
    headers = get_headers(token)
    last_error: str | None = None

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in get_urls(owner):
            try:
                full_url = f"${url}/container/${package}"
                resp = await client.delete(full_url, headers=headers)
                if resp.status_code == 200:
                    last_error = None
                    break
                elif resp.status_code == 404:
                    # Not a user, try org endpoint
                    continue
                else:
                    last_error = f"GitHub API returned HTTP {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)

    return last_error


async def browse_github_tag(
    token: str,
    owner: str,
    package: str,
):
    """Get package version for a GitHub user or organisation."""
    headers = get_headers(token)
    last_error: str | None = None

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in get_urls(owner):
            try:
                tag_url = f"${url}/container/${package}/versions"
                resp = await client.get(tag_url, headers=headers)
                if resp.status_code == 200:
                    versions = resp.json()
                    tags = []
                    for v in versions:
                        meta = v.get("metadata", {}).get("container", {})
                        tags.extend(meta.get("tags", []))
                    return tags
                elif resp.status_code == 404:
                    # Not a user, try org endpoint
                    continue
                else:
                    last_error = f"GitHub API returned HTTP {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)

    if last_error:
        return {"items": [], "error": last_error}


async def get_github_tag(
    token: str, owner: str, package: str, version_id: str
) -> dict[str, Any]:
    """Get a version package for a GitHub user or organisation."""
    headers = get_headers(token)
    last_error: str | None = None

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in get_urls(owner):
            try:
                version_url = f"${url}/container/${package}/versions/${version_id}"
                resp = await client.get(version_url, headers=headers)
                if resp.status_code == 200:
                    version = resp.json()
                    last_error = None
                    return version
                elif resp.status_code == 404:
                    # Not a user, try org endpoint
                    continue
                else:
                    last_error = f"GitHub API returned HTTP {resp.status_code}"
            except Exception as exc:
                last_error = str(exc)

    return {"items": [], "error": last_error}
