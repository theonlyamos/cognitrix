"""Persisted team and task management tools."""

from __future__ import annotations

from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import EntityRef, ToolOutcome, current_execution_context


class ToolAuthorizationError(ValueError):
    pass


def _require_scope(scope: str) -> None:
    ctx = current_execution_context()
    if not ctx.has_scope(scope):
        raise ToolAuthorizationError(f'API key missing required scope: {scope}')


def _authorize_agents(agents: list[Any]) -> None:
    _authorize_agent_ids([str(agent.id) for agent in agents])


def _authorize_agent_ids(agent_ids: list[str]) -> None:
    ctx = current_execution_context()
    denied = [agent_id for agent_id in agent_ids if not ctx.agent_allowed(agent_id)]
    if denied:
        raise ToolAuthorizationError('API key is not allowed to manage the selected agent(s)')


def _authorize_team(team: Any) -> None:
    if not current_execution_context().team_allowed(str(team.id)):
        raise ToolAuthorizationError('API key is not allowed to manage the selected team')


async def _resolve_one(model: Any, ref: str, label: str, field: str) -> Any:
    ref = (ref or '').strip()
    if not ref:
        raise ValueError(f'{label} reference is required')
    entity = await model.get(ref)
    if entity is not None:
        return entity
    matches = [x for x in (await model.all() or []) if str(getattr(x, field, '')).casefold() == ref.casefold()]
    if not matches:
        raise ValueError(f"{label} '{ref}' was not found")
    if len(matches) != 1:
        raise ValueError(f"{label} '{ref}' is ambiguous; use its id")
    return matches[0]


async def _agents(refs: list[str] | None) -> list[Any]:
    from cognitrix.agents import Agent
    resolved, seen = [], set()
    for ref in refs or []:
        agent = await _resolve_one(Agent, ref, 'Agent', 'name')
        if str(agent.id) not in seen:
            resolved.append(agent)
            seen.add(str(agent.id))
    _authorize_agents(resolved)
    return resolved


async def _team(ref: str) -> Any:
    from cognitrix.teams.base import Team
    team = await _resolve_one(Team, ref, 'Team', 'name')
    _authorize_team(team)
    return team


def _manager_key() -> str | None:
    """Who is managing tasks: the signed-in user, or None for the local CLI.

    Scheduled and webhook runs carry no user and no scopes; they are already
    refused by the write-scope check, and this refuses any other context
    without a person behind it.
    """
    from cognitrix.common.process_security import HostProcessMode

    ctx = current_execution_context()
    if ctx.user_id:
        return str(ctx.user_id)
    if ctx.host_process_mode == HostProcessMode.TRUSTED_LOCAL and not ctx.task_id:
        return None
    raise ToolAuthorizationError('Managing tasks needs a signed-in user')


