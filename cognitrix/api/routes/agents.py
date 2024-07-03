from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ...llms import LLM
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

@agents_api.post('')
async def save_agent(request: Request, agent: Agent):
    data = await request.json()
    
    llm = LLM(**data['llm'])
    llm.provider = data['llm']['provider']

    agent.llm = llm
    await agent.save()
    
    return JSONResponse(agent.dict())

@agents_api.get('/{agent_id}')
async def load_agent(agent_id: str):
    agent = await Agent.get(agent_id)
    response = {}
    if agent:
        response = agent.dict()
    
    return JSONResponse(response)

