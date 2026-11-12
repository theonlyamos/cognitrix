"""Teammate features: standing assistants and their check-ins, offers of
help, skill drafts waiting for review, and "stop everything" for an agent.

See ``cognitrix/tasks/standing.py`` and ``cognitrix/skills/drafts.py``.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.sessions import runs as chat_runs
from cognitrix.skills import drafts
from cognitrix.tasks import standing

logger = logging.getLogger('cognitrix.log')

assistant_api = APIRouter(dependencies=[Depends(jwt_only)])


def _me(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


async def _agent(agent_id: str, ctx: AuthContext, *, edit: bool = False):
    agent = await agent_access.find_visible_agent(_me(ctx), agent_id=agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found')
    if edit and not await agent_access.can_manage(agent, ctx.user):
        # Check-ins run as the agent's owner, so only the owner sets them up.
        raise HTTPException(
            status_code=403,
            detail="Only this agent's owner can change its check-ins" if agent.owner_id
            else 'Only an admin can change a shared agent')
    return agent


# --- standing assistant ---------------------------------------------------------

async def _standing_view(agent: Any, ctx: AuthContext) -> dict[str, Any]:
    from cognitrix.tasks.repository import RunRepository

    settings = standing.settings_of(agent)
    task = await standing.home_task(agent)
    # A home task belongs to the person who turned check-ins on.
    if task is not None and str(task.owner_id or '') != _me(ctx):
        task = None
    last_run = None
    if task is not None:
        latest = await RunRepository().latest_run(task.id)
        if latest is not None:
            last_run = {
                'id': latest.id,
                'status': str(getattr(latest.status, 'value', latest.status)),
                'completed_at': latest.completed_at,
                'result': (latest.result or '')[:600],
            }
    offers = [
        standing.public_offer(row) for row in await standing.offers_for(_me(ctx), str(agent.id))
        if row.status == standing.OFFER_PENDING
    ]
    return {
        'enabled': bool(settings['enabled'] and task is not None and task.schedule_enabled),
        'brief': settings['brief'],
        'every_minutes': settings['every_minutes'],
        'offers': settings['offers'],
        'primary': settings['primary'],
        'task_id': str(task.id) if task is not None else None,
        'notes': (task.notes or '') if task is not None else '',
        'next_check_at': (
            min(filter(None, [task.next_run_at, task.wake_at if task.wake_pending else None]), default=None)
            if task is not None and task.schedule_enabled else None
        ),
        'wake_reason': task.wake_reason if task is not None and task.wake_pending else None,
        'last_run': last_run,
        'pending_offers': offers,
        'limits': {
            'min_every_minutes': standing.MIN_EVERY_MINUTES,
            'max_every_minutes': standing.MAX_EVERY_MINUTES,
            'brief_max_chars': standing.BRIEF_MAX_CHARS,
        },
    }


@assistant_api.get('/agents/{agent_id}/standing')
async def get_standing(agent_id: str, ctx: AuthContext = Depends(get_auth_context)):
    agent = await _agent(agent_id, ctx)
    # A brief and its notes are the owner's; sharing the agent doesn't show them.
    return await _standing_view(await _agent(agent_id, ctx, edit=True) if agent.owner_id else agent, ctx)


class StandingBody(BaseModel):
    enabled: bool | None = None
    brief: dict[str, str] | None = None
    every_minutes: int | None = None
    offers: bool | None = None
    primary: bool | None = None
    notes: str | None = None


@assistant_api.put('/agents/{agent_id}/standing')
async def save_standing(agent_id: str, body: StandingBody, ctx: AuthContext = Depends(get_auth_context)):
    from cognitrix.api.routes.tasks import _run_authority, _run_identity
    from cognitrix.tasks.automation import NOTES_MAX_CHARS
    from cognitrix.tasks.base import Task

    agent = await _agent(agent_id, ctx, edit=True)
    existing = await standing.home_task(agent)
    if existing is not None and str(existing.owner_id or '') != _me(ctx):
        raise HTTPException(status_code=409, detail='Someone else already runs check-ins for this agent')
    if not getattr(agent.llm, 'supports_tool_use', True) and body.enabled:
        raise HTTPException(status_code=400, detail="This agent's model can't use tools, so it can't check in")
    try:
        settings = standing.clean_settings(body.model_dump(exclude_none=True), standing.settings_of(agent))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if body.notes is not None and len(body.notes) > NOTES_MAX_CHARS:
        raise HTTPException(status_code=400, detail=f'Notes hold at most {NOTES_MAX_CHARS} characters')

    requested_by, _actor = _run_identity(ctx)
    authority_kind, authority_id = _run_authority(ctx)
    task = await standing.sync_home_task(
        agent, settings, owner_key=_me(ctx), requested_by=requested_by,
        authority_kind=authority_kind, authority_id=authority_id,
    )
    if task is not None and body.notes is not None:
        await Task.update_one({'id': task.id}, {'notes': body.notes.strip() or None})
    if settings['primary']:
        await standing.clear_other_primaries(agent, _me(ctx))
    await standing.save_settings(agent, settings)
    return await _standing_view(agent, ctx)


@assistant_api.post('/agents/{agent_id}/standing/check', status_code=202)
async def check_now(agent_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Run a check-in right away."""
    from cognitrix.api.routes.tasks import _enqueue_task_start, _run_authority, _run_identity

    agent = await _agent(agent_id, ctx, edit=True)
    task = await standing.home_task(agent)
    if task is None or str(task.owner_id or '') != _me(ctx):
        raise HTTPException(status_code=409, detail='Write a brief and turn check-ins on first')
    requested_by, actor_key = _run_identity(ctx)
    authority_kind, authority_id = _run_authority(ctx)
    run = await _enqueue_task_start(
        task, requested_by=requested_by, actor_key=actor_key,
        authority_kind=authority_kind, authority_id=authority_id,
        trigger_kind='manual', trigger_note='The person asked for a check-in now',
    )
    return {'task_id': task.id, 'run_id': run.id, 'status': run.status}


