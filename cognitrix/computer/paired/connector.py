"""The PC side of a paired computer.

Runs on the person's own PC: in the desktop app's engine once the app is
linked to a web account, or with ``cognitrix connect`` on any machine. It
keeps one outgoing WebSocket to the server (no open ports), answers tool
calls by running the same Bash and file tools the desktop app uses, and
enforces the owner's limits here, whatever the server asks:

- only the folders shared on this PC (links can't lead out of them),
- only the powers switched on (files, commands),
- nothing at all while paused.

The link is saved in ``~/.cognitrix/paired.json`` (readable only by this
user): the server address, this PC's token, its name, folders and powers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform as _platform
import random
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cognitrix.computer.paired import (
    COMMANDS,
    FILES,
    PROTOCOL,
    clean_folders,
    clean_powers,
    normalize_code,
    tool_power,
)

logger = logging.getLogger('cognitrix.paired')

API_PREFIX = '/api/v1/computer/devices'
PING_S = 45
MAX_BACKOFF_S = 60


class LinkError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# The saved link
# ---------------------------------------------------------------------------

def config_path() -> Path:
    return Path(os.environ.get('COGNITRIX_PAIRED_CONFIG') or (Path.home() / '.cognitrix' / 'paired.json'))


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def forget_config() -> None:
    try:
        config_path().unlink()
    except OSError:
        pass


def public_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The link without its token, for the desktop app's screen."""
    cfg = load_config() if cfg is None else cfg
    return {
        'linked': bool(cfg.get('token')),
        'server': cfg.get('server') or '',
        'account': cfg.get('account') or '',
        'device_id': cfg.get('device_id') or '',
        'name': cfg.get('name') or '',
        'folders': clean_folders(cfg.get('folders')),
        'powers': clean_powers(cfg.get('powers')),
        'paused': bool(cfg.get('paused')),
    }


def platform_label() -> str:
    system = _platform.system()
    if system == 'Windows':
        release = _platform.release()
        return f'Windows {release}'.strip()
    if system == 'Darwin':
        return f"macOS {_platform.mac_ver()[0]}".strip()
    return f'Linux {_platform.release()}'.strip() if system == 'Linux' else system


def default_name() -> str:
    host = _platform.node().split('.')[0] or 'My PC'
    return host[:80]


def normalize_server(value: str) -> str:
    raw = str(value or '').strip().rstrip('/')
    if not raw:
        raise LinkError('bad_server', 'Give the address of your Cognitrix server, like cognitrix.example.com.')
    if '://' not in raw:
        raw = 'https://' + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        raise LinkError('bad_server', f"{value} isn't a web address.")
    host = (parsed.hostname or '').lower()
    if parsed.scheme == 'http' and host not in ('localhost', '127.0.0.1', '::1') \
            and os.environ.get('COGNITRIX_PAIRED_ALLOW_HTTP') != '1':
        raise LinkError('insecure', 'The server has to use https, so your token and files are encrypted on the way.')
    path = parsed.path.rstrip('/')
    if path.endswith('/api/v1'):
        path = path[:-len('/api/v1')]
    return f'{parsed.scheme}://{parsed.netloc}{path}'


def check_folders(folders: Any) -> list[dict[str, Any]]:
    """Shared folders as absolute, existing, allowed paths."""
    from cognitrix.common.folders import is_blocked
    out, seen = [], set()
    for item in clean_folders(folders):
        path = Path(item['path']).expanduser()
        if not path.is_absolute():
            raise LinkError('bad_folder', f"{item['path']} isn't a full path.")
        try:
            real = path.resolve()
        except OSError:
            raise LinkError('bad_folder', f"{item['path']} can't be opened.") from None
        if not real.is_dir():
            raise LinkError('bad_folder', f"{item['path']} isn't a folder on this PC.")
        if is_blocked(real):
            raise LinkError('bad_folder', f"{item['path']} is a protected place (keys, sign-ins, system files or a "
                            'whole drive), so it can\'t be shared.')
        key = os.path.normcase(str(real))
        if key not in seen:
            seen.add(key)
            out.append({'path': str(real), 'writable': bool(item.get('writable', True))})
    return out


# ---------------------------------------------------------------------------
# Pairing (HTTP, before there is a token)
# ---------------------------------------------------------------------------