async def _task(ref: str, *, edit: bool = True) -> Any:
    """A live task the caller may see: their own or a shared one (the local
    CLI only sees shared tasks). With ``edit`` a shared task also needs an
    admin, the rule for changing it; looking at it or starting and stopping
    its runs only needs to see it, as in the app."""
    from cognitrix.agents.access import can_view, is_admin
    from cognitrix.tasks.base import Task

    user_key = _manager_key()
    ref = (ref or '').strip()
    if not ref:
        raise ValueError('Task reference is required')
    task = await Task.get(ref)
    if task is not None and (task.deleted_at or not can_view(task, user_key)):
        task = None
    if task is None:
        matches = [t for t in (await Task.all() or [])
                   if not t.deleted_at and can_view(t, user_key)
                   and str(t.title or '').casefold() == ref.casefold()]
        if not matches:
            raise ValueError(f"Task '{ref}' was not found")
        if len(matches) != 1:
            raise ValueError(f"Task '{ref}' is ambiguous; use its id")
        task = matches[0]
    if edit and not task.owner_id and user_key is not None:
        from cognitrix.models.user import User
        if not await is_admin(await User.get(user_key)):
            raise ToolAuthorizationError('Only an admin can change a shared task')
    ctx = current_execution_context()
    if task.team_id and not ctx.team_allowed(str(task.team_id)):
        raise ToolAuthorizationError("API key is not allowed to manage this task's team")
    _authorize_agent_ids([str(a) for a in task.assigned_agents or []])
    return task


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def create_new_team(name: str, description: str, agent_refs: list[str] | None = None,
                          leader_ref: str | None = None) -> ToolOutcome:
    """Create a persisted team with optional existing members and leader."""
    from cognitrix.agents import Agent
    from cognitrix.teams.base import Team
    _require_scope('write')
    if not name.strip() or not description.strip():
        return ToolOutcome.failure('invalid_team', 'Team name and description are required')
    try:
        agents = await _agents(agent_refs)
        leader = await _resolve_one(Agent, leader_ref, 'Leader', 'name') if leader_ref else None
        ids = [str(agent.id) for agent in agents]
        if leader and str(leader.id) not in ids:
            return ToolOutcome.failure('leader_not_member', 'The leader must be a member of the team')
        team = Team(name=name.strip(), description=description.strip(), assigned_agents=ids,
                    leader_id=str(leader.id) if leader else None)
        await team.save()
        return ToolOutcome.success(f"Team '{team.name}' created with {len(ids)} agent(s).",
                                   entities=[EntityRef(type='team', id=str(team.id), name=team.name)])
    except ValueError as exc:
        return ToolOutcome.failure('invalid_team_reference', str(exc))


