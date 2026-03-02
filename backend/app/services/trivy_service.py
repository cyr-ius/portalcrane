"""
Portalcrane - Trivy Service
Calls the Trivy server HTTP API instead of spawning a subprocess.
The Trivy server runs on localhost:4954 (managed by supervisord).
"""

import asyncio
import json
import logging
from datetime import datetime
from urllib.parse import urlparse

from ..config import (
    REGISTRY_URL,
    TRIVY_BINARY,
    TRIVY_CACHE_DIR,
    TRIVY_DB_METADATA,
    TRIVY_SERVER_URL,
)

logger = logging.getLogger(__name__)


def _normalize_image_ref(image_ref: str) -> str:
    """
    Ensure the image reference always points unambiguously to the local registry.

    Problem: Trivy parses the first path segment of a bare reference as a
    registry hostname when it contains a dot or a colon+port.  For names that
    do NOT match this heuristic (e.g. "production/traefik/whoami") Trivy
    silently treats "production" as an unknown host, connects nowhere, and
    returns 0 results — while "traefik/whoami" happens to resolve against
    Docker Hub and returns CVEs.

    Fix: if the reference already starts with a known host (contains ":" or ".")
    in its first segment, leave it unchanged.  Otherwise, prepend the local
    registry host extracted from REGISTRY_URL so Trivy always talks to the
    internal registry.

    Examples
    --------
    "traefik/whoami"                 → "localhost:5000/traefik/whoami"
    "traefik/whoami:latest"          → "localhost:5000/traefik/whoami:latest"
    "production/traefik/whoami"      → "localhost:5000/production/traefik/whoami"
    "production/traefik/whoami:1.0"  → "localhost:5000/production/traefik/whoami:1.0"
    "localhost:5000/traefik/whoami"  → "localhost:5000/traefik/whoami"  (unchanged)
    "myregistry.example.com/img:v1"  → "myregistry.example.com/img:v1"   (unchanged)
    """
    # Extract the first path segment (before the first "/")
    first_segment = image_ref.split("/")[0]

    # A segment that contains ":" (port) or "." (domain) is already a hostname
    has_host = ":" in first_segment or "." in first_segment

    if has_host:
        return image_ref

    # Derive local registry host from REGISTRY_URL (e.g. "http://localhost:5000")
    registry_host = urlparse(REGISTRY_URL).netloc or "localhost:5000"
    return f"{registry_host}/{image_ref}"


async def get_trivy_db_info() -> dict:
    """
    Returns Trivy vulnerability database metadata.
    Reads the metadata.json file written by Trivy after each DB update.
    """
    info = {
        "last_update": None,
        "next_update": None,
        "version": None,
        "up_to_date": False,
    }

    if not TRIVY_DB_METADATA.exists():
        info["error"] = "Trivy DB not yet downloaded"
        return info

    try:
        data = json.loads(TRIVY_DB_METADATA.read_text())
        info["last_update"] = data.get("UpdatedAt")
        info["next_update"] = data.get("NextUpdate")
        info["version"] = data.get("Version")

        if info["next_update"]:
            next_dt = datetime.fromisoformat(info["next_update"].replace("Z", "+00:00"))
            info["up_to_date"] = datetime.now(next_dt.tzinfo) < next_dt
    except Exception as e:
        info["error"] = str(e)

    return info


async def scan_image(
    image_ref: str,
    severity: list[str] | None = None,
    ignore_unfixed: bool = False,
) -> dict:
    """
    Scans a container image using Trivy CLI in client mode.
    Connects to the local Trivy server for cached DB access.

    Args:
        image_ref: Image reference — may be bare ("repo/img:tag") or fully
                   qualified ("localhost:5000/repo/img:tag").  Bare references
                   are automatically prefixed with the local registry host so
                   that images stored under namespace prefixes (e.g.
                   "production/traefik/whoami") are always scanned from the
                   internal registry rather than being misrouted to Docker Hub.
        severity: Filter by severity levels, e.g. ["HIGH", "CRITICAL"]
        ignore_unfixed: Skip vulnerabilities without a known fix
    """
    if severity is None:
        severity = ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

    # Always resolve to a fully-qualified reference before calling Trivy
    resolved_ref = _normalize_image_ref(image_ref)

    if resolved_ref != image_ref:
        logger.debug("scan_image: normalized '%s' → '%s'", image_ref, resolved_ref)

    cmd = [
        TRIVY_BINARY,
        "image",
        "--quiet",
        "--format",
        "json",
        "--server",
        TRIVY_SERVER_URL,  # use the local trivy-server for DB
        "--severity",
        ",".join(severity),
        "--insecure",  # allow HTTP on local registry
    ]

    if ignore_unfixed:
        cmd.append("--ignore-unfixed")

    cmd.append(resolved_ref)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "error": "Trivy binary not found",
            "image": image_ref,
        }

    stdout, stderr = await proc.communicate()

    # 0 = no vulns, 1 = vulns found — both are valid
    if proc.returncode not in (0, 1):
        return {
            "success": False,
            "error": f"Trivy scan failed: {stderr.decode() or stdout.decode()}",
            "image": image_ref,
        }

    try:
        raw = json.loads(stdout.decode() or "{}")
    except json.JSONDecodeError as exc:
        return {
            "success": False,
            "error": f"Unable to parse Trivy output: {exc}",
            "image": image_ref,
        }

    return _parse_trivy_result(image_ref, raw)


