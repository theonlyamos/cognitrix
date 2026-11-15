"""Sharing a chat with other people on the server (see sessions/sharing.py).

Browser sessions only. The owner manages who a chat is shared with; people
it's shared with list it, read it, follow its replies live and (with "chat")
write in it.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.session_ownership import (
    OwnershipConflict,
    OwnershipNotFound,
    principal_key,
    require_active_owned,
)
from cognitrix.sessions import sharing

chat_sharing_api = APIRouter(prefix='/shared-chats', dependencies=[Depends(jwt_only)])


class SharedPerson(BaseModel):
    model_config = ConfigDict(extra='forbid')

    user_id: str = Field(min_length=1, max_length=200)
    role: str


class SharingUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    people: list[SharedPerson] = Field(default_factory=list, max_length=sharing.MAX_PEOPLE)


class SharedMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')

    message: str = Field(min_length=1, max_length=sharing.MESSAGE_MAX_CHARS)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail='Chat not found')


async def _people() -> dict[str, Any]:
    from cognitrix.api.routes.agents import _people as people
    from cognitrix.api.routes.agents import _person

    return {p['id']: p for p in (_person(u) for u in await people())}


async def _owned_chat(session_id: str, me: str):
    """The caller's own ordinary chat with an agent, or 404."""
    from cognitrix.sessions.base import Session

    try:
        binding = await require_active_owned(str(session_id), me)
    except (OwnershipNotFound, OwnershipConflict):
        raise _not_found() from None
    session = await Session.get(str(session_id))
    if session is None or str(session.agent_id or '') != binding.agent_id:
        raise _not_found()
    return binding, session


def _shareable(session: Any) -> None:
    if session.task_id or session.run_id or session.group_chat:
        raise HTTPException(status_code=400, detail='Only chats with one agent can be shared')
    if getattr(session, 'incognito', False):
        raise HTTPException(status_code=400, detail='Incognito chats can’t be shared')
    if getattr(session, 'public_link_id', None):
        raise _not_found()


async def _access(session_id: str, me: str):
    """(binding of the owner, session, my role): 'owner', 'chat' or 'view'."""
    from cognitrix.session_ownership import OwnershipState, SessionOwnership
    from cognitrix.sessions.base import Session

    share = await sharing.share_for(session_id, me)
    if share is None:
        binding, session = await _owned_chat(session_id, me)
        return binding, session, 'owner'
    binding = await SessionOwnership.find_one({'session_id': str(session_id)})
    session = await Session.get(str(session_id))
    if (
        binding is None or binding.state != OwnershipState.ACTIVE
        or str(binding.user_id) != str(share.owner_id)
        or session is None or str(session.agent_id or '') != str(binding.agent_id)
        or getattr(session, 'incognito', False)
    ):
        raise _not_found()
    return binding, session, share.role


def _title(session: Any) -> str:
    from cognitrix.api.routes.sessions import _session_title

    custom = (getattr(session, 'title', None) or '').strip()
    if custom:
        return custom
    return _session_title(session.chat) or 'Chat'


async def _agent_view(agent_id: str) -> dict[str, Any]:
    from cognitrix.agents import Agent

    agent = await Agent.find_one({'id': str(agent_id)})
    if agent is None:
        return {'id': str(agent_id), 'name': 'Agent', 'avatar': {}}
    avatar = getattr(agent, 'avatar', None)
    return {'id': str(agent.id), 'name': agent.name, 'avatar': avatar if isinstance(avatar, dict) else {}}


def _running(owner: str, session_id: str):
    from cognitrix.sessions import runs as chat_runs

    run = chat_runs.get_run(owner, session_id)
    return run if run is not None and run.running else None


# ------------------------------------------------------------------ the owner

