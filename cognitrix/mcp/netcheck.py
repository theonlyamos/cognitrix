"""Keep connected-app sign-in requests off private networks.

Sign-in metadata comes from the remote server (where to register, where to
swap codes for tokens), so a hostile server could point this engine at an
address inside its own network. On a shared server every address the sign-in
touches must be https and public. Admins and the desktop app may use private
addresses, the same rule as adding an MCP server.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    pass


async def _resolve_host(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(info[4][0].split('%')[0]) for info in infos]


async def check_url(url: str, *, allow_private: bool) -> str:
    parsed = urlparse(str(url or ''))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise UnsafeURL('not a web address')
    if allow_private:
        return url
    if parsed.scheme != 'https':
        raise UnsafeURL('not https')
    try:
        addresses = await _resolve_host(parsed.hostname, parsed.port or 443)
    except (OSError, UnicodeError):
        raise UnsafeURL('unknown host')
    if not addresses or any(not a.is_global for a in addresses):
        raise UnsafeURL('private address')
    return url
