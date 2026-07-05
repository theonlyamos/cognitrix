
import asyncio
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from cognitrix.agents import Agent
from cognitrix.common.security import (
    AuthContext,
    crud_scope,
    get_auth_context,
    get_current_user,
    jwt_only,
    redact_secrets,
    require,
)
from cognitrix.sessions.base import Session
from cognitrix.utils.sse import get_sse_manager

from ...providers import LLM

logger = logging.getLogger('cognitrix.log')

CHAT_TIMEOUT = float(os.getenv('COGNITRIX_API_CHAT_TIMEOUT', '300'))

agents_api = APIRouter(
    prefix='/agents',
    dependencies=[Depends(crud_scope)]
)

# Invoke routes live on their own router: generating with an agent is the
# 'chat' scope, not the 'write' crud_scope would infer from POST.
agents_invoke_api = APIRouter(
    prefix='/agents',
    dependencies=[Depends(require('chat'))]
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

# Browser-session plumbing: these two run full tool-enabled agent turns via
# the SSE action queue, so API keys (which must pass 'chat' scope + agent
# allowlists on the invoke endpoints) are rejected here.
@agents_api.get("/sse", dependencies=[Depends(jwt_only)])
async def sse_endpoint(request: Request, agent_id: str | None = None, user=Depends(get_current_user)):
    # Per-(user, agent) manager: isolates concurrent users so one client's
    # stream never carries another's messages/agent.
    agent = await _resolve_agent(agent_id, request)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    manager = get_sse_manager(_user_key(user), agent.id, agent)
    return await manager.sse_endpoint(request)

# Add other endpoints to handle user input and trigger SSE events
@agents_api.post("/chat", dependencies=[Depends(jwt_only)])
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

class GenerateRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = False


async def _resolve_generate_session(agent: Agent, session_id: str | None) -> Session:
    if session_id:
        session = await Session.get(session_id)
        if session is None or (session.agent_id and session.agent_id != agent.id):
            raise HTTPException(status_code=404, detail="Session not found for this agent")
        return session
    session = Session(agent_id=agent.id)
    await session.save()
    return session


@agents_invoke_api.post('/{agent_id}/generate')
async def generate(agent_id: str, body: GenerateRequest,
                   ctx: AuthContext = Depends(get_auth_context)):
    """Programmatic chat: one agent turn (tools run server-side), stateful via
    session_id. Blocking JSON by default; stream=true for SSE chunks."""
    agent = await Agent.find_one({'id': agent_id})
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not ctx.agent_allowed(agent.id):
        raise HTTPException(status_code=403, detail="API key not allowed for this agent")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    session = await _resolve_generate_session(agent, body.session_id)

    if body.stream:
        return _stream_generate(session, agent, body.message)

    captured = ''

    async def capture(payload=None, *args, **kwargs):
        nonlocal captured
        content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
        if content:
            captured += content

    try:
        await asyncio.wait_for(
            session(body.message, agent, interface='web', stream=True, output=capture, wsquery={}),
            timeout=CHAT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # The turn unwinds without persisting — save what we have so the
        # session survives (user message + any partial history).
        try:
            await session.save()
        except Exception:
            logger.exception("Could not save session %s after generate timeout", session.id)
        raise HTTPException(status_code=504, detail="Generation timed out")

    answer = captured.strip()
    if 'Streaming error:' in answer:
        raise HTTPException(status_code=502, detail="Provider error during generation")
    return {'reply': answer, 'session_id': session.id}


def _stream_generate(session: Session, agent: Agent, message: str) -> EventSourceResponse:
    """SSE bridge: a producer task runs the turn pushing chunks into a bounded
    queue; the generator drains it. Session.__call__ has no end-of-stream
    signal — the sentinel goes in after the awaited turn returns."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=512)
    error: dict = {}

    async def push(payload=None, *args, **kwargs):
        content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
        if content:
            await queue.put(str(content))

    async def producer():
        try:
            await asyncio.wait_for(
                session(message, agent, interface='web', stream=True, output=push, wsquery={}),
                timeout=CHAT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            error['detail'] = 'Generation timed out'
        except Exception:
            logger.exception("Streaming generate failed for agent %s", agent.id)
            error['detail'] = 'Generation failed'
        finally:
            await queue.put(None)  # sentinel: turn finished

    producer_task = asyncio.create_task(producer())

    async def event_stream():
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if 'Streaming error:' in chunk:
                    error.setdefault('detail', 'Provider error during generation')
                    continue
                yield {'event': 'chunk', 'data': json.dumps({'content': chunk})}
            if error:
                yield {'event': 'error', 'data': json.dumps(error)}
            else:
                yield {'event': 'done', 'data': json.dumps({'session_id': session.id})}
        finally:
            # Client disconnect (or timeout) — stop the turn and persist what
            # exists; a cancelled turn never reaches its own save.
            producer_task.cancel()
            try:
                await session.save()
            except Exception:
                logger.exception("Could not save session %s after stream end", session.id)

    return EventSourceResponse(event_stream())


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