async def _post(server: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    import aiohttp
    url = normalize_server(server) + API_PREFIX + path
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
            async with http.post(url, json=body) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                if resp.status >= 400:
                    detail = data.get('detail') if isinstance(data, dict) else None
                    if isinstance(detail, dict):
                        raise LinkError(str(detail.get('code') or 'failed'), str(detail.get('message') or detail))
                    raise LinkError('failed', str(detail or f'The server answered {resp.status}.'))
                return data if isinstance(data, dict) else {}
    except LinkError:
        raise
    except Exception as exc:
        raise LinkError('unreachable', f"Couldn't reach {server}: {exc}") from None


async def preview(server: str, code: str) -> dict[str, Any]:
    """Whose account a code belongs to, so the owner knows what they allow."""
    code = normalize_code(code)
    if not code:
        raise LinkError('bad_code', 'The code has 8 letters and numbers, like K7QF-29XM.')
    out = await _post(server, '/claim', {'code': code})
    return {'server': normalize_server(server), 'account': str(out.get('account') or ''), 'code': code}


async def link(server: str, code: str, *, name: str, folders: Any, powers: Any) -> dict[str, Any]:
    """Pair this PC and save the link. Returns the public view of it."""
    from cognitrix.config import VERSION

    server = normalize_server(server)
    code = normalize_code(code)
    if not code:
        raise LinkError('bad_code', 'The code has 8 letters and numbers, like K7QF-29XM.')
    shared = check_folders(folders)
    chosen = clean_powers(powers)
    info = await _post(server, '/claim', {'code': code})
    out = await _post(server, '/pair', {
        'code': code, 'name': (name or default_name())[:80], 'platform': platform_label(),
        'folders': shared, 'powers': chosen, 'version': VERSION,
    })
    if not out.get('token'):
        raise LinkError('failed', "The server didn't send a token back.")
    cfg = {
        'server': server, 'token': out['token'], 'device_id': str(out.get('device_id') or ''),
        'account': str(info.get('account') or ''), 'name': (name or default_name())[:80],
        'folders': shared, 'powers': chosen, 'paused': False, 'paired_at': time.time(),
    }
    save_config(cfg)
    return public_config(cfg)


# ---------------------------------------------------------------------------
# Running a call from the server
# ---------------------------------------------------------------------------

def _folders_for(cfg: dict[str, Any], *, for_commands: bool = False):
    """The shared folders as tool folders. For commands, only read & write
    folders: a command runs as the person and can change whatever it reaches,
    so a folder shared read only never hosts one."""
    from cognitrix.common.folders import READ, WRITE, Folder
    powers = set(clean_powers(cfg.get('powers')))
    out = []
    for item in clean_folders(cfg.get("folders")):
        path = Path(item['path'])
        if not path.is_dir():
            continue
        if for_commands and not item.get('writable', True):
            continue
        writable = item.get('writable', True) and FILES in powers
        out.append(Folder(path=str(path), access=WRITE if writable else READ, name=path.name or str(path),
                          primary=not out))
    return tuple(out)


def _text(value: Any) -> str:
    content = getattr(value, 'content', value)
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, default=str)[:200_000]
    except (TypeError, ValueError):
        return str(content)


def _log(entry: dict[str, Any]) -> None:
    """A local record of what agents did here (~/.cognitrix/paired-log.jsonl)."""
    try:
        path = config_path().with_name('paired-log.jsonl')
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry, default=str) + '\n')
    except OSError:
        pass


