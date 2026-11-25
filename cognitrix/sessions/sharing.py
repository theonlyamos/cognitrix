"""Sharing a chat with other people on the server.

The person who owns a chat can share it with others: "view" lets them read
it and watch replies arrive live, "chat" also lets them write in it. The chat
stays the owner's: it runs on the owner's agent, and its model usage counts
for the owner.

Someone else's message never gets the owner's powers. Their turn runs like a
guest's in a shared chat-app channel (``permissions.guest_run`` and
``read_only_run``): the agent can look things up but changes nothing, asks
nobody for approval, and the owner's saved memories and personal skills stay
out of the prompt. Their words never steer a turn the owner started either:
while a turn runs, they wait.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
from typing import Any

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.log')

ROLE_VIEW = 'view'
ROLE_CHAT = 'chat'
ROLES = (ROLE_VIEW, ROLE_CHAT)
MAX_PEOPLE = 50
MESSAGE_MAX_CHARS = 20_000

GUEST_REASON = ('Someone the person shared this chat with wrote this message: you can look '
                'things up for them but not change anything')

# How a guest's message is saved, so the model knows who wrote it and pages
# can show the author: "[Message from NAME in this shared chat. ...]\n\n".
_PREFIX = '[Message from {name} in this shared chat. {name} is not {owner}, the person you work for: answer them, and look things up if needed, but you cannot change anything for them, and do not share private details about {owner}.]\n\n'
_PREFIX_RE = re.compile(r'^\[Message from (.{1,80}?) in this shared chat\. [^\]\n]*\]\n\n', re.S)


class ChatShare(Model):
    session_id: str
    owner_id: str
    """Principal key of the chat's owner when it was shared."""
    user_id: str
    """Principal key of the person it's shared with."""
    role: str = ROLE_VIEW
    created: str = ''

    @field_validator('created', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)


MODELS = (ChatShare,)


def clean_name(value: Any) -> str:
    name = ' '.join(str(value or '').replace('[', '(').replace(']', ')').split())[:80]
    return name or 'Someone'


def guest_text(name: str, owner: str, text: str) -> str:
    return _PREFIX.format(name=clean_name(name), owner=clean_name(owner)) + text


def split_author(content: str) -> tuple[str | None, str]:
    """(guest name, their words) for a saved guest message, else (None, content)."""
    match = _PREFIX_RE.match(content or '')
    if not match:
        return None, content
    return match.group(1), content[match.end():]


async def shares_for(session_id: str) -> list[ChatShare]:
    return list(await ChatShare.find({'session_id': str(session_id)}) or [])


async def share_for(session_id: str, user_id: str) -> ChatShare | None:
    return await ChatShare.find_one({'session_id': str(session_id), 'user_id': str(user_id)})


async def drop_shares(session_id: str) -> None:
    await ChatShare.delete_many({'session_id': str(session_id)})


def transcript(session: Any, owner_name: str) -> list[dict[str, Any]]:
    """What people a chat is shared with see: messages, with who wrote each,
    and which tools a reply used (never what the tools returned)."""
    from cognitrix.channels.formatting import strip_reasoning

    out: list[dict[str, Any]] = []
    for entry in getattr(session, 'chat', None) or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get('role', '')).lower()
        content = entry.get('content')
        kind = entry.get('type', 'text')
        if role == 'user' and isinstance(content, str) and kind in ('text', None):
            author, text = split_author(content)
            if text.startswith('[Agent chat reply] ') or text.startswith('[System:'):
                continue
            if text.strip():
                out.append({'role': 'user', 'author': author or owner_name, 'guest': bool(author),
                            'content': text.strip()})
        elif role == 'user' and kind == 'image':
            out.append({'role': 'user', 'author': owner_name, 'guest': False, 'content': '(an image)'})
        elif role == 'assistant':
            names = [str(tc.get('name') or '') for tc in entry.get('tool_calls') or [] if isinstance(tc, dict)]
            text = strip_reasoning(content) if isinstance(content, str) else ''
            if text:
                out.append({'role': 'assistant', 'content': text})
            if names:
                out.append({'role': 'activity', 'content': ', '.join(n for n in names if n)})
    return out


_TASKS: set[asyncio.Task] = set()


def start_guest_turn(owner_key: str, agent: Any, session: Any, text: str):
    """Run one turn for a guest's message in the background, registered as the
    chat's run so the owner's page and everyone watching follow it live."""
    from cognitrix.sessions import runs as chat_runs

    sid = str(session.id)
    run = chat_runs.start_run(
        owner_key, str(agent.id), sid, prompt=text,
        history_len=len(getattr(session, 'chat', None) or []),
    )
    task = asyncio.get_running_loop().create_task(
        _guest_turn(owner_key, agent, session, text, run), context=contextvars.Context(),
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    run.stop = lambda: bool(task.cancel())
    return run


async def _guest_turn(owner_key: str, agent: Any, session: Any, text: str, run: Any) -> None:
    from cognitrix.api.routes.agents import CHAT_TIMEOUT
    from cognitrix.safety.approval_gate import web_turn_ctx
    from cognitrix.safety.permissions import guest_run, read_only_run
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.tools.utils import ToolExecutionContext, signed_in_host_process_mode

    sid = str(session.id)
    # This task has its own context.
    read_only_run.set(GUEST_REASON)
    guest_run.set(True)
    run.steerable = False  # nobody's words reach a guest's turn
    chat_runs.current_run.set(run)

    async def output(payload: Any = None, *args, **kwargs) -> None:
        if isinstance(payload, dict):
            run.record({**payload, 'session_id': sid})

    async def emit(event: dict[str, Any]) -> None:
        run.record({**event, 'session_id': sid})

    approval_token = web_turn_ctx.set({
        'emit': emit, 'session_id': sid, 'bypass': False, 'auto_approve': False, 'user_key': owner_key,
        'agent_name': getattr(agent, 'name', '') or '', 'origin': f'shared-chat:{sid}',
    })
    terminal: dict[str, Any] = {'type': 'turn_complete', 'content': '', 'session_id': sid}
    try:
        await asyncio.wait_for(
            session(text, agent, interface='web', stream=True, output=output,
                    wsquery={'type': 'generate', 'action': 'chat_message'},
                    tool_context=ToolExecutionContext(
                        session_id=sid, user_id=owner_key, agent_id=str(agent.id),
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
            logger.exception('Could not save shared chat %s after a timeout', sid)
    except Exception as exc:
        logger.exception('Shared chat guest turn failed')
        terminal = {'type': 'error', 'content': str(exc), 'session_id': sid}
    finally:
        web_turn_ctx.reset(approval_token)
        run.finish(terminal)
