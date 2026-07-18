import asyncio
import inspect
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from cognitrix.artifacts import absolute_path, bound_task_run_artifact
from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, require
from cognitrix.tasks import Task
from cognitrix.tasks.base import TaskStatus
from cognitrix.tasks.budget import TaskBudget, stable_actor_key
from cognitrix.tasks.events import event_payload, events_after, step_tool_calls
from cognitrix.tasks.repository import (
    ActiveRunExists,
    RunRepository,
    TaskDeleted,
    UnsupportedDurableTaskBackend,
)
from cognitrix.tasks.results import StepResult, canonical_artifact_ref
from cognitrix.tasks.run import (
    TaskRun,
    TaskRunHead,
    TaskRunStatus,
    run_acl_allowed,
)
from cognitrix.tasks.scheduler import (SCHEDULE_FIELDS, compute_next_run,
                                       normalize_schedule_at, validate_schedule)
from cognitrix.tasks.step import TaskRunStep

from ...celery_worker import broker_available, ensure_local_worker, run_task

_ACTIVE_RUN_STATUSES = (
    TaskRunStatus.QUEUED,
    TaskRunStatus.RUNNING,
    TaskRunStatus.CANCELLING,
)
_TERMINAL_RUN_STATUSES = (
    TaskRunStatus.COMPLETED,
    TaskRunStatus.FAILED,
    TaskRunStatus.CANCELLED,
)
_TASK_RUN_SCAN_BATCH = 100
# Offset is a compatibility surface for the current UI, but it must not turn
# one authenticated request into an unbounded history scan.  Keep both the
# visible window and the underlying ACL scan finite; cursor pagination can
# replace this compatibility window in a future API version.
_TASK_RUN_MAX_OFFSET = 1_000
_TASK_RUN_MAX_SCAN_ROWS = 5_000


def _check_task_allowlists(ctx: AuthContext, task: Task) -> None:
    """Invoke-path allowlist enforcement: a key restricted to certain
    teams/agents may only execute tasks within them. JWT passes everything."""
    if task.team_id and not ctx.team_allowed(task.team_id):
        raise HTTPException(status_code=403, detail="API key not allowed for this task's team")
    for agent_id in task.assigned_agents or []:
        if not ctx.agent_allowed(agent_id):
            raise HTTPException(status_code=403, detail="API key not allowed for this task's agents")


def _task_json(task: Task) -> dict:
    """API projection of a task. Callback fields never leave the server —
    callback URLs routinely embed capability tokens."""
    data = task.json()
    if isinstance(data, dict):
        data.pop('callback_url', None)
        data.pop('callback_key_id', None)
        data.pop('schedule_requested_by', None)
        data.pop('schedule_authority_kind', None)
        data.pop('schedule_authority_id', None)
        data.pop('deleted_at', None)
    return data


def _task_is_deleted(task: Task | None) -> bool:
    return task is None or bool(task.deleted_at)


async def _visible_task(task_id: str) -> Task | None:
    task = await Task.get(task_id)
    return None if _task_is_deleted(task) else task


async def _update_existing_task_if_live(task: Task) -> int:
    """Full-row edit fenced by both durable task deletion markers.

    ``Model.save`` updates by id only. A DELETE can therefore commit after
    ``save_task`` reads a live row and then have that stale model clear the
    Task-row tombstone. Keep creates on the model insert path, but make edits
    one conditional database statement. The head predicate also closes the
    deliberate crash-repair interval where the authoritative admission
    tombstone has committed but its Task projection has not.
    """
    from odbms import DBMS

    database = DBMS.Database
    if database is None:
        raise RuntimeError("Database not initialized")

    # Preserve the validation, computed-field, timestamp, and hook lifecycle
    # of Model.save while replacing only its unsafe id-only UPDATE.
    await task._run_hooks(task._before_save_hooks)
    task.validate_fields()
    task.compute_fields()
    task.updated_at = datetime.now()
    data = Task.normalise(task.model_dump(), "params")
    # Primary keys identify the conditional target; they are never mutable
    # update data (MongoDB rejects attempts to $set its immutable ``_id``).
    data.pop("id", None)
    data.pop("_id", None)

    dbms = getattr(database, "dbms", "")
    if dbms == "mongodb":
        # Durable task deletion is intentionally unsupported on MongoDB, but
        # retain the Task-row fence for existing installations.
        conditions = Task.normalise(
            {"id": task.id, "deleted_at": None},
            "params",
        )
        changed = await database.update_one(
            Task.table_name(),
            conditions,
            data,
        )
    elif dbms in ("sqlite", "postgresql", "mysql"):
        def marker(name: str) -> str:
            return _sql_parameter(dbms, name)

        params = {"task_id": task.id}
        assignments = []
        for field, value in data.items():
            parameter = f"set_{field}"
            assignments.append(f"{field} = {marker(parameter)}")
            params[parameter] = value
        cursor = await database.query(
            f"UPDATE {Task.table_name()} SET {', '.join(assignments)} "
            f"WHERE id = {marker('task_id')} "
            "AND deleted_at IS NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {TaskRunHead.table_name()} "
            f"WHERE id = {marker('task_id')} AND deleted_at IS NOT NULL)",
            params,
        )
        changed = int(getattr(cursor, "rowcount", 0) or 0)
    else:
        raise RuntimeError(
            f"Atomic task edits are unsupported for database {dbms!r}"
        )

    if changed == 1:
        await task._run_hooks(task._after_save_hooks)
    return int(changed or 0)


