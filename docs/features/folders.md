# Folder-Based Access Control

A **folder** is a named prefix applied to image repository paths — for
example, a folder named `production` scopes permissions to every image
pushed as `production/*`, like `production/my-api:1.4.0`.

Folders are managed under **Settings → Folders** (`/api/folders`, admin
only for CRUD). Permissions on a folder are always granted to
[groups](users-groups-permissions.md#groups), never directly to a user.

## The four permissions

Each folder carries an independent set of four boolean flags **per
group**:

| Permission          | Governs                                                                                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `can_pull`          | Read images from the **local** embedded registry under this folder                                                                                       |
| `can_push`          | Write / delete images in the **local** embedded registry under this folder                                                                               |
| `can_pull_external` | Pull images **into** Portalcrane **from** a genuinely external registry (Docker Hub, a saved registry, or an ad-hoc host) via the Staging/Transfer pages |
| `can_push_external` | Push images **out** of Portalcrane **to** a genuinely external registry via the Staging/Transfer pages                                                   |

The `_external` permissions are **independent** from their local
counterparts — they govern transfers with external registries, not reads
and writes on the local registry. A group could have `can_pull_external`
without `can_pull`, letting a team stage images from Docker Hub without
being able to browse what's already stored locally, for example.

```bash
curl -X PUT http://<host>:8000/api/folders/<folder_id>/permissions \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{
        "group_id": "<backend-team-group-id>",
        "can_pull": true,
        "can_push": true,
        "can_pull_external": true,
        "can_push_external": false
      }'
```

## How a repository path resolves to a folder

- Only the **first path segment** matters: `production/editeur/image`
  resolves to the folder named `production`, regardless of what comes
  after.
- If no folder matches the first segment — including images with **no**
  prefix at all, like `nginx` — the path falls back to the special
  `__root__` folder.

```bash
curl -X POST http://<host>:8000/api/folders \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "production", "description": "Production images"}'
```

Folder names must be lowercase with no spaces or slashes — Portalcrane
normalizes and validates this at creation time.

## The `__root__` folder

`__root__` is the catch-all folder:

- Created automatically at startup; it **cannot be deleted**.
- Its description can still be edited by admins.
- Its permissions are managed exactly like any other folder's.
- It catches images with no prefix (`nginx`) and images whose prefix
  doesn't match any declared folder (`editeur/nginx` when no `editeur`
  folder exists).

If you want _some_ baseline pull access for everyone without creating a
dedicated folder for every top-level namespace, grant it on `__root__`.

## Default-deny

A user whose groups have **no** matching permission entry on the resolved
folder is always denied — there's no implicit access. This applies
per-operation: a group with `can_pull` but not `can_push` on `staging/`
can `docker pull staging/*` but any `docker push staging/*` gets `403`.

## Worked example

Goal: the `backend-team` group can freely read/write anything under
`backend/`, and can also pull straight from Docker Hub into staging, but
cannot push anywhere external and has no access to anything outside
`backend/`.

```bash
# 1. Create the folder
curl -X POST http://<host>:8000/api/folders \
  -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"name": "backend"}'

# 2. Grant the group's permissions on it
curl -X PUT http://<host>:8000/api/folders/<folder_id>/permissions \
  -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{
        "group_id": "<backend-team-group-id>",
        "can_pull": true,
        "can_push": true,
        "can_pull_external": true,
        "can_push_external": false
      }'
```

`alice` (a member of `backend-team`) can now:

- ✅ `docker pull <host>:8000/backend/my-api:1.0`
- ✅ `docker push <host>:8000/backend/my-api:1.1`
- ✅ Pull `redis:7.2` from Docker Hub into a staging job destined for `backend/`
- ❌ `docker pull <host>:8000/frontend/app:1.0` (no grant on `frontend/`)
- ❌ Push a staged image out to an external registry (no `can_push_external`
  anywhere)

## Discovery endpoints

The frontend (and any API client) can ask what the **current user** is
allowed to do without walking every folder manually:

| Endpoint                             | Returns                                        |
| ------------------------------------ | ---------------------------------------------- |
| `GET /api/folders/mine`              | Folder names the user can pull from            |
| `GET /api/folders/pushable`          | Folder names the user can push to              |
| `GET /api/folders/pullable-external` | Folder names granting external-pull capability |
| `GET /api/folders/pushable-external` | Folder names granting external-push capability |
| `GET /api/folders/names`             | All folder names (for admin selectors)         |

These power the folder pickers in the Staging and Transfer pages, so a
non-admin only ever sees destinations they're actually allowed to use.
