"""Chat titles the model writes.

A new chat is listed by its first message until its first reply is in. Then
one short model call names it ("Newsletter launch plan"), the way a person
would label the conversation. A name the person types always wins, and a chat
that already has a written title is left alone.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger('cognitrix.log')

TITLE_TIMEOUT = 30.0
MAX_TITLE_CHARS = 60
SNIPPET_CHARS = 1200

SYSTEM_PROMPT = (
    'Write a short title for this conversation, the way a person would label '
    'it in a list: 2 to 6 words, in the language the person wrote in, no '
    'quotes, no full stop, no emoji. Say what it is about, not what was '
    'asked ("Newsletter launch plan", not "Give me a plan"). Answer with the '
    'title only.'
)

_pending: set[str] = set()
_tasks: set[asyncio.Task] = set()


def _text(message: dict[str, Any]) -> str:
    content = message.get('content')
    if isinstance(content, list):
        content = ' '.join(str(p.get('text', '')) for p in content if isinstance(p, dict))
    return content.strip() if isinstance(content, str) else ''


def first_exchange(chat: list[dict[str, Any]]) -> tuple[str, str]:
    """The first thing the person wrote and the first written reply."""
    asked = reply = ''
    for message in chat or []:
        if not isinstance(message, dict) or message.get('type') not in (None, 'text'):
            continue
        role = str(message.get('role') or '').lower()
        if role == 'user' and not asked:
            from cognitrix.sessions.sharing import split_author

            asked = split_author(_text(message))[1].strip()
        elif role == 'assistant' and asked and not reply:
            reply = _text(message)
        if asked and reply:
            break
    return asked, reply


def clean(raw: str) -> str:
    """One tidy line from whatever the model answered; '' when it is unusable."""
    text = re.sub(r'<think>.*?</think>', '', raw or '', flags=re.S)
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ''
    title = re.sub(r'^(title|titre|título)\s*:\s*', '', lines[0], flags=re.I)
    title = title.strip(' "\'“”‘’`*#').rstrip('.').strip()
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
    return title


def wanted(session: Any) -> bool:
    """Only ordinary saved chats that nobody has named yet."""
    if (getattr(session, 'title', None) or '').strip() or (getattr(session, 'auto_title', None) or '').strip():
        return False
    if getattr(session, 'incognito', None) or getattr(session, 'group_chat', None):
        return False
    if session.task_id or session.run_id or getattr(session, 'public_link_id', None):
        return False
    asked, reply = first_exchange(session.chat or [])
    return bool(asked and reply)


async def write_title(session: Any, agent: Any) -> str:
    """Ask the model for a title and store it. Returns '' when it could not."""
    from cognitrix.sessions.side_questions import _load_llm

    asked, reply = first_exchange(session.chat or [])
    if not asked or not reply:
        return ''
    llm = _load_llm(agent)
    if llm is None:
        return ''
    prompt = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': f'The person wrote:\n{asked[:SNIPPET_CHARS]}\n\nThe reply began:\n{reply[:SNIPPET_CHARS]}'},
    ]
    response = await asyncio.wait_for(llm(prompt, stream=False), timeout=TITLE_TIMEOUT)
    if response is None or getattr(response, 'error', None):
        return ''
    title = clean(getattr(response, 'llm_response', '') or '')
    if not title:
        return ''
    # Only this one field: the chat may have moved on while the model wrote.
    session.auto_title = title
    await type(session).update_one({'id': session.id}, {'auto_title': title})
    from cognitrix.sessions import index as chat_index

    row = await chat_index.ChatIndex.find_one({'session_id': str(session.id)})
    if row is not None:
        await chat_index.ChatIndex.update_one({'id': row.id}, {'title': title})
        chat_index._bump(str(row.user_id or ''))
    return title


def schedule(session: Any, agent: Any) -> None:
    """Title the chat in the background after a turn; never raises, never waits."""
    try:
        if not wanted(session):
            return
        key = str(session.id)
        if key in _pending:
            return
        _pending.add(key)

        async def run() -> None:
            try:
                # Counted toward the person's usage, not this chat's own tally.
                from cognitrix import usage
                usage.set_sink(None)
                await write_title(session, agent)
            except Exception:
                logger.debug('Could not write a chat title', exc_info=True)
            finally:
                _pending.discard(key)

        task = asyncio.get_running_loop().create_task(run())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:
        logger.debug('Could not schedule a chat title', exc_info=True)
