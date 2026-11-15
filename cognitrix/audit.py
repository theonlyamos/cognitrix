"""Audit log: who, and which of their agents, did what on this server.

Recorded:
- every tool call an agent makes (ran, failed, blocked or denied), with a short,
  secret-free summary of its arguments;
- approvals a person or the reviewer gave or refused;
- sign-ins, and failed sign-ins;
- admin changes to people (role, disable, password, caps, removal, invites).

Rows are kept for ``COGNITRIX_AUDIT_DAYS`` days (90 by default; 0 turns the log
off). Each row carries its UTC day so a page reads one day at a time with
plain equality lookups, which every database odbms supports.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.audit')

KINDS = ('tool', 'approval', 'sign_in', 'people', 'settings')
DETAIL_MAX = 400
_pending: set[asyncio.Task] = set()
_pruned_on: dict[str, str] = {'day': ''}


class AuditEvent(Model):
    at: int
    """Unix time."""
    day: str
    """UTC 'YYYY-MM-DD' of ``at``."""
    kind: str
    action: str
    user_id: str = ''
    """Principal key of the person it was done by or for."""
    actor: str = ''
    """Their name or email at the time, so the row still reads after they leave."""
    agent_id: str = ''
    agent_name: str = ''
    target: str = ''
    detail: str = ''
    outcome: str = ''
    """ok, failed, blocked, denied."""
    risk: str = ''
    session_id: str = ''

    @field_validator('user_id', 'actor', 'agent_id', 'agent_name', 'target', 'detail', 'outcome', 'risk',
                     'session_id', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)


def retention_days() -> int:
    try:
        return max(0, int(os.environ.get('COGNITRIX_AUDIT_DAYS', '90')))
    except ValueError:
        return 90


def enabled() -> bool:
    return retention_days() > 0


def _day(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime('%Y-%m-%d')


def summarize(args: Any, limit: int = DETAIL_MAX) -> str:
    """A short, secret-free line from a tool call's arguments."""
    from cognitrix.common.security import redact_secrets

    if not args:
        return ''
    try:
        clean = redact_secrets(args) if isinstance(args, (dict, list)) else args
        if isinstance(clean, dict):
            parts = []
            for key, value in clean.items():
                if not isinstance(value, (str, int, float, bool)) and value is not None:
                    value = json.dumps(value, default=str)
                text = ' '.join(str(value).split())
                if len(text) > 120:
                    text = text[:117] + '...'
                parts.append(f'{key}={text}')
            line = ', '.join(parts)
        else:
            line = ' '.join(str(clean).split())
    except Exception:
        return ''
    return line if len(line) <= limit else line[: limit - 3] + '...'


_names: dict[str, str] = {}


async def _actor_name(user_id: str) -> str:
    if not user_id:
        return ''
    if user_id in _names:
        return _names[user_id]
    name = ''
    try:
        from cognitrix.models import User

        user = await User.get(user_id)
        if user is None and '@' in user_id:
            user = await User.find_one({'email': user_id})
        if user is not None:
            name = str(user.email or user.name or '')
    except Exception:
        name = ''
    if len(_names) > 500:
        _names.clear()
    _names[user_id] = name
    return name


async def add(kind: str, action: str, *, user_id: str = '', actor: str = '', agent_id: str = '',
              agent_name: str = '', target: str = '', detail: str = '', outcome: str = '', risk: str = '',
              session_id: str = '') -> None:
    if not enabled():
        return
    now = time.time()
    user_id = str(user_id or '')
    row = AuditEvent(
        at=int(now), day=_day(now), kind=kind, action=action[:120], user_id=user_id,
        actor=(actor or await _actor_name(user_id))[:200], agent_id=str(agent_id or ''),
        agent_name=str(agent_name or '')[:120], target=str(target or '')[:300],
        detail=str(detail or '')[:DETAIL_MAX], outcome=outcome, risk=risk, session_id=str(session_id or ''),
    )
    await row.save()
    await _prune_daily()


