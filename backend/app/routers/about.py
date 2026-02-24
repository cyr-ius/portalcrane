"""
Portalcrane - About Router
Provides application metadata: current version, latest GitHub release, author and AI credits.
"""

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import (
    APP_AI_GENERATOR,
    APP_AUTHOR,
    GITHUB_LATEST_RELEASE_URL,
    GITHUB_REPO_URL,
    Settings,
    get_settings,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _strip_v(tag: str) -> str:
    """Remove a leading 'v' from a semver tag (e.g. 'v1.2.3' → '1.2.3')."""
    return tag.lstrip("v")


class AboutResponse(BaseModel):
    current_version: str
    latest_version: str | None
    update_available: bool
    author: str
    ai_generator: str
    github_url: str
    github_error: str | None


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.get("/about", response_model=AboutResponse)
async def get_about(settings: Settings = Depends(get_settings)) -> dict:
    """
    Return application metadata and check GitHub for a newer release.

    Response fields:
    - current_version   : version running in this container (from APP_VERSION env var)
    - latest_version    : latest published GitHub release tag (None on error)
    - update_available  : True when latest_version > current_version (string compare)
    - author            : project author GitHub handle
    - ai_generator      : AI tool used to generate the code
    - github_url        : link to the GitHub repository
    - github_error      : error message when the GitHub check fails (None otherwise)
    """
    current_version = settings.app_version  # e.g. "1.2.0" from APP_VERSION env var

    latest_version: str | None = None
    github_error: str | None = None
    update_available = False

    # ── Query the GitHub Releases API ─────────────────────────────────────────
    proxy = getattr(settings, "httpx_proxy", None)
    try:
        async with httpx.AsyncClient(
            proxy=proxy, timeout=settings.httpx_timeout
        ) as client:
            resp = await client.get(
                GITHUB_LATEST_RELEASE_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            data = resp.json()
            latest_version = _strip_v(data.get("tag_name", ""))
    except httpx.HTTPStatusError as exc:
        github_error = f"GitHub API error: HTTP {exc.response.status_code}"
    except httpx.RequestError as exc:
        github_error = f"GitHub API unreachable: {exc}"
    except Exception as exc:  # noqa: BLE001
        github_error = f"Unexpected error: {exc}"

    # ── Compare versions (simple string comparison works for semver x.y.z) ───
    if latest_version and current_version:
        try:
            # Parse each segment as an integer for correct numeric comparison
            current_parts = tuple(int(x) for x in current_version.split("."))
            latest_parts = tuple(int(x) for x in latest_version.split("."))
            update_available = latest_parts > current_parts
        except ValueError:
            # Fall back to string comparison if parsing fails
            update_available = latest_version != current_version

    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "author": APP_AUTHOR,
        "ai_generator": APP_AI_GENERATOR,
        "github_url": GITHUB_REPO_URL,
        "github_error": github_error,
    }
