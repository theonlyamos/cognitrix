from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from typing import List

from cognitrix.common.security import get_current_user

from ...agents.team import Team

teams_api = APIRouter(
    prefix='/teams',
    dependencies=[Depends(get_current_user)]
)

@teams_api.get("")
async def get_all_teams():
    teams = [team.dict() for team in Team.all()]
    
    return JSONResponse([team.dict() for team in teams])

@teams_api.post("")
async def save_team(team: Team):
    team.save()
    return JSONResponse(team.dict())

@teams_api.get("/{team_id}")
async def get_team(team_id: str):
    team = Team.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return JSONResponse(team.dict())

@teams_api.delete("/{team_id}")
async def delete_team(team_id: str):
    team = Team.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    Team.remove({'id': team_id})
    return JSONResponse({"message": "Team deleted successfully"})