def _run_identity(ctx: AuthContext) -> tuple[str | None, str]:
    """Return sanitized audit and concurrency identities for a run request."""
    user_id = str(ctx.user.id) if getattr(ctx, 'user', None) is not None else None
    if ctx.api_key is not None:
        return user_id, stable_actor_key('api_key', str(ctx.api_key.id))
    return user_id, stable_actor_key('jwt', user_id) if user_id else 'system'


def _run_authority(ctx: AuthContext) -> tuple[str, str | None]:
    """Return only the persisted, non-secret authority reference."""
    if ctx.api_key is not None:
        return "api_key", str(ctx.api_key.id)
    user_id = str(ctx.user.id) if getattr(ctx, "user", None) is not None else None
    return ("jwt", user_id) if user_id else ("system", None)


async def _task_projection(task: Task) -> dict:
    """Project TaskRun lifecycle state over the legacy Task status cache."""
    data = _task_json(task)
    latest = await RunRepository().latest_run(task.id)
    if latest is None:
        data['run_id'] = None
        data['run_status'] = None
        return data
    return _task_projection_for_run(task, latest, data=data)


def _task_projection_for_run(
    task: Task,
    latest: TaskRun,
    *,
    data: dict | None = None,
) -> dict:
    """Project a known run without a lossy re-query after publication."""
    data = data or _task_json(task)
    task_status = {
        TaskRunStatus.QUEUED: TaskStatus.IN_PROGRESS,
        TaskRunStatus.RUNNING: TaskStatus.IN_PROGRESS,
        TaskRunStatus.CANCELLING: TaskStatus.IN_PROGRESS,
        TaskRunStatus.COMPLETED: TaskStatus.COMPLETED,
        TaskRunStatus.FAILED: TaskStatus.FAILED,
        TaskRunStatus.CANCELLED: TaskStatus.CANCELLED,
    }[latest.status]
    data['run_id'] = latest.id
    data['run_status'] = latest.status
    data['status'] = task_status
    return data


async def _set_callback(task: Task, callback_url: str | None, ctx: AuthContext) -> None:
    """Attach a completion webhook to a task (mutates, does not save).
    Key-authed only: the key's webhook_secret signs deliveries."""
    if not callback_url:
        return
    if ctx.api_key is None:
        raise HTTPException(status_code=400, detail="callback_url requires API-key authentication")
    from cognitrix.utils.webhooks import check_callback_url
    reason = await asyncio.to_thread(check_callback_url, callback_url)
    if reason:
        raise HTTPException(status_code=400, detail=reason)
    task.callback_url = callback_url
    task.callback_key_id = ctx.api_key.id


async def _run_summary(
    run: TaskRun,
    *,
    steps: list[TaskRunStep] | None = None,
) -> dict:
    """Run projection for list views: full per-step results (~8KB each) are
    execution-side data (dependency prompts, resume) — polling clients only
    need statuses. Long descriptions are trimmed too."""
    data = _public_run_projection(run)
    if steps is None:
        plan = await RunRepository().hydrate_plan(
            run.id,
            include_results=False,
        )
    elif steps:
        plan = [
            row.to_plan_entry()
            for row in sorted(steps, key=lambda item: item.step_index)
        ]
    else:
        # Pre-row legacy runs still keep their authoritative plan snapshot on
        # the run.  A batched empty step lookup must not erase it from lists.
        plan = list(run.plan or [])
    data['plan'] = [
        {
            'index': s.get('index'),
            'title': s.get('title'),
            'description': (s.get('description') or '')[:200],
            'agent_name': s.get('agent_name'),
            'dependencies': s.get('dependencies') or [],
            'status': s.get('status'),
            'attempts': s.get('attempts'),
            'gate': s.get('gate'),
        }
        for s in plan
    ]
    return data


async def _cursor_records(cursor) -> list[dict]:
    """Materialize a portable ODBMS cursor while its connection is live."""
    if cursor is None:
        return []
    description = getattr(cursor, 'description', None)
    rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else cursor
    if inspect.isawaitable(rows):
        rows = await rows
    columns = [item[0] for item in (description or [])]
    records = []
    for row in rows or []:
        if isinstance(row, Mapping):
            records.append(dict(row))
            continue
        try:
            records.append(dict(row))
        except (TypeError, ValueError):
            records.append(dict(zip(columns, row)))
    return records


async def _relational_records(database, statement: str, params: dict) -> list[dict]:
    """Fetch before PostgreSQL/MySQL pooled cursors release their lease."""
    pool = getattr(database, '_pool', None)
    if pool is None:
        return await _cursor_records(await database.query(statement, params))
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(statement, params)
            return await _cursor_records(cursor)


def _sql_parameter(dbms: str, name: str) -> str:
    return f':{name}' if dbms == 'sqlite' else f'%({name})s'


