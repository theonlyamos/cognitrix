"""Remotely accessible Session routes with durable ownership enforcement."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

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

logger = logging.getLogger('cognitrix.log')

sessions_api = APIRouter(
    prefix='/sessions',
    dependencies=[Depends(crud_scope)],
)


class SessionUpdate(BaseModel):
    """What a person can change about a chat from the chat list."""

    model_config = ConfigDict(extra='forbid')

    # '' or None clears a custom name; the first message is shown again.
    title: str | None = Field(default=None, max_length=120)
    pinned: bool | None = None
    archived: bool | None = None


class SessionRewind(BaseModel):
    model_config = ConfigDict(extra='forbid')

    # 0-based position among the person's own text messages in this chat.
    turn: int = Field(ge=0)
    # What the page shows for that message; a mismatch means the page is out
    # of step with the saved chat and nothing is removed.
    expected: str | None = Field(default=None, max_length=200_000)


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
            return first_line[:60] + ('…' if len(first_line) > 60 else '')
    return 'New conversation'


def _session_summary(session: Session) -> dict:
    """Slim projection for list views; transcripts have a dedicated route."""
    data = session.json()
    return {
        'id': session.id,
        'title': (getattr(session, 'title', None) or '').strip() or _session_title(session.chat),
        'renamed': bool((getattr(session, 'title', None) or '').strip()),
        'pinned': bool(getattr(session, 'pinned', False)),
        'archived': bool(getattr(session, 'archived', False)),
        'datetime': session.datetime,
        'updated_at': data.get('updated_at'),
        'started_at': session.started_at,
        'completed_at': session.completed_at,
        'task_id': session.task_id,
        'run_id': session.run_id,
        'step_index': session.step_index,
        'step_title': session.step_title,
        'message_count': len(session.chat or []),
        'started_by': _started_by(session),
    }


def _started_by(session: Session) -> dict | None:
    """Which agent started this chat, if one did (see sessions/agent_chats.py)."""
    started_by = getattr(session, 'started_by', None)
    if not isinstance(started_by, dict) or not started_by.get('agent_id'):
        return None
    return {
        'agent_id': str(started_by.get('agent_id')),
        'agent_name': str(started_by.get('agent_name') or ''),
        'session_id': started_by.get('session_id'),
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
    return JSONResponse([session.json() for session in sessions if not getattr(session, 'incognito', False)])


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


# Declared before '/{session_id}' so 'chats' is not read as a session id.
@sessions_api.get('/chats')
async def all_chats(ctx: AuthContext = Depends(get_auth_context)):
    """Every chat the caller owns, newest first: one-to-one chats with any
    agent plus team group chats. Task-run sessions are left out."""
    from cognitrix.agents.access import can_view
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.teams import group_chat
    from cognitrix.teams.base import Team

    user_id = principal_key(ctx.user)
    run_key = str(getattr(ctx.user, 'id', None) or getattr(ctx.user, 'email', 'anon'))

    def running(session_id: str) -> bool:
        run = chat_runs.get_run(run_key, session_id)
        return bool(run and run.running)

    # Read from the chat index (one small row per chat), never from the
    # sessions themselves: a session row carries its whole message history.
    from cognitrix.session_ownership import OwnershipState
    from cognitrix.sessions import index as chat_index

    bindings = {
        binding.session_id: binding
        for binding in await SessionOwnership.find({'user_id': user_id})
        if binding.state == OwnershipState.ACTIVE
    }
    rows = await chat_index.rows_for(user_id, sorted(bindings))
    await _sweep_incognito(ctx, rows)
    chats: list[dict[str, Any]] = []
    teams: dict[str, Any] = {}
    for row in rows:
        if row.incognito:
            continue
        if row.kind == chat_index.KIND_TEAM:
            # Group chats are browser-only (their routes are JWT-only).
            if ctx.api_key is not None or not row.team_id:
                continue
            if row.team_id not in teams:
                team = await Team.get(row.team_id)
                teams[row.team_id] = team if team is not None and can_view(team, user_id) else None
            if teams[row.team_id] is None:
                continue
            chats.append({
                'id': row.session_id,
                'title': row.title,
                'updated_at': row.changed,
                'message_count': row.message_count,
                'kind': 'team',
                'agent_id': None,
                'team_id': row.team_id,
                'running': running(row.session_id),
            })
            continue
        # Access comes from the live binding, not from the index row.
        binding = bindings.get(row.session_id)
        if binding is None or not ctx.agent_allowed(binding.agent_id):
            continue
        chats.append({
            'id': row.session_id,
            'title': row.custom_title or row.title,
            'renamed': bool(row.custom_title),
            'pinned': row.pinned,
            'archived': row.archived,
            'datetime': row.started,
            'updated_at': row.changed,
            'started_at': None,
            'completed_at': None,
            'task_id': None,
            'run_id': None,
            'step_index': None,
            'step_title': None,
            'message_count': row.message_count,
            'started_by': row.started_by,
            'kind': 'agent',
            'agent_id': row.agent_id or str(binding.agent_id or ''),
            'team_id': None,
            'running': running(row.session_id),
        })

    chats.sort(key=lambda c: str(c.get('updated_at') or ''), reverse=True)
    return chats


SEARCH_MAX_RESULTS = 20


@sessions_api.get('/search')
async def search_chats(q: str = '', ctx: AuthContext = Depends(get_auth_context)):
    """Find the caller's own chats by words in the title or the messages."""
    from cognitrix.tools.chat_history import _snippet, _text_messages, _words

    words = _words(q)
    if not words:
        return []
    hits: list[tuple[int, str, dict[str, Any]]] = []
    for session in await _owned_sessions(ctx):
        if session.task_id or session.run_id or session.group_chat or getattr(session, 'incognito', False):
            continue
        summary = _session_summary(session)
        title_hits = len(words & _words(summary['title']))
        best: tuple[int, str] = (0, '')
        for message in _text_messages(session):
            found = len(words & _words(message['content']))
            if found > best[0]:
                best = (found, _snippet(message['content'], words))
        if not title_hits and not best[0]:
            continue
        hits.append((title_hits * 2 + best[0], str(summary.get('updated_at') or ''), {
            'id': summary['id'],
            'title': summary['title'],
            'agent_id': str(session.agent_id or ''),
            'updated_at': summary.get('updated_at'),
            'archived': summary['archived'],
            'snippet': best[1],
        }))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return [h[2] for h in hits[:SEARCH_MAX_RESULTS]]


