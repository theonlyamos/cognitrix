"""Chats that agents start: a new conversation that runs in the background.

An agent can open a chat with itself or another agent, send it a first
message and carry on. The chat is an ordinary conversation owned by the same
person: it shows in their chat list (labelled with the agent that started
it), they can open it, watch it run and answer its approvals there. When the
reply is ready it can be sent back to the chat that started it, so two agents
can talk with each other.

Limits keep this from running away: chats started by agents can only nest
``MAX_DEPTH`` levels, a person has at most ``MAX_RUNNING_PER_USER`` agent
chats working at once, and each agent chat takes at most ``MAX_EXCHANGES``
messages from agents (replies sent back count too).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from cognitrix.sessions import runs as chat_runs

logger = logging.getLogger('cognitrix.log')

MAX_DEPTH = 2
MAX_RUNNING_PER_USER = 5
MAX_EXCHANGES = 6
MESSAGE_MAX_CHARS = 8000
REPLY_PREVIEW_CHARS = 4000
# How long a reply waits for the chat that started it to be free.
REPORT_WAIT_SECONDS = 600.0
REPORT_POLL_SECONDS = 2.0

# Marks a reply delivered back to the chat that started it, so a run of
# agent-only turns can be counted from the saved history.
REPORT_PREFIX = '[Agent chat reply] '

# Strong references: a bare create_task can be garbage collected mid-turn.
_TASKS: set[asyncio.Task] = set()


class AgentChatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def last_reply(session: Any) -> str:
    """The text of the newest assistant message in a chat, or ''."""
    for entry in reversed(getattr(session, 'chat', None) or []):
        if entry.get('role') != 'assistant':
            continue
        content = entry.get('content')
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ''


def depth_of(session: Any) -> int:
    started_by = getattr(session, 'started_by', None) or {}
    try:
        return int(started_by.get('depth') or 0)
    except (TypeError, ValueError):
        return 0


def running_agent_chats(user_key: str) -> int:
    return sum(1 for run in chat_runs.active_runs(user_key) if run.started_by_agent)


def is_running(user_key: str, session_id: str) -> bool:
    run = chat_runs.get_run(user_key, session_id)
    return bool(run and run.running)


async def create_chat(user_key: str, agent: Any, started_by: dict[str, Any]) -> Any:
    """Make a new chat owned by ``user_key`` with ``agent``."""
    from cognitrix.session_ownership import claim_new
    from cognitrix.sessions.base import Session

    session = Session(agent_id=str(agent.id), user_id=user_key, started_by=started_by)
    await session.save()
    try:
        await claim_new(str(session.id), user_key, str(agent.id))
    except BaseException:
        await Session.delete_many({'id': str(session.id)})
        raise
    return session


def run_in_background(
    user_key: str,
    agent: Any,
    session: Any,
    text: str,
    *,
    on_done: Callable[[str, str], Awaitable[None]] | None = None,
) -> chat_runs.ChatRun:
    """Run one turn of ``session`` as a background task the page can follow.

    ``on_done(status, reply)`` runs after the turn ends, with status
    complete / stopped / error.
    """
    run = chat_runs.start_run(
        user_key, str(agent.id), str(session.id), prompt=text,
        history_len=len(getattr(session, 'chat', None) or []),
    )
    run.started_by_agent = True
    # A fresh context: the new chat must not inherit the starting turn's
    # approval prompt, question channel or tool authority.
    task = asyncio.get_running_loop().create_task(
        _turn(user_key, agent, session, text, run, on_done),
        context=contextvars.Context(),
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    run.stop = lambda: bool(task.cancel())
    return run


async def _turn(
    user_key: str,
    agent: Any,
    session: Any,
    text: str,
    run: chat_runs.ChatRun,
    on_done: Callable[[str, str], Awaitable[None]] | None,
) -> None:
    from cognitrix.api.routes.agents import CHAT_TIMEOUT
    from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
    from cognitrix.safety.approval_gate import web_turn_ctx
    from cognitrix.tools.utils import ToolExecutionContext, signed_in_host_process_mode

    sid = str(session.id)

    async def output(payload: Any = None, *args, **kwargs) -> None:
        if isinstance(payload, dict):
            run.record({**payload, 'session_id': sid})

    async def emit(event: dict[str, Any]) -> None:
        # Nobody may be looking at this chat: an approval it asks for is
        # listed in the Inbox and sent as a notification (approval_alerts).
        run.record({**event, 'session_id': sid})

    approval_token = web_turn_ctx.set({
        'emit': emit, 'session_id': sid, 'bypass': False, 'user_key': user_key,
        'agent_name': getattr(agent, 'name', '') or '',
    })
    question_token = question_turn_ctx.set(QuestionTurnContext(
        emit=emit, session_id=sid, stream_id=f'agent-chat-{sid}', user_key=user_key,
    ))
    terminal: dict[str, Any] = {'type': 'turn_complete', 'content': '', 'session_id': sid}
    try:
        await asyncio.wait_for(
            session(text, agent, interface='web', stream=True, output=output,
                    wsquery={'type': 'generate', 'action': 'chat_message'},
                    tool_context=ToolExecutionContext(
                        session_id=sid, user_id=user_key, agent_id=str(agent.id),
                        host_process_mode=signed_in_host_process_mode(),
                    )),
            timeout=CHAT_TIMEOUT,
        )
    except asyncio.CancelledError:
        terminal = {'type': 'turn_stopped', 'content': '', 'session_id': sid}
    except TimeoutError:
        terminal = {'type': 'error', 'content': 'timed out', 'session_id': sid}
        try:
            await session.save()
        except Exception:
            logger.exception('Could not save chat %s after a timeout', sid)
    except Exception as exc:
        logger.exception('Agent-started chat turn failed')
        terminal = {'type': 'error', 'content': str(exc), 'session_id': sid}
    finally:
        web_turn_ctx.reset(approval_token)
        question_turn_ctx.reset(question_token)
        run.finish(terminal)
    if on_done is not None:
        try:
            await on_done(run.status, last_reply(session) if run.status == 'complete' else '')
        except Exception:
            logger.exception('Follow-up for chat %s failed', sid)


_REPORT_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _report_lock(user_key: str, session_id: str) -> asyncio.Lock:
    key = (user_key, session_id)
    lock = _REPORT_LOCKS.get(key)
    if lock is None:
        lock = _REPORT_LOCKS[key] = asyncio.Lock()
    return lock


def reports_since_person(session: Any) -> int:
    """Replies delivered back into this chat since the person last wrote in it."""
    count = 0
    for entry in reversed(getattr(session, 'chat', None) or []):
        if str(entry.get('role') or '').lower() != 'user':
            continue
        content = entry.get('content')
        if isinstance(content, list):
            content = ' '.join(str(p.get('text') or '') for p in content if isinstance(p, dict))
        if not str(content or '').startswith(REPORT_PREFIX):
            break
        count += 1
    return count


async def _alert_once(user_key: str, session_id: str, title: str, message: str) -> None:
    """An Inbox alert for a chat, unless one for it is still unread."""
    from cognitrix.tasks.automation import AgentNotification, create_notification

    rows = await AgentNotification.find({'user_id': user_key, 'session_id': session_id}) or []
    if any(not row.read_at for row in rows):
        return
    await create_notification(user_key, title, message, session_id=session_id)


async def bump_exchanges(session: Any) -> int:
    """Count one more agent message in this chat; refuse past the limit."""
    from cognitrix.sessions.base import Session

    started_by = dict(getattr(session, 'started_by', None) or {})
    count = int(started_by.get('exchanges') or 0)
    if count >= MAX_EXCHANGES:
        raise AgentChatError(
            'too_many_messages',
            f'This chat already had {MAX_EXCHANGES} messages from agents. '
            'Ask the person before going further.')
    started_by['exchanges'] = count + 1
    session.started_by = started_by
    await Session.update_one({'id': str(session.id)}, {'started_by': started_by})
    return count + 1


def send_turn(
    user_key: str,
    agent: Any,
    session: Any,
    text: str,
) -> chat_runs.ChatRun:
    """Run a message from an agent in a chat it started, then report back."""
    started_by = getattr(session, 'started_by', None) or {}

    async def done(status: str, reply: str) -> None:
        if started_by.get('report_back'):
            await report_back(user_key, agent, session, status, reply)
        elif status == 'complete':
            await _alert_once(user_key, str(session.id), f'{agent.name} finished a chat',
                              reply[:300] or 'The chat finished.')

    return run_in_background(user_key, agent, session, text, on_done=done)


async def report_back(user_key: str, agent: Any, session: Any, status: str, reply: str) -> None:
    """Send a chat's reply to the chat that started it, as a new turn there."""
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions.base import Session

    started_by = getattr(session, 'started_by', None) or {}
    parent_id = str(started_by.get('session_id') or '')
    parent_agent_id = str(started_by.get('agent_id') or '')
    if not parent_id or not parent_agent_id or status == 'stopped':
        return
    parent_agent = await find_visible_agent(user_key, agent_id=parent_agent_id)
    if parent_agent is None:
        return

    # One delivery at a time per chat: two children finishing together must
    # not both see it idle and run overlapping turns on the same history.
    async with _report_lock(user_key, parent_id):
        waited = 0.0
        while is_running(user_key, parent_id) and waited < REPORT_WAIT_SECONDS:
            await asyncio.sleep(REPORT_POLL_SECONDS)
            waited += REPORT_POLL_SECONDS
        if is_running(user_key, parent_id):
            logger.info('Chat %s stayed busy; reply from chat %s not delivered', parent_id, session.id)
            return
        parent = await Session.get(parent_id)
        if parent is None or str(parent.agent_id or '') != parent_agent_id or str(parent.user_id or '') != user_key:
            return
        if reports_since_person(parent) >= MAX_EXCHANGES:
            # Agents have gone back and forth long enough without the person.
            await _alert_once(user_key, parent_id, f'{agent.name} replied to {parent_agent.name}',
                              'Agents paused after several replies in a row. Open the chat to carry on.')
            return
        _deliver(user_key, agent, parent_agent, parent, session, status, reply)


def _deliver(user_key: str, agent: Any, parent_agent: Any, parent: Any, session: Any,
             status: str, reply: str) -> None:
    parent_id = str(parent.id)
    if status == 'complete':
        body = reply[:MESSAGE_MAX_CHARS] or '(no reply)'
        text = (f'{REPORT_PREFIX}{agent.name} (agent) replied in the chat you started (chat id {session.id}):\n\n{body}\n\n'
                'Carry on with what you were doing. Only use Message Chat if you need something more '
                'from it; do not reply just to thank it or confirm.')
    else:
        text = (f'{REPORT_PREFIX}The chat you started with {agent.name} (chat id {session.id}) failed before it replied. '
                'Tell the person if it matters.')

    async def told(parent_status: str, parent_reply: str) -> None:
        # The person may not be looking at the chat that just woke up.
        if parent_status != 'complete':
            return
        await _alert_once(user_key, parent_id, f'{parent_agent.name} heard back from {agent.name}',
                          parent_reply[:300] or 'Open the chat to see the reply.')

    run_in_background(user_key, parent_agent, parent, text, on_done=told)
