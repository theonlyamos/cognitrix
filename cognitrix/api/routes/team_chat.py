"""Team group chats: the signed-in user talks with all of a team's agents.

A message starts a background run (like a web chat turn, see
``cognitrix.sessions.runs``), so the browser follows it with the existing
``GET /agents/runs/{session_id}/events`` stream and can leave and come back.
Approvals and questions from the agents use the usual ``/agents/approval``
and ``/agents/question`` endpoints.
"""

import asyncio
import logging
import weakref
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from cognitrix.agents import Agent
from cognitrix.agents.access import can_view
from cognitrix.common.security import get_current_user, jwt_only
from cognitrix.session_ownership import principal_key
from cognitrix.sessions import runs as chat_runs
from cognitrix.sessions.base import Session
from cognitrix.teams import group_chat
from cognitrix.teams.base import Team

logger = logging.getLogger('cognitrix.log')

team_chat_api = APIRouter(prefix='/teams', dependencies=[Depends(jwt_only)])

MAX_MESSAGE_CHARS = 32_000

# Strong refs so running group turns are not garbage-collected.
_TASKS: set[asyncio.Task] = set()
# One send at a time per chat, so two tabs can't both start a turn.
_SEND_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _send_lock(session_id: str) -> asyncio.Lock:
    lock = _SEND_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _SEND_LOCKS[session_id] = lock
    return lock


def _owner(user) -> str:
    return principal_key(user)


def _run_key(user) -> str:
    # Same key the /agents run, approval and question endpoints use.
    return str(getattr(user, 'id', None) or getattr(user, 'email', 'anon'))


def _run_agent_id(team_id: str) -> str:
    return f'team:{team_id}'


async def _team_and_members(team_id: str, user) -> tuple[Team, list[Agent]]:
    owner = _owner(user)
    team = await Team.get(team_id)
    if team is None or not can_view(team, owner):
        raise HTTPException(status_code=404, detail='Team not found')
    members: list[Agent] = []
    for agent_id in team.assigned_agents or []:
        agent = await Agent.find_one({'id': agent_id})
        if agent is None:
            continue
        if not can_view(agent, owner):
            raise HTTPException(status_code=403, detail='Not allowed to use this agent')
        members.append(agent)
    return team, members


async def _owned_chat(team_id: str, session_id: str, user) -> Session:
    session = await Session.get(session_id)
    if (
        session is None
        or not session.group_chat
        or str(session.team_id or '') != str(team_id)
        or str(session.user_id or '') != _owner(user)
    ):
        raise HTTPException(status_code=404, detail='Chat not found')
    return session


def _member_json(agent: Agent, team: Team) -> dict[str, Any]:
    return {
        'id': agent.id,
        'name': agent.name,
        'handle': group_chat.handle(agent.name),
        'lead': str(agent.id) == str(team.leader_id or ''),
        'provider': getattr(agent.llm, 'provider', None),
        'model': getattr(agent.llm, 'model', None),
    }


def _chat_summary(session: Session, run_key: str) -> dict[str, Any]:
    run = chat_runs.get_run(run_key, str(session.id))
    last = next(
        (e.get('timestamp') for e in reversed(session.chat or []) if e.get('timestamp')),
        None,
    )
    return {
        'id': session.id,
        'title': (getattr(session, 'title', None) or '').strip() or group_chat.chat_title(session.chat or []),
        'pinned': bool(getattr(session, 'pinned', False)),
        'archived': bool(getattr(session, 'archived', False)),
        'updated_at': last,
        'running': bool(run and run.running),
    }


@team_chat_api.get('/{team_id}/chats')
async def list_team_chats(team_id: str, user=Depends(get_current_user)):
    team, members = await _team_and_members(team_id, user)
    rows = await Session.find({'team_id': str(team.id), 'user_id': _owner(user)})
    chats = [_chat_summary(s, _run_key(user)) for s in rows if s.group_chat]
    chats.sort(key=lambda c: c['updated_at'] or '', reverse=True)
    return {
        'team': {'id': team.id, 'name': team.name, 'leader_id': team.leader_id},
        'members': [_member_json(m, team) for m in members],
        'chats': chats,
    }


