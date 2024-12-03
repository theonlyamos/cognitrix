import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import List

from cognitrix.common.security import get_current_user

from cognitrix.sessions.base import Session

sessions_api = APIRouter(
    prefix='/sessions',
    dependencies=[Depends(get_current_user)]
)

@sessions_api.get("")
async def get_all_sessions():
    sessions = [session.model_dump() for session in Session.all()]
    
    return JSONResponse(sessions)

@sessions_api.post("")
async def new_session(request: Request, session: Session):
    session.agent_id = request.state.agent.id
    session.save()
    return JSONResponse(session.model_dump())

@sessions_api.get("/{session_id}")
async def get_session(session_id: str):
    session = Session.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return JSONResponse(session.model_dump())

@sessions_api.delete("/{session_id}")
async def delete_session(session_id: str):
    session = Session.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    Session.remove({'id': session_id})
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
    session = Session.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return JSONResponse(session.chat)

@sessions_api.delete("/{session_id}/chat")
async def delete_chat(session_id: str):
    session = Session.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session.chat = []
    session.save()
    return JSONResponse({"message": "Chat deleted successfully"})

@sessions_api.get("/agents/{agent_id}")
async def sessions_by_agent(agent_id: str):
    sessions = Session.find({'agent_id': agent_id})
    return JSONResponse([session.model_dump() for session in sessions])

@sessions_api.get("/teams/{team_id}")
async def sessions_by_team(team_id: str):
    sessions = Session.find({'team_id': team_id})
    return JSONResponse([session.model_dump() for session in sessions])

@sessions_api.get("/tasks/{task_id}")
async def sessions_by_task(task_id: str):
    sessions = Session.find({'task_id': task_id})
    return JSONResponse([session.model_dump() for session in sessions])