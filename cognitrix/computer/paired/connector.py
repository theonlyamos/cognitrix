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
    APPS,
    COMMANDS,
    FILES,
    PROTOCOL,
    SCREEN,
    clean_folders,
    clean_powers,
    normalize_code,
    secret_store,
    tool_power,
    undo,
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


_keychain_token: str | None = None  # what the keychain last gave or took, so it isn't asked on every call


def load_config() -> dict[str, Any]:
    global _keychain_token
    try:
        data = json.loads(config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if not data.get('token') and data.get('token_store') == 'keychain':
        if _keychain_token is None:
            _keychain_token = secret_store.load()
        if _keychain_token:
            data['token'] = _keychain_token
    return data


def save_config(cfg: dict[str, Any]) -> None:
    """Write the link. The token goes to the system keychain when there is one; else into the file."""
    global _keychain_token
    cfg = dict(cfg)
    token = cfg.get('token')
    cfg.pop('token_store', None)
    if token:
        if token == _keychain_token or secret_store.save(token):
            _keychain_token = token
            cfg.pop('token')
            cfg['token_store'] = 'keychain'
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def token_location(cfg: dict[str, Any] | None = None) -> str:
    """Where this PC's token is kept, in words for the person."""
    try:
        raw = json.loads(config_path().read_text(encoding='utf-8')) if cfg is None else cfg
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict) and raw.get('token_store') == 'keychain':
        return secret_store.where() or 'keychain'
    return str(config_path())


def forget_config() -> None:
    global _keychain_token
    _keychain_token = None
    secret_store.delete()
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
        'servers': servers_of(cfg),
        'account': cfg.get('account') or '',
        'device_id': cfg.get('device_id') or '',
        'name': cfg.get('name') or '',
        'folders': clean_folders(cfg.get('folders')),
        'powers': clean_powers(cfg.get('powers')),
        'paused': bool(cfg.get('paused')),
        'token_kept_in': token_location(cfg) if cfg.get('token') else '',
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
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in ('https', 'http') or not host or any(c.isspace() for c in parsed.netloc):
        raise LinkError('bad_server', f"{value} isn't a web address.")
    if parsed.scheme == 'http' and host not in ('localhost', '127.0.0.1', '::1') \
            and os.environ.get('COGNITRIX_PAIRED_ALLOW_HTTP') != '1' and not _on_my_tailnet(host):
        raise LinkError('insecure', 'The server has to use https, so your token and files are encrypted on the way. '
                        'Plain http only works for a machine on your tailnet, where Tailscale encrypts the path, '
                        "and Tailscale on this PC doesn't list this address.")
    path = parsed.path.rstrip('/')
    if path.endswith('/api/v1'):
        path = path[:-len('/api/v1')]
    return f'{parsed.scheme}://{parsed.netloc}{path}'


def _on_my_tailnet(host: str) -> bool:
    try:
        from cognitrix.computer.paired import tailnet

        return tailnet.confirms(host)
    except Exception:  # noqa: BLE001 - no Tailscale here: the address is not confirmed
        return False


MAX_SERVERS = 4


def servers_of(cfg: dict[str, Any]) -> list[str]:
    """Where this PC's server answers, best first (its tailnet address, then the
    rest). A link made before there were several has only ``server``."""
    out: list[str] = []
    saved = cfg.get('servers')
    for value in [*(saved if isinstance(saved, list) else []), cfg.get('server')]:
        value = str(value or '').strip().rstrip('/')
        if value and value not in out:
            out.append(value)
    return out[:MAX_SERVERS]


def _servers_after_pairing(server: str, told: Any) -> list[str]:
    """The address that just worked plus the ones the server says it also answers
    at: its Tailscale address first, so the link stays inside the tailnet when it can."""
    extra: list[str] = []
    for value in (told if isinstance(told, list) else [])[:MAX_SERVERS]:
        if not isinstance(value, str) or '://' not in value:
            continue
        try:
            extra.append(normalize_server(value))
        except LinkError:
            continue   # not https and not on this PC's tailnet: never dialed
    on_tailnet = [u for u in extra if (urlparse(u).hostname or '').endswith('.ts.net')]
    out: list[str] = []
    for value in [*on_tailnet, server, *extra]:
        if value not in out:
            out.append(value)
    return out[:MAX_SERVERS]


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
# Handing tasks to the web account (the 'tasks' power), with this PC's token
# ---------------------------------------------------------------------------

