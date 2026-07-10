"""Task schedule engine.

One asyncio loop (started by the FastAPI lifespan — never by the Celery
worker) ticks every TICK_SECONDS, claims due tasks by compare-and-set on
next_run_at, and enqueues them through the normal Celery start path. The
claim advances the schedule BEFORE enqueueing, so a crash mid-fire loses at
most one occurrence instead of double-firing.

Timezone contract: everything stored is naive UTC '%Y-%m-%d %H:%M:%S'
(the sqlite adapter's *_at format). Cron expressions are the exception in
spirit: they're evaluated on the server's wall clock ("0 9 * * *" = 9am
server time) and the resulting instant is converted back to naive UTC.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from croniter import croniter

from cognitrix.models.api_key import normalize_expiry
from cognitrix.tasks.base import Task

logger = logging.getLogger('cognitrix.log')

FMT = '%Y-%m-%d %H:%M:%S'
MIN_INTERVAL = 60
TICK_SECONDS = 20

SCHEDULE_FIELDS = ('schedule_at', 'schedule_interval', 'schedule_cron',
                   'next_run_at', 'schedule_enabled')


def normalize_schedule_at(value: str | None) -> str | None:
    """Normalize a scheduled datetime without relying on an import re-export."""
    return normalize_expiry(value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_schedule(task: Task, *, respecified: bool, now: datetime | None = None) -> str | None:
    """Rejection reason, or None when the schedule is acceptable.

    The past-time check only applies when the schedule was just (re)specified:
    a carried-over, already-fired schedule_at must not block unrelated edits.
    """
    set_types = [f for f in ('schedule_at', 'schedule_interval', 'schedule_cron')
                 if getattr(task, f)]
    if len(set_types) > 1:
        return 'set at most one of schedule_at, schedule_interval, schedule_cron'
    if task.schedule_interval is not None and task.schedule_interval < MIN_INTERVAL:
        return f'schedule_interval must be at least {MIN_INTERVAL} seconds'
    if task.schedule_cron:
        try:
            # get_next (not bare construction) also rejects well-formed but
            # unsatisfiable expressions like "0 0 30 2 *" (Feb 30) — croniter
            # raises CroniterBadDateError there, a ValueError subclass. Without
            # this, such a cron passes validation and 500s later in compute.
            croniter(task.schedule_cron, now or _utcnow()).get_next(datetime)
        except (ValueError, KeyError) as exc:
            return f'invalid cron expression: {exc}'
    if task.schedule_at:
        try:
            at = datetime.fromisoformat(task.schedule_at)
        except ValueError:
            return 'schedule_at is not a valid datetime'
        if respecified and at <= (now or _utcnow()):
            return 'schedule_at is in the past'
    return None


def compute_next_run(task: Task, now: datetime | None = None) -> str | None:
    """Next occurrence as a naive-UTC string, or None when unscheduled."""
    now = now or _utcnow()
    if task.schedule_at:
        return task.schedule_at
    if task.schedule_interval:
        return (now + timedelta(seconds=task.schedule_interval)).strftime(FMT)
    if task.schedule_cron:
        local_tz = datetime.now().astimezone().tzinfo
        local_now = now.replace(tzinfo=timezone.utc).astimezone(local_tz)
        nxt = croniter(task.schedule_cron, local_now).get_next(datetime)
        return nxt.astimezone(timezone.utc).replace(tzinfo=None).strftime(FMT)
    return None


async def tick(now: datetime | None = None) -> int:
    """One scheduler pass. Returns the number of runs enqueued."""
    now = now or _utcnow()
    fired = 0
    tasks = await Task.find({'schedule_enabled': True})
    for task in tasks or []:
        try:
            if not task.next_run_at:
                continue
            if datetime.fromisoformat(task.next_run_at) > now:
                continue

            prev = {'next_run_at': task.next_run_at, 'schedule_enabled': True}
            if task.schedule_at:  # one-shot: fire once, then off
                claim = {'next_run_at': None, 'schedule_enabled': False}
            else:
                claim = {'next_run_at': compute_next_run(task, now)}
            # Non-null claim values that uniquely pin our claim for this id, for
            # a CAS revert later. Kept as its own dict: Model.update_one mutates
            # the data dict it's handed (injects updated_at), so `claim` is not
            # reusable as a WHERE clause afterwards.
            claim_cond = {'id': task.id, **{k: v for k, v in claim.items() if v is not None}}
            # CAS on the exact stored string — rowcount 0 means another writer
            # (a concurrent tick or an edit) got there first.
            claimed = await Task.update_one(
                {'id': task.id, 'next_run_at': task.next_run_at}, dict(claim))
            if claimed != 1:
                continue
            for key, value in claim.items():
                setattr(task, key, value)

            from cognitrix.api.routes.tasks import _enqueue_task_start
            try:
                await _enqueue_task_start(task)
                fired += 1
            except Exception as exc:
                status = getattr(exc, 'status_code', None)
                if status == 409 and not task.schedule_at:
                    # Recurring overlap: drop this occurrence, schedule already
                    # advanced to the next one.
                    logger.debug('Scheduler skipped task %s: run already active', task.id)
                else:
                    # One-shot blocked by an active run, broker down, or an
                    # unexpected failure: put the claim back so it retries next
                    # tick instead of silently never firing. CAS the revert on
                    # our own claim values (broker probes make this window
                    # seconds-long) so a concurrent pause/edit isn't clobbered.
                    await Task.update_one(dict(claim_cond), dict(prev))
                    logger.warning('Scheduler could not start task %s (%s); will retry',
                                   task.id, exc)
        except Exception:
            logger.exception('Scheduler skipped task %s', getattr(task, 'id', '?'))
    return fired


async def scheduler_loop() -> None:
    # ponytail: per-process — single uvicorn worker assumed; distributed lock
    # (or moving to celery beat) if this ever runs multi-instance.
    logger.info('Task scheduler started (tick every %ss)', TICK_SECONDS)
    while True:
        try:
            await tick()
        except Exception:
            logger.exception('Scheduler tick failed')
        await asyncio.sleep(TICK_SECONDS)
