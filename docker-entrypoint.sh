#!/bin/sh
# ─── Portalcrane container entrypoint ─────────────────────────────────────────
# Validates required environment variables, then hands off to supervisord.
# supervisord manages both the embedded registry process and the uvicorn process.

set -e

# ── Warn if SECRET_KEY was not changed ────────────────────────────────────────
if [ "${SECRET_KEY:-change-this-secret-key-in-production}" = "change-this-secret-key-in-production" ]; then
    echo "[entrypoint] WARNING: SECRET_KEY is not set. Using insecure default — set a strong SECRET_KEY in production." >&2
fi

# ── Ensure staging directory exists ───────────────────────────────────────────
mkdir -p "${STAGING_DIR:-/tmp/staging}"

# ── Ensure registry storage directory exists ──────────────────────────────────
mkdir -p /var/lib/registry

echo "[entrypoint] Starting supervisord (registry + portalcrane)..."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf

# Wait for Trivy server to be ready (max 60s)
echo "[entrypoint] Waiting for Trivy server on 127.0.0.1:4954..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:4954/healthz > /dev/null 2>&1; then
        echo "[entrypoint] Trivy server is ready."
        break
    fi
    sleep 2
done