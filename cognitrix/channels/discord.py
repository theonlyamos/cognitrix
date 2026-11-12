"""Discord bot over the Gateway websocket, so no public URL is needed.

Needs one bot token from the Discord Developer Portal. The bot only reads
direct messages, which arrive without the privileged "message content"
intent. Replies and buttons go through the REST API.
"""
from __future__ import annotations

import asyncio
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

logger = logging.getLogger('cognitrix.log')

API_BASE = 'https://discord.com/api/v10'
# DIRECT_MESSAGES only: the bot never sees what is said in servers.
INTENTS = 1 << 12
_BUTTON_STYLE = {'primary': 1, 'danger': 4}
# Close codes that mean the token or the app's setup is wrong.
_FATAL_CLOSE = {4004: 'Discord rejected the bot token', 4013: 'Discord rejected the connection settings',
                4014: 'Discord refused the permissions this bot asks for'}


class DiscordError(RuntimeError):
    def __init__(self, status: int, description: str):
        super().__init__(description or f'Discord error {status}')
        self.status = status


class DiscordTransport(Transport):
    kind = 'discord'
    text_limit = 1900  # Discord's cap is 2000

    def __init__(self, bot_token: str, *, session: aiohttp.ClientSession | None = None,
                 api_base: str = API_BASE):
        self._token = bot_token
        self._base = api_base
        self._session = session
        self._owns_session = session is None
        self._bot_user_id = ''
        self._seq: int | None = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), trust_env=True)
        return self._session

    async def _call(self, method: str, path: str, auth: bool = True, **body: Any) -> Any:
        headers = {'Authorization': f'Bot {self._token}'} if auth else {}
        payload = {k: v for k, v in body.items() if v is not None} or None
        for attempt in range(3):
            try:
                async with self._http().request(method, f'{self._base}{path}', json=payload,
                                                headers=headers) as response:
                    status = response.status
                    try:
                        data = await response.json(content_type=None)
                    except ValueError:
                        data = None
            except aiohttp.ClientError as exc:
                raise DiscordError(0, f'Could not reach Discord ({type(exc).__name__})') from None
            if status == 429 and attempt < 2:
                wait = float((data or {}).get('retry_after', 1)) if isinstance(data, dict) else 1.0
                await asyncio.sleep(min(max(wait, 0.2), 10))
                continue
            if status == 401:
                raise ChannelAuthError('Discord rejected the bot token')
            if status >= 400:
                message = str((data or {}).get('message') or '') if isinstance(data, dict) else ''
                raise DiscordError(status, f'Discord error: {message or status}')
            return data
        raise DiscordError(429, 'Discord is rate limiting this bot')

    async def check(self) -> BotInfo:
        me = await self._call('GET', '/users/@me')
        self._bot_user_id = str((me or {}).get('id') or '')
        name = str((me or {}).get('username') or 'bot')
        link = f'https://discord.com/users/{self._bot_user_id}' if self._bot_user_id else ''
        return BotInfo(name=f'@{name}', link_url=link)

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await ws.send_str(json.dumps({'op': 1, 'd': self._seq}))

    async def run(self, handlers: Handlers) -> None:
        while True:
            gateway = await self._call('GET', '/gateway/bot')
            url = str((gateway or {}).get('url') or 'wss://gateway.discord.gg')
            beat: asyncio.Task | None = None
            self._seq = None
            try:
                async with self._http().ws_connect(
                        f'{url}/?v=10&encoding=json', timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
                    async for frame in ws:
                        if frame.type != aiohttp.WSMsgType.TEXT:
                            if frame.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        try:
                            event = json.loads(frame.data)
                        except ValueError:
                            continue
                        if not await self._on_event(ws, event, handlers):
                            break
                        if event.get('op') == 10 and beat is None:
                            interval = float((event.get('d') or {}).get('heartbeat_interval', 41250)) / 1000
                            beat = asyncio.create_task(self._heartbeat(ws, interval))
                    if ws.close_code in _FATAL_CLOSE:
                        raise ChannelAuthError(_FATAL_CLOSE[ws.close_code])
            finally:
                if beat is not None:
                    beat.cancel()
            await asyncio.sleep(2)

    async def _on_event(self, ws: Any, event: dict[str, Any], handlers: Handlers) -> bool:
        """Handle one gateway event. False means reconnect."""
        op = event.get('op')
        if event.get('s') is not None:
            self._seq = event['s']
        if op == 10:  # hello
            await ws.send_str(json.dumps({'op': 2, 'd': {
                'token': self._token,
                'intents': INTENTS,
                'properties': {'os': 'linux', 'browser': 'cognitrix', 'device': 'cognitrix'},
            }}))
        elif op == 1:  # the gateway asks for a heartbeat now
            await ws.send_str(json.dumps({'op': 1, 'd': self._seq}))
        elif op in (7, 9):  # reconnect, or the session is no longer valid
            return False
        elif op == 0:
            try:
                await self._dispatch(str(event.get('t') or ''), event.get('d') or {}, handlers)
            except Exception:
                logger.exception('Discord event %s failed', event.get('t'))
        return True

    async def _dispatch(self, kind: str, data: dict[str, Any], handlers: Handlers) -> None:
        if kind == 'READY':
            self._bot_user_id = str((data.get('user') or {}).get('id') or self._bot_user_id)
            if handlers.on_connected is not None:
                await handlers.on_connected()
        elif kind == 'MESSAGE_CREATE':
            author = data.get('author') or {}
            if author.get('bot') or str(author.get('id') or '') in ('', self._bot_user_id):
                return
            text = str(data.get('content') or '')
            if not text:
                return
            await handlers.on_message(Incoming(
                chat_id=str(data.get('channel_id') or ''),
                sender_id=str(author.get('id') or ''),
                sender_name=str(author.get('global_name') or author.get('username') or ''),
                text=text,
                private=not data.get('guild_id'),
            ))
        elif kind == 'INTERACTION_CREATE' and data.get('type') == 3:
            user = data.get('user') or (data.get('member') or {}).get('user') or {}
            # Discord shows "interaction failed" unless it is answered within
            # three seconds, so acknowledge before doing the work.
            try:
                await self._call('POST', f"/interactions/{data.get('id')}/{data.get('token')}/callback",
                                 auth=False, type=6)
            except DiscordError as exc:
                logger.debug('Discord interaction ack failed: %s', exc)
            await handlers.on_button(ButtonPress(
                chat_id=str(data.get('channel_id') or ''),
                sender_id=str(user.get('id') or ''),
                data=str((data.get('data') or {}).get('custom_id') or ''),
                message_ref=str((data.get('message') or {}).get('id') or ''),
            ))

    @staticmethod
    def _components(buttons: list[Button] | None) -> list[dict[str, Any]] | None:
        if not buttons:
            return None
        return [{'type': 1, 'components': [
            {'type': 2, 'label': b.label[:80], 'style': _BUTTON_STYLE.get(b.style, 2), 'custom_id': b.data[:100]}
            for b in buttons[:5]
        ]}]

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None,
                   thread_id: str = '') -> str:
        # No pings from model output, whatever it writes.
        result = await self._call('POST', f'/channels/{chat_id}/messages', content=text[:2000] or '…',
                                  components=self._components(buttons), allowed_mentions={'parse': []})
        return str((result or {}).get('id') or '')

    async def edit(self, chat_id: str, message_ref: str, text: str) -> None:
        try:
            await self._call('PATCH', f'/channels/{chat_id}/messages/{message_ref}',
                             content=text[:2000], components=[])
        except DiscordError as exc:
            logger.debug('Discord edit failed: %s', exc)

    async def typing(self, chat_id: str) -> None:
        try:
            await self._call('POST', f'/channels/{chat_id}/typing')
        except Exception:
            pass

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None
