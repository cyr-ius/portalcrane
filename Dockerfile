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

LABEL maintainer="cyr-ius <https://github.com/cyr-ius>"
LABEL org.opencontainers.image.title="Portalcrane"
LABEL org.opencontainers.image.description="Docker Registry Manager - Single Container"
LABEL org.opencontainers.image.source="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.url="https://github.com/cyr-ius/portalcrane"
LABEL org.opencontainers.image.licenses="MIT"

# Install system dependencies (Docker CLI for staging pipeline + ClamAV client)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    clamav \
    clamav-daemon \
    clamdscan \
    wget \
    gnupg \
    lsb-release \
    && curl -fsSL https://get.docker.com -o get-docker.sh \
    && sh get-docker.sh \
    && rm get-docker.sh \
    && wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | gpg --dearmor | tee /usr/share/keyrings/trivy.gpg > /dev/null \
    && echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | tee -a /etc/apt/sources.list.d/trivy.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends trivy \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python backend
COPY backend/ ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy built frontend
COPY --from=frontend-builder /build/frontend/dist/portalcrane/browser ./frontend/dist/portalcrane/browser

# Create staging directory
RUN mkdir -p /tmp/staging

# Expose port
EXPOSE 8080

# Default environment variables
ENV REGISTRY_URL=http://localhost:5000 \
    ADMIN_USERNAME=admin \
    ADMIN_PASSWORD=changeme \
    CLAMAV_HOST=localhost \
    CLAMAV_PORT=3310 \
    STAGING_DIR=/tmp/staging \
    ADVANCED_MODE=false \
    VULN_SCAN_ENABLED=false \
    VULN_SCAN_SEVERITIES=CRITICAL,HIGH \
    VULN_IGNORE_UNFIXED=false \
    VULN_SCAN_TIMEOUT=5m

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Start application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
