"""Saved memories: list, add, edit, pin and delete what agents remember about
you, and the memory settings. Each memory is private to the person it's about.
Browser sessions only, so an API key can't read or change them.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.memory import importer, saved

memories_api = APIRouter(prefix='/memories')


class MemoryBody(BaseModel):
    content: str
    scope: Literal['global', 'agent'] = 'global'
    agent_id: str | None = None
    pinned: bool = False


class MemoryUpdate(BaseModel):
    content: str | None = None
    pinned: bool | None = None
    scope: Literal['global', 'agent'] | None = None
    agent_id: str | None = None


class SettingsBody(BaseModel):
    enabled: bool | None = None
    history_enabled: bool | None = None
    auto_save: Literal['auto', 'ask', 'explicit'] | None = None


class DeleteAll(BaseModel):
    confirm: bool = False


class ImportBody(BaseModel):
    text: str


class PendingBody(BaseModel):
    approve: bool = True
    """True approves every waiting memory; False discards them all."""


def _owner(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


async def _agent_names(user_key: str) -> dict[str, str]:
    return {str(a.id): a.name for a in await agent_access.visible_agents(user_key)}


async def _check_agent(user_key: str, agent_id: str | None) -> str:
    agent_id = str(agent_id or '').strip()
    if not agent_id or agent_id not in await _agent_names(user_key):
        raise HTTPException(status_code=400, detail='Choose one of your agents for an agent-only memory')
    return agent_id


def _bad(exc: saved.MemoryError_) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.message)


async def _settings_json(user_key: str) -> dict:
    row = await saved.get_settings(user_key)
    return {'enabled': bool(row.enabled), 'history_enabled': bool(row.history_enabled), 'auto_save': row.auto_save}


@memories_api.get('')
async def list_memories(scope: str | None = None, agent_id: str | None = None, q: str | None = None,
                        ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    names = await _agent_names(owner)
    rows = await saved.list_memories(owner, scope=scope, agent_id=agent_id, query=q)
    return {
        'memories': [saved.project(r, names) for r in rows],
        'settings': await _settings_json(owner),
        'limit': saved.MAX_PER_USER,
        'max_chars': saved.MAX_CHARS,
    }


@memories_api.post('')
async def add_memory(body: MemoryBody, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    agent_id = await _check_agent(owner, body.agent_id) if body.scope == 'agent' else ''
    try:
        row, created = await saved.add(owner, body.content, scope=body.scope, agent_id=agent_id,
                                       source='user', pinned=body.pinned)
    except saved.MemoryError_ as exc:
        raise _bad(exc) from None
    if not created:
        raise HTTPException(status_code=409, detail=f'Already saved: {row.content}')
    return saved.project(row, await _agent_names(owner))


@memories_api.get('/settings')
async def get_settings(ctx: AuthContext = Depends(jwt_only)):
    return await _settings_json(_owner(ctx))


@memories_api.put('/settings')
async def put_settings(body: SettingsBody, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    try:
        await saved.save_settings(owner, enabled=body.enabled, auto_save=body.auto_save,
                                  history_enabled=body.history_enabled)
    except saved.MemoryError_ as exc:
        raise _bad(exc) from None
    return await _settings_json(owner)


@memories_api.get('/legacy')
async def legacy_status(ctx: AuthContext = Depends(jwt_only)):
    """How much the old automatic memory (raw chat messages) still holds for you."""
    count = await asyncio.to_thread(saved.legacy_count, _owner(ctx))
    return {'count': count}


@memories_api.delete('/legacy')
async def delete_legacy(ctx: AuthContext = Depends(jwt_only)):
    removed = await asyncio.to_thread(saved.delete_legacy, _owner(ctx))
    return {'deleted': removed}


@memories_api.post('/legacy/convert')
async def convert_legacy(ctx: AuthContext = Depends(jwt_only)):
    """Read the old automatic memory into memories you approve one by one."""
    try:
        return await importer.convert_legacy(_owner(ctx))
    except importer.ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@memories_api.get('/export')
async def export_memories(format: Literal['json', 'markdown'] = 'json', ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    stamp = saved.now_str()[:10]
    if format == 'markdown':
        text = await importer.export_markdown(owner, await _agent_names(owner))
        return PlainTextResponse(text, media_type='text/markdown; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="cognitrix-memories-{stamp}.md"'})
    return JSONResponse(await importer.export_json(owner), headers={
        'Content-Disposition': f'attachment; filename="cognitrix-memories-{stamp}.json"'})


@memories_api.post('/import')
async def import_memories(body: ImportBody, ctx: AuthContext = Depends(jwt_only)):
    """Propose memories from a paste (another assistant's memory list, notes,
    or this app's export). They wait for your OK before any agent sees them."""
    owner = _owner(ctx)
    try:
        return await importer.import_text(owner, body.text, valid_agents=set(await _agent_names(owner)))
    except importer.ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@memories_api.post('/pending')
async def settle_pending(body: PendingBody, ctx: AuthContext = Depends(jwt_only)):
    """Approve or discard every memory waiting for your OK."""
    owner = _owner(ctx)
    rows = await saved.pending_memories(owner)
    for row in rows:
        if body.approve:
            await saved.confirm(row)
        else:
            await saved.delete(row)
    return {'approved' if body.approve else 'discarded': len(rows)}


@memories_api.post('/{memory_id}/approve')
async def approve_memory(memory_id: str, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    row = await saved.get_memory(owner, memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Memory not found')
    return saved.project(await saved.confirm(row), await _agent_names(owner))


@memories_api.patch('/{memory_id}')
async def update_memory(memory_id: str, body: MemoryUpdate, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    row = await saved.get_memory(owner, memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Memory not found')
    agent_id = None
    if body.scope == 'agent':
        agent_id = await _check_agent(owner, body.agent_id or row.agent_id)
    try:
        row = await saved.update(row, content=body.content, pinned=body.pinned,
                                 scope=body.scope, agent_id=agent_id)
    except saved.MemoryError_ as exc:
        raise _bad(exc) from None
    return saved.project(row, await _agent_names(owner))


@memories_api.delete('/{memory_id}')
async def delete_memory(memory_id: str, ctx: AuthContext = Depends(jwt_only)):
    row = await saved.get_memory(_owner(ctx), memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Memory not found')
    await saved.delete(row)
    return {'deleted': True}


@memories_api.post('/delete-all')
async def delete_all(body: DeleteAll, ctx: AuthContext = Depends(jwt_only)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail='Send {"confirm": true} to delete every memory')
    return {'deleted': await saved.delete_all(_owner(ctx))}
