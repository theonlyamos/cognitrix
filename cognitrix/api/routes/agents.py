
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

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
from cognitrix.utils.sse import SSEManagerCapacityError, get_sse_manager

from ...media.staging import (
    MAX_UPLOAD_COUNT,
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_TOTAL_BYTES,
    StagedAttachmentSet,
    _decode_data_url as _staging_decode_data_url,
    stage_legacy_data_urls,
    stage_upload_files,
)

from ...providers import LLM

logger = logging.getLogger('cognitrix.log')

CHAT_TIMEOUT = float(os.getenv('COGNITRIX_API_CHAT_TIMEOUT', '300'))

# Request parsers buffer/spool parts before staging sees them. Bound the ASGI
# body as well as decoded files: multipart gets 1 MiB of framing allowance;
# legacy JSON gets base64 expansion plus 1 MiB of metadata allowance.
MAX_CHAT_PAYLOAD_BYTES = 256 * 1024
MAX_MULTIPART_BODY_BYTES = (
    MAX_UPLOAD_TOTAL_BYTES + MAX_CHAT_PAYLOAD_BYTES + 1024 * 1024
)
MAX_JSON_BODY_BYTES = ((MAX_UPLOAD_TOTAL_BYTES + 2) // 3) * 4 + 1024 * 1024
_decode_data_url = _staging_decode_data_url


class _RequestBodyTooLarge(MultiPartException):
    def __init__(self):
        super().__init__('Chat request exceeds the size limit.')


def _body_limit_error() -> HTTPException:
    return HTTPException(status_code=413, detail='Chat request exceeds the size limit.')


def _install_bounded_receive(request: Request, limit: int) -> None:
    """Enforce a body envelope even for chunked or dishonest requests."""
    headers = getattr(request, 'headers', {}) or {}
    content_length = headers.get('content-length')
    if content_length:
        try:
            parsed_length = int(content_length)
            if parsed_length < 0:
                raise HTTPException(status_code=400, detail='Invalid Content-Length')
            if parsed_length > limit:
                raise _body_limit_error()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='Invalid Content-Length') from exc

    cached = getattr(request, '_body', None)
    if cached is not None and len(cached) > limit:
        raise _body_limit_error()
    receive = getattr(request, '_receive', None)
    if receive is None or getattr(request, '_chat_body_bounded', False):
        return

    consumed = 0

    async def bounded_receive():
        nonlocal consumed
        message = await receive()
        if message.get('type') == 'http.request':
            consumed += len(message.get('body', b''))
            if consumed > limit:
                raise _RequestBodyTooLarge()
        return message

    request._receive = bounded_receive
    request._chat_body_bounded = True


def _multipart_parser_error(exc: MultiPartException) -> HTTPException:
    detail = str(exc)
    if 'Too many files' in detail or 'Too many fields' in detail:
        return _body_limit_error()
    return HTTPException(status_code=400, detail='Malformed multipart request')


def _parse_json_payload(raw: str | bytes) -> dict:
    if isinstance(raw, str):
        if len(raw) > MAX_CHAT_PAYLOAD_BYTES:
            raise _body_limit_error()
        encoded = raw.encode('utf-8')
        if len(encoded) > MAX_CHAT_PAYLOAD_BYTES:
            raise _body_limit_error()
        source = raw
    else:
        if len(raw) > MAX_CHAT_PAYLOAD_BYTES:
            raise _body_limit_error()
        try:
            source = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail='Malformed payload JSON') from exc
    try:
        data = json.loads(source)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail='Malformed payload JSON') from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail='payload must be a JSON object')
    return data


