from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List
import uuid

from ...agents.team import Team

teams_api = APIRouter(
    prefix='/teams'
)

@teams_api.get("/teams")
async def get_all_teams():
    teams = await Team.list_teams()
    
    return JSONResponse(teams)

@teams_api.get("/teams/{team_id}")
async def get_team(team_id: str):
    team = await Team.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return JSONResponse(team.dict())

@teams_api.post("/teams")
async def save_team(team: Team):
    await team.save()
    return JSONResponse(team.dict())


@teams_api.delete("/teams/{team_id}")
async def delete_team(team_id: str):
    team = await Team.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    await Team.delete(team_id)
    return JSONResponse({"message": "Team deleted successfully"})