async def remote(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call the web account's server as this PC. Raises LinkError."""
    import aiohttp
    cfg = load_config()
    if not cfg.get('token'):
        raise LinkError('unpaired', 'This PC is not linked to a web account.')
    last: LinkError | None = None
    for server in servers_of(cfg):
        try:
            url = normalize_server(server) + API_PREFIX + path
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http, http.request(
                    method, url, json=body, headers={'Authorization': f"Bearer {cfg['token']}"}) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                if resp.status >= 400:
                    detail = data.get('detail') if isinstance(data, dict) else None
                    raise LinkError('failed', str(detail or f'The server answered {resp.status}.'))
                return data if isinstance(data, dict) else {}
        except LinkError as exc:
            if exc.code not in ('insecure', 'bad_server'):
                raise
            last = exc      # this address can't be used right now: the next one may
        except Exception as exc:
            last = LinkError('unreachable', f"Couldn't reach {server}: {exc}")
    raise last or LinkError('unreachable', "This PC's link has no server address.")


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


async def link(server: str, code: str, *, name: str, folders: Any, powers: Any, client: str = 'cli') -> dict[str, Any]:
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
        'folders': shared, 'powers': chosen, 'version': VERSION, 'client': client,
    })
    return _save_link(server, out, account=str(info.get('account') or ''), name=name, folders=shared, powers=chosen)


def _save_link(server: str, out: dict[str, Any], *, account: str, name: str, folders: list[dict[str, Any]],
               powers: list[str]) -> dict[str, Any]:
    if not out.get('token'):
        raise LinkError('failed', "The server didn't send a token back.")
    cfg = {
        'server': server, 'servers': _servers_after_pairing(server, out.get('addresses')),
        'token': out['token'], 'device_id': str(out.get('device_id') or ''),
        'account': account, 'name': (name or default_name())[:80],
        'folders': folders, 'powers': powers, 'paused': False, 'paired_at': time.time(),
    }
    save_config(cfg)
    return public_config(cfg)


# ---------------------------------------------------------------------------
# Pairing without a code, over the tailnet: ask, wait for a yes on the web, pair
# ---------------------------------------------------------------------------

async def ask(server: str, *, name: str = '', client: str = 'cli') -> dict[str, Any]:
    """Ask the server to pair this PC with the account its Tailscale login is
    linked to. Returns {server, request_id, secret, account, expires_in}; the
    account's owner approves it on the web (Settings, Computers)."""
    server = normalize_server(server)
    out = await _post(server, '/pair-requests', {'name': (name or default_name())[:80],
                                                 'platform': platform_label(), 'client': client})
    if not out.get('request_id') or not out.get('secret'):
        raise LinkError('failed', "The server didn't take the request.")
    return {'server': server, 'request_id': str(out['request_id']), 'secret': str(out['secret']),
            'account': str(out.get('account') or ''), 'expires_in': int(out.get('expires_in') or 0)}


async def ask_status(asked: dict[str, Any]) -> dict[str, Any]:
    """{status: pending | approved | denied | expired | used, account}."""
    out = await _post(asked['server'], f"/pair-requests/{asked['request_id']}/status", {'secret': asked['secret']})
    return {'status': str(out.get('status') or 'pending'), 'account': str(out.get('account') or asked.get('account') or '')}


