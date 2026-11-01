"""Always-on agents, built on the task scheduler.

A task becomes an automation once it has any of these:

* notes that every run reads and agents can rewrite (``Task.notes``);
* an agent-requested wake-up (``Task.wake_*``), armed by the Schedule Next
  Check tool and fired by the scheduler loop;
* webhook triggers (``TaskTrigger``): each has a secret URL, and every POST
  to it is stored as a ``TriggerEvent`` and handed to one run;
* owner notifications (``AgentNotification``) written by the Notify Me tool
  and shown in the Inbox.

Wake-ups and webhook runs are started by something other than the owner, so
they count against a per-task rolling 24 hour cap. User-defined schedules do
not.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from odbms import Model
from pydantic import validator

logger = logging.getLogger('cognitrix.log')

FMT = '%Y-%m-%d %H:%M:%S'

WAKE_FIELDS = (
    'wake_pending', 'wake_at', 'wake_reason',
    'wake_requested_by', 'wake_authority_kind', 'wake_authority_id',
)
NOTES_MAX_CHARS = 8_000
REASON_MAX_CHARS = 500
TITLE_MAX_CHARS = 120
MESSAGE_MAX_CHARS = 2_000
WAKE_MIN_MINUTES = 1
WAKE_MAX_MINUTES = 30 * 24 * 60
EVENT_MAX_BYTES = 16 * 1024
MAX_PENDING_EVENTS = 20
MAX_TRIGGERS_PER_TASK = 10
MAX_RUNS_PER_DAY_LIMIT = 1_440
DISPATCH_CLAIM_SECONDS = 300
AUTOMATION_TRIGGER_KINDS = ('wake', 'webhook')

PENDING = 'pending'
DISPATCHING = 'dispatching'
DISPATCHED = 'dispatched'
DROPPED = 'dropped'


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def utcnow_str() -> str:
    return utcnow().strftime(FMT)


def default_max_runs_per_day() -> int:
    try:
        value = int(os.getenv('COGNITRIX_AUTOMATION_MAX_RUNS_PER_DAY', '48'))
    except ValueError:
        return 48
    return max(1, min(value, MAX_RUNS_PER_DAY_LIMIT))


def validate_automation_fields(task: Any) -> str | None:
    """Rejection reason for user-supplied automation fields, or None."""
    notes = getattr(task, 'notes', None)
    if notes is not None and len(notes) > NOTES_MAX_CHARS:
        return f'notes must be at most {NOTES_MAX_CHARS} characters'
    cap = getattr(task, 'max_runs_per_day', None)
    if cap is not None and not (1 <= int(cap) <= MAX_RUNS_PER_DAY_LIMIT):
        return f'max_runs_per_day must be between 1 and {MAX_RUNS_PER_DAY_LIMIT}'
    return None


def _clip(text: str | None, limit: int) -> str:
    return str(text or '').strip()[:limit]


# --- models ------------------------------------------------------------------

class TaskTrigger(Model):
    """A secret URL that starts a run of its task when something POSTs to it."""

    task_id: str
    owner_id: str | None = None
    name: str = 'Webhook'
    kind: str = 'webhook'
    token_hash: str
    """sha256 of the URL secret. The secret itself is shown once, at creation."""
    token_hint: str = ''
    enabled: bool = True
    requested_by: str | None = None
    authority_kind: str | None = None
    authority_id: str | None = None
    """Authority of the owner who created the trigger; its runs use it."""
    last_fired_at: str | None = None
    fire_count: int = 0

    @validator('enabled', pre=True)
    def _null_enabled(cls, value):
        return True if value is None else value

    @validator('fire_count', pre=True)
    def _null_count(cls, value):
        return 0 if value is None else value


class TriggerEvent(Model):
    """One received webhook delivery, waiting for (or handled by) a run."""

    trigger_id: str
    task_id: str
    status: str = PENDING
    payload: str = ''
    content_type: str = ''
    received_at: str | None = None
    claimed_at: str | None = None
    """When a dispatcher claimed it; a stale claim is recovered."""
    run_id: str | None = None
    error: str | None = None


class AgentNotification(Model):
    """Something an agent wanted its owner to see (Notify Me tool)."""

    user_id: str
    """Principal key of the person to notify."""
    task_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    title: str
    message: str = ''
    read_at: str | None = None


def public_trigger(trigger: TaskTrigger) -> dict[str, Any]:
    """API projection: never the hash or the owner's authority."""
    data = trigger.json()
    return {
        'id': data.get('id'),
        'task_id': data.get('task_id'),
        'name': data.get('name'),
        'kind': data.get('kind'),
        'enabled': bool(data.get('enabled', True)),
        'token_hint': data.get('token_hint') or '',
        'last_fired_at': data.get('last_fired_at'),
        'fire_count': int(data.get('fire_count') or 0),
        'created_at': data.get('created_at'),
    }


