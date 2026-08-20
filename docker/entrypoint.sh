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
if [ -z "${SECRET_KEY}" ]; then
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
# TRIVY_ENABLED is the master switch for vulnerability scanning, local or remote.
# Set it to false to disarm scanning entirely. TRIVY_SERVER_URL picks where the
# backend's Trivy client connects: leave it at the default (localhost:4954) to
# use the embedded server started below, or point it at a remote Trivy server
# (e.g. http://trivy:4954) to use one running in another container. The embedded
# trivy-server only autostarts when TRIVY_ENABLED is true AND TRIVY_SERVER_URL
# still targets localhost — pointing it elsewhere skips the redundant local
# server (and its DB download) since the remote instance manages its own DB.
TRIVY_ENABLED=${TRIVY_ENABLED:-true}
export TRIVY_ENABLED
TRIVY_SERVER_URL=${TRIVY_SERVER_URL:-"http://localhost:4954"}
export TRIVY_SERVER_URL

case "${TRIVY_SERVER_URL}" in
    http://localhost:*|http://127.0.0.1:*) TRIVY_SERVER_LOCAL=true ;;
    *)                                     TRIVY_SERVER_LOCAL=false ;;
esac

case "$(echo "${TRIVY_ENABLED}" | tr '[:upper:]' '[:lower:]')" in
    false|0|no|off)
        TRIVY_AUTOSTART=false
        echo "[entrypoint] Trivy disabled (TRIVY_ENABLED=${TRIVY_ENABLED})"
        ;;
    *)
        if [ "${TRIVY_SERVER_LOCAL}" = "true" ]; then
            TRIVY_AUTOSTART=true
        else
            TRIVY_AUTOSTART=false
            echo "[entrypoint] Trivy enabled with remote server ${TRIVY_SERVER_URL}; embedded trivy-server not started"
        fi
        ;;
esac
export TRIVY_AUTOSTART

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
