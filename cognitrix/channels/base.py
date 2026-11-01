"""Shared types for chat app transports.

A transport owns the connection to one bot (Telegram long polling, Slack
Socket Mode). It turns what arrives into ``Incoming`` / ``ButtonPress`` and
hands them to the service; the service answers through ``send`` / ``edit``.
Neither side needs a public URL, so this works from the desktop app too.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


class ChannelAuthError(RuntimeError):
    """The chat app rejected the token: retrying won't help."""


class ChannelConflictError(RuntimeError):
    """Another program is reading this bot's messages."""


@dataclass(frozen=True)
class Button:
    label: str
    data: str
    style: str = ''
    """'primary' or 'danger'; Telegram ignores it."""


@dataclass(frozen=True)
class Incoming:
    chat_id: str
    sender_id: str
    sender_name: str
    text: str
    private: bool = True


@dataclass(frozen=True)
class ButtonPress:
    chat_id: str
    sender_id: str
    data: str
    message_ref: str
    ack_id: str = ''
    """Telegram callback id to answer; '' for Slack."""


@dataclass(frozen=True)
class BotInfo:
    name: str
    """Username / handle shown to the person."""
    link_url: str = ''
    """A link that opens a chat with the bot, when the app has one."""


@dataclass
class Handlers:
    on_message: Callable[[Incoming], Awaitable[None]]
    on_button: Callable[[ButtonPress], Awaitable[None]]
    on_connected: Callable[[], Awaitable[None]] = field(default=None)  # type: ignore[assignment]


class Transport:
    """Interface every chat app transport implements."""

    kind = ''
    text_limit = 3500

    async def check(self) -> BotInfo:
        """Validate the tokens; raise ChannelAuthError if they're wrong."""
        raise NotImplementedError

    async def run(self, handlers: Handlers) -> None:
        """Receive messages until cancelled. Raises on fatal errors."""
        raise NotImplementedError

    async def send(self, chat_id: str, text: str, buttons: list[Button] | None = None) -> str:
        """Send Markdown text; returns a reference ``edit`` accepts."""
        raise NotImplementedError

    async def edit(self, chat_id: str, message_ref: str, text: str) -> None:
        """Replace a message's text and drop its buttons."""
        raise NotImplementedError

    async def typing(self, chat_id: str) -> None:
        """Show a typing indicator if the app has one."""

    async def answer_button(self, press: ButtonPress, text: str = '') -> None:
        """Acknowledge a button press if the app needs it."""

    async def close(self) -> None:
        """Release network resources."""
