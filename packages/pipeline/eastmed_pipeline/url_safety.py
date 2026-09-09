import ipaddress
import socket
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit


class UnsafeFetchURLError(ValueError):
    pass


Resolver = Callable[..., list[tuple[Any, ...]]]


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeFetchURLError("Source URL contains an invalid port") from exc
    default_port = (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
    clean = SplitResult(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
        path=parsed.path or "/",
        query=parsed.query,
        fragment="",
    )
    return urlunsplit(clean)


def canonicalize_reference_url(url: str) -> str:
    """Canonicalize an outbound display link without authorizing it for fetching."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeFetchURLError("Reference URL must use HTTP(S)")
    if parsed.username or parsed.password or not parsed.hostname:
        raise UnsafeFetchURLError("Reference URL contains credentials or has no hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeFetchURLError("Reference URL contains an invalid port") from exc
    if port not in {None, 80, 443}:
        raise UnsafeFetchURLError("Reference URL uses a non-standard port")
    return canonicalize_url(url)


def validate_fetch_url(
    url: str,
    *,
    allowed_hosts: Iterable[str],
    resolver: Resolver = socket.getaddrinfo,
) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeFetchURLError("Only HTTP(S) source URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeFetchURLError("Credentials in source URLs are forbidden")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeFetchURLError("Source URL has no hostname")
    allowlist = {item.lower().rstrip(".") for item in allowed_hosts}
    if host not in allowlist:
        raise UnsafeFetchURLError(f"Host {host!r} is not in the fetch allowlist")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeFetchURLError("Source URL contains an invalid port") from exc
    if port not in {None, 80, 443}:
        raise UnsafeFetchURLError("Non-standard source URL ports are forbidden")

    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in resolver(host, None)]
        except (OSError, ValueError) as exc:
            raise UnsafeFetchURLError(f"Could not safely resolve host {host!r}") from exc
    if not addresses:
        raise UnsafeFetchURLError(f"Host {host!r} resolved to no addresses")
    for address in addresses:
        if not address.is_global:
            raise UnsafeFetchURLError(f"Host {host!r} resolves to a non-public address")
    return canonicalize_url(url)
