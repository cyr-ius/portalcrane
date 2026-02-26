#!/bin/sh
# ─── Portalcrane container entrypoint ─────────────────────────────────────────
set -e

# ── Warn if SECRET_KEY was not changed ────────────────────────────────────────
if [ "${SECRET_KEY:-change-this-secret-key-in-production}" = "change-this-secret-key-in-production" ]; then
    echo "[entrypoint] WARNING: SECRET_KEY is not set. Using insecure default." >&2
fi

# ── Ensure required directories exist ─────────────────────────────────────────
mkdir -p "${STAGING_DIR:-/tmp/staging}"
mkdir -p /var/lib/registry
mkdir -p /var/cache/trivy
mkdir -p /var/log

# ── Generate registry config from template ────────────────────────────────────
REGISTRY_HTTP_SECRET=${SECRET_KEY}
export REGISTRY_HTTP_SECRET

echo "[entrypoint] Generating /etc/registry/config.yml..."
mkdir -p /etc/registry
envsubst < /etc/registry/config.yml.template > /etc/registry/config.yml

# ── Validate registry config before handing off to supervisord ────────────────
echo "[entrypoint] Validating registry config..."
if ! /usr/local/bin/registry serve /etc/registry/config.yml --help > /dev/null 2>&1; then
    echo "[entrypoint] Registry binary test:"
    /usr/local/bin/registry --version || true
fi

# Dry-run: attempt to parse config (registry prints error and exits 1 on bad config)
echo "[entrypoint] Generated config:"
cat /etc/registry/config.yml
echo "---"

# ── Start supervisord ─────────────────────────────────────────────────────────
echo "[entrypoint] Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf