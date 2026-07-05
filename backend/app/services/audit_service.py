"""
Portalcrane - Audit Service
============================
Logs all registry pull/push events that transit through the registry proxy.

Each event is emitted as a structured JSON line to the "portalcrane.audit"
logger. In production, route this logger to your SIEM, ELK stack, or a
dedicated audit log file by configuring Python logging in your deployment.

Default output (stdout) example:
  {"event": "registry_pull", "timestamp": "2025-02-21T10:00:00+00:00",
   "path": "v2/myimage/manifests/latest", "method": "GET",
   "http_status": 200, "bytes": 1024, "elapsed_s": 0.042,
   "client_ip": "192.168.1.10"}

To persist audit logs independently of application logs, add a handler in
your logging configuration:

  [loggers]
  keys=portalcrane.audit

  [handlers]
  keys=auditFileHandler

  [handler_auditFileHandler]
  class=FileHandler
  args=('/var/log/portalcrane/audit.log', 'a')
  formatter=jsonFormatter
"""

import json
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import Request
from jose import JWTError, jwt
from pydantic import BaseModel

from ..config import DATA_DIR, Settings
from ..core.jwt import ALGORITHM

audit_logger = logging.getLogger("portalcrane.audit")

_audit_max_events = 100
_recent_audit_events: deque[dict[str, Any]] = deque(maxlen=_audit_max_events)
_audit_events_lock = Lock()
_AUDIT_FILE_PATH = Path(f"{DATA_DIR}/audit-events.jsonl")
_AUDIT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _set_audit_max_events(max_events: int) -> None:
    global _audit_max_events, _recent_audit_events

    normalized_max = max(1, int(max_events))
    with _audit_events_lock:
        _audit_max_events = normalized_max
        _recent_audit_events = deque(_recent_audit_events, maxlen=_audit_max_events)


def _trim_audit_file(max_events: int) -> None:
    events = _read_recent_events_from_disk(limit=max_events)
    if not events:
        if _AUDIT_FILE_PATH.exists():
            _AUDIT_FILE_PATH.write_text("", encoding="utf-8")
        return

    with _AUDIT_FILE_PATH.open("w", encoding="utf-8") as file_obj:
        for event in events:
            file_obj.write(f"{json.dumps(event)}\n")


class AuditEvent(BaseModel):
    event: str
    timestamp: str
    path: str | None = None
    method: str | None = None
    client_ip: str | None = None
    http_status: int
    bytes: int
    elapsed_s: float = 0.0
    username: str | None = None
    # Authentication origin for web_login events: "local" or "oidc".
    auth_source: str | None = None


def _store_recent_event(event: dict[str, Any]) -> None:
    """Store an audit event in memory for live UI access."""
    with _audit_events_lock:
        _recent_audit_events.append(event)
        max_events = _audit_max_events

    with _AUDIT_FILE_PATH.open("a", encoding="utf-8") as file_obj:
        file_obj.write(f"{json.dumps(event)}\n")

    _trim_audit_file(max_events=max_events)


def _read_recent_events_from_disk(limit: int) -> list[dict[str, Any]]:
    if not _AUDIT_FILE_PATH.exists():
        return []

    events: deque[dict[str, Any]] = deque(maxlen=limit)
    with _AUDIT_FILE_PATH.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
    return list(events)


def get_recent_audit_events(limit: int = 200) -> list[dict[str, Any]]:
    """Return the latest audit events (newest first)."""
    with _audit_events_lock:
        in_memory_events = list(_recent_audit_events)

    if len(in_memory_events) < limit:
        disk_events = _read_recent_events_from_disk(limit=limit)
        if len(disk_events) > len(in_memory_events):
            in_memory_events = disk_events

    return in_memory_events[-limit:][::-1]


class AuditService:
    """Structured audit logger for registry proxy events."""

    def __init__(self, settings: Settings):
        self.settings = settings
        _set_audit_max_events(settings.audit_max_events)

        self.path: str | None = None
        self.method: str | None = None
        self.http_status: int = 200
        self.size: int = 0
        self.elapsed: float = 0.0
        self.client_ip: str | None = None
        self.username: str | None = None

    async def log(
        self,
        subject: str,
        path: str | None = None,
        method: str | None = None,
        status: int = 200,
        size: int = 0,
        elapsed: float = 0.0,
        client_ip: str | None = None,
        username: str | None = None,
        auth_source: str | None = None,
    ) -> None:
        """
        Log a registry pull (GET/HEAD) event.

        Parameters
        ----------
        path:        The v2 API path, e.g. "library/nginx/manifests/latest"
        method:      HTTP method (GET or HEAD)
        status:      HTTP response status code from the upstream registry
        size:        Response body size in bytes
        elapsed:     Round-trip time in seconds
        client_ip:   IP address of the Docker client (or reverse-proxy forwarded IP)
        username:    Username
        auth_source: Authentication origin for web_login events ("local"/"oidc")
        """

        event = AuditEvent(
            event=subject,
            timestamp=datetime.now(UTC).isoformat(),
            path=path or self.path,
            method=method or self.method,
            http_status=status or self.http_status,
            bytes=size or self.size,
            elapsed_s=round(elapsed, 3),
            client_ip=client_ip or self.client_ip,
            username=username or self.username,
            auth_source=auth_source,
        ).model_dump()

        _store_recent_event(event)
        audit_logger.info(json.dumps(event))


def _extract_username_from_request(request: Request, settings: Settings) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    else:
        # Web UI requests authenticate via cookie, not the Authorization header
        # (see core.jwt.get_current_user which accepts both sources).
        token = request.cookies.get(settings.auth_cookie_name, "").strip()

    if not token:
        return None

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None

    username = payload.get("sub")
    return username if isinstance(username, str) and username else None


# Login endpoints emit a dedicated web_login event carrying the resolved
# username (unavailable from the request itself, which has no session cookie
# yet). Skip them in the generic middleware to avoid a duplicate, username-less
# entry.
_LOGIN_PATHS = frozenset({"/api/auth/login", "/api/auth/token", "/api/oidc/callback"})


async def log_web_login(
    request: Request,
    username: str,
    settings: Settings,
    auth_source: str,
) -> None:
    """Log a successful web UI login (local or OIDC) to the audit stream.

    Emitted from the login endpoints themselves rather than the generic
    middleware, because the authenticated username is only known here — the
    incoming request carries no session cookie yet.
    """
    audit = AuditService(settings)
    await audit.log(
        subject="web_login",
        path=request.url.path,
        method=request.method,
        status=200,
        client_ip=request.client.host if request.client else None,
        username=username,
        auth_source=auth_source,
    )


async def log_web_ui_action(
    request: Request,
    status_code: int,
    settings: Settings,
    elapsed_s: float,
) -> None:
    """Log web UI API actions (non-GET requests) to the audit stream."""
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return

    path = request.url.path
    if not path.startswith("/api/"):
        return

    if path.startswith("/api/system/audit/logs"):
        return

    if path in _LOGIN_PATHS:
        return

    audit = AuditService(settings)
    await audit.log(
        subject="web_ui_action",
        path=path,
        method=request.method,
        status=status_code,
        elapsed=elapsed_s,
        client_ip=request.client.host if request.client else None,
        username=_extract_username_from_request(request, settings),
    )
