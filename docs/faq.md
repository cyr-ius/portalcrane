# FAQ & Troubleshooting

## I lost the admin password — how do I get back in?

If you have a persistent volume on `/var/lib/portalcrane` (you should —
see [Installation](getting-started/installation.md#data-persistence)),
delete the stored hash and restart:

```bash
docker exec portalcrane rm /var/lib/portalcrane/admin_password.hash
docker restart portalcrane
docker logs portalcrane | grep -A5 "initial admin account"
```

A new password is generated and printed in the logs. If you **don't** have
a persistent volume, this happens automatically on every restart — which
is exactly why one is required for any real deployment.

## Why did my admin password and secret key change after a restart?

You didn't mount a volume on `/var/lib/portalcrane` (`DATA_DIR`). Without
it, the admin password hash and JWT `SECRET_KEY` are regenerated on every
container start, invalidating every existing session and Personal Access
Token. Add `-v /portalcrane_data:/var/lib/portalcrane` (or the Compose
equivalent) and restart once — from then on both values persist.

## `docker login` fails against Portalcrane — where do I start?

1. Confirm `REGISTRY_PROXY_AUTH_ENABLED` isn't unexpectedly `false`
   (anonymous mode) or `true` when you expected open access.
2. Make sure you're authenticating against port `8000` (the proxy), not
   `5000` — port `5000` is only exposed directly in the
   [dev Compose stack](development.md#full-stack-dev-compose-stack) and has
   no authentication of its own.
3. If using a [Personal Access Token](features/personal-access-tokens.md),
   confirm it was created with the **`docker`** scope — an `api`-scoped
   token is rejected by design.
4. Check `GET /api/system/processes` — if the embedded `registry` process
   isn't running, the proxy has nothing to forward to.

## Why can't a user push even though they're not disabled?

Non-admin push access is **always** folder-scoped and **always** granted
through a group, never directly to the user. Check:

- Is the user a member of a group that has `can_push` (or
  `can_push_external`, for external destinations) on the folder matching
  the image's first path segment?
- Does the image path actually resolve to the folder you think it does?
  Remember only the first segment matters, and anything unmatched falls
  back to `__root__`.

See [Folder-Based Access Control](features/folders.md) for the full
resolution logic and a worked example.

## What's the difference between Staging and Transfer?

Both run the same pull → scan → push pipeline under the hood, but:

- **[Staging](features/staging-pipeline.md)** is built for one image at a
  time with a human reviewing the vulnerability report between pull and
  push — the interactive workflow.
- **[Transfer](features/external-registries.md#transfers)** moves a batch
  of images between any two registries (local or external) in a single
  API call, without an interactive review step — the bulk/automation
  workflow.

## Can I disable vulnerability scanning entirely?

Yes — set `TRIVY_ENABLED=false`. This stops the embedded Trivy server from
starting at all (saving memory, useful on constrained hardware like a
Raspberry Pi), skips the CVE database updater, and makes every scan-related
endpoint return `503`. If you only want to skip scanning in the staging
pipeline while keeping Trivy available for manual on-demand scans, use
`VULN_SCAN_ENABLED=false` instead (or its per-job override).

## Why does OIDC login fail with "collides with the local admin account"?

This is an intentional anti-usurpation guard: if your OIDC provider's
`preferred_username` (or whichever claim maps to a username) resolves to
`admin` (or `ADMIN_USERNAME`), or to any **existing local, password-based**
account, the login is rejected with `403`. Portalcrane never lets an SSO
identity assume a local account's identity or rights. Rename the claim
mapping on the provider side, or have that person use a different
username.

## I enabled OIDC-only mode and now nobody can log in as admin

`OIDC_ONLY=true` disables **every** local login, including the built-in
env-admin, so Portalcrane refuses to enable it unless admin access is
guaranteed to survive — either an admin group-claim mapping is configured
(`OIDC_ADMIN_GROUP_CLAIM`/`OIDC_ADMIN_GROUP`), or at least one OIDC account
has already been manually promoted to admin. If you're stuck, set
`OIDC_ONLY=false` (or remove the persisted override) to restore local
login, fix the group mapping, then re-enable it. See
[Authentication & OIDC](configuration/authentication.md#oidc-only-mode-admin-bootstrap).

## The dashboard shows `registry_status: "unreachable"`

The embedded registry process may still be starting, or it crashed. Check
`GET /api/system/processes` (admin only) for its actual state, and
container logs for the `registry` program's stderr/stdout (supervisord
streams both to the container's own stdout/stderr). The dashboard is
designed to degrade gracefully in this case rather than error out, so a
brief "unreachable" state right after container start is normal.

## How do I know if my reverse proxy setup is leaking a shared IP into rate limiting / audit logs?

If every audit-log entry and every rate-limit hit shows the **same** source
IP no matter who's actually making requests, `TRUSTED_PROXIES` almost
certainly isn't set (or doesn't match your proxy's actual address) — see
[TLS & Reverse Proxy → Trusted proxies](configuration/tls-reverse-proxy.md#trusted-proxies-client-ip-resolution).

## Where is everything actually stored?

Everywhere state lives is documented in
[Architecture → Data layout](architecture.md#data-layout) — in short, one
directory (`DATA_DIR`, default `/var/lib/portalcrane`) holds every JSON
config file plus the registry's own blob storage and the Trivy DB cache.
There is no external database to back up separately — backing up that one
directory is sufficient.
