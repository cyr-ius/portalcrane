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
from datetime import datetime, timezone
from threading import Lock
from typing import Any
import time

from pydantic import BaseModel

from ..config import Settings

audit_logger = logging.getLogger("portalcrane.audit")
_recent_audit_events: deque[dict[str, Any]] = deque(maxlen=500)
_audit_events_lock = Lock()


class AuditEvent(BaseModel):
    event: str
    timestamp: str
    path: str | None = None
    method: str | None = None
    client_ip: str | None = None
    http_status: int
    bytes: int
    elapsed_s: float = time.monotonic()
    username: str | None = None


def _store_recent_event(event: dict[str, Any]) -> None:
    """Store an audit event in memory for live UI access."""
    with _audit_events_lock:
        _recent_audit_events.append(event)


def get_recent_audit_events(limit: int = 200) -> list[dict[str, Any]]:
    """Return the latest audit events (newest first)."""
    with _audit_events_lock:
        return list(_recent_audit_events)[-limit:][::-1]


class AuditService:
    """Structured audit logger for registry proxy events."""

    def __init__(self, settings: Settings):
        self.settings = settings

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
    ) -> None:
        """
        Log a registry pull (GET/HEAD) event.

        Parameters
        ----------
        path:       The v2 API path, e.g. "library/nginx/manifests/latest"
        method:     HTTP method (GET or HEAD)
        status:     HTTP response status code from the upstream registry
        size:       Response body size in bytes
        elapsed:    Round-trip time in seconds
        client_ip:  IP address of the Docker client (or reverse-proxy forwarded IP)
        username:   Username
        """

        event = AuditEvent(
            event=subject,
            timestamp=datetime.now(timezone.utc).isoformat(),
            path=path or self.path,
            method=method or self.method,
            http_status=status or self.http_status,
            bytes=size or self.size,
            elapsed_s=round(elapsed, 3),
            client_ip=client_ip or self.client_ip,
            username=username,
        ).model_dump()

        _store_recent_event(event)
        audit_logger.info(json.dumps(event))