async def scan_tarball(
    tarball_path: str,
    severity: list[str] | None = None,
    ignore_unfixed: bool = False,
) -> dict:
    """
    Scans a local tarball using the Trivy CLI in client mode.
    Uses --server to leverage the trivy-server's shared DB cache.
    Falls back to --skip-db-update with local cache if server is unreachable.
    """
    if severity is None:
        severity = ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

    cmd = [
        TRIVY_BINARY,
        "image",
        "--quiet",
        "--format",
        "json",
        "--server",
        TRIVY_SERVER_URL,  # use trivy-server for shared DB
        "--severity",
        ",".join(severity),
        "--skip-java-db-update",
    ]

    if ignore_unfixed:
        cmd.append("--ignore-unfixed")

    cmd += ["--input", tarball_path]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Trivy binary not found.") from exc

    stdout, stderr = await proc.communicate()

    if proc.returncode not in (0, 1):  # 0 = clean, 1 = vulnerabilities found
        raise RuntimeError(f"Trivy scan failed: {stderr.decode() or stdout.decode()}")

    try:
        raw = json.loads(stdout.decode() or "{}")
        logger.debug(
            "Trivy scan completed for %s with %d results",
            tarball_path,
            len(raw.get("Results", [])),
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unable to parse Trivy output: {exc}") from exc

    return _parse_trivy_result(tarball_path, raw)


def _parse_trivy_result(image_ref: str, raw: dict) -> dict:
    """
    Parses raw Trivy JSON output into a clean structured result.
    Groups vulnerabilities by severity for easier frontend consumption.
    """
    results = raw.get("Results", [])
    summary = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    vulnerabilities = []

    for target in results:
        target_name = target.get("Target", "")
        target_type = target.get("Type", "")
        for vuln in target.get("Vulnerabilities") or []:
            sev = vuln.get("Severity", "UNKNOWN")
            summary[sev] = summary.get(sev, 0) + 1
            vulnerabilities.append(
                {
                    "id": vuln.get("VulnerabilityID"),
                    "package": vuln.get("PkgName"),
                    "installed_version": vuln.get("InstalledVersion"),
                    "fixed_version": vuln.get("FixedVersion"),
                    "severity": sev,
                    "title": vuln.get("Title"),
                    "description": vuln.get("Description"),
                    "cvss_score": _extract_cvss(vuln),
                    "target": target_name,
                    "type": target_type,
                }
            )

    vulnerabilities.sort(key=lambda v: v.get("cvss_score") or 0, reverse=True)

    return {
        "success": True,
        "image": image_ref,
        "scanned_at": datetime.utcnow().isoformat(),
        "summary": summary,
        "total": sum(summary.values()),
        "vulnerabilities": vulnerabilities,
    }


def _extract_cvss(vuln: dict) -> float | None:
    """Extracts the highest CVSS v3 score available from a vulnerability entry."""
    cvss_map = vuln.get("CVSS", {})
    scores = [
        float(data["V3Score"])
        for data in cvss_map.values()
        if data.get("V3Score") is not None
    ]
    return max(scores) if scores else None


async def update_trivy_db() -> dict:
    """
    Forces an immediate Trivy database update via the CLI.
    The trivy-server process will pick up the refreshed DB automatically
    since it reads from the shared cache directory.
    """
    proc = await asyncio.create_subprocess_exec(
        TRIVY_BINARY,
        "image",
        "--download-db-only",
        "--cache-dir",
        TRIVY_CACHE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "success": proc.returncode == 0,
        "output": stdout.decode() + stderr.decode(),
    }
