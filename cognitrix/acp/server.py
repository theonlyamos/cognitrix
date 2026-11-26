"""Agent Client Protocol server: chat with Cognitrix agents from an editor.

The editor starts ``cognitrix acp`` and talks JSON-RPC over stdin/stdout
(https://agentclientprotocol.com). Each editor session is a Cognitrix chat:

* the agent is picked with ``--agent`` and can be switched from the editor's
  mode menu (every saved agent is a mode);
* the chat's working folder is the editor's project folder (read & write);
* text streams back as message chunks, every tool call shows in the editor
  with its result, and anything that would ask you in the app asks you in the
  editor's own permission prompt instead;
* chats are saved like any other, so the editor can reopen them (load_session).

stdout belongs to the protocol. Everything else that would print there (status
lines, tools, child processes) is sent to stderr instead, see ``serve``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, RequestError, run_agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    ContentToolCallContent,
    CurrentModeUpdate,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    SessionMode,
    SessionModeState,
    SetSessionModeResponse,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UserMessageChunk,
)

logger = logging.getLogger('cognitrix.log')

_KINDS = {
    'read': 'read', 'glob': 'search', 'grep': 'search', 'list_files': 'search',
    'write': 'edit', 'edit': 'edit',
    'bash': 'execute', 'run_tests': 'execute', 'coding_agent': 'execute', 'run_code': 'execute',
    'webfetch': 'fetch', 'web_fetch': 'fetch', 'http_request': 'fetch',
    'search': 'search', 'tavily_search': 'search', 'find_tools': 'search', 'search_knowledge': 'search',
}
_PATH_KEYS = ('file_path', 'path', 'filepath', 'filename', 'target_file')


def _snake(name: str) -> str:
    return str(name or '').strip().lower().replace(' ', '_')


def tool_kind(name: str) -> str:
    return _KINDS.get(_snake(name), 'other')


def tool_title(name: str, params: Any) -> str:
    """A short line for the editor's tool card: the tool and its main argument."""
    label = str(name or 'Tool').replace('_', ' ').strip().title()
    if isinstance(params, dict):
        for key in (*_PATH_KEYS, 'command', 'pattern', 'url', 'query'):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip().splitlines()[0]
                return f"{label}: {text[:80]}"
    return label


def _locations(params: Any, cwd: str) -> list[ToolCallLocation] | None:
    if not isinstance(params, dict):
        return None
    for key in _PATH_KEYS:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            path = value.strip()
            if not os.path.isabs(path):
                path = os.path.join(cwd, path)
            return [ToolCallLocation(path=os.path.normpath(path))]
    return None


def prompt_text(blocks: list[Any]) -> str:
    """The editor's prompt (text, @-mentioned files, pasted context) as one message."""
    parts: list[str] = []
    for block in blocks or []:
        kind = getattr(block, 'type', None)
        if kind == 'text':
            parts.append(block.text)
        elif kind == 'resource_link':
            parts.append(f"[File: {_uri_path(block.uri)}]")
        elif kind == 'resource':
            res = block.resource
            text = getattr(res, 'text', None)
            where = _uri_path(getattr(res, 'uri', ''))
            if text is not None:
                parts.append(f"[Contents of {where}]\n```\n{text}\n```")
            else:
                parts.append(f"[Attached file: {where}]")
        elif kind in ('image', 'audio'):
            parts.append(f"[An {kind} was attached, but this agent can't read it from the editor yet.]")
    return '\n\n'.join(p for p in parts if p)


def _uri_path(uri: str) -> str:
    uri = str(uri or '')
    if uri.startswith('file://'):
        from urllib.parse import unquote, urlparse

        path = unquote(urlparse(uri).path)
        # file:///C:/x on Windows
        if len(path) > 2 and path[0] == '/' and path[2] == ':':
            path = path[1:]
        return path
    return uri


