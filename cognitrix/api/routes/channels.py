"""Chat apps: connect a Telegram, Slack, Discord, Microsoft Teams or WhatsApp bot to chat with your agents.

``GET /channels`` lists both apps with their state. ``PUT /channels/{kind}``
saves tokens and options (tokens are checked with the app before saving).
Until a chat is linked, the payload carries a one-time ``link_code`` to send
to the bot. ``POST /channels/{kind}/relink`` forgets the linked chat and
issues a new code, ``POST /channels/{kind}/test`` sends a test message and
``DELETE /channels/{kind}`` disconnects.

Tokens are never sent back; the payload only says whether one is set.

Microsoft Teams and WhatsApp have no connection the engine can open. Teams
posts each message to ``POST /channels/teams/{channel_id}/messages`` (checked
against Microsoft's signing keys) and WhatsApp to
``/channels/whatsapp/{channel_id}/webhook`` (checked against the Meta app
secret). Neither has a sign-in, and both need the server to be reachable
over public HTTPS.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.channels import service as channel_service
from cognitrix.channels.base import ChannelAuthError
from cognitrix.channels.slack import SLACK_MANIFEST
from cognitrix.common.security import AuthContext, get_auth_context, jwt_only
from cognitrix.models.chat_channel import ChannelDelivery, ChannelThread, ChatChannel

logger = logging.getLogger('cognitrix.log')

channels_api = APIRouter(prefix='/channels', dependencies=[Depends(jwt_only)])
# Called by Microsoft, not by a signed-in person.
channel_hooks_api = APIRouter(prefix='/channels')
MAX_ACTIVITY_BYTES = 256 * 1024

MAX_TOKEN_LENGTH = 600


class ChannelUpdate(BaseModel):
    bot_token: str | None = None
    app_token: str | None = None
    agent_id: str | None = None
    enabled: bool | None = None
    send_alerts: bool | None = None
    send_approvals: bool | None = None
    answer_others: bool | None = None
    app_id: str | None = None
    """Teams: the bot's Microsoft App ID. WhatsApp: the phone number ID."""
    tenant_id: str | None = None
    """Teams: the Microsoft 365 organisation the bot belongs to."""
    app_secret: str | None = None
    """WhatsApp: the Meta app's secret, which signs its webhook calls."""


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
            'answer_others': False, 'mentions': kind in channel_service.MENTION_KINDS,
            'app_id': '', 'tenant_id': '', 'webhook_path': '', 'verify_token': '',
            'needs_public_url': kind in channel_service.WEBHOOK_KINDS,
        }
    code = row.link_code if not row.chat_id and row.link_code and (
        not row.link_code_expires_at or row.link_code_expires_at >= utcnow_str()) else ''
    link_url = ''
    if kind == 'telegram' and row.bot_name.startswith('@'):
        link_url = f'https://t.me/{row.bot_name[1:]}' + (f'?start={code}' if code else '')
    if kind == 'discord' and row.bot_token:
        # A bot token starts with the bot's user id in base64; the id makes
        # the link that adds the bot to a server, which Discord requires
        # before the bot can be messaged.
        app_id = _discord_app_id(row.bot_token)
        if app_id:
            link_url = f'https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot&permissions=0'
    app_id, tenant_id = ('', '')
    if kind == 'teams':
        from cognitrix.channels.teams import split_app

        app_id, tenant_id = split_app(row.app_token)
    verify = ''
    if kind == 'whatsapp':
        from cognitrix.channels import whatsapp

        app_id, secret = whatsapp.split_app(row.app_token)
        verify = whatsapp.verify_token(secret, str(row.id)) if secret else ''
        digits = ''.join(c for c in row.bot_name if c.isdigit())
        if digits:
            link_url = f'https://wa.me/{digits}' + (f'?text={code}' if code else '')
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
        'answer_others': bool(row.answer_others),
        'mentions': kind in channel_service.MENTION_KINDS,
        'app_id': app_id,
        'tenant_id': tenant_id,
        # The messaging endpoint to paste into the Azure Bot, after the server's public address.
        'webhook_path': {
            'teams': f'/api/v1/channels/teams/{row.id}/messages',
            'whatsapp': f'/api/v1/channels/whatsapp/{row.id}/webhook',
        }.get(kind, ''),
        # WhatsApp: what to type as "Verify token" in Meta's webhook form.
        'verify_token': verify,
        'needs_public_url': kind in channel_service.WEBHOOK_KINDS,
    }


