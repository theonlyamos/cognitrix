"""Results of agent-created tasks, sent back to the agent that created them.

An agent that creates a task (Create Task) is recorded on it as its creator
(``Task.created_by``): the agent, the person, and the chat or task run it
was working in. ``Task.report_to`` says who hears about a finished run:

* ``person``  - the person gets the usual "Task finished" push (the default
  for tasks people make, and for scheduled tasks).
* ``creator`` - the creating agent gets the result instead, and decides
  whether the person needs to know (Notify Me). Default for an agent's
  one-off tasks.
* ``both``    - both.

When a run ends, ``queue_report`` (called from the completion notifier)
writes a durable ``TaskReport`` row. The scheduler loop delivers due rows
(``deliver_due_reports``), so a report survives restarts and is sent from
the API process even when the run itself ran in a Celery worker:

* created in a chat: the report goes into that chat, steered into a running
  turn or as a new background turn (``agent_chats.deliver_report``). That
  turn has Notify Me and does not alert the person by itself.
* created in a task run: the creating task starts a new run
  (``trigger_kind='task_result'``) whose brief carries the report.

Guards: tasks created by agents nest at most ``MAX_TASK_DEPTH`` levels;
reports count as agent messages in a chat (the pause after several in a
row); several runs that end before a report goes out are sent as one;
``report_only_on`` can limit reports to failures or changed results; runs
woken by reports count toward the creating task's daily run cap; after
``MAX_FAILED_REPORTS`` failed runs in a row the person is told as well. A
creator that cannot be reached, or a report that cannot be delivered in
time, falls back to an Inbox alert for the person.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from odbms import Model
from pydantic import Field, validator

logger = logging.getLogger('cognitrix.log')

REPORT_TO = ('person', 'creator', 'both')
REPORT_ONLY_ON = ('always', 'failure', 'changed')
MAX_TASK_DEPTH = 3
MAX_FAILED_REPORTS = 2
"""Failed runs reported in a row to the creator before the person is told too."""
RESULT_CHARS = 6_000
OTHER_RUNS_SHOWN = 5
BUSY_RETRY_SECONDS = 20
CAP_RETRY_SECONDS = 300
ERROR_RETRY_SECONDS = (30, 120, 600)
GIVE_UP_AFTER = timedelta(hours=6)
CLAIM_SECONDS = 600
FMT = '%Y-%m-%d %H:%M:%S'

PENDING = 'pending'
DELIVERING = 'delivering'
DELIVERED = 'delivered'
PAUSED = 'paused'
FALLBACK = 'fallback'

# Authority fields stay on the server: the API projection drops them.
PRIVATE_CREATOR_FIELDS = ('authority_kind', 'authority_id', 'requested_by')


class TaskReport(Model):
    """One report on a task's finished run(s), owed to the agent that created it."""

    task_id: str
    user_id: str
    agent_id: str
    """The creating agent."""
    target_kind: str
    """'chat' (the chat it was created in) or 'task' (the task whose run created it)."""
    target_id: str
    run_ids: list[str] = Field(default_factory=list)
    run_statuses: list[str] = Field(default_factory=list)
    status: str = PENDING
    result_hash: str | None = None
    """Hash of the newest run's result, for report_only_on='changed'."""
    queued_at: str | None = None
    next_at: str | None = None
    claimed_at: str | None = None
    attempts: int = 0
    outcome: str | None = None
    """How it went: steered, started (chat turn), run (task run), paused, or why it fell back."""
    delivered_at: str | None = None
    delivered_run_id: str | None = None
    """The creating task's run that took the report (task targets)."""

    @validator('run_ids', 'run_statuses', pre=True)
    def _null_list(cls, value):
        return [] if value is None else value

    @validator('attempts', pre=True)
    def _null_attempts(cls, value):
        return 0 if value is None else value


class TaskReportClaim(Model):
    """One row per finished run that was considered for a report.

    A unique index on ``run_id`` (``ensure_schema``) makes queueing atomic:
    the completion step, recovery and the reconcile sweep may all try the
    same run, and only the one whose insert succeeds goes on.
    """

    run_id: str
    task_id: str = ''


CLAIM_INDEX = 'ux_task_report_claims_run'
RECONCILE_EVERY = timedelta(minutes=5)
RECONCILE_WINDOW = timedelta(days=1)
RECONCILE_GRACE = timedelta(minutes=2)
_last_reconcile: dict[str, datetime] = {}


