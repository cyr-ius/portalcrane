"""
Portalcrane - Trivy Service
Vulnerability scanning helpers + override persistence.

Override priority (highest → lowest):
  1. Persisted admin override  (DATA_DIR/vuln_override.json)
  2. Environment variables     (Settings.vuln_*)
"""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from ..config import DATA_DIR, TRIVY_SERVER_URL, Settings, get_settings

_TRIVY_CACHE_DIR = Path(f"{DATA_DIR}/cache/trivy")
_TRIVY_DB_METADATA = Path(f"{_TRIVY_CACHE_DIR}/db/metadata.json")
_OVERRIDE_FILE = Path(DATA_DIR) / "vuln_override.json"
_TRIVY_BINARY = "/usr/local/bin/trivy"
_TRIVY_DB_REFRESH_INTERVAL = 86400

settings = get_settings()
logger = logging.getLogger(__name__)

# ── Override persistence ──────────────────────────────────────────────────────


def load_vuln_override() -> dict | None:
    """
    Load the persisted vuln override from disk.
    Returns None when no override file exists.
    """
    try:
        if _OVERRIDE_FILE.exists():
            return json.loads(_OVERRIDE_FILE.read_text())
    except Exception:
        pass
    return None


def save_vuln_override(data: dict) -> None:
    """Persist admin vuln override to disk so all users pick it up."""
    _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDE_FILE.write_text(json.dumps(data, indent=2))


def clear_vuln_override() -> None:
    """Remove the persisted override file, falling back to env-var defaults."""
    try:
        if _OVERRIDE_FILE.exists():
            _OVERRIDE_FILE.unlink()
    except Exception:
        pass


def resolve_vuln_config(settings: Settings) -> dict:
    """
    Return the effective vuln configuration dict.

    When a persisted override exists, its values win over env vars.
    The returned dict always includes a 'vuln_scan_override' flag so
    the frontend knows whether a custom override is active.
    """
    # Master kill-switch wins over everything: when Trivy is disabled at the
    # container level, scanning is impossible regardless of any override.
    if not settings.trivy_enabled:
        return {
            "trivy_enabled": False,
            "vuln_scan_override": False,
            "vuln_scan_enabled": False,
            "vuln_scan_severities": settings.vuln_scan_severities,
            "vuln_ignore_unfixed": settings.vuln_ignore_unfixed,
            "vuln_scan_timeout": settings.vuln_scan_timeout,
        }

    override = load_vuln_override()
    if override:
        return {
            "trivy_enabled": True,
            "vuln_scan_override": True,
            "vuln_scan_enabled": override.get(
                "vuln_scan_enabled", settings.vuln_scan_enabled
            ),
            "vuln_scan_severities": override.get(
                "vuln_scan_severities", settings.vuln_scan_severities
            ),
            "vuln_ignore_unfixed": override.get(
                "vuln_ignore_unfixed", settings.vuln_ignore_unfixed
            ),
            "vuln_scan_timeout": override.get(
                "vuln_scan_timeout", settings.vuln_scan_timeout
            ),
        }
    return {
        "trivy_enabled": True,
        "vuln_scan_override": False,
        "vuln_scan_enabled": settings.vuln_scan_enabled,
        "vuln_scan_severities": settings.vuln_scan_severities,
        "vuln_ignore_unfixed": settings.vuln_ignore_unfixed,
        "vuln_scan_timeout": settings.vuln_scan_timeout,
    }


# ── Trivy DB helpers ──────────────────────────────────────────────────────────