def _steps(steps: list[str] | None) -> dict[str, dict[str, str]]:
    return {str(i): {'step': step.strip(), 'step_title': f'Step {i + 1}',
                     'description': step.strip()}
            for i, step in enumerate(steps or []) if step and step.strip()}


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def create_task(title: str, description: str, steps: list[str] | None = None,
                      agent_refs: list[str] | None = None, team_ref: str | None = None,
                      start_now: bool = False, schedule_at: str | None = None,
                      schedule_interval_seconds: int | None = None, schedule_cron: str | None = None,
                      schedule_enabled: bool | None = None) -> ToolOutcome:
    """Create a task, optionally assign it, schedule it, or queue it once now."""
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.scheduler import compute_next_run, normalize_schedule_at, validate_schedule
    _require_scope('write')
    if not isinstance(start_now, bool) or (schedule_enabled is not None and not isinstance(schedule_enabled, bool)):
        return ToolOutcome.failure('invalid_task', 'Boolean task options must be true or false')
    if schedule_interval_seconds is not None and not isinstance(schedule_interval_seconds, int):
        return ToolOutcome.failure('invalid_schedule', 'schedule_interval_seconds must be an integer')
    if not title.strip() or not description.strip():
        return ToolOutcome.failure('invalid_task', 'Task title and description are required')
    if team_ref and agent_refs:
        return ToolOutcome.failure('invalid_assignment', 'Specify agents or a team, not both')
    try:
        agents = await _agents(agent_refs)
        team = await _team(team_ref) if team_ref else None
        agent_ids = [str(agent.id) for agent in agents]
        if team:
            agent_ids = list(dict.fromkeys(str(aid) for aid in (team.assigned_agents or [])))
            if not agent_ids:
                return ToolOutcome.failure('empty_team', 'A team must have members before it can own a task')
            _authorize_agent_ids(agent_ids)
        scheduled = bool(schedule_at or schedule_interval_seconds or schedule_cron)
        enabled = bool(schedule_enabled) if schedule_enabled is not None else scheduled
        if enabled and not scheduled:
            return ToolOutcome.failure('invalid_schedule', 'An enabled schedule requires exactly one schedule type')
        if start_now or enabled:
            _require_scope('run')
        if (start_now or enabled) and not agent_ids:
            return ToolOutcome.failure('task_owner_required', 'Starting or scheduling a task requires an owner')
        if schedule_at:
            schedule_at = normalize_schedule_at(schedule_at)
        task = Task(title=title.strip(), description=description.strip(), step_instructions=_steps(steps),
                    owner_id=current_execution_context().user_id,
                    assigned_agents=agent_ids, team_id=str(team.id) if team else None, autostart=False,
                    schedule_at=schedule_at, schedule_interval=schedule_interval_seconds,
                    schedule_cron=schedule_cron, schedule_enabled=enabled)
        if enabled:
            authority = current_execution_context()
            task.schedule_requested_by = authority.user_id
            task.schedule_authority_kind = "api_key" if authority.api_key_id else "jwt"
            task.schedule_authority_id = authority.api_key_id or authority.user_id
        reason = validate_schedule(task, respecified=True)
        if reason:
            return ToolOutcome.failure('invalid_schedule', reason)
        if enabled and scheduled:
            task.next_run_at = compute_next_run(task)
        await task.save()
        warnings = []
        if start_now:
            try:
                from cognitrix.api.routes.tasks import _enqueue_task_start
                await _enqueue_task_start(task)
            except Exception:
                warnings.append('Task was saved, but it could not be queued to start now.')
        return ToolOutcome.success(f"Task '{task.title}' created.", warnings=warnings,
                                   entities=[EntityRef(type='task', id=str(task.id), name=task.title)])
    except ValueError as exc:
        return ToolOutcome.failure('invalid_task_reference', str(exc))


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def assign_task(task_ref: str, agent_refs: list[str] | None = None,
                      team_ref: str | None = None, replace_agents: bool = False) -> ToolOutcome:
    """Assign a task to an exclusive team snapshot or individual agent roster."""
    _require_scope('write')
    if not isinstance(replace_agents, bool):
        return ToolOutcome.failure('invalid_assignment', 'replace_agents must be true or false')
    if bool(agent_refs) == bool(team_ref):
        return ToolOutcome.failure('invalid_assignment', 'Specify exactly one of agent_refs or team_ref')
    try:
        task = await _task(task_ref)
        if team_ref:
            team = await _team(team_ref)
            members = list(dict.fromkeys(str(aid) for aid in (team.assigned_agents or [])))
            if not members:
                return ToolOutcome.failure('empty_team', 'A team must have members before it can own a task')
            _authorize_agent_ids(members)
            task.team_id, task.assigned_agents = str(team.id), members
        else:
            ids = [str(agent.id) for agent in await _agents(agent_refs)]
            if task.team_id and not replace_agents:
                return ToolOutcome.failure('team_assignment_exists',
                                           'Replace the team assignment before assigning individual agents')
            task.team_id = None
            task.assigned_agents = ids if replace_agents else list(dict.fromkeys([*task.assigned_agents, *ids]))
            _authorize_agent_ids(task.assigned_agents)
        from cognitrix.tasks.base import Task
        await Task.update_one(
            {'id': task.id},
            {'assigned_agents': task.assigned_agents, 'team_id': task.team_id},
        )
        active = str(task.status).lower() in {'taskstatus.in_progress', 'taskstatus.completed', 'in_progress', 'completed'}
        return ToolOutcome.success(f"Task '{task.title}' assignment updated.",
                                   warnings=['Assignment affects future runs only.'] if active else [],
                                   entities=[EntityRef(type='task', id=str(task.id), name=task.title)])
    except ToolAuthorizationError as exc:
        return ToolOutcome.failure('authorization_denied', str(exc), denied=True)
    except ValueError as exc:
        return ToolOutcome.failure('invalid_task_reference', str(exc))


_ACTIVE_STATUSES = {'taskstatus.in_progress', 'in_progress'}


def _is_active(task: Any) -> bool:
    return str(getattr(task, 'status', '')).lower() in _ACTIVE_STATUSES