@team_chat_api.get('/{team_id}/chats/{session_id}')
async def get_team_chat(team_id: str, session_id: str, user=Depends(get_current_user)):
    await _team_and_members(team_id, user)
    session = await _owned_chat(team_id, session_id, user)
    run = chat_runs.get_run(_run_key(user), str(session.id))
    return {
        **_chat_summary(session, _run_key(user)),
        'messages': session.chat or [],
        # While the team is still answering, the page shows the saved history
        # up to this run and replays the run on top of it.
        'run': run.summary() if run is not None and run.running else None,
    }


@team_chat_api.delete('/{team_id}/chats/{session_id}')
async def delete_team_chat(team_id: str, session_id: str, user=Depends(get_current_user)):
    session = await _owned_chat(team_id, session_id, user)
    run = chat_runs.get_run(_run_key(user), str(session.id))
    if run is not None and run.running:
        raise HTTPException(status_code=409, detail='Stop the chat before deleting it')
    await _delete_chat(session, _run_agent_id(team_id))
    return {'status': 'deleted'}


async def _delete_chat(session: Session, team_agent: str) -> None:
    """Delete the chat and, when it has uploads, the files stored with it."""
    from cognitrix import session_ownership as ownerships

    session_id = str(session.id)
    owner = str(session.user_id or '')
    binding = await ownerships.SessionOwnership.find_one({'session_id': session_id})
    if binding is None:
        await Session.delete_many({'id': session_id})
        return
    if binding.user_id != owner or binding.agent_id != team_agent:
        raise HTTPException(status_code=404, detail='Chat not found')
    from cognitrix.api.routes.sessions import cleanup_owned_session_resources

    try:
        if binding.state == ownerships.OwnershipState.ACTIVE:
            token = await ownerships.begin_delete(session_id, owner, team_agent)
        else:
            token = await ownerships.resume_lifecycle(
                session_id, owner, team_agent, ownerships.OwnershipState.DELETING,
            )
    except (ownerships.OwnershipNotFound, ownerships.OwnershipConflict) as exc:
        raise HTTPException(status_code=409, detail='This chat is busy. Try again.') from exc
    await cleanup_owned_session_resources(
        session_id=token.session_id,
        user_id=token.user_id,
        agent_id=token.agent_id,
        generation=token.generation,
    )
    await Session.delete_many({'id': session_id})
    await ownerships.finish_delete(token)


class TeamChatUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str | None = Field(default=None, max_length=120)
    pinned: bool | None = None
    archived: bool | None = None


@team_chat_api.patch('/{team_id}/chats/{session_id}')
async def update_team_chat(
    team_id: str, session_id: str, body: TeamChatUpdate, user=Depends(get_current_user),
):
    """Rename, pin or archive one of the caller's group chats."""
    session = await _owned_chat(team_id, session_id, user)
    run = chat_runs.get_run(_run_key(user), str(session.id))
    # A running turn saves its own copy of the chat when it ends and would
    # undo this change.
    if run is not None and run.running:
        raise HTTPException(status_code=409, detail='This chat is still working. Try again when it finishes.')
    changes = body.model_dump(exclude_unset=True)
    if 'title' in changes:
        session.title = (changes['title'] or '').strip() or None
    if changes.get('pinned') is not None:
        session.pinned = bool(changes['pinned'])
    if changes.get('archived') is not None:
        session.archived = bool(changes['archived'])
    await session.save()
    return _chat_summary(session, _run_key(user))


@team_chat_api.post('/{team_id}/chats/{session_id}/stop')
async def stop_team_chat(team_id: str, session_id: str, user=Depends(get_current_user)):
    await _owned_chat(team_id, session_id, user)
    if not chat_runs.stop_run(_run_key(user), session_id):
        raise HTTPException(status_code=409, detail='Nothing is running in this chat')
    return {'status': 'stopping'}


class TeamChatMessage(BaseModel):
    message: str = ''
    session_id: str | None = None
    reasoning_effort: str | None = None


