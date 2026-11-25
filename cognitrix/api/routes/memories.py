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
from cognitrix.memory import dream, importer, notion_backup, review, saved

memories_api = APIRouter(prefix='/memories')


class MemoryBody(BaseModel):
    content: str
    scope: Literal['global', 'agent'] = 'global'
    agent_id: str | None = None
    pinned: bool = False
    kind: str | None = None
    valid_until: str | None = None


class MemoryUpdate(BaseModel):
    content: str | None = None
    pinned: bool | None = None
    scope: Literal['global', 'agent'] | None = None
    agent_id: str | None = None
    kind: str | None = None
    """'' clears it."""
    valid_until: str | None = None
    """'' clears it."""


class SettingsBody(BaseModel):
    enabled: bool | None = None
    history_enabled: bool | None = None
    auto_save: Literal['auto', 'ask', 'explicit'] | None = None
    dream_enabled: bool | None = None
    review_enabled: bool | None = None


class ProfileBody(BaseModel):
    text: str
    locked: bool | None = None
    """Editing the summary yourself locks it from dream mode unless you say otherwise."""


class ProposalBody(BaseModel):
    approve: bool = True
    keep_id: str | None = None
    """For a conflict: the memory to keep."""


class DeleteAll(BaseModel):
    confirm: bool = False


class ImportBody(BaseModel):
    text: str


class NotionBackupBody(BaseModel):
    enabled: bool


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
    return {'enabled': bool(row.enabled), 'history_enabled': bool(row.history_enabled), 'auto_save': row.auto_save,
            'dream_enabled': bool(row.dream_enabled), 'review_enabled': bool(row.review_enabled)}


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
        'edit_max_chars': saved.STORED_MAX_CHARS,
    }


@memories_api.post('')
async def add_memory(body: MemoryBody, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    agent_id = await _check_agent(owner, body.agent_id) if body.scope == 'agent' else ''
    try:
        row, created = await saved.add(owner, body.content, scope=body.scope, agent_id=agent_id,
                                       source='user', pinned=body.pinned, kind=body.kind or '',
                                       valid_until=body.valid_until or '')
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
                                  history_enabled=body.history_enabled, dream_enabled=body.dream_enabled,
                                  review_enabled=body.review_enabled)
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
            await saved.confirm(row, by='person')
        else:
            await saved.delete(row, by='person')
    return {'approved' if body.approve else 'discarded': len(rows)}


@memories_api.get('/history')
async def memory_history(memory_id: str | None = None, dream_id: str | None = None, limit: int = 100,
                         ctx: AuthContext = Depends(jwt_only)):
    """Recent changes to your memories, newest first, each with Undo."""
    rows = await saved.history(_owner(ctx), memory_id=memory_id, dream_id=dream_id, limit=min(max(limit, 1), 500))
    return {'changes': [saved.project_change(r) for r in rows]}


