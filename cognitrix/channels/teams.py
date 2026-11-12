"""Microsoft Teams bot over the Bot Framework connector.

Unlike Telegram, Slack and Discord, Teams does not offer a connection the
engine can open from behind a firewall: Teams calls the bot. The engine
therefore needs a public HTTPS address, and the bot's messaging endpoint in
Azure points at ``/api/v1/channels/teams/<channel id>/messages``.

Needs an Azure Bot resource with a single-tenant app: its Microsoft App ID,
a client secret, and the tenant id.

Every incoming call carries a token signed by the Bot Framework. It is
checked as Microsoft's "Authenticate requests" document requires (issuer,
audience, signature, validity, serviceUrl claim, channel endorsement) before
anything in it is used.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
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

logger = logging.getLogger('cognitrix.log')

LOGIN_BASE = 'https://login.microsoftonline.com'
OPENID_URL = 'https://login.botframework.com/v1/.well-known/openidconfiguration'
ISSUER = 'https://api.botframework.com'
SCOPE = 'https://api.botframework.com/.default'
CHANNEL_ID = 'msteams'
KEYS_TTL = 24 * 3600
KEYS_MIN_REFRESH = 300
CLOCK_SKEW = 300
_ID_RE = re.compile(r'^[0-9a-fA-F-]{36}$')
_AT_RE = re.compile(r'<at>.*?</at>', re.S)


class TeamsError(RuntimeError):
    pass


class TeamsAuthError(RuntimeError):
    """An incoming call that isn't a genuine Bot Framework request."""


def split_app(value: str) -> tuple[str, str]:
    """``app id|tenant id`` as stored in ``ChatChannel.app_token``."""
    app_id, _, tenant = str(value or '').partition('|')
    return app_id.strip(), tenant.strip()


