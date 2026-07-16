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


async def _task(ref: str) -> Any:
    from cognitrix.tasks.base import Task
    return await _resolve_one(Task, ref, 'Task', 'title')


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