async def _task_run_rows(
    task_id: str,
    *,
    limit: int,
    offset: int,
) -> list[TaskRun]:
    """Read one deterministic database page without materializing history."""
    from odbms import DBMS

    database = DBMS.Database
    dbms = getattr(database, 'dbms', '')
    if dbms == 'mongodb':
        rows = await database.find(
            TaskRun.table_name(),
            {'task_id': task_id},
            skip=offset,
            limit=limit,
            sort=[('created_at', -1), ('_id', -1)],
        )
    elif dbms in ('sqlite', 'postgresql', 'mysql'):
        rows = await _relational_records(
            database,
            f'SELECT * FROM {TaskRun.table_name()} '
            f'WHERE task_id = {_sql_parameter(dbms, "task_id")} '
            'ORDER BY created_at DESC, id DESC '
            f'LIMIT {_sql_parameter(dbms, "limit")} '
            f'OFFSET {_sql_parameter(dbms, "offset")}',
            {'task_id': task_id, 'limit': limit, 'offset': offset},
        )
    else:
        raise RuntimeError(
            f'Indexed task-run paging is unsupported for database {dbms!r}'
        )
    return [TaskRun(**TaskRun.normalise(row)) for row in rows]


async def _authorized_task_run_page(
    task_id: str,
    ctx: AuthContext,
    *,
    limit: int,
    offset: int,
) -> list[TaskRun]:
    """Page the ACL-visible sequence without ever loading all task runs.

    ACL snapshots contain portable JSON agent lists, so applying their exact
    semantics in SQL would require divergent queries for every supported
    database. Scan fixed-size ordered pages instead: memory and every database
    read stay bounded while offset still counts visible runs, not hidden rows.
    """
    selected: list[TaskRun] = []
    visible_seen = 0
    database_offset = 0
    while (
        len(selected) < limit
        and database_offset < _TASK_RUN_MAX_SCAN_ROWS
    ):
        batch_limit = min(
            _TASK_RUN_SCAN_BATCH,
            _TASK_RUN_MAX_SCAN_ROWS - database_offset,
        )
        batch = await _task_run_rows(
            task_id,
            limit=batch_limit,
            offset=database_offset,
        )
        if not batch:
            break
        database_offset += len(batch)
        for run in batch:
            if not run_acl_allowed(run, ctx):
                continue
            if visible_seen < offset:
                visible_seen += 1
                continue
            selected.append(run)
            visible_seen += 1
            if len(selected) == limit:
                break
        if len(batch) < batch_limit:
            break
    return selected


async def _task_run_step_rows(run_ids: list[str]) -> list[TaskRunStep]:
    """Load authoritative steps only for the runs in the selected page."""
    if not run_ids:
        return []
    from odbms import DBMS

    database = DBMS.Database
    dbms = getattr(database, 'dbms', '')
    if dbms == 'mongodb':
        # MongoDB treats limit=0 as unbounded. The run-id predicate still
        # bounds this read to the selected history page.
        rows = await database.find(
            TaskRunStep.table_name(),
            {'run_id': {'$in': run_ids}},
            limit=0,
            sort=[('run_id', 1), ('step_index', 1)],
        )
    elif dbms in ('sqlite', 'postgresql', 'mysql'):
        params = {f'run_id_{index}': run_id for index, run_id in enumerate(run_ids)}
        placeholders = ', '.join(
            _sql_parameter(dbms, name) for name in params
        )
        rows = await _relational_records(
            database,
            f'SELECT * FROM {TaskRunStep.table_name()} '
            f'WHERE run_id IN ({placeholders}) '
            'ORDER BY run_id ASC, step_index ASC',
            params,
        )
    else:
        raise RuntimeError(
            f'Indexed task-step paging is unsupported for database {dbms!r}'
        )
    return [TaskRunStep(**TaskRunStep.normalise(row)) for row in rows]


async def _run_detail(run: TaskRun) -> dict:
    """Hydrate a run from authoritative rows without exposing step bodies."""
    data = _public_run_projection(run)
    plan = await RunRepository().hydrate_plan(
        run.id,
        include_results=False,
    )
    data['plan'] = [
        {
            'index': step.get('index'),
            'title': step.get('title'),
            'description': step.get('description') or '',
            'expected_output': step.get('expected_output') or '',
            'verification_criteria': step.get('verification_criteria') or '',
            'agent_name': step.get('agent_name'),
            'dependencies': step.get('dependencies') or [],
            'status': step.get('status'),
            'attempts': step.get('attempts'),
            'gate': step.get('gate'),
        }
        for step in plan
    ]
    return data


def _public_run_projection(run: TaskRun) -> dict:
    """Return polling-safe run metadata without execution internals or bodies."""
    stored = run.json()
    public_fields = (
        'id',
        'task_id',
        'status',
        'requested_by',
        'resume_from_run_id',
        'queued_at',
        'started_at',
        'completed_at',
        'cancel_requested_at',
        'error_code',
        'error',
        'budget',
        'usage',
        'created_at',
        'updated_at',
    )
    data = {field: stored.get(field) for field in public_fields if field in stored}
    data['force_cancel_ready'] = RunRepository().force_cancel_ready(run)
    return data


