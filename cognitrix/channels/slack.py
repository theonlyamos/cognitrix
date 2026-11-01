"""Slack app over Socket Mode, so no public URL is needed.

Needs two tokens from the same Slack app: the bot token (xoxb-...) for the
Web API and an app-level token (xapp-..., scope connections:write) to open
the Socket Mode connection. ``SLACK_MANIFEST`` creates a matching app.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .base import (
    BotInfo,
    Button,
    ButtonPress,
    ChannelAuthError,
    Handlers,
    Incoming,
    Transport,
)
from .formatting import to_slack_mrkdwn

logger = logging.getLogger('cognitrix.log')

API_BASE = 'https://slack.com/api'

SLACK_MANIFEST: dict[str, Any] = {
    'display_information': {
        'name': 'Cognitrix',
        'description': 'Chat with your Cognitrix agents and answer their approvals.',
    },
    'features': {
        'app_home': {
            'messages_tab_enabled': True,
            'messages_tab_read_only_enabled': False,
        },
        'bot_user': {'display_name': 'Cognitrix', 'always_online': True},
    },
    'oauth_config': {
        'scopes': {'bot': ['chat:write', 'im:history', 'im:read', 'im:write']},
    },
    'settings': {
        'event_subscriptions': {'bot_events': ['message.im']},
        'interactivity': {'is_enabled': True},
        'org_deploy_enabled': False,
        'socket_mode_enabled': True,
        'token_rotation_enabled': False,
    },
}

_AUTH_ERRORS = {'invalid_auth', 'not_authed', 'account_inactive', 'token_revoked',
                'token_expired', 'not_allowed_token_type', 'missing_scope'}


class SlackError(RuntimeError):
    pass


class SlackTransport(Transport):
    kind = 'slack'
    text_limit = 3500

    def __init__(self, bot_token: str, app_token: str, *,
                 session: aiohttp.ClientSession | None = None, api_base: str = API_BASE):
        self._bot_token = bot_token
        self._app_token = app_token
        self._base = api_base
        self._session = session
        self._owns_session = session is None
        self._bot_user_id = ''

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), trust_env=True)
        return self._session

    async def _call(self, method: str, token: str | None = None, **params: Any) -> dict[str, Any]:
        headers = {'Authorization': f'Bearer {token or self._bot_token}'}
        payload = {k: v for k, v in params.items() if v is not None}
        try:
            async with self._http().post(f'{self._base}/{method}', json=payload,
                                         headers=headers) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as exc:
            raise SlackError(f'Could not reach Slack ({type(exc).__name__})') from None
        if not isinstance(data, dict):
            raise SlackError('Slack sent an unexpected reply')
        if not data.get('ok'):
            error = str(data.get('error') or 'unknown_error')
            if error in _AUTH_ERRORS:
                raise ChannelAuthError(f'Slack rejected the token ({error})')
            raise SlackError(f'Slack error: {error}')
        return data

    async def check(self) -> BotInfo:
        if not self._app_token.startswith('xapp-'):
            raise ChannelAuthError('The app-level token should start with xapp-')
        auth = await self._call('auth.test')
        self._bot_user_id = str(auth.get('user_id') or '')
        # Opening a Socket Mode URL proves the app token and that Socket Mode is on.
        await self._call('apps.connections.open', token=self._app_token)
        team = str(auth.get('team') or '')
        name = str(auth.get('user') or 'Cognitrix')
        return BotInfo(name=f'@{name}' + (f' in {team}' if team else ''))

    async def run(self, handlers: Handlers) -> None:
        if not self._bot_user_id:
            auth = await self._call('auth.test')
            self._bot_user_id = str(auth.get('user_id') or '')
        while True:
            opened = await self._call('apps.connections.open', token=self._app_token)
            url = str(opened.get('url') or '')
            async with self._http().ws_connect(url, heartbeat=30, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
                async for frame in ws:
                    if frame.type != aiohttp.WSMsgType.TEXT:
                        if frame.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    try:
                        envelope = json.loads(frame.data)
                    except ValueError:
                        continue
                    kind = envelope.get('type')
                    if kind == 'hello':
                        if handlers.on_connected is not None:
                            await handlers.on_connected()
                        continue
                    if kind == 'disconnect':
                        break  # Slack asks us to reconnect
                    if envelope.get('envelope_id'):
                        await ws.send_str(json.dumps({'envelope_id': envelope['envelope_id']}))
                    try:
                        await self._dispatch(envelope, handlers)
                    except Exception:
                        logger.exception('Slack envelope %s failed', kind)

    async def _dispatch(self, envelope: dict[str, Any], handlers: Handlers) -> None:
        payload = envelope.get('payload') or {}
        if envelope.get('type') == 'events_api':
            event = payload.get('event') or {}
            if (event.get('type') != 'message' or event.get('subtype') or event.get('bot_id')
                    or event.get('user') in (None, '', self._bot_user_id)):
                return
            await handlers.on_message(Incoming(
                chat_id=str(event.get('channel') or ''),
                sender_id=str(event.get('user') or ''),
                sender_name='',
                text=str(event.get('text') or ''),
                private=event.get('channel_type') == 'im',
            ))
        elif envelope.get('type') == 'interactive' and payload.get('type') == 'block_actions':
            container = payload.get('container') or {}
            for action in payload.get('actions') or []:
                await handlers.on_button(ButtonPress(
                    chat_id=str(container.get('channel_id') or (payload.get('channel') or {}).get('id') or ''),
                    sender_id=str((payload.get('user') or {}).get('id') or ''),
                    data=str(action.get('value') or ''),
                    message_ref=str(container.get('message_ts') or ''),
                ))

    def _blocks(self, text: str, buttons: list[Button] | None) -> list[dict[str, Any]] | None:
        if not buttons:
            return None
        elements = []
        for button in buttons:
            element: dict[str, Any] = {
                'type': 'button',
                'text': {'type': 'plain_text', 'text': button.label[:75]},
                'value': button.data,
                'action_id': button.data[:255],
            }
            if button.style in ('primary', 'danger'):
                element['style'] = button.style
            elements.append(element)
        return [
            {'type': 'section', 'text': {'type': 'mrkdwn', 'text': to_slack_mrkdwn(text)[:2900]}},
            {'type': 'actions', 'elements': elements},
        ]

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None) -> str:
        data = await self._call('chat.postMessage', channel=chat_id, text=to_slack_mrkdwn(text),
                                blocks=self._blocks(text, buttons), unfurl_links=False)
        return str(data.get('ts') or '')

    async def edit(self, chat_id: str, message_ref: str, text: str) -> None:
        try:
            await self._call('chat.update', channel=chat_id, ts=message_ref,
                             text=to_slack_mrkdwn(text), blocks=[])
        except SlackError as exc:
            logger.debug('Slack edit failed: %s', exc)

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
