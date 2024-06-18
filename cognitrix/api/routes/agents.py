from fastapi import APIRouter
from fastapi.responses import JSONResponse
from ...agents import Agent

agents_api = APIRouter(
    prefix='/agents'
)

@agents_api.get('')
async def list_agents():
    agents = await Agent.list_agents()
    response = [{
        'id': agent.id, 'name': agent.name, 'provider': agent.llm.provider,
        'model': agent.llm.model, 'tools': [tool.name for tool in agent.tools]
    } for agent in agents]
    
    return JSONResponse(response)