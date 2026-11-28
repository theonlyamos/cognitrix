"""Each person's Fly computer as a machine on the tailnet.

Without this, the engine reaches Fly computers over Fly's private network, so
the engine has to run on Fly too. With it, a computer joins the owner's
tailnet when it starts, and an engine anywhere on that tailnet (a home
server, the desktop app) can use it. Agents working on that computer can in
turn reach the home machines the tailnet policy opens to the computers' tag.

How a computer joins (see ``cognitrix/computer/sandbox/fly.py`` and
``sandbox/desktop/bin/cognitrix-sandbox-root``):

1. The engine asks Tailscale for a join key that works once, lasts ten
   minutes, is already approved and carries the computers' tag (``api.py``).
2. The key goes into the new machine's settings. The computer's root start
   script starts Tailscale with it, then drops the key from the environment
   before anything of the agents' runs.
3. Tailscale's identity stays in a root-only folder on the computer's volume,
   so a stop and a wake keep the same name and address with no new key.

Who gets it is a setting (``COGNITRIX_TAILSCALE_SANDBOX``): nobody, admins'
computers or everyone's. The manager runs in worker threads and can't ask
the database who is an admin, so the web process writes the admins' keys to
``members.json`` in the Tailscale folder (``refresh_members``).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
import time
from typing import Any

from cognitrix import tailscale
from cognitrix.tailscale import api, cli

logger = logging.getLogger('cognitrix.tailscale')

NAME_PREFIX = 'cognitrix-sandbox-'
ENV_ON = 'COGNITRIX_TAILNET'
ENV_NAME = 'COGNITRIX_TAILNET_NAME'
ENV_KEY = 'COGNITRIX_TAILNET_AUTHKEY'
# Ports the engine dials on a computer (its tools, its live screen).
PORTS = (8790, 6080)
_STATUS_FRESH_S = 5.0
# How long a started machine gets to show up on the tailnet.
JOIN_WAIT_S = 75.0

_lock = threading.Lock()
_status_cache: tuple[float, dict[str, Any] | None] | None = None


class ComputersError(RuntimeError):
    """Words for the person."""


# ---------------------------------------------------------------------------
# Whose computers join
# ---------------------------------------------------------------------------

def _members_file():
    return tailscale.folder() / 'members.json'


def _members() -> set[str]:
    try:
        data = json.loads(_members_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    return {str(k) for k in data.get('admins', [])} if isinstance(data, dict) else set()


async def refresh_members() -> None:
    """Write down whose computers join while the choice is "admins"."""
    if tailscale.sandbox_scope() != tailscale.ADMINS:
        return
    from cognitrix.agents.access import is_admin, owner_key
    from cognitrix.computer.sandbox.client import user_key
    from cognitrix.models.user import User

    try:
        users = list(await User.all() or [])
    except Exception:  # noqa: BLE001 - keep what was written before
        logger.debug("Couldn't list accounts for the tailnet computers", exc_info=True)
        return
    admins = sorted({user_key(owner_key(u)) for u in users if await is_admin(u)} | {user_key(None)})
    try:
        path = _members_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({'admins': admins})
        if not path.exists() or path.read_text(encoding='utf-8') != text:
            path.write_text(text, encoding='utf-8')
    except OSError:
        logger.debug("Couldn't save whose computers join the tailnet", exc_info=True)


def wanted_for(key: str) -> bool:
    """Whether the computer of the person with this key (``user_key``) joins."""
    scope = tailscale.sandbox_scope()
    if scope == tailscale.EVERYONE:
        return True
    return scope == tailscale.ADMINS and key in _members()


def on_fly() -> bool:
    """The engine itself runs on Fly, so it has Fly's private network."""
    return bool(os.environ.get('FLY_PRIVATE_IP', '').strip())


def dial_over_tailnet() -> bool:
    """The engine reaches Fly computers by their tailnet address: it isn't on
    Fly itself and computers join the tailnet."""
    return tailscale.sandbox_scope() != tailscale.NOBODY and not on_fly()


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------

