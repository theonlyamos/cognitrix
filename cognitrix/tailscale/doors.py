"""Two extra ways in, each listening on this machine only.

Tailscale forwards traffic to a local port. Two things must hold for that to
be safe, and the doors are how:

- **Tailnet door** (Tailscale Serve, private). Tailscale adds headers saying
  who the visitor is. Its own advice is to trust them only on a listener that
  nothing but Tailscale can reach. The main listener is open to the local
  network, so the headers are honoured only on requests that came through
  this door (``DoorGuard`` strips them everywhere else).
- **Public door** (Tailscale Funnel, the internet). It passes an allow list
  of paths (webhooks unless Settings widens it) and answers 404 for anything
  else before the app sees it.

Both run inside the web process, next to the main listener, and hand
requests to the same app. A request's door is in ``scope['cognitrix.door']``
(``door_of(request)``): 'tailnet', 'public', or 'direct' for the main listener.

Requests through a door never count as coming from this computer: the
caller's address is taken from what Tailscale forwarded, and is never a
loopback address, so nothing that trusts "the same machine" (the desktop
app's passkey step, for one) can be reached from outside.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import re
import socket
import time
from collections import deque
from typing import Any

from cognitrix import tailscale
from cognitrix.config import API_VERSION

logger = logging.getLogger('cognitrix.tailscale')

TAILNET = 'tailnet'
PUBLIC = 'public'
DIRECT = 'direct'
SCOPE_KEY = 'cognitrix.door'

# Headers only Tailscale Serve may set.
IDENTITY_PREFIX = b'tailscale-'

_API = f'/api/{API_VERSION}'
_SEG = r'[^/]+'

# (methods, path) the public door passes, by group. Whole-path matches.
_ALWAYS = (
    ({'GET'}, re.compile(rf'{_API}/tailscale/door')),
)
_GROUPS: dict[str, tuple[tuple[set[str], re.Pattern[str]], ...]] = {
    tailscale.ALLOW_WEBHOOKS: (
        ({'POST'}, re.compile(rf'{_API}/hooks/{_SEG}/{_SEG}')),                     # automation webhooks
        ({'POST'}, re.compile(rf'{_API}/github/app/webhook')),                      # the GitHub App
        ({'POST'}, re.compile(rf'{_API}/github/hooks/{_SEG}')),                     # repository webhooks
        ({'POST'}, re.compile(rf'{_API}/channels/sms/{_SEG}/webhook')),             # Twilio
        ({'GET', 'POST'}, re.compile(rf'{_API}/channels/whatsapp/{_SEG}/webhook')),  # Meta
        ({'POST'}, re.compile(rf'{_API}/channels/teams/{_SEG}/messages')),          # Microsoft Teams
    ),
    tailscale.ALLOW_LINKS: (
        ({'GET'}, re.compile(rf'/chat/{_SEG}(/widget\.js)?')),                      # public agent links
        ({'GET', 'POST'}, re.compile(rf'{_API}/public/.+')),
        ({'GET'}, re.compile(rf'/s/{_SEG}')),                                       # public artifact pages
        ({'GET'}, re.compile(rf'{_API}/artifact-frame/public/{_SEG}/{_SEG}')),
        # What those pages load: the web app's own files, nothing with data in it.
        ({'GET'}, re.compile(r'/(assets|css|fonts|webfonts|icons)/.+')),
        ({'GET'}, re.compile(r'/[A-Za-z0-9._-]+\.(js|css|png|svg|ico|json|webmanifest|txt|woff2?)')),
    ),
}

RATE_WINDOW_S = 60.0
RATE_LIMIT = 240          # requests a minute from one address at the public door
RATE_LIMIT_APP = 1500     # the same with the whole app let through (pages load many files)
_MAX_TRACKED = 5000
_hits: dict[str, deque[float]] = {}


def public_allowed(method: str, path: str, allow: frozenset[str] | None = None) -> bool:
    """Whether the public door lets this request through."""
    allow = tailscale.funnel_allow() if allow is None else allow
    # Nothing that could be read two ways further in.
    if '//' in path or '\\' in path or '/./' in path or '/../' in path or path.endswith(('/.', '/..')):
        return False
    method = method.upper()
    if any(method in methods and pattern.fullmatch(path) for methods, pattern in _ALWAYS):
        return True
    if tailscale.ALLOW_APP in allow:
        return True
    for group in allow:
        if any(method in methods and pattern.fullmatch(path) for methods, pattern in _GROUPS.get(group, ())):
            return True
    return False


def _over_limit(address: str, now: float | None = None, limit: int | None = None) -> bool:
    now = time.monotonic() if now is None else now
    limit = RATE_LIMIT if limit is None else limit
    if len(_hits) > _MAX_TRACKED:
        for key in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW_S]:
            _hits.pop(key, None)
    window = _hits.setdefault(address, deque())
    while window and now - window[0] > RATE_WINDOW_S:
        window.popleft()
    if len(window) >= limit:
        return True
    window.append(now)
    return False


def _header(scope: dict, name: bytes) -> str:
    for key, value in scope.get('headers') or ():
        if key == name:
            return value.decode('latin-1', 'replace')
    return ''


def _caller(scope: dict) -> tuple[str, int]:
    """The visitor's address as Tailscale forwarded it. Never a loopback
    address: a door is only reached from outside this machine."""
    forwarded = _header(scope, b'x-forwarded-for').split(',')[0].strip()
    try:
        ip = ipaddress.ip_address(forwarded.strip('[]'))
        if not (ip.is_loopback or ip.is_unspecified):
            return str(ip), 0
    except ValueError:
        pass
    return '0.0.0.0', 0  # noqa: S104 - a placeholder that matches nothing, not a bind address


async def _plain(send, status: int, text: str) -> None:
    body = text.encode()
    await send({'type': 'http.response.start', 'status': status, 'headers': [
        (b'content-type', b'text/plain; charset=utf-8'), (b'content-length', str(len(body)).encode()),
        (b'cache-control', b'no-store')]})
    await send({'type': 'http.response.body', 'body': body})


def door_app(app, name: str):
    """The app as seen through one door."""

    async def call(scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await app(scope, receive, send)
        scope = dict(scope)
        scope[SCOPE_KEY] = name
        scope['client'] = _caller(scope)
        # Nothing in front of a door but Tailscale: a hosting platform's own
        # "who is calling" header means nothing here.
        headers = [(k, v) for k, v in scope.get('headers') or () if k != b'fly-client-ip']
        forwarded_host = _header(scope, b'x-forwarded-host').split(',')[0].strip()
        if forwarded_host:
            # The name the visitor typed, so links and passkeys see the real address.
            headers = [(k, v) for k, v in headers if k != b'host'] + [(b'host', forwarded_host.encode('latin-1', 'ignore'))]
        scope['scheme'] = 'wss' if scope['type'] == 'websocket' else 'https'
        if name == PUBLIC:
            headers = [(k, v) for k, v in headers if not k.startswith(IDENTITY_PREFIX)]
            method = scope.get('method', 'GET') if scope['type'] == 'http' else 'GET'
            if not tailscale.funnel_wanted():
                # The public address is off in Settings. Should Tailscale still
                # forward to this door (taking it down failed), nothing gets through.
                if scope['type'] == 'websocket':
                    await receive()
                    return await send({'type': 'websocket.close', 'code': 1008})
                return await _plain(send, 404, 'Not found')
            allow = tailscale.funnel_allow()
            whole_app = tailscale.ALLOW_APP in allow
            if scope['type'] == 'websocket':
                # Live connections belong to signed-in people: only with the whole app public.
                if not whole_app:
                    await receive()
                    return await send({'type': 'websocket.close', 'code': 1008})
            elif not public_allowed(method, scope.get('path', ''), allow):
                return await _plain(send, 404, 'Not found')
            elif _over_limit(scope['client'][0], limit=RATE_LIMIT_APP if whole_app else None):
                return await _plain(send, 429, 'Too many requests')
        scope['headers'] = headers
        await app(scope, receive, send)

    return call


class DoorGuard:
    """On the app itself: a request that did not come through the tailnet
    door carries no Tailscale identity, whatever its headers claim."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket') and scope.get(SCOPE_KEY) != TAILNET:
            headers = scope.get('headers') or ()
            if any(k.startswith(IDENTITY_PREFIX) for k, _ in headers):
                scope = dict(scope)
                scope['headers'] = [(k, v) for k, v in headers if not k.startswith(IDENTITY_PREFIX)]
        await self.app(scope, receive, send)