def _clear_wake(task: Any) -> None:
    """Drop an agent's pending wake-up when the schedule is turned off."""
    for field in ('wake_pending', 'wake_at', 'wake_reason', 'wake_requested_by',
                  'wake_authority_kind', 'wake_authority_id'):
        if field in type(task).model_fields:
            setattr(task, field, False if field == 'wake_pending' else None)


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def update_task(task_ref: str, title: str | None = None, description: str | None = None,
                      steps: list[str] | None = None, schedule_at: str | None = None,
                      schedule_interval_seconds: int | None = None, schedule_cron: str | None = None,
                      schedule_enabled: bool | None = None, clear_schedule: bool = False) -> ToolOutcome:
    """Change an existing task's title, description, steps or schedule. Only the fields you pass change.
    steps replaces all steps. Setting one of schedule_at / schedule_interval_seconds / schedule_cron replaces
    the schedule; schedule_enabled alone pauses or resumes it; clear_schedule removes it."""
    from cognitrix.tasks.scheduler import compute_next_run, normalize_schedule_at, validate_schedule
    _require_scope('write')
    if not isinstance(clear_schedule, bool) or (schedule_enabled is not None and not isinstance(schedule_enabled, bool)):
        return ToolOutcome.failure('invalid_task', 'Boolean task options must be true or false')
    if schedule_interval_seconds is not None and (
            isinstance(schedule_interval_seconds, bool) or not isinstance(schedule_interval_seconds, int)):
        return ToolOutcome.failure('invalid_schedule', 'schedule_interval_seconds must be an integer')
    if title is not None and not title.strip():
        return ToolOutcome.failure('invalid_task', 'Task title cannot be empty')
    if description is not None and not description.strip():
        return ToolOutcome.failure('invalid_task', 'Task description cannot be empty')
    new_type = bool(schedule_at or schedule_interval_seconds or schedule_cron)
    if clear_schedule and (new_type or schedule_enabled):
        return ToolOutcome.failure('invalid_schedule', 'clear_schedule cannot be combined with a new schedule')
    if not (title is not None or description is not None or steps is not None or new_type
            or schedule_enabled is not None or clear_schedule):
        return ToolOutcome.failure('nothing_to_update', 'Pass at least one field to change')
    try:
        task = await _task(task_ref)
        was_enabled = bool(task.schedule_enabled)
        if title is not None:
            task.title = title.strip()
        if description is not None:
            task.description = description.strip()
        if steps is not None:
            task.step_instructions = _steps(steps)

        schedule_touched = new_type or clear_schedule or schedule_enabled is not None
        if clear_schedule:
            task.schedule_at = task.schedule_interval = task.schedule_cron = None
            task.schedule_enabled = False
        elif new_type:
            task.schedule_at = normalize_schedule_at(schedule_at) if schedule_at else None
            task.schedule_interval = schedule_interval_seconds
            task.schedule_cron = schedule_cron
            task.schedule_enabled = True if schedule_enabled is None else schedule_enabled
        elif schedule_enabled is not None:
            if schedule_enabled and not (task.schedule_at or task.schedule_interval or task.schedule_cron):
                return ToolOutcome.failure('invalid_schedule', 'Task has no schedule to enable')
            task.schedule_enabled = schedule_enabled

        if schedule_touched:
            # A new schedule, or turning one back on, is checked as if just set:
            # a one-off time in the past is rejected instead of firing at once.
            reason = validate_schedule(task, respecified=new_type or (bool(schedule_enabled) and not was_enabled))
            if reason:
                return ToolOutcome.failure('invalid_schedule', reason)
        if task.schedule_enabled:
            _require_scope('run')
            if not task.assigned_agents:
                return ToolOutcome.failure('task_owner_required', 'Scheduling a task requires an owner')
        if task.schedule_enabled:
            # Any edit of an enabled schedule, even to its instructions only,
            # makes the editor its authority (as save_task does). Otherwise a
            # narrower key could change what runs under broader stored authority.
            if new_type or not was_enabled:
                task.next_run_at = compute_next_run(task)
            authority = current_execution_context()
            task.schedule_requested_by = authority.user_id
            task.schedule_authority_kind = 'api_key' if authority.api_key_id else 'jwt'
            task.schedule_authority_id = authority.api_key_id or authority.user_id
        elif schedule_touched:
            task.next_run_at = None
            if clear_schedule or new_type:
                task.schedule_requested_by = None
                task.schedule_authority_kind = None
                task.schedule_authority_id = None
            if was_enabled:
                _clear_wake(task)

        from cognitrix.api.routes.tasks import _update_existing_task_if_live
        if await _update_existing_task_if_live(task) != 1:
            return ToolOutcome.failure('task_not_found', 'The task was deleted before the change was saved')
        warnings = ['A run is in progress; the changes apply to future runs.'] if _is_active(task) else []
        return ToolOutcome.success(f"Task '{task.title}' updated.", warnings=warnings,
                                   entities=[EntityRef(type='task', id=str(task.id), name=task.title)])
    except ToolAuthorizationError as exc:
        return ToolOutcome.failure('authorization_denied', str(exc), denied=True)
    except ValueError as exc:
        return ToolOutcome.failure('invalid_task_reference', str(exc))


