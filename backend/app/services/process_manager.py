import xmlrpc.client

SUPERVISOR_RPC_URL = "http://127.0.0.1:9001/RPC2"
MONITORED_PROCESSES = ["registry", "trivy-db-updater", "portalcrane"]


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
