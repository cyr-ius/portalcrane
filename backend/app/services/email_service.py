"""
Portalcrane - Email Service
===========================
Delivers the audit log by email over SMTP.

The SMTP server is configured by an administrator through the Network settings
tab and persisted alongside the other network overrides (proxy, syslog) in
DATA_DIR/proxy_config.json. Two operations are exposed:

  - send a small connectivity test email (verify credentials / reachability)
  - send the recent audit events as a ``.jsonl`` attachment

Only the Python standard library (``smtplib`` / ``email``) is used, so no
extra dependency is required.
"""

import json
import logging
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage

from .audit_service import get_recent_audit_events
from .proxy_service import EmailSettings

logger = logging.getLogger(__name__)


def _recipients(cfg: EmailSettings) -> list[str]:
    """Parse the comma-separated recipient list into individual addresses."""
    return [addr.strip() for addr in cfg.to_addresses.split(",") if addr.strip()]


def _send(cfg: EmailSettings, message: EmailMessage) -> None:
    """Open an SMTP connection honouring the configured security mode and send."""
    timeout = 15

    if cfg.security == "ssl":
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            cfg.host, cfg.port, timeout=timeout, context=context
        ) as smtp:
            if cfg.username:
                smtp.login(cfg.username, cfg.password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(cfg.host, cfg.port, timeout=timeout) as smtp:
        if cfg.security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if cfg.username:
            smtp.login(cfg.username, cfg.password)
        smtp.send_message(message)


def _validate(cfg: EmailSettings) -> str | None:
    """Return an error string when the configuration is incomplete, else None."""
    if not cfg.host:
        return "SMTP host is not configured"
    if not cfg.from_address:
        return "Sender address is not configured"
    if not _recipients(cfg):
        return "No recipient address is configured"
    return None


def send_test_email(cfg: EmailSettings) -> tuple[bool, str]:
    """Send a small connectivity test email. Returns (success, message)."""
    error = _validate(cfg)
    if error:
        return False, error

    message = EmailMessage()
    message["Subject"] = f"{cfg.subject} — test"
    message["From"] = cfg.from_address
    message["To"] = ", ".join(_recipients(cfg))
    message.set_content(
        "This is a Portalcrane email connectivity test.\n"
        f"Sent at {datetime.now(UTC).isoformat()}."
    )

    try:
        _send(cfg, message)
        return True, "Test email sent successfully"
    except Exception as exc:  # noqa: BLE001 — surface any SMTP error to the admin
        logger.warning("Email test failed: %s", exc)
        return False, str(exc)


def send_audit_log_email(cfg: EmailSettings) -> tuple[bool, str]:
    """Email the recent audit events as a ``.jsonl`` attachment.

    Returns (success, message).
    """
    error = _validate(cfg)
    if error:
        return False, error

    events = get_recent_audit_events(limit=10000)
    if not events:
        return False, "No audit events to send"

    payload = "\n".join(json.dumps(event) for event in events)
    generated_at = datetime.now(UTC).isoformat()

    message = EmailMessage()
    message["Subject"] = cfg.subject
    message["From"] = cfg.from_address
    message["To"] = ", ".join(_recipients(cfg))
    message.set_content(
        f"Portalcrane audit log export.\n"
        f"Generated at {generated_at}.\n"
        f"{len(events)} event(s) attached as audit-log.jsonl."
    )
    message.add_attachment(
        payload.encode("utf-8"),
        maintype="application",
        subtype="json",
        filename="audit-log.jsonl",
    )

    try:
        _send(cfg, message)
        return True, f"Audit log sent ({len(events)} event(s))"
    except Exception as exc:  # noqa: BLE001 — surface any SMTP error to the admin
        logger.warning("Audit log email failed: %s", exc)
        return False, str(exc)