async def _result_payload(
    result: StepResult,
    *,
    task_id: str,
    run: TaskRun,
) -> dict:
    """Project only canonically bound artifacts under authenticated URLs."""
    data = result.model_dump(mode='json')
    artifacts = []
    seen: set[str] = set()
    for reference in result.artifacts:
        artifact_id = str(reference.id)
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifact = await bound_task_run_artifact(
            artifact_id,
            run_id=str(run.id),
            user_id=run.requested_by,
        )
        if artifact is not None:
            artifacts.append(canonical_artifact_ref(
                artifact,
                task_id=task_id,
                run_id=str(run.id),
            ).model_dump(mode='json'))
    data['artifacts'] = artifacts
    return data


def _result_references_artifact(value, artifact_id: str) -> bool:
    if value is None:
        return False
    try:
        result = StepResult.from_stored(value)
    except (TypeError, ValueError):
        return False
    return any(str(item.id) == artifact_id for item in result.artifacts)


async def _run_references_artifact(run: TaskRun, artifact_id: str) -> bool:
    if _result_references_artifact(run.result_data or run.result, artifact_id):
        return True
    rows = await TaskRunStep.find({'run_id': run.id})
    if any(_result_references_artifact(row.result, artifact_id) for row in rows):
        return True
    return any(
        _result_references_artifact(entry.get('result'), artifact_id)
        for entry in (run.plan or [])
    )


async def _authorized_task_run(
    task_id: str,
    run_id: str,
    ctx: AuthContext,
) -> tuple[Task, TaskRun]:
    """Load and authorize a run before exposing execution metadata/results."""
    task, run = await asyncio.gather(Task.get(task_id), TaskRun.get(run_id))
    if task is None or run is None or run.task_id != task_id:
        raise HTTPException(status_code=404, detail='Task run not found')
    if not run_acl_allowed(run, ctx):
        raise HTTPException(status_code=403, detail='Not allowed to access this task run')
    return task, run


async def _active_run(task_id: str) -> TaskRun | None:
    return await RunRepository().active_run(task_id)


def _event_cursor(request: Request, after: int | None) -> int:
    values = [after or 0]
    try:
        values.append(int(request.headers.get('last-event-id', '0')))
    except (TypeError, ValueError):
        pass
    return max(0, *values)


async def _task_run_event_stream(
    request: Request,
    run_id: str,
    after: int,
    *,
    poll_interval: float = 0.5,
):
    last_sequence = after
    terminal_quiet_pass = False
    while not await request.is_disconnected():
        rows = await events_after(run_id, last_sequence)
        if rows:
            terminal_quiet_pass = False
            for row in rows:
                last_sequence = row.sequence
                yield {
                    'event': 'task_run',
                    'id': str(row.sequence),
                    'data': json.dumps(event_payload(row)),
                }

        fresh = await TaskRun.get(run_id)
        terminal = bool(fresh and fresh.status in _TERMINAL_RUN_STATUSES)
        if terminal and not rows:
            if terminal_quiet_pass:
                return
            terminal_quiet_pass = True
        await asyncio.sleep(poll_interval)

logger = logging.getLogger('cognitrix.log')


async def _step_tool_call_projection(run_id: str, step_index: int):
    try:
        return await step_tool_calls(run_id, step_index)
    except Exception:
        logger.exception(
            'Could not project tool calls for run %s step %s',
            run_id,
            step_index,
        )
        return None

tasks_api = APIRouter(
    prefix='/tasks',
    dependencies=[Depends(crud_scope)]
)

# Execute routes live on their own router: starting/cancelling a run is the
# 'run' scope, not the 'write' that crud_scope would infer from POST (or the
# 'read' it would infer from the legacy GET start route). Registered BEFORE
# tasks_api in routes/__init__.py so /tasks/start/{id} isn't swallowed by
# GET /tasks/{task_id}.
tasks_run_api = APIRouter(
    prefix='/tasks',
    dependencies=[Depends(require('run'))]
)

@tasks_api.get('')
async def list_tasks():
    tasks = await Task.all()
    tasks = [task for task in tasks if not task.deleted_at]
    return await asyncio.gather(*[_task_projection(task) for task in tasks])

