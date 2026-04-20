# ─── FastAPI + Angular Development Image ─────────────────────────────────────────────

FROM mcr.microsoft.com/devcontainers/base:alpine

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# ── System dependencies (apk = gestionnaire Alpine) ────────────────────────
RUN apk add --no-cache \
    curl \
    git \
    python3 \
    nodejs \
    npm \
    skopeo

# ── Installation de uv (gestionnaire Python ultra-rapide) ──────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Configurer uv pour installer dans le système (pas de venv par défaut)
ENV UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Angular CLI 21 + outils globaux ────────────────────────────────────────
ENV NG_CLI_ANALYTICS=ci \
    NG_FORCE_TTY=false

RUN npm install -g \
    @angular/cli@21 \
    prettier@3

# Download and install registry binary
ARG REGISTRY_VERSION=3.0.0
RUN curl -L https://github.com/distribution/distribution/releases/download/v${REGISTRY_VERSION}/registry_${REGISTRY_VERSION}_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin registry && \
    mkdir -p /etc/registry

# Copy configuration template file
COPY registry-config.yml.tpl /etc/registry/config.yml

# Download and install Trivy binary
ARG TRIVY_VERSION=0.69.3
RUN curl -L https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz \
    | tar xz -C /usr/local/bin trivy

# ── Workspace ───────────────────────────────────────────────────────────────
ARG WORKSPACE=/workspaces
WORKDIR /${WORKSPACE}

# ── Volume pour le registre local ─────────────────────────────────────────────
VOLUME ["${WORKSPACE}/.data_env"]

RUN mkdir -p ${WORKSPACE}/.data_env \
    && chown vscode:vscode ${WORKSPACE}/.data_env

# ── Créer un utilisateur non-root pour le développement ─────────────────────
USER vscode

# ── Dépendances Python via uv ───────────────────────────────────────────────
RUN uv tool install ruff \
    && uv tool install prek \
    && uv tool install alembic


# Set the default shell to bash instead of sh
ENV SHELL=/bin/bash
