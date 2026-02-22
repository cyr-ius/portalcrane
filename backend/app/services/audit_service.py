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
from datetime import datetime, timezone

from ..config import Settings

audit_logger = logging.getLogger("portalcrane.audit")


class AuditService:
    """Structured audit logger for registry proxy events."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def log_pull(
        self,
        path: str,
        method: str,
        status: int,
        size: int,
        elapsed: float,
        client_ip: str,
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
        """
        event = {
            "event": "registry_pull",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": path,
            "method": method,
            "http_status": status,
            "bytes": size,
            "elapsed_s": round(elapsed, 3),
            "client_ip": client_ip,
        }
        audit_logger.info(json.dumps(event))

    async def log_push(
        self,
        path: str,
        method: str,
        status: int,
        size: int,
        elapsed: float,
        client_ip: str,
    ) -> None:
        """
        Log a registry push (PUT/POST/PATCH) event.

        Parameters
        ----------
        path:       The v2 API path
        method:     HTTP method (PUT, POST, PATCH)
        status:     HTTP response status code from the upstream registry
        size:       Request body size in bytes
        elapsed:    Round-trip time in seconds
        client_ip:  IP address of the Docker client
        """
        event = {
            "event": "registry_push",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": path,
            "method": method,
            "http_status": status,
            "bytes": size,
            "elapsed_s": round(elapsed, 3),
            "client_ip": client_ip,
        }
        audit_logger.info(json.dumps(event))
