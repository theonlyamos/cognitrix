"""When the main assistant does work nobody asked for, and what it reads when it does.

Called from the task scheduler:

* ``gate_checkin`` runs before every scheduled check-in of a standing
  assistant. It refuses at level Off or Brief only, while the person has been
  away for a week, over the day's token limit, while backing off after
  dismissals, and when nothing changed since the last check-in (the free
  change check: no model call at all).
* ``proactive_tick`` runs from ``automation_tick``: the morning brief at the
  person's time, meeting prep before one-off calls, stuck-work alerts,
  due follow-ups and dream mode.
* ``run_extras`` adds what a check-in, brief or prep run reads: what's going
  on, new mail sorted by rules, the person's past answers.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger('cognitrix.log')

TICK_EVERY_S = 60
PREP_FROM_MIN = 30
PREP_TO_MIN = 60
REPEAT_DAYS = 14
REPEAT_MIN = 3
REPEAT_SIMILAR = 0.8

_last_tick = 0.0


def _utc(now: datetime | None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now


async def main_assistants() -> list[tuple[Any, str]]:
    """(agent, owner) for every person's main assistant whose check-ins are on."""
    from cognitrix.models.agent import Agent
    from cognitrix.tasks import standing

    out = []
    for agent in await Agent.all() or []:
        settings = standing.settings_of(agent)
        owner = str(getattr(agent, 'owner_id', '') or '')
        if settings['primary'] and settings['enabled'] and settings['task_id'] and owner:
            out.append((agent, owner))
    return out


def _state(agent: Any) -> dict[str, Any]:
    auto = getattr(agent, 'automation', None)
    raw = auto.get('standing_state') if isinstance(auto, dict) else None
    return dict(raw) if isinstance(raw, dict) else {}


async def _save_state(agent: Any, state: dict[str, Any]) -> None:
    from cognitrix.models.agent import Agent

    fresh = await Agent.find_one({'id': str(agent.id)})
    auto = dict(getattr(fresh or agent, 'automation', None) or {})
    auto['standing_state'] = state
    await Agent.update_one({'id': agent.id}, {'automation': auto})
    agent.automation = auto


async def change_marker(owner: str, agent: Any, settings: Any, now: datetime | None = None) -> str:
    """Fingerprint of everything a check-in looks at that the app can see without a model."""
    from cognitrix.assistant import mail, signals
    from cognitrix.tasks import standing

    sit = await signals.situation(owner, now=now)
    new_mail = await mail.check_new_mail(owner, settings, now)
    brief = standing.brief_text(standing.settings_of(agent))
    recent_mail = ','.join(sorted(r.mail_id for r in await mail.recent(owner)))
    extra = hashlib.sha256(f'{brief}|{recent_mail}|{len(new_mail)}'.encode()).hexdigest()
    return signals.fingerprint(sit, extra)