async def ensure_schema() -> None:
    """Create the unique index that makes per-run claims atomic."""
    from odbms import DBMS

    database = DBMS.Database
    if getattr(database, 'dbms', '') not in ('sqlite', 'postgresql', 'mysql'):
        return
    table = TaskReportClaim.table_name()
    statement = (f'CREATE UNIQUE INDEX {CLAIM_INDEX} ON {table} (run_id)'
                 if database.dbms == 'mysql' else
                 f'CREATE UNIQUE INDEX IF NOT EXISTS {CLAIM_INDEX} ON {table} (run_id)')
    try:
        await database.query(statement)
    except Exception as exc:
        if 'exist' not in str(exc).lower() and 'duplicate' not in str(exc).lower():
            raise


async def _claim_run(task_id: str, run_id: str) -> bool:
    """True for exactly one caller per run."""
    if await TaskReportClaim.find({'run_id': run_id}):
        return False
    try:
        await TaskReportClaim(run_id=run_id, task_id=str(task_id)).save()
    except Exception as exc:
        if 'unique' in str(exc).lower() or 'duplicate' in str(exc).lower():
            return False
        raise
    return True


async def _unclaim_run(run_id: str) -> None:
    try:
        await TaskReportClaim.delete_many({'run_id': run_id})
    except Exception:
        logger.debug('Could not release the report claim of run %s', run_id, exc_info=True)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fmt(value: datetime) -> str:
    return value.strftime(FMT)


def _plain(value: Any) -> str:
    return str(getattr(value, 'value', value) or '')


# --- recording the creator ---------------------------------------------------

async def creator_snapshot(parent: Any, ctx: Any) -> dict[str, Any] | None:
    """Who is creating a task, from the calling agent and its execution context.

    None when there is no agent, no signed-in person, or neither a chat nor
    a task run to send a result back to. Raises ValueError past the depth limit.
    """
    agent_id = str(getattr(parent, 'id', '') or getattr(ctx, 'agent_id', '') or '')
    user_id = str(getattr(ctx, 'user_id', '') or '')
    if not agent_id or not user_id:
        return None
    snapshot: dict[str, Any] = {
        'agent_id': agent_id,
        'agent_name': str(getattr(parent, 'name', '') or '')[:120] or None,
        'user_id': user_id,
        'requested_by': user_id,
        'authority_kind': 'api_key' if getattr(ctx, 'api_key_id', None) else 'jwt',
        'authority_id': getattr(ctx, 'api_key_id', None) or user_id,
    }
    task_id = str(getattr(ctx, 'task_id', '') or '')
    session_id = str(getattr(ctx, 'session_id', '') or '')
    if task_id:
        from cognitrix.tasks.base import Task

        origin = await Task.get(task_id)
        depth = int((getattr(origin, 'created_by', None) or {}).get('depth') or 0) + 1
        snapshot.update(kind='task', task_id=task_id, run_id=str(getattr(ctx, 'run_id', '') or '') or None,
                        depth=depth)
    elif session_id:
        snapshot.update(kind='chat', session_id=session_id, depth=1)
    else:
        return None
    if snapshot['depth'] > MAX_TASK_DEPTH:
        raise ValueError(
            f'Tasks created by agents can only nest {MAX_TASK_DEPTH} levels deep, and this one would be '
            f'level {snapshot["depth"]}. Do the work in this run instead, or ask the person.')
    return snapshot


def public_creator(created_by: Any) -> dict[str, Any] | None:
    if not isinstance(created_by, dict) or not created_by:
        return None
    return {k: v for k, v in created_by.items() if k not in PRIVATE_CREATOR_FIELDS}


def _scheduled(task: Any) -> bool:
    return bool(getattr(task, 'schedule_at', None) or getattr(task, 'schedule_interval', None)
                or getattr(task, 'schedule_cron', None))


def default_report_to(task: Any) -> str:
    """Agents' one-off tasks report to the agent; anything on a schedule to the person."""
    if getattr(task, 'created_by', None) and not _scheduled(task):
        return 'creator'
    return 'person'


def effective_report_to(task: Any) -> str:
    created_by = getattr(task, 'created_by', None) or {}
    if not created_by.get('agent_id') or not created_by.get('kind'):
        return 'person'
    value = getattr(task, 'report_to', None)
    return value if value in REPORT_TO else default_report_to(task)