async def run_call(cfg: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """Run one call from the server here, within this PC's limits."""
    from cognitrix.common.process_security import HostProcessMode
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    call_id = message.get('id')
    name = str(message.get('tool') or '')
    args = message.get('args') if isinstance(message.get('args'), dict) else {}
    ctx = message.get('ctx') if isinstance(message.get('ctx'), dict) else {}

    def fail(code: str, text: str) -> dict[str, Any]:
        _log({'at': time.time(), 'tool': name, 'ok': False, 'error': code})
        return {'type': 'result', 'id': call_id, 'ok': False, 'error': code, 'message': text}

    power = tool_power(name)
    if power is None:
        return fail('not_allowed', f"{name} doesn't run on a paired PC.")
    if cfg.get('paused'):
        return fail('paused', 'Agents are paused on this PC.')
    if power not in clean_powers(cfg.get('powers')):
        return fail(f'no_{power}', f'{power} is switched off on this PC.')
    if power == COMMANDS and _folders_for(cfg) and not _folders_for(cfg, for_commands=True):
        return fail('read_only', 'Every folder on this PC is shared read only, and a command could change files, so '
                    'commands are refused. Ask the person to share a folder as read & write to run commands.')
    folders = _folders_for(cfg, for_commands=power == COMMANDS)
    if not folders:
        return fail('no_folders', "This PC hasn't shared any folders (or they were moved). Ask the person to add one "
                    'on the PC.')
    tool = ToolManager.get_by_name(name)
    if tool is None:
        return fail('not_allowed', f'{name} is not available on this PC.')
    if name != 'Bash':
        args.pop('approved', None)
    context = ToolExecutionContext(
        user_id=f"paired:{cfg.get('device_id') or 'pc'}",
        session_id=str(ctx.get('session_id') or '') or None,
        agent_id=str(ctx.get('agent_id') or '') or None,
        task_id=str(ctx.get('task_id') or '') or None,
        host_process_mode=HostProcessMode.TRUSTED_LOCAL if power == COMMANDS else HostProcessMode.DENY,
        folders=folders,
        device_host=True,
    )
    token = set_execution_context(context)
    started = time.time()
    try:
        validated = tool.validate_parameters(dict(args)) if hasattr(tool, 'validate_parameters') else dict(args)
        text = _text(await tool.run(**validated))
    except Exception as exc:
        logger.warning('A call from the server failed here', exc_info=True)
        return fail('failed', f'{name} failed on the PC: {exc}')
    finally:
        reset_execution_context(token)
    _log({'at': started, 'tool': name, 'ok': True, 'seconds': round(time.time() - started, 1),
          'command': args.get('command'), 'path': args.get('file_path') or args.get('path')})
    return {'type': 'result', 'id': call_id, 'ok': True, 'text': text}


# ---------------------------------------------------------------------------
# The connection
# ---------------------------------------------------------------------------

class Connector:
    """Keeps this PC connected to its server, reconnecting with backoff."""

    def __init__(self) -> None:
        self.status = 'stopped'  # stopped | connecting | connected | offline | unpaired
        self.last_error = ''
        self.connected_since: float | None = None
        self._ws: Any = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._calls: set[asyncio.Task] = set()

    # -- control ---------------------------------------------------------------
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self.status = 'stopped'

    async def push_state(self) -> None:
        """Tell the server about changed folders, powers or pause."""
        if self._ws is None or self._ws.closed:
            return
        cfg = load_config()
        try:
            await self._ws.send_json({'type': 'state', 'name': cfg.get('name'), 'folders': clean_folders(cfg.get('folders')),
                                      'powers': clean_powers(cfg.get('powers')), 'paused': bool(cfg.get('paused'))})
        except Exception:
            logger.debug('Could not send the new state', exc_info=True)

    async def unpair(self) -> None:
        """Tell the server to forget this PC, then forget the link here."""
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_json({'type': 'unpair'})
            except Exception:
                pass
        forget_config()
        await self.stop()
        self.status = 'unpaired'

    def view(self) -> dict[str, Any]:
        return {'status': self.status, 'error': self.last_error, 'connected_since': self.connected_since}

    # -- loop ------------------------------------------------------------------
    async def run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            cfg = load_config()
            if not cfg.get('token'):
                self.status = 'unpaired'
                return
            self.status = 'connecting'
            try:
                await self._session(cfg)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except LinkError as exc:
                self.last_error = exc.message
                if exc.code in ('revoked', 'unpaired'):
                    forget_config()
                    self.status = 'unpaired'
                    return
            except Exception as exc:
                self.last_error = str(exc) or exc.__class__.__name__
                logger.info('Paired PC connection dropped: %s', self.last_error)
            finally:
                self._ws = None
                self.connected_since = None
            if self._stop.is_set():
                break
            self.status = 'offline'
            delay = min(MAX_BACKOFF_S, backoff) * (0.7 + random.random() * 0.6)
            backoff = min(MAX_BACKOFF_S, backoff * 2)
            try:
                await asyncio.wait_for(self._stop.wait(), delay)
            except TimeoutError:
                pass
        self.status = 'stopped'

    async def _session(self, cfg: dict[str, Any]) -> None:
        import aiohttp

        from cognitrix.config import VERSION

        server = normalize_server(cfg['server'])
        ws_url = server.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1) + API_PREFIX + '/connect'
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(ws_url, heartbeat=PING_S, max_msg_size=64 * 1024 * 1024) as ws:
                self._ws = ws
                await ws.send_json({
                    'type': 'hello', 'protocol': PROTOCOL, 'token': cfg['token'], 'name': cfg.get('name'),
                    'platform': platform_label(), 'version': VERSION,
                    'folders': clean_folders(cfg.get('folders')), 'powers': clean_powers(cfg.get('powers')),
                    'paused': bool(cfg.get('paused')),
                })
                first = await asyncio.wait_for(ws.receive(), 20)
                hello = _decode(first)
                if hello.get('type') == 'error':
                    raise LinkError(str(hello.get('code') or 'failed'), str(hello.get('message') or 'Refused'))
                if hello.get('type') != 'welcome':
                    raise LinkError('failed', 'The server did not accept this PC.')
                self.status = 'connected'
                self.last_error = ''
                self.connected_since = time.time()
                async for msg in ws:
                    data = _decode(msg)
                    kind = data.get('type')
                    if kind == 'call':
                        task = asyncio.create_task(self._answer(ws, data))
                        self._calls.add(task)
                        task.add_done_callback(self._calls.discard)
                    elif kind == 'unpaired':
                        raise LinkError('unpaired', 'This PC was unpaired on the web.')
                    elif kind == 'error':
                        raise LinkError(str(data.get('code') or 'failed'), str(data.get('message') or ''))
                close_code = ws.close_code
                if close_code == 4401:
                    raise LinkError('revoked', 'The server no longer knows this PC. Pair it again.')

    async def _answer(self, ws: Any, message: dict[str, Any]) -> None:
        reply = await run_call(load_config(), message)
        try:
            await ws.send_json(reply)
        except Exception:
            logger.debug('Could not send a result back', exc_info=True)


def _decode(msg: Any) -> dict[str, Any]:
    import aiohttp
    if msg.type == aiohttp.WSMsgType.TEXT:
        try:
            data = json.loads(msg.data)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
        return {'type': 'closed'}
    return {}


_connector: Connector | None = None


def get_connector() -> Connector:
    global _connector
    if _connector is None:
        _connector = Connector()
    return _connector
