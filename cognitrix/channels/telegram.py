"""Telegram bot over long polling (getUpdates), so no public URL is needed."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .base import (
    BotInfo,
    Button,
    ButtonPress,
    ChannelAuthError,
    ChannelConflictError,
    Handlers,
    Incoming,
    Transport,
)
from .formatting import to_telegram_html

logger = logging.getLogger('cognitrix.log')

API_BASE = 'https://api.telegram.org'
POLL_SECONDS = 25


class TelegramError(RuntimeError):
    def __init__(self, status: int, description: str):
        super().__init__(description or f'Telegram error {status}')
        self.status = status


class TelegramTransport(Transport):
    kind = 'telegram'
    text_limit = 3800  # Telegram's cap is 4096 after formatting

    def __init__(self, bot_token: str, *, client: httpx.AsyncClient | None = None,
                 api_base: str = API_BASE):
        self._token = bot_token
        self._base = f'{api_base}/bot{bot_token}'
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(POLL_SECONDS + 15))
        self._owns_client = client is None
        self._offset = 0

    async def _call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        try:
            response = await self._client.post(f'{self._base}/{method}', json=payload)
        except httpx.HTTPError as exc:
            # Never let the URL (which holds the token) reach logs or the UI.
            raise TelegramError(0, f'Could not reach Telegram ({type(exc).__name__})') from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 401:
            raise ChannelAuthError('Telegram rejected the bot token')
        if response.status_code == 409:
            raise ChannelConflictError(
                'Another program is reading this bot\'s messages (or it has a webhook). '
                'Stop it, or create a separate bot for Cognitrix.')
        if not data.get('ok'):
            raise TelegramError(response.status_code, str(data.get('description') or ''))
        return data.get('result')

    async def check(self) -> BotInfo:
        me = await self._call('getMe')
        username = str((me or {}).get('username') or '')
        return BotInfo(name=f'@{username}' if username else 'bot',
                       link_url=f'https://t.me/{username}' if username else '')

    async def run(self, handlers: Handlers) -> None:
        # getUpdates refuses to run while a webhook is set.
        await self._call('deleteWebhook', drop_pending_updates=False)
        if handlers.on_connected is not None:
            await handlers.on_connected()
        while True:
            updates = await self._call(
                'getUpdates', offset=self._offset or None, timeout=POLL_SECONDS,
                allowed_updates=['message', 'callback_query'])
            for update in updates or []:
                self._offset = max(self._offset, int(update.get('update_id', 0)) + 1)
                try:
                    await self._dispatch(update, handlers)
                except Exception:
                    logger.exception('Telegram update %s failed', update.get('update_id'))

    async def _dispatch(self, update: dict[str, Any], handlers: Handlers) -> None:
        message = update.get('message')
        if message and isinstance(message.get('text'), str):
            chat = message.get('chat') or {}
            sender = message.get('from') or {}
            name = ' '.join(filter(None, [sender.get('first_name'), sender.get('last_name')]))
            await handlers.on_message(Incoming(
                chat_id=str(chat.get('id', '')),
                sender_id=str(sender.get('id', '')),
                sender_name=name or str(sender.get('username') or ''),
                text=message['text'],
                private=chat.get('type') == 'private',
            ))
            return
        query = update.get('callback_query')
        if query:
            msg = query.get('message') or {}
            await handlers.on_button(ButtonPress(
                chat_id=str((msg.get('chat') or {}).get('id', '')),
                sender_id=str((query.get('from') or {}).get('id', '')),
                data=str(query.get('data') or ''),
                message_ref=str(msg.get('message_id', '')),
                ack_id=str(query.get('id') or ''),
            ))

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None) -> str:
        markup = None
        if buttons:
            markup = {'inline_keyboard': [[{'text': b.label, 'callback_data': b.data} for b in buttons]]}
        try:
            result = await self._call('sendMessage', chat_id=chat_id, text=to_telegram_html(text),
                                      parse_mode='HTML', reply_markup=markup,
                                      link_preview_options={'is_disabled': True})
        except TelegramError as exc:
            if exc.status != 400:
                raise
            # Formatting Telegram couldn't parse: send it as plain text.
            result = await self._call('sendMessage', chat_id=chat_id, text=text, reply_markup=markup)
        return str((result or {}).get('message_id', ''))

    async def edit(self, chat_id: str, message_ref: str, text: str) -> None:
        try:
            await self._call('editMessageText', chat_id=chat_id, message_id=int(message_ref),
                             text=to_telegram_html(text), parse_mode='HTML')
        except (TelegramError, ValueError) as exc:
            logger.debug('Telegram edit failed: %s', exc)

    async def typing(self, chat_id: str) -> None:
        try:
            await self._call('sendChatAction', chat_id=chat_id, action='typing')
        except Exception:
            pass

    async def answer_button(self, press: ButtonPress, text: str = '') -> None:
        if not press.ack_id:
            return
        try:
            await self._call('answerCallbackQuery', callback_query_id=press.ack_id, text=text or None)
        except Exception:
            pass

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
