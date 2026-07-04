"""Helper functions."""

from .config import REGISTRY_HOST


def is_local_registry_host(host: str) -> bool:
    """Return True when *host* resolves to the embedded local registry.

    The host may arrive with a scheme (ad-hoc registries) or as a bare netloc
    (saved / system registries), so both forms are normalized before comparing
    against REGISTRY_HOST. Callers use this to detect reads/writes that actually
    land on the internal registry and enforce the local folder permissions
    rather than the dedicated external-pull / external-push permissions.
    """
    if not host:
        return False
    bare = host.rstrip("/")
    for scheme in ("http://", "https://"):
        if bare.startswith(scheme):
            bare = bare[len(scheme) :]
            break
    return bare == REGISTRY_HOST


def bytes_to_human(size_bytes: float) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