async def get_trivy_version() -> str | None:
    """Return the installed Trivy binary version (e.g. "0.72.3").

    Runs `trivy --version` and parses the "Version: X.Y.Z" line. Returns None
    when the binary is missing or the call fails, so callers can degrade
    gracefully.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            _TRIVY_BINARY,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        match = re.search(r"Version:\s*([^\s]+)", stdout.decode())
        return match.group(1) if match else None
    except Exception as exc:  # binary missing or unexpected failure
        logger.warning("Unable to read Trivy version: %s", exc)
        return None


async def get_trivy_db_info() -> dict:
    """Return Trivy vulnerability database metadata and freshness status."""
    import json as _json
    from datetime import datetime, timedelta

    info: dict = {
        "last_update": None,
        "next_update": None,
        "version": None,
        "up_to_date": False,
    }
    try:
        if _TRIVY_DB_METADATA.exists():
            meta = _json.loads(_TRIVY_DB_METADATA.read_text())
            last = meta.get("UpdatedAt") or meta.get("DownloadedAt")
            info["last_update"] = last
            info["version"] = meta.get("Version")
            if last:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                next_dt = last_dt + timedelta(hours=24)
                info["next_update"] = next_dt.isoformat()
                info["up_to_date"] = (
                    datetime.now(UTC) - last_dt
                ).total_seconds() < 86400
    except Exception as exc:
        info["error"] = str(exc)
    return info


async def update_trivy_db() -> dict:
    """Force an immediate Trivy DB update."""
    proc = await asyncio.create_subprocess_exec(
        _TRIVY_BINARY,
        "image",
        "--download-db-only",
        "--cache-dir",
        str(_TRIVY_CACHE_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "success": proc.returncode == 0,
        "output": stdout.decode() + stderr.decode(),
    }


def parse_trivy_output(raw: bytes, severities: list[str]) -> dict:
    """Parse Trivy JSON output and return a structured vuln_result dict."""
    try:
        data = json.loads(raw.decode())
    except json.JSONDecodeError, UnicodeDecodeError:
        return {
            "enabled": True,
            "blocked": False,
            "severities": severities,
            "counts": {},
            "vulnerabilities": [],
            "total": 0,
        }

    vulns: list[dict] = []
    counts: dict[str, int] = {}

    for result in data.get("Results", []):
        for v in result.get("Vulnerabilities") or []:
            sev = v.get("Severity", "UNKNOWN").upper()
            counts[sev] = counts.get(sev, 0) + 1
            vulns.append(
                {
                    "id": v.get("VulnerabilityID", ""),
                    "package": v.get("PkgName", ""),
                    "installed_version": v.get("InstalledVersion", ""),
                    "fixed_version": v.get("FixedVersion"),
                    "severity": sev,
                    "title": v.get("Title"),
                    "cvss_score": (v.get("CVSS") or {}).get("nvd", {}).get("V3Score"),
                    "target": result.get("Target", ""),
                }
            )

    blocked = any(counts.get(s, 0) > 0 for s in severities)

    return {
        "enabled": True,
        "blocked": blocked,
        "severities": severities,
        "counts": counts,
        "vulnerabilities": vulns,
        "total": len(vulns),
    }


def effective_vuln(settings: Settings, override: bool | None) -> bool:
    """Return the effective vulnerability-scan flag for a given job."""
    # A disabled Trivy server can never scan, whatever the per-job override asks.
    if not settings.trivy_enabled:
        return False
    if override is not None:
        return override
    return settings.vuln_scan_enabled


def effective_severities(settings: Settings, override: str | None) -> list[str]:
    """Return the effective CVE severity list for a given job."""
    if override is not None:
        return [s.strip().upper() for s in override.split(",") if s.strip()]
    return settings.vuln_severities


# ── Image scan ────────────────────────────────────────────────────────────────


def has_explicit_tag_or_digest(image: str) -> bool:
    """Return True when the image reference contains an explicit tag or digest."""
    return bool(re.search(r"(:[^/]+$|@sha256:[a-f0-9]{64}$)", image))


async def scan_image(
    image: str,
    severity: list[str] | None = None,
    ignore_unfixed: bool = False,
) -> dict:
    """
    Scan a local registry image with Trivy.
    Returns a structured result dict compatible with the ScanResult model.
    """

    stdout, stderr, returncode = await trivy_raw_scan(image, severity, ignore_unfixed)

    if returncode != 0:
        return {
            "success": False,
            "image": image,
            "scanned_at": datetime.now(UTC).isoformat(),
            "summary": {},
            "total": 0,
            "vulnerabilities": [],
            "error": stderr.decode(),
        }

    parsed = parse_trivy_output(stdout, severity)
    return {
        "success": True,
        "image": image,
        "scanned_at": datetime.now(UTC).isoformat(),
        "summary": parsed["counts"],
        "total": parsed["total"],
        "vulnerabilities": parsed["vulnerabilities"],
    }


async def trivy_raw_scan(
    image: str,
    severity: list[str] | None = None,
    ignore_unfixed: bool = False,
) -> tuple[bytes, bytes, int]:
    """Run a Trivy vulnerability scan and return (stdout, stderr, returncode).

    Scan mode selection:
      - Local OCI layout directory (produced by skopeo copy): uses the
        filesystem scanner via "--input <path>" which avoids the container
        runtime lookup entirely.  Trivy detects OCI layouts automatically
        when pointed at a directory containing an "index.json" file.
      - Regular docker:// image reference (used by the manual scan endpoint):
        uses the image scanner with "--server" for remote DB access.

    The previous approach of building "oci:<path>:latest" was incorrect —
    Trivy does not support that URI scheme and falls back to trying Docker /
    containerd / podman runtimes, which are not available in this container.
    """
    skopeo_env = {**os.environ, **settings.env_proxy}

    if severity is None:
        severity = ["HIGH", "CRITICAL"]

    sev_str = ",".join(s.upper() for s in severity)

    # Determine whether the target is a local OCI layout directory.
    # skopeo produces directories with an "index.json" at their root;
    # Trivy's filesystem scanner handles them natively via --input.
    path_candidate = Path(image)
    is_oci_dir = path_candidate.is_dir() and (path_candidate / "index.json").exists()

    # Also handle the legacy "oci:<path>:tag" format that may be passed by
    # internal callers — strip the scheme and tag so we get a plain path.
    if not is_oci_dir and image.startswith("oci:"):
        # Strip leading "oci:" prefix and trailing ":tag" if present
        stripped = image[4:]  # remove "oci:"
        if ":" in stripped:
            stripped = stripped.rsplit(":", 1)[0]
        path_candidate = Path(stripped)
        is_oci_dir = (
            path_candidate.is_dir() and (path_candidate / "index.json").exists()
        )
        if is_oci_dir:
            image = stripped  # use the plain path for --input

    if is_oci_dir:
        # OCI layout directory: use filesystem scanner with --input.
        # This mode does not require a running container runtime.
        cmd = [
            _TRIVY_BINARY,
            "image",
            "--server",
            TRIVY_SERVER_URL,
            "--cache-dir",
            str(_TRIVY_CACHE_DIR),
            "--format",
            "json",
            "--scanners",
            "vuln",
            "--severity",
            sev_str,
            "--input",
            str(path_candidate),
        ]
    else:
        # Regular image reference (docker:// or registry image name).
        cmd = [
            _TRIVY_BINARY,
            "image",
            "--server",
            TRIVY_SERVER_URL,
            "--cache-dir",
            str(_TRIVY_CACHE_DIR),
            "--format",
            "json",
            "--scanners",
            "vuln",
            "--severity",
            sev_str,
            image,
        ]

    if ignore_unfixed:
        # Insert --ignore-unfixed before the final positional argument
        cmd.insert(-1, "--ignore-unfixed")

    logger.debug("trivy command: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=skopeo_env,
    )
    stdout, stderr = await proc.communicate()

    logger.debug(
        "trivy scan returncode=%s stdout=%r stderr=%r",
        proc.returncode,
        stdout.decode()[:500],
        stderr.decode()[:500],
    )

    return stdout, stderr, proc.returncode


# ── Trivy DB background task ──────────────────────────────────────────────────


async def db_updater_loop() -> None:
    """Background task: download the Trivy vulnerability database at startup,
    then refresh it every 24 hours.

    Runs inside the uvicorn process so it inherits os.environ directly —
    including any proxy override applied by apply_proxy_to_os_environ().
    """
    while True:
        logger.info("Trivy DB updater: starting database download...")
        try:
            result = await update_trivy_db()
            if result["success"]:
                logger.info("Trivy DB updater: database updated successfully.")
            else:
                logger.warning(
                    "Trivy DB updater: download failed — %s",
                    result.get("output", "unknown error"),
                )
        except Exception as exc:
            logger.error("Trivy DB updater: unexpected error — %s", exc)

        logger.info(
            "Trivy DB updater: next refresh in %dh.",
            _TRIVY_DB_REFRESH_INTERVAL // 3600,
        )
        await asyncio.sleep(_TRIVY_DB_REFRESH_INTERVAL)