def record(kind: str, action: str, **fields: Any) -> None:
    """Write a row without waiting. Never raises."""
    if not enabled():
        return

    async def _save() -> None:
        try:
            await add(kind, action, **fields)
        except Exception:
            logger.debug('Could not write an audit row', exc_info=True)

    try:
        task = asyncio.get_running_loop().create_task(_save())
    except RuntimeError:
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def flush() -> None:
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


def record_tool(agent: Any, tool_name: str, args: Any, outcome: str, *, risk: str = '', note: str = '') -> None:
    """One agent tool call, attributed to the person whose turn it ran in."""
    from cognitrix import usage

    owner = usage.current_owner() or {}
    detail = summarize(args)
    if note:
        detail = f'{detail} | {note}' if detail else note
    record(
        'tool', str(tool_name), user_id=owner.get('user_id', ''),
        agent_id=str(getattr(agent, 'id', '') or owner.get('agent_id', '') or ''),
        agent_name=str(getattr(agent, 'name', '') or ''), detail=detail, outcome=outcome, risk=risk,
    )


async def _prune_daily() -> None:
    today = _day(time.time())
    if _pruned_on['day'] == today:
        return
    _pruned_on['day'] = today
    keep = retention_days()
    start = datetime.now(UTC).date() - timedelta(days=keep)
    # Look back a further 60 days each time, which also covers a server that
    # was off for a while.
    for back in range(60):
        day = (start - timedelta(days=back)).strftime('%Y-%m-%d')
        try:
            await AuditEvent.delete_many({'day': day})
        except Exception:
            logger.debug('Could not prune audit rows for %s', day, exc_info=True)
            return


def project(row: AuditEvent) -> dict[str, Any]:
    return {
        'id': str(row.id), 'at': int(row.at), 'kind': row.kind, 'action': row.action,
        'user_id': row.user_id, 'actor': row.actor, 'agent_id': row.agent_id, 'agent_name': row.agent_name,
        'target': row.target, 'detail': row.detail, 'outcome': row.outcome, 'risk': row.risk,
    }


async def page(*, user_id: str | None = None, agent_id: str = '', kind: str = '', outcome: str = '',
               text: str = '', before: str = '', limit: int = 100, max_days: int = 31) -> dict[str, Any]:
    """Newest rows first, one day at a time from ``before`` (a day, exclusive)
    or today. ``user_id`` None means everyone (admins)."""
    limit = max(1, min(int(limit or 100), 500))
    if before:
        try:
            cursor = datetime.strptime(before, '%Y-%m-%d').date() - timedelta(days=1)
        except ValueError:
            cursor = datetime.now(UTC).date()
    else:
        cursor = datetime.now(UTC).date()
    oldest = datetime.now(UTC).date() - timedelta(days=max(retention_days(), 1))
    needle = text.strip().lower()
    rows: list[AuditEvent] = []
    scanned = 0
    last_day = ''
    while cursor >= oldest and scanned < max_days and len(rows) < limit:
        day = cursor.strftime('%Y-%m-%d')
        conditions: dict[str, Any] = {'day': day}
        if user_id is not None:
            conditions['user_id'] = str(user_id)
        if kind:
            conditions['kind'] = kind
        found = await AuditEvent.find(conditions)
        for row in found:
            if agent_id and row.agent_id != agent_id:
                continue
            if outcome and row.outcome != outcome:
                continue
            if needle and needle not in ' '.join(
                    (row.action, row.actor, row.agent_name, row.target, row.detail)).lower():
                continue
            rows.append(row)
        last_day = day
        scanned += 1
        cursor -= timedelta(days=1)
    rows.sort(key=lambda r: (-int(r.at or 0), str(r.id)))
    more = cursor >= oldest
    return {
        'events': [project(r) for r in rows],
        # Days are read whole, so a page can run past ``limit``; the next page
        # starts at the day before the last one read.
        'next_before': last_day if more and last_day else None,
        'retention_days': retention_days(),
    }