@memories_api.post('/history/{change_id}/undo')
async def undo_change(change_id: str, ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.models.memory import MemoryChange

    owner = _owner(ctx)
    change = await MemoryChange.get(change_id)
    if change is None or str(change.user_id) != owner:
        raise HTTPException(status_code=404, detail='Change not found')
    try:
        row = await saved.undo(change)
    except saved.UndoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {'memory': saved.project(row, await _agent_names(owner)) if row else None}


@memories_api.get('/profile')
async def get_profile(ctx: AuthContext = Depends(jwt_only)):
    row = await saved.get_profile(_owner(ctx))
    return {'text': row.text if row else '', 'locked': bool(row.locked) if row else False,
            'updated': row.updated if row else None, 'by_dream': bool(row and row.dream_id and not row.locked)}


@memories_api.put('/profile')
async def put_profile(body: ProfileBody, ctx: AuthContext = Depends(jwt_only)):
    try:
        row = await saved.set_profile(_owner(ctx), body.text, locked=True if body.locked is None else body.locked)
    except saved.MemoryError_ as exc:
        raise _bad(exc) from None
    return {'text': row.text, 'locked': bool(row.locked), 'updated': row.updated, 'by_dream': False}


@memories_api.get('/dream')
async def dream_status(ctx: AuthContext = Depends(jwt_only)):
    """Dream mode: recent runs, whether one is running, and changes waiting for your OK."""
    owner = _owner(ctx)
    runs = await dream.runs_for(owner, 10)
    proposals = [await dream.public_proposal(p) for p in await dream.proposals_for(owner)]
    settings = await saved.get_settings(owner)
    return {
        'enabled': bool(settings.dream_enabled),
        'running': any(r.status == dream.RUNNING and not dream._stale(r) for r in runs),
        'runs': [dream.public_run(r) for r in runs],
        'proposals': proposals,
        'hour': dream.DREAM_HOUR,
    }


@memories_api.post('/dream', status_code=202)
async def dream_now(ctx: AuthContext = Depends(jwt_only)):
    """Tidy now: run dream mode right away."""
    owner = _owner(ctx)
    settings = await saved.get_settings(owner)
    if not settings.enabled:
        raise HTTPException(status_code=400, detail='Memory is turned off. Turn it on to tidy it.')
    row = await dream.start(owner, 'manual')
    return dream.public_run(row)


@memories_api.post('/dream/proposals/{proposal_id}')
async def answer_proposal(proposal_id: str, body: ProposalBody, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    row = await dream.DreamProposal.get(proposal_id)
    if row is None or str(row.user_id) != owner:
        raise HTTPException(status_code=404, detail='Proposal not found')
    try:
        row = await dream.decide(row, body.approve, keep_id=body.keep_id)
    except dream.ProposalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except saved.MemoryError_ as exc:
        raise _bad(exc) from None
    return await dream.public_proposal(row)


@memories_api.post('/dream/{dream_id}/undo')
async def undo_dream(dream_id: str, ctx: AuthContext = Depends(jwt_only)):
    """Undo everything one dream changed on its own."""
    owner = _owner(ctx)
    run = await dream.DreamRun.get(dream_id)
    if run is None or str(run.user_id) != owner:
        raise HTTPException(status_code=404, detail='Dream not found')
    return {'undone': await dream.undo_dream(owner, dream_id)}


async def _notion_state(owner: str) -> dict:
    row = await notion_backup.get_state(owner)
    return notion_backup.public_state(row, await notion_backup.find_connection(owner) is not None)


@memories_api.get('/notion')
async def notion_status(ctx: AuthContext = Depends(jwt_only)):
    """The Notion backup: whether Notion is connected, and how the last backup went."""
    return await _notion_state(_owner(ctx))


@memories_api.put('/notion')
async def notion_settings(body: NotionBackupBody, ctx: AuthContext = Depends(jwt_only)):
    """Turn the backup after every tidy on or off."""
    owner = _owner(ctx)
    await notion_backup.set_enabled(owner, body.enabled)
    return await _notion_state(owner)


@memories_api.post('/notion/backup')
async def notion_backup_now(ctx: AuthContext = Depends(jwt_only)):
    """Back up now: write memories to the Notion page right away (replaces an older backup)."""
    owner = _owner(ctx)
    await notion_backup.backup(owner, trigger='manual')
    return await _notion_state(owner)


@memories_api.post('/notion/restore')
async def notion_restore(ctx: AuthContext = Depends(jwt_only)):
    """Bring memories back from the Notion backup page."""
    owner = _owner(ctx)
    try:
        result = await notion_backup.restore(owner)
    except notion_backup.BackupError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    return {**result, 'state': await _notion_state(owner)}


@memories_api.post('/{memory_id}/approve')
async def approve_memory(memory_id: str, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    row = await saved.get_memory(owner, memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Memory not found')
    return saved.project(await saved.confirm(row, by='person'), await _agent_names(owner))


@memories_api.patch('/{memory_id}')
async def update_memory(memory_id: str, body: MemoryUpdate, ctx: AuthContext = Depends(jwt_only)):
    owner = _owner(ctx)
    row = await saved.get_memory(owner, memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Memory not found')
    agent_id = None
    if body.scope == 'agent':
        agent_id = await _check_agent(owner, body.agent_id or row.agent_id)
    extra = {}
    if body.kind is not None:
        extra['kind'] = body.kind
    if body.valid_until is not None:
        extra['valid_until'] = body.valid_until
    try:
        row = await saved.update(row, content=body.content, pinned=body.pinned,
                                 scope=body.scope, agent_id=agent_id, by='person', **extra)
    except saved.MemoryError_ as exc:
        raise _bad(exc) from None
    return saved.project(row, await _agent_names(owner))


@memories_api.delete('/{memory_id}')
async def delete_memory(memory_id: str, ctx: AuthContext = Depends(jwt_only)):
    row = await saved.get_memory(_owner(ctx), memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Memory not found')
    await saved.delete(row, by='person')
    return {'deleted': True}


@memories_api.post('/delete-all')
async def delete_all(body: DeleteAll, ctx: AuthContext = Depends(jwt_only)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail='Send {"confirm": true} to delete every memory')
    return {'deleted': await saved.delete_all(_owner(ctx))}


# ---------------------------------------------------------------- chat review

@memories_api.get('/review')
async def review_status(ctx: AuthContext = Depends(jwt_only)):
    """The nightly chat review: its switch, recent runs and what it learned."""
    owner = _owner(ctx)
    settings = await saved.get_settings(owner)
    runs = await review.runs_for(owner, 10)
    return {
        'enabled': bool(settings.review_enabled),
        'running': any(r.status == review.RUNNING and not review._stale(r) for r in runs),
        'runs': [review.public_run(r) for r in runs],
        'learned': [review.public_item(r) for r in await review.learned_for(owner, 100)],
        'hour': dream.DREAM_HOUR,
    }


@memories_api.post('/review', status_code=202)
async def review_now(ctx: AuthContext = Depends(jwt_only)):
    """Review chats now: learn from finished chats and tasks right away."""
    row = await review.start(_owner(ctx), 'manual')
    return review.public_run(row)


@memories_api.post('/review/learned/{item_id}/remove')
async def remove_learned(item_id: str, ctx: AuthContext = Depends(jwt_only)):
    """Remove something the review made, or put an improved skill or note back as it was."""
    owner = _owner(ctx)
    row = await review.LearnedItem.get(item_id)
    if row is None or str(row.user_id) != owner:
        raise HTTPException(status_code=404, detail='Not found')
    try:
        row = await review.remove(row)
    except review.RemoveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return review.public_item(row)