def public_notification(row: AgentNotification) -> dict[str, Any]:
    data = row.json()
    return {
        'id': data.get('id'),
        'task_id': data.get('task_id'),
        'run_id': data.get('run_id'),
        'session_id': data.get('session_id'),
        'title': data.get('title') or '',
        'message': data.get('message') or '',
        'read_at': data.get('read_at'),
        'created_at': _timestamp(data.get('created_at')),
    }


# --- trigger secrets ----------------------------------------------------------

def new_trigger_token() -> tuple[str, str]:
    """(secret, sha256 hex). The secret goes in the trigger URL."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def token_matches(trigger: TaskTrigger, token: str) -> bool:
    return hmac.compare_digest(trigger.token_hash or '', hash_token(token or ''))


# --- run caps ------------------------------------------------------------------

def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime(FMT)
    return str(value).replace('T', ' ')[:19]


async def automation_runs_last_day(task_id: str, now: datetime | None = None) -> int:
    """Wake-up and webhook runs of this task in the last 24 hours."""
    from cognitrix.tasks.run import TaskRun

    cutoff = ((now or utcnow()) - timedelta(days=1)).strftime(FMT)
    count = 0
    for run in await TaskRun.find({'task_id': task_id}) or []:
        if run.trigger_kind not in AUTOMATION_TRIGGER_KINDS:
            continue
        created = _timestamp(run.json().get('created_at') or run.queued_at)
        if created and created >= cutoff:
            count += 1
    return count


async def over_daily_cap(task: Any, now: datetime | None = None) -> bool:
    cap = getattr(task, 'max_runs_per_day', None) or default_max_runs_per_day()
    return await automation_runs_last_day(task.id, now) >= cap


# --- prompt context ------------------------------------------------------------

TOOLS_HINT = (
    "Tools for work that continues between runs: Read Notes and Update Notes "
    "keep notes for your next run. Schedule Next Check picks when to look again "
    f"({WAKE_MIN_MINUTES} minute to {WAKE_MAX_MINUTES // 1440} days). Notify Me alerts "
    "the task owner; use it only when something needs their attention."
)


async def automation_brief(task: Any, run: Any) -> str:
    """Extra prompt context for automation runs; empty for plain manual runs."""
    from cognitrix.tasks.context import untrusted_text_block

    kind = getattr(run, 'trigger_kind', None)
    note = _clip(getattr(run, 'trigger_note', None), REASON_MAX_CHARS)
    parts: list[str] = []
    if kind == 'wake':
        parts.append(
            'Why this run started: an earlier run asked to check again'
            + (f', because: {note}' if note else '.')
        )
    elif kind == 'schedule':
        parts.append("Why this run started: the task's regular schedule.")
    elif kind == 'webhook':
        parts.append(
            'Why this run started: a webhook event arrived'
            + (f' on the trigger "{note}".' if note else '.')
        )
        event_id = getattr(run, 'trigger_event_id', None)
        event = await TriggerEvent.get(event_id) if event_id else None
        if event is not None and event.payload:
            parts.append(untrusted_text_block('Webhook event payload', event.payload, EVENT_MAX_BYTES + 512))

    notes = _clip(getattr(task, 'notes', None), NOTES_MAX_CHARS)
    if notes:
        parts.append(
            'Notes kept between runs (written by earlier runs and the task owner):\n'
            f'<notes>\n{notes}\n</notes>'
        )

    is_automation = bool(parts) or bool(
        getattr(task, 'schedule_enabled', False) or getattr(task, 'wake_pending', False)
    )
    if not is_automation and getattr(task, 'id', None):
        try:
            is_automation = bool(await TaskTrigger.find({'task_id': task.id}))
        except Exception as exc:
            if 'no such table' not in str(exc).lower():
                raise
    if not is_automation:
        return ''
    parts.append(TOOLS_HINT)
    return 'Automation context:\n' + '\n\n'.join(parts)


# --- scheduler passes ----------------------------------------------------------

def _status_code(exc: BaseException) -> int | None:
    return getattr(exc, 'status_code', None)


async def fire_due_wakes(now: datetime | None = None) -> int:
    """Start a run for every task whose agent-requested wake-up is due."""
    from cognitrix.tasks.base import Task

    now = now or utcnow()
    fired = 0
    for task in await Task.find({'wake_pending': True}) or []:
        try:
            if task.deleted_at or not task.wake_at:
                continue
            if datetime.fromisoformat(task.wake_at) > now:
                continue
            cleared = {field: None for field in WAKE_FIELDS}
            cleared['wake_pending'] = False
            claimed = await Task.update_one(
                {'id': task.id, 'wake_at': task.wake_at, 'wake_pending': True}, dict(cleared))
            if claimed != 1:
                continue
            if await over_daily_cap(task, now):
                logger.warning('Task %s: wake-up dropped, daily automation run cap reached', task.id)
                continue
            from cognitrix.api.routes.tasks import _enqueue_task_start
            try:
                await _enqueue_task_start(
                    task,
                    actor_key='scheduler',
                    requested_by=task.wake_requested_by,
                    authority_kind=task.wake_authority_kind,
                    authority_id=task.wake_authority_id,
                    trigger_kind='wake',
                    trigger_note=task.wake_reason,
                )
                fired += 1
            except Exception as exc:
                if _status_code(exc) == 404:
                    continue
                # Busy (the run that armed it may still be going) or the queue
                # is down: re-arm unless the agent armed a newer wake-up.
                restore = {field: getattr(task, field) for field in WAKE_FIELDS}
                await Task.update_one({'id': task.id, 'wake_pending': False}, restore)
                logger.debug('Task %s: wake-up deferred (%s)', task.id, exc)
        except Exception:
            logger.exception('Wake-up pass skipped task %s', getattr(task, 'id', '?'))
    return fired


async def recover_stale_dispatches(now: datetime | None = None) -> int:
    """Settle events whose dispatcher died between claiming and enqueueing.

    A run carrying the event's id means the run was published: mark it
    dispatched. Otherwise put the event back in the queue.
    """
    from cognitrix.tasks.run import TaskRun

    cutoff = ((now or utcnow()) - timedelta(seconds=DISPATCH_CLAIM_SECONDS)).strftime(FMT)
    settled = 0
    for event in await TriggerEvent.find({'status': DISPATCHING}) or []:
        if event.claimed_at and event.claimed_at > cutoff:
            continue
        runs = await TaskRun.find({'trigger_event_id': event.id}) or []
        if runs:
            update = {'status': DISPATCHED, 'run_id': str(runs[0].id)}
        else:
            update = {'status': PENDING, 'claimed_at': None}
        settled += await TriggerEvent.update_one(
            {'id': event.id, 'status': DISPATCHING}, update) or 0
    return settled


async def dispatch_trigger_events(now: datetime | None = None) -> int:
    """Hand the oldest waiting webhook event of each idle task to a new run."""
    from cognitrix.tasks.base import Task

    now = now or utcnow()
    try:
        await recover_stale_dispatches(now)
    except Exception:
        logger.exception('Could not recover interrupted webhook dispatches')
    events = sorted(
        await TriggerEvent.find({'status': PENDING}) or [],
        key=lambda item: (item.received_at or '', str(item.id)),
    )
    oldest: dict[str, TriggerEvent] = {}
    for event in events:
        oldest.setdefault(event.task_id, event)

    started = 0
    for event in oldest.values():
        try:
            task = await Task.get(event.task_id)
            trigger = await TaskTrigger.get(event.trigger_id)
            if task is None or task.deleted_at or trigger is None or not trigger.enabled:
                await TriggerEvent.update_one(
                    {'id': event.id, 'status': PENDING},
                    {'status': DROPPED, 'error': 'trigger or task no longer exists'})
                continue
            if await over_daily_cap(task, now):
                continue  # waits for the rolling window to free up
            if await TriggerEvent.update_one(
                    {'id': event.id, 'status': PENDING},
                    {'status': DISPATCHING, 'claimed_at': now.strftime(FMT)}) != 1:
                continue
            from cognitrix.api.routes.tasks import _enqueue_task_start
            try:
                run = await _enqueue_task_start(
                    task,
                    actor_key='scheduler',
                    requested_by=trigger.requested_by,
                    authority_kind=trigger.authority_kind,
                    authority_id=trigger.authority_id,
                    trigger_kind='webhook',
                    trigger_note=trigger.name,
                    trigger_event_id=event.id,
                )
            except Exception as exc:
                if _status_code(exc) == 404:
                    await TriggerEvent.update_one(
                        {'id': event.id}, {'status': DROPPED, 'error': 'task not found'})
                else:
                    await TriggerEvent.update_one({'id': event.id}, {'status': PENDING})
                    logger.debug('Webhook event %s deferred (%s)', event.id, exc)
                continue
            await TriggerEvent.update_one(
                {'id': event.id}, {'status': DISPATCHED, 'run_id': str(run.id)})
            await TaskTrigger.update_one(
                {'id': trigger.id},
                {'last_fired_at': utcnow_str(), 'fire_count': int(trigger.fire_count or 0) + 1})
            started += 1
        except Exception:
            logger.exception('Webhook dispatch skipped event %s', getattr(event, 'id', '?'))
    return started


# --- notifications -------------------------------------------------------------

async def create_notification(
    user_id: str,
    title: str,
    message: str,
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
) -> AgentNotification:
    row = AgentNotification(
        user_id=str(user_id),
        title=_clip(title, TITLE_MAX_CHARS) or 'Update',
        message=_clip(message, MESSAGE_MAX_CHARS),
        task_id=task_id,
        run_id=run_id,
        session_id=session_id,
    )
    await row.save()
    return row


async def unread_notifications(user_id: str) -> list[AgentNotification]:
    rows = await AgentNotification.find({'user_id': str(user_id)}) or []
    return [row for row in rows if not row.read_at]