def _discord_app_id(token: str) -> str:
    import base64
    import binascii

    head = token.split('.', 1)[0]
    try:
        app_id = base64.b64decode(head + '=' * (-len(head) % 4)).decode('ascii')
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ''
    return app_id if app_id.isdigit() else ''


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
    if kind == 'whatsapp' and (body.app_id is not None or body.app_secret is not None):
        from cognitrix.channels import whatsapp

        old_phone, old_secret = whatsapp.split_app(app_token)
        phone = _clean_token(body.app_id, 'phone number ID') if body.app_id is not None else old_phone
        secret = _clean_token(body.app_secret, 'app secret') if body.app_secret else old_secret
        if '|' in phone or '|' in secret:
            raise HTTPException(status_code=400, detail='That phone number ID or app secret looks wrong.')
        app_token = f'{phone}|{secret}' if phone or secret else ''
    if body.app_token is not None and kind not in ('teams', 'whatsapp'):
        app_token = _clean_token(body.app_token, 'app-level token')
    elif kind == 'teams' and (body.app_id is not None or body.tenant_id is not None):
        from cognitrix.channels.teams import split_app

        old_app, old_tenant = split_app(app_token)
        new_app = _clean_token(body.app_id, 'App ID') if body.app_id is not None else old_app
        new_tenant = _clean_token(body.tenant_id, 'tenant ID') if body.tenant_id is not None else old_tenant
        if '|' in new_app or '|' in new_tenant:
            raise HTTPException(status_code=400, detail='That App ID or tenant ID looks wrong.')
        app_token = f'{new_app}|{new_tenant}' if new_app or new_tenant else ''
    tokens_changed = (row is None or bot_token != row.bot_token or app_token != row.app_token)
    if tokens_changed:
        if kind == 'teams':
            from cognitrix.channels.teams import split_app

            if not bot_token or not all(split_app(app_token)):
                raise HTTPException(
                    status_code=400, detail='Teams needs the App ID, a client secret and the tenant ID.')
        if kind == 'whatsapp':
            from cognitrix.channels import whatsapp

            if not bot_token or not all(whatsapp.split_app(app_token)):
                raise HTTPException(
                    status_code=400, detail='WhatsApp needs the phone number ID, an access token and the app secret.')
        if not bot_token:
            raise HTTPException(status_code=400, detail='Paste the bot token.')
        if kind == 'slack' and not app_token:
            raise HTTPException(status_code=400, detail='Slack also needs the app-level token (xapp-...).')
        same_bot = {'kind': kind, 'app_token': app_token} if kind == 'teams' else {'kind': kind, 'bot_token': bot_token}
        for other in await ChatChannel.find(same_bot) or []:
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
            'bot_token': bot_token, 'app_token': app_token if kind in ('slack', 'teams', 'whatsapp') else '',
            'bot_name': info.name, 'status': '', 'last_error': '', 'service_url': '',
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
    if body.answer_others and kind not in channel_service.MENTION_KINDS:
        raise HTTPException(status_code=400, detail='Only Slack and Teams bots can answer in shared channels.')
    for field in ('enabled', 'send_alerts', 'send_approvals', 'answer_others'):
        value = getattr(body, field)
        if value is not None:
            data[field] = value

    if row is None:
        row = ChatChannel(user_id=user_key, kind=kind, **data)
        await row.save()
    elif data:
        await ChatChannel.update_one({'id': row.id}, data)
    if tokens_changed and row is not None:
        await ChannelThread.delete_many({'channel_id': row.id})
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
    await ChannelThread.delete_many({'channel_id': row.id})
    service = channel_service.get_service()
    if service is not None:
        await service.cancel_turn(row.id)
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
    await ChannelThread.delete_many({'channel_id': row.id})
    service = channel_service.get_service()
    if service is not None:
        await service.reload(row.id)
    return {'status': 'deleted'}


@channel_hooks_api.post('/teams/{channel_id}/messages', include_in_schema=False)
async def teams_messages(channel_id: str, request: Request) -> Response:
    """Where Microsoft Teams posts messages for a connected bot.

    Anyone can reach this, so nothing is trusted until the transport has
    checked the request's signature, audience and organisation.
    """
    import json

    from cognitrix.channels.teams import TeamsAuthError

    service = channel_service.get_service()
    if service is None:
        raise HTTPException(status_code=404, detail='Not found')
    raw = await request.body()
    if len(raw) > MAX_ACTIVITY_BYTES:
        raise HTTPException(status_code=413, detail='Too large')
    try:
        activity = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail='Not JSON') from None
    if not isinstance(activity, dict):
        raise HTTPException(status_code=400, detail='Not an activity')
    try:
        await service.deliver_teams(channel_id, request.headers.get('authorization', ''), activity)
    except LookupError:
        raise HTTPException(status_code=404, detail='Not found') from None
    except TeamsAuthError:
        raise HTTPException(status_code=401, detail='Unauthorized') from None
    return Response(status_code=200)


@channel_hooks_api.get('/whatsapp/{channel_id}/webhook', include_in_schema=False)
async def whatsapp_verify(channel_id: str, request: Request) -> Response:
    """Meta's one-time check that this address belongs to whoever set the
    webhook up: it sends the verify token and expects its challenge back."""
    import hmac

    from cognitrix.channels import whatsapp

    row = await ChatChannel.get(channel_id)
    query = request.query_params
    secret = whatsapp.split_app(row.app_token)[1] if row is not None and row.kind == 'whatsapp' else ''
    expected = whatsapp.verify_token(secret, channel_id) if secret else ''
    challenge = query.get('hub.challenge', '')
    if (not expected or query.get('hub.mode') != 'subscribe'
            or not hmac.compare_digest(query.get('hub.verify_token', ''), expected)
            or not challenge.isalnum() or len(challenge) > 200):
        raise HTTPException(status_code=403, detail='Forbidden')
    return Response(content=challenge, media_type='text/plain')


@channel_hooks_api.post('/whatsapp/{channel_id}/webhook', include_in_schema=False)
async def whatsapp_messages(channel_id: str, request: Request) -> Response:
    """Where WhatsApp posts messages for a connected number. Anyone can reach
    this, so the call is only used once its signature has been checked."""
    from cognitrix.channels.whatsapp import WhatsAppAuthError

    service = channel_service.get_service()
    if service is None:
        raise HTTPException(status_code=404, detail='Not found')
    raw = await request.body()
    if len(raw) > MAX_ACTIVITY_BYTES:
        raise HTTPException(status_code=413, detail='Too large')
    try:
        await service.deliver_whatsapp(channel_id, request.headers.get('x-hub-signature-256', ''), raw)
    except LookupError:
        raise HTTPException(status_code=404, detail='Not found') from None
    except WhatsAppAuthError:
        raise HTTPException(status_code=401, detail='Unauthorized') from None
    return Response(status_code=200)
