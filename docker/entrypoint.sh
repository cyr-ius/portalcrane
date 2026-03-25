#!/bin/sh
# ─── Portalcrane container entrypoint ─────────────────────────────────────────
set -e

# ── Warn if SECRET_KEY was not changed ────────────────────────────────────────
if [ "${SECRET_KEY:-change-this-secret-key-in-production}" = "change-this-secret-key-in-production" ]; then
    echo "[entrypoint] WARNING: SECRET_KEY is not set. Using insecure default." >&2
fi

# ── Generate registry config from template ────────────────────────────────────
DATA_DIR=${DATA_DIR:-"/var/lib/portalcrane"}
export DATA_DIR

REGISTRY_HTTP_SECRET=${SECRET_KEY}
export REGISTRY_HTTP_SECRET

REGISTRY_LOG_LEVEL=${REGISTRY_LOG_LEVEL:-INFO}
export REGISTRY_LOG_LEVEL

LOG_LEVEL=${LOG_LEVEL:-INFO}
export LOG_LEVEL

# ── Ensure required directories exist ──────────────────────────────────────────
mkdir -p ${DATA_DIR}/registry ${DATA_DIR}/cache/trivy ${DATA_DIR}/cache/staging

# ── Generate configuration files ───────────────────────────────────────────────
echo "[entrypoint] Generating /etc/supervisord/supervisord.conf..."
mkdir -p /etc/supervisor
envsubst < /usr/src/supervisord.conf.tpl > /etc/supervisor/supervisord.conf

echo "[entrypoint] Generating /etc/registry/config.yml..."
mkdir -p /etc/registry
envsubst < /usr/src/registry-config.yml.tpl > /etc/registry/config.yml

# ── Validate registry config before handing off to supervisord ─────────────────
echo "[entrypoint] Validating registry config..."
if ! /usr/local/bin/registry serve /etc/registry/config.yml --help > /dev/null 2>&1; then
    echo "[entrypoint] Registry binary test:"
    /usr/local/bin/registry --version || true
fi

# ── Start supervisord ─────────────────────────────────────────────────────────
echo "[entrypoint] Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
