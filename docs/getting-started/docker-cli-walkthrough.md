# Docker CLI Walkthrough: Login, Pull & Push

This is a hands-on walkthrough of the plain Docker CLI against Portalcrane's
registry proxy — login, pull, push, and logout — using
[`traefik/whoami`](https://hub.docker.com/r/traefik/whoami) as a disposable
test image. `whoami` is a tiny (~8 MB) HTTP server that echoes back request
and network info, which makes it a convenient smoke test: once it's running,
a single `curl` immediately proves the whole pull/push chain actually
worked.

The two scenarios below show exactly what changes when
`REGISTRY_PROXY_AUTH_ENABLED` is `true` (the default) versus `false`.

## Before you start

- Portalcrane is reachable at `<host>:8000`.
- You have an account — local, OIDC, or the built-in admin — with `can_pull`
  and `can_push` on the destination folder. Since we push `whoami` with no
  namespace prefix, it lands in the [`__root__` folder](../features/folders.md#the-__root__-folder);
  make sure your account (or a group it belongs to) has both permissions
  granted there. See [Folder-Based Access Control](../features/folders.md)
  if you need to set that up first.
- A [Personal Access Token](../features/personal-access-tokens.md) with the
  **Docker** scope. This is not optional: the registry proxy's `docker
login` only accepts a Docker-scoped PAT as the password — your account's
  real password is rejected there, even for the built-in admin. Generate
  one from the account menu → Personal Access Tokens before continuing.

## Scenario A — Authentication enabled (default)

This is the out-of-the-box behavior (`REGISTRY_PROXY_AUTH_ENABLED=true`).

### 1. Log in

Use your Docker-scoped Personal Access Token as the password — **not**
your account's real password:

```console
$ docker login <host>:8000 -u alice -p <docker-scoped-token>
Login Succeeded
```

If you try your real account password instead, the proxy rejects it
outright, even for the built-in admin:

```console
$ docker login <host>:8000
Username: alice
Password:
Error response from daemon: login attempt to https://<host>:8000/v2/ failed with status: 401 Unauthorized
```

!!! note "Login succeeding isn't the same as having access"
    A valid PAT only proves your **identity**. Whether a given `pull` or
    `push` is actually allowed is decided **per request**, against the
    folder permissions of the image path you're touching — see step 3 for
    what a denied push looks like.

### 2. Pull `whoami` from Docker Hub

This goes straight to Docker Hub, onto your local machine — Portalcrane
isn't involved yet:

```console
$ docker pull traefik/whoami
Using default tag: latest
latest: Pulling from traefik/whoami
...
Status: Downloaded newer image for traefik/whoami:latest
```

### 3. Tag it for Portalcrane and push

```bash
docker tag traefik/whoami <host>:8000/whoami:latest
docker push <host>:8000/whoami:latest
```

With push access on the folder, you'll see:

```console
$ docker push <host>:8000/whoami:latest
The push refers to repository [<host>:8000/whoami]
...
latest: digest: sha256:2fc8...  size: 856
```

Without it, the proxy returns `403` and Docker surfaces Portalcrane's own
error message:

```console
$ docker push <host>:8000/whoami:latest
denied: Folder access denied: push permission required
```

### 4. Pull it back from Portalcrane

Remove the local copy first so the next pull can only come from Portalcrane,
not Docker's local cache:

```bash
docker rmi <host>:8000/whoami:latest
docker pull <host>:8000/whoami:latest
```

### 5. Run it and confirm

```bash
docker run --rm -d --name whoami-test -p 8080:80 <host>:8000/whoami:latest
curl http://localhost:8080
```

```text
Hostname: 3f1a9c8e2b4d
IP: 127.0.0.1
IP: 172.17.0.3
GET / HTTP/1.1
Host: localhost:8080
User-Agent: curl/8.5.0
Accept: */*
```

```bash
docker stop whoami-test
```

### 6. Log out

```bash
docker logout <host>:8000
```

Docker's credential store is now cleared for that host. Try pulling again
without logging back in:

```console
$ docker pull <host>:8000/whoami:latest
Error response from daemon: Head "https://<host>:8000/v2/whoami/manifests/latest": unauthorized
```

With no `Authorization` header on the request, the proxy responds `401
Unauthorized` — exactly the same as if you'd never logged in at all.

## Scenario B — Authentication disabled

Set `REGISTRY_PROXY_AUTH_ENABLED=false` and restart Portalcrane. The proxy
now grants **every** request unconditionally, before even looking at
credentials or folder permissions — there's no `docker login` step, and no
permission check runs for anyone, admin or not:

```bash
docker pull traefik/whoami
docker tag traefik/whoami <host>:8000/whoami:latest
docker push <host>:8000/whoami:latest    # succeeds, no docker login ever run
docker pull <host>:8000/whoami:latest    # anyone can pull, no credentials needed
```

`docker logout <host>:8000` is a no-op here, since nothing was ever
authenticated in the first place.

!!! danger "Only for fully trusted networks"
    This isn't "anonymous read access with folder rules still enforced" —
    it's a genuinely open registry: **any** client that can reach port
    `8000` can push or pull **any** image, regardless of folders, groups, or
    admin status. Only use this on a network you fully control, and never
    expose that port to the internet while it's set this way.

## At a glance

|                             | Auth enabled (default)                               | Auth disabled              |
| --------------------------- | ---------------------------------------------------- | -------------------------- |
| `docker login` required     | Yes                                                  | No                         |
| Credentials checked         | Docker-scoped PAT only (a real password is rejected) | Not checked at all         |
| Folder permissions enforced | Yes, per pull/push, per folder                       | **No — bypassed entirely** |
| `docker logout` effect      | Clears local credentials; next request gets `401`    | No-op                      |

See [Environment Variables → Registry](../configuration/environment-variables.md#registry)
for the underlying setting, and
[Folder-Based Access Control](../features/folders.md) for how `can_pull`
and `can_push` are resolved when auth is enabled.
