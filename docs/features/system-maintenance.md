# System Maintenance & Audit Logs

Everything on this page lives under **Settings → System** and is
**admin-only**.

## Process status

```bash
curl http://<host>:8000/api/system/processes -H "Authorization: Bearer <admin-token>"
```

Returns the live status of every process supervisord manages —
`registry`, `trivy-server`, and `portalcrane` itself — queried through
supervisord's XML-RPC interface. Useful to confirm Trivy actually started
after flipping `TRIVY_ENABLED`, or that the registry survived a garbage
collection run.

## Audit logs

Every API operation is recorded in an in-memory audit log (bounded by
`AUDIT_MAX_EVENTS`, default `100`):

```bash
curl "http://<host>:8000/api/system/audit/logs?limit=200" \
  -H "Authorization: Bearer <admin-token>"
```

Each event includes at least the acting username, the operation, a
timestamp, and the resolved client IP (see
[Trusted proxies](../configuration/tls-reverse-proxy.md#trusted-proxies-client-ip-resolution)
for how that IP is determined behind a reverse proxy). Web login/logout
events are tagged distinctly so they can be routed separately from other
operations by the notification channels below.

The audit log can also be **exported or delivered automatically** by email
or forwarded live to syslog — see
[Notifications](../configuration/notifications.md).

## Garbage collection

Deleting an image or tag only removes registry references; the underlying
blobs are reclaimed by running the registry's garbage collector:

```bash
curl -X POST "http://<host>:8000/api/system/gc?dry_run=false" \
  -H "Authorization: Bearer <admin-token>"

curl http://<host>:8000/api/system/gc -H "Authorization: Bearer <admin-token>"
```

Behind the scenes, Portalcrane:

1. Records disk usage before the run.
2. **Stops** the `registry` process via supervisord (garbage collection
   isn't safe to run against a live registry).
3. Runs `registry garbage-collect` (optionally with `--dry-run`, to preview
   what would be freed without deleting anything).
4. **Restarts** the `registry` process.
5. Reports bytes freed.

!!! warning "Brief registry downtime"
`docker pull`/`push` against Portalcrane will fail for the few seconds
the registry process is stopped. Schedule GC runs during a maintenance
window if the registry sees continuous traffic.

Only one GC job runs at a time — starting a new one while another is
`running` returns `409 Conflict`. Poll `GET /api/system/gc` for
`status: "running" | "done" | "failed"`, `output` (captured
stdout/stderr), and `freed_bytes`/`freed_human` once finished.

## Orphan OCI cleanup

The [staging](staging-pipeline.md) and [transfer](external-registries.md#transfers)
pipelines create temporary OCI layout directories under
`DATA_DIR/cache/staging/`. If a job record is lost (e.g. a crash mid-run)
its directory can be left behind, silently consuming disk space.

```bash
GET    /api/system/orphan-oci    # list OCI directories with no matching job record
DELETE /api/system/orphan-oci    # delete every orphaned directory
```

The listing includes total size, so you can gauge whether it's worth
running the purge before it becomes a disk-pressure problem.

## Ghost repository cleanup

See [Image & Tag Management → Ghost repositories](image-management.md#ghost-repositories)
for repository-level (rather than staging-directory-level) cleanup.

## Putting it together: a maintenance routine

A reasonable weekly admin routine:

1. Check `GET /api/system/orphan-oci` — purge if it's grown.
2. Check `GET /api/images/__local__/empty` — purge ghost repositories.
3. Run a **dry-run** GC (`POST /api/system/gc?dry_run=true`) to see how much
   would be freed.
4. If worthwhile, run a real GC during a low-traffic window.
5. Skim `GET /api/system/audit/logs` (or your syslog/email export) for
   anything unexpected — unfamiliar logins, unexpected permission changes,
   repeated failed pushes.
