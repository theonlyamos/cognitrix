
import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

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

# Upload limits. base64-in-JSON is bounded so a single request can't exhaust
# memory; enforced on DECODED bytes (base64 inflates ~33%).
# ponytail: caps + JSON transport; switch to streamed multipart if large files matter.
MAX_UPLOAD_FILE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_COUNT = 20  # backstop on file count (bytes are already capped)


def _uploads_root() -> Path:
    """Uploads land under the agent's tools root so its (confined) file tools can read them."""
    return Path(os.getenv('COGNITRIX_TOOLS_ROOT') or os.getcwd()) / 'uploads'


def _decode_data_url(data_url: str) -> bytes | None:
    """Decode a `data:<mime>;base64,<payload>` string to raw bytes (None if malformed)."""
    if not isinstance(data_url, str) or not data_url.startswith('data:'):
        return None
    try:
        _, b64 = data_url.split(',', 1)
        return base64.b64decode(b64)
    except Exception:
        return None


def _is_real_image(raw: bytes) -> bool:
    """Verify bytes are a decodable image — don't trust the client's declared kind."""
    try:
        from PIL import Image
        Image.open(io.BytesIO(raw)).verify()
        return True
    except Exception:
        return False


def _save_attachments(attachments: list) -> tuple[list[dict], list[dict]]:
    """Persist uploads under the tools root; return (images, files) as [{name, path}].

    Images are kept on disk (encoded to a data URI at model-send time); other files
    are handed to the agent as workspace paths. Names are sanitized (uuid-prefixed,
    basename only) so a crafted name can't escape the uploads dir.
    """
    images: list[dict] = []
    files: list[dict] = []
    total = 0
    batch = uuid4().hex
    for att in (attachments or [])[:MAX_UPLOAD_COUNT]:
        if not isinstance(att, dict):
            continue
        raw = _decode_data_url(att.get('dataUrl', ''))
        if raw is None:
            continue
        total += len(raw)
        if len(raw) > MAX_UPLOAD_FILE_BYTES or total > MAX_UPLOAD_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="Attachment exceeds the size limit.")
        name = att.get('name') or 'file'
        safe = f"{uuid4().hex}-{Path(name).name}"
        dest_dir = _uploads_root() / batch
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe
        dest.write_bytes(raw)
        entry = {'name': name, 'path': str(dest.resolve())}
        if att.get('kind') == 'image' and _is_real_image(raw):
            images.append(entry)
        else:
            files.append(entry)
    return images, files

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
    # base64-decode + PIL verify + disk writes are blocking — keep them off the loop.
    images, files = await asyncio.to_thread(_save_attachments, data.get("attachments") or [])
    manager = get_sse_manager(_user_key(user), agent.id, agent)
    await manager.action_queue.put({
        "type": "chat_message",
        "content": data.get("message", ""),
        "session_id": data.get("session_id"),
        "images": images,
        "files": files,
        "bypass_permissions": bool(data.get("bypass_permissions")),
    })
    return {"status": "Message sent"}


@agents_api.post("/approval", dependencies=[Depends(jwt_only)])
async def approval_endpoint(request: Request, user=Depends(get_current_user)):
    """Resolve a pending browser approval for a risky tool call (Approve/Deny).

    Runs on its own request — never through the SSE action queue, which is busy
    draining the awaiting turn. Scoped to the requesting user so one user can't
    answer another's prompt.
    """
    data = await request.json()
    request_id = data.get("request_id")
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")
    from cognitrix.safety.approval_gate import resolve_web_approval
    ok = resolve_web_approval(
        request_id,
        approved=bool(data.get("approved")),
        user_key=_user_key(user),
        remember=bool(data.get("remember")),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved.")
    return {"status": "resolved"}


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

