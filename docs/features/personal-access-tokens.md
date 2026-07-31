# Personal Access Tokens

Every authenticated user — local or OIDC-provisioned — can generate their
own **Personal Access Tokens (PATs)** from the account menu
(**account avatar → Personal Access Tokens**). They're especially useful
for OIDC users, who have no local password to hand to `docker login` or
a CI system.

The raw token value is shown **only once**, at creation time. Internally it
is stored as a bcrypt hash and identified by a unique signed `jti` claim —
Portalcrane never persists (or can display again) the plaintext token.

## Scopes

Every token is created with **exactly one** of two mutually exclusive
scopes:

| Scope      | `docker login` | REST API / Swagger | 16-char short token |
| ---------- | :------------: | :----------------: | :-----------------: |
| **Docker** |       ✅       |         ❌         |         ✅          |
| **API**    |       ❌       |         ✅         |         ❌          |

A token created for one scope is rejected on the other — a Docker-scoped
CI credential can never authenticate against the REST API, and an
API-scoped key can never be used to push or pull an image. This means a
leaked CI secret has a strictly bounded blast radius.

## Creating a token

```bash
curl -X POST http://<host>:8000/api/auth/tokens \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "CI pipeline", "scope": "docker", "expires_in_days": 90}'
```

Response (abridged) — **save `raw_token` now, it won't be shown again**:

```json
{
  "id": "b3f1...",
  "name": "CI pipeline",
  "scope": "docker",
  "created_at": "2026-07-31T10:00:00Z",
  "expires_at": "2026-10-29T10:00:00Z",
  "raw_token": "pct_eyJhbGciOi...",
  "short_token": "aZ3xQ...16chars",
  "short_token_hint": "aZ3x…6chr"
}
```

`expires_in_days` defaults to **90 days** if omitted. `short_token` is only
issued for `docker`-scoped tokens.

## Using a Docker-scoped token

Use either the full token or the short 16-character convenience token as
the password for `docker login`:

```bash
docker login <host>:8000 -u alice -p pct_eyJhbGciOi...
# or, shorter:
docker login <host>:8000 -u alice -p aZ3xQ...16chars
```

The short token is purely a `docker login` convenience for typing/pasting
by hand — it is **never** accepted where an API scope is required.

## Using an API-scoped token

Send it as a Bearer token on any REST API call:

```bash
curl -H "Authorization: Bearer pct_eyJhbGciOi..." http://<host>:8000/api/auth/me
```

In **Swagger UI** (`/api/docs`, enabled with `SWAGGER_ENABLED=true`): click
**Authorize**, choose **PersonalAccessToken**, paste the token, and every
request in that session is authenticated as you.

!!! info "The registry proxy isn't in Swagger"
The `/v2/...` registry proxy endpoints implement the Docker Registry
HTTP API and are intentionally hidden from Swagger — they're only
meaningful to a Docker client, not a REST API consumer.

## Revoking a token

```bash
curl -X DELETE http://<host>:8000/api/auth/tokens/<token_id> \
  -H "Authorization: Bearer <token>"
```

Users can revoke only their own tokens; admins can revoke anyone's.
Expired or revoked tokens are rejected immediately on next use.
Deleting a user account also revokes every one of their tokens.

## Disabling the feature entirely

Set `API_KEYS_ENABLED=false` to disarm PATs system-wide: token endpoints
return `403`, existing API-scoped keys stop working against the REST API,
and the token-generation panel disappears from the account menu. Existing
Docker-scoped tokens used for `docker login` are also rejected once the
feature is disabled.
