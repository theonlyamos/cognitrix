"""A small row per chat for the chat list.

A session keeps its whole history in one value, so listing chats by loading
sessions means loading every message of every chat. The list reads these rows
instead: title, flags, counts and times, written whenever a session is saved.

Rows are a cache of the session, never the source of truth. Access is still
decided by the ownership bindings; a row only says what to show.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.log')

KIND_AGENT, KIND_TEAM, KIND_SKIP, KIND_MARKER = 'agent', 'team', 'skip', 'marker'


class ChatIndex(Model):
    session_id: str
    user_id: str = ''
    kind: str = KIND_AGENT
    """agent, team, skip (a session that is not a chat, e.g. a task step) or
    marker (bookkeeping: this person's older chats have been indexed)."""
    agent_id: str = ''
    team_id: str = ''
    title: str = ''
    """Derived from the first message."""
    custom_title: str = ''
    pinned: bool = False
    archived: bool = False
    incognito: bool = False
    message_count: int = 0
    started: str = ''
    changed: str = ''
    """The session's updated_at, ISO."""
    started_by: dict[str, Any] | None = None

    @field_validator('user_id', 'agent_id', 'team_id', 'title', 'custom_title', 'started', 'changed', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)

    @field_validator('pinned', 'archived', 'incognito', mode='before')
    @classmethod
    def _flag(cls, value):
        return bool(value)

    @field_validator('message_count', mode='before')
    @classmethod
    def _count(cls, value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @field_validator('started_by', mode='before')
    @classmethod
    def _started_by(cls, value):
        if isinstance(value, str):
            import json

            try:
                value = json.loads(value)
            except ValueError:
                return None
        return value if isinstance(value, dict) else None


# Per-person change counters, so a chat list can ask "anything new?" cheaply
# instead of re-reading every row. In memory: a restart starts a new epoch,
# which reads as a change and makes lists re-read once.
_EPOCH = uuid.uuid4().hex[:8]
_VERSIONS: dict[str, int] = {}


def _bump(user_id: str) -> None:
    if user_id:
        _VERSIONS[user_id] = _VERSIONS.get(user_id, 0) + 1


def version(user_id: str) -> str:
    """Changes whenever a chat of this person is added, changed or removed."""
    return f'{_EPOCH}-{_VERSIONS.get(str(user_id), 0)}'


def _marker_id(user_id: str) -> str:
    return f'indexed:{user_id}'


async def _owner_of(session) -> str:
    owner = str(getattr(session, 'user_id', None) or '')
    if owner:
        return owner
    from cognitrix.session_ownership import SessionOwnership

    binding = await SessionOwnership.find_one({'session_id': str(session.id)})
    return str(getattr(binding, 'user_id', '') or '') if binding is not None else ''


def _fields(session, owner: str) -> dict[str, Any]:
    from cognitrix.api.routes.sessions import _session_title, _started_by
    from cognitrix.teams import group_chat

    chat = session.chat or []
    team = bool(getattr(session, 'group_chat', None)) and bool(session.team_id)
    changed = getattr(session, 'updated_at', None)
    return {
        'session_id': str(session.id),
        'user_id': owner,
        'kind': KIND_TEAM if team else KIND_AGENT,
        'agent_id': str(session.agent_id or ''),
        'team_id': str(session.team_id or ''),
        'title': group_chat.chat_title(chat) if team else _session_title(chat),
        'custom_title': (getattr(session, 'title', None) or '').strip(),
        'pinned': bool(getattr(session, 'pinned', False)),
        'archived': bool(getattr(session, 'archived', False)),
        'incognito': bool(getattr(session, 'incognito', False)),
        'message_count': len(chat),
        'started': str(session.datetime or ''),
        'changed': changed.isoformat() if hasattr(changed, 'isoformat') else str(changed or ''),
        'started_by': _started_by(session),
    }


async def _upsert(fields: dict[str, Any]) -> None:
    row = await ChatIndex.find_one({'session_id': fields['session_id']})
    if row is None:
        await ChatIndex(**fields).save()
    else:
        await ChatIndex.update_one({'id': row.id}, fields)
        if row.user_id and row.user_id != fields.get('user_id'):
            _bump(row.user_id)
    _bump(str(fields.get('user_id') or ''))


async def record(session) -> None:
    """Write or refresh the row for a saved session. Never raises."""
    try:
        if not getattr(session, 'id', None):
            return
        if session.task_id or session.run_id:
            return
        owner = await _owner_of(session)
        if not owner:
            return
        await _upsert(_fields(session, owner))
    except Exception:
        logger.debug('Could not index chat %s', getattr(session, 'id', None), exc_info=True)


async def refresh(session_id: str) -> None:
    try:
        from cognitrix.sessions.base import Session

        session = await Session.get(str(session_id))
        if session is not None:
            await record(session)
    except Exception:
        logger.debug('Could not refresh chat index for %s', session_id, exc_info=True)


async def forget(session_id: Any) -> None:
    try:
        for row in await ChatIndex.find({'session_id': str(session_id)}):
            _bump(row.user_id)
        await ChatIndex.delete_many({'session_id': str(session_id)})
    except Exception:
        logger.debug('Could not drop chat index row for %s', session_id, exc_info=True)


async def rows_for(user_id: str, owned_ids: list[str]) -> list[ChatIndex]:
    """This person's rows, after indexing any of their chats that have none
    (chats from before the index existed)."""
    from cognitrix.sessions.base import Session

    rows = list(await ChatIndex.find({'user_id': str(user_id)}))
    known = {row.session_id for row in rows}
    added = False

    # Chats bound to this person without a row yet.
    for session_id in owned_ids:
        if session_id in known:
            continue
        session = await Session.get(session_id)
        if session is None:
            continue
        if session.task_id or session.run_id:
            await _upsert({'session_id': session_id, 'user_id': str(user_id), 'kind': KIND_SKIP})
        else:
            await _upsert(_fields(session, str(user_id)))
        added = True

    # Group chats have no binding; look through this person's sessions once.
    if _marker_id(str(user_id)) not in known:
        for session in await Session.find({'user_id': str(user_id)}):
            if session.group_chat and session.team_id and str(session.id) not in known:
                await _upsert(_fields(session, str(user_id)))
        await _upsert({'session_id': _marker_id(str(user_id)), 'user_id': str(user_id), 'kind': KIND_MARKER})
        added = True

    if added:
        rows = list(await ChatIndex.find({'user_id': str(user_id)}))
    return [row for row in rows if row.kind in (KIND_AGENT, KIND_TEAM)]
