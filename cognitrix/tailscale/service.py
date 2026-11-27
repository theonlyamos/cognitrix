"""What Settings > Tailscale shows and does.

``reconcile`` makes this machine's Tailscale match Settings: the private
address (Tailscale Serve pointed at the tailnet door) and the public address
(Tailscale Funnel pointed at the public door). It only ever touches what it
put there itself: a port something else already serves is reported, never
taken over, and the machine's own Tailscale is never signed in or out from
here (only the built-in one is, see ``daemon``).

Only the web process calls ``reconcile``: it owns the doors. It writes the
addresses to ``addresses.json`` for every other process
(``cognitrix.common.addresses``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from cognitrix import tailscale
from cognitrix.tailscale import cli, daemon, doors

logger = logging.getLogger('cognitrix.tailscale')

SYSTEM = 'system'      # the machine's own Tailscale
BUILTIN = 'builtin'    # the one Cognitrix runs itself (a server in Docker or on Fly)
MISSING = 'missing'

# Where the machine's own Tailscale keeps its things on Linux. When any is
# here, the machine has a Tailscale of its own and the built-in one stays off.
_SYSTEM_MARKS = ('/var/run/tailscale/tailscaled.sock', '/run/tailscale/tailscaled.sock',
                 '/var/lib/tailscale/tailscaled.state')

# A try that ended with "turn this on for your tailnet first" is not repeated
# sooner than this: each one waits on the client.
RETRY_AFTER_S = 20.0
FRESH_FOR_S = 3.0

_lock = threading.Lock()
_last: dict[str, Any] | None = None
_last_at = 0.0
_waiting: dict[str, tuple[str, float, str]] = {}   # 'serve'|'funnel' -> (page to open, when, what for)
_login_url = ''
_task: asyncio.Task | None = None


class TailscaleError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def mode() -> str:
    if daemon.running():
        return BUILTIN
    if cli.find_cli() is None:
        return MISSING
    if any(os.path.exists(mark) for mark in _SYSTEM_MARKS):
        return SYSTEM
    if daemon.available():
        return BUILTIN
    return SYSTEM


# ---------------------------------------------------------------------------
# What we set up, and the addresses other code reads
# ---------------------------------------------------------------------------

def _ours_file() -> Path:
    return tailscale.folder() / 'ours.json'


def addresses_file() -> Path:
    return tailscale.folder() / 'addresses.json'


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict[str, Any]) -> None:
    try:
        if _read(path) == data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data), encoding='utf-8')
        os.replace(temp, path)
    except OSError:
        logger.debug("Couldn't write %s", path, exc_info=True)


def _url(name: str, port: int) -> str:
    if not name:
        return ''
    return f'https://{name}' if port == 443 else f'https://{name}:{port}'


# ---------------------------------------------------------------------------
# Reading Tailscale's status
# ---------------------------------------------------------------------------

def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


_STATES = {'Running': 'running', 'NeedsLogin': 'needs_login', 'NoState': 'needs_login',
           'NeedsMachineAuth': 'needs_approval', 'Stopped': 'stopped', 'Starting': 'starting'}


def _has_cap(me: dict, name: str) -> bool | None:
    """Whether this machine has a capability. None when Tailscale didn't say."""
    cap_map, caps = me.get('CapMap'), me.get('Capabilities')
    if not isinstance(cap_map, dict) and not isinstance(caps, list):
        return None
    names = [*(_dict(cap_map)), *(str(c) for c in _list(caps))]
    return any(n == name or n.split('?')[0].rstrip('/').endswith(f'/{name}') for n in names)


