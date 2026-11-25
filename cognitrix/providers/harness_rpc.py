"""JSON-RPC over a coding agent CLI's stdin/stdout, for 'ask' mode.

In 'ask' mode Cognitrix runs a CLI in its protocol mode instead of its one-shot
JSON output mode, so the CLI can stop and ask before a command or a change, and
the person answers on an approval card:

* Codex: ``codex app-server`` (Codex's own protocol, JSON-RPC without the
  ``"jsonrpc"`` field). Approvals arrive as ``item/.../requestApproval``.
* Gemini CLI, GitHub Copilot CLI, OpenCode and Antigravity CLI: the Agent
  Client Protocol (https://agentclientprotocol.com). Approvals arrive as
  ``session/request_permission``.

Both are newline-delimited JSON-RPC on stdio. ``Peer`` is the transport; the
two drivers turn one chat turn into the same ``harness.Event`` values the
one-shot path yields, and send every approval through ``approve`` (the
agent's ``approve_cli_action``), which shows the card.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger('cognitrix.log')

Approver = Callable[[str, dict[str, Any]], Awaitable[tuple[bool, str]]]
Emit = Callable[[Any], None]

ACP_KEYS = frozenset({'gemini-cli', 'copilot-cli', 'opencode', 'antigravity-cli'})
RPC_KEYS = ACP_KEYS | {'codex'}


class RpcError(RuntimeError):
    def __init__(self, message: str, code: int = -32603, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class Closed(RuntimeError):
    """The CLI ended (or stopped talking) before answering."""


class Peer:
    """Newline-delimited JSON-RPC with a CLI started by ``harness._spawn``.

    ``lines`` is the queue ``_spawn`` fills with stdout lines (None at the
    end). Requests the CLI sends us go to ``on_request`` (its return value is
    the result; raising RpcError answers with an error), notifications to
    ``on_notification``.
    """

    def __init__(self, proc: subprocess.Popen, lines: asyncio.Queue, *, jsonrpc: bool = True,
                 on_request: Callable[[str, Any], Awaitable[Any]] | None = None,
                 on_notification: Callable[[str, Any], None] | None = None):
        self.proc = proc
        self.lines = lines
        self.jsonrpc = jsonrpc
        self.on_request = on_request
        self.on_notification = on_notification
        self._next_id = 0
        self._waiting: dict[Any, asyncio.Future] = {}
        self._write_lock = threading.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.closed = False

    # --- sending -----------------------------------------------------------

    def _send(self, message: dict[str, Any]) -> None:
        if self.jsonrpc:
            message = {'jsonrpc': '2.0', **message}
        data = (json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8')
        with self._write_lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise Closed(f'could not write to the CLI: {exc}') from exc

    async def request(self, method: str, params: Any = None, timeout: float | None = None) -> Any:
        if self.closed:
            raise Closed('the CLI has ended')
        self._next_id += 1
        rid = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = future
        try:
            self._send({'id': rid, 'method': method, **({'params': params} if params is not None else {})})
            return await asyncio.wait_for(future, timeout) if timeout else await future
        finally:
            self._waiting.pop(rid, None)

    def notify(self, method: str, params: Any = None) -> None:
        self._send({'method': method, **({'params': params} if params is not None else {})})

    # --- receiving ---------------------------------------------------------

    async def run(self) -> None:
        """Read until the CLI's stdout ends. Run as a task."""
        try:
            while True:
                line = await self.lines.get()
                if line is None:
                    break
                line = line.strip()
                if not line.startswith('{'):
                    continue  # stray log output
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        finally:
            self.closed = True
            for future in list(self._waiting.values()):
                if not future.done():
                    future.set_exception(Closed('the CLI ended before answering'))
            for task in list(self._tasks):
                task.cancel()

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get('method')
        rid = message.get('id')
        if method is None and rid is not None:
            future = self._waiting.get(rid)
            if future is not None and not future.done():
                if 'error' in message and message['error'] is not None:
                    err = message['error'] if isinstance(message['error'], dict) else {'message': str(message['error'])}
                    future.set_exception(RpcError(str(err.get('message') or 'error'), int(err.get('code') or -32603),
                                                  err.get('data')))
                else:
                    future.set_result(message.get('result'))
            return
        if not isinstance(method, str):
            return
        params = message.get('params')
        if rid is None:
            if self.on_notification is not None:
                try:
                    self.on_notification(method, params)
                except Exception:  # noqa: BLE001 - one odd update must not end the turn
                    logger.exception('Could not read %s from a coding agent', method)
            return
        task = asyncio.get_running_loop().create_task(self._answer(rid, method, params))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _answer(self, rid: Any, method: str, params: Any) -> None:
        try:
            if self.on_request is None:
                raise RpcError(f'Method not found: {method}', -32601)
            result = await self.on_request(method, params)
            self._send({'id': rid, 'result': result})
        except asyncio.CancelledError:
            raise
        except RpcError as exc:
            self._safe_send({'id': rid, 'error': {'code': exc.code, 'message': str(exc)}})
        except Exception as exc:  # noqa: BLE001 - answered as an error
            logger.exception('Could not answer %s from a coding agent', method)
            self._safe_send({'id': rid, 'error': {'code': -32603, 'message': str(exc)}})

    def _safe_send(self, message: dict[str, Any]) -> None:
        try:
            self._send(message)
        except Closed:
            pass