@tool(category='system', retryable=False, max_attempts=1, approval_mode='always')
async def delete_task(task_ref: str) -> ToolOutcome:
    """Delete a task and stop its schedule. Always asks the user to approve first.
    A task with a run in progress must be cancelled before it can be deleted."""
    from cognitrix.tasks.repository import ActiveRunExists
    _require_scope('write')
    try:
        task = await _task(task_ref)
        from cognitrix.api.routes.tasks import soft_delete_task
        try:
            await soft_delete_task(task)
        except ActiveRunExists:
            return ToolOutcome.failure('task_running',
                                       'The task has a run in progress. Cancel it before deleting the task.')
        return ToolOutcome.success(f"Task '{task.title}' deleted.",
                                   entities=[EntityRef(type='task', id=str(task.id), name=task.title)])
    except ToolAuthorizationError as exc:
        return ToolOutcome.failure('authorization_denied', str(exc), denied=True)
    except ValueError as exc:
        return ToolOutcome.failure('invalid_task_reference', str(exc))


# ── looking at tasks and starting / stopping their runs ──────────────────────

_RUN_TO_TASK_STATUS = {
    'queued': 'in_progress', 'running': 'in_progress', 'cancelling': 'in_progress',
    'completed': 'completed', 'failed': 'failed', 'cancelled': 'cancelled',
}
_TASK_STATUSES = frozenset({'pending', 'in_progress', 'completed', 'failed', 'cancelled'})
_LIST_LIMIT = 50
_RESULT_CHARS = 4000


def _plain(value: Any) -> str:
    """'TaskStatus.COMPLETED' / TaskRunStatus.RUNNING -> 'completed' / 'running'."""
    value = getattr(value, 'value', value)
    return str(value or '').rsplit('.', 1)[-1].lower()


def _key_allows(task: Any) -> bool:
    """An API key limited to some teams or agents only sees their tasks."""
    ctx = current_execution_context()
    if task.team_id and not ctx.team_allowed(str(task.team_id)):
        return False
    return all(ctx.agent_allowed(str(a)) for a in task.assigned_agents or [])


def _schedule_text(task: Any) -> str:
    if task.schedule_cron:
        kind = f'cron "{task.schedule_cron}"'
    elif task.schedule_interval:
        kind = f'every {task.schedule_interval}s'
    elif task.schedule_at:
        kind = f'once at {task.schedule_at} UTC'
    else:
        return 'none'
    if not task.schedule_enabled:
        return f'{kind} (paused)'
    return f'{kind}, next run {task.next_run_at} UTC' if task.next_run_at else kind


async def _latest_run(task: Any) -> Any:
    from cognitrix.tasks.repository import RunRepository
    try:
        return await RunRepository().latest_run(task.id)
    except Exception:
        return None


def _status(task: Any, run: Any) -> str:
    """The task's status as the app shows it: its latest run wins."""
    if run is not None:
        return _RUN_TO_TASK_STATUS.get(_plain(run.status), _plain(run.status))
    return _plain(task.status)


