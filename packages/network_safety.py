"""Network destination validation for server-side remote fetches."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

AddressResolver = Callable[[str, int], Awaitable[list[str]]]


def is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(address.is_global)


async def resolve_host(hostname: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return list(dict.fromkeys(record[4][0] for record in records))


async def validate_public_http_url(
    value: str,
    *,
    resolver: AddressResolver = resolve_host,
    allowed_ports: frozenset[int] = frozenset({80, 443}),
    allow_test_hosts: bool = False,
) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("remote URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("remote URL credentials are not allowed")
    if parsed.fragment:
        raise ValueError("remote URL fragments are not allowed")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        raise ValueError("local network destinations are not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in allowed_ports:
        raise ValueError("remote URL port is not allowed")
    if allow_test_hosts and hostname.endswith((".test", ".example")):
        return
    try:
        addresses = [hostname] if ipaddress.ip_address(hostname) else []
    except ValueError:
        addresses = await resolver(hostname, port)
    if not addresses or any(not is_public_address(address) for address in addresses):
        raise ValueError("remote URL resolves to a non-public network address")
