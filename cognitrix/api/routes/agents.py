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

@agents_api.get('/{agent_id}')
async def load_agent(agent_id: str):
    agent = await Agent.get(agent_id)
    response = {}
    if agent:
        response = agent.dict()
    
    return JSONResponse(response)