async def link_approved(asked: dict[str, Any], *, name: str, folders: Any, powers: Any,
                        client: str = 'cli') -> dict[str, Any]:
    """Finish an approved request: send what this PC shares, save the link."""
    from cognitrix.config import VERSION

    shared = check_folders(folders)
    chosen = clean_powers(powers)
    out = await _post(asked['server'], f"/pair-requests/{asked['request_id']}/pair", {
        'secret': asked['secret'], 'name': (name or default_name())[:80], 'platform': platform_label(),
        'folders': shared, 'powers': chosen, 'version': VERSION, 'client': client,
    })
    return _save_link(asked['server'], out, account=str(asked.get('account') or ''), name=name, folders=shared,
                      powers=chosen)


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

    from cognitrix.produced_files.sources import PC_FILE_BYTES, PC_NEW_FILES
    special = (undo.CHANGES, undo.UNDO, PC_NEW_FILES, PC_FILE_BYTES)
    power = APPS if name == SCREEN else FILES if name in special else tool_power(name)
    if power is None:
        return fail('not_allowed', f"{name} doesn't run on a paired PC.")
    if cfg.get('paused'):
        return fail('paused', 'Agents are paused on this PC.')
    if power not in clean_powers(cfg.get('powers')):
        return fail(f'no_{power}', f'{power} is switched off on this PC.')
    if power == APPS:
        return await _run_app_call(call_id, name, args, ctx, fail)
    if power == COMMANDS and _folders_for(cfg) and not _folders_for(cfg, for_commands=True):
        return fail('read_only', 'Every folder on this PC is shared read only, and a command could change files, so '
                    'commands are refused. Ask the person to share a folder as read & write to run commands.')
    folders = _folders_for(cfg, for_commands=power == COMMANDS)
    only = {_norm_path(p) for p in (ctx.get('only_folders') or []) if isinstance(p, str) and p.strip()}
    if only and folders:
        # A chat in a project uses only the project's folders on this PC.
        from cognitrix.common.folders import Folder
        folders = [Folder(f.path, f.access, f.name, i == 0)
                   for i, f in enumerate(f for f in folders if _norm_path(f.path) in only)]
        if not folders:
            return fail('outside_project', "This chat's project doesn't use any of the folders this call needs on "
                        'this PC. Ask the person to add the folder to the project.')
    if not folders:
        return fail('no_folders', "This PC hasn't shared any folders (or they were moved). Ask the person to add one "
                    'on the PC.')
    tool = None if name in special else ToolManager.get_by_name(name)
    if tool is None and name not in special:
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
        if name in (undo.CHANGES, undo.UNDO):
            return _run_undo_call(call_id, name, args, ctx, fail)
        if name in (PC_NEW_FILES, PC_FILE_BYTES):
            return await _run_files_call(call_id, name, args, fail)
        if name in undo.EDIT_TOOLS:
            _keep_for_undo(ctx, args)
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


async def _run_files_call(call_id: Any, name: str, args: dict[str, Any], fail) -> dict[str, Any]:
    """Files the chat made here (for the shelf under a reply), or one piece
    of a file an agent shared (cognitrix/produced_files)."""
    from cognitrix.produced_files import sources
    try:
        if name == sources.PC_NEW_FILES:
            out = await asyncio.to_thread(sources.pc_new_files, float(args.get('since') or 0))
        else:
            out = await asyncio.to_thread(sources.pc_file_bytes, str(args.get('path') or ''),
                                          int(args.get('offset') or 0), int(args.get('length') or 0))
    except Exception as exc:
        return fail('failed', str(exc))
    return {'type': 'result', 'id': call_id, 'ok': True, 'text': json.dumps(out)}


def _norm_path(path: str) -> str:
    return str(path or '').replace('\\', '/').rstrip('/').lower()


def _keep_for_undo(ctx: dict[str, Any], args: dict[str, Any]) -> None:
    """Before an agent's Write or Edit: keep the file as it is, so the chat can undo."""
    from cognitrix.common.folders import resolve_path
    raw = args.get('file_path') or args.get('path')
    if not raw:
        return
    try:
        undo.remember(str(ctx.get('session_id') or ctx.get('task_id') or ''), resolve_path(str(raw), write=True))
    except Exception:
        # The tool itself will say why the path is refused; a failed copy must not block the edit.
        logger.debug('Could not keep a copy for undo', exc_info=True)


def _run_undo_call(call_id: Any, name: str, args: dict[str, Any], ctx: dict[str, Any], fail) -> dict[str, Any]:
    """The chat's changed files on this PC, or putting some of them back."""
    from cognitrix.common.folders import resolve_path
    chat = str(ctx.get('session_id') or ctx.get('task_id') or '')
    if not chat:
        return fail('failed', 'Which chat? No chat was given.')
    if name == undo.CHANGES:
        return {'type': 'result', 'id': call_id, 'ok': True, 'text': json.dumps({'changes': undo.changes(chat)})}
    paths = args.get('paths')
    paths = None if paths is None else [str(p) for p in paths if p]
    results = undo.restore(chat, paths, lambda p: resolve_path(p, write=True))
    _log({'at': time.time(), 'tool': 'Undo', 'ok': all(r['ok'] for r in results),
          'paths': [r['path'] for r in results]})
    return {'type': 'result', 'id': call_id, 'ok': True, 'text': json.dumps({'restored': results})}


