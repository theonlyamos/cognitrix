"""Text messages (SMS) with agents, over Twilio.

Like WhatsApp, Twilio calls the engine: each text sent to the Twilio number is
posted to ``/api/v1/channels/sms/<channel id>/webhook``, so the engine needs a
public HTTPS address. Replies are sent with Twilio's Messages API.

Needs a Twilio account: the Account SID, its Auth Token and a phone number
that can send and receive SMS. Every incoming call is signed with the Auth
Token (``X-Twilio-Signature``, HMAC-SHA1 over the URL and the sorted form
fields); nothing in a call is used before that signature has been checked.

SMS has no buttons and messages can't be edited, so choices (Approve / Deny,
Ask User options) are numbered in the text and the person replies with the
number or the word.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
from collections import OrderedDict

import aiohttp

from .base import BotInfo, Button, ButtonPress, ChannelAuthError, Handlers, Incoming, Transport

logger = logging.getLogger('cognitrix.log')

API_BASE = 'https://api.twilio.com/2010-04-01'
_SID_RE = re.compile(r'^AC[0-9a-fA-F]{32}$')
_PHONE_RE = re.compile(r'^\+[1-9]\d{6,14}$')
_SEEN_LIMIT = 500


class SmsError(RuntimeError):
    pass


class SmsAuthError(RuntimeError):
    """An incoming call that isn't a genuine, signed Twilio webhook."""


def split_app(value: str) -> tuple[str, str]:
    """``account sid|phone number`` as stored in ``ChatChannel.app_token``."""
    sid, _, phone = str(value or '').partition('|')
    return sid.strip(), phone.strip()


def normal_phone(value: str) -> str:
    """``+1 (555) 010-2000`` -> ``+15550102000``."""
    digits = re.sub(r'[^\d+]', '', value or '')
    return digits if digits.startswith('+') else (f'+{digits}' if digits else '')


def valid_phone(value: str) -> bool:
    return bool(_PHONE_RE.match(value or ''))


def valid_sid(value: str) -> bool:
    return bool(_SID_RE.match(value or ''))


def signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    data = url + ''.join(f'{key}{params[key]}' for key in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


_BOLD = re.compile(r'(\*\*|__)(.+?)\1', re.S)
_HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*#*$', re.M)
_LINK = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')
_BULLET = re.compile(r'^(\s*)[*+]\s+', re.M)
_FENCE = re.compile(r'```[a-zA-Z0-9_-]*\n?')


def to_sms(markdown: str) -> str:
    """Markdown as plain text: phones show the symbols literally."""
    text = _FENCE.sub('', markdown or '')
    text = _HEADING.sub(r'\1', text)
    text = _BOLD.sub(r'\2', text)
    text = _LINK.sub(r'\1 (\2)', text)
    text = _BULLET.sub(r'\1- ', text)
    return text.replace('`', '').strip()


class SmsTransport(Transport):
    kind = 'sms'
    text_limit = 1500
    """Twilio refuses bodies over 1600 characters; longer replies are split."""

    def __init__(self, auth_token: str, account_sid: str, phone: str, *,
                 session: aiohttp.ClientSession | None = None, api_base: str = API_BASE):
        self._token = auth_token
        self._sid = account_sid
        self._phone = phone
        self._base = api_base.rstrip('/')
        self._session = session
        self._owns_session = session is None
        self._handlers: Handlers | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._choices: dict[str, tuple[str, list[Button]]] = {}
        """chat -> (message ref, numbered choices) of the last message that offered some."""

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), trust_env=True)
        return self._session

    async def _call(self, method: str, path: str, form: dict[str, str] | None = None) -> dict:
        auth = aiohttp.BasicAuth(self._sid, self._token)
        try:
            async with self._http().request(method, f'{self._base}/Accounts/{self._sid}{path}',
                                            data=form, auth=auth) as response:
                data = await response.json(content_type=None)
                status = response.status
        except (aiohttp.ClientError, ValueError) as exc:
            raise SmsError(f'Could not reach Twilio ({type(exc).__name__})') from None
        data = data if isinstance(data, dict) else {}
        if status in (401, 403):
            raise ChannelAuthError('Twilio rejected the Account SID or Auth Token')
        if status >= 400:
            code = data.get('code')
            raise SmsError(f'Twilio error {status}' + (f' ({code}: {data.get("message")})' if code else ''))
        return data

    async def check(self) -> BotInfo:
        if not valid_sid(self._sid):
            raise ChannelAuthError('The Account SID starts with AC and has 34 characters')
        if not valid_phone(self._phone):
            raise ChannelAuthError('Give the Twilio number with its country code, like +15550102000')
        data = await self._call('GET', f'/IncomingPhoneNumbers.json?PhoneNumber={self._phone.replace("+", "%2B")}')
        if not data.get('incoming_phone_numbers'):
            raise ChannelAuthError(f'{self._phone} is not a number in this Twilio account')
        return BotInfo(name=self._phone, link_url=f'sms:{self._phone}')

    async def run(self, handlers: Handlers) -> None:
        # Nothing to connect to: Twilio calls ``receive`` through the API.
        self._handlers = handlers
        await self.check()
        if handlers.on_connected is not None:
            await handlers.on_connected()
        await asyncio.Event().wait()

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None,
                   thread_id: str = '') -> str:
        body = to_sms(text)
        if buttons:
            body += '\n\n' + '\n'.join(f'{i}. {b.label}' for i, b in enumerate(buttons, 1))
            body += '\nReply with a number.'
        data = await self._call('POST', '/Messages.json', {'From': self._phone, 'To': chat_id, 'Body': body or '...'})
        ref = str(data.get('sid') or '')
        if buttons:
            self._choices[chat_id] = (ref, list(buttons))
        return ref

    async def edit(self, chat_id: str, message_ref: str, text: str) -> None:
        """Texts can't be edited; ``answer_button`` says the outcome instead."""

    async def answer_button(self, press: ButtonPress, text: str = '') -> None:
        if text:
            try:
                await self.send(press.chat_id, text)
            except Exception as exc:
                logger.debug('SMS button answer failed: %s', exc)

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    # --- incoming: calls from Twilio ------------------------------------------------

    def verify(self, urls: list[str], sig: str, params: dict[str, str]) -> None:
        """Raise SmsAuthError unless one of the addresses this call may have been
        made to, signed with the Auth Token, gives ``sig``. (Behind a proxy the
        engine can't always tell which address Twilio used.)"""
        given = (sig or '').strip()
        if not self._token or not given:
            raise SmsAuthError('missing signature')
        for url in dict.fromkeys(u for u in urls if u):
            if hmac.compare_digest(signature(self._token, url, params), given):
                return
        raise SmsAuthError('bad signature')

    def _first_time(self, message_sid: str) -> bool:
        if not message_sid:
            return True
        if message_sid in self._seen:
            return False
        self._seen[message_sid] = None
        while len(self._seen) > _SEEN_LIMIT:
            self._seen.popitem(last=False)
        return True

    def _choice(self, chat_id: str, text: str) -> ButtonPress | None:
        """A reply that picks one of the numbered choices last offered to this chat."""
        offered = self._choices.get(chat_id)
        if offered is None:
            return None
        ref, buttons = offered
        word = text.strip().rstrip('.!').lower()
        picked = None
        if word.isdigit() and 1 <= int(word) <= len(buttons):
            picked = buttons[int(word) - 1]
        else:
            picked = next((b for b in buttons if b.label.strip().lower() == word), None)
        if picked is None:
            return None
        self._choices.pop(chat_id, None)
        return ButtonPress(chat_id=chat_id, sender_id=chat_id, data=picked.data, message_ref=ref)

    async def receive(self, urls: list[str], sig: str, params: dict[str, str]) -> None:
        """Handle one webhook call. Raises SmsAuthError if it isn't genuine."""
        self.verify(urls, sig, params)
        if self._handlers is None:
            return
        if params.get('AccountSid') != self._sid or normal_phone(params.get('To', '')) != self._phone:
            return
        sender = normal_phone(params.get('From', ''))
        if not sender or not self._first_time(params.get('MessageSid', '') or params.get('SmsSid', '')):
            return
        text = (params.get('Body') or '').strip()
        if not text:
            return
        press = self._choice(sender, text)
        if press is not None:
            await self._handlers.on_button(press)
            return
        await self._handlers.on_message(Incoming(chat_id=sender, sender_id=sender, sender_name=sender, text=text))