# --- shared -----------------------------------------------------------------

def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit] + '…'


def _first(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, '', [], {}):
            return value
    return None


def _command_text(value: Any) -> str:
    if isinstance(value, list):
        return ' '.join(str(v) for v in value)
    return str(value or '')


# --- Agent Client Protocol (Gemini CLI, Copilot CLI, OpenCode, Antigravity) ---

ACP_PROTOCOL_VERSION = 1
_ACP_EDIT_KINDS = frozenset({'edit', 'delete', 'move'})
_ACP_ALLOW = ('allow_once', 'allow_always')
_ACP_REJECT = ('reject_once', 'reject_always')


def acp_argv(key: str, program: str, *, model: str, extra_dirs: list[str] | tuple[str, ...],
             effort: str = '') -> list[str]:
    """The command that starts the CLI as an ACP agent on stdio.

    Model and extra folders go on the command line where the CLI takes them
    there; the rest is set on the session (see AcpTurn)."""
    if key == 'gemini-cli':
        argv = [program, '--acp']
        if model:
            argv += ['-m', model]
        if extra_dirs:
            argv += ['--include-directories', ','.join(extra_dirs)]
        return argv
    if key == 'copilot-cli':
        argv = [program, '--acp', '--stdio', '--no-auto-update']
        if model:
            argv += [f'--model={model}']
        if effort in ('low', 'medium', 'high', 'xhigh', 'max'):
            argv += [f'--reasoning-effort={effort}']
        return argv + [f'--add-dir={d}' for d in extra_dirs]
    if key == 'opencode':
        return [program, 'acp']
    if key == 'antigravity-cli':
        # HTTP MCP servers given on the session need a process per session.
        argv = [program, 'acp', '--process-per-session']
        for d in extra_dirs:
            argv += ['--add-dir', d]
        return argv
    raise ValueError(f'{key} has no ACP mode')


