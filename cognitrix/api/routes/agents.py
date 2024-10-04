import json
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cognitrix.agents import Agent
from cognitrix.common.security import get_current_user
from cognitrix.llms import Session
from ...llms import LLM

agents_api = APIRouter(
    prefix='/agents',
    dependencies=[Depends(get_current_user)]
)

@agents_api.get('')
async def list_agents():
    agents = Agent.all()

    response = [agent.model_dump() for agent in agents]
    
    return JSONResponse(response)

@agents_api.post('')
async def save_agent(request: Request, agent: Agent):
    data = await request.json()
    
    llm = LLM(**data['llm'])
    llm.provider = data['llm']['provider']

    agent.llm = llm
    agent.save()
    
    if request.state.agent.id == agent.id:
        request.state.agent = agent
    
    return JSONResponse(agent.json())

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
    agent = Agent.find_one({'id': agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    response = agent.json()
    
    return JSONResponse(response)

@agents_api.get('/{agent_id}/session')
async def load_session(agent_id: str):
    agent = Agent.find_one({'id': agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    session_id: str = ''
    if agent:
        session = await Session.get_by_agent_id(agent_id)
        if not session:
            session = Session(agent_id=agent_id)
            
        session_id = session.id
        session.save()
        
    return JSONResponse({'session_id': session_id})

