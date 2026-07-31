# Authentication & OIDC

Portalcrane supports three ways to authenticate: the built-in admin
account, local username/password accounts, and OpenID Connect (OIDC / SSO).
All three can coexist, and a Personal Access Token can substitute for a
password once an account exists (see
[Personal Access Tokens](../features/personal-access-tokens.md)).

## Local authentication

- **Built-in admin** — the `admin` account (configurable via
  `ADMIN_USERNAME`), whose password is auto-generated on first launch and
  printed once in the container logs. It cannot be deleted, renamed, or
  demoted; only its password can be changed.
- **Local users** — created by an admin under **Settings → Accounts**, each
  with a username/password pair. See
  [Users, Groups & Permissions](../features/users-groups-permissions.md)
  for the full account model.

Login is a simple JSON POST:

```bash
curl -X POST http://<host>:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<password>"}'
```

The browser flow additionally receives the JWT in an **HttpOnly** cookie
(`pc_token` by default), so the Angular app never touches the token
directly; API clients can use the `access_token` from the JSON response as
a `Bearer` token instead.

Any authenticated user can change their own password:

```bash
curl -X POST http://<host>:8000/api/auth/me/password \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "old", "new_password": "at-least-8-chars"}'
```

OIDC-provisioned accounts have no local password and this endpoint rejects
them — their identity is managed entirely by the external provider.

## OIDC (Single Sign-On)

Set `OIDC_ENABLED=true` and the standard OIDC fields
(`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`,
…) to add an SSO login option alongside local login. All values can also be
set from **Settings → OIDC** — see the full variable list in
[Environment Variables → OIDC](environment-variables.md#oidc-optional).

### Just-in-time provisioning

The first time a user completes the SSO flow, Portalcrane creates a local
record for them (`auth_source: "oidc"`, no password hash). Subsequent
logins upsert that record — in particular, admin status is **recomputed on
every login** when a group-claim mapping is configured (see below), so
promotions/demotions made in your identity provider take effect
immediately, without an admin having to touch Portalcrane's user list.

### Custom CA bundle (private PKI)

If your OIDC provider is fronted by a private CA (self-signed or internal
PKI), supply the CA chain (intermediate + root, concatenated into a single
PEM file) via the standard `SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE`
environment variable, pointing at a mounted file. Outbound OIDC calls then
trust that chain instead of the default `certifi` bundle. Leave both unset
to keep default verification.

### OIDC-only mode & admin bootstrap

By default, OIDC is offered **alongside** local login. Set `OIDC_ONLY=true`
to **disable every local password login — including the built-in
env-admin** — and authenticate solely through your provider.

Because there is no break-glass account in this mode, Portalcrane refuses
to enable it unless admin access is guaranteed to remain reachable. You
need **one** of:

- **Group-claim mapping** — `OIDC_ADMIN_GROUP_CLAIM=groups` and
  `OIDC_ADMIN_GROUP=registry-admins` (make sure your provider's scope
  actually exposes that claim). Admin rights are then re-evaluated on
  **every** SSO login.
- At least one OIDC account **already manually promoted to admin** through
  the Accounts panel, if you don't want to rely on a group claim.

The UI enforces this at the API level too — `PUT /api/oidc/settings` returns
`400` if you try to enable `oidc_only` without one of the above.

### Restricting access to specific users (allowlist)

By default, **every** authenticated SSO user is provisioned as a regular
user. To restrict access, map regular users the same way as admins:

```bash
OIDC_USER_GROUP_CLAIM=groups
OIDC_USER_GROUP=registry-users
OIDC_RESTRICT_TO_GROUPS=true
```

With `OIDC_RESTRICT_TO_GROUPS=true` (or **Restrict access to mapped
groups** ticked in the UI), OIDC access becomes an allowlist: only users
whose claims match the admin **or** the user mapping may log in. Anyone
matching **neither** is denied with `403` and their account is **not
created**. Admin and user group claims may point at different claims from
the token; both are read on every login.

### Anti-usurpation protection

OIDC identities are kept strictly separate from local accounts to prevent
privilege escalation:

- An SSO login is **rejected (403)** when the resolved username matches the
  built-in env-admin username. This stops a provider that returns
  `preferred_username=admin` from inheriting the admin account's rights.
- An SSO login is also rejected when the username collides with an
  **existing local (password-based) account** — an OIDC identity can never
  bind onto a local account.
- Deleting an OIDC-provisioned account adds its username to a revocation
  list (`oidc_revoked.json`); the next SSO callback for that username
  returns `403` instead of silently re-provisioning it.
- A disabled OIDC account (`disabled: true`) is rejected at login without
  being re-enabled.

## Deployment scenario: local-only

Simplest setup — leave `OIDC_ENABLED` at its default (`false`). Only the
admin account plus any local users you create can log in.

## Deployment scenario: mixed local + SSO

Set `OIDC_ENABLED=true` with your provider's details, leave `OIDC_ONLY`
and `OIDC_RESTRICT_TO_GROUPS` at their defaults. Both a "Sign in with
SSO" button and the local username/password form appear on the login page;
any SSO user gets provisioned as a regular (non-admin) user unless the
admin group mapping says otherwise.

## Deployment scenario: SSO-only, group-mapped admins

```bash
OIDC_ENABLED=true
OIDC_ONLY=true
OIDC_ADMIN_GROUP_CLAIM=groups
OIDC_ADMIN_GROUP=registry-admins
OIDC_USER_GROUP_CLAIM=groups
OIDC_USER_GROUP=registry-users
OIDC_RESTRICT_TO_GROUPS=true
```

Only members of `registry-admins` or `registry-users` (as reported by your
IdP's `groups` claim) can log in at all; membership in `registry-admins`
grants admin, re-evaluated on every login. This is the recommended
configuration for centrally managed identity providers (Keycloak, Authentik,
Entra ID, Okta, …).