@asynccontextmanager
async def _chat_request_parts(request: Request):
    """Yield parsed metadata and live multipart files under bounded parsing."""
    headers = getattr(request, 'headers', {}) or {}
    content_type = headers.get('content-type', '')
    multipart = content_type.lower().startswith('multipart/form-data')
    _install_bounded_receive(
        request, MAX_MULTIPART_BODY_BYTES if multipart else MAX_JSON_BODY_BYTES
    )

    if not multipart:
        try:
            data = await request.json()
        except _RequestBodyTooLarge as exc:
            raise _body_limit_error() from exc
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail='Malformed payload JSON') from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail='Request body must be a JSON object')
        yield data, []
        return

    try:
        async with request.form(
            max_files=MAX_UPLOAD_COUNT + 1,
            max_fields=10,
        ) as form:
            items = list(form.multi_items())
            if any(key not in {'payload', 'files'} for key, _value in items):
                raise HTTPException(status_code=400, detail='Unexpected multipart field')
            payload_parts = [value for key, value in items if key == 'payload']
            file_parts = [value for key, value in items if key == 'files']
            if len(payload_parts) != 1:
                raise HTTPException(status_code=400, detail='Exactly one payload part is required')
            if len(file_parts) > MAX_UPLOAD_COUNT:
                raise _body_limit_error()
            if any(not isinstance(value, UploadFile) for value in file_parts):
                raise HTTPException(status_code=400, detail='files parts must be files')

            payload = payload_parts[0]
            if isinstance(payload, UploadFile):
                mime = (payload.content_type or '').split(';', 1)[0].strip().lower()
                if mime != 'application/json':
                    raise HTTPException(
                        status_code=400,
                        detail='payload file must use application/json',
                    )
                raw_payload = await payload.read(MAX_CHAT_PAYLOAD_BYTES + 1)
            elif isinstance(payload, str):
                raw_payload = payload
            else:
                raise HTTPException(status_code=400, detail='Invalid payload part')
            data = _parse_json_payload(raw_payload)
            if file_parts and data.get('attachments'):
                raise HTTPException(
                    status_code=400,
                    detail='Do not mix multipart files with legacy attachments',
                )
            yield data, file_parts
    except _RequestBodyTooLarge as exc:
        raise _body_limit_error() from exc
    except MultiPartException as exc:
        raise _multipart_parser_error(exc) from exc
    except StarletteHTTPException as exc:
        detail = str(exc.detail)
        if exc.status_code == 400 and (
            'Too many files' in detail
            or 'Too many fields' in detail
            or detail == 'Chat request exceeds the size limit.'
        ):
            raise _body_limit_error() from exc
        raise


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
async def sse_endpoint(request: Request, agent_id: str | None = None,
                       stream_id: str | None = None, user=Depends(get_current_user)):
    # Per-browser manager: isolates users and prevents another tab or stale
    # reconnect for the same agent from consuming this stream's chat action.
    agent = await _resolve_agent(agent_id, request)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if stream_id is not None and (not stream_id.strip() or len(stream_id) > 128):
        raise HTTPException(status_code=400, detail="Invalid stream_id")
    try:
        manager = get_sse_manager(
            _user_key(user), agent.id, agent, stream_id=stream_id
        )
    except SSEManagerCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    assert manager is not None
    return await manager.sse_endpoint(request)

# Add other endpoints to handle user input and trigger SSE events
@agents_api.post('/chat', dependencies=[Depends(jwt_only)])
async def chat_endpoint(request: Request, user=Depends(get_current_user)):
    async with _chat_request_parts(request) as (data, upload_files):
        agent = await _resolve_agent(data.get('agent_id'), request)
        if agent is None:
            raise HTTPException(status_code=404, detail='Agent not found')
        stream_id = data.get('stream_id')
        if stream_id is not None and (
            not isinstance(stream_id, str)
            or not stream_id.strip()
            or len(stream_id) > 128
        ):
            raise HTTPException(status_code=400, detail='Invalid stream_id')
        manager = get_sse_manager(
            _user_key(user), agent.id, agent, stream_id=stream_id, create=False
        )
        if manager is None:
            raise HTTPException(
                status_code=409,
                detail='Connect to the event stream before sending a message',
            )
        if not manager.begin_turn():
            raise HTTPException(
                status_code=409,
                detail='A turn is already running for this browser stream',
            )

        staged: StagedAttachmentSet | None = None
        try:
            if upload_files:
                staged = await stage_upload_files(
                    upload_files,
                    user_key=_user_key(user),
                    stream_id=stream_id or 'default',
                )
            elif data.get('attachments'):
                staged = await stage_legacy_data_urls(
                    data['attachments'],
                    user_key=_user_key(user),
                    stream_id=stream_id or 'default',
                )
            await manager.action_queue.put({
                'type': 'chat_message',
                'content': data.get('message', ''),
                'session_id': data.get('session_id'),
                'staged_attachments': staged,
                'edit_source_artifact_id': data.get('edit_source_artifact_id'),
                'bypass_permissions': bool(data.get('bypass_permissions')),
            })
        except BaseException:
            try:
                manager.finish_turn()
            finally:
                if staged is not None:
                    await staged.cleanup()
            raise
    return {'status': 'Message sent'}


@agents_api.post("/stop", dependencies=[Depends(jwt_only)])
async def stop_endpoint(request: Request, user=Depends(get_current_user)):
    """Cancel the pending or active turn for this exact browser stream."""
    data = await request.json()
    agent = await _resolve_agent(data.get("agent_id"), request)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    stream_id = data.get("stream_id")
    if stream_id is not None and (
        not isinstance(stream_id, str) or not stream_id.strip() or len(stream_id) > 128
    ):
        raise HTTPException(status_code=400, detail="Invalid stream_id")
    manager = get_sse_manager(
        _user_key(user), agent.id, agent, stream_id=stream_id, create=False
    )
    if manager is None:
        raise HTTPException(
            status_code=409,
            detail="No turn is running for this browser stream",
        )
    if not manager.stop_current_turn():
        raise HTTPException(status_code=409, detail="No turn is running for this browser stream")
    return {"status": "stopping"}


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
        return _stream_generate(session, agent, body.message, ctx.tool_execution_context())

    captured = ''

    async def capture(payload=None, *args, **kwargs):
        nonlocal captured
        content = payload.get('content', '') if isinstance(payload, dict) else (str(payload) if payload else '')
        if content:
            captured += content

    try:
        await asyncio.wait_for(
            session(body.message, agent, interface='web', stream=True, output=capture,
                    wsquery={}, tool_context=ctx.tool_execution_context()),
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


def _stream_generate(session: Session, agent: Agent, message: str, tool_context) -> EventSourceResponse:
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
                session(message, agent, interface='web', stream=True, output=push,
                        wsquery={}, tool_context=tool_context),
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