def node_name(key: str) -> str:
    return f'{NAME_PREFIX}{key}'[:63]


def join_env(key: str) -> dict[str, str]:
    """What a new (or not yet joined) machine needs in its settings to join."""
    try:
        auth_key = api.join_key(tailscale.sandbox_tag(), f'Cognitrix computer {key}')
    except api.ApiError as exc:
        raise ComputersError(f"The agents' computer couldn't join your tailnet: {exc}") from None
    return {ENV_ON: '1', ENV_NAME: node_name(key), ENV_KEY: auth_key}


def _status(fresh: bool = False) -> dict[str, Any] | None:
    global _status_cache
    now = time.monotonic()
    with _lock:
        if not fresh and _status_cache is not None and now - _status_cache[0] < _STATUS_FRESH_S:
            return _status_cache[1]
    status = cli.status()
    with _lock:
        _status_cache = (now, status)
    return status


def _running(status: dict[str, Any] | None) -> bool:
    return isinstance(status, dict) and status.get('BackendState') == 'Running'


def _ipv4(row: dict[str, Any]) -> str:
    for ip in row.get('TailscaleIPs') or []:
        try:
            if ipaddress.ip_address(str(ip)).version == 4:
                return str(ip)
        except ValueError:
            continue
    return ''


def engine_address() -> str:
    """This engine's own tailnet address (IPv4), '' when it isn't on a tailnet."""
    status = _status()
    if not _running(status):
        return ''
    me = status.get('Self')
    return _ipv4(me) if isinstance(me, dict) else ''


def find(key: str, fresh: bool = False) -> dict[str, Any] | None:
    """The computer as this engine's Tailscale sees it: {name, dns, ip, online}.
    It has to carry the computers' tag: a machine that merely took the name
    is never dialled (the engine would hand it the computer's token)."""
    status = _status(fresh)
    if not _running(status):
        return None
    peers = status.get('Peer')
    want, tag = node_name(key), tailscale.sandbox_tag()
    found = []
    for row in (peers.values() if isinstance(peers, dict) else []):
        if not isinstance(row, dict) or tag not in [str(t) for t in (row.get('Tags') or [])]:
            continue
        if str(row.get('HostName') or '').lower() != want:
            continue
        ip = _ipv4(row)
        if ip:
            found.append({'name': want, 'dns': str(row.get('DNSName') or '').rstrip('.').lower(), 'ip': ip,
                          'online': bool(row.get('Online')), 'created': str(row.get('Created') or '')})
    if not found:
        return None
    # A reset leaves the old machine's entry behind until it is removed: the live one wins.
    found.sort(key=lambda r: (r['online'], r['created']))
    return found[-1]


def wait_for(key: str, seconds: float = 60.0) -> dict[str, Any] | None:
    """The computer once it shows up online on the tailnet, or None."""
    deadline = time.monotonic() + seconds
    while True:
        peer = find(key, fresh=True)
        if peer is not None and peer['online']:
            return peer
        if time.monotonic() >= deadline:
            return peer if peer is not None and peer['online'] else None
        time.sleep(2)


def reach(key: str, wait: float = 0.0) -> tuple[str, int, int]:
    """(host, tools port, screen port) this engine dials for the computer."""
    from cognitrix.tailscale import outbound

    if not engine_address():
        raise ComputersError("This server isn't connected to Tailscale, which is how it reaches the agents' "
                             'computers on Fly. Check Settings, Tailscale.')
    peer = find(key)
    if (peer is None or not peer['online']) and wait > 0:
        peer = wait_for(key, wait)
    if peer is None or not peer['online']:
        raise ComputersError("The agents' computer started but this server can't see it on your tailnet yet. "
                             "Check that your tailnet policy lets this server reach the computers' tag "
                             '(Settings, Tailscale shows what to add), then try again.')
    host, tools = outbound.reach(peer['ip'], PORTS[0])
    _, screen = outbound.reach(peer['ip'], PORTS[1])
    return host, tools, screen