class _RunViewer:
    """The tool caller, shaped like the API's AuthContext for run checks."""

    def __init__(self, user_key: str | None):
        ctx = current_execution_context()
        self.user_key = user_key
        self.user = None
        self.api_key = type('Key', (), {'id': ctx.api_key_id})() if ctx.api_key_id else None
        self.team_allowed = ctx.team_allowed
        self.agent_allowed = ctx.agent_allowed
        self._admin: bool | None = None

    async def admin(self) -> bool:
        if self.user_key is None:
            return True  # the local CLI manages shared tasks (see _task)
        if self._admin is None:
            from cognitrix.agents.access import is_admin
            from cognitrix.models.user import User
            self.user = await User.get(self.user_key)
            self._admin = await is_admin(self.user)
        return self._admin

    async def may_see(self, task: Any, run: Any) -> bool:
        """Same rule as the app: a private task's owner sees every run; on a
        shared task each person sees the runs they started, admins see all."""
        from cognitrix.tasks.run import run_acl_allowed
        if self.user_key is not None and self.user is None:
            self.user = type('User', (), {'id': self.user_key})()
        if not run_acl_allowed(run, self):
            return False
        if task.owner_id or not run.requested_by or str(run.requested_by) == str(self.user_key):
            return True
        return await self.admin()


async def _visible_runs(task: Any, viewer: _RunViewer, limit: int) -> list[Any]:
    from cognitrix.api.routes.tasks import _task_run_rows
    runs: list[Any] = []
    offset = 0
    while len(runs) < limit and offset < 500:
        batch = await _task_run_rows(task.id, limit=50, offset=offset)
        if not batch:
            break
        offset += len(batch)
        for run in batch:
            if await viewer.may_see(task, run):
                runs.append(run)
                if len(runs) == limit:
                    break
    return runs


def _run_line(run: Any) -> str:
    stored = run.json() if hasattr(run, 'json') else {}
    when = run.completed_at or run.started_at or run.queued_at or stored.get('created_at') or ''
    bits = [f'run {run.id}: {_plain(run.status)}']
    if when:
        bits.append(f'at {when} UTC')
    if run.trigger_kind:
        bits.append(f'(started by {run.trigger_kind})')
    if run.error:
        bits.append(f'error: {str(run.error)[:300]}')
    return ' '.join(bits)


def _result_text(run: Any) -> str:
    from cognitrix.tasks.results import StepResult
    try:
        result = run.result_data or (StepResult.from_stored(run.result) if run.result else None)
    except (TypeError, ValueError):
        return ''
    return str(getattr(result, 'text', '') or '') if result is not None else ''


