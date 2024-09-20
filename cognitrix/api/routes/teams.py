from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import List

from cognitrix.common.security import get_current_user

from cognitrix.teams.base import Team

teams_api = APIRouter(
    prefix='/teams',
    dependencies=[Depends(get_current_user)]
)

@teams_api.get("")
async def get_all_teams():
    teams = [team.model_dump() for team in Team.all()]
    
    return JSONResponse(teams)

@teams_api.post("")
async def save_team(team: Team):
    if Team.get(team.id):
        update = Team.update({'id': team.id}, team.model_dump())
        
        if update:
            result = Team.get(team.id)
            if result:  
                team = result
                return JSONResponse(team.model_dump())
            else:
                raise HTTPException(status_code=404, detail="Error updating team")
    else:
        team.save()

        
    return JSONResponse(team.model_dump())


@teams_api.get("/{team_id}")
async def get_team(team_id: str):
    team = Team.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    
    return JSONResponse(team.model_dump())

@teams_api.delete("/{team_id}")
async def delete_team(team_id: str):
    team = Team.get(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    Team.remove({'id': team_id})
    return JSONResponse({"message": "Team deleted successfully"})

@teams_api.get(path="/generate")
async def sse_endpoint(request: Request):
    sse_manager = request.state.sse_manager
    return await sse_manager.sse_endpoint(request)