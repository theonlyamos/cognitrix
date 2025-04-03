import json
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cognitrix.agents import Agent
from cognitrix.common.security import get_current_user
from cognitrix.providers import Session
from ...providers import LLM

agents_api = APIRouter(
    prefix='/agents',
    dependencies=[Depends(get_current_user)]
)

@agents_api.get('')
async def list_agents():
    agents = await Agent.all()

    return [agent.json() for agent in agents]

@agents_api.post('')
async def save_agent(request: Request, agent: Agent):
    data = await request.json()
    
    llm = LLM(**data['llm'])
    llm.provider = data['llm']['provider']

    agent.llm = llm
    await agent.save()
    
    if request.state.agent.id == agent.id:
        request.state.agent = agent
    
    return agent

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
    agent = await Agent.find_one({'id': agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return agent

@agents_api.get('/{agent_id}/session')
async def load_session(agent_id: str):
    agent = await Agent.find_one({'id': agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    session_id: str = ''
    if agent:
        session = await Session.get_by_agent_id(agent_id)
        if not session:
            session = Session(agent_id=agent_id)
            
        session_id = session.id
        await session.save()
        
    return JSONResponse({'session_id': session_id})