def _describe(status: dict[str, Any] | None, kind: str) -> dict[str, Any]:
    status = status or {}
    me = _dict(status.get('Self'))
    name = str(me.get('DNSName') or '').rstrip('.').lower()
    user = _dict(_dict(status.get('User')).get(str(me.get('UserID') or '')))
    tags = [str(t) for t in _list(me.get('Tags'))]
    if kind == MISSING:
        state = 'missing'
    elif not status:
        state = 'needs_login' if kind == BUILTIN else 'stopped'
    else:
        state = _STATES.get(str(status.get('BackendState') or ''), 'stopped')
    allow = sorted(tailscale.funnel_allow())
    return {
        'enabled': tailscale.enabled(),
        'mode': kind,
        'state': state,
        'version': str(status.get('Version') or '')[:40],
        'name': name if state == 'running' else '',
        'machine': name.split('.')[0] if name else '',
        'tailnet': str(_dict(status.get('CurrentTailnet')).get('Name') or ''),
        'ips': [str(ip) for ip in _list(me.get('TailscaleIPs') or status.get('TailscaleIPs'))][:4],
        'user': str(user.get('LoginName') or ''),
        'tags': tags,
        'key_expiry': '' if tags else str(me.get('KeyExpiry') or ''),
        'https': bool(_list(status.get('CertDomains'))) if status else False,
        'funnel_allowed': _has_cap(me, 'funnel'),
        'login_url': (str(status.get('AuthURL') or '') or _login_url) if state == 'needs_login' else '',
        'health': [str(h)[:300] for h in _list(status.get('Health'))][:5],
        'hostname': tailscale.hostname(),
        'can_sign_in': kind == BUILTIN,
        'doors_open': doors.is_open(),
        'private': {'wanted': tailscale.serve_wanted(), 'port': tailscale.serve_port(), 'active': False,
                    'url': '', 'problem': '', 'detail': '', 'needs_url': ''},
        'public': {'wanted': tailscale.funnel_wanted(), 'port': tailscale.funnel_port(), 'active': False,
                   'url': '', 'problem': '', 'detail': '', 'needs_url': '', 'allow': allow,
                   'ports': list(tailscale.FUNNEL_PORTS)},
    }


# ---------------------------------------------------------------------------
# Serve and Funnel
# ---------------------------------------------------------------------------

def _explain(result: cli.Result) -> tuple[str, str, str]:
    """(problem code, detail, page to open) for a Serve or Funnel call that failed."""
    text = result.text
    low = text.lower()
    url = cli.find_url(text)
    if url and (result.timed_out or 'not enabled' in low or 'to enable' in low):
        return 'needs_turning_on', '', url
    if 'access denied' in low or 'permission denied' in low or '--operator' in low:
        return 'needs_operator', '', ''
    if result.timed_out:
        return 'slow', '', ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return 'other', (lines[0] if lines else 'Tailscale gave no reason.')[:300], url


def _is_door(served: cli.Served | None, mine: dict[str, Any], target: str) -> bool:
    """Whether what a port serves is something we put there: one handler,
    proxying to a door (the one open now, or the one we remembered)."""
    if served is None or served.handlers != 1 or not served.target:
        return False
    return served.target == target or served.target == str(mine.get('target') or '-')


_KINDS = (('serve', 'private', doors.TAILNET), ('funnel', 'public', doors.PUBLIC))


def _take_down(ours: dict[str, Any], served: dict[int, cli.Served], keep: dict[str, int]) -> bool:
    """Remove what we set up on a port Settings no longer names. True when anything changed."""
    changed = False
    ports = doors.ports()
    for kind, _, door in _KINDS:
        mine = _dict(ours.get(kind))
        port = int(mine.get('port') or 0)
        if not port or keep.get(kind) == port:
            continue
        target = cli.target_for(ports[door]) if door in ports else '-'
        if _is_door(served.get(port), mine, target):
            cli.serve_off(port, funnel=kind == 'funnel')
            changed = True
        ours.pop(kind, None)
        _waiting.pop(kind, None)
    return changed


