"""
Portalcrane - Auth cookie helpers
Carry the session JWT in an HttpOnly cookie instead of localStorage.

Storing the token in an HttpOnly, SameSite=Lax cookie keeps it out of reach of
JavaScript (no `localStorage`), so an XSS flaw can no longer exfiltrate the
session token. SameSite=Lax still lets the cookie ride top-level navigations
(needed for the OIDC redirect) while blocking it on cross-site state-changing
requests, which mitigates CSRF for mutations.

The Bearer-token path is kept in core/jwt.py for API clients (Swagger, scripts);
these helpers only manage the browser session cookie.
"""

from fastapi import Request, Response

from ..config import get_settings


def _cookie_secure(request: Request) -> bool:
    """Set the ``Secure`` flag only when the request is actually over HTTPS.

    Honours ``X-Forwarded-Proto`` for TLS-terminating proxies so the cookie is
    marked secure in production, while staying usable on plain-HTTP local dev.
    """
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def set_auth_cookie(response: Response, request: Request, token: str) -> None:
    """Store the session JWT in the HttpOnly auth cookie."""
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Delete the auth cookie (used on logout)."""
    settings = get_settings()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        httponly=True,
        samesite="lax",
        path="/",
    )