def proxy_addresses() -> tuple[str, str]:
    """(where the hidden-keys proxy listens, the address computers reach it at)."""
    from cognitrix.tailscale import outbound

    address = engine_address()
    if not address:
        raise ComputersError("Hidden keys need this server on your tailnet, so the agents' computers can reach "
                             'it. Check Settings, Tailscale.')
    # The built-in Tailscale hands tailnet connections to this machine's own
    # loopback; the machine's own Tailscale has the address as a real one.
    return ('127.0.0.1' if outbound.proxy_port() else address), address


def forget(key: str) -> bool:
    """Take a reset computer off the tailnet. True when something was removed."""
    if not api.configured():
        return False
    want, tag = node_name(key), tailscale.sandbox_tag()
    removed = False
    try:
        for row in api.devices():
            if str(row.get('hostname') or '').lower() == want and tag in [str(t) for t in (row.get('tags') or [])]:
                api.remove_device(str(row.get('nodeId') or row.get('id') or ''))
                removed = True
    except api.ApiError as exc:
        logger.info("Couldn't remove %s from the tailnet: %s", want, exc)
    return removed


# ---------------------------------------------------------------------------
# Settings > Tailscale
# ---------------------------------------------------------------------------

def policy_snippet() -> str:
    """What to add to the tailnet policy file so this server and the
    computers can talk to each other, and nothing else."""
    status = _status()
    me = status.get('Self') if _running(status) else None
    tags = [str(t) for t in (me.get('Tags') or [])] if isinstance(me, dict) else []
    server = tags[0] if tags else (_ipv4(me) if isinstance(me, dict) else '') or '<this server>'
    tag = tailscale.sandbox_tag()
    return '\n'.join([
        '// 1. Under "tagOwners": who may give a machine the computers\' tag.',
        f'"{tag}": ["autogroup:admin"],',
        '',
        '// 2. Under "grants": this server reaches the computers (their tools and live screen),',
        '//    and the computers reach this server (its hidden-keys proxy picks its port at start).',
        f'{{"src": ["{server}"], "dst": ["{tag}"], "ip": ["tcp:{PORTS[0]}", "tcp:{PORTS[1]}"]}},',
        f'{{"src": ["{tag}"], "dst": ["{server}"], "ip": ["tcp:*"]}},',
        '',
        '// 3. Optional, one line per machine the agents\' computers may reach, for example a NAS:',
        f'// {{"src": ["{tag}"], "dst": ["my-nas"], "ip": ["tcp:445"]}},',
    ])


def view() -> dict[str, Any]:
    """What Settings shows about Fly computers on the tailnet (never the secret)."""
    return {
        'scope': tailscale.sandbox_choice(),
        'tag': tailscale.sandbox_tag(),
        'oauth_id': api.client_id(),
        'oauth_saved': api.configured(),
        'on_fly': on_fly(),
        'policy': policy_snippet(),
    }


def check() -> dict[str, Any]:
    """Try the saved OAuth client: can it make a join key with the tag?"""
    steps: list[dict[str, Any]] = []

    def step(name: str, ok: bool, detail: str = '') -> None:
        steps.append({'name': name, 'ok': ok, 'detail': detail})

    if not api.configured():
        step('oauth', False, 'Save the OAuth client ID and secret first.')
        return {'ok': False, 'steps': steps}
    api.forget_token()
    try:
        api.access_token()
        step('oauth', True)
    except api.ApiError as exc:
        step('oauth', False, str(exc))
        return {'ok': False, 'steps': steps}
    try:
        api.join_key(tailscale.sandbox_tag(), 'Cognitrix check (unused)')
        step('join_key', True)
    except api.ApiError as exc:
        step('join_key', False, str(exc))
    step('engine', bool(engine_address()), '' if engine_address() else "This server isn't connected to Tailscale.")
    return {'ok': all(s['ok'] for s in steps), 'steps': steps}
