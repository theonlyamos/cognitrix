
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from cognitrix.agents import Agent
from cognitrix.common.security import get_current_user, redact_secrets
from cognitrix.sessions.base import Session
from cognitrix.utils.sse import get_sse_manager

from ...providers import LLM

agents_api = APIRouter(
    prefix='/agents',
    dependencies=[Depends(get_current_user)]
)


async def _resolve_agent(agent_id, request: Request):
    """Resolve the agent for this request: by id if given, else the server default."""
    if agent_id:
        agent = await Agent.find_one({'id': agent_id})
        if agent:
            return agent
    return getattr(request.state, 'agent', None)


def _user_key(user) -> str:
    return str(getattr(user, 'id', None) or getattr(user, 'email', 'anon'))

@agents_api.get('')
async def list_agents():
    agents = await Agent.all()

    return [redact_secrets(agent.json()) for agent in agents]

@agents_api.post('')
async def save_agent(request: Request, agent: Agent):
    data = await request.json()

    llm = LLM(**data['llm'])
    llm.provider = data['llm']['provider']

    agent.llm = llm
    await agent.save()

    default_agent = getattr(request.state, 'agent', None)
    if default_agent is not None and default_agent.id == agent.id:
        request.state.agent = agent

    # Return the dict directly so FastAPI's encoder handles datetime/UUID;
    # wrapping in JSONResponse uses stdlib json and raises on datetime.
    return redact_secrets(agent.json())

@agents_api.get("/sse")
async def sse_endpoint(request: Request, agent_id: str | None = None, user=Depends(get_current_user)):
    # Per-(user, agent) manager: isolates concurrent users so one client's
    # stream never carries another's messages/agent.
    agent = await _resolve_agent(agent_id, request)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    manager = get_sse_manager(_user_key(user), agent.id, agent)
    return await manager.sse_endpoint(request)

# Add other endpoints to handle user input and trigger SSE events
@agents_api.post("/chat")
async def chat_endpoint(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    agent = await _resolve_agent(data.get("agent_id"), request)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    manager = get_sse_manager(_user_key(user), agent.id, agent)
    await manager.action_queue.put({
        "type": "chat_message",
        "content": data.get("message", ""),
        "session_id": data.get("session_id"),
    })
    return {"status": "Message sent"}

@agents_api.get('/{agent_id}')
async def load_agent(agent_id: str):
    agent = await Agent.find_one({'id': agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return redact_secrets(agent.json())

@agents_api.delete('/{agent_id}')
async def delete_agent(agent_id: str):
    agent = await Agent.find_one({'id': agent_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # delete_many avoids the odbms sqlite `delete_one` bug (emits DELETE … LIMIT,
    # which SQLite rejects); an id filter still deletes exactly one row.
    await Agent.delete_many({'id': agent_id})
    return {"message": "Agent deleted successfully"}

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

