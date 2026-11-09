"""The signed-in person's own data in one file: agents, chats, tasks, teams and
saved memories. A backup they can keep, read or move elsewhere. Keys,
passwords and saved sign-ins are never part of it. Browser sessions only.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, jwt_only, redact_secrets
from cognitrix.config import VERSION

account_api = APIRouter(prefix='/account')


def _messages(chat) -> list[dict]:
    """Text of a chat, without inline image data or tool plumbing."""
    out = []
    for message in chat or []:
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            continue
        if message.get('type') not in (None, 'text'):
            continue
        entry = {'role': message.get('role'), 'content': message['content']}
        for key in ('name', 'timestamp'):
            if message.get(key):
                entry[key] = message[key]
        out.append(entry)
    return out


@account_api.get('/export')
async def export_my_data(ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.api.routes.sessions import _owned_sessions, _session_summary
    from cognitrix.memory import saved
    from cognitrix.tasks import Task
    from cognitrix.teams.base import Team

    owner = agent_access.owner_key(ctx.user)
    agents = [
        agent for agent in await agent_access.visible_agents(owner)
        if str(getattr(agent, 'owner_id', '') or '') == owner
    ]
    for agent in agents:
        # A key typed into one agent stays on this server.
        if agent.llm is not None:
            agent.llm.api_key = ''
            agent.llm.extra_headers = None

    chats = []
    for session in await _owned_sessions(ctx):
        if getattr(session, 'incognito', False):
            continue
        summary = _session_summary(session)
        chats.append({
            'id': str(session.id),
            'agent_id': str(session.agent_id or ''),
            'title': summary['title'],
            'started': session.datetime,
            'task_id': session.task_id,
            'messages': _messages(session.chat),
        })

    tasks = [
        task.json() for task in await Task.all()
        if not getattr(task, 'deleted_at', None) and str(getattr(task, 'owner_id', '') or '') == owner
    ]
    teams = [
        team.json() for team in await Team.all()
        if str(getattr(team, 'owner_id', '') or '') == owner
    ]
    names = {str(agent.id): agent.name for agent in agents}
    memories = [saved.project(row, names) for row in await saved.list_memories(owner)]

    stamp = datetime.now(UTC)
    payload = {
        'cognitrix_export': 1,
        'version': VERSION,
        'exported_at': stamp.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'account': {'name': getattr(ctx.user, 'name', ''), 'email': getattr(ctx.user, 'email', '')},
        'agents': redact_secrets([agent.json() for agent in agents]),
        'chats': chats,
        'tasks': redact_secrets(tasks),
        'teams': redact_secrets(teams),
        'memories': memories,
    }
    from fastapi.encoders import jsonable_encoder

    return JSONResponse(
        jsonable_encoder(payload),
        headers={'Content-Disposition': f'attachment; filename="cognitrix-export-{stamp:%Y-%m-%d}.json"'},
    )
