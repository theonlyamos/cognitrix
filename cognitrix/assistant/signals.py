"""What is going on for a person, worked out from the database without a model.

Three things use it:

* the "what's going on" note added to chats with the main assistant
  (``awareness_block``), so "prep me for my next call" or "what did I miss?"
  just work;
* the free change check in front of every check-in (``fingerprint``): when
  nothing here changed since the last check-in, no model is called;
* stuck-work alerts (``stalled``), which go into the brief and, when they
  block work, into one alert.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger('cognitrix.log')

FAILED_IN_A_ROW = 2
STUCK_RUN_HOURS = 2
WAITING_APPROVAL_HOURS = 2
WAITING_OFFER_DAYS = 3
COMING_DUE_DAYS = 3
MAX_ITEMS = 8
AWARENESS_MAX_CHARS = 1_200
AWARENESS_CACHE_S = 15 * 60


@dataclass
class Item:
    kind: str
    """meeting, approval, offer, failing, stuck, due, followup, alert, mail"""
    title: str
    detail: str = ''
    key: str = ''
    """Stable id for dedupe and the change check."""
    urgent: bool = False
    blocking: bool = False
    href: str = ''


@dataclass
class Situation:
    local_time: str = ''
    meetings: list[Item] = field(default_factory=list)
    waiting: list[Item] = field(default_factory=list)
    """Things waiting on the person: approvals, offers."""
    stalled: list[Item] = field(default_factory=list)
    due: list[Item] = field(default_factory=list)
    """Open loops coming due (memories with a valid-until date) and follow-ups."""
    unread_alerts: int = 0
    activity: str = ''
    """A marker that changes when the person's chats change."""

    def items(self) -> list[Item]:
        return [*self.meetings, *self.waiting, *self.stalled, *self.due]

    def as_dict(self) -> dict[str, Any]:
        return {
            'local_time': self.local_time,
            'meetings': [asdict(i) for i in self.meetings],
            'waiting': [asdict(i) for i in self.waiting],
            'stalled': [asdict(i) for i in self.stalled],
            'due': [asdict(i) for i in self.due],
            'unread_alerts': self.unread_alerts,
        }


def _at(value: Any) -> datetime | None:
    """A stored timestamp as naive local time (how odbms writes created_at)."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    text = str(value).replace('T', ' ')[:19]
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def _utc_at(value: Any) -> datetime | None:
    """A stored naive-UTC string ('YYYY-MM-DD HH:MM:SS') as aware UTC."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).replace('T', ' ')[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
    except ValueError:
        return None


def _ago(then: datetime | None, now: datetime) -> timedelta:
    return now - then if then else timedelta(0)


