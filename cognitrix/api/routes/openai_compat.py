"""OpenAI-compatible shim so existing OpenAI SDKs can call a cognitrix agent as
a model. Mounted at the app root (/v1) — point a client at base_url=<host>/v1
with api_key=ctx_… .

Only the two endpoints real clients need: /v1/models and
/v1/chat/completions. Chat scope + agent allowlist apply exactly as on the
native generate endpoint.
"""

import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cognitrix.agents import Agent
from cognitrix.common.security import AuthContext, get_auth_context, require
from cognitrix.sessions.base import Session

logger = logging.getLogger('cognitrix.log')

CHAT_TIMEOUT = float(os.getenv('COGNITRIX_API_CHAT_TIMEOUT', '300'))

# Own router at the app root — NOT under /api/v1. Registered before the SPA
# catch-all in api/main.py so /v1/* isn't served index.html.
openai_api = APIRouter(prefix='/v1', dependencies=[Depends(require('chat'))])


class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False


async def _resolve_agent(model: str, ctx: AuthContext) -> Agent:
    agents = await Agent.all()
    match = next((a for a in agents if a.name.lower() == model.lower() or a.id == model), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Model '{model}' not found")
    if not ctx.agent_allowed(match.id):
        raise HTTPException(status_code=403, detail="API key not allowed for this model")
    return match


def _seed_session(agent: Agent, messages: list[ChatMessage]) -> Session:
    """Build an ephemeral (never saved) session whose history holds ALL the
    incoming messages — the final user turn included. Session prompts are built
    exclusively from session.chat, so seeding is the only way the model sees
    the request under save_history=False (save_history=True would persist junk
    sessions). The agent's own system prompt is always prepended by the context
    manager; incoming system messages are folded in as user-visible context."""
    session = Session(agent_id=agent.id)
    for m in messages:
        content = m.content or ''
        if not content:
            continue
        role = m.role.lower()
        if role == 'assistant':
            session.chat.append({'role': 'assistant', 'type': 'text', 'content': content})
        elif role == 'system':
            session.chat.append({'role': 'User', 'type': 'text', 'content': f'[system]\n{content}'})
        else:  # user / tool / anything else → a user turn
            session.chat.append({'role': 'User', 'type': 'text', 'content': content})
    return session


@openai_api.get('/models')
async def list_models(ctx: AuthContext = Depends(get_auth_context)):
    agents = await Agent.all()
    data = [
        {'id': a.name, 'object': 'model', 'created': 0, 'owned_by': 'cognitrix'}
        for a in agents if ctx.agent_allowed(a.id)
    ]
    return {'object': 'list', 'data': data}


@openai_api.post('/chat/completions')
async def chat_completions(body: ChatCompletionRequest, ctx: AuthContext = Depends(get_auth_context)):
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages is required")
    agent = await _resolve_agent(body.model, ctx)
    session = _seed_session(agent, body.messages)
    completion_id = f"chatcmpl-{session.id}"
    created = int(time.time())

    if body.stream:
        return _stream_completion(session, agent, completion_id, created, body.model)

    captured = ''

    async def capture(payload=None, *args, **kwargs):
        nonlocal captured
        content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
        if content:
            captured += content

    try:
        # Inert message: save_history=False means it never enters the prompt;
        # the seeded session.chat is the actual input.
        await asyncio.wait_for(
            session('', agent, interface='web', stream=True, output=capture, wsquery={}, save_history=False),
            timeout=CHAT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Generation timed out")

    answer = captured.strip()
    if 'Streaming error:' in answer:
        raise HTTPException(status_code=502, detail="Provider error during generation")

    return {
        'id': completion_id,
        'object': 'chat.completion',
        'created': created,
        'model': body.model,
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': answer},
            'finish_reason': 'stop',
        }],
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
    }


def _stream_completion(session: Session, agent: Agent, completion_id: str,
                       created: int, model: str) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue(maxsize=512)

    async def push(payload=None, *args, **kwargs):
        content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
        if content:
            await queue.put(str(content))

    async def producer():
        try:
            await asyncio.wait_for(
                session('', agent, interface='web', stream=True, output=push, wsquery={}, save_history=False),
                timeout=CHAT_TIMEOUT,
            )
        except Exception:
            logger.exception("OpenAI-shim stream failed for agent %s", agent.id)
        finally:
            await queue.put(None)

    def _chunk(delta: dict, finish_reason=None) -> str:
        payload = {
            'id': completion_id,
            'object': 'chat.completion.chunk',
            'created': created,
            'model': model,
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    async def event_stream():
        producer_task = asyncio.create_task(producer())
        try:
            yield _chunk({'role': 'assistant'})
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if 'Streaming error:' in chunk:
                    continue
                yield _chunk({'content': chunk})
            yield _chunk({}, finish_reason='stop')
            yield "data: [DONE]\n\n"
        finally:
            producer_task.cancel()

    return StreamingResponse(event_stream(), media_type='text/event-stream')