@team_chat_api.post('/{team_id}/chats', status_code=202)
async def send_team_chat(team_id: str, request: Request, user=Depends(get_current_user)):
    """Send a message to the team. JSON, or multipart with a ``payload`` JSON
    part and ``files`` parts (the same shape as a chat with one agent)."""
    from pydantic import ValidationError

    from cognitrix.api.routes.agents import _chat_request_parts
    from cognitrix.media.staging import cleanup_staged_attachments, stage_upload_files

    async with _chat_request_parts(request) as (data, upload_files):
        try:
            body = TeamChatMessage(**{k: data.get(k) for k in TeamChatMessage.model_fields if k in data})
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail='Invalid message') from exc
        message = (body.message or '').strip()
        if not message and not upload_files:
            raise HTTPException(status_code=400, detail='message is required')
        if len(message) > MAX_MESSAGE_CHARS:
            raise HTTPException(status_code=400, detail='message is too long')
        team, members = await _team_and_members(team_id, user)
        if not members:
            raise HTTPException(status_code=400, detail='This team has no agents yet')

        run_key = _run_key(user)
        staged = None
        try:
            if body.session_id:
                lock = _send_lock(str(body.session_id))
                async with lock:
                    session = await _owned_chat(team_id, body.session_id, user)
                    running = chat_runs.get_run(run_key, str(session.id))
                    if running is not None and running.running:
                        raise HTTPException(
                            status_code=409,
                            detail='The team is still answering the last message',
                        )
                    if upload_files:
                        staged = await stage_upload_files(
                            upload_files, user_key=run_key, stream_id=_run_agent_id(team_id),
                        )
                    return await _start_turn(session, team, members, message, run_key, body, staged)
            session = Session(team_id=str(team.id), user_id=_owner(user), group_chat=True)
            if upload_files:
                staged = await stage_upload_files(
                    upload_files, user_key=run_key, stream_id=_run_agent_id(team_id),
                )
            return await _start_turn(session, team, members, message, run_key, body, staged)
        except BaseException:
            # The turn never started, so nothing else will remove the upload.
            if staged is not None:
                await cleanup_staged_attachments(staged)
            raise


async def _start_turn(
    session: Session,
    team: Team,
    members: list[Agent],
    message: str,
    run_key: str,
    body: TeamChatMessage,
    staged: Any = None,
) -> dict[str, Any]:
    """Save the user's message and register the run before anything can yield
    to a second send for the same chat (the caller holds its lock)."""
    from cognitrix.providers.base import normalize_reasoning_effort

    history_len = len(session.chat or [])
    session.update_history(group_chat.transcript_entry(message))
    await session.save()

    task: asyncio.Task | None = None

    def stop() -> bool:
        if task is None or task.done():
            return False
        task.cancel()
        return True

    run = chat_runs.start_run(
        run_key,
        _run_agent_id(str(team.id)),
        str(session.id),
        prompt=message,
        history_len=history_len,
        stop=stop,
    )
    task = asyncio.create_task(_run_turn(
        session, team, members, message,
        run=run,
        run_key=run_key,
        effort=normalize_reasoning_effort(body.reasoning_effort) or None,
        staged=staged,
    ))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {'session_id': session.id, 'run_id': run.run_id, 'status': 'running'}


