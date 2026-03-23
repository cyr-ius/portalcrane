FROM mcr.microsoft.com/devcontainers/base:alpine

# Registry version — update this ARG to upgrade
ARG REGISTRY_VERSION

# Trivy version — update this ARG to upgrade
# Check release https://github.com/aquasecurity/trivy/releases
ARG TRIVY_VERSION

# Install Python + deps
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-virtualenv \
    git \
    curl \
    skopeo \
    nodejs \
    npm


# Download and install registry binary
RUN curl -L https://github.com/distribution/distribution/releases/download/v${REGISTRY_VERSION}/registry_${REGISTRY_VERSION}_linux_amd64.tar.gz \
    | tar xz -C /usr/local/bin registry

# Download and install Trivy binary
RUN curl -L https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz \
    | tar xz -C /usr/local/bin trivy

# Angular CLI
RUN npm install -g @angular/cli

# Create venv
RUN python3 -m venv /opt/venv \
    &&  chown -R vscode:vscode /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip and uv
RUN pip install --upgrade pip uv pre-commit envsubst

ENV UV_SYSTEM_PYTHON=true \
    UV_NO_CACHE=true

RUN git config --global --add safe.directory /workspace

WORKDIR /workspace