async def _agent_names(ids: list[str]) -> list[str]:
    from cognitrix.agents import Agent
    names = []
    for agent_id in ids or []:
        try:
            agent = await Agent.get(str(agent_id))
        except Exception:
            agent = None
        names.append(f'{agent.name} ({agent_id})' if agent is not None else str(agent_id))
    return names


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def list_tasks(status: str | None = None, search: str | None = None, limit: int = 20) -> ToolOutcome:
    """List the tasks you can see, newest first, with each task's id, status and schedule.
    status filters by pending, in_progress, completed, failed, cancelled or scheduled.
    search matches words in the title or description. Use Get Task for one task's details and runs."""
    from cognitrix.agents.access import can_view
    from cognitrix.tasks.base import Task
    _require_scope('read')
    wanted = (status or '').strip().lower().replace(' ', '_') or None
    if wanted and wanted not in _TASK_STATUSES | {'scheduled'}:
        return ToolOutcome.failure('invalid_status', 'status must be one of pending, in_progress, completed, '
                                   'failed, cancelled or scheduled')
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return ToolOutcome.failure('invalid_limit', 'limit must be a whole number of 1 or more')
    limit = min(limit, _LIST_LIMIT)
    try:
        user_key = _manager_key()
    except ToolAuthorizationError as exc:
        return ToolOutcome.failure('authorization_denied', str(exc), denied=True)
    words = [w.casefold() for w in (search or '').split() if w.strip()]
    tasks = [t for t in (await Task.all() or [])
             if not t.deleted_at and can_view(t, user_key) and _key_allows(t)]
    if words:
        tasks = [t for t in tasks
                 if all(w in f'{t.title or ""} {t.description or ""}'.casefold() for w in words)]
    if wanted == 'scheduled':
        tasks = [t for t in tasks if t.schedule_enabled]
    tasks.sort(key=lambda t: str(t.json().get('created_at') or ''), reverse=True)

    lines, total = [], 0
    for task in tasks:
        state = _status(task, await _latest_run(task))
        if wanted and wanted != 'scheduled' and state != wanted:
            continue
        total += 1
        if len(lines) < limit:
            lines.append(f'- {task.title} (id {task.id}): {state}; schedule: {_schedule_text(task)}'
                         + ('' if task.owner_id else '; shared'))
    if not lines:
        return ToolOutcome.success('No matching tasks.' if (wanted or words) else 'There are no tasks yet.')
    more = f'\n…and {total - len(lines)} more; narrow with status or search.' if total > len(lines) else ''
    return ToolOutcome.success(f'{total} task(s):\n' + '\n'.join(lines) + more)


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def get_task(task_ref: str, runs: int = 5) -> ToolOutcome:
    """Show one task by id or exact title: its description, steps, agents, schedule, status,
    its most recent runs (runs, up to 20) and the latest finished run's result."""
    from cognitrix.tasks.context import untrusted_text_block
    _require_scope('read')
    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 0:
        return ToolOutcome.failure('invalid_limit', 'runs must be a whole number of 0 or more')
    try:
        task = await _task(task_ref, edit=False)
        viewer = _RunViewer(_manager_key())
        recent = await _visible_runs(task, viewer, min(runs, 20)) if runs else []
    except ToolAuthorizationError as exc:
        return ToolOutcome.failure('authorization_denied', str(exc), denied=True)
    except ValueError as exc:
        return ToolOutcome.failure('invalid_task_reference', str(exc))

    latest = await _latest_run(task)
    if latest is not None and not await viewer.may_see(task, latest):
        latest = recent[0] if recent else None
    lines = [f'Task: {task.title} (id {task.id})',
             f'Status: {_status(task, latest)}',
             f'Schedule: {_schedule_text(task)}',
             f'Owner: {"you" if task.owner_id else "shared"}']
    agents = await _agent_names(list(task.assigned_agents or []))
    if task.team_id:
        lines.append(f'Team: {task.team_id}')
    lines.append('Agents: ' + (', '.join(agents) if agents else 'none assigned'))
    steps = [s.get('step') or s.get('description') or '' for _, s in
             sorted((task.step_instructions or {}).items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)]
    body = [f'Description:\n{task.description}']
    if steps:
        body.append('Steps:\n' + '\n'.join(f'{i}. {s}' for i, s in enumerate(steps, 1)))
    if recent:
        body.append('Recent runs (newest first):\n' + '\n'.join(f'- {_run_line(r)}' for r in recent))
    elif runs:
        body.append('No runs yet.')
    finished = next((r for r in recent if _plain(r.status) == 'completed'), None)
    text = _result_text(finished) if finished is not None else ''
    if text:
        body.append(untrusted_text_block(f'Result of run {finished.id}', text, _RESULT_CHARS))
    return ToolOutcome.success('\n'.join(lines) + '\n\n' + '\n\n'.join(body),
                               entities=[EntityRef(type='task', id=str(task.id), name=task.title)])


