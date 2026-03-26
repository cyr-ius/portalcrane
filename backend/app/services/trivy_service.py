"""
Portalcrane - Trivy Service
Vulnerability scanning helpers + override persistence.

Override priority (highest → lowest):
  1. Persisted admin override  (DATA_DIR/vuln_override.json)
  2. Environment variables     (Settings.vuln_*)
"""

import os
import logging
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from ..config import DATA_DIR, Settings, TRIVY_SERVER_URL, get_settings

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
    override = load_vuln_override()
    if override:
        return {
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
        "vuln_scan_override": False,
        "vuln_scan_enabled": settings.vuln_scan_enabled,
        "vuln_scan_severities": settings.vuln_scan_severities,
        "vuln_ignore_unfixed": settings.vuln_ignore_unfixed,
        "vuln_scan_timeout": settings.vuln_scan_timeout,
    }


# ── Trivy DB helpers ──────────────────────────────────────────────────────────


async def get_trivy_db_info() -> dict:
    """Return Trivy vulnerability database metadata and freshness status."""
    import json as _json
    from datetime import datetime, timedelta, timezone

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
                    datetime.now(timezone.utc) - last_dt
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
        _TRIVY_CACHE_DIR,
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
    except (json.JSONDecodeError, UnicodeDecodeError):
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
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "summary": {},
            "total": 0,
            "vulnerabilities": [],
            "error": stderr.decode(),
        }

    parsed = parse_trivy_output(stdout, severity)
    return {
        "success": True,
        "image": image,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "summary": parsed["counts"],
        "total": parsed["total"],
        "vulnerabilities": parsed["vulnerabilities"],
    }


async def trivy_raw_scan(
    image: str,
    severity: list[str] | None = None,
    ignore_unfixed: bool = False,
) -> tuple[bytes, bytes, int]:
    """Trivy scan.

    Return stdout , stderr , returncode
    """
    skopeo_env = {**os.environ, **settings.env_proxy}

    if severity is None:
        severity = ["HIGH", "CRITICAL"]

    sev_str = ",".join(s.upper() for s in severity)

    image_ref = image
    # Staging / transfer pipelines scan an OCI layout directory produced by
    # skopeo ("oci:<path>:latest"). If only the raw path is provided, Trivy
    # interprets it as an image name and fails to resolve it.
    if "://" not in image and not image.startswith("oci:"):
        path_candidate = Path(image)
        if path_candidate.exists() and path_candidate.is_dir():
            image_ref = f"oci:{path_candidate}:latest"

    cmd = [
        _TRIVY_BINARY,
        "image",
        "--server",
        TRIVY_SERVER_URL,
        "--cache-dir",
        _TRIVY_CACHE_DIR,
        "--format",
        "json",
        "--severity",
        sev_str,
    ]
    if ignore_unfixed:
        cmd.append("--ignore-unfixed")
    cmd.append(image_ref)

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