def acp_permission_call(params: Any, lent: frozenset[str] = frozenset()) -> tuple[str, dict[str, Any], bool]:
    """(tool name, input) to ask the person about, and whether it is one of
    Cognitrix's own lent tools (checked by Cognitrix when it runs, so the
    CLI's own prompt for it is answered yes without a second card)."""
    call = params.get('toolCall') if isinstance(params, dict) else None
    call = call if isinstance(call, dict) else {}
    kind = str(call.get('kind') or '').lower()
    raw = call.get('rawInput') if isinstance(call.get('rawInput'), dict) else {}
    title = str(call.get('title') or call.get('name') or 'a tool')
    name = str(call.get('name') or '')
    if kind == 'execute' or _first(raw, 'command', 'cmd'):
        command = _command_text(_first(raw, 'command', 'cmd', 'commandLine')) or title
        return 'Bash', {'command': command}, False
    if kind in _ACP_EDIT_KINDS:
        locations = call.get('locations') if isinstance(call.get('locations'), list) else []
        path = _first(raw, 'file_path', 'filePath', 'path', 'absolute_path', 'target_file')
        if not path and locations and isinstance(locations[0], dict):
            path = locations[0].get('path')
        if path:
            return ('Write' if kind != 'delete' else 'Delete'), {'file_path': str(path)}, False
        return title, {k: v for k, v in raw.items() if isinstance(v, (str, int, float, bool))}, False
    # One of the agent's own Cognitrix tools, reached through the lent MCP server.
    words = {w for w in (name, title.split(' ')[0] if title else '') if w}
    words |= {w.split('__')[-1] for w in words} | {w.split('_', 1)[-1] for w in words if w.startswith('cognitrix')}
    if lent and kind not in ('execute', *_ACP_EDIT_KINDS) and words & lent:
        return title, {}, True
    return name or title, {k: v for k, v in raw.items() if isinstance(v, (str, int, float, bool))}, False


def acp_answer(options: Any, allowed: bool | None) -> dict[str, Any]:
    """The reply to session/request_permission: allow or reject once.

    Never "always": what holds for the rest of a chat is decided by Cognitrix's
    own cards, so the CLI must keep asking."""
    options = [o for o in (options or []) if isinstance(o, dict) and o.get('optionId')]
    if allowed is not None:
        for kinds in ((_ACP_ALLOW[0],) if allowed else (_ACP_REJECT[0],), _ACP_ALLOW if allowed else _ACP_REJECT):
            for option in options:
                if option.get('kind') in kinds:
                    return {'outcome': {'outcome': 'selected', 'optionId': option['optionId']}}
    return {'outcome': {'outcome': 'cancelled'}}