@tasks_api.post('')
async def save_task(request: Request, task: Task, background_tasks: BackgroundTasks,
                    ctx: AuthContext = Depends(get_auth_context)):
    # autostart executes the orchestrator — for API keys that is the 'run'
    # scope + allowlists, not something 'write' alone may trigger.
    if task.autostart and ctx.api_key is not None:
        if not ctx.has_scope('run'):
            raise HTTPException(status_code=403, detail="API key missing required scope: run (autostart)")
        _check_task_allowlists(ctx, task)

    stored = await Task.get(task.id) if task.id else None
    if stored is not None and stored.deleted_at:
        raise HTTPException(status_code=404, detail="Task not found")
    # Tombstones are server-owned. Full-row creates/edits cannot hide or
    # resurrect a task by supplying this field themselves.
    task.deleted_at = None
    # A schedule TYPE field (at/interval/cron) in the payload means the client
    # is (re)defining the schedule; schedule_enabled alone is a pause/resume
    # toggle over the stored schedule (like POST /tasks/{id}/schedule), not a
    # respecification that would wipe the type. next_run_at is server-owned.
    type_fields = ('schedule_at', 'schedule_interval', 'schedule_cron')
    respecified = bool(set(type_fields) & task.model_fields_set)
    toggling = 'schedule_enabled' in task.model_fields_set and not respecified
    if stored:
        # save() below is a full-row write: anything the client didn't send
        # resets to default. Callback fields never round-trip (stripped from
        # projections) and schedule fields may be omitted by API clients —
        # carry them over instead of silently wiping them.
        for field in ('callback_url', 'callback_key_id'):
            if field not in task.model_fields_set:
                setattr(task, field, getattr(stored, field))
        if not respecified:
            desired_enabled = task.schedule_enabled if toggling else None
            # Carry the whole schedule over. A title-only edit leaves it as-is
            # (recomputing would push an enabled interval's next fire back).
            for field in SCHEDULE_FIELDS:
                setattr(task, field, getattr(stored, field))
            if toggling:
                task.schedule_enabled = desired_enabled
                task.next_run_at = compute_next_run(task) if desired_enabled else None

    # Scheduling executes the orchestrator later — same rule as autostart:
    # 'run' scope + allowlists for API keys. Covers (re)defining a schedule,
    # toggling one on, and editing a task whose stored schedule is enabled.
    if ctx.api_key is not None and (respecified or toggling or (stored is not None and stored.schedule_enabled)):
        if not ctx.has_scope('run'):
            raise HTTPException(status_code=403, detail="API key missing required scope: run (schedule)")
        _check_task_allowlists(ctx, task)

    if respecified:
        if task.schedule_at:
            try:
                task.schedule_at = normalize_schedule_at(task.schedule_at)
            except ValueError:
                raise HTTPException(status_code=422, detail="schedule_at is not a valid datetime")
        has_type = bool(task.schedule_at or task.schedule_interval or task.schedule_cron)
        if has_type and 'schedule_enabled' not in task.model_fields_set:
            task.schedule_enabled = True  # an API-set schedule must not be silently inert
        reason = validate_schedule(task, respecified=True)
        if reason:
            raise HTTPException(status_code=422, detail=reason)
        if has_type and task.schedule_enabled:
            task.next_run_at = compute_next_run(task)
        else:
            task.next_run_at = None
            task.schedule_enabled = False

    if task.schedule_enabled:
        requested_by, _actor_key = _run_identity(ctx)
        authority_kind, authority_id = _run_authority(ctx)
        task.schedule_requested_by = requested_by
        task.schedule_authority_kind = authority_kind
        task.schedule_authority_id = authority_id
    elif respecified:
        task.schedule_requested_by = None
        task.schedule_authority_kind = None
        task.schedule_authority_id = None

    if stored is None:
        await task.save()
    elif await _update_existing_task_if_live(task) != 1:
        # Either deletion marker may have won after the initial live read.
        # Do not autostart or return a projection for a stale edit.
        raise HTTPException(status_code=404, detail="Task not found")

    autostart_run = None
    if task.autostart:
        requested_by, actor_key = _run_identity(ctx)
        authority_kind, authority_id = _run_authority(ctx)
        autostart_run = await _enqueue_task_start(
            task,
            requested_by=requested_by,
            actor_key=actor_key,
            authority_kind=authority_kind,
            authority_id=authority_id,
        )

    if autostart_run is not None:
        return _task_projection_for_run(task, autostart_run)
    return await _task_projection(task)


class TaskAssignment(BaseModel):
    assigned_agents: list[str]
    team_id: str | None = None


@tasks_api.patch('/{task_id}/assignment')
async def assign_task(task_id: str, body: TaskAssignment,
                      ctx: AuthContext = Depends(get_auth_context)):
    """Update task ownership without touching execution or schedule state."""
    task = await _visible_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.assigned_agents = list(dict.fromkeys(body.assigned_agents))
    task.team_id = body.team_id
    _check_task_allowlists(ctx, task)
    await Task.update_one(
        {'id': task_id},
        {'assigned_agents': task.assigned_agents, 'team_id': task.team_id},
    )
    return await _task_projection(task)


