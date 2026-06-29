# 🐍 FastAPI — Standards Portalcrane

Versions : FastAPI 0.135.1 · Python 3.14 · Pydantic v2 · async/await partout.

## 1. Structure de base

```python
"""Portalcrane - Docker Registry Management Application. Main FastAPI entry point."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown handler."""
    logger.info("Application starting up")
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title="Portalcrane API",
    description="Docker Registry Management API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## 2. Modèles Pydantic v2

Utiliser la syntaxe moderne : `str | None` (pas `Optional[str]`), `list[int]` (pas `List[int]`), `field_validator`, `model_config`.

```python
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import datetime


class UserCreate(BaseModel):
    """User creation request model."""

    username: str = Field(..., min_length=3, max_length=50, description="Username 3-50 chars")
    email: EmailStr = Field(..., description="Valid email address")
    password: str = Field(..., min_length=8, description="Password min 8 chars")
    is_admin: bool = Field(default=False, description="Admin privileges")

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        """Validate username contains only alphanumeric and underscores."""
        if not v.replace("_", "").isalnum():
            raise ValueError("Username must be alphanumeric with underscores")
        return v


class UserResponse(BaseModel):
    """User response model (excludes sensitive data)."""

    id: int
    username: str
    email: EmailStr
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RegistryResponse(BaseModel):
    """Registry with nested images."""

    id: int
    name: str
    description: str | None = None
    images: list[dict] = Field(default_factory=list)
```

## 3. Routes Asynchrones

Toutes les fonctions de route sont `async`. Injection de session via `Depends`.

```python
from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/users", tags=["Users"])


async def get_db() -> AsyncSession:
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@router.get("", response_model=list[UserResponse])
async def get_users(
    skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)
) -> list[UserResponse]:
    """Retrieve all users with pagination."""
    try:
        return await db.get_users(skip=skip, limit=limit)
    except Exception as exc:
        logger.error(f"Error fetching users: {exc}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to fetch users")


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)) -> UserResponse:
    """Get user by ID."""
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {user_id} not found")
    return user


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(user_data: UserCreate, db: AsyncSession = Depends(get_db)) -> UserResponse:
    """Create a new user."""
    if await db.get_user_by_username(user_data.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    try:
        return await db.create_user(user_data)
    except Exception as exc:
        logger.error(f"Error creating user: {exc}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to create user")


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """Delete a user by ID."""
    if not await db.get_user_by_id(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User {user_id} not found")
    await db.delete_user(user_id)
```

## 4. Services Asynchrones

Logique métier isolée dans des classes de service recevant la session DB.

```python
import logging

logger = logging.getLogger(__name__)


class RegistryService:
    """Service for Docker registry operations (async)."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def list_images(self, registry_id: int, limit: int = 100) -> list[dict]:
        """List all images in a registry.

        Raises:
            ValueError: If registry not found.
        """
        registry = await self.db.get_registry(registry_id)
        if not registry:
            raise ValueError(f"Registry {registry_id} not found")
        return await self.db.list_images(registry_id, limit=limit)

    async def scan_image_cves(self, registry_id: int, image_name: str) -> dict:
        """Scan image for CVE vulnerabilities.

        Raises:
            RuntimeError: If scan fails.
        """
        try:
            scan_result = await self._run_trivy_scan(registry_id, image_name)
            await self.db.save_scan_result(image_name, scan_result)
            return scan_result
        except Exception as exc:
            logger.error(f"CVE scan failed: {exc}")
            raise RuntimeError("Failed to scan image for vulnerabilities")

    async def _run_trivy_scan(self, registry_id: int, image_name: str) -> dict:
        """Execute Trivy vulnerability scan."""
        ...
```

## 5. Configuration (variables d'environnement)

Jamais de secret en dur. Tout via `BaseSettings`.

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables / .env."""

    app_title: str = "Portalcrane"
    debug: bool = False

    # Security — MUST be set in production
    secret_key: str
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "portalcrane"
    db_user: str = "postgres"
    db_password: str = "password"

    # Registry
    registry_url: str = "http://localhost:5000"
    registry_username: str | None = None
    registry_password: str | None = None

    # Vulnerability scanning
    vuln_scan_enabled: bool = True
    vuln_scan_severities: str = "CRITICAL,HIGH"
    vuln_scan_timeout: int = 300

    log_level: str = "INFO"
    cors_origins: str = "*"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "case_sensitive": False}

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",")]


settings = Settings()
```

Variables `.env` clés : `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `DB_*`, `REGISTRY_*`, `VULN_SCAN_*`, `LOG_LEVEL`, `CORS_ORIGINS`.

## 6. Exception Handling

Ne jamais renvoyer de backtrace au client (fuite d'information). Définir des exceptions typées + handlers.

```python
from fastapi import status
from fastapi.responses import JSONResponse


class BaseAPIException(Exception):
    """Base class for API exceptions."""

    def __init__(self, status_code: int, detail: str, headers: dict | None = None):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers


class NotFoundException(BaseAPIException):
    def __init__(self, resource: str, resource_id):
        super().__init__(status.HTTP_404_NOT_FOUND, f"{resource} with ID {resource_id} not found")


class ConflictException(BaseAPIException):
    def __init__(self, message: str):
        super().__init__(status.HTTP_409_CONFLICT, message)


class UnauthorizedException(BaseAPIException):
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(status.HTTP_401_UNAUTHORIZED, detail, {"WWW-Authenticate": "Bearer"})


@app.exception_handler(NotFoundException)
async def not_found_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```

## 7. Architecture conteneur

L'application entière est déployée dans un **seul conteneur Docker** (multi-stage : build frontend Node → build backend Python 3.14-slim → runtime servant le SPA statique via Uvicorn).

```dockerfile
# Stage 1: frontend
FROM node:18-alpine as frontend_builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build -- --configuration production

# Stage 2: backend deps
FROM python:3.14-slim as backend_builder
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Stage 3: runtime
FROM python:3.14-slim
WORKDIR /app
COPY --from=frontend_builder /app/frontend/dist ./frontend/dist
COPY backend/ /app/backend/
ENV PYTHONUNBUFFERED=1 PORT=8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8080/api/health')"
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

## ⚠️ Erreurs courantes à éviter

- ❌ Backtrace/`exc` renvoyé au client → ✅ message générique + log serveur.
- ❌ Endpoint non `async` (bloque le thread) → ✅ `async def` + `await`.
- ❌ `print()` pour les logs → ✅ `logging.getLogger(__name__)`.
- ❌ Absence de type hints → ✅ type hints complets + docstrings.
- ❌ Config/secrets en dur → ✅ `BaseSettings`.
- ❌ `data: dict` sans validation → ✅ modèle Pydantic dédié.

## Ressources

- [FastAPI](https://fastapi.tiangolo.com/) · [Pydantic v2](https://docs.pydantic.dev/latest/) · [SQLAlchemy Async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) · [Uvicorn](https://www.uvicorn.org/)
