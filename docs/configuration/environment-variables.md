# Environment Variables

Portalcrane is configured almost entirely through environment variables at
first boot. Most of them can also be overridden later from the UI
(**Settings**) — in that case the persisted override takes precedence over
the environment variable, as explained in
[Configuration precedence](../architecture.md#configuration-precedence).

## Authentication

| Variable         | Description                                                               | Default       |
| ---------------- | ------------------------------------------------------------------------- | ------------- |
| `ADMIN_USERNAME` | Built-in admin username                                                   | `admin`       |
| `SECRET_KEY`     | JWT signing secret — auto-generated & persisted under `DATA_DIR` if unset | _(generated)_ |

!!! note
The admin **password** is not an environment variable: it is
auto-generated on first launch and printed once in the container logs.
See [Installation → Security notes](../getting-started/installation.md#security-notes).

## TLS / SSL (optional)

Both variables must point to PEM files mounted inside the container. When
set, the FastAPI backend (`uvicorn`) serves HTTPS directly using
`--ssl-keyfile` / `--ssl-certfile`. Leave them unset to serve plain HTTP and
terminate TLS at a reverse proxy instead.

| Variable      | Description                                         | Default |
| ------------- | --------------------------------------------------- | ------- |
| `PRIVATE_KEY` | Path to the TLS private key file (`--ssl-keyfile`)  | —       |
| `PUBLIC_KEY`  | Path to the TLS certificate file (`--ssl-certfile`) | —       |

See [TLS & Reverse Proxy](tls-reverse-proxy.md) for a full walkthrough of
both options.

## Registry

| Variable                      | Description                                         | Default |
| ----------------------------- | --------------------------------------------------- | ------- |
| `REGISTRY_PROXY_AUTH_ENABLED` | Enforce authentication on the `/v2/` registry proxy | `true`  |

## API, Swagger & Personal Access Tokens

| Variable           | Description                                    | Default |
| ------------------ | ---------------------------------------------- | ------- |
| `SWAGGER_ENABLED`  | Serve the Swagger UI on `/api/docs`            | `false` |
| `API_KEYS_ENABLED` | Enable the Personal Access Token (PAT) feature | `true`  |

Set `API_KEYS_ENABLED=false` to disarm PATs entirely: the token endpoints
return `403`, existing API-scoped keys are rejected on the REST API, and the
token generation panel is hidden from the account drawer. See
[Personal Access Tokens](../features/personal-access-tokens.md).

## Reverse proxy & rate limiting

| Variable                          | Description                                                               | Default           |
| --------------------------------- | ------------------------------------------------------------------------- | ----------------- |
| `TRUSTED_PROXIES`                 | Comma-separated CIDR ranges (or bare IPs) of the reverse proxies in front | —                 |
| `RATE_LIMIT_ENABLED`              | Enable the per-IP rate limiter on `/api/*` routes                         | `true`            |
| `RATE_LIMIT_WINDOW_SECONDS`       | Sliding-window duration, in seconds                                       | `60`              |
| `RATE_LIMIT_MAX_REQUESTS`         | Max requests per IP per window, all `/api/*` routes                       | `100`             |
| `RATE_LIMIT_LOGIN_PATH`           | Path of the login endpoint, throttled in its own bucket                   | `/api/auth/login` |
| `RATE_LIMIT_LOGIN_WINDOW_SECONDS` | Sliding-window duration for the login bucket, in seconds                  | `300`             |
| `RATE_LIMIT_LOGIN_MAX_ATTEMPTS`   | Stricter budget per IP per login window                                   | `5`               |

`TRUSTED_PROXIES` sets the reverse-proxy trust boundary. Forwarded client
IPs (`Forwarded` / `X-Forwarded-For` / `X-Real-IP`) are honoured **only**
when the direct TCP peer matches one of these ranges; otherwise the headers
are treated as spoofable and ignored, falling back to the real peer address.
This resolved client IP feeds **both** the audit log and the per-IP rate
limiter.

!!! warning
Leaving `TRUSTED_PROXIES` empty (default) keys every request by the real
TCP peer — safe, but when the app sits behind a reverse proxy **all**
clients then share the proxy's IP and thus a single rate-limit bucket.
Set it to your proxy's network, e.g.
`TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12`, so per-client throttling and
audit logging see the real client address. Only list proxies you
actually control — trusting a range lets anything inside it spoof
client IPs.

The rate-limit state lives in process memory: adequate for the
single-container deployment (one Uvicorn process) but **not** shared across
workers or replicas.

## OIDC (optional)

| Variable                        | Description                                                   | Default                       |
| ------------------------------- | ------------------------------------------------------------- | ----------------------------- |
| `OIDC_ENABLED`                  | Enable OIDC login                                             | `false`                       |
| `OIDC_ISSUER`                   | OIDC issuer URL                                               | —                             |
| `OIDC_CLIENT_ID`                | OIDC client ID                                                | —                             |
| `OIDC_CLIENT_SECRET`            | OIDC client secret                                            | —                             |
| `OIDC_REDIRECT_URI`             | OIDC redirect URI                                             | —                             |
| `OIDC_POST_LOGOUT_REDIRECT_URI` | Post-logout redirect URI                                      | —                             |
| `OIDC_RESPONSE_TYPE`            | OIDC response type                                            | `code`                        |
| `OIDC_SCOPE`                    | OIDC scopes                                                   | `openid profile email groups` |
| `OIDC_ONLY`                     | Disable all local login (OIDC-only mode)                      | `false`                       |
| `OIDC_ADMIN_GROUP_CLAIM`        | OIDC claim carrying the user's groups/roles for admin mapping | —                             |
| `OIDC_ADMIN_GROUP`              | Group value that grants admin                                 | —                             |
| `OIDC_USER_GROUP_CLAIM`         | OIDC claim carrying the user's groups/roles for user mapping  | —                             |
| `OIDC_USER_GROUP`               | Group value that grants regular-user access                   | —                             |
| `OIDC_RESTRICT_TO_GROUPS`       | Restrict access to mapped groups (allowlist)                  | `false`                       |

All of these can be overridden from **Settings → OIDC**. A private-CA
environment is supported via the standard `SSL_CERT_FILE` /
`REQUESTS_CA_BUNDLE` variables. Full details, including OIDC-only mode and
the anti-usurpation rules, are in
[Authentication & OIDC](authentication.md#oidc-single-sign-on).

## Vulnerability Scanning (Trivy)

| Variable               | Description                                                             | Default         |
| ---------------------- | ----------------------------------------------------------------------- | --------------- |
| `TRIVY_ENABLED`        | Master switch — start the embedded Trivy server and enable CVE scanning | `true`          |
| `VULN_SCAN_ENABLED`    | Enable the Trivy CVE scan step in the staging pipeline                  | `true`          |
| `VULN_SCAN_SEVERITIES` | Blocking severity levels (comma-separated)                              | `CRITICAL,HIGH` |
| `VULN_IGNORE_UNFIXED`  | Ignore CVEs with no available fix                                       | `false`         |
| `VULN_SCAN_TIMEOUT`    | Trivy scan timeout                                                      | `5m`            |

Set `TRIVY_ENABLED=false` (also accepts `0`, `no`, `off`) to fully disarm
Trivy: the embedded server is not started by `supervisord`, the DB updater
is skipped, scans never run, and the scan endpoints return `503`. The other
`VULN_*` values are then ignored. See
[Vulnerability Scanning](vulnerability-scanning.md).

## HTTP Proxy (optional)

| Variable      | Description                    | Default               |
| ------------- | ------------------------------ | --------------------- |
| `HTTP_PROXY`  | HTTP proxy URL                 | —                     |
| `HTTPS_PROXY` | HTTPS proxy URL                | —                     |
| `NO_PROXY`    | Comma-separated no-proxy hosts | `localhost,127.0.0.1` |

These are propagated both to outbound `httpx` calls (Docker Hub search,
OIDC discovery) and to every `skopeo` subprocess spawned by the staging and
transfer pipelines. Overridable from **Settings → Network**.

## Logging & audit

| Variable           | Description                                       | Default |
| ------------------ | ------------------------------------------------- | ------- |
| `LOG_LEVEL`        | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)   | `INFO`  |
| `AUDIT_MAX_EVENTS` | Maximum number of audit events retained in memory | `100`   |

## Syslog forwarding (optional)

Configurable from **Settings → Network**; the table below lists every
underlying setting, most of which have no dedicated top-level environment
variable documented in the UI but can still be set via the corresponding
`Settings` field name in uppercase (e.g. `SYSLOG_ENABLED`,
`SYSLOG_HOST`, `SYSLOG_PORT`).

| Setting                                         | Description                                            | Default   |
| ----------------------------------------------- | ------------------------------------------------------ | --------- |
| `syslog_enabled`                                | Enable syslog forwarding                               | `false`   |
| `syslog_host`                                   | Syslog server hostname/IP                              | —         |
| `syslog_port`                                   | Syslog server port                                     | `514`     |
| `syslog_protocol`                               | Transport: `udp`, `tcp`, or `tcp+tls`                  | `udp`     |
| `syslog_rfc`                                    | Message format: `rfc3164` or `rfc5424`                 | `rfc5424` |
| `syslog_forward_audit`                          | Forward audit-log events to syslog                     | `true`    |
| `syslog_forward_uvicorn`                        | Also forward uvicorn/application logs to syslog        | `false`   |
| `syslog_tls_verify`                             | Verify the syslog server's TLS certificate (`tcp+tls`) | `true`    |
| `syslog_tls_ca_cert`                            | Path to a custom CA bundle for `tcp+tls`               | —         |
| `syslog_auth_enabled`                           | Enable authenticated syslog (where supported)          | `false`   |
| `syslog_auth_username` / `syslog_auth_password` | Syslog authentication credentials                      | —         |

Changes apply immediately — Python logging handlers are re-attached
in-process, no restart required.

## Email (optional)

Delivers the audit log by email over SMTP. All values can be overridden
from the UI (**Settings → Network → Email**), which also exposes a **test**
button.

| Variable             | Description                                     | Default                 |
| -------------------- | ----------------------------------------------- | ----------------------- |
| `EMAIL_ENABLED`      | Enable email delivery                           | `false`                 |
| `EMAIL_HOST`         | SMTP server host                                | —                       |
| `EMAIL_PORT`         | SMTP server port                                | `587`                   |
| `EMAIL_SECURITY`     | Connection security (`none`, `starttls`, `ssl`) | `starttls`              |
| `EMAIL_USERNAME`     | SMTP username (leave empty for anonymous)       | —                       |
| `EMAIL_PASSWORD`     | SMTP password                                   | —                       |
| `EMAIL_FROM_ADDRESS` | Sender address                                  | —                       |
| `EMAIL_TO_ADDRESSES` | Comma-separated recipient addresses             | —                       |
| `EMAIL_SUBJECT`      | Subject prefix                                  | `Portalcrane audit log` |
| `EMAIL_NOTIFY_LOGIN` | Email each web login / logout event             | `false`                 |
| `EMAIL_NOTIFY_AUDIT` | Email each other audited operation              | `false`                 |

`EMAIL_NOTIFY_LOGIN` and `EMAIL_NOTIFY_AUDIT` are independent opt-ins for
**automatic per-event** delivery; they are separate from the on-demand
audit-log export, which stays available from the Network panel at any time.
See [Notifications](notifications.md).

## Storage (debug)

| Variable   | Description                              | Default                |
| ---------- | ---------------------------------------- | ---------------------- |
| `DATA_DIR` | Base data directory inside the container | `/var/lib/portalcrane` |

Only change this if you know what you're doing — it must match the mount
point of your persistent volume.