def _hours(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours >= 48:
        return f'{hours // 24} days'
    if hours >= 1:
        return f'{hours} h'
    return f'{max(1, int(delta.total_seconds() // 60))} min'


async def _meetings(user_id: str, settings: Any, now_utc: datetime) -> list[Item]:
    from cognitrix.assistant.proactivity import local_now

    try:
        from cognitrix.meetings.calendar_calls import CalendarCall
        rows = await CalendarCall.find({'user_id': str(user_id)}) or []
    except Exception:
        return []
    upcoming = []
    for row in rows:
        start = _utc_at(row.starts_at)
        if start is None or start < now_utc - timedelta(minutes=10) or start > now_utc + timedelta(hours=24):
            continue
        upcoming.append((start, row))
    upcoming.sort(key=lambda p: p[0])
    items = []
    for start, row in upcoming[:3]:
        local = local_now(settings, start)
        minutes = int((start - now_utc).total_seconds() // 60)
        soon = 0 <= minutes <= 60
        when = f'in {minutes} min' if soon else local.strftime('%a %H:%M')
        items.append(Item('meeting', row.title or 'A call', f'{when}' + (f', organised by {row.organizer}'
                                                                           if row.organizer else ''),
                          key=f'meeting:{row.event_id}:{row.starts_at}', urgent=soon, href='/meetings'))
    return items


async def _waiting(user_id: str, now: datetime) -> tuple[list[Item], list[Item]]:
    """(waiting on the person, stalled because of it)."""
    waiting: list[Item] = []
    stalled: list[Item] = []
    try:
        from cognitrix.tasks import approvals

        rows = await approvals.TaskApproval.find({'user_id': str(user_id), 'status': approvals.PENDING}) or []
    except Exception:
        rows = []
    for row in rows:
        age = _ago(_at(getattr(row, 'created_at', None)), now)
        item = Item('approval', f'Approve {row.tool_name}', f'waiting {_hours(age)}',
                    key=f'approval:{row.id}', href='/inbox')
        if age >= timedelta(hours=WAITING_APPROVAL_HOURS):
            item.kind, item.blocking = 'stuck', True
            item.title = f'A task has waited {_hours(age)} for you to approve {row.tool_name}'
            stalled.append(item)
        else:
            waiting.append(item)
    try:
        from cognitrix.safety.approval_alerts import pending_chat_approvals

        for entry in pending_chat_approvals(str(user_id)):
            waiting.append(Item('approval', f"Approve {entry.get('tool') or 'an action'} in a chat",
                                key=f"chat-approval:{entry.get('id')}", href='/inbox'))
    except Exception:
        pass
    try:
        from cognitrix.tasks import standing

        offers = [o for o in await standing.offers_for(str(user_id)) if o.status == standing.OFFER_PENDING]
    except Exception:
        offers = []
    for offer in offers:
        age = _ago(_at(getattr(offer, 'created_at', None)), now)
        item = Item('offer', f'{offer.agent_name or "Your assistant"} offers: {offer.title}',
                    f'waiting {_hours(age)}', key=f'offer:{offer.id}', href='/inbox')
        (stalled if age >= timedelta(days=WAITING_OFFER_DAYS) else waiting).append(item)
    return waiting, stalled


async def _tasks(user_id: str, now: datetime) -> list[Item]:
    """Tasks that failed twice in a row, and runs stuck far past normal."""
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    items: list[Item] = []
    try:
        tasks = [t for t in await Task.find({'owner_id': str(user_id)}) or [] if not t.deleted_at]
    except Exception:
        return []
    for task in tasks[:200]:
        try:
            runs = list(await TaskRun.find({'task_id': str(task.id)}) or [])
        except Exception:
            continue
        if not runs:
            continue
        runs.sort(key=lambda r: str(r.started_at or r.queued_at or getattr(r, 'created_at', '') or ''))
        recent = runs[-FAILED_IN_A_ROW:]
        status = lambda r: getattr(r.status, 'value', r.status)  # noqa: E731
        if len(recent) == FAILED_IN_A_ROW and all(status(r) == TaskRunStatus.FAILED.value for r in recent):
            last = recent[-1]
            reason = str(last.error or '').strip().splitlines()[0][:140] if last.error else ''
            items.append(Item('failing', f'"{task.title}" failed {FAILED_IN_A_ROW} times in a row',
                              reason, key=f'failing:{task.id}:{last.id}', blocking=bool(task.schedule_enabled),
                              href=f'/tasks/{task.id}'))
        latest = runs[-1]
        if status(latest) == TaskRunStatus.RUNNING.value:
            started = _utc_at(latest.started_at)
            if started and datetime.now(UTC) - started > timedelta(hours=STUCK_RUN_HOURS):
                items.append(Item('stuck', f'"{task.title}" has been running for '
                                           f'{_hours(datetime.now(UTC) - started)}',
                                  key=f'stuck:{latest.id}', href=f'/tasks/{task.id}'))
    return items


async def _due(user_id: str, settings: Any, now_utc: datetime) -> list[Item]:
    from cognitrix.assistant.proactivity import local_now
    from cognitrix.models.memory import Memory

    today = local_now(settings, now_utc).date()
    items: list[Item] = []
    try:
        rows = await Memory.find({'user_id': str(user_id)}) or []
    except Exception:
        rows = []
    for row in rows:
        if row.pending or not row.valid_until:
            continue
        try:
            until = datetime.strptime(row.valid_until, '%Y-%m-%d').date()
        except ValueError:
            continue
        days = (until - today).days
        if 0 <= days <= COMING_DUE_DAYS:
            when = 'today' if days == 0 else 'tomorrow' if days == 1 else f'in {days} days'
            items.append(Item('due', row.content, f'until {row.valid_until} ({when})',
                              key=f'due:{row.id}:{row.valid_until}', urgent=days == 0))
    items.sort(key=lambda i: i.detail)
    try:
        from cognitrix.assistant.followups import pending_for

        for follow in await pending_for(str(user_id)):
            due = _utc_at(follow.due_at)
            when = local_now(settings, due).strftime('%a %H:%M') if due else ''
            items.append(Item('followup', follow.what, f'follow-up {when}', key=f'followup:{follow.id}',
                              href=f'/home?chat={follow.session_id}'))
    except Exception:
        logger.debug('Could not list follow-ups', exc_info=True)
    return items


async def _activity(user_id: str) -> str:
    try:
        from cognitrix.sessions.base import Session

        rows = await Session.find({'user_id': str(user_id)}) or []
    except Exception:
        return ''
    latest = max((str(getattr(r, 'updated_at', '') or '') for r in rows), default='')
    return f'{len(rows)}:{latest}'


async def situation(user_id: str, *, now: datetime | None = None, include_tasks: bool = True) -> Situation:
    from cognitrix.assistant.proactivity import get_settings, local_now

    now_utc = now or datetime.now(UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    local_naive_now = datetime.now()
    settings = await get_settings(user_id)
    out = Situation(local_time=local_now(settings, now_utc).strftime('%A %d %B %Y, %H:%M'))
    out.meetings = await _meetings(user_id, settings, now_utc)
    out.waiting, stalled = await _waiting(user_id, local_naive_now)
    out.stalled = stalled + (await _tasks(user_id, local_naive_now) if include_tasks else [])
    out.due = await _due(user_id, settings, now_utc)
    try:
        from cognitrix.tasks.automation import unread_notifications

        out.unread_alerts = len(await unread_notifications(str(user_id)))
    except Exception:
        out.unread_alerts = 0
    out.activity = await _activity(user_id)
    return out


def fingerprint(sit: Situation, extra: str = '') -> str:
    """Changes when anything a check-in would look at changed. Times of day don't count."""
    keys = sorted(i.key for i in sit.items())
    payload = json.dumps({'keys': keys, 'alerts': sit.unread_alerts, 'activity': sit.activity,
                          'extra': extra}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _lines(title: str, items: list[Item]) -> list[str]:
    if not items:
        return []
    out = [f'{title}:']
    for item in items[:MAX_ITEMS]:
        out.append(f'- {item.title}' + (f' ({item.detail})' if item.detail else ''))
    if len(items) > MAX_ITEMS:
        out.append(f'- and {len(items) - MAX_ITEMS} more')
    return out


def describe(sit: Situation) -> str:
    lines = [f"When this note was made: {sit.local_time} (the person's time)."]
    lines += _lines('Next meetings', sit.meetings)
    lines += _lines('Waiting on the person', sit.waiting)
    lines += _lines('Stuck or failing', sit.stalled)
    lines += _lines('Coming due', sit.due)
    if sit.unread_alerts:
        lines.append(f'Unread alerts in the Inbox: {sit.unread_alerts}.')
    if len(lines) == 1:
        lines.append('Nothing is waiting on the person right now.')
    return '\n'.join(lines)


AWARENESS_HEADER = (
    "## What's going on (from the app)\n"
    "Use it to understand requests like \"prep me for my next call\" or \"what did I miss?\". "
    "Don't recite it unprompted; mention an item only when it matters to what the person is doing. "
    "It was true when this chat turn started and can be a little out of date."
)

_AWARENESS_MAX = 512
_awareness_cache: dict[tuple[str, str], tuple[float, str, str]] = {}
"""(user, chat) -> (made at, content fingerprint, text)."""


async def awareness_block(user_id: str | None, agent: Any, session_id: str | None = None) -> str:
    """The "what's going on" note for a chat with the person's main assistant, or ''.

    A chat keeps the note it was given for AWARENESS_CACHE_S, and longer when
    nothing in it changed, so the system prompt (and the provider's prompt
    cache) stays the same across most turns."""
    from cognitrix.tasks import standing

    if not user_id or agent is None or not standing.settings_of(agent)['primary']:
        return ''
    owner = str(getattr(agent, 'owner_id', '') or '')
    if owner and owner != str(user_id):
        return ''
    key = (str(user_id), str(session_id or ''))
    cached = _awareness_cache.get(key)
    if cached and time.monotonic() - cached[0] < AWARENESS_CACHE_S:
        return cached[2]
    try:
        sit = await situation(str(user_id), include_tasks=True)
    except Exception:
        logger.debug('Could not build the awareness note', exc_info=True)
        return cached[2] if cached else ''
    print_ = fingerprint(sit)
    if cached and cached[1] == print_:
        _awareness_cache[key] = (time.monotonic(), print_, cached[2])
        return cached[2]
    text = AWARENESS_HEADER + '\n' + describe(sit)
    if len(text) > AWARENESS_MAX_CHARS:
        text = text[:AWARENESS_MAX_CHARS].rsplit('\n', 1)[0] + '\n- (more in the Inbox)'
    _awareness_cache[key] = (time.monotonic(), print_, text)
    while len(_awareness_cache) > _AWARENESS_MAX:
        _awareness_cache.pop(next(iter(_awareness_cache)))
    return text


def forget_awareness(user_id: str | None = None) -> None:
    if user_id is None:
        _awareness_cache.clear()
        return
    for key in [k for k in _awareness_cache if k[0] == str(user_id)]:
        _awareness_cache.pop(key, None)
