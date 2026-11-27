"""The PC's own Tailscale, as the pairing connector uses it.

Two things, both from ``tailscale status --json`` on this PC:

- **Is this address a machine on my tailnet?** A server reached over the
  tailnet may be given as plain ``http://``: Tailscale already encrypts that
  path, end to end. So a plain http address is accepted when, and only when,
  it leads to an address Tailscale on this PC lists for a machine in its
  tailnet (``confirms``). A name is resolved first: every address it resolves
  to has to be one of those.
- **Find my server.** Ask each machine on the tailnet "are you Cognitrix?"
  (``find_servers``). The question is one small GET; the answer is the app's
  name and version.

Nothing here needs Tailscale turned on in Settings: that setting is about this
machine being a server. Here the PC is only a client of someone's tailnet.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from typing import Any

HELLO_PATH = '/api/v1/computer/devices/hello'
# Where a server answers on the tailnet: its Tailscale private address (https),
# then its own port over plain http.
SERVER_PORT = 8000
MAX_MACHINES = 40
ASK_TIMEOUT_S = 3.0
_CACHE_S = 20.0

_cached: tuple[float, list[dict[str, Any]]] | None = None


def _status() -> dict[str, Any] | None:
    from cognitrix.tailscale import cli

    return cli.status()


def machines(fresh: bool = False) -> list[dict[str, Any]]:
    """The machines on this PC's tailnet: [{name, dns, ips, online, me}].
    Empty when Tailscale isn't installed, running or signed in here."""
    global _cached
    now = time.time()
    if _cached is not None and not fresh and now - _cached[0] < _CACHE_S:
        return _cached[1]
    out: list[dict[str, Any]] = []
    status = _status()
    if isinstance(status, dict) and status.get('BackendState') == 'Running':
        peers = status.get('Peer')
        rows = [(status.get('Self'), True)] + [(p, False) for p in (peers.values() if isinstance(peers, dict) else [])]
        for row, me in rows:
            if not isinstance(row, dict):
                continue
            ips = [str(ip) for ip in (row.get('TailscaleIPs') or []) if isinstance(ip, str)]
            dns = str(row.get('DNSName') or '').rstrip('.').lower()
            if not ips:
                continue
            out.append({'name': str(row.get('HostName') or dns.split('.')[0] or ips[0]), 'dns': dns, 'ips': ips,
                        'online': bool(me or row.get('Online')), 'me': me})
    _cached = (now, out)
    return out


def _tailnet_ips() -> set[str]:
    out: set[str] = set()
    for machine in machines():
        for ip in machine['ips']:
            try:
                out.add(str(ipaddress.ip_address(ip)))
            except ValueError:
                continue
    return out


def confirms(host: str) -> bool:
    """True when ``host`` leads only to machines Tailscale on this PC lists in its tailnet."""
    host = str(host or '').strip().strip('[]').lower()
    if not host:
        return False
    known = _tailnet_ips()
    if not known:
        return False
    try:
        return str(ipaddress.ip_address(host)) in known
    except ValueError:
        pass
    try:
        found = {str(ipaddress.ip_address(info[4][0])) for info in socket.getaddrinfo(host, None)}
    except (OSError, ValueError):
        return False
    return bool(found) and found <= known


async def _ask(http, url: str) -> dict[str, Any] | None:
    try:
        async with http.get(url + HELLO_PATH, allow_redirects=False) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except Exception:  # noqa: BLE001 - most machines on a tailnet are not Cognitrix
        return None
    if not isinstance(data, dict) or data.get('app') != 'cognitrix':
        return None
    return {'server': url, 'version': str(data.get('version') or '')[:40], 'ask_to_pair': bool(data.get('ask_to_pair'))}


async def find_servers() -> list[dict[str, Any]]:
    """Cognitrix servers on this PC's tailnet: [{name, server, version, ask_to_pair}],
    the ones that can pair without a code first. Raises LookupError with words
    for the person when Tailscale here can't be asked."""
    import aiohttp

    known = await asyncio.to_thread(machines, True)
    if not known:
        raise LookupError("Tailscale isn't running on this PC, or isn't signed in. Open Tailscale and sign in, "
                          'or type the address.')
    targets = [m for m in known if m['online'] and not m['me'] and m['dns']][:MAX_MACHINES]

    async def one(http, machine: dict[str, Any]) -> dict[str, Any] | None:
        # The Tailscale private address first: only it can pair without a code.
        for url in (f"https://{machine['dns']}", f"http://{machine['dns']}:{SERVER_PORT}"):
            found = await _ask(http, url)
            if found:
                return {'name': machine['name'], **found}
        return None

    timeout = aiohttp.ClientTimeout(total=ASK_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        results = await asyncio.gather(*(one(http, m) for m in targets))
    found = [r for r in results if r]
    found.sort(key=lambda r: (not r['ask_to_pair'], r['name'].lower()))
    return found
