# Image & Tag Management

The image browser works against any registry known to Portalcrane — the
local embedded one (`registry_id = __local__`, a hidden system entry) or
any [saved external registry](external-registries.md). Every endpoint below
is namespaced under `/api/images/{registry_id}`.

## Browsing

```bash
curl "http://<host>:8000/api/images/__local__?search=redis&page=1&page_size=20" \
  -H "Authorization: Bearer <token>"
```

- `search` filters repositories by name.
- `page` / `page_size` (5–200) paginate the catalog.
- For non-admins, visibility is filtered **before** pagination so
  `total`/`total_pages` stay consistent with what's actually visible:
  - **Local registry** — per-folder `can_pull` on each repository path.
  - **External registry** — the coarse `can_pull_external` capability
    (a foreign path like `library/nginx` doesn't map to a Portalcrane
    folder, so it can't be scoped per-folder).

If the target registry is temporarily unreachable, these endpoints return
`503 Service Unavailable` instead of a generic `500`, so the UI can show a
"registry unreachable" state rather than crashing.

## Tags

```bash
# List tags for a repository
curl "http://<host>:8000/api/images/__local__/tags?repository=backend/api" \
  -H "Authorization: Bearer <token>"

# Detailed metadata for one tag — layers, labels, env vars, architecture...
curl "http://<host>:8000/api/images/__local__/tags/detail?repository=backend/api&tag=1.4.0" \
  -H "Authorization: Bearer <token>"
```

The "Advanced mode" in the UI is powered by the `/tags/detail` endpoint and
surfaces everything skopeo/registry manifests expose: layer digests and
sizes, OCI labels, environment variables baked into the image, and target
architecture — handy for confirming exactly what's running in production
without pulling the image yourself.

## Retagging

Add a new tag to an existing image by copying its manifest — no re-pull
needed:

```bash
curl -X POST "http://<host>:8000/api/images/__local__/tags?repository=backend/api" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"source_tag": "1.4.0", "new_tag": "stable"}'
```

Requires push access on the repository's folder.

## Copying to a new repository path

Unlike retagging (same repository, new tag), **copy** moves an image to a
**different repository path** within the local registry — for example,
promoting a staged image into `production/`:

```bash
curl -X POST http://<host>:8000/api/images/copy \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{
        "source_repository": "staging/api",
        "source_tag": "1.4.0",
        "dest_repository": "production/api",
        "dest_tag": "1.4.0"
      }'
```

Requires `can_pull` on the source folder **and** `can_push` on the
destination folder — both are checked before the copy runs. `dest_tag`
defaults to `source_tag` if omitted.

## Deleting

```bash
# Delete a single tag
curl -X DELETE "http://<host>:8000/api/images/__local__/tags?repository=backend/api&tag=0.9.0-rc1" \
  -H "Authorization: Bearer <token>"

# Delete an entire repository (all tags)
curl -X DELETE "http://<host>:8000/api/images/__local__?repository=backend/old-service" \
  -H "Authorization: Bearer <token>"
```

Both require push access on the repository's folder. Deleting marks blobs
for removal at the registry level — actual disk space is reclaimed by
[garbage collection](system-maintenance.md#garbage-collection).

## Ghost repositories

Deleting every tag from a repository can leave an empty, tag-less entry
behind ("ghost" repository) depending on the registry backend. Admins can
list and purge these directly from the local filesystem:

```bash
GET    /api/images/__local__/empty    # list repositories with zero tags
DELETE /api/images/__local__/empty    # purge them from disk
```

## Vulnerability scanning of an image

Any browsed image can be scanned on demand — see
[Vulnerability Scanning](../configuration/vulnerability-scanning.md#on-demand-scanning)
for the endpoint and severity filtering options.
