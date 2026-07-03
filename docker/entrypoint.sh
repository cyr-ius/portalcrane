#!/bin/sh
# ─── Portalcrane container entrypoint ─────────────────────────────────────────
set -e

# ── Data directory ────────────────────────────────────────────────────────────
DATA_DIR=${DATA_DIR:-"/var/lib/portalcrane"}
export DATA_DIR
mkdir -p "${DATA_DIR}"

# ── Resolve SECRET_KEY (auto-generate & persist on first launch) ───────────────
# Shared with the backend (JWT signing) and the embedded registry. When unset or
# left at the default, generate a random secret once and persist it under
# DATA_DIR so JWTs and registry signatures survive restarts.
SECRET_KEY_FILE="${DATA_DIR}/secret_key"
if [ -z "${SECRET_KEY}" ] || [ "${SECRET_KEY}" = "change-this-secret-key-in-production" ]; then
    if [ ! -s "${SECRET_KEY_FILE}" ]; then
        head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "${SECRET_KEY_FILE}"
        chmod 600 "${SECRET_KEY_FILE}"
        echo "[entrypoint] Generated a new SECRET_KEY in ${SECRET_KEY_FILE}"
    fi
    SECRET_KEY=$(cat "${SECRET_KEY_FILE}")
fi
export SECRET_KEY

REGISTRY_HTTP_SECRET=${SECRET_KEY}
export REGISTRY_HTTP_SECRET

REGISTRY_LOG_LEVEL=${REGISTRY_LOG_LEVEL:-INFO}
export REGISTRY_LOG_LEVEL

LOG_LEVEL=${LOG_LEVEL:-INFO}
export LOG_LEVEL

PRIVATE_KEY=${PRIVATE_KEY}
export PRIVATE_KEY

PUBLIC_KEY=${PUBLIC_KEY}
export PUBLIC_KEY

# ── Trivy toggle ────────────────────────────────────────────────────────────────
# Set TRIVY_ENABLED=false to disarm the embedded Trivy server (no autostart).
# Any value other than "false" (case-insensitive) keeps Trivy enabled.
TRIVY_ENABLED=${TRIVY_ENABLED:-true}
export TRIVY_ENABLED
case "$(echo "${TRIVY_ENABLED}" | tr '[:upper:]' '[:lower:]')" in
    false|0|no|off) TRIVY_AUTOSTART=false ;;
    *)              TRIVY_AUTOSTART=true ;;
esac
export TRIVY_AUTOSTART
if [ "${TRIVY_AUTOSTART}" = "false" ]; then
    echo "[entrypoint] Trivy server disabled (TRIVY_ENABLED=${TRIVY_ENABLED})"
fi

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