async def _enqueue_task_start(
    task: Task,
    resume: bool = False,
    *,
    requested_by: str | None = None,
    actor_key: str = 'system',
    authority_kind: str | None = None,
    authority_id: str | None = None,
    budget: TaskBudget | dict | None = None,
) -> TaskRun:
    """Shared start path: 409-guard, broker probe, enqueue, mark in-progress.

    Guard both signals: an active TaskRun AND task IN_PROGRESS — the latter
    covers the enqueue→pickup window before the worker creates the run row
    (a duplicate start there would execute the task twice).
    """
    # Callers can hold a live-looking model while another request has already
    # tombstoned the persisted row. Reject before broker work in that case.
    if await _visible_task(task.id) is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Enqueue on the Celery broker. If the broker is unreachable, surface a
    # clear 503 instead of a 500, and do NOT mark the task in-progress. Probe
    # first so a down broker returns promptly rather than blocking on retries.
    # On the filesystem fallback broker, ensure_local_worker spawns a consumer
    # if none is running; on Redis it is a no-op (workers are external).
    broker_down_detail = "Task queue unavailable. Is the Celery broker (Redis) running and a worker started?"
    if not await asyncio.to_thread(ensure_local_worker):
        raise HTTPException(
            status_code=503,
            detail="Task queue unavailable — the local fallback worker failed to start. Check celery-queue/worker.log in the cognitrix workdir.",
        )
    if not await asyncio.to_thread(broker_available):
        raise HTTPException(status_code=503, detail=broker_down_detail)

    repo = RunRepository()
    if authority_kind is None:
        from cognitrix.tools.utils import current_execution_context

        current = current_execution_context()
        if current.api_key_id:
            authority_kind, authority_id = "api_key", current.api_key_id
            requested_by = requested_by or current.user_id
            actor_key = stable_actor_key('api_key', current.api_key_id)
        elif current.user_id:
            authority_kind, authority_id = "jwt", current.user_id
            requested_by = requested_by or current.user_id
            actor_key = stable_actor_key('jwt', current.user_id)
        else:
            authority_kind = "scheduler" if actor_key == "scheduler" else "system"
    resume_from_run_id = None
    if resume:
        prior = await TaskRun.find({'task_id': task.id})
        resumable = [
            item for item in prior
            if item.status in (TaskRunStatus.FAILED, TaskRunStatus.CANCELLED)
        ]
        resumable.sort(key=lambda item: item.json().get('created_at') or '', reverse=True)
        resume_from_run_id = resumable[0].id if resumable else None
    try:
        queued = await repo.create_queued(
            task_id=task.id,
            requested_by=requested_by,
            actor_key=actor_key,
            authority_kind=authority_kind,
            authority_id=authority_id,
            acl_team_id=task.team_id,
            acl_agent_ids=list(task.assigned_agents or []),
            callback_url=task.callback_url,
            callback_key_id=task.callback_key_id,
            resume_from_run_id=resume_from_run_id,
            budget=(
                budget.model_dump(mode='json', exclude_none=True)
                if isinstance(budget, TaskBudget)
                else budget
            ),
        )
    except ActiveRunExists:
        raise HTTPException(
            status_code=409,
            detail="Task already has an active run. Cancel it first.",
        )
    except TaskDeleted:
        raise HTTPException(status_code=404, detail="Task not found")
    except UnsupportedDurableTaskBackend as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Close delete/enqueue races after the durable reservation but before the
    # first externally observable side effect. Cancelling a queued run also
    # releases its TaskRunHead reservation.
    if await _visible_task(task.id) is None:
        await repo.request_cancel(
            queued.id,
            reason="task deleted before queue publication",
        )
        raise HTTPException(status_code=404, detail="Task not found")

    # Publish is the moment a worker can observe the run. Move the legacy
    # compatibility cache first so a fast worker's terminal status can never
    # be overwritten by a late IN_PROGRESS write from this request.
    previous_task_status = task.status
    previous_pid = task.pid
    task.status = TaskStatus.IN_PROGRESS
    task.pid = None
    await Task.update_one(
        {'id': task.id},
        {'status': TaskStatus.IN_PROGRESS.value, 'pid': None},
    )
    try:
        result = run_task.apply_async(args=[queued.id], retry=False)
    except Exception as exc:
        # Log the exception type only — the message can embed the broker URL
        # (with credentials, if configured).
        logger.error("Failed to enqueue task %s: %s", task.id, type(exc).__name__)
        await repo.mutate(
            queued.id,
            claim=None,
            updates={
                'status': TaskRunStatus.FAILED.value,
                'error_code': 'queue_publish_failed',
                'error': 'task queue publication failed',
                'completed_at': datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S'),
            },
            expected_statuses={TaskRunStatus.QUEUED},
        )
        # No job was published, so restore the cache changed before the
        # broker call. The predicate preserves a concurrent terminal writer.
        task.status = previous_task_status
        task.pid = previous_pid
        await Task.update_one(
            {'id': task.id, 'status': TaskStatus.IN_PROGRESS.value},
            {
                'status': previous_task_status.value,
                'pid': previous_pid,
            },
        )
        raise HTTPException(status_code=503, detail=broker_down_detail)

    queued = await repo.attach_queue_job_id(queued.id, result.id)
    task.pid = result.id
    # Partial write — a full-row save here would clobber concurrent edits and,
    # worse, revert the scheduler's just-advanced next_run_at claim.
    await Task.update_one({'id': task.id}, {'pid': result.id})
    return queued


@tasks_run_api.get('/start/{task_id}')
async def update_task_status(request: Request, task_id: str, resume: bool = False,
                             ctx: AuthContext = Depends(get_auth_context)):
    """Legacy UI start route. Deliberately takes no callback_url — capability-
    bearing URLs don't belong in query strings/access logs; API callers use
    POST /tasks/{id}/run."""
    task = await _visible_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _check_task_allowlists(ctx, task)
    requested_by, actor_key = _run_identity(ctx)
    authority_kind, authority_id = _run_authority(ctx)
    run = await _enqueue_task_start(
        task,
        resume=resume,
        requested_by=requested_by,
        actor_key=actor_key,
        authority_kind=authority_kind,
        authority_id=authority_id,
    )
    return _task_projection_for_run(task, run)


