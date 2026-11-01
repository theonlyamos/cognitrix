"""Chat apps: connect a Telegram or Slack bot to chat with your agents.

``GET /channels`` lists both apps with their state. ``PUT /channels/{kind}``
saves tokens and options (tokens are checked with the app before saving).
Until a chat is linked, the payload carries a one-time ``link_code`` to send
to the bot. ``POST /channels/{kind}/relink`` forgets the linked chat and
issues a new code, ``POST /channels/{kind}/test`` sends a test message and
``DELETE /channels/{kind}`` disconnects.

Tokens are never sent back; the payload only says whether one is set.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.channels import service as channel_service
from cognitrix.channels.base import ChannelAuthError
from cognitrix.channels.slack import SLACK_MANIFEST
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.models.chat_channel import ChannelDelivery, ChatChannel

logger = logging.getLogger('cognitrix.log')

channels_api = APIRouter(prefix='/channels', dependencies=[Depends(jwt_only)])

MAX_TOKEN_LENGTH = 300


class ChannelUpdate(BaseModel):
    bot_token: str | None = None
    app_token: str | None = None
    agent_id: str | None = None
    enabled: bool | None = None
    send_alerts: bool | None = None
    send_approvals: bool | None = None


def _user_key(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


def _check_kind(kind: str) -> str:
    kind = kind.strip().lower()
    if kind not in channel_service.KINDS:
        raise HTTPException(status_code=404, detail='Unknown chat app')
    return kind


async def _load(user_key: str, kind: str) -> ChatChannel | None:
    return await ChatChannel.find_one({'user_id': user_key, 'kind': kind})


def _hint(token: str) -> str | None:
    return f'…{token[-4:]}' if token else None


def _payload(kind: str, row: ChatChannel | None) -> dict[str, Any]:
    from cognitrix.tasks.automation import utcnow_str

    service = channel_service.get_service()
    if row is None:
        return {
            'kind': kind, 'configured': False, 'enabled': False, 'linked': False,
            'bot_token_hint': None, 'app_token_set': False, 'bot_name': '', 'agent_id': '',
            'send_alerts': True, 'send_approvals': True, 'peer_name': '', 'linked_at': '',
            'link_code': '', 'link_url': '', 'status': '', 'last_error': '', 'connected': False,
        }
    code = row.link_code if not row.chat_id and row.link_code and (
        not row.link_code_expires_at or row.link_code_expires_at >= utcnow_str()) else ''
    link_url = ''
    if kind == 'telegram' and row.bot_name.startswith('@'):
        link_url = f'https://t.me/{row.bot_name[1:]}' + (f'?start={code}' if code else '')
    return {
        'kind': kind,
        'configured': bool(row.bot_token),
        'enabled': bool(row.enabled),
        'linked': bool(row.chat_id),
        'bot_token_hint': _hint(row.bot_token),
        'app_token_set': bool(row.app_token),
        'bot_name': row.bot_name,
        'agent_id': row.agent_id,
        'send_alerts': bool(row.send_alerts),
        'send_approvals': bool(row.send_approvals),
        'peer_name': row.peer_name,
        'linked_at': row.linked_at,
        'link_code': code,
        'link_code_expires_at': row.link_code_expires_at if code else '',
        'link_url': link_url,
        'status': row.status,
        'last_error': row.last_error,
        'connected': bool(service and service.is_connected(row.id)),
    }


async def _reload(row: ChatChannel | None) -> None:
    service = channel_service.get_service()
    if service is not None and row is not None:
        await service.reload(row.id)


def _clean_token(value: str, label: str) -> str:
    token = value.strip()
    if len(token) > MAX_TOKEN_LENGTH or any(c.isspace() for c in token):
        raise HTTPException(status_code=400, detail=f'That {label} looks wrong.')
    return token


@channels_api.get('')
async def list_channels(ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    user_key = _user_key(ctx)
    return {
        'available': channel_service.channels_enabled(),
        'channels': [_payload(kind, await _load(user_key, kind)) for kind in channel_service.KINDS],
        'slack_manifest': SLACK_MANIFEST,
    }


@channels_api.put('/{kind}')
async def update_channel(kind: str, body: ChannelUpdate,
                         ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    kind = _check_kind(kind)
    user_key = _user_key(ctx)
    row = await _load(user_key, kind)
    data: dict[str, Any] = {}

    bot_token = row.bot_token if row else ''
    app_token = row.app_token if row else ''
    if body.bot_token is not None:
        bot_token = _clean_token(body.bot_token, 'bot token')
    if body.app_token is not None:
        app_token = _clean_token(body.app_token, 'app-level token')
    tokens_changed = (row is None or bot_token != row.bot_token or app_token != row.app_token)
    if tokens_changed:
        if not bot_token:
            raise HTTPException(status_code=400, detail='Paste the bot token.')
        if kind == 'slack' and not app_token:
            raise HTTPException(status_code=400, detail='Slack also needs the app-level token (xapp-...).')
        for other in await ChatChannel.find({'kind': kind, 'bot_token': bot_token}) or []:
            if row is None or other.id != row.id:
                raise HTTPException(status_code=409, detail='This bot is already connected to another account.')
        probe = channel_service.make_transport(ChatChannel(
            user_id=user_key, kind=kind, bot_token=bot_token, app_token=app_token))
        try:
            info = await probe.check()
        except ChannelAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'Could not check the token: {exc}') from None
        finally:
            await probe.close()
        code, expires = channel_service.new_link_code()
        data.update({
            'bot_token': bot_token, 'app_token': app_token if kind == 'slack' else '',
            'bot_name': info.name, 'status': '', 'last_error': '',
            # A different bot means a different chat: link again.
            'chat_id': '', 'peer_id': '', 'peer_name': '', 'linked_at': '', 'session_id': '',
            'link_code': code, 'link_code_expires_at': expires,
        })

    if body.agent_id is not None:
        agent_id = body.agent_id.strip()
        if agent_id and await agent_access.find_visible_agent(user_key, agent_id=agent_id) is None:
            raise HTTPException(status_code=404, detail='Agent not found')
        if row is None or agent_id != row.agent_id:
            data.update({'agent_id': agent_id, 'session_id': ''})
    for field in ('enabled', 'send_alerts', 'send_approvals'):
        value = getattr(body, field)
        if value is not None:
            data[field] = value

    if row is None:
        row = ChatChannel(user_id=user_key, kind=kind, **data)
        await row.save()
    elif data:
        await ChatChannel.update_one({'id': row.id}, data)
    row = await _load(user_key, kind)
    if tokens_changed or 'enabled' in data:
        await _reload(row)
    return _payload(kind, row)


@channels_api.post('/{kind}/relink')
async def relink_channel(kind: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    kind = _check_kind(kind)
    row = await _load(_user_key(ctx), kind)
    if row is None:
        raise HTTPException(status_code=404, detail='Connect the bot first')
    code, expires = channel_service.new_link_code()
    await ChatChannel.update_one({'id': row.id}, {
        'chat_id': '', 'peer_id': '', 'peer_name': '', 'linked_at': '', 'session_id': '',
        'link_code': code, 'link_code_expires_at': expires,
    })
    return _payload(kind, await ChatChannel.get(row.id))


@channels_api.post('/{kind}/test')
async def test_channel(kind: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    kind = _check_kind(kind)
    row = await _load(_user_key(ctx), kind)
    if row is None or not row.chat_id:
        raise HTTPException(status_code=409, detail='Link a chat first')
    service = channel_service.get_service()
    if service is None or not service.is_connected(row.id):
        raise HTTPException(status_code=409, detail='The bot is not connected right now')
    await service.send(row, row.chat_id, 'Test message from Cognitrix. Alerts and approvals will show up here.')
    return {'status': 'sent'}


@channels_api.delete('/{kind}')
async def delete_channel(kind: str, ctx: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    kind = _check_kind(kind)
    row = await _load(_user_key(ctx), kind)
    if row is None:
        raise HTTPException(status_code=404, detail='Not connected')
    await ChatChannel.delete_many({'id': row.id})
    await ChannelDelivery.delete_many({'channel_id': row.id})
    service = channel_service.get_service()
    if service is not None:
        await service.reload(row.id)
    return {'status': 'deleted'}
