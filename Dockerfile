# ─── Stage 1: Build Angular Frontend ──────────────────────────────────────
FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend

# Install dependencies
COPY frontend/package.json ./
RUN npm install --legacy-peer-deps

# Copy source and build
COPY frontend/ ./
RUN npm run build:prod


# ─── Stage 2: Final container ─────────────────────────────────────────────
FROM python:3.14-slim

LABEL maintainer="cyr-ius <https://github.com/cyr-ius>"
LABEL org.opencontainers.image.title="Portalcrane"
LABEL org.opencontainers.image.description="Docker Registry Manager - Single Container"
LABEL org.opencontainers.image.source="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.url="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.licenses="MIT"

# Registry version — update this ARG to upgrade
ARG REGISTRY_VERSION=3.0.0

# Trivy version — update this ARG to upgrade
ARG TRIVY_VERSION=0.69.2

# Install system dependencies (Docker CLI for staging pipeline)
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    skopeo \
    curl \
    ca-certificates \
    lsb-release \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Download and install registry binary
RUN curl -L https://github.com/distribution/distribution/releases/download/v${REGISTRY_VERSION}/registry_${REGISTRY_VERSION}_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin registry

# Download and install Trivy binary
RUN curl -L https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz \
    | tar xz -C /usr/local/bin trivy

# Create Trivy cache directory
RUN mkdir -p /var/cache/trivy

# Create staging directory
RUN mkdir -p /tmp/staging

# Set working directory for application
WORKDIR /app

# Copy Python backend
COPY backend/ ./

# Install Python dependencies
ENV UV_SYSTEM_PYTHON=true \
    UV_NO_CACHE=true
RUN pip3 install --no-cache-dir uv envsubst
RUN uv pip install --no-cache-dir -r requirements.txt

# Copy built frontend
COPY --from=frontend-builder /build/frontend/dist/portalcrane/browser ./frontend/dist/portalcrane/browser

# Pass application version from build ARG to runtime ENV for about endpoint
ARG VERSION
ENV APP_VERSION=${VERSION}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Copy Supervisor configuration and registry config template
COPY ./dockerfiles/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY ./dockerfiles/registry-config.yml.template /etc/registry/config.yml.template

# Entrypoint generates registry config from env vars then starts supervisord
COPY ./dockerfiles/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# For staging pipeline (if needed)
EXPOSE 8000

# Start application
CMD ["/docker-entrypoint.sh"]