async def gate_checkin(task: Any, now: datetime | None = None) -> tuple[bool, str]:
    """Whether a scheduled check-in may start a run. (allowed, reason when not)."""
    from cognitrix.assistant import proactivity as p
    from cognitrix.tasks import standing

    agent = await standing.standing_agent(task)
    owner = str(getattr(task, 'owner_id', '') or '')
    if agent is None or not owner:
        return True, ''
    settings = await p.get_settings(owner)
    if not p.allows(settings, 'checkins'):
        return False, f'proactivity level is {settings.level}'
    if p.inactive(settings, now):
        return False, 'the person has not opened the app for a week'
    if await p.over_budget(owner, now):
        await _note_limit(owner)
        return False, "today's token limit for proactive work is used up"
    state = _state(agent)
    every = standing.settings_of(agent)['every_minutes']
    backoff = settings.backoff or 1
    last = state.get('last_run_at')
    if backoff > 1 and last:
        try:
            since = _utc(now) - datetime.strptime(last, '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
        except ValueError:
            since = timedelta.max
        if since < timedelta(minutes=every * backoff):
            await p.bump(owner, 'skipped', now=now)
            return False, f'backing off ({backoff}x) after dismissed items'
    marker = await change_marker(owner, agent, settings, now)
    if marker == state.get('fingerprint'):
        await p.bump(owner, 'skipped', now=now)
        return False, 'nothing changed since the last check-in'
    state.update({'fingerprint': marker, 'last_run_at': _utc(now).strftime('%Y-%m-%d %H:%M:%S')})
    await _save_state(agent, state)
    await p.bump(owner, 'runs', now=now)
    return True, ''


async def _note_limit(owner: str) -> None:
    """Say once a day that proactive work stopped for the day."""
    from cognitrix.assistant import proactivity as p
    from cognitrix.tasks.automation import create_notification

    if await p.already_alerted(owner, 'token-limit'):
        return
    await p.mark_alerted(owner, 'token-limit')
    await create_notification(
        owner, 'Your assistant paused for today',
        "It reached today's limit for work you didn't ask for. It picks up again tomorrow; "
        'you can raise the limit in Settings › Assistant.', link='/settings/assistant')


async def _start(task: Any, kind: str, note: str) -> bool:
    from cognitrix.api.routes.tasks import _enqueue_task_start

    try:
        await _enqueue_task_start(
            task, actor_key='scheduler', requested_by=task.schedule_requested_by,
            authority_kind=task.schedule_authority_kind, authority_id=task.schedule_authority_id,
            trigger_kind=kind, trigger_note=note)
        return True
    except Exception as exc:
        logger.info('Could not start the %s run of task %s: %s', kind, task.id, exc)
        return False


async def brief_due(agent: Any, owner: str, now: datetime) -> bool:
    """Start the morning brief when it is time. True when one started."""
    from cognitrix.assistant import proactivity as p
    from cognitrix.tasks import standing

    settings = await p.get_settings(owner)
    if not p.allows(settings, 'brief') or p.inactive(settings, now) or await p.over_budget(owner, now):
        return False
    local = p.local_now(settings, now)
    day = local.strftime('%Y-%m-%d')
    if settings.last_brief_day == day:
        return False
    if local.strftime('%H:%M') < (settings.brief_time or p.DEFAULT_BRIEF_TIME):
        return False
    task = await standing.home_task(agent)
    if task is None:
        return False
    await p.save_settings(owner, last_brief_day=day)
    started = await _start(task, 'brief', f'Morning brief for {day}')
    if not started:
        await p.save_settings(owner, last_brief_day=settings.last_brief_day or '')
        return False
    await p.bump(owner, 'runs', now=now)
    return True


async def prep_due(agent: Any, owner: str, now: datetime) -> int:
    """Start meeting prep 30 to 60 minutes before one-off calls. Returns how many started."""
    from cognitrix.assistant import proactivity as p
    from cognitrix.meetings.calendar_calls import CalendarCall
    from cognitrix.tasks import standing

    settings = await p.get_settings(owner)
    if not p.allows(settings, 'prep') or p.inactive(settings, now) or await p.over_budget(owner, now):
        return 0
    try:
        calls = await CalendarCall.find({'user_id': owner}) or []
    except Exception:
        return 0
    started = 0
    for call in calls:
        if call.series_id:
            continue  # recurring meetings don't get prep
        try:
            starts = datetime.strptime(call.starts_at[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
        except ValueError:
            continue
        minutes = (starts - now).total_seconds() / 60
        if not PREP_FROM_MIN <= minutes <= PREP_TO_MIN:
            continue
        key = f'prep:{call.event_id}:{call.starts_at}'
        if await p.already_alerted(owner, key, now):
            continue
        task = await standing.home_task(agent)
        if task is None:
            return started
        local = p.local_now(settings, starts).strftime('%H:%M')
        note = f'{call.title or "A call"} at {local}' + (f', organised by {call.organizer}' if call.organizer else '')
        if await _start(task, 'prep', note[:400]):
            await p.mark_alerted(owner, key, now)
            await p.bump(owner, 'runs', now=now)
            started += 1
    return started


async def stalled_alerts(owner: str, now: datetime) -> int:
    """One alert per blocking stuck item per day."""
    from cognitrix.assistant import proactivity as p
    from cognitrix.assistant import signals
    from cognitrix.tasks.automation import create_notification

    settings = await p.get_settings(owner)
    if not p.allows(settings, 'checkins') or p.inactive(settings, now):
        return 0
    sit = await signals.situation(owner, now=now)
    sent = 0
    for item in sit.stalled:
        if not item.blocking or await p.already_alerted(owner, item.key, now):
            continue
        await p.mark_alerted(owner, item.key, now)
        await create_notification(owner, item.title, item.detail or 'Open it to unblock it.',
                                  link=item.href or '/inbox', proactive=True)
        sent += 1
    return sent


async def proactive_tick(now: datetime | None = None) -> None:
    """Everything proactive that runs on the clock. Called every scheduler tick."""
    global _last_tick
    now = _utc(now)
    try:
        from cognitrix.assistant.followups import deliver_due

        await deliver_due(now.replace(tzinfo=None))
    except Exception:
        logger.exception('Could not deliver follow-ups')
    if time.monotonic() - _last_tick < TICK_EVERY_S:
        return
    _last_tick = time.monotonic()
    try:
        assistants = await main_assistants()
    except Exception:
        logger.exception('Could not list main assistants')
        assistants = []
    for agent, owner in assistants:
        for step in (brief_due, prep_due):
            try:
                await step(agent, owner, now)
            except Exception:
                logger.exception('Proactive %s failed for %s', step.__name__, owner)
        try:
            await stalled_alerts(owner, now)
        except Exception:
            logger.exception('Stuck-work alerts failed for %s', owner)
    try:
        from cognitrix.memory.dream import dream_tick

        await dream_tick(now)
    except Exception:
        logger.exception('Dream mode tick failed')


# --- what a run reads ------------------------------------------------------------

async def repeated_requests(owner: str) -> list[str]:
    """Requests the person made about the same way three or more times in two weeks."""
    from cognitrix.memory import semantic
    from cognitrix.sessions.base import Session

    try:
        sessions = await Session.find({'user_id': owner}) or []
    except Exception:
        return []
    cutoff = datetime.now() - timedelta(days=REPEAT_DAYS)
    firsts: list[str] = []
    for session in sessions:
        updated = getattr(session, 'updated_at', None)
        if isinstance(updated, datetime) and updated.replace(tzinfo=None) < cutoff:
            continue
        if getattr(session, 'incognito', False) or getattr(session, 'task_id', None):
            continue
        for message in session.chat or []:
            if str(message.get('role', '')).lower() == 'user' and message.get('type', 'text') == 'text':
                text = ' '.join(str(message.get('content') or '').split())[:200]
                if len(text) > 15:
                    firsts.append(text)
                break
    if len(firsts) < REPEAT_MIN:
        return []
    pairs = await semantic.similar_pairs(firsts[:150], REPEAT_SIMILAR)
    if not pairs:
        return []
    groups: dict[int, set[int]] = {}
    for i, j, _score in pairs:
        root = next((k for k, members in groups.items() if i in members or j in members), i)
        groups.setdefault(root, {root}).update({i, j})
    return [firsts[min(members)] for members in groups.values() if len(members) >= REPEAT_MIN][:3]


async def run_extras(task: Any, run: Any, agent: Any) -> str:
    """Extra context for a standing assistant's run: brief/prep instructions,
    what's going on, sorted mail and the person's past answers."""
    from cognitrix.assistant import brief, mail, proactivity, signals
    from cognitrix.tasks.context import untrusted_text_block

    owner = str(getattr(task, 'owner_id', '') or '')
    if not owner:
        return ''
    kind = getattr(run, 'trigger_kind', None) if run is not None else None
    settings = await proactivity.get_settings(owner)
    parts: list[str] = []
    if kind == 'brief':
        parts.append(brief.run_instructions(brief.local_day(settings)))
    elif kind == 'prep':
        note = str(getattr(run, 'trigger_note', '') or '')
        parts.append(
            f'This run is MEETING PREP for: {note}. Write one short prep note and send it with Notify Me '
            '(urgent true, title "Prep: <meeting>"): who is coming and what you know about them, the last '
            'mail thread with them, open items or promises involving them, and two or three points worth '
            'raising. Use what you can read; skip what you cannot find. Do nothing else.')
    try:
        sit = await signals.situation(owner)
        parts.append("What's going on (from the app):\n" + signals.describe(sit))
    except Exception:
        logger.debug('Could not describe the situation', exc_info=True)
    rows = await mail.recent(owner)
    if rows:
        parts.append(
            'New mail since recent check-ins, sorted by plain rules (newsletters and no-reply senders are '
            'already left out). Decide what really needs a reply; you may read a thread with the Gmail '
            'tools and prepare a draft reply as an Offer Help, but never send.\n'
            + untrusted_text_block('New mail', mail.describe(rows), 6000))
    note = proactivity.feedback_note(settings)
    if note:
        parts.append(note)
    if kind == 'brief':
        try:
            repeats = await repeated_requests(owner)
        except Exception:
            repeats = []
        if repeats:
            parts.append(
                'The person asked for much the same thing several times in the last two weeks. If it would '
                'help, offer (Offer Help) to make it a scheduled task or a skill:\n'
                + '\n'.join(f'- "{r}"' for r in repeats))
    parts.append(
        'Follow these guards: bring up only what is new or changed since your notes, with a deadline, '
        'or that blocks work. Routine items wait for the morning brief. Alerts push to the person only '
        'outside their quiet hours and under their daily limit; everything else waits in the Inbox.')
    return '\n\n'.join(parts)
