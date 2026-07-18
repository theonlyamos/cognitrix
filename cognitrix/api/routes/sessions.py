"""Remotely accessible Session routes with durable ownership enforcement."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from cognitrix.common.security import (
    AuthContext,
    crud_scope,
    get_auth_context,
    jwt_only,
)
from cognitrix.session_ownership import (
    LifecycleToken,
    OwnershipConflict,
    OwnershipNotFound,
    OwnershipState,
    SessionOwnership,
    begin_clear,
    begin_delete,
    claim_new,
    discard_fresh_claim,
    finish_clear,
    finish_delete,
    owned_session_ids,
    principal_key,
    require_active_owned,
    require_owned,
    resume_lifecycle,
)
from cognitrix.sessions.access import visible_sessions
from cognitrix.sessions.base import Session
from cognitrix.tasks.run import TaskRun, run_acl_allowed

sessions_api = APIRouter(
    prefix='/sessions',
    dependencies=[Depends(crud_scope)],
)


class SessionCreate(BaseModel):
    """Client-selectable fields for a new ordinary conversation."""

    model_config = ConfigDict(extra='forbid')

    agent_id: str | None = None
    team_id: str | None = None
    # Wire-compatible only; ownership is always server-authored.
    user_id: str | None = None


async def _settle_mutation(operation):
    mutation = asyncio.create_task(operation)
    try:
        return await asyncio.shield(mutation)
    except asyncio.CancelledError as cancelled:
        while not mutation.done():
            try:
                await asyncio.shield(mutation)
            except asyncio.CancelledError:
                continue
        result = mutation.result()
        raise cancelled


async def cleanup_owned_session_resources(
    *,
    session_id: str,
    user_id: str,
    agent_id: str,
    generation: int,
) -> None:
    """Exact-authority cleanup seam supplied by the artifact subsystem.

    Deliberately never falls back to the legacy session-id-only cleanup API.
    The ownership generation has already rotated before this hook is called,
    preventing a stale promotion from adopting storage during destruction.
    """
    from cognitrix import artifacts

    cleanup = getattr(artifacts, 'delete_owned_session_artifacts', None)
    if cleanup is None:
        raise RuntimeError('Exact owned-session artifact cleanup is unavailable')
    await cleanup(
        session_id=session_id,
        user_id=user_id,
        agent_id=agent_id,
        generation=generation,
    )


def _session_title(chat) -> str:
    """Derive a conversation title from the first user text message."""
    for message in chat or []:
        if (
            str(message.get('role', '')).lower() == 'user'
            and message.get('type') == 'text'
            and message.get('content')
        ):
            first_line = str(message['content']).strip().splitlines()[0]
            return first_line[:60] + ('â€¦' if len(first_line) > 60 else '')
    return 'New conversation'


def _session_summary(session: Session) -> dict:
    """Slim projection for list views; transcripts have a dedicated route."""
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
    """Resolve a durable TaskRun through its immutable enqueue-time ACL."""
    run = await TaskRun.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail='Task run not found')
    if not run_acl_allowed(run, ctx):
        raise HTTPException(status_code=403, detail='Not allowed to access this task run')
    return run


async def _visible_remote_sessions(sessions: list[Session], ctx: AuthContext) -> list[Session]:
    """Combine exact ordinary ownership with immutable TaskRun visibility."""
    owned = await _owned_sessions(ctx)
    requested_ids = {str(session.id) for session in sessions}
    visible = [session for session in owned if str(session.id) in requested_ids]
    visible_ids = {str(session.id) for session in visible}
    user_id = principal_key(ctx.user)
    run_candidates: list[Session] = []
    for session in sessions:
        if str(session.id) in visible_ids:
            continue
        # A row with any ordinary-session binding can never fall through to
        # TaskRun ACL authorization for a different principal.
        binding = await SessionOwnership.find_one(
            {'session_id': str(session.id)},
        )
        if binding is not None or not session.run_id:
            continue
        run_candidates.append(session)
    if run_candidates:
        visible.extend(await visible_sessions(run_candidates, ctx))
    return visible


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail='Session not found')


def _raise_ownership_error(error: Exception) -> None:
    if isinstance(error, OwnershipNotFound):
        raise _not_found() from None
    if isinstance(error, OwnershipConflict):
        raise HTTPException(status_code=409, detail=str(error)) from None
    raise error


async def _binding_for_context(
    session_id: str,
    ctx: AuthContext,
    *,
    expected_agent_id: str | None = None,
    active: bool = True,
):
    user_id = principal_key(ctx.user)
    try:
        require_binding = require_active_owned if active else require_owned
        binding = await require_binding(
            session_id,
            user_id,
            expected_agent_id,
        )
    except (OwnershipNotFound, OwnershipConflict) as error:
        _raise_ownership_error(error)
    if not ctx.agent_allowed(binding.agent_id):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this session agent',
        )
    return binding


async def _load_authorized_session(
    session_id: str,
    ctx: AuthContext,
    *,
    expected_agent_id: str | None = None,
) -> tuple[Any, Session]:
    try:
        binding = await require_active_owned(
            session_id,
            principal_key(ctx.user),
            expected_agent_id,
        )
    except OwnershipConflict as error:
        _raise_ownership_error(error)
    except OwnershipNotFound:
        # Only an entirely unbound row may use TaskRun authority. A foreign
        # durable binding must never fall through to the immutable run ACL.
        existing_binding = await SessionOwnership.find_one(
            {'session_id': session_id},
        )
        if existing_binding is not None:
            raise _not_found()
        session = await Session.find_one({'id': session_id})
        if session is None or not session.run_id:
            raise _not_found()
        await _authorized_run(str(session.run_id), ctx)
        agent_id = str(session.agent_id or '')
        if expected_agent_id is not None and agent_id != str(expected_agent_id):
            raise _not_found()
        return SimpleNamespace(
            session_id=str(session.id),
            user_id=principal_key(ctx.user),
            agent_id=agent_id,
        ), session
    if not ctx.agent_allowed(binding.agent_id):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this session agent',
        )
    session = await Session.get(session_id)
    if session is None or str(session.agent_id or '') != binding.agent_id:
        raise _not_found()
    return binding, session


async def _owned_sessions(
    ctx: AuthContext,
    *,
    agent_id: str | None = None,
) -> list[Session]:
    if agent_id is not None and not ctx.agent_allowed(agent_id):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this agent',
        )
    user_id = principal_key(ctx.user)
    session_ids = await owned_session_ids(user_id, agent_id=agent_id)
    sessions: list[Session] = []
    for session_id in session_ids:
        # Re-check the live binding before each Session row load.  This avoids
        # a list/delete race and filters API-key allowlists before data access.
        try:
            binding = await require_active_owned(session_id, user_id, agent_id)
        except (OwnershipNotFound, OwnershipConflict):
            continue
        if not ctx.agent_allowed(binding.agent_id):
            continue
        session = await Session.get(session_id)
        if session is not None and str(session.agent_id or '') == binding.agent_id:
            sessions.append(session)
    return sessions


@sessions_api.get('')
async def get_all_sessions(ctx: AuthContext = Depends(get_auth_context)):
    sessions = await _visible_remote_sessions(list(await Session.all()), ctx)
    return JSONResponse([session.json() for session in sessions])


@sessions_api.post('')
async def new_session(
    request: Request,
    body: SessionCreate,
    ctx: AuthContext = Depends(get_auth_context),
):
    agent_id = str(
        body.agent_id
        or getattr(getattr(request.state, 'agent', None), 'id', '')
        or ''
    ).strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail='Session agent is required')
    if not ctx.agent_allowed(agent_id):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this agent',
        )
    if body.team_id and not ctx.team_allowed(str(body.team_id)):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this team',
        )

    # Construct persistence server-side so clients cannot inject ids, chat,
    # or TaskRun execution metadata into an ordinary remote session.
    user_id = principal_key(ctx.user)
    session = Session(agent_id=agent_id, team_id=body.team_id, user_id=user_id)
    session_id: str | None = None
    try:
        await _settle_mutation(session.save())
        session_id = str(session.id)
        await claim_new(session_id, user_id, agent_id)
    except BaseException as error:
        session_id = session_id or (
            str(session.id) if session.id is not None else None
        )
        if session_id is not None:
            async def compensate() -> None:
                # Delete the Session first; an exact fresh binding, if
                # claim_new committed before cancellation, is removed last.
                await Session.delete_many({'id': session_id})
                await discard_fresh_claim(session_id, user_id, agent_id)

            cleanup = asyncio.create_task(compensate())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
        if isinstance(error, OwnershipConflict):
            _raise_ownership_error(error)
        raise
    return session.json()


@sessions_api.get('/{session_id}')
async def get_session(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    _, session = await _load_authorized_session(session_id, ctx)
    return session.json()


async def _begin_owned_lifecycle(
    session_id: str,
    ctx: AuthContext,
    operation: str,
) -> tuple[LifecycleToken, Session | None, bool]:
    binding = await _binding_for_context(session_id, ctx, active=False)
    desired_state = (
        OwnershipState.CLEARING if operation == 'clear'
        else OwnershipState.DELETING
    )
    try:
        if binding.state == OwnershipState.ACTIVE:
            session = await Session.get(session_id)
            if session is None or str(session.agent_id or '') != binding.agent_id:
                raise _not_found()
            if operation == 'clear':
                token = await begin_clear(
                    session_id,
                    binding.user_id,
                    binding.agent_id,
                )
            else:
                token = await begin_delete(
                    session_id,
                    binding.user_id,
                    binding.agent_id,
                )
            resumed = False
        elif binding.state == desired_state:
            token = await resume_lifecycle(
                session_id,
                binding.user_id,
                binding.agent_id,
                desired_state,
            )
            session = await Session.get(session_id)
            if operation == 'clear' and (
                session is None or str(session.agent_id or '') != binding.agent_id
            ):
                raise OwnershipConflict('Clearing session row is unavailable')
            resumed = True
        else:
            raise OwnershipConflict('Session is in a different lifecycle state')
    except (OwnershipNotFound, OwnershipConflict) as error:
        _raise_ownership_error(error)
    return token, session, resumed


async def _cleanup_for_token(token: LifecycleToken) -> None:
    await cleanup_owned_session_resources(
        session_id=token.session_id,
        user_id=token.user_id,
        agent_id=token.agent_id,
        generation=token.generation,
    )


@sessions_api.delete('/{session_id}')
async def delete_session(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    token, _, resumed = await _begin_owned_lifecycle(session_id, ctx, 'delete')
    try:
        await _cleanup_for_token(token)
        deleted = await Session.delete_many({'id': session_id})
        if deleted != 1 and not (resumed and deleted == 0):
            raise OwnershipConflict('Session changed concurrently')
        # The binding is the last row deleted, after exact resource cleanup and
        # Session deletion both succeeded.
        await finish_delete(token)
    except Exception as error:
        if isinstance(error, (OwnershipNotFound, OwnershipConflict)):
            _raise_ownership_error(error)
        # Once the durable lifecycle state is visible, cleanup may have made
        # partial progress. Keep DELETING for exact idempotent recovery.
        raise
    return JSONResponse({'message': 'Session deleted successfully'})


# Browser-session plumbing rejects API keys, but still consumes the same cached
# AuthContext so authorization is resolved once per request.
@sessions_api.get('/{session_id}/events', dependencies=[Depends(jwt_only)])
async def sse_endpoint(
    request: Request,
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    await _load_authorized_session(session_id, ctx)
    return await request.state.sse_manager.sse_endpoint(request)


@sessions_api.post('/{session_id}/chat', dependencies=[Depends(jwt_only)])
async def chat_endpoint(
    request: Request,
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    binding, _ = await _load_authorized_session(session_id, ctx)
    data = await request.json()
    try:
        message = json.loads(data['message'])
    except (KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail='Invalid chat action') from None
    if not isinstance(message, dict):
        raise HTTPException(status_code=400, detail='Invalid chat action')
    supplied_session = message.get('session_id')
    supplied_agent = message.get('agent_id')
    if (
        supplied_session is not None
        and str(supplied_session) != binding.session_id
    ) or (
        supplied_agent is not None
        and str(supplied_agent) != binding.agent_id
    ):
        # Do not reveal whether the spoofed target exists.
        raise _not_found()
    message['session_id'] = binding.session_id
    message['agent_id'] = binding.agent_id
    await request.state.sse_manager.action_queue.put(message)
    return {'status': 'Message sent'}


@sessions_api.get('/{session_id}/chat')
async def get_chat(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    _, session = await _load_authorized_session(session_id, ctx)
    return JSONResponse(session.chat)


@sessions_api.delete('/{session_id}/chat')
async def delete_chat(
    session_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    token, session, _ = await _begin_owned_lifecycle(session_id, ctx, 'clear')
    assert session is not None
    try:
        session.chat = []
        await session.save()
        await _cleanup_for_token(token)
        await finish_clear(token)
    except Exception as error:
        if isinstance(error, (OwnershipNotFound, OwnershipConflict)):
            _raise_ownership_error(error)
        # Do not reactivate after cleanup might have partially succeeded.
        # A retry resumes this exact CLEARING token.
        raise
    return JSONResponse({'message': 'Chat deleted successfully'})


# List endpoints are summaries.  Each begins from owned binding ids and only
# then loads the matching Session rows; no global Session.find/all is allowed.
@sessions_api.get('/agents/{agent_id}')
async def sessions_by_agent(
    agent_id: str,
    exclude_tasks: bool = False,
    ctx: AuthContext = Depends(get_auth_context),
):
    if not ctx.agent_allowed(agent_id):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this agent',
        )
    sessions = await _visible_remote_sessions(
        list(await Session.find({'agent_id': agent_id})), ctx,
    )
    if exclude_tasks:
        sessions = [session for session in sessions if not session.task_id]
    return [_session_summary(session) for session in sessions]


@sessions_api.get('/teams/{team_id}')
async def sessions_by_team(
    team_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    if not ctx.team_allowed(team_id):
        raise HTTPException(
            status_code=403,
            detail='API key not allowed for this team',
        )
    sessions = await _visible_remote_sessions(
        list(await Session.find({'team_id': team_id})), ctx,
    )
    return [_session_summary(session) for session in sessions]


@sessions_api.get('/tasks/{task_id}')
async def sessions_by_task(
    task_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    sessions = await _visible_remote_sessions(
        list(await Session.find({'task_id': task_id})), ctx,
    )
    return [_session_summary(session) for session in sessions]


@sessions_api.get('/runs/{run_id}')
async def sessions_by_run(
    run_id: str,
    ctx: AuthContext = Depends(get_auth_context),
):
    # Authorize the immutable run before touching its session mapping.
    await _authorized_run(run_id, ctx)
    rows = list(await Session.find({'run_id': run_id}))
    if not rows:
        return []
    sessions = await _visible_remote_sessions(rows, ctx)
    return [_session_summary(session) for session in sessions]
