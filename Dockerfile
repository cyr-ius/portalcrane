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
FROM python:3.12-slim

ARG BUILD_DATE
ARG VERSION
ENV APP_VERSION=${VERSION}

LABEL maintainer="cyr-ius <https://github.com/cyr-ius>"
LABEL org.opencontainers.image.title="Portalcrane"
LABEL org.opencontainers.image.description="Docker Registry Manager - Single Container"
LABEL org.opencontainers.image.source="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.url="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.licenses="MIT"

# Trivy version — update this ARG to upgrade
ARG TRIVY_VERSION=0.69.1

# Install system dependencies (Docker CLI for staging pipeline + ClamAV client)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    clamdscan \
    lsb-release \
    && curl -LsSf https://get.docker.com | sh \
    # Install Trivy from the official .deb (single install, no APT conflict)
    && ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in \
         amd64) TRIVY_ARCH="Linux-64bit" ;; \
         arm64) TRIVY_ARCH="Linux-ARM64" ;; \
         *) echo "Unsupported architecture: $ARCH" && exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/trivy.deb \
         "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_${TRIVY_ARCH}.deb" \
    && dpkg -i /tmp/trivy.deb \
    && rm /tmp/trivy.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir uv

ENV UV_SYSTEM_PYTHON=true \
    UV_NO_CACHE=true

WORKDIR /app

# Copy Python backend
COPY backend/ ./

# Install Python dependencies
RUN uv pip install --no-cache-dir -r requirements.txt

# Copy built frontend
COPY --from=frontend-builder /build/frontend/dist/portalcrane/browser ./frontend/dist/portalcrane/browser

# Create staging directory
RUN mkdir -p /tmp/staging

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Start application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
