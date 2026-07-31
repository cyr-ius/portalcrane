# TLS & Reverse Proxy

Portalcrane can terminate TLS itself, or sit behind a reverse proxy that
terminates it for you. Either way, if you expose the UI beyond a trusted
local network, **enable HTTPS** — the login flow sets an HttpOnly session
cookie and, without TLS, credentials and that cookie travel in the clear.

## Option A — native TLS

Mount a certificate and private key into the container and point
`PRIVATE_KEY` / `PUBLIC_KEY` at them:

```bash
docker run -d \
  --name portalcrane \
  -p 8443:8000 \
  -v /portalcrane_data:/var/lib/portalcrane \
  -v /path/to/certs:/certs:ro \
  -e PRIVATE_KEY=/certs/privkey.pem \
  -e PUBLIC_KEY=/certs/fullchain.pem \
  cyrius44/portalcrane:latest
```

Uvicorn then serves HTTPS directly, using `--ssl-keyfile=/certs/privkey.pem
--ssl-certfile=/certs/fullchain.pem`. `docker login` / `pull` / `push` (which
all go through the same port) will then use `https://` as their transport.

## Option B — reverse proxy termination

Leave `PRIVATE_KEY` / `PUBLIC_KEY` unset (Portalcrane serves plain HTTP on
`8000`) and terminate TLS at a reverse proxy — nginx, Traefik, Caddy, an
ingress controller, etc. Two things matter here:

1. **Forward the `Host` header untouched** so redirect URIs and the OIDC
   flow resolve correctly.
2. **Set `TRUSTED_PROXIES`** to your proxy's network so Portalcrane
   correctly resolves the real client IP (see below) instead of attributing
   every request to the proxy.

Example nginx snippet:

```nginx
location / {
    proxy_pass http://portalcrane:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## Trusted proxies & client IP resolution

Portalcrane resolves the "real" client IP from `Forwarded` /
`X-Forwarded-For` / `X-Real-IP` headers, but **only trusts them when the
direct TCP peer is in `TRUSTED_PROXIES`**. This resolved IP feeds both the
[audit log](../features/system-maintenance.md#audit-logs) and the
[per-IP rate limiter](#rate-limiting).

```bash
TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12
```

!!! danger "Only list proxies you actually control"
    Trusting a CIDR range means anything inside it can spoof a client IP by
    setting `X-Forwarded-For` itself. Never set this to `0.0.0.0/0` or a
    public range.

If you leave `TRUSTED_PROXIES` empty (the default), every request is keyed
by the real TCP peer — safe, but if Portalcrane sits behind an untrusted-by-config
proxy, every client will appear to share the proxy's IP, collapsing them
into a single rate-limit bucket and a single audit-log source address.

## Rate limiting

Enabled by default on all `/api/*` routes, with a stricter dedicated bucket
for the login endpoint to slow down credential brute-forcing:

| Variable                          | Default           | Applies to                         |
| --------------------------------- | ----------------- | ---------------------------------- |
| `RATE_LIMIT_ENABLED`              | `true`            | Master switch                      |
| `RATE_LIMIT_WINDOW_SECONDS`       | `60`              | General `/api/*` sliding window    |
| `RATE_LIMIT_MAX_REQUESTS`         | `100`             | Requests per IP per general window |
| `RATE_LIMIT_LOGIN_PATH`           | `/api/auth/login` | Path matched for the login bucket  |
| `RATE_LIMIT_LOGIN_WINDOW_SECONDS` | `300`             | Login sliding window               |
| `RATE_LIMIT_LOGIN_MAX_ATTEMPTS`   | `5`               | Login attempts per IP per window   |

The rate-limit state is kept in the process's memory. That's fine for the
single-Uvicorn-process deployment Portalcrane ships as, but it means the
limits are **not** shared across replicas if you were to scale
horizontally behind a load balancer — each replica would enforce its own
independent budget.

## HTTP proxy for outbound calls

If Portalcrane itself needs to go through a corporate proxy to reach the
internet (Docker Hub search, OIDC discovery, pulling from external
registries), set the standard variables:

```bash
HTTP_PROXY=http://proxy.internal:3128
HTTPS_PROXY=http://proxy.internal:3128
NO_PROXY=localhost,127.0.0.1,10.0.0.0/8
```

These are applied to both outbound `httpx` requests and every `skopeo`
subprocess the staging/transfer pipelines spawn, and can also be managed
live from **Settings → Network** without a restart.
