#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${1:-}"
REGISTRY_VERSION="3.0.0"
TRIVY_VERSION="0.72.0"
INSTALL_DIR="/usr/local/bin"

if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  SUDO=""
fi

install_binary() {
  local url="$1"
  local binary="$2"

  echo "📦 Installation de ${binary}..."
  ${SUDO} mkdir -p "${INSTALL_DIR}"
  curl -L "$url" | ${SUDO} tar xz -C "${INSTALL_DIR}" "$binary"
  ${SUDO} chmod +x "${INSTALL_DIR}/${binary}"
}

if command -v registry >/dev/null 2>&1; then
  echo "✅ registry déjà installé : $(command -v registry)"
else
  install_binary "https://github.com/distribution/distribution/releases/download/v${REGISTRY_VERSION}/registry_${REGISTRY_VERSION}_linux_amd64.tar.gz" registry
fi

if command -v trivy >/dev/null 2>&1; then
  echo "✅ trivy déjà installé : $(command -v trivy)"
else
  install_binary "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" trivy
fi

if [ -n "$WORKSPACE" ] && [ -f "$WORKSPACE/.devcontainer/registry-config.yml.tpl" ]; then
  mkdir -p /etc/registry
  cp "$WORKSPACE/.devcontainer/registry-config.yml.tpl" /etc/registry/registry-config.yml.tpl
  echo "✅ Template de configuration registry copié vers /etc/registry"
fi

echo "✅ trivy et registry sont prêts dans le devcontainer."
