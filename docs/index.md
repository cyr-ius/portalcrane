# Portalcrane

**Portalcrane** is a self-hosted Docker registry manager. It ships a modern,
single-container application that combines a private [CNCF Distribution](https://github.com/distribution/distribution)
registry with a full management UI: browsing and searching images, a staging
pipeline that scans images for vulnerabilities before they ever reach your
registry, fine-grained folder-based access control, and synchronization with
external registries such as Docker Hub, GHCR, or Quay.

Everything — frontend, backend, embedded registry, and vulnerability
scanner — runs from a **single Docker image**, supervised by `supervisord`.
There is no external database to provision: configuration and metadata are
stored as JSON files under a single data directory.

![Portalcrane dashboard](https://github.com/user-attachments/assets/a6fa3b39-e603-4562-b784-2fb5483b795c)

## Why Portalcrane?

If you run your own Docker registry today, you are probably doing it with
the bare `registry:2` image plus a patchwork of scripts for auth, cleanup,
and mirroring. Portalcrane replaces that patchwork with one opinionated,
batteries-included tool:

- A UI your whole team can use, not just people comfortable with `curl` and `jq`.
- A **staging pipeline** that pulls an image, scans it with Trivy, and only
  then lets you push it into the registry — so a vulnerable image never
  lands silently.
- **Folder-based RBAC** so you can give a team push access to `team-a/*`
  without giving them the keys to the whole registry.
- Built-in **synchronization** with external registries, so promoting an
  image from staging to production (or mirroring from Docker Hub) is a UI
  action, not a shell script.

## Feature overview

| Area                       | What you get                                                                                       |
| -------------------------- | -------------------------------------------------------------------------------------------------- |
| **UI**                     | Angular 22 single-page app with light / dark / auto themes                                         |
| **Authentication**         | Local admin + per-user accounts, optional OIDC/SSO, Personal Access Tokens                         |
| **Authorization**          | Folder-based RBAC (pull / push / external-pull / external-push), group-based permission grants     |
| **Image browsing**         | Search, paginate, inspect layers/labels/env vars/architecture, delete images or single tags, retag |
| **Staging pipeline**       | Docker Hub search → pull (skopeo) → Trivy CVE scan → push to registry                              |
| **Vulnerability scanning** | Embedded Trivy server, configurable blocking severities, on-demand scans from the UI               |
| **External registries**    | CRUD management of Docker Hub / GHCR / Quay / self-hosted registries, connectivity tests           |
| **Transfers & Sync**       | Cross-registry image transfer (local↔external, external↔external) with optional scanning           |
| **Dashboard**              | Live stats: image count, disk usage, largest image, user/admin counts                              |
| **Observability**          | Audit log of every API operation, Syslog forwarding, SMTP email delivery of the audit log          |
| **Operations**             | Garbage collection, orphan OCI layout cleanup, ghost repository purge, process status              |
| **Security**               | Per-IP rate limiting, trusted-proxy aware client IP resolution, TLS termination, HttpOnly cookies  |
| **Deployment**             | Single container image, `linux/amd64` + `linux/arm64` (Raspberry Pi, Apple Silicon)                |

## Where to go next

<div class="grid cards" markdown>

- :material-rocket-launch:{ .lg .middle } **New to Portalcrane?**

  ***

  Get a working instance in under five minutes.

  [:octicons-arrow-right-24: Installation](getting-started/installation.md)

- :material-sitemap:{ .lg .middle } **Understand the moving parts**

  ***

  Frontend, backend, embedded registry, Trivy — how they fit together.

  [:octicons-arrow-right-24: Architecture](architecture.md)

- :material-cog:{ .lg .middle } **Configure your deployment**

  ***

  Every environment variable, what it does, and when you need it.

  [:octicons-arrow-right-24: Environment Variables](configuration/environment-variables.md)

- :material-shield-lock:{ .lg .middle } **Lock down access**

  ***

  Users, groups, folders, tokens — Portalcrane's permission model explained.

  [:octicons-arrow-right-24: Users, Groups & Permissions](features/users-groups-permissions.md)

</div>

## Project status

Portalcrane is developed by [@cyr-ius](https://github.com/cyr-ius) and
distributed under the [MIT license](https://github.com/cyr-ius/portalcrane/blob/main/LICENSE).
Images are published to [Docker Hub](https://hub.docker.com/r/cyrius44/portalcrane)
and [GHCR](https://github.com/cyr-ius/portalcrane/pkgs/container/portalcrane) on
every tagged release. If you find the project useful, consider
[sponsoring it on GitHub](https://github.com/sponsors/cyr-ius).
