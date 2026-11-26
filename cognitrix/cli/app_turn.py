"""Terminal turns that run exactly like a chat in the app.

When the CLI runs for a person's account (their main assistant, or an agent
they picked with ``--agent``), each turn goes through the same path as a chat
in the desktop or web app: ``Session.__call__`` with ``interface='web'``, the
person as the turn's owner, and the same approval and question channels.
So the agent gets the same system prompt (AGENTS.md, memory, skills, project
notes), the same per-turn tools (Run Code and tools on demand, connected apps,
MCP servers, memory, artifacts, board, swarms, Request Access ...), the same
permission rules and auto-approve setting, and the chat is saved to the
person's chat list like any other.

The terminal stands in for the chat page: replies stream to stdout, tool
calls are one line each on stderr, and approval cards and Ask User questions
are asked at the prompt. With no terminal to ask (stdin is not a TTY, as in
scripts and benchmarks), an approval is refused and a question is cancelled,
unless auto-approve applies (the person's "Auto-approve everything" setting
or ``--dangerously-skip-permissions``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger('cognitrix.log')

WSQUERY = {'type': 'generate', 'action': 'chat_message'}
TERMINAL_MARK = {'via': 'terminal'}  # Session.started_by for chats the CLI made


def _interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _short(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def _err(text: str) -> None:
    try:
        print(text, file=sys.stderr, flush=True)
    except Exception:
        pass


# --- chats --------------------------------------------------------------------

def terminal_folders(agent: Any, cwd: str | None = None) -> list[dict[str, Any]]:
    """The folder the CLI was started in (primary, read and write), then the
    agent's own default folders, as a new chat in the app would have them."""
    from cognitrix.common.folders import allow_root

    path = os.path.abspath(os.path.expanduser(cwd or os.getcwd()))
    allow_root(path)
    folders = [{'path': path, 'access': 'write', 'name': Path(path).name or path, 'primary': True}]
    for folder in getattr(agent, 'folders', None) or []:
        if not isinstance(folder, dict) or not folder.get('path'):
            continue
        if os.path.abspath(os.path.expanduser(str(folder['path']))) == path:
            continue
        folders.append({**folder, 'primary': False})
    return folders


async def new_chat(owner: str, agent: Any, *, cwd: str | None = None) -> Any:
    """A new chat owned by ``owner``, made the way the app makes one."""
    from cognitrix.session_ownership import claim_new
    from cognitrix.sessions.base import Session

    folder = Path(os.path.abspath(cwd or os.getcwd())).name
    session = Session(agent_id=str(agent.id), user_id=owner, started_by=dict(TERMINAL_MARK),
                      title=f'Terminal: {folder}' if folder else 'Terminal')
    session.folders = terminal_folders(agent, cwd)
    await session.save()
    try:
        await claim_new(str(session.id), owner, str(agent.id))
    except BaseException:
        await Session.delete_many({'id': str(session.id)})
        raise
    return session


async def last_chat(owner: str, agent: Any) -> Any | None:
    """The newest chat the CLI made for ``owner`` with ``agent`` (``--continue``)."""
    from cognitrix.sessions.base import Session

    rows = [
        s for s in await Session.find({'agent_id': str(agent.id), 'user_id': owner}) or []
        if (getattr(s, 'started_by', None) or {}).get('via') == TERMINAL_MARK['via']
    ]
    if not rows:
        return None
    session = max(rows, key=lambda s: str(getattr(s, 'created_at', '') or getattr(s, 'started_at', '') or ''))
    # Same folder as this run, so a continued chat works where the CLI is.
    session.folders = terminal_folders(agent)
    return session


# --- the terminal as the chat page ---------------------------------------------