def validate_report_fields(report_to: Any, report_only_on: Any) -> str | None:
    if report_to is not None and report_to not in REPORT_TO:
        return f'report_to must be one of: {", ".join(REPORT_TO)}'
    if report_only_on is not None and report_only_on not in REPORT_ONLY_ON:
        return f'report_only_on must be one of: {", ".join(REPORT_ONLY_ON)}'
    return None


# --- queueing when a run ends ------------------------------------------------

def _result_text(run: Any) -> str:
    from cognitrix.tasks.results import StepResult

    try:
        result = run.result_data or (StepResult.from_stored(run.result) if run.result else None)
    except (TypeError, ValueError):
        return str(getattr(run, 'result', '') or '')
    return str(getattr(result, 'text', '') or '') if result is not None else ''


def _hash(text: str) -> str:
    return hashlib.sha256(' '.join(text.split()).encode('utf-8')).hexdigest()


async def _rows_for(task_id: str) -> list[TaskReport]:
    rows = await TaskReport.find({'task_id': str(task_id)}) or []
    return sorted(rows, key=lambda r: (r.queued_at or '', str(r.id)))


def _target(created_by: dict[str, Any]) -> tuple[str, str] | None:
    if created_by.get('kind') == 'chat' and created_by.get('session_id'):
        return 'chat', str(created_by['session_id'])
    if created_by.get('kind') == 'task' and created_by.get('task_id'):
        return 'task', str(created_by['task_id'])
    return None


async def queue_report(task: Any, run: Any) -> bool:
    """Owe the creating agent a report on this finished run.

    Returns True when the person's own "Task finished" push should be
    skipped (the result goes to the agent only). Safe to call twice for one run.
    """
    report_to = effective_report_to(task)
    if report_to == 'person':
        return False
    creator_only = report_to == 'creator'
    status = _plain(getattr(run, 'status', ''))
    if status not in ('completed', 'failed'):
        return creator_only  # a stopped run is not reported back
    created_by = dict(getattr(task, 'created_by', None) or {})
    target = _target(created_by)
    if target is None:
        return False
    run_id = str(run.id)
    if not await _claim_run(task.id, run_id):
        return creator_only  # someone else queued (or skipped) this run
    try:
        await _queue_claimed(task, run, status, created_by, target)
    except BaseException:
        await _unclaim_run(run_id)  # let a later pass try again
        raise
    return creator_only


async def _queue_claimed(task: Any, run: Any, status: str, created_by: dict[str, Any],
                         target: tuple[str, str]) -> None:
    rows = await _rows_for(task.id)
    run_id = str(run.id)
    if any(run_id in (row.run_ids or []) for row in rows):
        return

    text = _result_text(run)
    result_hash = _hash(text) if status == 'completed' else None
    only_on = getattr(task, 'report_only_on', None) or 'always'
    if only_on == 'failure' and status == 'completed':
        return
    if only_on == 'changed' and status == 'completed':
        last = next((r.result_hash for r in reversed(rows) if r.result_hash), None)
        if last == result_hash:
            return

    # The agent kept getting failures back: tell the person as well.
    failed_in_a_row = 0
    for row in reversed(rows):
        if row.run_statuses and all(s == 'failed' for s in row.run_statuses):
            failed_in_a_row += 1
        else:
            break
    if status == 'failed' and failed_in_a_row >= MAX_FAILED_REPORTS:
        await _tell_person(task, run, 'It failed again after the agent that created it was told '
                           f'{failed_in_a_row} times.')

    user_id = str(created_by.get('user_id') or getattr(task, 'owner_id', '') or '')
    kind, target_id = target
    # Runs that end while an earlier report still waits go out together.
    for row in reversed(rows):
        if row.status != PENDING or (row.target_kind, row.target_id) != (kind, target_id):
            continue
        changed = await TaskReport.update_one(
            {'id': row.id, 'status': PENDING},
            {'run_ids': [*row.run_ids, run_id], 'run_statuses': [*row.run_statuses, status],
             'result_hash': result_hash or row.result_hash})
        if changed == 1:
            return
        break
    now = _fmt(_now())
    await TaskReport(
        task_id=str(task.id), user_id=user_id, agent_id=str(created_by['agent_id']),
        target_kind=kind, target_id=target_id, run_ids=[run_id], run_statuses=[status],
        result_hash=result_hash, queued_at=now, next_at=now,
    ).save()


