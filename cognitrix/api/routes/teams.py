from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import List

from cognitrix.common.security import get_current_user

from cognitrix.sessions.base import Session
from cognitrix.teams.base import Team

teams_api = APIRouter(
    prefix='/teams',
    dependencies=[Depends(get_current_user)]
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
    
    await Team.remove_one({'id': team_id})
    return {"message": "Team deleted successfully"}

@teams_api.get("/{team_id}/sessions")
async def sessions(team_id: str):
    sessions = await Session.find({'team_id': team_id})
    return [session.json() for session in sessions]


@teams_api.get("/teams/{team_id}/tasks")
async def get_tasks_by_team(team_id: str):
    team = await Team.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    tasks = team.get_assigned_tasks()
    return tasks

@teams_api.get(path="/generate")
async def sse_endpoint(request: Request):
    sse_manager = request.state.sse_manager
    return await sse_manager.sse_endpoint(request)