@chat_sharing_api.get('/{session_id}/people')
async def get_people(session_id: str, ctx: AuthContext = Depends(get_auth_context)):
    me = principal_key(ctx.user)
    _, session = await _owned_chat(session_id, me)
    _shareable(session)
    people = await _people()
    shares = await sharing.shares_for(session_id)
    return {
        'people': [
            {**people.get(s.user_id, {'id': s.user_id, 'name': 'Someone who left', 'email': ''}), 'role': s.role}
            for s in shares
        ],
        'directory': sorted((p for key, p in people.items() if key != me), key=lambda p: p['name'].lower()),
    }


@chat_sharing_api.put('/{session_id}/people')
async def set_people(session_id: str, body: SharingUpdate, ctx: AuthContext = Depends(get_auth_context)):
    """Replace who the chat is shared with. Newly added people get an Inbox alert."""
    from cognitrix.tasks.automation import create_notification, utcnow_str

    me = principal_key(ctx.user)
    binding, session = await _owned_chat(session_id, me)
    _shareable(session)
    people = await _people()
    wanted: dict[str, str] = {}
    for entry in body.people:
        key = entry.user_id.strip()
        if entry.role not in sharing.ROLES:
            raise HTTPException(status_code=400, detail="A role is 'view' or 'chat'")
        if key == me:
            continue
        if key not in people:
            raise HTTPException(status_code=400, detail='That person has no account on this server')
        wanted[key] = entry.role
    current = {s.user_id: s for s in await sharing.shares_for(session_id)}
    for key, share in current.items():
        if key not in wanted:
            await sharing.ChatShare.delete_many({'id': share.id})
        elif share.role != wanted[key]:
            await sharing.ChatShare.update_one({'id': share.id}, {'role': wanted[key]})
    me_name = people.get(me, {}).get('name') or 'Someone'
    agent = await _agent_view(binding.agent_id)
    title = _title(session)
    for key, role in wanted.items():
        if key in current:
            continue
        await sharing.ChatShare(session_id=str(session_id), owner_id=me, user_id=key, role=role,
                                created=utcnow_str()).save()
        try:
            await create_notification(
                key, f'{me_name} shared a chat with you',
                f'“{title[:120]}” with {agent["name"]}. '
                + ('You can read it and write in it.' if role == sharing.ROLE_CHAT else 'You can read it.'),
                link=f'/shared/{session_id}',
            )
        except Exception:
            pass
    return await get_people(session_id, ctx)


# ---------------------------------------------------------- people shared with

@chat_sharing_api.get('')
async def shared_with_me(ctx: AuthContext = Depends(get_auth_context)):
    """Chats other people shared with me, newest first."""
    me = principal_key(ctx.user)
    people = await _people()
    out = []
    for share in await sharing.ChatShare.find({'user_id': me}) or []:
        try:
            binding, session, role = await _access(share.session_id, me)
        except HTTPException:
            continue
        updated = getattr(session, 'updated_at', None)
        out.append({
            'id': str(session.id),
            'title': _title(session),
            'owner': people.get(str(binding.user_id), {}).get('name') or 'Someone',
            'agent': await _agent_view(binding.agent_id),
            'role': role,
            'updated_at': updated.isoformat() if hasattr(updated, 'isoformat') else str(updated or ''),
            'running': _running(str(binding.user_id), str(session.id)) is not None,
        })
    out.sort(key=lambda c: c['updated_at'], reverse=True)
    return out


@chat_sharing_api.get('/{session_id}')
async def read_shared(session_id: str, ctx: AuthContext = Depends(get_auth_context)):
    me = principal_key(ctx.user)
    binding, session, role = await _access(session_id, me)
    people = await _people()
    owner_name = people.get(str(binding.user_id), {}).get('name') or 'Owner'
    run = _running(str(binding.user_id), str(session.id))
    shares = await sharing.shares_for(session_id)
    return {
        'id': str(session.id),
        'title': _title(session),
        'owner': {'id': str(binding.user_id), 'name': owner_name},
        'agent': await _agent_view(binding.agent_id),
        'role': role,
        'people': [{'name': people.get(s.user_id, {}).get('name') or 'Someone', 'role': s.role} for s in shares],
        'messages': sharing.transcript(session, owner_name),
        'running': run is not None,
        'run_id': run.run_id if run is not None else None,
    }