class TaskRunRequest(BaseModel):
    resume: bool = False
    callback_url: str | None = None
    budget: TaskBudget | None = None


@tasks_run_api.post('/{task_id}/run', status_code=202)
async def start_task_run(task_id: str, body: TaskRunRequest | None = None,
                         ctx: AuthContext = Depends(get_auth_context)):
    """API-first start/resume for a pre-created task, with optional completion
    webhook. Async — poll GET /tasks/{id} + /tasks/{id}/runs."""
    body = body or TaskRunRequest()
    task = await Task.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _check_task_allowlists(ctx, task)
    await _set_callback(task, body.callback_url, ctx)
    requested_by, actor_key = _run_identity(ctx)
    authority_kind, authority_id = _run_authority(ctx)
    enqueue_options = {
        'resume': body.resume,
        'requested_by': requested_by,
        'actor_key': actor_key,
        'authority_kind': authority_kind,
        'authority_id': authority_id,
    }
    if body.budget is not None:
        enqueue_options['budget'] = body.budget
    run = await _enqueue_task_start(task, **enqueue_options)
    return {
        'task_id': task.id,
        'run_id': run.id,
        'status': run.status,
    }


@tasks_run_api.post('/{task_id}/cancel')
async def cancel_task(task_id: str, ctx: AuthContext = Depends(get_auth_context)):
    task = await _visible_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    run = await _active_run(task_id)
    if run is not None:
        if not run_acl_allowed(run, ctx):
            raise HTTPException(
                status_code=403,
                detail='Not allowed to access this task run',
            )
        repository = RunRepository()
        if run.status == TaskRunStatus.CANCELLING:
            # A repeated request force-finalizes and advances the lease
            # generation, fencing a worker that failed to stop cooperatively.
            authoritative = await repository.force_cancel(run.id)
        else:
            # Queued runs terminalize immediately; running runs enter the
            # cooperative CANCELLING state with a durable status event.
            authoritative = await repository.request_cancel(run.id)
        if authoritative.status == TaskRunStatus.CANCELLED:
            task.status = TaskStatus.CANCELLED
            await Task.update_one(
                {'id': task.id},
                {'status': TaskStatus.CANCELLED.value},
            )
        return _public_run_projection(authoritative)

    _check_task_allowlists(ctx, task)

    if task.status == TaskStatus.IN_PROGRESS:
        # Enqueued but never picked up (or the worker died pre-run): cancel the
        # task directly; the orchestrator's entry guard makes a late pickup a
        # no-op and the prerun guard won't resurrect it.
        task.status = TaskStatus.CANCELLED
        await task.save()
        return _task_json(task)

    raise HTTPException(status_code=409, detail="Nothing to cancel — no active run.")


class ScheduleToggle(BaseModel):
    enabled: bool


@tasks_api.post('/{task_id}/schedule')
async def toggle_schedule(task_id: str, body: ScheduleToggle,
                          ctx: AuthContext = Depends(get_auth_context)):
    """Pause/resume a task's schedule. Resume recomputes next_run_at so a
    long-paused schedule doesn't fire from a stale instant."""
    task = await _visible_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not body.enabled:
        updates = {'schedule_enabled': False, 'next_run_at': None}
    else:
        if not (task.schedule_at or task.schedule_interval or task.schedule_cron):
            raise HTTPException(status_code=422, detail="Task has no schedule to enable")
        # Enabling arms orchestrator execution — same rule as autostart.
        if ctx.api_key is not None:
            if not ctx.has_scope('run'):
                raise HTTPException(status_code=403, detail="API key missing required scope: run (schedule)")
            _check_task_allowlists(ctx, task)
        if task.schedule_at and datetime.fromisoformat(task.schedule_at) <= \
                datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(status_code=422, detail="schedule_at is in the past; set a new time")
        requested_by, _actor_key = _run_identity(ctx)
        authority_kind, authority_id = _run_authority(ctx)
        updates = {
            'schedule_enabled': True,
            'next_run_at': compute_next_run(task),
            'schedule_requested_by': requested_by,
            'schedule_authority_kind': authority_kind,
            'schedule_authority_id': authority_id,
        }

    await Task.update_one({'id': task_id}, updates)
    for key, value in updates.items():
        setattr(task, key, value)
    return _task_json(task)


@tasks_api.get('/{task_id}/runs')
async def list_task_runs(
    task_id: str,
    ctx: AuthContext = Depends(get_auth_context),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=_TASK_RUN_MAX_OFFSET)] = 0,
):
    # The Task row is retained specifically so authorized historical runs
    # remain addressable after the authoring task is hidden.
    task = await Task.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='Task not found')
    page = await _authorized_task_run_page(
        task_id,
        ctx,
        limit=limit,
        offset=offset,
    )
    if not page:
        return []
    page_ids = [run.id for run in page]
    step_rows = await _task_run_step_rows(page_ids)
    steps_by_run: dict[str, list[TaskRunStep]] = {
        run_id: [] for run_id in page_ids
    }
    for row in step_rows:
        if row.run_id in steps_by_run:
            steps_by_run[row.run_id].append(row)
    return await asyncio.gather(*(
        _run_summary(run, steps=steps_by_run[run.id])
        for run in page
    ))


