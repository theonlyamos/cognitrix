import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cognitrix.llms.session import Session
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

@agents_api.get("/sse")
async def sse_endpoint(request: Request):
    sse_manager = request.state.sse_manager
    return await sse_manager.sse_endpoint(request)

# Add other endpoints to handle user input and trigger SSE events
@agents_api.post("/chat")
async def chat_endpoint(request: Request):
    sse_manager = request.state.sse_manager
    data = await request.json()

    await sse_manager.action_queue.put(json.loads(data["message"]))
    return {"status": "Message sent"}

@agents_api.get('/{agent_id}')
async def load_agent(agent_id: str):
    agent = await Agent.get(agent_id)
    response = {}
    if agent:
        response = agent.dict()
    
    return JSONResponse(response)

@agents_api.get('/{agent_id}/session')
async def load_session(agent_id: str):
    agent = await Agent.get(agent_id)
    session_id: str = ''
    if agent:
        session = await Session.get_by_agent_id(agent_id)
        session_id = session.id
        await session.save()
        
    return JSONResponse({'session_id': session_id})

