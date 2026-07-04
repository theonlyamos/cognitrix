import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from cognitrix.common.security import get_current_user
from cognitrix.sessions.base import Session

sessions_api = APIRouter(
    prefix='/sessions',
    dependencies=[Depends(get_current_user)]
)


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
        'message_count': len(session.chat or []),
    }

@sessions_api.get("")
async def get_all_sessions():
    sessions = [session.json() for session in await Session.all()]

    return JSONResponse(sessions)

@sessions_api.post("")
async def new_session(request: Request, session: Session):
    # Only default the agent — a client-supplied agent_id must win.
    if not session.agent_id:
        session.agent_id = request.state.agent.id
    await session.save()
    return session.json()

@sessions_api.get("/{session_id}")
async def get_session(session_id: str):
    session = await Session.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return session.json()

@sessions_api.delete("/{session_id}")
async def delete_session(session_id: str):
    session = await Session.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # remove()/delete_one emit DELETE ... LIMIT on sqlite, which it rejects;
    # delete_many works (same fix as agents/tasks/teams).
    await Session.delete_many({'id': session_id})
    return JSONResponse({"message": "Session deleted successfully"})

@sessions_api.get("/{session_id}/events")
async def sse_endpoint(request: Request):
    sse_manager = request.state.sse_manager
    return await sse_manager.sse_endpoint(request)

# Add other endpoints to handle user input and trigger SSE events
@sessions_api.post("/{session_id}/chat")
async def chat_endpoint(request: Request):
    sse_manager = request.state.sse_manager
    data = await request.json()

    await sse_manager.action_queue.put(json.loads(data["message"]))
    return {"status": "Message sent"}

@sessions_api.get("/{session_id}/chat")
async def get_chat(session_id: str):
    session = await Session.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return JSONResponse(session.chat)

@sessions_api.delete("/{session_id}/chat")
async def delete_chat(session_id: str):
    session = await Session.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.chat = []
    await session.save()
    return JSONResponse({"message": "Chat deleted successfully"})

# List endpoints return summaries, not full sessions: every session carries its
# full chat (up to 1000 entries), so full-fat lists grow into MB-scale payloads.
# Load a transcript via GET /sessions/{id}/chat instead.

@sessions_api.get("/agents/{agent_id}")
async def sessions_by_agent(agent_id: str, exclude_tasks: bool = False):
    sessions = await Session.find({'agent_id': agent_id})
    if exclude_tasks:
        sessions = [s for s in sessions if not s.task_id]
    return [_session_summary(s) for s in sessions]

@sessions_api.get("/teams/{team_id}")
async def sessions_by_team(team_id: str):
    sessions = await Session.find({'team_id': team_id})
    return [_session_summary(s) for s in sessions]

@sessions_api.get("/tasks/{task_id}")
async def sessions_by_task(task_id: str):
    sessions = await Session.find({'task_id': task_id})
    return [_session_summary(s) for s in sessions]
