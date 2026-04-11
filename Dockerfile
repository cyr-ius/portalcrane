# ─── Stage 1: Build Angular Frontend ──────────────────────────────────────
FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend

# Install dependencies
COPY frontend/package.json ./
RUN npm install

# Copy source and build
COPY frontend/ ./
RUN npm run build

# ─── Stage 2: Final container ─────────────────────────────────────────────
FROM python:3.14-alpine

LABEL maintainer="cyr-ius <https://github.com/cyr-ius>"
LABEL org.opencontainers.image.title="Portalcrane"
LABEL org.opencontainers.image.description="Docker Registry Manager - Single Container"
LABEL org.opencontainers.image.source="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.url="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.licenses="MIT"

# Registry version — update this ARG to upgrade
ARG REGISTRY_VERSION=3.0.0

# Trivy version — update this ARG to upgrade
# Check release https://github.com/aquasecurity/trivy/releases
ARG TRIVY_VERSION=0.69.3

# Install system dependencies (Docker CLI for staging pipeline)
RUN apk add --no-cache \
    envsubst \
    supervisor \
    skopeo \
    curl \
    ca-certificates

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_NO_CACHE=true
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PYTHONUNBUFFERED=1
ENV PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"

WORKDIR /app

RUN --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    uv sync --frozen --no-dev

# Copy Supervisor configuration template file
COPY ./docker/supervisord.conf.tpl /usr/src/supervisord.conf.tpl

# Download and install registry binary
RUN curl -L https://github.com/distribution/distribution/releases/download/v${REGISTRY_VERSION}/registry_${REGISTRY_VERSION}_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin registry && \
    mkdir -p /etc/registry

# Copy configuration template file
COPY ./docker/registry-config.yml.tpl /usr/src/registry-config.yml.tpl

# Download and install Trivy binary
RUN curl -L https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz \
    | tar xz -C /usr/local/bin trivy

# Install Python dependencies from requirements
RUN --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    uv sync --frozen --no-dev

# Copy built frontend
COPY --from=frontend-builder /build/frontend/dist/portalcrane/browser ./frontend

# Copy Python backend
COPY backend/ ./backend

# Pass application version from build ARG to runtime ENV for about endpoint
ARG VERSION
ENV APP_VERSION=${VERSION}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Entrypoint generates registry config from env vars then starts supervisord
COPY ./docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# For staging pipeline (if needed)
EXPOSE 8000

# Volumes for registry data and config (if you want to persist or customize config)
VOLUME [ "/var/lib/portalcrane" ]

# Start application
ENTRYPOINT ["/entrypoint.sh"]