class TeamsTransport(Transport):
    kind = 'teams'
    text_limit = 3500

    def __init__(self, app_id: str, app_secret: str, tenant_id: str, *, service_url: str = '',
                 on_service_url: Any = None, session: aiohttp.ClientSession | None = None):
        self._app_id = app_id
        self._secret = app_secret
        self._tenant = tenant_id
        self._session = session
        self._owns_session = session is None
        self._handlers: Handlers | None = None
        self._token = ''
        self._token_expires = 0.0
        self._keys: dict[str, dict[str, Any]] = {}
        self._keys_at = 0.0
        self._service_urls: dict[str, str] = {}
        self._default_service_url = service_url
        self._on_service_url = on_service_url

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), trust_env=True)
        return self._session

    # --- outgoing: the bot's own token -------------------------------------------

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        if not _ID_RE.match(self._app_id) or not _ID_RE.match(self._tenant):
            raise ChannelAuthError('The App ID and the tenant ID are both 36-character ids from Azure')
        form = {'grant_type': 'client_credentials', 'client_id': self._app_id,
                'client_secret': self._secret, 'scope': SCOPE}
        try:
            async with self._http().post(f'{LOGIN_BASE}/{self._tenant}/oauth2/v2.0/token', data=form) as response:
                data = await response.json(content_type=None)
                status = response.status
        except (aiohttp.ClientError, ValueError) as exc:
            raise TeamsError(f'Could not reach Microsoft ({type(exc).__name__})') from None
        if status in (400, 401) or not isinstance(data, dict) or not data.get('access_token'):
            reason = str((data or {}).get('error') or 'rejected') if isinstance(data, dict) else 'rejected'
            if status in (400, 401):
                raise ChannelAuthError(f'Microsoft rejected the App ID, secret or tenant ({reason})')
            raise TeamsError(f'Microsoft sign-in failed ({status})')
        self._token = str(data['access_token'])
        self._token_expires = time.time() + float(data.get('expires_in') or 3600)
        return self._token

    async def _call(self, method: str, chat_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        base = (self._service_urls.get(chat_id) or self._default_service_url).rstrip('/')
        if not base:
            raise TeamsError('Teams has not contacted this bot yet, so there is nowhere to send to')
        headers = {'Authorization': f'Bearer {await self._access_token()}'}
        try:
            async with self._http().request(method, f'{base}{path}', json=body, headers=headers) as response:
                if response.status >= 400:
                    raise TeamsError(f'Teams error {response.status}')
                data = await response.json(content_type=None) if response.content_length != 0 else {}
        except (aiohttp.ClientError, ValueError) as exc:
            raise TeamsError(f'Could not reach Teams ({type(exc).__name__})') from None
        return data if isinstance(data, dict) else {}

    async def check(self) -> BotInfo:
        await self._access_token()
        return BotInfo(name='Teams bot')

    async def run(self, handlers: Handlers) -> None:
        # Nothing to connect to: Teams calls ``receive`` through the API.
        self._handlers = handlers
        await self._access_token()
        if handlers.on_connected is not None:
            await handlers.on_connected()
        await asyncio.Event().wait()

    def _activity(self, text: str, buttons: list[Button] | None) -> dict[str, Any]:
        if not buttons:
            return {'type': 'message', 'textFormat': 'markdown', 'text': text}
        card = {
            'type': 'AdaptiveCard', 'version': '1.4',
            '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
            'body': [{'type': 'TextBlock', 'text': text, 'wrap': True}],
            'actions': [
                {'type': 'Action.Submit', 'title': button.label[:60], 'data': {'cx': button.data},
                 **({'style': 'positive'} if button.style == 'primary' else {}),
                 **({'style': 'destructive'} if button.style == 'danger' else {})}
                for button in buttons
            ],
        }
        return {'type': 'message', 'attachments': [
            {'contentType': 'application/vnd.microsoft.card.adaptive', 'content': card}]}

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None,
                   thread_id: str = '') -> str:
        data = await self._call('POST', chat_id, f'/v3/conversations/{_quote(chat_id)}/activities',
                                self._activity(text, buttons))
        return str(data.get('id') or '')

    async def edit(self, chat_id: str, message_ref: str, text: str) -> None:
        try:
            await self._call('PUT', chat_id,
                             f'/v3/conversations/{_quote(chat_id)}/activities/{_quote(message_ref)}',
                             self._activity(text, None))
        except TeamsError as exc:
            logger.debug('Teams edit failed: %s', exc)

    async def typing(self, chat_id: str) -> None:
        try:
            await self._call('POST', chat_id, f'/v3/conversations/{_quote(chat_id)}/activities', {'type': 'typing'})
        except TeamsError:
            pass

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    # --- incoming: calls from the Bot Framework -----------------------------------

    async def _signing_keys(self, force: bool = False) -> dict[str, dict[str, Any]]:
        age = time.time() - self._keys_at
        if self._keys and age < KEYS_TTL and not (force and age > KEYS_MIN_REFRESH):
            return self._keys
        try:
            async with self._http().get(OPENID_URL) as response:
                config = await response.json(content_type=None)
            async with self._http().get(str(config['jwks_uri'])) as response:
                document = await response.json(content_type=None)
        except (aiohttp.ClientError, ValueError, KeyError, TypeError) as exc:
            if self._keys:
                return self._keys
            raise TeamsAuthError(f'Could not load the Bot Framework signing keys ({type(exc).__name__})') from None
        self._keys = {str(key.get('kid')): key for key in document.get('keys') or [] if key.get('kid')}
        self._keys_at = time.time()
        return self._keys

    async def verify(self, authorization: str, activity: dict[str, Any]) -> None:
        """Raise TeamsAuthError unless this call really comes from Teams for this bot."""
        import jwt

        scheme, _, token = str(authorization or '').partition(' ')
        if scheme.lower() != 'bearer' or not token.strip():
            raise TeamsAuthError('missing bearer token')
        token = token.strip()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TeamsAuthError('unreadable token') from None
        kid = str(header.get('kid') or '')
        keys = await self._signing_keys()
        if kid not in keys:
            keys = await self._signing_keys(force=True)
        key = keys.get(kid)
        if key is None:
            raise TeamsAuthError('unknown signing key')
        try:
            claims = jwt.decode(
                token, jwt.PyJWK.from_dict(key).key, algorithms=['RS256'],
                audience=self._app_id, issuer=ISSUER, leeway=CLOCK_SKEW,
                options={'require': ['exp', 'iss', 'aud']},
            )
        except jwt.PyJWTError as exc:
            raise TeamsAuthError(f'token rejected ({type(exc).__name__})') from None
        service_url = str(activity.get('serviceUrl') or '')
        if not service_url or str(claims.get('serviceurl') or claims.get('serviceUrl') or '') != service_url:
            raise TeamsAuthError('serviceUrl does not match the token')
        if not service_url.lower().startswith('https://'):
            raise TeamsAuthError('serviceUrl is not https')
        if activity.get('channelId') != CHANNEL_ID or CHANNEL_ID not in (key.get('endorsements') or []):
            raise TeamsAuthError('not a Teams channel request')
        tenant = str(((activity.get('channelData') or {}).get('tenant') or {}).get('id')
                     or (activity.get('conversation') or {}).get('tenantId') or '')
        if tenant.lower() != self._tenant.lower():
            raise TeamsAuthError('request from another organisation')

    async def receive(self, authorization: str, activity: dict[str, Any]) -> None:
        """Handle one activity Teams posted. Raises TeamsAuthError if it isn't genuine."""
        await self.verify(authorization, activity)
        if self._handlers is None or activity.get('type') != 'message':
            return
        conversation = activity.get('conversation') or {}
        chat_id = str(conversation.get('id') or '')
        sender = activity.get('from') or {}
        sender_id = str(sender.get('aadObjectId') or sender.get('id') or '')
        if not chat_id or not sender_id:
            return
        service_url = str(activity['serviceUrl'])
        self._service_urls[chat_id] = service_url
        if service_url != self._default_service_url:
            self._default_service_url = service_url
            if self._on_service_url is not None:
                try:
                    await self._on_service_url(service_url)
                except Exception:
                    logger.exception('Could not save the Teams service URL')

        value = activity.get('value')
        if isinstance(value, dict) and value.get('cx'):
            await self._handlers.on_button(ButtonPress(
                chat_id=chat_id, sender_id=sender_id, data=str(value['cx']),
                message_ref=str(activity.get('replyToId') or ''),
            ))
            return
        text = _AT_RE.sub(' ', str(activity.get('text') or '')).replace('&nbsp;', ' ')
        text = ' '.join(text.split()) if '\n' not in text else text.strip()
        if not text:
            return
        personal = str(conversation.get('conversationType') or '') == 'personal'
        await self._handlers.on_message(Incoming(
            chat_id=chat_id, sender_id=sender_id, sender_name=str(sender.get('name') or ''),
            text=text, private=personal, mention=not personal,
        ))


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe='')
