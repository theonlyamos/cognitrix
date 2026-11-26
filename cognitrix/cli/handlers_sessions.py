"""`cognitrix sessions`: list, read, export and delete saved chats.

The ids it prints are what `cognitrix --resume ID` takes.

    cognitrix sessions                    # newest first
    cognitrix sessions --agent Coder --limit 5 --json
    cognitrix sessions show ID            # the transcript
    cognitrix sessions show ID --json     # every saved message, raw
    cognitrix sessions delete ID
"""
from __future__ import annotations

import sys
from argparse import Namespace
from typing import Any

from .utils import print_json, print_table


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get('text', '') if isinstance(p, dict) else str(p) for p in content]
        return '\n'.join(p for p in parts if p)
    return '' if content is None else str(content)


def first_user_message(chat: list[dict[str, Any]] | None) -> str:
    for message in chat or []:
        if isinstance(message, dict) and str(message.get('role', '')).lower() == 'user':
            text = _text(message.get('content')).strip()
            if text:
                return text
    return ''


def session_summary(session: Any, agent_names: dict[str, str] | None = None) -> dict[str, Any]:
    chat = session.chat or []
    title = session.title or session.auto_title or first_user_message(chat)
    return {
        'id': str(session.id),
        'agent_id': session.agent_id,
        'agent': (agent_names or {}).get(str(session.agent_id), ''),
        'title': ' '.join(title.split())[:80],
        'messages': sum(1 for m in chat if isinstance(m, dict) and str(m.get('role', '')).lower() in ('user', 'assistant')),
        'updated_at': str(getattr(session, 'updated_at', '') or session.datetime),
    }


async def saved_sessions(agent: str = '', limit: int = 0) -> list[dict[str, Any]]:
    from cognitrix.agents import Agent
    from cognitrix.sessions.base import Session

    agents = await Agent.all()
    names = {str(a.id): a.name for a in agents}
    sessions = await Session.all()
    rows = [session_summary(s, names) for s in sessions
            if not getattr(s, 'task_id', None) and not getattr(s, 'run_id', None)]
    if agent:
        wanted = agent.lower()
        rows = [r for r in rows if r['agent'].lower() == wanted or r['agent_id'] == agent]
    rows.sort(key=lambda r: r['updated_at'], reverse=True)
    return rows[:limit] if limit > 0 else rows


async def list_saved_sessions(as_json: bool = False, agent: str = '', limit: int = 0) -> None:
    rows = await saved_sessions(agent, limit)
    if as_json:
        print_json(rows)
        return
    print_table([[r['id'], r['agent'], r['messages'], r['updated_at'][:19], r['title']] for r in rows],
                ['Session ID', 'Agent', 'Msgs', 'Updated', 'Title'])


async def find_session(session_id: str):
    """A saved session by its id, or by a unique start of it."""
    from cognitrix.sessions.base import Session

    session = await Session.find_one({'id': session_id})
    if session or len(session_id) < 4:
        return session
    matches = [s for s in await Session.all() if str(s.id).startswith(session_id)]
    return matches[0] if len(matches) == 1 else None


def _print_transcript(session: Any) -> None:
    for message in session.chat or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get('role', '')).lower()
        if role not in ('user', 'assistant'):
            continue
        text = _text(message.get('content')).strip()
        if not text and message.get('tool_calls'):
            names = ', '.join(str(c.get('name')) for c in message['tool_calls'] if isinstance(c, dict))
            text = f"[called {names}]"
        if text:
            label = 'You' if role == 'user' else (message.get('name') or 'Assistant')
            print(f"{label}: {text}\n")


async def manage_sessions(args: Namespace) -> None:
    from .core import run_configuration

    await run_configuration()
    action = getattr(args, 'sessions_action', None) or 'list'
    as_json = getattr(args, 'json', False)
    if action == 'list':
        await list_saved_sessions(as_json, args.agent, args.limit)
        return
    session = await find_session(args.session_id)
    if session is None:
        print(f"No saved session {args.session_id}.", file=sys.stderr)
        sys.exit(1)
    if action == 'show':
        if as_json:
            from cognitrix.agents import Agent

            agent = await Agent.find_one({'id': session.agent_id}) if session.agent_id else None
            names = {str(agent.id): agent.name} if agent else {}
            print_json({**session_summary(session, names), 'chat': session.chat or []})
        else:
            _print_transcript(session)
        return
    if action == 'delete':
        from cognitrix.artifacts import delete_session_artifacts
        from cognitrix.sessions.base import Session

        await Session.delete(str(session.id))
        await delete_session_artifacts(str(session.id))
        print(f"Deleted session {session.id}")


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser('sessions', help='List, read or delete saved chats (ids work with --resume)')
    parser.add_argument('--agent', default='', help='Only this agent\'s chats (name or id)')
    parser.add_argument('--limit', type=int, default=0, help='Show at most this many (newest first)')
    parser.add_argument('--json', action='store_true', help='Print JSON')
    inner = parser.add_subparsers(dest='sessions_action')
    import argparse

    listing = inner.add_parser('list', help='List saved chats, newest first')
    listing.add_argument('--json', action='store_true', default=argparse.SUPPRESS, help='Print JSON')
    show = inner.add_parser('show', help='Print a chat\'s messages')
    show.add_argument('session_id')
    show.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                      help='Print every saved message as JSON')
    delete = inner.add_parser('delete', help='Delete a chat')
    delete.add_argument('session_id')
    parser.set_defaults(func=manage_sessions)
