"""Follow Up Later: an agent comes back to the same chat at a set time.

The Follow Up Later tool stores a ``FollowUp``. When it is due, the scheduler's
automation tick hands it to the chat the same way news from a helper agent
arrives (``sessions/agent_chats.deliver_report``): the agent gets a turn in
that chat, checks what it said it would, and replies. When nothing needs the
person it says so in one line and no alert goes out.

A busy chat is retried shortly after. A follow-up that can't be delivered for
FALLBACK_AFTER_HOURS becomes an Inbox alert instead.
"""
from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from odbms import Model
from pydantic import field_validator

logger = logging.getLogger('cognitrix.log')

FMT = '%Y-%m-%d %H:%M:%S'
MIN_MINUTES = 5
MAX_MINUTES = 30 * 24 * 60
MAX_PENDING_PER_USER = 20
WHAT_MAX = 300
WHY_MAX = 300
RETRY_SECONDS = 30
FALLBACK_AFTER_HOURS = 6
NOTHING_NEW = 'NOTHING NEW'

PENDING = 'pending'
DONE = 'done'
CANCELLED = 'cancelled'
FAILED = 'failed'


class FollowUp(Model):
    user_id: str
    agent_id: str
    session_id: str
    what: str
    """What to check, in the agent's words."""
    why: str = ''
    due_at: str
    """Naive UTC 'YYYY-MM-DD HH:MM:SS'."""
    next_at: str = ''
    """When to try again after a busy chat; '' means due_at."""
    status: str = PENDING
    delivered_at: str = ''
    outcome: str = ''

    @field_validator('why', 'next_at', 'delivered_at', 'outcome', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)


MODELS = (FollowUp,)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def pending_for(user_id: str, session_id: str | None = None) -> list[FollowUp]:
    query: dict[str, Any] = {'user_id': str(user_id), 'status': PENDING}
    if session_id:
        query['session_id'] = str(session_id)
    try:
        rows = list(await FollowUp.find(query) or [])
    except Exception as exc:
        if 'no such table' in str(exc).lower():
            return []
        raise
    rows.sort(key=lambda r: r.due_at)
    return rows


async def schedule(user_id: str, agent_id: str, session_id: str, *, minutes: int, what: str,
                   why: str = '', now: datetime | None = None) -> FollowUp:
    """Store a follow-up. Raises ValueError with words for the agent."""
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError(f'minutes must be between {MIN_MINUTES} and {MAX_MINUTES} (30 days)')
    what = ' '.join(str(what or '').split())[:WHAT_MAX]
    if not what:
        raise ValueError('Say what to check when you come back.')
    if len(await pending_for(user_id)) >= MAX_PENDING_PER_USER:
        raise ValueError(f'This person already has {MAX_PENDING_PER_USER} follow-ups waiting. '
                         'Cancel one in the chat it belongs to first.')
    due = (now or utcnow()) + timedelta(minutes=minutes)
    row = FollowUp(user_id=str(user_id), agent_id=str(agent_id), session_id=str(session_id), what=what,
                   why=' '.join(str(why or '').split())[:WHY_MAX], due_at=due.strftime(FMT))
    await row.save()
    return row


async def cancel(row: FollowUp) -> None:
    await FollowUp.update_one({'id': row.id}, {'status': CANCELLED})


def report_text(row: FollowUp) -> str:
    from cognitrix.sessions.agent_chats import REPORT_PREFIX

    why = f' Why: {row.why}' if row.why else ''
    return (
        f'{REPORT_PREFIX}Follow-up you scheduled in this chat is due now: {row.what}.{why}\n'
        'Check it now with your tools. If something needs the person, tell them in a few lines. '
        f'If nothing changed or nothing needs them, reply with only "{NOTHING_NEW}" and a few words.'
    )


def quiet_reply(reply: str) -> bool:
    return str(reply or '').strip().upper().startswith(NOTHING_NEW)


async def _alert(row: FollowUp, title: str, message: str) -> None:
    from cognitrix.tasks.automation import create_notification

    await create_notification(row.user_id, title, message, session_id=row.session_id,
                              link=f'/home?chat={row.session_id}', proactive=True)


async def _deliver(row: FollowUp, now: datetime) -> str:
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    agent = await find_visible_agent(row.user_id, agent_id=row.agent_id)
    session = await Session.get(row.session_id)
    if agent is None or session is None or str(session.user_id or '') != row.user_id \
            or str(session.agent_id or '') != row.agent_id:
        return 'gone'

    async def done(status: str, reply: str) -> None:
        if status != 'complete' or quiet_reply(reply):
            return
        await _alert(row, f'{agent.name} followed up', reply[:300] or 'Open the chat to see it.')

    kwargs: dict[str, Any] = {'sender': 'Follow-up', 'wait_seconds': 0}
    if 'on_done' in inspect.signature(agent_chats.deliver_report).parameters:
        kwargs['on_done'] = done
    return await agent_chats.deliver_report(row.user_id, row.agent_id, row.session_id, report_text(row), **kwargs)


async def deliver_due(now: datetime | None = None) -> int:
    """Hand due follow-ups to their chats. Returns how many were delivered."""
    now = now or utcnow()
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    stamp = now.strftime(FMT)
    try:
        rows = list(await FollowUp.find({'status': PENDING}) or [])
    except Exception as exc:
        if 'no such table' in str(exc).lower():
            return 0
        raise
    delivered = 0
    for row in rows:
        if (row.next_at or row.due_at) > stamp:
            continue
        try:
            outcome = await _deliver(row, now)
        except Exception as exc:
            logger.exception('Could not deliver follow-up %s', row.id)
            outcome = f'error: {exc}'[:200]
        if outcome in ('steered', 'started', 'paused'):
            await FollowUp.update_one({'id': row.id}, {'status': DONE, 'delivered_at': stamp, 'outcome': outcome})
            delivered += 1
            continue
        if outcome == 'gone':
            await FollowUp.update_one({'id': row.id}, {'status': CANCELLED, 'outcome': 'chat or agent gone'})
            continue
        due = datetime.strptime(row.due_at, FMT)
        if now - due > timedelta(hours=FALLBACK_AFTER_HOURS):
            await FollowUp.update_one({'id': row.id}, {'status': FAILED, 'outcome': outcome or 'busy'})
            await _alert(row, 'A follow-up is waiting for you',
                         f'Your assistant planned to check: {row.what}. Open the chat to pick it up.')
            continue
        retry = (now + timedelta(seconds=RETRY_SECONDS)).strftime(FMT)
        await FollowUp.update_one({'id': row.id}, {'next_at': retry})
    return delivered


def public(row: FollowUp) -> dict[str, Any]:
    return {'id': str(row.id), 'agent_id': row.agent_id, 'session_id': row.session_id, 'what': row.what,
            'why': row.why, 'due_at': row.due_at, 'status': row.status}