async def reconcile_missing_reports(now: datetime | None = None, *, force: bool = False) -> int:
    """Queue reports for finished runs that never reached the completion step
    (a worker that stopped right after saving the run). Runs every few minutes."""
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun

    now = now or _now()
    last = _last_reconcile.get('at')
    if not force and last is not None and now - last < RECONCILE_EVERY:
        return 0
    _last_reconcile['at'] = now
    oldest = _fmt(now - RECONCILE_WINDOW)
    newest = _fmt(now - RECONCILE_GRACE)
    queued = 0
    for task in await Task.find({}) or []:
        if task.deleted_at or not getattr(task, 'created_by', None) or effective_report_to(task) == 'person':
            continue
        for run in await TaskRun.find({'task_id': str(task.id)}) or []:
            ended = str(run.completed_at or '').replace('T', ' ')[:19]
            if _plain(run.status) not in ('completed', 'failed') or not ended or not oldest <= ended <= newest:
                continue
            if await TaskReportClaim.find({'run_id': str(run.id)}):
                continue
            try:
                await queue_report(task, run)
                queued += 1
            except Exception:
                logger.exception('Could not queue the missed report of run %s', run.id)
    return queued


async def _tell_person(task: Any, run: Any, why: str) -> None:
    from cognitrix.tasks.automation import create_notification

    user_id = str(((getattr(task, 'created_by', None) or {}).get('user_id'))
                  or getattr(task, 'owner_id', '') or getattr(run, 'requested_by', '') or '')
    if not user_id:
        return
    status = _plain(getattr(run, 'status', ''))
    title = getattr(task, 'title', '') or 'A task'
    head = f'Task "{title}" ' + ('failed' if status == 'failed' else 'finished')
    detail = str(getattr(run, 'error', '') or '') if status == 'failed' else _result_text(run)
    message = why + (f'\n\n{detail[:600]}' if detail else '')
    try:
        await create_notification(user_id, head, message, task_id=str(task.id), run_id=str(run.id),
                                  link=f'/tasks/{task.id}')
    except Exception:
        logger.exception('Could not alert the owner of task %s', getattr(task, 'id', '?'))


# --- the report text -----------------------------------------------------------

async def _run_artifacts(run_id: str) -> list[Any]:
    """The artifacts a run published or had its result saved to."""
    try:
        from cognitrix.artifact_pages.models import ArtifactPage, ArtifactVersion

        seen: dict[str, Any] = {}
        for version in await ArtifactVersion.find({'run_id': str(run_id)}) or []:
            if version.page_id not in seen:
                page = await ArtifactPage.get(version.page_id)
                if page is not None:
                    seen[version.page_id] = page
        return list(seen.values())[:5]
    except Exception:
        return []


async def report_text(row: TaskReport, task: Any, *, for_chat: bool) -> str:
    from cognitrix.tasks.context import untrusted_text_block
    from cognitrix.tasks.run import TaskRun

    title = getattr(task, 'title', '') or 'Untitled task'
    runs = [r for r in [await TaskRun.get(rid) for rid in row.run_ids] if r is not None]
    lines: list[str] = []
    if len(runs) <= 1:
        run = runs[0] if runs else None
        status = _plain(getattr(run, 'status', '')) if run else (row.run_statuses or ['completed'])[-1]
        verb = 'failed' if status == 'failed' else 'finished'
        lines.append(f'The task "{title}" (id {task.id}) that you created has {verb}.')
    else:
        failed = sum(1 for r in runs if _plain(r.status) == 'failed')
        lines.append(f'The task "{title}" (id {task.id}) that you created ran {len(runs)} times since '
                     f'you last heard about it ({len(runs) - failed} finished, {failed} failed).')
        older = runs[:-1][-OTHER_RUNS_SHOWN:]
        lines.append('Earlier runs:\n' + '\n'.join(
            f'- run {r.id}: {_plain(r.status)}'
            + (f' ({str(r.error)[:200]})' if _plain(r.status) == 'failed' and r.error else '')
            for r in older))
        run = runs[-1]
    if run is not None:
        if _plain(run.status) == 'failed':
            lines.append(f'Latest run {run.id} failed: {str(run.error or "no reason given")[:1000]}\n'
                         'You can resume it with Start Task (resume=true) if trying again makes sense.')
        else:
            text = _result_text(run)
            lines.append(untrusted_text_block(f'Result of run {run.id}', text, RESULT_CHARS)
                         if text else f'Run {run.id} finished without a written result.')
            pages = await _run_artifacts(str(run.id))
            if pages:
                lines.append('Artifacts from this run: ' + ', '.join(
                    f'"{p.title}" (artifact_id {p.id}, version {p.version})' for p in pages)
                    + '. Point the person to the artifact rather than repeating it.')
    lines.append(
        'Decide whether the person needs to know. If they do, tell them with Notify Me in a short summary'
        + (' (or answer here if they are in this chat)' if for_chat else '')
        + '. If not, carry on with what you were doing, or stop. Do not reply only to acknowledge this.')
    return '\n\n'.join(lines)