def _preview(value: Any, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '\n[...]'


class _Chat:
    """One editor session: its Cognitrix chat, agent and running prompt."""

    def __init__(self, session: Any, agent: Any, cwd: str):
        self.session = session
        self.agent = agent
        self.cwd = cwd
        self.task: asyncio.Task | None = None
        self.started: set[str] = set()
        # "Allow for this session" answers, for this editor session only
        # (the approval gate's own cache is per agent, shared by sessions).
        self.allowed: set[str] = set()


class CognitrixAgent:
    """The ACP ``Agent`` side, backed by saved Cognitrix agents and chats."""

    def __init__(self, agent_name: str = '', user_id: str | None = None):
        self.agent_name = agent_name
        self.user_id = user_id
        self.conn: Any = None
        self.chats: dict[str, _Chat] = {}
        self._ready = False

    def on_connect(self, conn: Any) -> None:
        self.conn = conn

    # -- setup -------------------------------------------------------------

    async def _setup(self) -> None:
        if self._ready:
            return
        from cognitrix.cli.core import run_configuration

        await run_configuration()
        self._ready = True

    async def _agents(self) -> list[Any]:
        from cognitrix.agents import Agent

        agents = await Agent.find({'is_sub_agent': False})
        agents = [a for a in agents or [] if getattr(a, 'name', None)]
        if self.user_id:
            agents = [a for a in agents if not getattr(a, 'owner_id', None) or str(a.owner_id) == self.user_id]
        return sorted(agents, key=lambda a: str(a.name).lower())

    async def _pick_agent(self, agent_id: str | None = None) -> Any:
        agents = await self._agents()
        if not agents:
            raise RequestError.internal_error({
                'message': 'No agents yet. Open Cognitrix and create one, or run `cognitrix` once.'})
        if agent_id:
            for a in agents:
                if str(a.id) == agent_id:
                    return a
        if self.agent_name:
            for a in agents:
                if str(a.name).lower() == self.agent_name.lower():
                    return a
        if self.user_id:
            # The person's main assistant, the same one the app marks as main.
            from cognitrix.assistant.setup import main_assistant

            main = await main_assistant(self.user_id)
            if main is not None:
                for a in agents:
                    if str(a.id) == str(main.id):
                        return a
        for a in agents:
            if str(a.name).lower() == 'assistant':
                return a
        return agents[0]

    async def _modes(self, current: Any) -> SessionModeState:
        modes = [
            SessionMode(id=str(a.id), name=str(a.name), description=(getattr(a, 'description', '') or None))
            for a in await self._agents()
        ]
        return SessionModeState(current_mode_id=str(current.id), available_modes=modes)

    # -- protocol methods --------------------------------------------------

    async def initialize(self, protocol_version: int, client_capabilities: Any = None,
                         client_info: Any = None, **kwargs: Any) -> InitializeResponse:
        from cognitrix.config import VERSION

        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(image=False, audio=False, embedded_context=True),
            ),
            agent_info=Implementation(name='cognitrix', title='Cognitrix', version=str(VERSION)),
            auth_methods=[],
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        return None

    async def new_session(self, cwd: str, mcp_servers: Any = None, **kwargs: Any) -> NewSessionResponse:
        await self._setup()
        from cognitrix.sessions.base import Session

        agent = await self._pick_agent()
        session = Session(agent_id=str(agent.id), user_id=self.user_id, title=f"Editor: {Path(cwd).name or cwd}")
        session.folders = _folders(cwd)
        if self.user_id:
            # A chat in the person's account, like one started in the app:
            # the editor's folder first, then the agent's own folders.
            from cognitrix.cli.app_turn import terminal_folders
            from cognitrix.session_ownership import claim_new

            session.folders = terminal_folders(agent, cwd)
            await session.save()
            await claim_new(str(session.id), self.user_id, str(agent.id))
        else:
            await session.save()
        chat = _Chat(session, agent, cwd)
        self.chats[str(session.id)] = chat
        return NewSessionResponse(session_id=str(session.id), modes=await self._modes(agent))

    async def load_session(self, cwd: str, session_id: str, mcp_servers: Any = None,
                           **kwargs: Any) -> LoadSessionResponse | None:
        await self._setup()
        from cognitrix.sessions.base import Session

        session = await Session.find_one({'id': session_id})
        if session is None:
            raise RequestError.resource_not_found(session_id)
        if self.user_id and session.user_id and str(session.user_id) != self.user_id:
            raise RequestError.resource_not_found(session_id)
        agent = await self._pick_agent(str(session.agent_id or ''))
        session.folders = _folders(cwd)
        chat = _Chat(session, agent, cwd)
        self.chats[session_id] = chat
        await self._replay(session_id, session.chat or [])
        return LoadSessionResponse(modes=await self._modes(agent))

    async def set_session_mode(self, session_id: str, mode_id: str, **kwargs: Any) -> SetSessionModeResponse:
        chat = self._chat(session_id)
        agent = await self._pick_agent(mode_id)
        if str(agent.id) != mode_id:
            raise RequestError.invalid_params({'message': f'No agent with id {mode_id}'})
        chat.agent = agent
        chat.session.agent_id = str(agent.id)
        await self.conn.session_update(session_id=session_id, update=CurrentModeUpdate(current_mode_id=mode_id))
        return SetSessionModeResponse()

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        chat = self._chat(session_id)
        if chat.task is not None and not chat.task.done():
            raise RequestError.invalid_request({'message': 'A prompt is already running in this session.'})
        text = prompt_text(prompt)
        if not text.strip():
            return PromptResponse(stop_reason='end_turn')
        chat.task = asyncio.create_task(self._run_turn(chat, session_id, text))
        try:
            await chat.task
        except asyncio.CancelledError:
            # session/cancel: the turn closes its open tool calls and saves.
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return PromptResponse(stop_reason='cancelled')
        finally:
            chat.task = None
        error = chat.session.last_turn_error
        if error:
            await self._say(session_id, f"\n\n{error}")
        return PromptResponse(stop_reason='end_turn')

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        chat = self.chats.get(session_id)
        if chat is not None and chat.task is not None and not chat.task.done():
            chat.task.cancel()

    # -- the turn ----------------------------------------------------------

    def _chat(self, session_id: str) -> _Chat:
        chat = self.chats.get(session_id)
        if chat is None:
            raise RequestError.resource_not_found(session_id)
        return chat

    async def _say(self, session_id: str, text: str) -> None:
        await self.conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(content=TextContentBlock(text=text)),
        )

    async def _run_turn(self, chat: _Chat, session_id: str, text: str) -> None:
        from cognitrix.safety.approval_gate import acp_approver
        from cognitrix.tools.utils import ToolExecutionContext

        async def output(event: Any = None, *args: Any, **kwargs: Any) -> None:
            await self._forward(chat, session_id, event)

        async def approve(tool_call: Any, risk: Any, person_only: bool) -> Any:
            return await self._ask(chat, session_id, tool_call, risk, person_only)

        if self.user_id:
            # Signed in: the turn runs like a chat in the app.
            await self._run_app_turn(chat, session_id, text)
            return
        token = acp_approver.set(approve)
        try:
            from cognitrix.tools.utils import HostProcessMode

            # The editor started this process on the person's own machine,
            # like the `cognitrix` CLI: the shell and coding tools may run
            # (risky commands still ask in the editor).
            context = ToolExecutionContext(
                user_id=self.user_id, host_process_mode=HostProcessMode.TRUSTED_LOCAL)
            await chat.session(
                text, chat.agent, 'acp', True, output, {}, True,
                tool_context=context,
            )
        finally:
            acp_approver.reset(token)

    async def _run_app_turn(self, chat: _Chat, session_id: str, text: str) -> None:
        """A turn for a person's account: the same path as a chat in the app
        (``interface='web'``, the person as owner), so the agent has the same
        prompt, tools, connected apps and permission rules. Approval cards are
        asked in the editor; questions are shown and skipped."""
        from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
        from cognitrix.safety.approval_gate import resolve_web_approval, web_turn_ctx
        from cognitrix.tools.utils import HostProcessMode, ToolExecutionContext

        owner = str(self.user_id)

        async def emit(event: Any = None, *args: Any, **kwargs: Any) -> None:
            if isinstance(event, dict) and event.get('type') == 'approval_request':
                await self._ask_card(chat, session_id, event, owner, resolve_web_approval)
            elif isinstance(event, dict) and event.get('type') == 'question_request':
                from cognitrix.cli.app_turn import skip_question

                await self._say(session_id, f"\n\n{event.get('prompt') or ''}\n")
                await skip_question(str(event.get('request_id') or ''), owner,
                                    "The editor can't show question cards. Carry on with your best judgement, "
                                    'or ask in your reply.')
            else:
                await self._forward(chat, session_id, event)

        sid = str(chat.session.id)
        approval_token = web_turn_ctx.set({
            'emit': emit, 'session_id': sid, 'bypass': False, 'user_key': owner,
            'agent_name': getattr(chat.agent, 'name', '') or '', 'origin': 'editor',
        })
        question_token = question_turn_ctx.set(QuestionTurnContext(
            emit=emit, session_id=sid, stream_id=f'acp-{sid}', user_key=owner))
        try:
            await chat.session(
                text, chat.agent, interface='web', stream=True, output=emit,
                wsquery={'type': 'generate', 'action': 'chat_message'},
                tool_context=ToolExecutionContext(
                    session_id=sid, user_id=owner, agent_id=str(chat.agent.id),
                    host_process_mode=HostProcessMode.TRUSTED_LOCAL),
            )
        finally:
            question_turn_ctx.reset(question_token)
            web_turn_ctx.reset(approval_token)

    async def _ask_card(self, chat: _Chat, session_id: str, event: dict[str, Any], owner: str,
                        resolve: Any) -> None:
        """An app approval card, asked in the editor."""
        request_id = str(event.get('request_id') or '')
        options = [
            PermissionOption(option_id='allow_once', name='Allow', kind='allow_once'),
            PermissionOption(option_id='allow_always', name='Allow for this session', kind='allow_always'),
            PermissionOption(option_id='reject_once', name='Deny', kind='reject_once'),
        ]
        name = str(event.get('tool_name') or 'tool')
        params = _params(event.get('params'))
        level = str(event.get('risk_level') or '')
        details = str(event.get('details') or '')
        update = ToolCallUpdate(
            tool_call_id=f"approval-{uuid.uuid4().hex[:10]}",
            title=tool_title(name, params) + (f" ({level} risk)" if level else ''),
            kind=tool_kind(name), status='pending', raw_input=params,
            content=[ContentToolCallContent(content=TextContentBlock(text=details))] if details else None,
            locations=_locations(params, chat.cwd),
        )
        try:
            response = await self.conn.request_permission(session_id=session_id, tool_call=update, options=options)
        except Exception:
            logger.warning('Editor approval failed', exc_info=True)
            resolve(request_id, False, owner)
            return
        outcome = getattr(response, 'outcome', None)
        grant = 'chat' if event.get('access') else None
        if isinstance(outcome, AllowedOutcome) and outcome.option_id == 'allow_once':
            resolve(request_id, True, owner, grant=grant)
        elif isinstance(outcome, AllowedOutcome) and outcome.option_id == 'allow_always':
            resolve(request_id, True, owner, remember=True, remember_shell=bool(event.get('full_shell')), grant=grant)
        else:
            resolve(request_id, False, owner)

    async def _forward(self, chat: _Chat, session_id: str, event: Any) -> None:
        """Turn one chat event into the editor's session update."""
        if not isinstance(event, dict):
            if event:
                await self._say(session_id, str(event))
            return
        if event.get('type') == 'tool':
            await self._tool_event(chat, session_id, event)
            return
        content = event.get('content')
        if isinstance(content, str) and content and event.get('type') in (None, '', 'text'):
            await self._say(session_id, content)

    async def _tool_event(self, chat: _Chat, session_id: str, event: dict[str, Any]) -> None:
        call_id = str(event.get('tool_call_id') or f"call-{uuid.uuid4().hex[:10]}")
        name = str(event.get('tool_name') or 'tool')
        status = event.get('status')
        if status == 'started':
            chat.started.add(call_id)
            params = _params(event.get('params'))
            await self.conn.session_update(session_id=session_id, update=ToolCallStart(
                tool_call_id=call_id, title=tool_title(name, params), kind=tool_kind(name),
                status='in_progress', raw_input=params, locations=_locations(params, chat.cwd),
            ))
            return
        result = event.get('result')
        body = [ContentToolCallContent(content=TextContentBlock(text=_preview(result)))] if result else None
        update_status = 'completed' if status == 'completed' else 'failed'
        if call_id not in chat.started:
            # A tool the editor never saw start (a nested call): show it whole.
            await self.conn.session_update(session_id=session_id, update=ToolCallStart(
                tool_call_id=call_id, title=tool_title(name, None), kind=tool_kind(name),
                status=update_status, content=body,
            ))
            return
        chat.started.discard(call_id)
        await self.conn.session_update(session_id=session_id, update=ToolCallProgress(
            tool_call_id=call_id, status=update_status, content=body,
        ))

    async def _ask(self, chat: _Chat, session_id: str, tool_call: Any, risk: Any, person_only: bool) -> Any:
        """Ask the editor's person about a risky tool call."""
        import hashlib

        from cognitrix.safety.approval_gate import ApprovalResult

        key = hashlib.sha256(json.dumps(
            [tool_call.tool_name, tool_call.params], sort_keys=True, default=str).encode()).hexdigest()
        if key in chat.allowed:
            return ApprovalResult(approved=True)
        options = [
            PermissionOption(option_id='allow_once', name='Allow', kind='allow_once'),
            PermissionOption(option_id='allow_always', name='Allow for this session', kind='allow_always'),
            PermissionOption(option_id='reject_once', name='Deny', kind='reject_once'),
        ]
        params = _params(tool_call.params)
        level = getattr(getattr(risk, 'risk_level', None), 'value', '')
        details = str(getattr(risk, 'details', '') or '')
        update = ToolCallUpdate(
            tool_call_id=f"approval-{uuid.uuid4().hex[:10]}",
            title=tool_title(tool_call.tool_name, params) + (f" ({level} risk)" if level else ''),
            kind=tool_kind(tool_call.tool_name),
            status='pending',
            raw_input=params,
            content=[ContentToolCallContent(content=TextContentBlock(text=details))] if details else None,
            locations=_locations(params, chat.cwd),
        )
        response = await self.conn.request_permission(session_id=session_id, tool_call=update, options=options)
        outcome = getattr(response, 'outcome', None)
        if not isinstance(outcome, AllowedOutcome):
            return ApprovalResult(approved=False)
        if outcome.option_id == 'allow_always':
            chat.allowed.add(key)
            return ApprovalResult(approved=True)
        if outcome.option_id == 'allow_once':
            return ApprovalResult(approved=True)
        return ApprovalResult(approved=False)

    async def _replay(self, session_id: str, history: list[dict[str, Any]]) -> None:
        """Send a reopened chat's messages back to the editor."""
        for message in history:
            if not isinstance(message, dict):
                continue
            role = str(message.get('role') or '').lower()
            kind = message.get('type')
            content = message.get('content')
            if kind in ('turn_timing', 'tool_calls_result', 'screenshot') or not isinstance(content, str) or not content:
                continue
            if role == 'user' and kind in (None, 'text', 'steer'):
                update: Any = UserMessageChunk(content=TextContentBlock(text=content))
            elif role == 'assistant':
                update = AgentMessageChunk(content=TextContentBlock(text=content))
            else:
                continue
            await self.conn.session_update(session_id=session_id, update=update)