# --- stop everything ------------------------------------------------------------

@assistant_api.post('/agents/{agent_id}/stop-all')
async def stop_all(agent_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Stop what this agent is doing for me: its running chats (and chats
    those started), its active task runs, and a pending self-scheduled check."""
    from cognitrix.agents.access import can_view
    from cognitrix.api.routes.tasks import _active_run, _run_visible, cancel_active_run
    from cognitrix.sessions.base import Session
    from cognitrix.tasks.automation import WAKE_FIELDS
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import run_acl_allowed

    agent = await _agent(agent_id, ctx)
    agent_id = str(agent.id)
    me = _me(ctx)

    stopped_chats = 0
    own = {run.session_id for run in chat_runs.active_runs(me, agent_id)}
    for run in chat_runs.active_runs(me):
        started_here = False
        if run.session_id not in own and run.started_by_agent:
            # A chat another agent runs, started from one of this agent's chats.
            try:
                session = await Session.get(run.session_id)
                started = getattr(session, 'started_by', None) or {}
                started_here = str(started.get('agent_id') or '') == agent_id
            except Exception:
                started_here = False
        if (run.session_id in own or started_here) and chat_runs.stop_run(me, run.session_id):
            stopped_chats += 1

    stopped_runs = cancelled_checks = 0
    for task in await Task.all() or []:
        if task.deleted_at or agent_id not in (task.assigned_agents or []) or not can_view(task, me):
            continue
        try:
            if not await agent_access.can_edit(task, ctx.user):
                continue
            run = await _active_run(task.id)
            if run is not None and run_acl_allowed(run, ctx) and await _run_visible(task, run, ctx):
                await cancel_active_run(task, run)
                stopped_runs += 1
            if task.wake_pending:
                cleared: dict[str, Any] = {field: None for field in WAKE_FIELDS}
                cleared['wake_pending'] = False
                await Task.update_one({'id': task.id}, cleared)
                cancelled_checks += 1
        except Exception:
            logger.exception('Stop all: could not stop task %s', task.id)
    return {'chats': stopped_chats, 'task_runs': stopped_runs, 'checks': cancelled_checks}


# --- offers ---------------------------------------------------------------------

@assistant_api.get('/offers')
async def list_offers(ctx: AuthContext = Depends(get_auth_context)):
    rows = [row for row in await standing.offers_for(_me(ctx)) if row.status == standing.OFFER_PENDING]
    rows.sort(key=lambda row: str(row.json().get('created_at') or ''), reverse=True)
    return [standing.public_offer(row) for row in rows]


async def _offer(offer_id: str, ctx: AuthContext) -> standing.AgentOffer:
    row = await standing.AgentOffer.get(offer_id)
    if row is None or str(row.user_id) != _me(ctx):
        raise HTTPException(status_code=404, detail='Offer not found')
    return row


@assistant_api.post('/offers/{offer_id}/accept')
async def accept_offer(offer_id: str, ctx: AuthContext = Depends(get_auth_context)):
    """Start a chat in which the agent does what it offered."""
    from cognitrix.sessions import agent_chats

    row = await _offer(offer_id, ctx)
    me = _me(ctx)
    agent = await agent_access.find_visible_agent(me, agent_id=row.agent_id)
    if agent is None:
        raise HTTPException(status_code=409, detail='The agent that made this offer no longer exists')
    if not await standing.decide_offer(row, standing.OFFER_ACCEPTED):
        raise HTTPException(status_code=409, detail='This offer was already answered')
    try:
        session = await agent_chats.create_chat(me, agent, {
            'agent_id': str(agent.id), 'agent_name': str(agent.name or ''), 'session_id': None,
            'depth': 0, 'report_back': False, 'exchanges': 1, 'offer_id': str(row.id),
        })
        agent_chats.send_turn(me, agent, session, standing.accept_prompt(row, agent))
    except Exception:
        await standing.AgentOffer.update_one(
            {'id': row.id}, {'status': standing.OFFER_PENDING, 'decided_at': None})
        logger.exception('Could not start the chat for offer %s', row.id)
        raise HTTPException(status_code=500, detail="Couldn't start the chat. Try again.") from None
    await standing.AgentOffer.update_one({'id': row.id}, {'session_id': str(session.id)})
    return {'status': standing.OFFER_ACCEPTED, 'session_id': str(session.id), 'agent_id': str(agent.id)}


@assistant_api.post('/offers/{offer_id}/dismiss')
async def dismiss_offer(offer_id: str, ctx: AuthContext = Depends(get_auth_context)):
    row = await _offer(offer_id, ctx)
    if not await standing.decide_offer(row, standing.OFFER_DISMISSED):
        raise HTTPException(status_code=409, detail='This offer was already answered')
    return {'status': standing.OFFER_DISMISSED}


# --- skill drafts ---------------------------------------------------------------

async def _draft_view(row: drafts.SkillDraft, ctx: AuthContext, admin: bool) -> dict[str, Any]:
    data = drafts.public_draft(row)
    data['mine'] = str(row.user_id) == _me(ctx)
    data['can_approve'] = True
    # Saving for everyone writes a shared skill, which only an admin may do.
    data['can_share'] = admin
    return data


@assistant_api.get('/skill-drafts')
async def list_drafts(ctx: AuthContext = Depends(get_auth_context)):
    """My skill drafts waiting for review."""
    admin = await agent_access.is_admin(ctx.user)
    rows = await drafts.drafts_of(_me(ctx))
    rows.sort(key=lambda row: str(row.json().get('created_at') or ''), reverse=True)
    return [await _draft_view(row, ctx, admin) for row in rows]


class DraftFromChat(BaseModel):
    session_id: str


@assistant_api.post('/skill-drafts')
async def draft_from_chat(body: DraftFromChat, ctx: AuthContext = Depends(get_auth_context)):
    """Write a skill draft from a finished chat, using that chat's agent."""
    from cognitrix.api.routes.sessions import _load_authorized_session

    _binding, session = await _load_authorized_session(body.session_id, ctx)
    if getattr(session, 'incognito', False):
        raise HTTPException(status_code=400, detail="Incognito chats aren't used to learn skills")
    agent = await agent_access.find_visible_agent(_me(ctx), agent_id=str(session.agent_id or ''))
    if agent is None or agent.llm is None:
        raise HTTPException(status_code=409, detail="This chat's agent no longer exists")
    try:
        name, description, text = await drafts.draft_from_chat(agent.llm, session.chat)
        from cognitrix.skills import personal

        if await personal.name_taken(_me(ctx), name):
            name = drafts.slug(f'{name}-{str(session.id)[:4]}')
        row = await drafts.create_draft(
            _me(ctx), name, description, text,
            agent_id=str(agent.id), agent_name=str(agent.name or ''), session_id=str(session.id),
        )
    except drafts.DraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception('Could not draft a skill from chat %s', session.id)
        raise HTTPException(status_code=502, detail="The model couldn't write the skill. Try again.") from None
    return await _draft_view(row, ctx, await agent_access.is_admin(ctx.user))


async def _draft(draft_id: str, ctx: AuthContext) -> tuple[drafts.SkillDraft, bool]:
    row = await drafts.SkillDraft.get(draft_id)
    admin = await agent_access.is_admin(ctx.user)
    if row is None or str(row.user_id) != _me(ctx):
        raise HTTPException(status_code=404, detail='Draft not found')
    if row.status != drafts.PENDING:
        raise HTTPException(status_code=409, detail='This draft was already reviewed')
    return row, admin


class DraftEdit(BaseModel):
    name: str | None = None
    description: str | None = None
    body: str | None = None


@assistant_api.patch('/skill-drafts/{draft_id}')
async def edit_draft(draft_id: str, body: DraftEdit, ctx: AuthContext = Depends(get_auth_context)):
    row, admin = await _draft(draft_id, ctx)
    try:
        name, description, text = drafts.clean(
            body.name if body.name is not None else row.name,
            body.description if body.description is not None else row.description,
            body.body if body.body is not None else row.body,
        )
    except drafts.DraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await drafts.SkillDraft.update_one(
        {'id': row.id, 'status': drafts.PENDING},
        {'name': name, 'description': description, 'body': text})
    row.name, row.description, row.body = name, description, text
    return await _draft_view(row, ctx, admin)


class DraftApproval(BaseModel):
    everyone: bool = False
    """Save as a shared skill every agent on the server can load (admins only)."""


@assistant_api.post('/skill-drafts/{draft_id}/approve')
async def approve_draft(draft_id: str, body: DraftApproval | None = None,
                        ctx: AuthContext = Depends(get_auth_context)):
    row, admin = await _draft(draft_id, ctx)
    everyone = bool(body and body.everyone)
    if everyone and not admin:
        raise HTTPException(status_code=403, detail='Only an admin can save a skill for everyone.')
    try:
        saved = await drafts.approve(row, _me(ctx), everyone=everyone)
    except drafts.DraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception as exc:
        logger.exception('Could not save skill %s', row.name)
        raise HTTPException(status_code=422, detail=f"Couldn't save the skill: {exc}") from None
    return {'status': drafts.APPROVED, 'name': saved.name, 'scope': 'everyone' if everyone else 'mine'}


@assistant_api.post('/skill-drafts/{draft_id}/reject')
async def reject_draft(draft_id: str, ctx: AuthContext = Depends(get_auth_context)):
    row, _admin = await _draft(draft_id, ctx)
    if not await drafts.reject(row, _me(ctx)):
        raise HTTPException(status_code=409, detail='This draft was already reviewed')
    return {'status': drafts.REJECTED}
