"""The proactive assistant: settings, the morning brief, what's going on, follow-ups.

See ``cognitrix/assistant/``. Browser sessions only.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.assistant import brief, followups, proactivity, signals
from cognitrix.common.security import AuthContext, jwt_only

logger = logging.getLogger('cognitrix.log')

proactive_api = APIRouter(prefix='/assistant', dependencies=[Depends(jwt_only)])


def _me(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


class ProactivityBody(BaseModel):
    level: Literal['off', 'brief', 'balanced', 'eager'] | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    max_pushes: int | None = None
    brief_time: str | None = None
    daily_tokens: int | None = None
    muted: list[str] | None = None


class FeedbackBody(BaseModel):
    index: int
    verdict: Literal['useful', 'not_useful', 'never']


async def _main_view(owner: str) -> dict[str, Any] | None:
    from cognitrix.assistant.setup import main_assistant
    from cognitrix.tasks import standing

    agent = await main_assistant(owner)
    if agent is None:
        return None
    settings = standing.settings_of(agent)
    return {'id': str(agent.id), 'name': agent.name, 'primary': settings['primary'],
            'checkins': bool(settings['enabled']), 'every_minutes': settings['every_minutes']}


@proactive_api.get('/proactivity')
async def get_proactivity(ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.common.clock import client_timezone

    owner = _me(ctx)
    settings = await proactivity.get_settings(owner)
    main = await _main_view(owner)
    return {
        **proactivity.view(settings),
        'browser_timezone': client_timezone.get() or '',
        'week': await proactivity.week(owner),
        'today_tokens': await proactivity.tokens_today(owner),
        'main_assistant': main,
        'needs_setup': main is None or not (main['primary'] and main['checkins']),
        'levels': list(proactivity.LEVELS),
        'limits': {'max_pushes': proactivity.MAX_PUSHES_LIMIT, 'daily_tokens': proactivity.MAX_DAILY_TOKENS},
    }


@proactive_api.put('/proactivity')
async def put_proactivity(body: ProactivityBody, ctx: AuthContext = Depends(jwt_only)):
    from cognitrix.common.clock import client_timezone

    owner = _me(ctx)
    fields = body.model_dump(exclude_none=True)
    tz = client_timezone.get()
    if tz:
        fields['timezone'] = tz
    try:
        await proactivity.save_settings(owner, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return await get_proactivity(ctx)


@proactive_api.post('/proactivity/enable')
async def enable_proactive(ctx: AuthContext = Depends(jwt_only)):
    """Make the person's assistant their main one and turn its check-ins on with a starter brief."""
    from cognitrix.api.routes.tasks import _run_authority, _run_identity
    from cognitrix.assistant.setup import enable_defaults, main_assistant
    from cognitrix.common.clock import client_timezone

    owner = _me(ctx)
    agent = await main_assistant(owner)
    if agent is None:
        raise HTTPException(status_code=400, detail='Create an assistant first.')
    if not await agent_access.can_manage(agent, ctx.user):
        raise HTTPException(status_code=403, detail="Only this agent's owner can turn its check-ins on")
    requested_by, _actor = _run_identity(ctx)
    authority_kind, authority_id = _run_authority(ctx)
    if not await enable_defaults(agent, owner, requested_by=requested_by,
                                 authority_kind=authority_kind, authority_id=authority_id):
        raise HTTPException(status_code=400, detail="This assistant's model can't use tools, so it can't check in.")
    settings = await proactivity.get_settings(owner)
    fields: dict[str, Any] = {}
    if settings.level == 'off':
        fields['level'] = proactivity.DEFAULT_LEVEL
    if client_timezone.get():
        fields['timezone'] = client_timezone.get()
    if fields:
        await proactivity.save_settings(owner, **fields)
    return await get_proactivity(ctx)


@proactive_api.get('/brief')
async def latest_brief(ctx: AuthContext = Depends(jwt_only)):
    row = await brief.latest(_me(ctx))
    return {'brief': brief.public(row) if row else None}


@proactive_api.post('/brief', status_code=202)
async def brief_now(ctx: AuthContext = Depends(jwt_only)):
    """Brief me now: run the morning brief right away."""
    from cognitrix.api.routes.tasks import _enqueue_task_start, _run_authority, _run_identity
    from cognitrix.assistant.setup import main_assistant
    from cognitrix.tasks import standing

    owner = _me(ctx)
    agent = await main_assistant(owner)
    task = await standing.home_task(agent) if agent is not None else None
    if task is None or str(task.owner_id or '') != owner:
        raise HTTPException(status_code=409, detail="Turn your assistant's check-ins on first.")
    requested_by, actor_key = _run_identity(ctx)
    authority_kind, authority_id = _run_authority(ctx)
    run = await _enqueue_task_start(
        task, requested_by=requested_by, actor_key=actor_key, authority_kind=authority_kind,
        authority_id=authority_id, trigger_kind='brief', trigger_note='The person asked for the brief now')
    settings = await proactivity.get_settings(owner)
    await proactivity.save_settings(owner, last_brief_day=brief.local_day(settings))
    return {'task_id': str(task.id), 'run_id': str(run.id), 'status': str(getattr(run.status, 'value', run.status))}


@proactive_api.post('/briefs/{brief_id}/feedback')
async def brief_feedback(brief_id: str, body: FeedbackBody, ctx: AuthContext = Depends(jwt_only)):
    row = await brief.Brief.get(brief_id)
    if row is None or str(row.user_id) != _me(ctx):
        raise HTTPException(status_code=404, detail='Brief not found')
    try:
        row = await brief.give_feedback(row, body.index, body.verdict)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {'brief': brief.public(row)}


@proactive_api.get('/situation')
async def situation(ctx: AuthContext = Depends(jwt_only)):
    """What's going on: next meetings, what waits on you, stuck work, things coming due."""
    sit = await signals.situation(_me(ctx))
    return sit.as_dict()


@proactive_api.get('/followups')
async def list_followups(session_id: str | None = None, ctx: AuthContext = Depends(jwt_only)):
    rows = await followups.pending_for(_me(ctx), session_id)
    return {'followups': [followups.public(r) for r in rows]}


@proactive_api.delete('/followups/{followup_id}')
async def cancel_followup(followup_id: str, ctx: AuthContext = Depends(jwt_only)):
    row = await followups.FollowUp.get(followup_id)
    if row is None or str(row.user_id) != _me(ctx) or row.status != followups.PENDING:
        raise HTTPException(status_code=404, detail='Follow-up not found')
    await followups.cancel(row)
    return {'cancelled': True}
