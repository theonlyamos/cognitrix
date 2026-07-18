from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, require
from cognitrix.session_ownership import (
    OwnershipConflict,
    OwnershipNotFound,
    owned_session_ids,
    principal_key,
    require_active_owned,
)
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


class TeamRunRequest(BaseModel):
    description: str
    title: str | None = None
    callback_url: str | None = None


@teams_run_api.post('/{team_id}/run', status_code=202)
async def run_team(team_id: str, body: TeamRunRequest,
                   ctx: AuthContext = Depends(get_auth_context)):
    """Create a task for this team and enqueue it. Async by design — poll
    GET /tasks/{id} + /tasks/{id}/runs, or register a callback_url webhook."""
    from cognitrix.api.routes.tasks import _check_task_allowlists, _enqueue_task_start, _set_callback
    from cognitrix.tasks import Task

    team = await Team.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
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
    )
    # An agent-restricted key must not invoke agents outside its allowlist by
    # laundering the call through a team — same guard every other run path uses.
    _check_task_allowlists(ctx, task)
    await _set_callback(task, body.callback_url, ctx)
    await task.save()
    task = await _enqueue_task_start(task)
    return {'task_id': task.id, 'status': task.status}

@teams_api.get("")
async def get_all_teams():
    teams = await Team.all()

    return [team.json() for team in teams]

@teams_api.post("")
async def save_team(team: Team):
    result = await Team.get(team.id)
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
async def get_team(team_id: str):
    team = await Team.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    return team.json()

@teams_api.delete("/{team_id}")
async def delete_team(team_id: str):
    team = await Team.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

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

    user_id = principal_key(ctx.user)
    rows = []
    for session_id in await owned_session_ids(user_id):
        try:
            binding = await require_active_owned(session_id, user_id)
        except (OwnershipNotFound, OwnershipConflict):
            continue
        if not ctx.agent_allowed(binding.agent_id):
            continue
        session = await Session.get(session_id)
        if (
            session is not None
            and str(session.agent_id or '') == binding.agent_id
            and str(session.team_id or '') == team_id
        ):
            rows.append(session.json())
    return rows


@teams_api.get("/{team_id}/tasks")
async def get_tasks_by_team(team_id: str):
    from cognitrix.api.routes.tasks import _task_json

    team = await Team.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    tasks = await team.get_assigned_tasks()
    return [_task_json(task) for task in tasks]

