from fastapi import APIRouter, Depends, HTTPException

from cognitrix.common.security import crud_scope
from cognitrix.sessions.base import Session
from cognitrix.teams.base import Team

teams_api = APIRouter(
    prefix='/teams',
    dependencies=[Depends(crud_scope)]
)

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
async def sessions(team_id: str):
    sessions = await Session.find({'team_id': team_id})
    return [session.json() for session in sessions]


@teams_api.get("/{team_id}/tasks")
async def get_tasks_by_team(team_id: str):
    team = await Team.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    tasks = await team.get_assigned_tasks()
    return [task.json() for task in tasks]

