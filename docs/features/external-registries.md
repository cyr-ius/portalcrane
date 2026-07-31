# External Registries & Transfers

Portalcrane can talk to any Docker-compatible registry besides its own
embedded one: Docker Hub, GHCR, Quay, or a self-hosted Distribution/Harbor
instance. Two things build on this: **saved registry entries** (so you
don't retype credentials every time) and **transfers**, which move images
between any two registries in bulk.

## Managing external registries

Under **Settings → Registries** (`/api/registries`):

```bash
curl -X POST http://<host>:8000/api/registries \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{
        "name": "GHCR",
        "host": "ghcr.io",
        "username": "cyr-ius",
        "password": "<personal-access-token>",
        "use_tls": true,
        "tls_verify": true,
        "owner": "personal"
      }'
```

- **Admins** may create **global** registries (`owner: "global"`), visible
  and usable by every user.
- **Non-admin users** may only create **personal** registries, visible only
  to themselves.
- Ownership can't be spoofed: a non-admin sending `owner: "global"` is
  rejected with `403`, and any owner value other than `"global"` always
  resolves server-side to the caller's own username — never an arbitrary
  one.
- On creation and update, Portalcrane probes the registry so the frontend
  knows immediately whether it's usable (`reachable` / `auth_ok` /
  `browsable`).

Other useful endpoints:

```bash
GET    /api/registries                       # list registries visible to the caller
POST   /api/registries/test                   # test connectivity with ad-hoc credentials (nothing saved)
POST   /api/registries/{id}/test              # test connectivity using a saved registry's stored credentials
GET    /api/registries/{id}/ping              # quick reachability check
GET    /api/registries/{id}/catalog-check     # whether /v2/_catalog is browsable (some registries disable it)
PATCH  /api/registries/{id}                   # update name/host/credentials/owner
DELETE /api/registries/{id}                   # remove (owner or admin only)
```

!!! note "Access is never leaked"
    Fetching, updating, or deleting a registry you don't own returns
    `404 Not Found`, not `403 Forbidden` — this prevents a non-admin from
    even confirming that another user's registry (and its stored
    credentials) exists.

### Browsing an external registry

Once saved, an external registry behaves like a second source in the image
browser: `GET /api/images/{registry_id}` lists its repositories via
`/v2/_catalog`, `GET /api/images/{registry_id}/tags` lists tags, etc. — see
[Image & Tag Management](image-management.md). Visibility for non-admins
on an external registry is governed by the coarse `can_pull_external`
capability (any folder grant), since a foreign repository path like
`library/nginx` doesn't map to a Portalcrane folder.

## Transfers

The **Transfer** endpoint (`POST /api/transfer`) unifies every combination
of copy operation — local→local, local→external, external→local,
external→external — under a single API with integrated Trivy scanning. It
supersedes the older idea of a separate "sync" import/export feature: one
API call can move a whole batch of images in one shot.

```bash
curl -X POST http://<host>:8000/api/transfer \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{
        "images": [
          {"repository": "backend/api", "tag": "1.4.0"},
          {"repository": "backend/worker", "tag": "1.4.0"}
        ],
        "source_registry_id": null,
        "dest_registry_id": "<ghcr-registry-id>",
        "dest_folder": "",
        "vuln_scan_enabled_override": true,
        "vuln_severities_override": "CRITICAL,HIGH"
      }'
```

- `source_registry_id` / `dest_registry_id`: `null` means the **local
  embedded registry**; any other value is a saved registry's ID. One
  request creates **one job per image**, all sharing the same scan policy.
- `dest_folder`: an optional path prefix applied to every destination
  image (e.g. promoting into `production/`).
- Each `TransferImageRef` accepts an optional `dest_name` to rename that
  specific image independently of the others in the same batch.

### Permission checks

Because the transfer pipeline talks to the local registry directly
(bypassing the registry auth proxy), folder permissions are enforced
explicitly, per end:

| End         | Resolves to...    | Required permission                  |
| ----------- | ----------------- | ------------------------------------ |
| Source      | Local registry    | `can_pull` on each source repository |
| Source      | External registry | `can_pull_external` capability       |
| Destination | Local registry    | `can_push` on the destination folder |
| Destination | External registry | `can_push_external` capability       |

A saved `registry_id` can itself point back at the local registry (the
hidden `__local__` system entry, or an ad-hoc host resolving to
`localhost:5000`) — Portalcrane resolves the actual target host before
deciding which permission applies, so that path can't be used to bypass
local folder checks.

### Tracking transfer jobs

```bash
GET    /api/transfer/jobs           # jobs visible to the caller (admins see all)
GET    /api/transfer/jobs/{job_id}  # status of one job
DELETE /api/transfer/jobs/{job_id}  # delete a job record + clean up its OCI directory
```

A job progresses through `pending → pulling → scanning →
scan_clean|scan_vulnerable|scan_skipped → pushing → done|failed`. A running
job (actively pulling/pushing) can't be cancelled mid-flight, but its
record and any partial OCI directory can be removed once it settles.