def _screen_image(max_width: int = 1280) -> str:
    """This PC's whole screen as a JPEG data URI, scaled down for the web."""
    import base64
    import io

    from cognitrix.tools.misc import _grab_screen

    img = _grab_screen().convert('RGB')
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=70)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


async def _run_app_call(call_id: Any, name: str, args: dict[str, Any], ctx: dict[str, Any], fail) -> dict[str, Any]:
    """The window tools, always on this PC's own screen (computer='person'),
    or the web's view of the screen. Screenshots go back with the answer."""
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.misc import coords_normalized, end_image_collection, start_image_collection
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

    started = time.time()
    if name == SCREEN:
        try:
            image = await asyncio.to_thread(_screen_image)
        except Exception as exc:
            return fail('failed', f"Couldn't capture the screen: {exc}")
        return {'type': 'result', 'id': call_id, 'ok': True, 'text': '', 'images': [image]}
    tool = ToolManager.get_by_name(name)
    if tool is None:
        return fail('not_allowed', f'{name} is not available on this PC.')
    args = {**args, 'computer': 'person'}
    context = ToolExecutionContext(
        user_id='paired', session_id=str(ctx.get('session_id') or '') or None,
        agent_id=str(ctx.get('agent_id') or '') or None, device_host=True)
    token = set_execution_context(context)
    img_token, images = start_image_collection()
    norm_token = coords_normalized.set(bool(ctx.get('coords_normalized')))
    try:
        validated = tool.validate_parameters(dict(args)) if hasattr(tool, 'validate_parameters') else dict(args)
        if 'parent' in set(getattr(tool, 'internal_params', None) or []):
            # Who is acting, so one agent can't act on another agent's look at a window.
            from types import SimpleNamespace
            who = str(ctx.get('agent_id') or ctx.get('session_id') or '') or None
            validated['parent'] = SimpleNamespace(id=who, name=None) if who else None
        text = _text(await tool.run(**validated))
    except Exception as exc:
        logger.warning('A window call from the server failed here', exc_info=True)
        return fail('failed', f'{name} failed on the PC: {exc}')
    finally:
        coords_normalized.reset(norm_token)
        end_image_collection(img_token)
        reset_execution_context(token)
    _log({'at': started, 'tool': name, 'ok': True, 'seconds': round(time.time() - started, 1),
          'target': args.get('target'), 'window': args.get('window_id')})
    return {'type': 'result', 'id': call_id, 'ok': True, 'text': text, 'images': images[:4]}


# ---------------------------------------------------------------------------
# The connection
# ---------------------------------------------------------------------------

class Connector:
    """Keeps this PC connected to its server, reconnecting with backoff."""

    def __init__(self) -> None:
        self.status = 'stopped'  # stopped | connecting | connected | offline | unpaired
        self.server = ''  # the address the connection is on (the link may have several)
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
        return {'status': self.status, 'error': self.last_error, 'connected_since': self.connected_since,
                'connected_to': self.server if self.status == 'connected' else ''}

    # -- loop ------------------------------------------------------------------
    async def run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            cfg = load_config()
            if not cfg.get('token'):
                self.status = 'unpaired'
                return
            self.status = 'connecting'
            # The server's addresses, its tailnet one first. One that can't be reached
            # hands over to the next; after a connection drops, start from the top again.
            for server in servers_of(cfg):
                was_up = False
                try:
                    await self._session(cfg, server)
                    backoff = 1.0
                    was_up = True
                except asyncio.CancelledError:
                    raise
                except LinkError as exc:
                    self.last_error = exc.message
                    if exc.code in ('revoked', 'unpaired'):
                        forget_config()
                        self.status = 'unpaired'
                        return
                    was_up = self.connected_since is not None
                except Exception as exc:
                    self.last_error = str(exc) or exc.__class__.__name__
                    was_up = self.connected_since is not None
                    logger.info('Paired PC connection to %s dropped: %s', server, self.last_error)
                finally:
                    self._ws = None
                    self.connected_since = None
                if was_up or self._stop.is_set():
                    break
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

    async def _session(self, cfg: dict[str, Any], server: str = '') -> None:
        import aiohttp

        from cognitrix.config import VERSION

        server = normalize_server(server or cfg['server'])
        self.server = server
        ws_url = server.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1) + API_PREFIX + '/connect'
        # An address that doesn't answer (the tailnet one, away from the tailnet) gives way quickly.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300, sock_connect=15)) as http:
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