# --- delivery --------------------------------------------------------------------

def _age(row: TaskReport, now: datetime) -> timedelta:
    try:
        return now - datetime.fromisoformat(str(row.queued_at))
    except (TypeError, ValueError):
        return timedelta(0)


async def _release(row: TaskReport, now: datetime, seconds: int, *, attempt: bool = False) -> None:
    update: dict[str, Any] = {'status': PENDING, 'claimed_at': None,
                              'next_at': _fmt(now + timedelta(seconds=seconds))}
    if attempt:
        update['attempts'] = int(row.attempts or 0) + 1
    await TaskReport.update_one({'id': row.id, 'status': DELIVERING}, update)


async def _settle(row: TaskReport, status: str, outcome: str, **extra: Any) -> None:
    await TaskReport.update_one(
        {'id': row.id, 'status': DELIVERING},
        {'status': status, 'outcome': outcome, 'delivered_at': _fmt(_now()), 'claimed_at': None, **extra})


async def _fall_back(row: TaskReport, task: Any, why: str) -> None:
    from cognitrix.tasks.run import TaskRun

    run = await TaskRun.get(row.run_ids[-1]) if row.run_ids else None
    if task is not None and run is not None:
        await _tell_person(task, run, why)
    await _settle(row, FALLBACK, why[:300])


def _notify_tool() -> list[Any]:
    from cognitrix.tools.base import ToolManager

    tool = ToolManager.get_by_name('Notify Me') or ToolManager.get_by_name('notify_me')
    return [tool] if tool is not None else []


async def _deliver_to_chat(row: TaskReport, task: Any, now: datetime) -> None:
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    agent = await find_visible_agent(row.user_id, agent_id=row.agent_id)
    session = await Session.get(row.target_id)
    if agent is None:
        return await _fall_back(row, task, 'The agent that created it is no longer available.')
    if (session is None or str(session.user_id or '') != row.user_id
            or str(session.agent_id or '') != row.agent_id):
        return await _fall_back(row, task, 'The chat it was created in no longer exists.')
    failed = 'failed' in (row.run_statuses or [])
    title = getattr(task, 'title', '') or 'A task'

    async def done(turn_status: str, _reply: str) -> None:
        # Quiet: the agent decides whether to tell the person. Only a turn
        # that could not handle the result alerts them.
        if turn_status == 'error':
            await agent_chats._alert_once(
                row.user_id, row.target_id, f'Task "{title}" ' + ('failed' if failed else 'finished'),
                f'{agent.name} could not look at the result. Open the chat or the task to see it.')

    text = agent_chats.REPORT_PREFIX + await report_text(row, task, for_chat=True)
    outcome = await agent_chats.deliver_report(
        row.user_id, row.agent_id, row.target_id, text, sender=f'The task "{title}"',
        wait_seconds=0, on_done=done, extra_tools=_notify_tool())
    if outcome in ('steered', 'started'):
        return await _settle(row, DELIVERED, outcome)
    if outcome == 'paused':
        return await _settle(row, PAUSED, 'paused: agents replied several times in a row')
    if _age(row, now) > GIVE_UP_AFTER:
        return await _fall_back(row, task, 'The chat it was created in stayed busy, so the agent was not told.')
    await _release(row, now, BUSY_RETRY_SECONDS)


