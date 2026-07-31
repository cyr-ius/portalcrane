# API Reference

Portalcrane's backend is a FastAPI application; every REST route lives
under `/api`, and the registry proxy lives at `/v2` (standard Docker
Registry HTTP API path, unauthenticated in the OpenAPI schema on purpose —
see below).

## Interactive docs (Swagger UI)

Set `SWAGGER_ENABLED=true` and browse to **`/api/docs`**. It's disabled by
default and self-hosted (no CDN dependency), authenticated the same way as
the rest of the API:

- Click **Authorize**, choose **PersonalAccessToken**, and paste an
  `api`-scoped [Personal Access Token](features/personal-access-tokens.md) —
  every request in that session is then authenticated as you.
- The raw OpenAPI schema is served at `/api/openapi.json` (also gated
  behind `SWAGGER_ENABLED`).

The `/v2/...` registry proxy routes are deliberately excluded from the
schema (`include_in_schema=False`) — they implement the Docker Registry
HTTP API and only make sense to a Docker client, never to a Swagger/REST
consumer.

## Authenticating requests

Three credential types work against the REST API, all resolving to the
same `UserInfo` context:

| Credential                          | Where it goes                                                                               | Typical use                                           |
| ----------------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| Session JWT                         | `pc_token` HttpOnly cookie (set by `/api/auth/login`)                                       | Browser sessions                                      |
| Session JWT                         | `Authorization: Bearer <token>` (the `access_token` from `/api/auth/login`'s JSON response) | Short-lived scripts, same lifetime as a login session |
| Personal Access Token (`api` scope) | `Authorization: Bearer <token>`                                                             | Long-lived automation, CI, external tools             |

See [Users, Groups & Permissions](features/users-groups-permissions.md) and
[Personal Access Tokens](features/personal-access-tokens.md) for how
accounts and tokens are provisioned.

## Health check

```bash
curl http://<host>:8000/api/health
```

Unauthenticated; returns a JSON status payload. Used by the container's
built-in Docker healthcheck.

## Endpoint groups

Every route below is prefixed with `/api` unless noted. Full request/response
shapes, permission requirements, and examples are covered on each feature's
own page — this table is the map.

| Prefix         | Tag                    | Covers                                                             | Docs                                                                                                                          |
| -------------- | ---------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `/auth`        | Authentication         | Login/logout, current user, local user CRUD, password change       | [Authentication & OIDC](configuration/authentication.md), [Users, Groups & Permissions](features/users-groups-permissions.md) |
| `/auth/tokens` | Personal Access Tokens | Create/list/revoke PATs                                            | [Personal Access Tokens](features/personal-access-tokens.md)                                                                  |
| `/oidc`        | OIDC                   | Public config, callback, admin settings, connectivity test         | [Authentication & OIDC](configuration/authentication.md)                                                                      |
| `/groups`      | Groups                 | CRUD for permission groups + membership                            | [Users, Groups & Permissions](features/users-groups-permissions.md)                                                           |
| `/folders`     | Folders                | CRUD for folders, per-group permission grants, discovery endpoints | [Folder-Based Access Control](features/folders.md)                                                                            |
| `/images`      | Images                 | Browse/search/delete repositories & tags, retag, copy              | [Image & Tag Management](features/image-management.md)                                                                        |
| `/registries`  | Registries             | CRUD for external registries, connectivity tests                   | [External Registries & Transfers](features/external-registries.md)                                                            |
| `/staging`     | Staging                | Docker Hub search, pull/scan/push pipeline, job tracking           | [Staging Pipeline](features/staging-pipeline.md)                                                                              |
| `/transfer`    | Transfer               | Bulk cross-registry copy jobs                                      | [External Registries & Transfers](features/external-registries.md#transfers)                                                  |
| `/trivy`       | Trivy                  | On-demand scans, DB status/update, scan-policy override            | [Vulnerability Scanning](configuration/vulnerability-scanning.md)                                                             |
| `/dashboard`   | Dashboard              | Aggregate registry/user/Trivy statistics                           | [Dashboard](features/dashboard.md)                                                                                            |
| `/network`     | Network                | Proxy, syslog, and email configuration + tests                     | [Notifications](configuration/notifications.md), [TLS & Reverse Proxy](configuration/tls-reverse-proxy.md)                    |
| `/system`      | System                 | Process status, audit logs, GC, orphan OCI cleanup                 | [System Maintenance](features/system-maintenance.md)                                                                          |
| `` (root)      | About                  | Version info, latest-release check                                 | —                                                                                                                             |
| `/v2`          | Registry Proxy         | Standard Docker Registry HTTP API v2 (for `docker` CLI)            | [Getting Started → Quick Start](getting-started/quick-start.md)                                                               |

## Error conventions worth knowing

- **`404` instead of `403` for ownership checks** — fetching a registry (or
  similar owned resource) you don't have access to returns `404 Not Found`
  rather than `403 Forbidden`, so its existence — and any credentials it
  might hold — is never confirmed to a user who shouldn't see it.
- **`503` for an unreachable backing registry** — image-browsing endpoints
  catch connection/timeout errors against the target registry and return
  `503 Service Unavailable` instead of an unhandled `500`.
- **`409` for a GC already in progress** — starting a garbage-collect while
  one is running returns `409 Conflict`; poll `GET /api/system/gc` instead
  of retrying blindly.

## Rate limits

All `/api/*` routes are subject to the per-IP rate limiter described in
[TLS & Reverse Proxy → Rate limiting](configuration/tls-reverse-proxy.md#rate-limiting)
unless `RATE_LIMIT_ENABLED=false`. The login endpoint has its own, stricter
budget.