@tasks_api.get('/{task_id}/runs/{run_id}')
async def load_task_run(
    task_id: str,
    run_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    _task, run = await _authorized_task_run(task_id, run_id, ctx)
    return await _run_detail(run)


@tasks_api.get('/{task_id}/runs/{run_id}/result')
async def load_task_run_result(
    task_id: str,
    run_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    """Return the explicit typed final body, separate from polling metadata."""
    _task, run = await _authorized_task_run(task_id, run_id, ctx)
    if run.result_data is None and run.result is None:
        raise HTTPException(status_code=404, detail='Task run result not found')
    result = run.result_data or StepResult.from_stored(run.result)
    return await _result_payload(result, task_id=task_id, run=run)


@tasks_api.get('/{task_id}/runs/{run_id}/artifacts/{artifact_id}')
async def load_task_run_artifact(
    task_id: str,
    run_id: str,
    artifact_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    """Deliver a referenced artifact under the run's immutable ACL."""
    _task, run = await _authorized_task_run(task_id, run_id, ctx)
    artifact = await bound_task_run_artifact(
        artifact_id,
        run_id=str(run.id),
        user_id=run.requested_by,
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail='Task run artifact not found')
    if not await _run_references_artifact(run, artifact_id):
        raise HTTPException(status_code=404, detail='Task run artifact not found')
    try:
        path = absolute_path(artifact)
    except ValueError:
        raise HTTPException(status_code=404, detail='Task run artifact not found')
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Artifact data is unavailable')
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.filename)


@tasks_api.get('/{task_id}/runs/{run_id}/steps/{step_index}/result')
async def load_task_run_step_result(
    task_id: str,
    run_id: str,
    step_index: int,
    ctx: AuthContext = Depends(get_auth_context),
):
    """Return one authoritative typed step body after run authorization."""
    _task, run = await _authorized_task_run(task_id, run_id, ctx)
    row = await TaskRunStep.find_one({
        'run_id': run_id,
        'step_index': step_index,
    })
    if row is not None:
        payload = {
            'step_index': row.step_index,
            'status': row.status,
            'result': (
                await _result_payload(row.result, task_id=task_id, run=run)
                if row.result is not None
                else None
            ),
            'error': row.error,
        }
        tool_calls = await _step_tool_call_projection(run_id, step_index)
        if isinstance(tool_calls, list):
            payload['tool_calls'] = tool_calls
        return payload

    # Historical runs may only have the legacy plan projection. Preserve
    # their bare-string result without pretending it is a new step row.
    for position, entry in enumerate(run.plan or []):
        index = int(entry.get('index', position))
        if index != step_index:
            continue
        stored_result = entry.get('result')
        payload = {
            'step_index': index,
            'status': entry.get('status', 'pending'),
            'result': (
                await _result_payload(
                    StepResult.from_stored(stored_result),
                    task_id=task_id,
                    run=run,
                )
                if stored_result is not None
                else None
            ),
            'error': entry.get('error'),
        }
        tool_calls = await _step_tool_call_projection(run_id, step_index)
        if isinstance(tool_calls, list):
            payload['tool_calls'] = tool_calls
        return payload
    raise HTTPException(status_code=404, detail='Task run step not found')


@tasks_api.get('/{task_id}/runs/{run_id}/events')
async def stream_task_run_events(
    request: Request,
    task_id: str,
    run_id: str,
    after: int | None = None,
    ctx: AuthContext = Depends(get_auth_context),
):
    _task, run = await _authorized_task_run(task_id, run_id, ctx)
    cursor = _event_cursor(request, after)
    return EventSourceResponse(
        _task_run_event_stream(request, run_id, cursor),
        ping=15,
    )


@tasks_api.get('/{task_id}')
async def load_task(task_id: str):
    task = await _visible_task(task_id)
    return await _task_projection(task) if task else {}

@tasks_api.delete('/{task_id}')
async def delete_task(task_id: str):
    # Read the retained row directly so repeating DELETE can repair a crash
    # between the authoritative head tombstone and this projection update.
    task = await Task.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    requested_deleted_at = task.deleted_at or datetime.now(timezone.utc).replace(
        tzinfo=None
    ).strftime('%Y-%m-%d %H:%M:%S')
    try:
        head = await RunRepository().tombstone_task(
            task_id,
            deleted_at=requested_deleted_at,
        )
    except ActiveRunExists:
        raise HTTPException(
            status_code=409,
            detail="Task has an active run. Cancel it before deleting the task.",
        )
    # The admission tombstone commits first. If this projection write fails,
    # repeating DELETE is safe and repairs the Task row from the head value.
    await Task.update_one(
        {'id': task_id},
        {
            'deleted_at': head.deleted_at,
            'autostart': False,
            'schedule_enabled': False,
            'next_run_at': None,
        },
    )
    return {'message': 'Task deleted successfully'}