def _params(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {'value': value}
    return value


def _folders(cwd: str) -> list[dict[str, Any]]:
    from cognitrix.common.folders import allow_root

    path = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    allow_root(path)
    return [{'path': path, 'access': 'write', 'name': Path(path).name or path, 'primary': True}]


class _LineWriter(asyncio.BaseTransport):
    """Writes protocol messages to the real stdout (a private copy of fd 1)."""

    def __init__(self, stream: Any):
        self._stream = stream
        self._closing = False
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:  # type: ignore[override]
        if self._closing:
            return
        with self._lock:
            self._stream.write(data)
            self._stream.flush()

    def can_write_eof(self) -> bool:  # type: ignore[override]
        return False

    def is_closing(self) -> bool:  # type: ignore[override]
        return self._closing

    def close(self) -> None:  # type: ignore[override]
        self._closing = True

    def abort(self) -> None:  # type: ignore[override]
        self.close()

    def get_extra_info(self, name: str, default: Any = None) -> Any:  # type: ignore[override]
        return default


class _Drain(asyncio.BaseProtocol):
    async def _drain_helper(self) -> None:
        return None


async def _protocol_streams() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Reader on stdin and writer on the real stdout; stray prints go to stderr.

    fd 1 is pointed at stderr after taking a private copy, so status lines,
    tool output and child processes that inherit stdout can't break the
    protocol stream.
    """
    loop = asyncio.get_running_loop()
    proto_fd = os.dup(sys.stdout.fileno())
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    out = os.fdopen(proto_fd, 'wb', buffering=0)
    stdin = os.fdopen(os.dup(sys.stdin.fileno()), 'rb', buffering=0)

    reader = asyncio.StreamReader(limit=50 * 1024 * 1024)

    def pump() -> None:
        try:
            while True:
                line = stdin.readline()
                if not line:
                    break
                loop.call_soon_threadsafe(reader.feed_data, line)
        except Exception:
            logger.exception('ACP stdin reader stopped')
        finally:
            loop.call_soon_threadsafe(reader.feed_eof)

    threading.Thread(target=pump, name='acp-stdin', daemon=True).start()
    writer = asyncio.StreamWriter(_LineWriter(out), _Drain(), None, loop)  # type: ignore[arg-type]
    return reader, writer


async def serve(agent_name: str = '', user_email: str = '') -> None:
    """Run the ACP server on stdin/stdout until the editor closes it."""
    reader, writer = await _protocol_streams()
    # Whose agents: --user, else the install's owner (the person who set up
    # the app on this computer), so the default agent is their main assistant.
    from cognitrix.cli import main_assistant as cli_agent
    from cognitrix.cli.core import run_configuration

    await run_configuration()
    try:
        user = await cli_agent.cli_owner(user_email)
    except cli_agent.NoSuchUser:
        print(f"No account with the email {user_email}", file=sys.stderr)
        return
    user_id = cli_agent._key(user)
    agent = CognitrixAgent(agent_name=agent_name, user_id=user_id)
    with contextlib.suppress(asyncio.CancelledError):
        await run_agent(agent, writer, reader)


async def command(args: Any) -> None:
    """``cognitrix acp``: entry point from the CLI."""
    await serve(agent_name=getattr(args, 'acp_agent', '') or '', user_email=getattr(args, 'user', '') or '')
