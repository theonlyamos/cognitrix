"""Search Chats and Read Chat: agents look through the person's past chats when
they refer to earlier work ("like we did last week").

Only the calling person's own chats are searched. Incognito chats, task runs
and the current chat are left out. An agent that doesn't read the person's
shared memory only sees its own chats. Added per turn while "Reference chat
history" is on (Settings › Memory); see ``Session._run_turn``.
"""
from __future__ import annotations

import re
from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

HISTORY_TOOL_CATEGORY = 'system'
MAX_RESULTS = 10
SNIPPET_CHARS = 160
READ_MAX_MESSAGES = 40
MESSAGE_MAX_CHARS = 2000


def _owner() -> str | None:
    ctx = current_execution_context()
    if ctx.task_id or ctx.run_id or ctx.restricted or not ctx.user_id:
        return None
    return str(ctx.user_id)


def _text_messages(session: Any) -> list[dict[str, Any]]:
    out = []
    for m in session.chat or []:
        role = str(m.get('role', '')).lower()
        if role not in ('user', 'assistant') or m.get('type', 'text') != 'text':
            continue
        content = m.get('content')
        if isinstance(content, str) and content.strip():
            text = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if text:
                out.append({'role': role, 'content': text, 'name': m.get('name') or m.get('speaker')})
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if len(w) > 2}


def _snippet(text: str, words: set[str]) -> str:
    lower = text.lower()
    hit = min((lower.find(w) for w in words if lower.find(w) >= 0), default=0)
    start = max(0, hit - SNIPPET_CHARS // 2)
    piece = text[start:start + SNIPPET_CHARS].replace('\n', ' ').strip()
    return ('…' if start else '') + piece + ('…' if start + SNIPPET_CHARS < len(text) else '')


def _when(session: Any) -> str:
    data = session.json() if hasattr(session, 'json') else {}
    stamp = str(data.get('updated_at') or '')
    return stamp[:10] if stamp else str(getattr(session, 'datetime', '') or '')


async def _searchable(user_id: str, parent: Any) -> list[Any]:
    from cognitrix.memory.saved import agent_reads_global
    from cognitrix.sessions.base import Session

    ctx = current_execution_context()
    own_only = parent is not None and not agent_reads_global(parent)
    agent_id = str(getattr(parent, 'id', '') or '')
    rows = []
    for s in await Session.find({'user_id': user_id}):
        if s.task_id or getattr(s, 'run_id', None) or getattr(s, 'incognito', False):
            continue
        if ctx.session_id and str(s.id) == str(ctx.session_id):
            continue
        if own_only and str(s.agent_id or '') != agent_id:
            continue
        rows.append(s)
    return rows


async def _agent_names(rows: list[Any]) -> dict[str, str]:
    from cognitrix.agents import Agent

    names: dict[str, str] = {}
    for agent_id in {str(r.agent_id or '') for r in rows if r.agent_id}:
        try:
            agent = await Agent.get(agent_id)
        except Exception:
            agent = None
        if agent is not None:
            names[agent_id] = agent.name
    return names


def _title(messages: list[dict[str, Any]]) -> str:
    first = next((m['content'] for m in messages if m['role'] == 'user'), '') or 'Untitled chat'
    first = ' '.join(first.split())
    return first[:70] + ('…' if len(first) > 70 else '')


@tool(category=HISTORY_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def search_chats(query: str = '', limit: int = 5, *, parent=None) -> ToolOutcome:
    """Search the person's past chats with you and their other agents, when they refer to earlier
    work or something "we talked about" that isn't in this chat or your memory.
    query: words to look for. Leave empty to list the most recent chats.
    Returns each matching chat's title, date, agent, id and a matching snippet; open one with Read Chat."""
    user_id = _owner()
    if not user_id:
        return ToolOutcome.failure('no_owner', 'Chat history works in chats with a signed-in person only.')
    limit = max(1, min(int(limit or 5), MAX_RESULTS))
    rows = await _searchable(user_id, parent)
    words = _words(query or '')
    scored = []
    for s in rows:
        messages = _text_messages(s)
        if not messages:
            continue
        if words:
            hits = [m for m in messages if words & _words(m['content'])]
            if not hits:
                continue
            matched = set().union(*(words & _words(m['content']) for m in hits))
            scored.append(((len(matched), len(hits), _when(s)), s, messages, hits))
        else:
            scored.append(((0, 0, _when(s)), s, messages, []))
    scored.sort(key=lambda t: t[0], reverse=True)
    picked = scored[:limit]
    if not picked:
        return ToolOutcome.success('No past chats match that.' if words else 'No past chats yet.')
    names = await _agent_names([s for _k, s, _m, _h in picked])
    lines = []
    for _key, s, messages, hits in picked:
        agent = names.get(str(s.agent_id or ''), 'an agent')
        line = f'- "{_title(messages)}" (chat id {s.id}, with {agent}, {_when(s)}, {len(messages)} messages)'
        for m in hits[:2]:
            who = 'person' if m['role'] == 'user' else agent
            line += f'\n    {who}: {_snippet(m["content"], words)}'
        lines.append(line)
    return ToolOutcome.success('\n'.join(lines))


@tool(category=HISTORY_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def read_chat(chat_id: str, start: int = 0, limit: int = 20, *, parent=None) -> ToolOutcome:
    """Read messages from one of the person's past chats (the chat id comes from Search Chats).
    start is the first message (0 is the beginning; use a negative number to count from the end,
    e.g. -10 for the last ten). limit is how many messages to read, at most 40."""
    user_id = _owner()
    if not user_id:
        return ToolOutcome.failure('no_owner', 'Chat history works in chats with a signed-in person only.')
    chat_id = str(chat_id or '').strip()
    session = next((s for s in await _searchable(user_id, parent) if str(s.id) == chat_id), None)
    if session is None:
        return ToolOutcome.failure('chat_not_found', f'No past chat {chat_id!r} you can read. Use Search Chats.')
    messages = _text_messages(session)
    total = len(messages)
    limit = max(1, min(int(limit or 20), READ_MAX_MESSAGES))
    start = int(start or 0)
    if start < 0:
        start = max(0, total + start)
    window = messages[start:start + limit]
    if not window:
        return ToolOutcome.success(f'That chat has {total} messages; nothing at {start}.')
    names = await _agent_names([session])
    agent = names.get(str(session.agent_id or ''), 'agent')
    body = []
    for i, m in enumerate(window, start=start):
        who = 'Person' if m['role'] == 'user' else (m.get('name') or agent)
        text = m['content']
        if len(text) > MESSAGE_MAX_CHARS:
            text = text[:MESSAGE_MAX_CHARS] + ' …[cut]'
        body.append(f'[{i}] {who}: {text}')
    head = f'"{_title(messages)}" with {agent}, {_when(session)}. Messages {start}-{start + len(window) - 1} of {total}:'
    return ToolOutcome.success(head + '\n' + '\n'.join(body))
