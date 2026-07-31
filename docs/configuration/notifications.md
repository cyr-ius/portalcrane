# Notifications (Email & Syslog)

Portalcrane keeps an in-memory [audit log](../features/system-maintenance.md#audit-logs)
of every API operation. Two independent delivery channels can export it (or
notify on individual events in real time): SMTP email and syslog
forwarding. Both are configured from **Settings → Network** and both can be
set via environment variables at boot and refined later from the UI without
a restart.

## Email

Configuration lives under `EMAIL_*` — see the full list in
[Environment Variables → Email](environment-variables.md#email-optional).
Only the Python standard library (`smtplib`/`email`) is used, so no extra
dependency or external service is required beyond SMTP credentials.

```bash
EMAIL_ENABLED=true
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_SECURITY=starttls        # none | starttls | ssl
EMAIL_USERNAME=notifications@example.com
EMAIL_PASSWORD=<smtp-password>
EMAIL_FROM_ADDRESS=portalcrane@example.com
EMAIL_TO_ADDRESSES=ops@example.com,security@example.com
```

Three distinct behaviors are available, and they can be combined:

| Behavior                   | Trigger                                                            | Setting                                    |
| -------------------------- | ------------------------------------------------------------------ | ------------------------------------------ |
| **On-demand export**       | Manual click ("Send now") or `POST /api/network/email/send`        | Always available once `EMAIL_ENABLED=true` |
| **Per-login notification** | Every web login/logout                                             | `EMAIL_NOTIFY_LOGIN=true`                  |
| **Per-event notification** | Every other audited operation (push, delete, permission change, …) | `EMAIL_NOTIFY_AUDIT=true`                  |

The on-demand export sends the recent audit events as a `.jsonl` attachment
to all `EMAIL_TO_ADDRESSES`. Test connectivity before relying on it:

```bash
curl -X POST http://<host>:8000/api/network/email/test \
  -H "Authorization: Bearer <admin-token>"
```

## Syslog forwarding

Available in the **Network** tab, syslog forwarding sends audit events (and
optionally the application's own logs) to a remote syslog collector over
UDP, TCP, or TCP+TLS.

| Setting                                         | Description                                        | Default   |
| ----------------------------------------------- | -------------------------------------------------- | --------- |
| `syslog_enabled`                                | Master switch                                      | `false`   |
| `syslog_host` / `syslog_port`                   | Collector address                                  | — / `514` |
| `syslog_protocol`                               | `udp`, `tcp`, or `tcp+tls`                         | `udp`     |
| `syslog_rfc`                                    | Message format: `rfc3164` or `rfc5424`             | `rfc5424` |
| `syslog_forward_audit`                          | Forward audit-log events                           | `true`    |
| `syslog_forward_uvicorn`                        | Also forward general application logs              | `false`   |
| `syslog_tls_verify`                             | Verify the collector's TLS certificate (`tcp+tls`) | `true`    |
| `syslog_tls_ca_cert`                            | Custom CA bundle path for `tcp+tls`                | —         |
| `syslog_auth_enabled`                           | Enable authenticated syslog                        | `false`   |
| `syslog_auth_username` / `syslog_auth_password` | Credentials for authenticated syslog               | —         |

Applying a syslog configuration change re-attaches Python logging handlers
in-process immediately — no container restart needed. Verify connectivity
with a one-off test message:

```bash
curl -X POST http://<host>:8000/api/network/syslog/test \
  -H "Authorization: Bearer <admin-token>"
```

which emits a structured test entry (`{"event": "syslog_test", ...}`)
through the active handler so you can confirm it reaches your collector
(rsyslog, syslog-ng, a SIEM, Loki via a syslog input, etc.).

## Choosing a channel

- Use **email** if you want a human to be notified of security-relevant
  events (logins, permission changes) without operating log
  infrastructure.
- Use **syslog** if you already centralize logs (ELK/OpenSearch, Loki,
  Splunk, a SIEM) and want Portalcrane's audit trail to flow into the same
  pipeline as everything else.
- Both can run at once — email for a small on-call rotation, syslog for
  long-term retention and correlation.
