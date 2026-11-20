"""Projects (cognitrix/projects): chats and tasks grouped with shared
instructions, agents, folders, knowledge, memories and a computer.

Browser sessions only.

    GET    /projects                         the person's projects (?archived=true for archived ones)
    POST   /projects                         create {name, description?, instructions?, color?, agents?,
                                             folders?, knowledge?, computer?, pc_folders?, use_account_memory?}
    GET    /projects/{id}                    one project with its chats and tasks
    PATCH  /projects/{id}                    change any of the fields above, pinned, archived
    DELETE /projects/{id}                    remove it (?delete_chats=true also deletes its chats)
    PUT    /projects/chats/{session_id}      move a chat {project_id | null}
    PUT    /projects/tasks/{task_id}         move a task {project_id | null}
    GET    /projects/{id}/memories           the project's own memories
    POST   /projects/{id}/memories           add one {content}
    GET    /projects/{id}/changes            file changes across the project's chats
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.projects import store
from cognitrix.projects.store import ProjectError

logger = logging.getLogger('cognitrix.projects')

projects_api = APIRouter(prefix='/projects')

MAX_CHANGE_CHATS = 30


def _owner(ctx: AuthContext) -> str:
    from cognitrix.agents.access import owner_key

    return owner_key(ctx.user)


def _error(exc: ProjectError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={'code': exc.code, 'message': exc.message})


async def _mine(ctx: AuthContext, project_id: str):
    row = await store.project_of(_owner(ctx), project_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Project not found')
    return row


class ProjectBody(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    color: str | None = None
    agents: list[str] | None = None
    folders: list[dict[str, Any]] | None = None
    knowledge: list[str] | None = None
    computer: str | None = None
    pc_folders: list[str] | None = None
    use_account_memory: bool | None = None
    pinned: bool | None = None
    archived: bool | None = None
    read_instructions: bool | None = None


class MoveBody(BaseModel):
    project_id: str | None = None


class MemoryBody(BaseModel):
    content: str


async def _chats_of(user_id: str, project_id: str) -> list[dict[str, Any]]:
    from cognitrix.sessions.index import KIND_AGENT, KIND_TEAM, ChatIndex

    rows = await ChatIndex.find({'user_id': str(user_id), 'project_id': str(project_id)}) or []
    chats = [{
        'id': row.session_id,
        'title': row.custom_title or row.title,
        'agent_id': row.agent_id or None,
        'team_id': row.team_id or None,
        'kind': row.kind,
        'pinned': row.pinned,
        'archived': row.archived,
        'message_count': row.message_count,
        'updated_at': row.changed,
    } for row in rows if row.kind in (KIND_AGENT, KIND_TEAM) and not row.incognito]
    chats.sort(key=lambda c: str(c['updated_at'] or ''), reverse=True)
    return chats


async def _tasks_of(user_id: str, project_id: str) -> list[dict[str, Any]]:
    try:
        from cognitrix.tasks.base import Task

        rows = await Task.find({'project_id': str(project_id)}) or []
    except Exception:
        return []
    return [{'id': str(t.id), 'title': getattr(t, 'title', '') or getattr(t, 'description', '')[:80],
             'status': str(getattr(getattr(t, 'status', ''), 'value', getattr(t, 'status', '')) or '')}
            for t in rows if str(getattr(t, 'owner_id', '') or '') == str(user_id)
            and not getattr(t, 'deleted_at', None)]


@projects_api.get('')
async def list_projects(archived: bool = False, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    owner = _owner(ctx)
    rows = await store.projects_of(owner, archived=archived)
    from cognitrix.sessions.index import ChatIndex

    counts: dict[str, dict[str, Any]] = {}
    for row in await ChatIndex.find({'user_id': owner}) or []:
        if not row.project_id or row.incognito or row.archived:
            continue
        entry = counts.setdefault(row.project_id, {'chats': 0, 'last_active': ''})
        entry['chats'] += 1
        entry['last_active'] = max(entry['last_active'], row.changed or '')
    return {'projects': [store.view(p, counts.get(str(p.id), {'chats': 0, 'last_active': ''})) for p in rows]}


@projects_api.post('')
async def create_project(body: ProjectBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    try:
        row = await store.create(_owner(ctx), body.model_dump(exclude_none=True))
    except ProjectError as exc:
        raise _error(exc) from None
    return store.view(row, {'chats': 0, 'last_active': ''})


@projects_api.get('/{project_id}')
async def get_project(project_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    row = await _mine(ctx, project_id)
    owner = _owner(ctx)
    chats = await _chats_of(owner, str(row.id))
    return store.view(row, {'chats': chats, 'tasks': await _tasks_of(owner, str(row.id))})


@projects_api.patch('/{project_id}')
async def update_project(project_id: str, body: ProjectBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    row = await _mine(ctx, project_id)
    try:
        row = await store.update(row, body.model_dump(exclude_unset=True))
    except ProjectError as exc:
        raise _error(exc) from None
    return store.view(row)


@projects_api.delete('/{project_id}')
async def delete_project(project_id: str, delete_chats: bool = False,
                         ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    row = await _mine(ctx, project_id)
    touched = await store.delete(row, delete_chats=delete_chats)
    if touched:
        from cognitrix.sessions.index import ChatIndex, refresh

        for entry in await ChatIndex.find({'project_id': str(row.id)}) or []:
            await refresh(entry.session_id)
    return {'deleted': True, 'chats': touched}


@projects_api.put('/chats/{session_id}')
async def move_chat(session_id: str, body: MoveBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.sessions.index import refresh

    try:
        session = await store.move_chat(_owner(ctx), session_id, body.project_id)
    except ProjectError as exc:
        raise _error(exc) from None
    await refresh(session_id)
    return {'id': session_id, 'project_id': getattr(session, 'project_id', None)}


@projects_api.put('/tasks/{task_id}')
async def move_task(task_id: str, body: MoveBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.tasks.base import Task

    owner = _owner(ctx)
    task = await Task.get(task_id)
    if task is None or str(getattr(task, 'owner_id', '') or '') != owner:
        raise HTTPException(status_code=404, detail='Task not found')
    if body.project_id and await store.project_of(owner, body.project_id) is None:
        raise HTTPException(status_code=404, detail='Project not found')
    await Task.update_one({'id': task.id}, {'project_id': body.project_id or None})
    return {'id': task_id, 'project_id': body.project_id or None}


@projects_api.get('/{project_id}/memories')
async def project_memories(project_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.memory import saved

    row = await _mine(ctx, project_id)
    rows = await saved.list_memories(_owner(ctx), project_id=str(row.id))
    return {'memories': [saved.project(m) for m in rows]}


@projects_api.post('/{project_id}/memories')
async def add_project_memory(project_id: str, body: MemoryBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.memory import saved

    row = await _mine(ctx, project_id)
    try:
        memory, _created = await saved.add(_owner(ctx), body.content, scope='project', project_id=str(row.id),
                                           source='user', by='person')
    except saved.MemoryError_ as exc:
        raise HTTPException(status_code=400, detail={'code': exc.code, 'message': exc.message}) from None
    return saved.project(memory)


@projects_api.get('/{project_id}/changes')
async def project_changes(project_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """File changes waiting for review in the project's chats, newest chat
    first. Each chat's changes are reviewed with the chat's own changes API
    (GET/POST /sessions/{id}/changes)."""
    from cognitrix.file_changes.service import chat_changes
    from cognitrix.sessions.base import Session

    row = await _mine(ctx, project_id)
    owner = _owner(ctx)
    chats = [c for c in await _chats_of(owner, str(row.id)) if c['kind'] == 'agent' and not c['archived']]

    async def one(chat: dict[str, Any]) -> dict[str, Any] | None:
        session = await Session.get(chat['id'])
        if session is None or str(session.user_id or '') != owner:
            return None
        try:
            changes = await chat_changes(session)
        except Exception:
            logger.debug('Could not read changes for %s', chat['id'], exc_info=True)
            return None
        sources = changes.get('sources') or []
        if not sources:
            return None
        return {'session_id': chat['id'], 'title': chat['title'], 'agent_id': chat['agent_id'],
                'updated_at': chat['updated_at'],
                'files': sum(len(s['files']) for s in sources),
                'added': sum(s['added'] for s in sources),
                'removed': sum(s['removed'] for s in sources),
                'pending': sum(s['pending'] for s in sources),
                'sources': sources}

    found = await asyncio.gather(*(one(c) for c in chats[:MAX_CHANGE_CHATS]))
    # With the shared project sandbox every chat sees the same sandbox copy:
    # show it once, under the newest chat.
    seen: set[str] = set()
    out = []
    for chat in (c for c in found if c):
        kept = []
        for src in chat['sources']:
            if src.get('kind') == 'sandbox':
                if src['key'] in seen:
                    continue
                seen.add(src['key'])
            kept.append(src)
        if not kept:
            continue
        chat.update(sources=kept, files=sum(len(s['files']) for s in kept),
                    added=sum(s['added'] for s in kept), removed=sum(s['removed'] for s in kept),
                    pending=sum(s['pending'] for s in kept))
        out.append(chat)
    return {
        'chats': out,
        'files': sum(c['files'] for c in out),
        'added': sum(c['added'] for c in out),
        'removed': sum(c['removed'] for c in out),
        'pending': sum(c['pending'] for c in out),
    }