class AcpTurn:
    """One prompt turn with a CLI over the Agent Client Protocol."""

    def __init__(self, *, key: str, label: str, emit: Emit, approve: Approver | None,
                 lent: frozenset[str] = frozenset(), event_type: Any = None):
        self.key = key
        self.label = label
        self.emit = emit
        self.approve = approve
        self.lent = lent
        self.Event = event_type
        self.session_id = ''
        self.replaying = False
        self.finished = False
        self.peer: Peer | None = None

    # The CLI asks us things: permissions, and (unadvertised) file/terminal access.
    async def on_request(self, method: str, params: Any) -> Any:
        if method == 'session/request_permission':
            name, shown, own = acp_permission_call(params, self.lent)
            if own:
                return acp_answer(params.get('options'), True)
            if self.approve is None:
                return acp_answer(params.get('options'), False)
            allowed, _why = await self.approve(name, shown)
            return acp_answer(params.get('options'), bool(allowed))
        raise RpcError(f'Method not found: {method}', -32601)

    def on_notification(self, method: str, params: Any) -> None:
        if method != 'session/update' or not isinstance(params, dict) or self.replaying:
            return
        update = params.get('update')
        if not isinstance(update, dict):
            return
        kind = update.get('sessionUpdate')
        content = update.get('content')
        text = content.get('text') if isinstance(content, dict) and content.get('type') == 'text' else None
        if kind == 'agent_message_chunk' and text:
            self.emit([self.Event(text=text)])
        elif kind == 'agent_thought_chunk' and text:
            self.emit([self.Event(activity=text)])
        elif kind == 'tool_call':
            title = str(update.get('title') or update.get('name') or 'tool')
            self.emit([self.Event(activity=f'› {_short(title)}\n')])
        elif kind == 'tool_call_update' and update.get('status') == 'failed':
            title = str(update.get('title') or 'a tool')
            self.emit([self.Event(activity=f'› {_short(title)} (failed)\n')])

    async def run(self, peer: Peer, *, cwd: str, extra_dirs: list[str], model: str, effort: str = '',
                  resume: str | None, text: str, full_text: str, mcp_servers: list[dict[str, Any]]) -> None:
        """initialize, open or resume the session, send the message, wait for the turn's end."""
        self.peer = peer
        try:
            await self._run(peer, cwd=cwd, extra_dirs=extra_dirs, model=model, resume=resume,
                            text=text, full_text=full_text, mcp_servers=mcp_servers)
        finally:
            self.finished = True

    async def _run(self, peer: Peer, *, cwd: str, extra_dirs: list[str], model: str, resume: str | None,
                   text: str, full_text: str, mcp_servers: list[dict[str, Any]]) -> None:
        init = await peer.request('initialize', {
            'protocolVersion': ACP_PROTOCOL_VERSION,
            'clientCapabilities': {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False},
            'clientInfo': {'name': 'cognitrix', 'title': 'Cognitrix', 'version': '1'},
        }, timeout=120)
        caps = (init or {}).get('agentCapabilities') or {}
        session_caps = caps.get('sessionCapabilities') or {}
        mcp_http = bool((caps.get('mcpCapabilities') or {}).get('http'))
        servers = [s for s in mcp_servers if s.get('type') != 'http' or mcp_http]
        extra = {'additionalDirectories': list(extra_dirs)} if extra_dirs and session_caps.get('additionalDirectories') is not None else {}
        opened = None
        if resume:
            base = {'sessionId': resume, 'cwd': cwd, 'mcpServers': servers, **extra}
            try:
                if session_caps.get('resume') is not None:
                    opened = await peer.request('session/resume', base, timeout=120)
                elif caps.get('loadSession'):
                    # Load replays the whole chat as updates: already shown, so muted.
                    self.replaying = True
                    try:
                        opened = await peer.request('session/load', base, timeout=300)
                    finally:
                        self.replaying = False
                if opened is not None:
                    self.session_id = resume
            except RpcError as exc:
                logger.info('%s could not reopen its session (%s); starting a new one', self.label, exc)
                opened = None
        if not self.session_id:
            opened = await peer.request('session/new', {'cwd': cwd, 'mcpServers': servers, **extra}, timeout=120)
            self.session_id = str((opened or {}).get('sessionId') or '')
            if not self.session_id:
                raise RpcError(f'{self.label} did not open a session')
            text = full_text
        self.emit([self.Event(session=self.session_id)])
        if model:
            await self._set_model(peer, opened or {}, model)
        result = await peer.request('session/prompt', {
            'sessionId': self.session_id, 'prompt': [{'type': 'text', 'text': text}],
        })
        result = result or {}
        usage = result.get('usage') if isinstance(result.get('usage'), dict) else None
        if usage:
            self.emit([self.Event(usage={
                'prompt_tokens': int(usage.get('inputTokens') or 0) + int(usage.get('cachedReadTokens') or 0),
                'completion_tokens': int(usage.get('outputTokens') or 0),
                'cached_tokens': int(usage.get('cachedReadTokens') or 0),
            })])
        stop = str(result.get('stopReason') or 'end_turn')
        if stop == 'refusal':
            self.emit([self.Event(error=f'{self.label} refused to continue.')])
        elif stop == 'max_tokens':
            self.emit([self.Event(text='\n\n(It stopped: the reply hit its length limit.)')])

    async def _set_model(self, peer: Peer, opened: dict[str, Any], model: str) -> None:
        """Pick the model on the session, when the CLI didn't take it as a flag."""
        if self.key in ('gemini-cli', 'copilot-cli'):
            return
        try:
            for option in opened.get('configOptions') or []:
                if isinstance(option, dict) and (option.get('id') == 'model' or option.get('category') == 'model'):
                    await peer.request('session/set_config_option', {
                        'sessionId': self.session_id, 'configId': option.get('id'), 'value': model}, timeout=60)
                    return
            if isinstance(opened.get('models'), dict):
                await peer.request('session/set_model', {'sessionId': self.session_id, 'modelId': model}, timeout=60)
        except RpcError as exc:
            self.emit([self.Event(activity=f'Could not pick the model {model!r} ({exc}); using its default.\n')])

    async def cancel(self) -> None:
        """Stop a turn that is still running (the chat was stopped)."""
        if self.peer is not None and self.session_id and not self.finished and not self.peer.closed:
            try:
                self.peer.notify('session/cancel', {'sessionId': self.session_id})
            except Closed:
                pass