async def _deliver_to_task(row: TaskReport, task: Any, now: datetime) -> None:
    from cognitrix.tasks.automation import over_daily_cap
    from cognitrix.tasks.base import Task

    target = await Task.get(row.target_id)
    if target is None or target.deleted_at:
        return await _fall_back(row, task, 'The task that created it no longer exists.')
    if row.agent_id not in [str(a) for a in (target.assigned_agents or [])]:
        return await _fall_back(row, task, 'The agent that created it no longer runs the task it came from.')
    if await over_daily_cap(target, now):
        if _age(row, now) > GIVE_UP_AFTER:
            return await _fall_back(row, task, 'The task that created it reached its daily run limit.')
        return await _release(row, now, CAP_RETRY_SECONDS)
    created_by = getattr(task, 'created_by', None) or {}
    from cognitrix.api.routes.tasks import _enqueue_task_start

    title = getattr(task, 'title', '') or 'A task'
    try:
        run = await _enqueue_task_start(
            target,
            actor_key='scheduler',
            requested_by=created_by.get('requested_by') or row.user_id,
            authority_kind=created_by.get('authority_kind') or 'jwt',
            authority_id=created_by.get('authority_id') or row.user_id,
            trigger_kind='task_result',
            trigger_note=f'Result of "{title}"'[:200],
            trigger_event_id=str(row.id),
        )
    except Exception as exc:
        code = getattr(exc, 'status_code', None)
        if code == 404:
            return await _fall_back(row, task, 'The task that created it no longer exists.')
        if code == 409:  # the creating run is still going: try once it ends
            if _age(row, now) > GIVE_UP_AFTER:
                return await _fall_back(row, task, 'The task that created it stayed busy.')
            return await _release(row, now, BUSY_RETRY_SECONDS)
        if code in (401, 403):
            return await _fall_back(row, task, 'The task that created it may no longer run.')
        if int(row.attempts or 0) + 1 >= len(ERROR_RETRY_SECONDS):
            return await _fall_back(row, task, 'The result could not be handed to the task that created it.')
        logger.debug('Report %s deferred (%s)', row.id, exc)
        return await _release(row, now, ERROR_RETRY_SECONDS[int(row.attempts or 0)], attempt=True)
    await _settle(row, DELIVERED, 'run', delivered_run_id=str(run.id))


async def _recover_claims(now: datetime) -> None:
    cutoff = _fmt(now - timedelta(seconds=CLAIM_SECONDS))
    for row in await TaskReport.find({'status': DELIVERING}) or []:
        if row.claimed_at and row.claimed_at > cutoff:
            continue
        await TaskReport.update_one({'id': row.id, 'status': DELIVERING},
                                    {'status': PENDING, 'claimed_at': None})


async def deliver_due_reports(now: datetime | None = None) -> int:
    """Send every due report to its agent. Runs in the scheduler loop."""
    from cognitrix.tasks.base import Task

    now = now or _now()
    try:
        await _recover_claims(now)
    except Exception:
        logger.exception('Could not recover interrupted task reports')
    try:
        await reconcile_missing_reports(now)
    except Exception:
        logger.exception('Could not look for task results that were never reported')
    due = [row for row in await TaskReport.find({'status': PENDING}) or []
           if not row.next_at or row.next_at <= _fmt(now)]
    sent = 0
    for row in sorted(due, key=lambda r: (r.queued_at or '', str(r.id))):
        try:
            if await TaskReport.update_one({'id': row.id, 'status': PENDING},
                                           {'status': DELIVERING, 'claimed_at': _fmt(now)}) != 1:
                continue
            # Runs may have been added to the row since it was read.
            row = await TaskReport.get(row.id) or row
            row.status = DELIVERING
            task = await Task.get(row.task_id)
            if task is None:
                await _settle(row, FALLBACK, 'the task no longer exists')
                continue
            if row.target_kind == 'chat':
                await _deliver_to_chat(row, task, now)
            else:
                await _deliver_to_task(row, task, now)
            sent += 1
        except Exception:
            logger.exception('Could not deliver task report %s', getattr(row, 'id', '?'))
            try:
                await _release(row, now, ERROR_RETRY_SECONDS[0], attempt=True)
            except Exception:
                logger.debug('Could not release task report %s', row.id, exc_info=True)
    return sent


async def brief_for_run(run: Any) -> str:
    """The report a task_result run handles, for its automation brief."""
    from cognitrix.tasks.base import Task

    report_id = getattr(run, 'trigger_event_id', None)
    row = await TaskReport.get(report_id) if report_id else None
    if row is None:
        return ''
    task = await Task.get(row.task_id)
    if task is None:
        return ''
    return await report_text(row, task, for_chat=False)
