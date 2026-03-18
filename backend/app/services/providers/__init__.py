from typing import Any

from .external_github import GithubProvider
from .external_dockerhub import DockerHubProvider
from .external_v2 import V2Provider
from .base import BaseRegistryProvider

_DOCKERHUB_HOSTS = {"docker.io", "registry-1.docker.io", "index.docker.io"}
_GHCR_HOSTS = {"ghcr.io"}


def _normalize_host(host: str) -> str:
    """Strip scheme and path, return bare hostname in lowercase."""
    return host.lower().removeprefix("https://").removeprefix("http://").split("/")[0]


def resolve_provider(
    host: str,
    username: str = "",
    password: str = "",
    use_tls: bool = True,
    tls_verify: bool = True,
) -> BaseRegistryProvider:
    """Factory: instantiate and return the correct provider for the given host.

    Routing:
        ghcr.io              -> GithubProvider
        docker.io variants   -> DockerHubProvider
        everything else      -> V2Provider

    Args:
        host:       Registry hostname (bare or with scheme).
        username:   Registry username or GitHub owner.
        password:   Registry password or access token.
        use_tls:    Use HTTPS when True (default).
        tls_verify: Validate TLS certificate when True (default).

    Returns:
        BaseRegistryProvider: Configured provider instance ready for use.
    """
    normalized = _normalize_host(host)
    kwargs: dict[str, Any] = dict(
        host=host,
        username=username,
        password=password,
        use_tls=use_tls,
        tls_verify=tls_verify,
    )

    if normalized in _GHCR_HOSTS:
        return GithubProvider(**kwargs)
    if normalized in _DOCKERHUB_HOSTS:
        return DockerHubProvider(**kwargs)
    return V2Provider(**kwargs)


def resolve_provider_from_registry(registry: dict) -> BaseRegistryProvider:
    """Convenience wrapper: resolve provider directly from a registry dict.

    Args:
        registry: Registry dict as stored in external_registries.json,
                  containing at minimum a 'host' key.

    Returns:
        BaseRegistryProvider: Configured provider instance.
    """
    return resolve_provider(
        host=registry["host"],
        username=registry.get("username", ""),
        password=registry.get("password", ""),
        use_tls=registry.get("use_tls", True),
        tls_verify=registry.get("tls_verify", True),
    )


def build_target_path(
    folder: str | None, image_name: str, tag: str, registry_host: str | None
) -> str:
    """Build the full skopeo destination reference."""
    path = f"{folder}/{image_name}" if folder else image_name
    if not registry_host:
        return f"docker://{path}:{tag}"
    return f"docker://{registry_host}/{path}:{tag}"


__all__ = [
    "GithubProvider",
    "DockerHubProvider",
    "V2Provider",
    "BaseRegistryProvider",
    "resolve_provider",
    "resolve_provider_from_registry",
    "build_target_path",
]