async def _agent_label() -> str | None:
    agent_id = current_execution_context().agent_id
    if not agent_id:
        return None
    from cognitrix.agents import Agent
    try:
        agent = await Agent.get(str(agent_id))
    except Exception:
        agent = None
    return str(agent.name) if agent is not None else None


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def start_task(task_ref: str, resume: bool = False) -> ToolOutcome:
    """Start a run of a task now, by id or exact title. The run happens in the background;
    check on it later with Get Task. resume=true continues the last failed or cancelled run
    from where it stopped instead of starting over. A task can only have one run at a time."""
    _require_scope('run')
    if not isinstance(resume, bool):
        return ToolOutcome.failure('invalid_task', 'resume must be true or false')
    try:
        task = await _task(task_ref, edit=False)
    except ToolAuthorizationError as exc:
        return ToolOutcome.failure('authorization_denied', str(exc), denied=True)
    except ValueError as exc:
        return ToolOutcome.failure('invalid_task_reference', str(exc))
    if not task.assigned_agents:
        return ToolOutcome.failure('task_owner_required', 'Assign an agent or team to the task before starting it')
    ctx = current_execution_context()
    if ctx.task_id and str(ctx.task_id) == str(task.id):
        return ToolOutcome.failure('task_running', 'This task is already running: it is the run you are in.')

    from fastapi import HTTPException

    from cognitrix.api.routes.tasks import _enqueue_task_start
    label = await _agent_label()
    try:
        run = await _enqueue_task_start(task, resume=resume, trigger_kind='agent',
                                        trigger_note=f'Started by {label}' if label else 'Started by an agent')
    except HTTPException as exc:
        if exc.status_code == 409:
            return ToolOutcome.failure('task_running', 'The task already has a run in progress. '
                                       'Cancel it first or wait for it to finish.')
        if exc.status_code == 404:
            return ToolOutcome.failure('invalid_task_reference', f"Task '{task_ref}' was not found")
        return ToolOutcome.failure('queue_unavailable', str(exc.detail), retryable=exc.status_code == 503)
    note = ' (resuming the last stopped run)' if resume and run.resume_from_run_id else ''
    warnings = ['There was no failed or cancelled run to resume, so it starts over.'] \
        if resume and not run.resume_from_run_id else []
    return ToolOutcome.success(f"Task '{task.title}' started{note}. Run id {run.id}, status {_plain(run.status)}.",
                               warnings=warnings,
                               entities=[EntityRef(type='task', id=str(task.id), name=task.title)])


@tool(category='system', retryable=False, max_attempts=1, approval_mode='assigned_only')
async def cancel_task_run(task_ref: str) -> ToolOutcome:
    """Stop the run of a task that is in progress, by task id or exact title. The task itself
    is kept and can be started again, or resumed with Start Task resume=true. Calling it again
    while the run is still stopping forces it to stop."""
    _require_scope('run')
    try:
        task = await _task(task_ref, edit=False)
        viewer = _RunViewer(_manager_key())
    except ToolAuthorizationError as exc:
        return ToolOutcome.failure('authorization_denied', str(exc), denied=True)
    except ValueError as exc:
        return ToolOutcome.failure('invalid_task_reference', str(exc))

    from cognitrix.api.routes.tasks import _active_run, cancel_active_run
    from cognitrix.tasks.base import Task, TaskStatus
    run = await _active_run(task.id)
    ctx = current_execution_context()
    if run is not None:
        if ctx.run_id and str(ctx.run_id) == str(run.id):
            return ToolOutcome.failure('own_run', 'That is the run you are in; finish your reply instead.')
        if not await viewer.may_see(task, run):
            return ToolOutcome.failure('authorization_denied', 'Not allowed to stop this run', denied=True)
        stopped = await cancel_active_run(task, run)
        state = _plain(stopped.status)
        text = (f"Run {run.id} of '{task.title}' cancelled." if state == 'cancelled'
                else f"Run {run.id} of '{task.title}' is stopping; it ends after its current step.")
        return ToolOutcome.success(text, entities=[EntityRef(type='task', id=str(task.id), name=task.title)])
    if _plain(task.status) == 'in_progress':
        # Queued but never picked up: mark the task cancelled, as the app does.
        await Task.update_one({'id': task.id}, {'status': TaskStatus.CANCELLED.value})
        return ToolOutcome.success(f"Task '{task.title}' cancelled before its run began.",
                                   entities=[EntityRef(type='task', id=str(task.id), name=task.title)])
    return ToolOutcome.failure('nothing_to_cancel', f"Task '{task.title}' has no run in progress.")
