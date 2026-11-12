"""WhatsApp bot over Meta's WhatsApp Business Cloud API.

Like Teams, WhatsApp calls the bot: Meta posts each message to a webhook, so
the engine needs a public HTTPS address. The webhook is
``/api/v1/channels/whatsapp/<channel id>/webhook``.

Needs a Meta app with the WhatsApp product: the phone number ID, an access
token that can send from it, and the app secret.

Every incoming call is signed with the app secret (``X-Hub-Signature-256``,
HMAC-SHA256 over the raw body); nothing in a call is used before that
signature has been checked.

Two WhatsApp rules shape what the bot can do:

* a business may only write freely to someone for 24 hours after that
  person's last message, so an alert sent later than that is refused by
  WhatsApp (``send`` raises ``WhatsAppWindowClosed``),
* messages can't be edited, so the outcome of a button is sent as a reply.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from collections import OrderedDict
from typing import Any

import aiohttp

from .base import (
    BotInfo,
    Button,
    ButtonPress,
    ChannelAuthError,
    ChannelUndeliverable,
    Handlers,
    Incoming,
    Transport,
)

logger = logging.getLogger('cognitrix.log')

GRAPH_BASE = 'https://graph.facebook.com/v21.0'
MAX_REPLY_BUTTONS = 3
BUTTON_TITLE = 20
ROW_TITLE = 24
_SEEN_LIMIT = 500
# Error codes that mean "outside the 24 hour window".
_WINDOW_CODES = {131047, 131026}
_ID_RE = re.compile(r'^\d{5,30}$')


class WhatsAppError(RuntimeError):
    pass


class WhatsAppWindowClosed(WhatsAppError, ChannelUndeliverable):
    """WhatsApp only lets a business reply within 24 hours of the person's last message."""


class WhatsAppAuthError(RuntimeError):
    """An incoming call that isn't a genuine, signed Meta webhook."""


def split_app(value: str) -> tuple[str, str]:
    """``phone number id|app secret`` as stored in ``ChatChannel.app_token``."""
    phone_id, _, secret = str(value or '').partition('|')
    return phone_id.strip(), secret.strip()


def verify_token(app_secret: str, channel_id: str) -> str:
    """The "Verify token" to type into Meta's webhook form for this channel."""
    return hmac.new(app_secret.encode(), f'verify:{channel_id}'.encode(), hashlib.sha256).hexdigest()[:32]


_BOLD = re.compile(r'\*\*(.+?)\*\*', re.S)
_HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*#*$', re.M)
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')
_BULLET = re.compile(r'^(\s*)[*+]\s+', re.M)


def to_whatsapp(markdown: str) -> str:
    """Markdown as WhatsApp shows it: *bold*, _italic_, ```code```."""
    parts = re.split(r'(```.*?```|`[^`\n]+`)', markdown or '', flags=re.S)
    for index in range(0, len(parts), 2):
        text = _BULLET.sub(r'\1- ', parts[index])
        text = _HEADING.sub(r'**\1**', text)
        text = _BOLD.sub(r'*\1*', text)
        parts[index] = _LINK.sub(r'\1 (\2)', text)
    return ''.join(parts)