def door_of(request: Any) -> str:
    try:
        return str(request.scope.get(SCOPE_KEY) or DIRECT)
    except Exception:  # noqa: BLE001
        return DIRECT


# ---------------------------------------------------------------------------
# The listeners
# ---------------------------------------------------------------------------

_servers: dict[str, Any] = {}
_tasks: dict[str, asyncio.Task] = {}
_ports: dict[str, int] = {}
_start_lock: asyncio.Lock | None = None


def _ports_file():
    return tailscale.folder() / 'doors.json'


def _remembered_ports() -> dict[str, int]:
    try:
        data = json.loads(_ports_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return {k: int(v) for k, v in data.items() if k in (TAILNET, PUBLIC) and isinstance(v, int)} \
        if isinstance(data, dict) else {}


def _bind(preferred: int) -> socket.socket:
    """A loopback listening socket, on the port used last time when it is free
    (so Tailscale's saved forwarding keeps pointing at the right place)."""
    for port in (preferred, 0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', port))
            return sock
        except OSError:
            sock.close()
            if port == 0:
                raise
    raise OSError('no free port')  # pragma: no cover


def ports() -> dict[str, int]:
    """{'tailnet': port, 'public': port} once the doors are open in this process."""
    return dict(_ports)


def is_open() -> bool:
    return len(_ports) == 2 and all(not task.done() for task in _tasks.values())


async def ensure_open(app) -> dict[str, int]:
    """Open both doors (once). Safe to call again."""
    global _start_lock
    if _start_lock is None:
        _start_lock = asyncio.Lock()
    async with _start_lock:
        if is_open():
            return ports()
        await close()
        import uvicorn

        class _Server(uvicorn.Server):
            # The main server owns Ctrl+C and SIGTERM; a door must not take them over.
            @contextlib.contextmanager
            def capture_signals(self):
                yield

        remembered = _remembered_ports()
        for name in (TAILNET, PUBLIC):
            sock = _bind(remembered.get(name, 0))
            config = uvicorn.Config(door_app(app, name), lifespan='off', log_level='warning',
                                    proxy_headers=False, access_log=False)
            server = _Server(config)
            _servers[name] = server
            _ports[name] = int(sock.getsockname()[1])
            _tasks[name] = asyncio.create_task(server.serve(sockets=[sock]), name=f'tailscale-{name}-door')
        try:
            folder = tailscale.folder()
            folder.mkdir(parents=True, exist_ok=True)
            _ports_file().write_text(json.dumps(_ports), encoding='utf-8')
        except OSError:
            logger.debug("Couldn't remember the door ports", exc_info=True)
        logger.info('Tailscale doors open on this machine: tailnet %s, public %s', _ports[TAILNET], _ports[PUBLIC])
        return ports()


async def close() -> None:
    for server in _servers.values():
        server.should_exit = True
    for task in list(_tasks.values()):
        try:
            await asyncio.wait_for(task, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception:  # noqa: BLE001
            logger.debug('A Tailscale door did not close cleanly', exc_info=True)
    _servers.clear()
    _tasks.clear()
    _ports.clear()