def _apply(snap: dict[str, Any]) -> None:
    ours = _read(_ours_file())
    before = json.dumps(ours, sort_keys=True)
    served = cli.serving()
    want = {kind: snap[part]['port'] for kind, part, _ in _KINDS if snap[part]['wanted']}
    if _take_down(ours, served, want):
        served = cli.serving()
    ports = doors.ports()
    now = time.monotonic()
    for kind, part, door in _KINDS:
        info = snap[part]
        if not info['wanted']:
            continue
        if not snap['name']:
            # No name, no https address: MagicDNS is off for the tailnet.
            info['problem'] = 'needs_magicdns'
            continue
        port = int(info['port'])
        funnel = kind == 'funnel'
        if funnel and snap['private']['wanted'] and port == int(snap['private']['port']):
            info['problem'] = 'same_port'
            continue
        if door not in ports:
            info['problem'] = 'doors_closed'
            continue
        target = cli.target_for(ports[door])
        mine = _dict(ours.get(kind)) if int(_dict(ours.get(kind)).get('port') or 0) == port else {}
        current = served.get(port)
        if current is not None and not _is_door(current, mine, target):
            info['problem'] = 'port_in_use'
            continue
        if current is None or current.target != target or current.funnel != funnel:
            waiting = _waiting.get(kind)
            if waiting and waiting[2] == f'{port}>{target}' and now - waiting[1] < RETRY_AFTER_S:
                info['problem'], info['needs_url'] = 'needs_turning_on', waiting[0]
                continue
            result = cli.serve_on(port, ports[door], funnel=funnel)
            if not result.ok:
                info['problem'], info['detail'], info['needs_url'] = _explain(result)
                if info['problem'] == 'needs_turning_on':
                    _waiting[kind] = (info['needs_url'], now, f'{port}>{target}')
                else:
                    logger.warning("Tailscale didn't set up the %s address: %s", part, result.text[:300])
                continue
        _waiting.pop(kind, None)
        ours[kind] = {'port': port, 'target': target}
        info['active'] = True
        info['url'] = _url(snap['name'], port)
    if json.dumps(ours, sort_keys=True) != before:
        _write(_ours_file(), ours)


def _forget_all() -> None:
    """Tailscale support was switched off: remove what we set up."""
    ours = _read(_ours_file())
    if ours and cli.find_cli() is not None:
        _take_down(ours, cli.serving(), {})
        _write(_ours_file(), ours)
    _waiting.clear()


def reconcile() -> dict[str, Any]:
    """Make Tailscale match Settings, and say how things stand. Slow (it runs
    the Tailscale client): call it from a worker thread."""
    global _last, _last_at
    with _lock:
        kind = mode()
        if not tailscale.enabled():
            _forget_all()
            if kind == BUILTIN and daemon.running():
                daemon.stop()
            snap = _describe(cli.status() if kind == SYSTEM else None, kind)
        else:
            if kind == BUILTIN and not daemon.running() and daemon.has_state():
                daemon.start()
            snap = _describe(cli.status() if kind != MISSING else None, kind)
            if snap['state'] == 'running':
                _apply(snap)
        private, public = snap['private'], snap['public']
        _write(addresses_file(), {
            'private': private['url'] if private['active'] else '',
            'public': public['url'] if public['active'] else '',
            'allow': public['allow'],
            'name': snap['name'],
        })
        _last, _last_at = snap, time.monotonic()
        return snap


def snapshot(fresh: bool = False) -> dict[str, Any]:
    if not fresh and _last is not None and time.monotonic() - _last_at < FRESH_FOR_S:
        return _last
    return reconcile()


def pending() -> bool:
    """Something is waiting on the owner (a sign-in, a feature to turn on)."""
    snap = _last or {}
    if not snap.get('enabled'):
        return False
    return snap.get('state') in ('needs_login', 'needs_approval', 'starting') or any(
        _dict(snap.get(part)).get('wanted') and not _dict(snap.get(part)).get('active')
        for part in ('private', 'public'))


# ---------------------------------------------------------------------------
# Signing the built-in Tailscale in and out
# ---------------------------------------------------------------------------

