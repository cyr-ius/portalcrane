# Quick Start

This walks through the first few things you'll do after
[installing](installation.md) Portalcrane: logging in, exploring the
dashboard, and using the registry from the Docker CLI.

## 1. Log in

Retrieve the auto-generated admin password from the container logs:

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Open `http://<host>:8000` in a browser and sign in with:

- **Username:** `admin`
- **Password:** the value printed in the logs

You land on the [Dashboard](../features/dashboard.md), which shows image
count, registry disk usage, and user counts at a glance.

## 2. Use the Docker CLI against Portalcrane

Portalcrane's registry proxy speaks the standard Docker Registry HTTP API,
so any Docker-compatible client works against it directly:

```bash
docker login <host>:8000
docker pull <image>:<tag>
docker push <image>:<tag>
docker logout
```

Authenticate with either your local username/password, or a
[Personal Access Token](../features/personal-access-tokens.md) scoped for
Docker (recommended for CI pipelines, since it avoids storing a real
account's password).

!!! tip "Anonymous access"
Set `REGISTRY_PROXY_AUTH_ENABLED=false` to allow unauthenticated
`pull`/`push` against the proxy — useful for fully trusted internal
networks. See [Environment Variables](../configuration/environment-variables.md#registry).

For a full step-by-step example — login, pull, tag, push, pull back, run,
and logout, using the tiny `traefik/whoami` image, plus a side-by-side look
at what changes with authentication disabled — see the
[Docker CLI Walkthrough](docker-cli-walkthrough.md).

## 3. Pull your first image via the Staging Pipeline

Rather than pushing images blindly from the CLI, use the **Staging**
page to pull an image from Docker Hub, have it scanned by Trivy, and only
then push it into your registry:

1. Go to **Staging** in the sidebar.
2. Search for an image (e.g. `nginx`) and pick a tag.
3. Click **Pull** — Portalcrane downloads it as an OCI layout via `skopeo`
   (no Docker daemon required on the server).
4. Once the CVE scan completes, review the vulnerability report.
5. Click **Push**, choose a destination name/tag and (optionally) a
   [folder](../features/folders.md), and the image lands in your registry.

See [Staging Pipeline](../features/staging-pipeline.md) for the full
walkthrough, including how to push to an **external** registry directly
from a staged image.

## 4. Create your first non-admin user

Go to **Settings → Accounts** and create a user with just **pull** access,
or grant folder-scoped push access instead of a blanket admin role. See
[Users, Groups & Permissions](../features/users-groups-permissions.md) for
the full model — permissions are always granted through **groups**, never
directly to a user.

## 5. Add an external registry (optional)

If you want to mirror images from — or sync images to — another registry
(Docker Hub, GHCR, Quay, a self-hosted instance), add it under
**Settings → Registries**. See
[External Registries & Transfers](../features/external-registries.md).

## What's next

- [Folder-Based Access Control](../features/folders.md) — scope permissions
  to image namespaces like `production/*`.
- [Vulnerability Scanning](../configuration/vulnerability-scanning.md) —
  tune which CVE severities block a staging push.
- [Environment Variables](../configuration/environment-variables.md) —
  full reference for every configurable option.
