"""
Portalcrane - Authentication Router
Handles local admin authentication and OIDC flow
"""

from datetime import datetime, timedelta, timezone

pass  # typing import cleaned

import httpx
from config import Settings, get_settings
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


# ─── Models ──────────────────────────────────────────────────────────────────


class Token(BaseModel):
    """JWT token response model."""
    access_token: str
    token_type: str
    expires_in: int


class TokenData(BaseModel):
    """Decoded token data model."""
    username: str | None = None


class UserInfo(BaseModel):
    """Authenticated user information."""
    username: str
    is_admin: bool = True


class OIDCConfig(BaseModel):
    """OIDC provider configuration response."""
    enabled: bool
    client_id: str
    issuer: str
    redirect_uri: str
    authorization_endpoint: str = ""


class LoginRequest(BaseModel):
    """Local login request model."""
    username: str
    password: str


# ─── Helpers ─────────────────────────────────────────────────────────────────


def create_access_token(data: dict, settings: Settings) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> UserInfo:
    """Validate JWT token and return current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    return UserInfo(username=token_data.username, is_admin=True)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    settings: Settings = Depends(get_settings),
):
    """OAuth2 compatible token endpoint for local admin authentication."""
    if (
        form_data.username != settings.admin_username
        or form_data.password != settings.admin_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token({"sub": form_data.username}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=Token)
async def login(
    request: LoginRequest,
    settings: Settings = Depends(get_settings),
):
    """JSON login endpoint for local admin authentication."""
    if (
        request.username != settings.admin_username
        or request.password != settings.admin_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    access_token = create_access_token({"sub": request.username}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserInfo)
async def read_users_me(current_user: UserInfo = Depends(get_current_user)):
    """Return current authenticated user information."""
    return current_user


@router.get("/oidc-config", response_model=OIDCConfig)
async def get_oidc_config(settings: Settings = Depends(get_settings)):
    """Return OIDC configuration for the frontend."""
    if not settings.oidc_enabled:
        return OIDCConfig(
            enabled=False,
            client_id="",
            issuer="",
            redirect_uri="",
        )

    # Fetch OIDC discovery document
    authorization_endpoint = ""
    try:
        proxy = settings.httpx_proxy.get("https://") or None
        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.get(
                f"{settings.oidc_issuer}/.well-known/openid-configuration",
                timeout=5.0,
            )
            if response.status_code == 200:
                discovery = response.json()
                authorization_endpoint = discovery.get("authorization_endpoint", "")
    except Exception:
        pass

    return OIDCConfig(
        enabled=True,
        client_id=settings.oidc_client_id,
        issuer=settings.oidc_issuer,
        redirect_uri=settings.oidc_redirect_uri,
        authorization_endpoint=authorization_endpoint,
    )


@router.post("/oidc/callback", response_model=Token)
async def oidc_callback(
    code: str,
    settings: Settings = Depends(get_settings),
):
    """Handle OIDC authorization code callback and exchange for JWT."""
    if not settings.oidc_enabled:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")

    # Exchange authorization code for tokens
    try:
        proxy = settings.httpx_proxy.get("https://") or None
        async with httpx.AsyncClient(proxy=proxy) as client:
            # Get token endpoint from discovery
            discovery_response = await client.get(
                f"{settings.oidc_issuer}/.well-known/openid-configuration"
            )
            token_endpoint = discovery_response.json()["token_endpoint"]

            # Exchange code for tokens
            token_response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                },
            )
            tokens = token_response.json()

            if "error" in tokens:
                raise HTTPException(status_code=401, detail=tokens.get("error_description", "OIDC error"))

            # Decode ID token to get username
            id_token = tokens.get("id_token", "")
            payload = jwt.get_unverified_claims(id_token)
            username = payload.get("preferred_username") or payload.get("email") or payload.get("sub")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OIDC callback error: {str(e)}")

    access_token = create_access_token({"sub": username, "oidc": True}, settings)
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )
