import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from cognitrix.agents import Agent
from cognitrix.artifacts import delete_session_artifacts
from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, jwt_only
from cognitrix.sessions.access import (
    authorization_user_id,
    session_access_allowed,
    visible_sessions,
)
from cognitrix.sessions.base import Session
from cognitrix.tasks.run import TaskRun, run_acl_allowed

sessions_api = APIRouter(
    prefix='/sessions',
    dependencies=[Depends(crud_scope)]
)


class SessionCreate(BaseModel):
    """Client-selectable fields for a new ordinary conversation.

    Persistence identity, transcript contents, and every TaskRun execution
    field are intentionally absent. ``extra='forbid'`` rejects those attempts
    before a Session object can reach ``save()``.
    """

    model_config = ConfigDict(extra='forbid')

    agent_id: str | None = None
    team_id: str | None = None
    # Retained for wire compatibility, but ownership remains server-authored.
    user_id: str | None = None


def _session_title(chat) -> str:
    """Derive a conversation title from the first user text message."""
    for m in chat or []:
        if str(m.get('role', '')).lower() == 'user' and m.get('type') == 'text' and m.get('content'):
            first_line = str(m['content']).strip().splitlines()[0]
            return first_line[:60] + ('…' if len(first_line) > 60 else '')
    return 'New conversation'


def _session_summary(session: Session) -> dict:
    """Slim projection for list views — full transcripts come from
    GET /sessions/{id}/chat, not list endpoints."""
    data = session.json()
    return {
        'id': session.id,
        'title': _session_title(session.chat),
        'datetime': session.datetime,
        'updated_at': data.get('updated_at'),
        'started_at': session.started_at,
        'completed_at': session.completed_at,
        'task_id': session.task_id,
        'run_id': session.run_id,
        'step_index': session.step_index,
        'step_title': session.step_title,
        'message_count': len(session.chat or []),
    }


async def _authorized_run(run_id: str, ctx: AuthContext) -> TaskRun:
    """Resolve a TaskRun and enforce its immutable enqueue-time ACL."""
    run = await TaskRun.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Task run not found")
    if not run_acl_allowed(run, ctx):
        raise HTTPException(status_code=403, detail="Not allowed to access this task run")
    return run


async def _authorized_session(session_id: str, ctx: AuthContext) -> Session:
    session = await Session.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.run_id:
        await _authorized_run(session.run_id, ctx)
        return session
    if not await session_access_allowed(session, ctx):
        raise HTTPException(status_code=403, detail="Not allowed to access this session")
    return session


async def _visible_sessions(sessions: list[Session], ctx: AuthContext) -> list[Session]:
    """Filter ordinary sessions by owner and run sessions by immutable ACL."""
    return await visible_sessions(sessions, ctx)


@sessions_api.get("")
async def get_all_sessions(ctx: AuthContext = Depends(get_auth_context)):
    sessions = await _visible_sessions(list(await Session.all()), ctx)

    return JSONResponse([session.json() for session in sessions])


@sessions_api.post("")
async def new_session(
    request: Request,
    body: SessionCreate,
    ctx: AuthContext = Depends(get_auth_context),
):
    # Construct the persistence model server-side so ids, transcripts, and
    # TaskRun execution metadata can never flow in from the request body.
    session = Session(
        agent_id=body.agent_id,
        team_id=body.team_id,
        user_id=authorization_user_id(ctx),
    )
    # Only default the agent — a client-supplied agent_id must win.
    if not session.agent_id:
        session.agent_id = request.state.agent.id
    await session.save()
    return session.json()


@sessions_api.get("/{session_id}")
async def get_session(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    session = await _authorized_session(session_id, ctx)
    return session.json()


@sessions_api.delete("/{session_id}")
async def delete_session(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    session = await _authorized_session(session_id, ctx)
    # remove()/delete_one emit DELETE ... LIMIT on sqlite, which it rejects;
    # delete_many works (same fix as agents/tasks/teams).
    await delete_session_artifacts(
        str(session.id),
        user_id=authorization_user_id(ctx),
    )
    await Session.delete_many({'id': session_id})
    return JSONResponse({"message": "Session deleted successfully"})


# Browser-session plumbing: the action queue runs tool-enabled agent turns
# (including arbitrary client-supplied action dicts), so API keys are rejected
# — they must use the scope-checked invoke endpoints instead.
async def _browser_session_manager(
    request: Request,
    session_id: str,
    ctx: AuthContext,
):
    from cognitrix.utils.sse import SSEManagerCapacityError, get_sse_manager

    session = await _authorized_session(session_id, ctx)
    agent = await Agent.get(session.agent_id) if session.agent_id else None
    if agent is None:
        agent = getattr(request.state, 'agent', None)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        return get_sse_manager(
            authorization_user_id(ctx),
            str(agent.id),
            agent,
            stream_id=f"session:{session_id}",
        )
    except SSEManagerCapacityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@sessions_api.get("/{session_id}/events", dependencies=[Depends(jwt_only)])
async def sse_endpoint(
    session_id: str,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
):
    manager = await _browser_session_manager(request, session_id, ctx)
    return await manager.sse_endpoint(request)


# Add other endpoints to handle user input and trigger SSE events
@sessions_api.post("/{session_id}/chat", dependencies=[Depends(jwt_only)])
async def chat_endpoint(
    session_id: str,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
):
    manager = await _browser_session_manager(request, session_id, ctx)
    data = await request.json()
    try:
        action = json.loads(data["message"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid chat message") from exc
    if not isinstance(action, dict) or action.get("type") != "chat_message":
        raise HTTPException(status_code=400, detail="Only chat_message actions are accepted")
    # The authorized URL is authoritative; ignore a client-supplied session id.
    action["session_id"] = session_id
    if not manager.begin_turn():
        raise HTTPException(status_code=409, detail="A turn is already running")
    try:
        await manager.action_queue.put(action)
    except BaseException:
        manager.finish_turn()
        raise
    return {"status": "Message sent"}


@sessions_api.get("/{session_id}/chat")
async def get_chat(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    session = await _authorized_session(session_id, ctx)
    return JSONResponse(session.chat)


@sessions_api.delete("/{session_id}/chat")
async def delete_chat(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    session = await _authorized_session(session_id, ctx)
    session.chat = []
    await session.save()
    await delete_session_artifacts(
        str(session.id),
        user_id=authorization_user_id(ctx),
    )
    return JSONResponse({"message": "Chat deleted successfully"})

# List endpoints return summaries, not full sessions: every session carries its
# full chat (up to 1000 entries), so full-fat lists grow into MB-scale payloads.
# Load a transcript via GET /sessions/{id}/chat instead.


@sessions_api.get("/agents/{agent_id}")
async def sessions_by_agent(
    agent_id: str,
    exclude_tasks: bool = False,
    ctx: AuthContext = Depends(get_auth_context),
):
    sessions = list(await Session.find({'agent_id': agent_id}))
    if exclude_tasks:
        sessions = [s for s in sessions if not s.task_id]
    sessions = await _visible_sessions(sessions, ctx)
    return [_session_summary(s) for s in sessions]


@sessions_api.get("/teams/{team_id}")
async def sessions_by_team(
    team_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    sessions = await _visible_sessions(list(await Session.find({'team_id': team_id})), ctx)
    return [_session_summary(s) for s in sessions]


@sessions_api.get("/tasks/{task_id}")
async def sessions_by_task(
    task_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    sessions = await _visible_sessions(list(await Session.find({'task_id': task_id})), ctx)
    return [_session_summary(s) for s in sessions]


@sessions_api.get("/runs/{run_id}")
async def sessions_by_run(
    run_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    """Step sessions of one task run (summaries; transcripts via /{id}/chat)."""
    await _authorized_run(run_id, ctx)
    sessions = await Session.find({'run_id': run_id})
    return [_session_summary(s) for s in sessions]