async def _run_turn(
    session: Session,
    team: Team,
    members: list[Agent],
    message: str,
    *,
    run: chat_runs.ChatRun,
    run_key: str,
    effort: str | None,
    staged: Any = None,
) -> None:
    from cognitrix.media import MediaOwnership
    from cognitrix.media.context import (
        MediaTurnContext,
        reset_media_turn_context,
        set_media_turn_context,
    )
    from cognitrix.media.staging import cleanup_staged_attachments, rollback_promoted_attachments
    from cognitrix.providers.base import turn_reasoning_effort
    from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
    from cognitrix.safety.approval_gate import web_turn_ctx
    from cognitrix.tools.utils import ToolExecutionContext, signed_in_host_process_mode

    session_id = str(session.id)

    async def emit(payload: dict[str, Any]) -> None:
        if isinstance(payload, dict):
            run.record({**payload, 'session_id': session_id})

    async def run_agent(agent: Agent, scratch: Session, trigger: str, out) -> None:
        await scratch(
            trigger,
            agent,
            interface='web',
            stream=True,
            output=out,
            wsquery={'type': 'generate', 'action': 'chat_message'},
            record_history=True,
            persist_history=False,
            compact_history=False,
            tool_context=ToolExecutionContext(
                session_id=session_id,
                user_id=run_key,
                agent_id=str(agent.id),
                document_capabilities=uploads.capabilities,
                host_process_mode=signed_in_host_process_mode(),
            ),
        )

    terminal: dict[str, Any] = {'type': 'turn_complete', 'content': '', 'session_id': session_id}
    # What the person attached belongs to the chat, not to one agent: every
    # agent that answers this message sees the pictures and may read the files.
    ownership = MediaOwnership(
        session_id=session_id,
        user_id=str(session.user_id or run_key),
        agent_id=_run_agent_id(str(team.id)),
    )
    uploads = _TurnUploads()
    approval_token = web_turn_ctx.set({
        'emit': emit,
        'session_id': session_id,
        'bypass': False,
        'user_key': run_key,
        'agent_name': getattr(team, 'name', '') or '',
        'href': f'/home?team={team.id}&chat={session_id}',
    })
    question_token = question_turn_ctx.set(QuestionTurnContext(
        emit=emit,
        session_id=session_id,
        stream_id='group',
        user_key=run_key,
    ))
    effort_token = turn_reasoning_effort.set(effort)
    media_token = None
    try:
        if staged is not None:
            await _take_in_uploads(session, staged, ownership, uploads, emit)
        media_token = set_media_turn_context(MediaTurnContext(
            ownership=ownership,
            current_images=list(uploads.images),
            selected_image=None,
            vision_data_uri_cache={},
        ))
        await group_chat.run_group_turn(
            session, team, members, message, emit=emit, run_agent=run_agent,
        )
    except asyncio.CancelledError:
        terminal = {'type': 'turn_stopped', 'content': '', 'session_id': session_id}
    except Exception as exc:
        logger.exception('Group chat turn failed')
        run.record({
            'type': 'error',
            'content': _UPLOAD_FAILED if isinstance(exc, _UploadFailed) else str(exc),
            'session_id': session_id,
        })
        # The error event above already told the page; end the run normally.
    finally:
        if media_token is not None:
            reset_media_turn_context(media_token)
        turn_reasoning_effort.reset(effort_token)
        question_turn_ctx.reset(question_token)
        web_turn_ctx.reset(approval_token)
        try:
            if uploads.promoted is not None and not uploads.adopted:
                await rollback_promoted_attachments(uploads.promoted, ownership)
        except BaseException:
            logger.exception('Failed to roll back team chat attachments')
        try:
            if staged is not None:
                await cleanup_staged_attachments(staged)
        except BaseException:
            logger.exception('Failed to clean staged team chat attachments')
        run.finish(terminal)


_UPLOAD_FAILED = 'The attached files could not be read. Send them again.'


class _UploadFailed(Exception):
    pass


class _TurnUploads:
    """What one message carried, once it is stored with the chat."""

    def __init__(self) -> None:
        self.promoted: Any = None
        self.adopted = False
        self.images: list[Any] = []
        self.capabilities: tuple[Any, ...] = ()


async def _bind_chat(ownership) -> None:
    """Uploads are counted against the chat's owner record. A team chat gets
    its record the first time someone attaches something."""
    from cognitrix import session_ownership as ownerships

    try:
        await ownerships.require_active_owned(
            ownership.session_id, ownership.user_id, ownership.agent_id,
        )
    except ownerships.OwnershipNotFound:
        await ownerships.claim_new(ownership.session_id, ownership.user_id, ownership.agent_id)


async def _take_in_uploads(session: Session, staged, ownership, uploads: _TurnUploads, emit) -> None:
    """Store the staged files with the chat, write them into the transcript
    after the person's message, and only then count them as kept."""
    from cognitrix.media.document_capabilities import load_turn_document_capabilities
    from cognitrix.media.documents import document_assets
    from cognitrix.media.staging import (
        promote_staged_attachments,
        release_promoted_attachment_reservation,
    )

    try:
        await _bind_chat(ownership)
        uploads.promoted = promoted = await promote_staged_attachments(staged, ownership)
        uploads.images = list(promoted.image_refs)
        documents = list(promoted.document_paths)
        document_ids = [str(item['id']) for item in documents if item.get('id')]
        uploads.capabilities = await load_turn_document_capabilities(
            ownership, fresh_document_ids=tuple(document_ids),
        )
        entries = group_chat.attachment_entries(
            [ref.model_dump() for ref in uploads.images], documents,
        )
        if entries:
            start = len(session.chat)
            session.update_history(entries)
            try:
                await asyncio.shield(session.save())
            except BaseException:
                del session.chat[start:]
                raise
        uploads.adopted = True
        release_promoted_attachment_reservation(promoted)
        if document_ids:
            await document_assets.mark_documents_adopted(document_ids, ownership)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception('Could not store team chat attachments')
        raise _UploadFailed() from exc
    await emit({
        'type': 'attachments_ingested',
        'artifacts': [ref.model_dump() for ref in uploads.images],
        'document_count': len(uploads.promoted.document_paths),
    })
