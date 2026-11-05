from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents.access import can_edit, can_view
from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, require
from cognitrix.session_ownership import principal_key
from cognitrix.sessions.base import Session
from cognitrix.teams.base import Team

teams_api = APIRouter(
    prefix='/teams',
    dependencies=[Depends(crud_scope)]
)

# Execute route on its own router: running a team is the 'run' scope, not the
# 'write' crud_scope would infer from POST. Registered before teams_api.
teams_run_api = APIRouter(
    prefix='/teams',
    dependencies=[Depends(require('run'))]
)


def _user_key(ctx: AuthContext) -> str | None:
    user = getattr(ctx, 'user', None)
    return principal_key(user) if user is not None else None


def _require_team_view(team: Team | None, ctx: AuthContext) -> Team:
    if team is None or not can_view(team, _user_key(ctx)):
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def _require_team_edit(team: Team | None, ctx: AuthContext) -> Team:
    team = _require_team_view(team, ctx)
    if not await can_edit(team, ctx.user):
        raise HTTPException(status_code=403, detail="Only the team's owner can change it")
    return team


async def _check_team_agents(team: Team, ctx: AuthContext) -> None:
    from cognitrix.agents import Agent

    user_key = _user_key(ctx)
    for agent_id in team.assigned_agents or []:
        agent = await Agent.find_one({'id': agent_id})
        if agent is not None and not can_view(agent, user_key):
            raise HTTPException(status_code=403, detail="Not allowed to use this agent")


class TeamRunRequest(BaseModel):
    description: str
    title: str | None = None
    callback_url: str | None = None


@teams_run_api.post('/{team_id}/run', status_code=202)
async def run_team(team_id: str, body: TeamRunRequest,
                   ctx: AuthContext = Depends(get_auth_context)):
    """Create a task for this team and enqueue it. Async by design — poll
    GET /tasks/{id} + /tasks/{id}/runs, or register a callback_url webhook."""
    from cognitrix.api.routes.tasks import (
        _active_run,
        _check_task_allowlists,
        _enqueue_task_start,
        _run_identity,
        _set_callback,
    )
    from cognitrix.tasks import Task

    team = _require_team_view(await Team.get(team_id), ctx)
    if not ctx.team_allowed(team.id):
        raise HTTPException(status_code=403, detail="API key not allowed for this team")
    if not team.assigned_agents:
        raise HTTPException(status_code=400, detail="Team has no agents")
    if not body.description.strip():
        raise HTTPException(status_code=400, detail="description is required")

    title = (body.title or body.description).strip().splitlines()[0][:60]
    task = Task(
        title=title or 'Team task',
        description=body.description,
        team_id=team.id,
        assigned_agents=list(team.assigned_agents),
        owner_id=_user_key(ctx),
    )
    # An agent-restricted key must not invoke agents outside its allowlist by
    # laundering the call through a team — same guard every other run path uses.
    _check_task_allowlists(ctx, task)
    await _set_callback(task, body.callback_url, ctx)
    await task.save()
    requested_by, actor_key = _run_identity(ctx)
    run = await _enqueue_task_start(
        task,
        requested_by=requested_by,
        actor_key=actor_key,
    )
    return {
        'task_id': task.id,
        'run_id': run.id,
        'status': run.status,
    }

@teams_api.get("")
async def get_all_teams(ctx: AuthContext = Depends(get_auth_context)):
    teams = await Team.all()
    user_key = _user_key(ctx)
    return [team.json() for team in teams if can_view(team, user_key)]

@teams_api.post("")
async def save_team(team: Team, ctx: AuthContext = Depends(get_auth_context)):
    result = await Team.get(team.id) if team.id else None
    if result is not None:
        await _require_team_edit(result, ctx)
        team.owner_id = result.owner_id
    else:
        team.owner_id = _user_key(ctx)
    await _check_team_agents(team, ctx)
    if result:
        update = await Team.update_one({'id': team.id}, team.json())

        if update:
            result = await Team.get(team.id)
            if result:
                team = result
                return team.json()
            else:
                raise HTTPException(status_code=503, detail="Error updating team")
    else:
        await team.save()

    return team.json()

@teams_api.get("/{team_id}")
async def get_team(team_id: str, ctx: AuthContext = Depends(get_auth_context)):
    team = _require_team_view(await Team.get(team_id), ctx)

    return team.json()

@teams_api.delete("/{team_id}")
async def delete_team(team_id: str, ctx: AuthContext = Depends(get_auth_context)):
    await _require_team_edit(await Team.get(team_id), ctx)

    await Team.delete_many({'id': team_id})
    return {"message": "Team deleted successfully"}

@teams_api.get("/{team_id}/sessions")
async def sessions(
    team_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    if not ctx.team_allowed(team_id):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this team',
        )

    # Keep this alias aligned with /sessions/teams/{team_id}: ordinary rows
    # require exact ownership, while TaskRun rows use their immutable run ACL.
    from cognitrix.api.routes.sessions import (
        _session_summary,
        _visible_remote_sessions,
    )

    rows = await _visible_remote_sessions(
        list(await Session.find({'team_id': team_id})),
        ctx,
    )
    return [_session_summary(session) for session in rows]


@teams_api.get("/{team_id}/tasks")
async def get_tasks_by_team(team_id: str, ctx: AuthContext = Depends(get_auth_context)):
    from cognitrix.api.routes.tasks import _task_json
    from cognitrix.tasks import Task

    team = _require_team_view(await Team.get(team_id), ctx)
    user_key = _user_key(ctx)
    # Team.tasks contains compatibility snapshots.  Rehydrate each reference
    # from the Task table so edits, reassignment, and soft deletion are
    # authoritative while preserving the embedded list's order.
    tasks = []
    for snapshot in await team.get_assigned_tasks():
        task = await Task.get(snapshot.id)
        if task is None or task.deleted_at or task.team_id != team.id or not can_view(task, user_key):
            continue
        tasks.append(task)
    return [_task_json(task) for task in tasks]

