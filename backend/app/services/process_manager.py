import asyncio
import xmlrpc.client

SUPERVISOR_RPC_URL = "http://127.0.0.1:9001/RPC2"
# Only registry needs lifecycle management for GC
MONITORED_PROCESSES = ["registry", "trivy-db-updater", "portalcrane"]
REGISTRY_GC_LOCK = asyncio.Lock()


def _get_proxy() -> xmlrpc.client.ServerProxy:
    """Returns a synchronous XML-RPC proxy to supervisord."""
    return xmlrpc.client.ServerProxy(SUPERVISOR_RPC_URL)


async def get_all_process_statuses() -> list[dict]:
    """Returns the status of all monitored supervised processes."""
    proxy = _get_proxy()
    statuses = []
    for name in MONITORED_PROCESSES:
        try:
            info = proxy.supervisor.getProcessInfo(name)
            statuses.append(
                {
                    "name": info["name"],
                    "running": info["statename"] == "RUNNING",
                    "state": info["statename"],
                    "pid": info.get("pid"),
                    "uptime_seconds": info.get("now", 0) - info.get("start", 0)
                    if info.get("start")
                    else 0,
                }
            )
        except Exception as e:
            statuses.append({"name": name, "running": False, "error": str(e)})
    return statuses


async def run_registry_garbage_collect(dry_run: bool = False) -> dict:
    """
    Orchestrates registry garbage collection:
    1. Stops the registry process via supervisord
    2. Runs the GC binary
    3. Restarts the registry
    The entire operation is protected by a lock to prevent concurrent runs.
    """
    async with REGISTRY_GC_LOCK:
        proxy = _get_proxy()
        result = {"success": False, "output": "", "dry_run": dry_run}

        try:
            # Step 1 — stop registry gracefully
            proxy.supervisor.stopProcess("registry")
            await asyncio.sleep(2)

            # Step 2 — run garbage collection
            cmd = [
                "/usr/local/bin/registry",
                "garbage-collect",
                "/etc/registry/config.yml",
            ]
            if dry_run:
                cmd.append("--dry-run")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            result["output"] = stdout.decode() + stderr.decode()
            result["return_code"] = proc.returncode
            result["success"] = proc.returncode == 0

        finally:
            # Step 3 — always restart the registry
            proxy.supervisor.startProcess("registry")

        return result