# --- Codex app-server ---------------------------------------------------------

CODEX_EFFORTS = frozenset({'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'})
# Codex asks (on-request), the person answers (not Codex's own auto reviewer).
_CODEX_ASKING = {'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}


class CodexTurn:
    """One turn with ``codex app-server``: a thread per chat, approvals as cards."""

    def __init__(self, *, label: str, emit: Emit, approve: Approver | None, event_type: Any = None):
        self.label = label
        self.emit = emit
        self.approve = approve
        self.Event = event_type
        self.thread_id = ''
        self.turn_id = ''
        self.finished = False
        self.peer: Peer | None = None
        self._done: asyncio.Future | None = None
        self._files: dict[str, list[str]] = {}  # file change item id -> paths
        self._usage: dict[str, int] = {}

    async def on_request(self, method: str, params: Any) -> Any:
        params = params if isinstance(params, dict) else {}
        if method == 'item/commandExecution/requestApproval':
            network = params.get('networkApprovalContext')
            if isinstance(network, dict) and network:
                name, shown = 'Network access', {k: v for k, v in network.items() if isinstance(v, (str, int))}
            else:
                command = _command_text(params.get('command')) or 'a command'
                name, shown = 'Bash', {'command': command}
            return {'decision': 'accept' if await self._ask(name, shown) else 'decline'}
        if method == 'item/fileChange/requestApproval':
            paths = list(self._files.get(str(params.get('itemId') or ''), []))
            if params.get('grantRoot'):
                paths = [str(params['grantRoot'])]
            if not paths:
                allowed = await self._ask(f"{self.label} file change", {'reason': str(params.get('reason') or '')})
            else:
                allowed = True
                for path in paths:
                    if not await self._ask('Write', {'file_path': path}):
                        allowed = False
                        break
            return {'decision': 'accept' if allowed else 'decline'}
        if method == 'item/permissions/requestApproval':
            # More sandbox access for the rest of the turn: not granted; Codex
            # then asks for each command or change, which gets its own card.
            return {'permissions': {}, 'scope': 'turn'}
        if method == 'mcpServer/elicitation/request':
            return {'action': 'decline', 'content': None}
        raise RpcError(f'Method not found: {method}', -32601)

    async def _ask(self, name: str, shown: dict[str, Any]) -> bool:
        if self.approve is None:
            return False
        allowed, _why = await self.approve(name, shown)
        return bool(allowed)

    def on_notification(self, method: str, params: Any) -> None:
        if not isinstance(params, dict):
            return
        if method in ('item/started', 'item/completed'):
            item = params.get('item') if isinstance(params.get('item'), dict) else {}
            kind = item.get('type')
            if kind == 'fileChange':
                paths = [str(c.get('path')) for c in item.get('changes') or [] if isinstance(c, dict) and c.get('path')]
                self._files[str(item.get('id') or '')] = paths
                if method == 'item/completed' and item.get('status') == 'completed':
                    self.emit([self.Event(activity=f"› edited {', '.join(paths) or 'files'}\n")])
            elif method == 'item/started' and kind == 'commandExecution':
                self.emit([self.Event(activity=f"› {_short(_command_text(item.get('command')))}\n")])
            elif method == 'item/completed' and kind == 'agentMessage' and item.get('text'):
                self.emit([self.Event(text=str(item['text']).rstrip() + '\n\n')])
            elif method == 'item/completed' and kind == 'reasoning':
                summary = item.get('summary')
                text = '\n'.join(str(x) for x in summary) if isinstance(summary, list) else str(summary or '')
                if text.strip():
                    self.emit([self.Event(activity=text.strip() + '\n')])
            elif method == 'item/completed' and kind == 'mcpToolCall':
                self.emit([self.Event(activity=f"› {item.get('server')}.{item.get('tool')}\n")])
            elif method == 'item/completed' and kind == 'webSearch':
                self.emit([self.Event(activity=f"› web search: {_short(item.get('query') or '')}\n")])
        elif method == 'thread/tokenUsage/updated':
            last = ((params.get('tokenUsage') or {}).get('last') or {}) if isinstance(params.get('tokenUsage'), dict) else {}
            for ours, theirs in (('prompt_tokens', 'inputTokens'), ('completion_tokens', 'outputTokens'),
                                 ('cached_tokens', 'cachedInputTokens')):
                self._usage[ours] = self._usage.get(ours, 0) + int(last.get(theirs) or 0)
        elif method == 'error':
            err = params.get('error') if isinstance(params.get('error'), dict) else {}
            if not params.get('willRetry') and err.get('message'):
                self.emit([self.Event(error=_short(err['message'], 400))])
        elif method == 'turn/completed':
            turn = params.get('turn') if isinstance(params.get('turn'), dict) else {}
            if turn.get('status') == 'failed':
                err = turn.get('error') if isinstance(turn.get('error'), dict) else {}
                self.emit([self.Event(error=_short(err.get('message') or 'Codex failed', 400))])
            if self._done is not None and not self._done.done():
                self._done.set_result(turn.get('status'))

    async def run(self, peer: Peer, *, cwd: str, extra_dirs: list[str], model: str, effort: str = '',
                  resume: str | None, text: str, full_text: str, mcp_servers: list[dict[str, Any]]) -> None:
        self.peer = peer
        self._done = asyncio.get_running_loop().create_future()
        try:
            await peer.request('initialize', {
                'clientInfo': {'name': 'cognitrix', 'title': 'Cognitrix', 'version': '1'}}, timeout=120)
            peer.notify('initialized', {})
            thread_opts = {'cwd': cwd, 'sandbox': 'workspace-write', **_CODEX_ASKING,
                           **({'model': model} if model else {})}
            if resume:
                try:
                    opened = await peer.request('thread/resume', {'threadId': resume, **thread_opts}, timeout=120)
                    self.thread_id = str(((opened or {}).get('thread') or {}).get('id') or resume)
                except RpcError as exc:
                    logger.info('Codex could not resume thread %s (%s); starting a new one', resume, exc)
            if not self.thread_id:
                opened = await peer.request('thread/start', thread_opts, timeout=120)
                self.thread_id = str(((opened or {}).get('thread') or {}).get('id') or '')
                if not self.thread_id:
                    raise RpcError('Codex did not start a thread')
                text = full_text
            self.emit([self.Event(session=self.thread_id)])
            started = await peer.request('turn/start', {
                'threadId': self.thread_id,
                'input': [{'type': 'text', 'text': text}],
                **_CODEX_ASKING,
                'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': [cwd, *extra_dirs],
                                  'networkAccess': False},
                **({'effort': effort} if effort in CODEX_EFFORTS else {}),
            }, timeout=120)
            self.turn_id = str(((started or {}).get('turn') or {}).get('id') or '')
            await self._done
            if self._usage:
                self.emit([self.Event(usage=dict(self._usage))])
        finally:
            self.finished = True

    async def cancel(self) -> None:
        if self.peer is not None and self.turn_id and not self.finished and not self.peer.closed:
            try:
                await self.peer.request('turn/interrupt', {'threadId': self.thread_id, 'turnId': self.turn_id},
                                        timeout=5)
            except (RpcError, Closed, TimeoutError):
                pass


def driver_for(key: str, *, label: str, emit: Emit, approve: Approver | None,
               lent: frozenset[str] = frozenset(), event_type: Any = None):
    """The protocol driver for one 'ask' turn with this CLI."""
    if key == 'codex':
        return CodexTurn(label=label, emit=emit, approve=approve, event_type=event_type)
    if key in ACP_KEYS:
        return AcpTurn(key=key, label=label, emit=emit, approve=approve, lent=lent, event_type=event_type)
    raise ValueError(f'{key} has no protocol mode')