def _chat_is_running(ctx: AuthContext, session_id: str) -> bool:
    from cognitrix.sessions import runs as chat_runs

    run_key = str(getattr(ctx.user, 'id', None) or getattr(ctx.user, 'email', 'anon'))
    run = chat_runs.get_run(run_key, session_id)
    return bool(run and run.running)


@sessions_api.patch('/{session_id}')
async def update_session(
    session_id: str,
    body: SessionUpdate,
    ctx: AuthContext = Depends(get_auth_context),
):
    """Rename, pin or archive one of the caller's chats."""
    _, session = await _load_authorized_session(session_id, ctx)
    if session.task_id or session.run_id:
        raise _not_found()
    # A running turn saves its own copy of the chat when it ends and would
    # undo this change.
    if _chat_is_running(ctx, session_id):
        raise HTTPException(status_code=409, detail='This chat is still working. Try again when it finishes.')
    changes = body.model_dump(exclude_unset=True)
    if 'title' in changes:
        session.title = (changes['title'] or '').strip() or None
    if changes.get('pinned') is not None:
        session.pinned = bool(changes['pinned'])
    if changes.get('archived') is not None:
        session.archived = bool(changes['archived'])
    await session.save()
    return _session_summary(session)


@sessions_api.post('/{session_id}/rewind', dependencies=[Depends(jwt_only)])
async def rewind_session(
    session_id: str,
    body: SessionRewind,
    ctx: AuthContext = Depends(get_auth_context),
):
    """Drop one of the person's messages and everything after it, so it can
    be sent again (regenerate) or reworded (edit). Returns the dropped text."""
    _, session = await _load_authorized_session(session_id, ctx)
    if session.task_id or session.run_id:
        raise _not_found()
    if _chat_is_running(ctx, session_id):
        raise HTTPException(status_code=409, detail='This chat is still working. Stop it first.')
    from cognitrix.media.documents import DOCUMENT_HISTORY_MARKER

    chat = list(session.chat or [])

    def is_user(message) -> bool:
        return str(message.get('role', '')).lower() == 'user'

    def is_upload_note(message) -> bool:
        # Bookkeeping the app adds after a turn with uploads; not a message
        # the person typed, so it is not a turn of its own.
        return (
            is_user(message)
            and (message.get('type') != 'text'
                 or str(message.get('content') or '').startswith(DOCUMENT_HISTORY_MARKER))
        )

    seen = -1
    for index, message in enumerate(chat):
        if (
            is_user(message)
            and message.get('type') == 'text'
            and str(message.get('content') or '').strip()
            and not is_upload_note(message)
        ):
            seen += 1
            if seen == body.turn:
                text = str(message['content'])
                if body.expected is not None and not body.expected.strip().startswith(text.strip()):
                    break
                # Dropping the turn would drop its uploads, and the resend
                # would not carry them.
                if index + 1 < len(chat) and is_upload_note(chat[index + 1]):
                    raise HTTPException(
                        status_code=409,
                        detail='This message has attachments, so it can\'t be edited or regenerated.',
                    )
                session.chat = chat[:index]
                await session.save()
                return {'message': text, 'message_count': len(session.chat)}
    raise HTTPException(status_code=404, detail='That message is no longer in this chat.')


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
    await _delete_owned(session_id, ctx)
    return JSONResponse({'message': 'Session deleted successfully'})


INCOGNITO_MAX_AGE = timedelta(hours=24)


async def _sweep_incognito(ctx: AuthContext, rows) -> None:
    """Delete this person's incognito chats left for more than a day (the
    browser normally deletes one as soon as the person leaves it). ``rows``
    are their chat-index rows."""
    cutoff = datetime.now() - INCOGNITO_MAX_AGE
    for row in rows:
        if not row.incognito:
            continue
        try:
            updated = datetime.fromisoformat(row.changed) if row.changed else None
        except ValueError:
            updated = None
        if updated is not None and updated.replace(tzinfo=None) > cutoff:
            continue
        try:
            await _delete_owned(row.session_id, ctx)
        except Exception:
            logger.debug('Could not delete old incognito chat %s', row.session_id, exc_info=True)


async def _delete_owned(session_id: str, ctx: AuthContext) -> None:
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
    return [_session_summary(session) for session in sessions if not getattr(session, 'incognito', False)]


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