class WhatsAppTransport(Transport):
    kind = 'whatsapp'
    text_limit = 3800

    def __init__(self, access_token: str, phone_id: str, app_secret: str, *,
                 session: aiohttp.ClientSession | None = None, graph_base: str = GRAPH_BASE):
        self._token = access_token
        self._phone_id = phone_id
        self._secret = app_secret
        self._base = graph_base.rstrip('/')
        self._session = session
        self._owns_session = session is None
        self._handlers: Handlers | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), trust_env=True)
        return self._session

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {'Authorization': f'Bearer {self._token}'}
        try:
            async with self._http().request(method, f'{self._base}{path}', json=body, headers=headers) as response:
                data = await response.json(content_type=None)
                status = response.status
        except (aiohttp.ClientError, ValueError) as exc:
            raise WhatsAppError(f'Could not reach WhatsApp ({type(exc).__name__})') from None
        data = data if isinstance(data, dict) else {}
        if status >= 400:
            error = data.get('error') if isinstance(data.get('error'), dict) else {}
            code = error.get('code')
            if status == 401 or code == 190:
                raise ChannelAuthError('WhatsApp rejected the access token')
            if code in _WINDOW_CODES:
                raise WhatsAppWindowClosed(
                    'WhatsApp only delivers this within 24 hours of your last message to the bot')
            raise WhatsAppError(f'WhatsApp error {status}' + (f' ({code})' if code else ''))
        return data

    async def check(self) -> BotInfo:
        if not _ID_RE.match(self._phone_id):
            raise ChannelAuthError('The phone number ID is the long number from the API Setup page, not the phone number')
        if not self._secret:
            raise ChannelAuthError('Paste the app secret too')
        try:
            data = await self._call('GET', f'/{self._phone_id}?fields=display_phone_number,verified_name')
        except WhatsAppError as exc:
            if 'error 4' in str(exc):  # 400/403/404: wrong id or a token for another number
                raise ChannelAuthError("WhatsApp doesn't know that phone number ID with this token") from None
            raise
        number = str(data.get('display_phone_number') or '')
        digits = re.sub(r'\D', '', number)
        return BotInfo(name=number or str(data.get('verified_name') or 'WhatsApp'),
                       link_url=f'https://wa.me/{digits}' if digits else '')

    async def run(self, handlers: Handlers) -> None:
        # Nothing to connect to: Meta calls ``receive`` through the API.
        self._handlers = handlers
        await self.check()
        if handlers.on_connected is not None:
            await handlers.on_connected()
        await asyncio.Event().wait()

    def _message(self, chat_id: str, text: str, buttons: list[Button] | None) -> dict[str, Any]:
        base = {'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': chat_id}
        body = to_whatsapp(text)
        if not buttons:
            return {**base, 'type': 'text', 'text': {'body': body, 'preview_url': False}}
        if len(buttons) <= MAX_REPLY_BUTTONS:
            action: dict[str, Any] = {'buttons': [
                {'type': 'reply', 'reply': {'id': button.data[:256], 'title': button.label[:BUTTON_TITLE]}}
                for button in buttons
            ]}
            kind = 'button'
        else:
            action = {'button': 'Choose', 'sections': [{'title': 'Options', 'rows': [
                {'id': button.data[:200], 'title': button.label[:ROW_TITLE]} for button in buttons[:10]
            ]}]}
            kind = 'list'
        return {**base, 'type': 'interactive',
                'interactive': {'type': kind, 'body': {'text': body[:1024] or '...'}, 'action': action}}

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None,
                   thread_id: str = '') -> str:
        data = await self._call('POST', f'/{self._phone_id}/messages', self._message(chat_id, text, buttons))
        messages = data.get('messages') or []
        return str(messages[0].get('id') or '') if messages and isinstance(messages[0], dict) else ''

    async def edit(self, chat_id: str, message_ref: str, text: str) -> None:
        """WhatsApp messages can't be edited; ``answer_button`` says the outcome instead."""

    async def answer_button(self, press: ButtonPress, text: str = '') -> None:
        if text:
            try:
                await self.send(press.chat_id, text)
            except WhatsAppError as exc:
                logger.debug('WhatsApp button answer failed: %s', exc)

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    # --- incoming: calls from Meta ---------------------------------------------------

    def verify(self, signature: str, raw: bytes) -> None:
        """Raise WhatsAppAuthError unless ``raw`` was signed with this app's secret."""
        scheme, _, digest = str(signature or '').partition('=')
        expected = hmac.new(self._secret.encode(), raw, hashlib.sha256).hexdigest()
        if scheme != 'sha256' or not self._secret or not hmac.compare_digest(digest.strip().lower(), expected):
            raise WhatsAppAuthError('bad signature')

    def _first_time(self, message_id: str) -> bool:
        # Meta sends a message again when it isn't sure the first call arrived.
        if not message_id:
            return True
        if message_id in self._seen:
            return False
        self._seen[message_id] = None
        while len(self._seen) > _SEEN_LIMIT:
            self._seen.popitem(last=False)
        return True

    async def receive(self, signature: str, raw: bytes) -> None:
        """Handle one webhook call. Raises WhatsAppAuthError if it isn't genuine."""
        self.verify(signature, raw)
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        if self._handlers is None or not isinstance(payload, dict):
            return
        for entry in payload.get('entry') or []:
            for change in (entry or {}).get('changes') or []:
                value = (change or {}).get('value') or {}
                if str((value.get('metadata') or {}).get('phone_number_id') or '') != self._phone_id:
                    continue  # another number of the same Meta app
                names = {str(c.get('wa_id') or ''): str((c.get('profile') or {}).get('name') or '')
                         for c in value.get('contacts') or [] if isinstance(c, dict)}
                for message in value.get('messages') or []:
                    await self._message_in(message, names)

    async def _message_in(self, message: dict[str, Any], names: dict[str, str]) -> None:
        sender = str(message.get('from') or '')
        if not sender or message.get('group_id') or not self._first_time(str(message.get('id') or '')):
            return
        kind = message.get('type')
        if kind == 'interactive':
            interactive = message.get('interactive') or {}
            reply = interactive.get('button_reply') or interactive.get('list_reply') or {}
            if reply.get('id'):
                await self._handlers.on_button(ButtonPress(
                    chat_id=sender, sender_id=sender, data=str(reply['id']),
                    message_ref=str((message.get('context') or {}).get('id') or ''),
                ))
            return
        if kind == 'button':  # a template's quick reply
            text = str((message.get('button') or {}).get('text') or '')
        elif kind == 'text':
            text = str((message.get('text') or {}).get('body') or '')
        else:
            await self._safe_send(sender, 'I can only read text messages here for now.')
            return
        if text.strip():
            await self._handlers.on_message(Incoming(
                chat_id=sender, sender_id=sender, sender_name=names.get(sender, ''), text=text.strip(),
            ))

    async def _safe_send(self, chat_id: str, text: str) -> None:
        try:
            await self.send(chat_id, text)
        except Exception as exc:
            logger.debug('WhatsApp reply failed: %s', exc)
