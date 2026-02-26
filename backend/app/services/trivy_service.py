import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

TRIVY_BINARY = "/usr/local/bin/trivy"
TRIVY_CACHE_DIR = "/var/cache/trivy"
TRIVY_DB_METADATA = Path(TRIVY_CACHE_DIR) / "db" / "metadata.json"


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

        # Consider up to date if next update is in the future
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
    Scans a container image stored in the local registry using Trivy.
    Returns structured vulnerability results.

    Args:
        image_ref: Full image reference, e.g. localhost:5000/myimage:latest
        severity: Filter by severity levels, e.g. ["HIGH", "CRITICAL"]
        ignore_unfixed: Skip vulnerabilities without a known fix
    """
    if severity is None:
        severity = ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

    cmd = [
        TRIVY_BINARY,
        "image",
        "--cache-dir",
        TRIVY_CACHE_DIR,
        "--format",
        "json",
        "--severity",
        ",".join(severity),
        "--insecure",  # Allow self-signed certs on local registry
    ]

    if ignore_unfixed:
        cmd.append("--ignore-unfixed")

    cmd.append(image_ref)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode not in (0, 1):  # 0 = clean, 1 = vulnerabilities found
        return {
            "success": False,
            "error": stderr.decode(),
            "image": image_ref,
        }

    try:
        raw = json.loads(stdout.decode())
        return _parse_trivy_result(image_ref, raw)
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Failed to parse Trivy output",
            "image": image_ref,
        }


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

    # Sort by CVSS score descending
    vulnerabilities.sort(key=lambda v: v.get("cvss_score") or 0, reverse=True)

    return {
        "success": True,
        "image": image_ref,
        "scanned_at": datetime.utcnow().isoformat(),
        "summary": summary,
        "total": sum(summary.values()),
        "vulnerabilities": vulnerabilities,
    }


def _extract_cvss(vuln: dict) -> Optional[float]:
    """Extracts the highest CVSS v3 score available from a vulnerability entry."""
    cvss_map = vuln.get("CVSS", {})
    scores = []
    for source, data in cvss_map.items():
        v3 = data.get("V3Score")
        if v3 is not None:
            scores.append(float(v3))
    return max(scores) if scores else None


async def update_trivy_db() -> dict:
    """
    Forces an immediate Trivy database update.
    Called on-demand from the API.
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