@chat_sharing_api.get('/{session_id}/events')
async def follow_shared(session_id: str, request: Request, ctx: AuthContext = Depends(get_auth_context)):
    """Live updates for a shared chat: the running reply as it is written
    (``text``), then ``changed`` whenever the saved chat moved on."""
    from cognitrix.sessions import runs as chat_runs
    from cognitrix.sessions.base import Session

    me = principal_key(ctx.user)
    binding, session, _ = await _access(session_id, me)
    owner = str(binding.user_id)

    async def still_allowed() -> bool:
        try:
            await _access(session_id, me)
            return True
        except HTTPException:
            return False

    async def events():
        seen_len = len(session.chat or [])
        followed: set[str] = set()
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            run = chat_runs.get_run(owner, str(session_id))
            if run is not None and run.running and run.run_id not in followed:
                followed.add(run.run_id)
                yield {'event': 'message', 'data': json.dumps({'type': 'run_started', 'run_id': run.run_id})}
                async for item in run.follow():
                    if await request.is_disconnected():
                        return
                    if item is chat_runs.IDLE:
                        yield {'event': 'ping', 'data': ''}
                        continue
                    kind = item.get('type') if isinstance(item, dict) else None
                    if kind == 'generate' and isinstance(item.get('content'), str):
                        yield {'event': 'message', 'data': json.dumps({'type': 'text', 'content': item['content']})}
                    elif kind == 'tool' and item.get('status') == 'started':
                        yield {'event': 'message', 'data': json.dumps({'type': 'tool', 'name': item.get('tool_name') or ''})}
                    elif kind == 'approval_request':
                        yield {'event': 'message', 'data': json.dumps({'type': 'waiting'})}
                yield {'event': 'message', 'data': json.dumps({'type': 'changed'})}
                continue
            await asyncio.sleep(2.0)
            fresh = await Session.get(str(session_id))
            if fresh is None or not await still_allowed():
                yield {'event': 'message', 'data': json.dumps({'type': 'gone'})}
                return
            if len(fresh.chat or []) != seen_len:
                seen_len = len(fresh.chat or [])
                yield {'event': 'message', 'data': json.dumps({'type': 'changed'})}
            idle += 1
            if idle % 8 == 0:
                yield {'event': 'ping', 'data': ''}

    return EventSourceResponse(events())


@chat_sharing_api.post('/{session_id}/messages')
async def send_shared(session_id: str, body: SharedMessage, ctx: AuthContext = Depends(get_auth_context)):
    """Write in a chat shared with you (role "chat"). The agent answers you
    read-only; see sessions/sharing.py."""
    from cognitrix.agents import Agent
    from cognitrix.agents import access as agent_access

    me = principal_key(ctx.user)
    binding, session, role = await _access(session_id, me)
    if role == 'owner':
        raise HTTPException(status_code=400, detail='Write in your own chat from the chat page')
    if role != sharing.ROLE_CHAT:
        raise HTTPException(status_code=403, detail='You can read this chat but not write in it')
    owner = str(binding.user_id)
    if _running(owner, str(session.id)) is not None:
        raise HTTPException(status_code=409, detail='The agent is still answering. Try again when it’s done.')
    agent = await Agent.find_one({'id': str(binding.agent_id)})
    if agent is None or not agent_access.can_view(agent, owner):
        raise HTTPException(status_code=409, detail='This chat’s agent is gone')
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail='Write a message first')
    people = await _people()
    name = people.get(me, {}).get('name') or 'Someone'
    owner_name = people.get(owner, {}).get('name') or 'the owner'
    run = sharing.start_guest_turn(owner, agent, session, sharing.guest_text(name, owner_name, text))
    return {'status': 'started', 'run_id': run.run_id}