class TerminalChannel:
    """Turns the app's chat events into terminal output and prompts."""

    def __init__(self, owner: str, write: Callable[..., Any] | None = None, *, show_tools: bool = True):
        self.owner = owner
        self.write = write or self._stdout
        self.show_tools = show_tools
        self.wrote_text = False

    @staticmethod
    def _stdout(text: str, end: str = '') -> None:
        sys.stdout.write(text + end)
        sys.stdout.flush()

    def _text(self, text: str) -> None:
        if not text:
            return
        self.wrote_text = True
        try:
            self.write(text, end='')
        except TypeError:
            self.write(text)

    async def emit(self, event: Any = None, *args: Any, **kwargs: Any) -> None:
        if not isinstance(event, dict):
            if event:
                self._text(str(event))
            return
        kind = event.get('type')
        if kind == 'tool':
            self._tool(event)
        elif kind == 'approval_request':
            await self._approval(event)
        elif kind == 'question_request':
            await self._question(event)
        elif kind == WSQUERY['type'] and isinstance(event.get('content'), str):
            self._text(event['content'])

    def _tool(self, event: dict[str, Any]) -> None:
        if not self.show_tools:
            return
        name = str(event.get('tool_name') or 'tool')
        status = event.get('status')
        if status == 'started':
            params = event.get('params')
            _err(f"\n⏺ {name}" + (f"({_short(params)})" if params not in (None, '', {}) else ''))
        elif status == 'error':
            _err(f"  ⎿ failed: {_short(event.get('result') or '')}")

    async def _ask(self, prompt: str) -> str | None:
        if not _interactive():
            return None
        try:
            return await asyncio.to_thread(input, prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    async def _approval(self, event: dict[str, Any]) -> None:
        from cognitrix.safety.approval_gate import resolve_web_approval

        request_id = str(event.get('request_id') or '')
        name = str(event.get('tool_name') or 'tool')
        level = str(event.get('risk_level') or '')
        _err(f"\n⚠ {name} needs your OK" + (f" ({level} risk)" if level else ''))
        if event.get('params'):
            _err(f"  {_short(event.get('params'), 400)}")
        if event.get('details'):
            _err(f"  {_short(event.get('details'), 400)}")
        if not _interactive():
            _err('  Refused: no terminal to ask in. Turn on auto-approve in the app, or use '
                 '--dangerously-skip-permissions in a throwaway sandbox.')
            resolve_web_approval(request_id, False, self.owner)
            return
        answer = (await self._ask('  Allow? [y]es / [s]ession / [n]o: ') or '').strip().lower()
        if answer in ('y', 'yes'):
            resolve_web_approval(request_id, True, self.owner, grant='chat' if event.get('access') else None)
        elif answer in ('s', 'session'):
            resolve_web_approval(request_id, True, self.owner, remember=True,
                                 remember_shell=bool(event.get('full_shell')),
                                 grant='chat' if event.get('access') else None)
        else:
            resolve_web_approval(request_id, False, self.owner)

    async def _question(self, event: dict[str, Any]) -> None:
        from cognitrix.questions.broker import resolve_question

        request_id = str(event.get('request_id') or '')
        if event.get('secret'):
            _err('\n? The agent asked for a sign-in. Save it in the app (Settings, Sign-ins) and ask again.')
            await resolve_question(request_id, self.owner, 'cancel')
            return
        questions = event.get('questions') or [{
            'prompt': event.get('prompt'), 'options': event.get('options') or []}]
        for item in questions:
            _err(f"\n? {item.get('prompt') or ''}")
            if item.get('details'):
                _err(f"  {item['details']}")
            for n, option in enumerate(item.get('options') or [], 1):
                _err(f"  {n}. {option.get('label') or option.get('id')}")
        if not _interactive():
            _err('  Skipped: no terminal to answer in.')
            await resolve_question(request_id, self.owner, 'cancel')
            return
        answer = (await self._ask('  Answer (a number, or type a reply): ') or '').strip()
        if not answer:
            await resolve_question(request_id, self.owner, 'cancel')
            return
        first = questions[0].get('options') or []
        if len(questions) == 1 and answer.isdigit() and 1 <= int(answer) <= len(first):
            if await resolve_question(request_id, self.owner, 'answer', option_id=str(first[int(answer) - 1].get('id'))):
                return
        if not await resolve_question(request_id, self.owner, 'answer', text=answer[:4000]):
            await resolve_question(request_id, self.owner, 'cancel')


# --- one turn -----------------------------------------------------------------

async def run_turn(session: Any, agent: Any, text: str, *, owner: str, stream: bool = True,
                   write: Callable[..., Any] | None = None, bypass: bool = False) -> None:
    """One turn of ``session`` as the app's chat would run it."""
    from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
    from cognitrix.safety.approval_gate import web_turn_ctx
    from cognitrix.tools.utils import HostProcessMode, ToolExecutionContext

    channel = TerminalChannel(owner, write)
    sid = str(session.id)
    approval_token = web_turn_ctx.set({
        'emit': channel.emit, 'session_id': sid, 'bypass': bool(bypass), 'user_key': owner,
        'agent_name': getattr(agent, 'name', '') or '', 'href': f'/home?chat={sid}', 'origin': 'terminal',
    })
    question_token = question_turn_ctx.set(QuestionTurnContext(
        emit=channel.emit, session_id=sid, stream_id=f'terminal-{sid}', user_key=owner))
    try:
        await session(
            text, agent, interface='web', stream=stream, output=channel.emit, wsquery=dict(WSQUERY),
            # The person started this on their own computer, like the desktop
            # app's local engine: the shell stays confined to the chat's
            # folders, and risky commands still ask.
            tool_context=ToolExecutionContext(
                session_id=sid, user_id=owner, agent_id=str(agent.id),
                host_process_mode=HostProcessMode.TRUSTED_LOCAL,
            ),
        )
    finally:
        question_turn_ctx.reset(question_token)
        web_turn_ctx.reset(approval_token)
    if channel.wrote_text:
        channel._text('\n')


async def turn(session: Any, agent: Any, text: str, *, stream: bool = True,
               output: Callable[..., Any] | None = None) -> None:
    """A terminal turn: like the app's chat when the chat has an owner (the
    CLI is running for a person's account), else the CLI's own local turn."""
    owner = str(getattr(session, 'user_id', '') or '')
    if owner:
        await run_turn(session, agent, text, owner=owner, stream=stream, write=output)
        return
    from cognitrix.tools.utils import trusted_local_execution_context

    kwargs: dict[str, Any] = {'stream': stream, 'tool_context': trusted_local_execution_context()}
    if output is not None:
        kwargs['output'] = output
    await session(text, agent, **kwargs)