def connect(auth_key: str = '') -> dict[str, Any]:
    """Sign the built-in Tailscale in: with a key right away, or by a page the
    owner opens (``login_url`` in the answer). The machine's own Tailscale is
    signed in from its own app, never from here."""
    global _login_url
    kind = mode()
    if kind == MISSING:
        raise TailscaleError('missing', 'Tailscale is not installed on this machine.')
    if kind == BUILTIN:
        if not daemon.start():
            raise TailscaleError('daemon', f"The built-in Tailscale couldn't start. Its log is at {daemon.log_file()}.")
        args = ['up', f'--hostname={tailscale.hostname()}']
        key_file = tailscale.folder() / 'join-key'
        auth_key = auth_key.strip()
        try:
            if auth_key:
                key_file.touch(mode=0o600, exist_ok=True)
                os.chmod(key_file, 0o600)
                key_file.write_text(auth_key, encoding='utf-8')
                args.append(f'--auth-key=file:{key_file}')
            result = cli.run(*args, timeout=45 if auth_key else 8)
        finally:
            key_file.unlink(missing_ok=True)
        _login_url = '' if result.ok else cli.find_url(result.text)
        if auth_key and not result.ok:
            lines = [line.strip() for line in result.text.splitlines() if line.strip()]
            raise TailscaleError('auth_key', (lines[-1] if lines else "Tailscale didn't accept the key.")[:300])
    return reconcile()


def disconnect() -> dict[str, Any]:
    """Sign the built-in Tailscale out. Its name and address are given up."""
    global _login_url
    if mode() != BUILTIN:
        raise TailscaleError('not_ours', "This machine's own Tailscale is signed out from its own app.")
    with _lock:
        _forget_all()
        if daemon.running():
            cli.run('logout', timeout=10)
        _login_url = ''
    return reconcile()


def check() -> dict[str, Any]:
    """Open each address the way a visitor would and see which door answers."""
    import httpx

    from cognitrix.config import API_VERSION

    snap = snapshot()
    proxy = daemon.proxy_url() or None
    out: dict[str, Any] = {}
    for part, door in (('private', doors.TAILNET), ('public', doors.PUBLIC)):
        info = snap[part]
        if not info['active']:
            continue
        try:
            with httpx.Client(timeout=10, proxy=proxy) as client:
                response = client.get(f"{info['url']}/api/{API_VERSION}/tailscale/door")
            ok = response.status_code == 200 and response.json().get('door') == door
            out[part] = {'ok': ok, 'detail': '' if ok else f'Answered {response.status_code}.'}
        except Exception as exc:  # noqa: BLE001 - whatever went wrong is the answer
            out[part] = {'ok': False, 'detail': str(exc)[:200] or type(exc).__name__}
    return out


# ---------------------------------------------------------------------------
# In the web process
# ---------------------------------------------------------------------------

async def _watch() -> None:
    while True:
        try:
            await asyncio.to_thread(reconcile)
        except Exception:  # noqa: BLE001 - keep watching
            logger.exception('Tailscale check failed')
        if not tailscale.enabled():
            return
        await asyncio.sleep(5 if pending() else 60)


async def start(app) -> None:
    """Open the doors and keep Tailscale matching Settings. Does nothing
    while Tailscale support is off."""
    global _task
    if not tailscale.enabled():
        return
    await doors.ensure_open(app)
    if _task is None or _task.done():
        _task = asyncio.create_task(_watch(), name='tailscale-watch')


async def settings_changed(app) -> dict[str, Any]:
    """Settings were saved: act on them now."""
    if tailscale.enabled():
        await start(app)
    snap = await asyncio.to_thread(reconcile)
    if not tailscale.enabled():
        await doors.close()
    return snap


async def stop() -> None:
    global _task
    task, _task = _task, None
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    await doors.close()
    if daemon.owned():
        await asyncio.to_thread(daemon.stop)
