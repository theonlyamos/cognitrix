"""Runs every connected chat app bot and links it to its owner's agents.

* One background task per channel keeps the bot connected (Telegram long
  polling, Slack Socket Mode, the Discord Gateway) and restarts it after network errors.
  Microsoft Teams and WhatsApp call the API instead (``deliver_teams``,
  ``deliver_whatsapp``), which needs a public URL.
* In Slack and Teams the bot also answers @mentions in shared channels, in
  the thread (``_on_mention``): the linked person as usual, other people
  only if the person allowed it, and then read-only.
* A message from the linked chat starts a chat turn with the channel's agent,
  in the same kind of conversation the web UI uses, so it also shows there.
  Risky tool calls and Ask User questions arrive as buttons in the chat.
* A delivery pass forwards new Notify Me alerts and parked task approvals
  from the Inbox. Approvals can be answered with the buttons.

Only the chat that sent the link code shown in Settings is ever answered.
Bots run in the API process (the desktop app's engine, or the web server).
Several API processes would each poll the same bot, so ``COGNITRIX_CHANNELS=0``
turns them off where that matters.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from cognitrix.models.chat_channel import ChannelDelivery, ChannelThread, ChatChannel

from .base import (
    Button,
    ButtonPress,
    ChannelAuthError,
    ChannelConflictError,
    ChannelUndeliverable,
    Handlers,
    Incoming,
    Transport,
)
from .formatting import split_text, strip_reasoning

logger = logging.getLogger('cognitrix.log')

KINDS = ('telegram', 'slack', 'discord', 'teams', 'whatsapp')
# Apps that call the engine (it needs a public https address) instead of the engine connecting out.
WEBHOOK_KINDS = ('teams', 'whatsapp')
# Apps where a person's id is the same in every chat, so someone who
# @mentions the bot in a shared channel can be told apart from its owner.
MENTION_KINDS = ('slack', 'teams')
STEER_ACK = 'Got it. I will read this as soon as the current step finishes.'
GUEST_REASON = 'this question came from someone other than your person, so you may only look things up'
LINK_CODE_MINUTES = 30
MAX_LINK_ATTEMPTS = 10
DELIVERY_INTERVAL = 4.0
TYPING_INTERVAL = 4.5
_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_COMMAND = re.compile(r'^[/!]([a-z_]+)(?:@\S+)?\s*(.*)$', re.S | re.I)


def channels_enabled() -> bool:
    return os.getenv('COGNITRIX_CHANNELS', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def new_link_code() -> tuple[str, str]:
    """(code, expiry) for linking a chat. 10 characters, about 50 bits."""
    from cognitrix.tasks.automation import FMT, utcnow

    code = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
    expires = (utcnow() + timedelta(minutes=LINK_CODE_MINUTES)).strftime(FMT)
    return code, expires


def make_transport(channel: ChatChannel) -> Transport:
    if channel.kind == 'telegram':
        from .telegram import API_BASE, TelegramTransport
        # A self-hosted Bot API server can stand in for api.telegram.org.
        base = os.getenv('COGNITRIX_TELEGRAM_API_BASE', '').strip().rstrip('/') or API_BASE
        return TelegramTransport(channel.bot_token, api_base=base)
    if channel.kind == 'slack':
        from .slack import SlackTransport
        return SlackTransport(channel.bot_token, channel.app_token)
    if channel.kind == 'discord':
        from .discord import DiscordTransport
        return DiscordTransport(channel.bot_token)
    if channel.kind == 'teams':
        from .teams import TeamsTransport, split_app

        app_id, tenant = split_app(channel.app_token)
        channel_id = channel.id

        async def remember(url: str) -> None:
            if channel_id:
                await ChatChannel.update_one({'id': channel_id}, {'service_url': url})

        return TeamsTransport(app_id, channel.bot_token, tenant,
                              service_url=channel.service_url, on_service_url=remember)
    if channel.kind == 'whatsapp':
        from .whatsapp import WhatsAppTransport, split_app

        phone_id, secret = split_app(channel.app_token)
        return WhatsAppTransport(channel.bot_token, phone_id, secret)
    raise ValueError(f'Unknown chat app {channel.kind!r}')


HELP_TEXT = (
    'Send a message to chat with {agent}.\n\n'
    'Commands:\n'
    '{p}new - start a new conversation\n'
    '{p}agents - list your agents\n'
    '{p}agent NAME - switch agent\n'
    '{p}stop - stop the current reply\n'
    '{p}help - show this list'
)


def _prefix(kind: str) -> str:
    # Slack and Discord treat "/word" as their own slash command, so people
    # type "!word" there.
    return '!' if kind in ('slack', 'discord') else '/'


def _params_preview(params: Any, limit: int = 600) -> str:
    import json

    try:
        text = json.dumps(params, ensure_ascii=False, indent=1, default=str)
    except (TypeError, ValueError):
        text = str(params)
    return text if len(text) <= limit else text[:limit] + '…'


class ChannelService:
    def __init__(self, transport_factory: Callable[[ChatChannel], Transport] = make_transport):
        self._factory = transport_factory
        self._runners: dict[str, asyncio.Task] = {}
        self._transports: dict[str, Transport] = {}
        self._connected: set[str] = set()
        self._turns: dict[str, asyncio.Task] = {}
        # The chat run behind each running turn, for steering (see _steer).
        self._turn_runs: dict[str, Any] = {}
        self._questions: dict[str, str] = {}
        """channel id -> request id of an Ask User question waiting for a reply."""
        self._guest_turns: set[str] = set()
        """Thread keys whose running turn was started by someone other than the linked person."""
        self._button_text: dict[tuple[str, str, str], str] = {}
        """(channel id, chat id, message ref) -> text of a message with buttons."""
        self._link_failures: dict[str, int] = {}
        self._delivered: dict[str, set[tuple[str, str]]] = {}
        self._delivery_task: asyncio.Task | None = None

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        for channel in await ChatChannel.all() or []:
            if channel.enabled and channel.bot_token:
                self._start_runner(channel.id)
        if self._delivery_task is None:
            self._delivery_task = asyncio.create_task(self._delivery_loop())

    async def stop(self) -> None:
        tasks = [*self._runners.values(), *self._turns.values()]
        if self._delivery_task is not None:
            tasks.append(self._delivery_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._runners.clear()
        self._turns.clear()
        self._delivery_task = None

    async def reload(self, channel_id: str) -> None:
        """Restart a channel's bot after its settings changed (or stop it)."""
        await self._stop_runner(channel_id)
        channel = await ChatChannel.get(channel_id)
        if channel is not None and channel.enabled and channel.bot_token:
            self._start_runner(channel_id)

    async def cancel_user_turns(self, user_key: str) -> None:
        """Stop every running reply of one person's channels (account disabled)."""
        for channel in await ChatChannel.find({'user_id': str(user_key)}) or []:
            await self.cancel_turn(channel.id)

    async def cancel_turn(self, channel_id: str) -> None:
        """Stop the running reply, e.g. because the chat it answers was unlinked."""
        for key in [k for k in self._turns if k == channel_id or k.startswith(channel_id + '|')]:
            turn = self._turns.pop(key, None)
            if turn is not None and not turn.done():
                turn.cancel()
                try:
                    await turn
                except (asyncio.CancelledError, Exception):
                    pass

    async def deliver_teams(self, channel_id: str, authorization: str, activity: dict[str, Any]) -> None:
        """Hand over an activity Microsoft Teams posted to the API.

        Raises LookupError when that bot isn't running here and
        ``TeamsAuthError`` when the request isn't genuinely from Teams.
        """
        transport = self._transports.get(channel_id)
        if transport is None or getattr(transport, 'kind', '') != 'teams':
            raise LookupError(channel_id)
        await transport.receive(authorization, activity)  # type: ignore[attr-defined]

    async def deliver_whatsapp(self, channel_id: str, signature: str, raw: bytes) -> None:
        """Hand over a webhook call from WhatsApp. Raises LookupError when that
        bot isn't running here and ``WhatsAppAuthError`` when the signature is wrong."""
        transport = self._transports.get(channel_id)
        if transport is None or getattr(transport, 'kind', '') != 'whatsapp':
            raise LookupError(channel_id)
        await transport.receive(signature, raw)  # type: ignore[attr-defined]

    async def _still_linked(self, channel: ChatChannel) -> bool:
        fresh = await ChatChannel.get(channel.id)
        return fresh is not None and fresh.chat_id == channel.chat_id and fresh.peer_id == channel.peer_id

    def is_connected(self, channel_id: str) -> bool:
        return channel_id in self._connected

    def _start_runner(self, channel_id: str) -> None:
        if channel_id not in self._runners:
            self._runners[channel_id] = asyncio.create_task(self._run_channel(channel_id))

    async def _stop_runner(self, channel_id: str) -> None:
        task = self._runners.pop(channel_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.cancel_turn(channel_id)
        self._connected.discard(channel_id)

    async def _set_status(self, channel_id: str, status: str, error: str = '') -> None:
        try:
            await ChatChannel.update_one({'id': channel_id}, {'status': status, 'last_error': error[:300]})
        except Exception:
            logger.exception('Could not save status of chat channel %s', channel_id)

    async def _run_channel(self, channel_id: str) -> None:
        backoff = 2.0
        while True:
            channel = await ChatChannel.get(channel_id)
            if channel is None or not channel.enabled or not channel.bot_token:
                return
            transport = self._factory(channel)
            self._transports[channel_id] = transport

            async def connected() -> None:
                nonlocal backoff
                backoff = 2.0
                self._connected.add(channel_id)
                await self._set_status(channel_id, 'connected')

            await self._set_status(channel_id, 'connecting')
            try:
                await transport.run(Handlers(
                    on_message=lambda msg: self._on_message(channel_id, msg),
                    on_button=lambda press: self._on_button(channel_id, press),
                    on_connected=connected,
                ))
                wait = 1.0  # the app closed the connection; reconnect
            except asyncio.CancelledError:
                raise
            except ChannelAuthError as exc:
                await self._set_status(channel_id, 'error', str(exc))
                return
            except ChannelConflictError as exc:
                await self._set_status(channel_id, 'error', str(exc))
                wait = 60.0
            except Exception as exc:
                logger.warning('Chat channel %s disconnected: %s', channel_id, exc)
                await self._set_status(channel_id, 'error', str(exc) or type(exc).__name__)
                wait = backoff
                backoff = min(backoff * 2, 60.0)
            finally:
                self._connected.discard(channel_id)
                self._transports.pop(channel_id, None)
                try:
                    await transport.close()
                except Exception:
                    pass
            await asyncio.sleep(wait)

    # --- incoming ------------------------------------------------------------

    async def _reply(self, channel: ChatChannel, chat_id: str, text: str,
                     buttons: list[Button] | None = None, thread_id: str = '') -> str:
        transport = self._transports.get(channel.id)
        if transport is None:
            return ''
        ref = ''
        parts = split_text(text, transport.text_limit) or ['']
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            ref = await transport.send(chat_id, part, buttons if last else None,
                                       **({'thread_id': thread_id} if thread_id else {}))
        if buttons and ref:
            self._button_text[(channel.id, chat_id, ref)] = parts[-1]
        return ref

    async def send(self, channel: ChatChannel, chat_id: str, text: str) -> str:
        return await self._reply(channel, chat_id, text)

    def _is_owner(self, channel: ChatChannel, chat_id: str, sender_id: str) -> bool:
        return bool(channel.chat_id) and chat_id == channel.chat_id and sender_id == channel.peer_id

    def _is_person(self, channel: ChatChannel, sender_id: str) -> bool:
        """The linked person, wherever in the app they wrote from."""
        return (channel.kind in MENTION_KINDS and bool(channel.chat_id)
                and bool(channel.peer_id) and sender_id == channel.peer_id)

    async def _owner_active(self, channel: ChatChannel) -> bool:
        """The channel's person still has a working account.

        Disabling someone has to cut off their bots too: a linked chat acts
        as that person without a password.
        """
        from cognitrix.models import User

        user = await User.get(channel.user_id) or await User.find_one({'email': channel.user_id})
        return not (user is not None and getattr(user, 'disabled', None))

    async def _on_message(self, channel_id: str, msg: Incoming) -> None:
        channel = await ChatChannel.get(channel_id)
        if channel is None or not msg.text.strip():
            return
        if not await self._owner_active(channel):
            await self.cancel_turn(channel.id)
            if msg.private:
                await self._reply(channel, msg.chat_id, 'This account is disabled.')
            return
        if msg.mention and not msg.private:
            await self._on_mention(channel, msg)
            return
        if not channel.chat_id:
            await self._try_link(channel, msg)
            return
        if not self._is_owner(channel, msg.chat_id, msg.sender_id):
            if msg.private:
                await self._reply(channel, msg.chat_id, 'This bot is private.')
            return

        text = msg.text.strip()
        command = _COMMAND.match(text)
        if command:
            await self._command(channel, command.group(1).lower(), command.group(2).strip())
            return

        pending = self._questions.get(channel.id)
        if pending:
            from cognitrix.questions.broker import resolve_question
            from cognitrix.questions.models import QuestionAction

            if await resolve_question(pending, channel.user_id, QuestionAction.ANSWER, text=text):
                self._questions.pop(channel.id, None)
                return
        turn = self._turns.get(channel.id)
        if turn is not None and not turn.done() and self._steer(channel.id, text, channel.kind):
            await self._reply(channel, channel.chat_id, STEER_ACK)
            return
        if turn is not None and not turn.done():
            await self._reply(channel, channel.chat_id,
                              f'Still working on your last message. Send {_prefix(channel.kind)}stop to cancel it.')
            return
        self._turns[channel.id] = asyncio.create_task(self._turn(channel, text))

    def _steer(self, key: str, text: str, source: str) -> bool:
        """Hand a message to the turn running under ``key``. False when it
        can't take one (then the person is told to wait, as before)."""
        run = self._turn_runs.get(key)
        if run is None or not run.running or not run.steerable:
            return False
        try:
            return run.steer(text, source=source) is not None
        except Exception:
            return False

    async def _on_mention(self, channel: ChatChannel, msg: Incoming) -> None:
        """Someone @mentioned the bot in a shared channel: answer in that thread.

        The linked person gets their agent as usual. Anyone else is answered
        only when the person turned that on, and then read-only, in a chat
        that has none of the person's memories.
        """
        if channel.kind not in MENTION_KINDS or not channel.chat_id:
            return  # not linked yet: linking happens in a direct message
        text = msg.text.strip()
        where = {'thread_id': msg.thread_id}
        guest = not self._is_person(channel, msg.sender_id)
        if guest and not channel.answer_others:
            await self._reply(channel, msg.chat_id,
                              f'I only answer {channel.peer_name or "the person who set me up"} here.', **where)
            return
        key = f'{channel.id}|{msg.chat_id}|{msg.thread_id}'
        command = _COMMAND.match(text)
        if command and command.group(1).lower() == 'stop':
            turn = self._turns.get(key)
            if turn is not None and not turn.done() and not guest:
                turn.cancel()
            return
        pending = self._questions.get(key)
        # A question from the person's own turn is theirs to answer: a guest's
        # words must never steer a turn that has the person's full powers.
        if pending and (not guest or key in self._guest_turns):
            from cognitrix.questions.broker import resolve_question
            from cognitrix.questions.models import QuestionAction

            if await resolve_question(pending, channel.user_id, QuestionAction.ANSWER, text=text):
                self._questions.pop(key, None)
                return
        turn = self._turns.get(key)
        # Only the person steers, and only their own (full-power) turn.
        if (
            turn is not None and not turn.done() and not guest and key not in self._guest_turns
            and self._steer(key, f'[{channel.peer_name or "The person you work for"} added this in the '
                                 f'shared {channel.kind.capitalize()} thread.]\n\n{text}', channel.kind)
        ):
            await self._reply(channel, msg.chat_id, STEER_ACK, **where)
            return
        if turn is not None and not turn.done():
            await self._reply(channel, msg.chat_id, 'Still working on the last message in this thread.', **where)
            return
        self._turns[key] = asyncio.create_task(self._turn(
            channel, text, chat_id=msg.chat_id, thread_id=msg.thread_id, guest=guest,
            sender_name=msg.sender_name, key=key))

    async def _try_link(self, channel: ChatChannel, msg: Incoming) -> None:
        from cognitrix.tasks.automation import utcnow_str

        if not msg.private:
            return
        code = channel.link_code
        words = {w.upper() for w in re.split(r'\s+', msg.text.strip()) if w}
        valid = bool(code) and code.upper() in words and (
            not channel.link_code_expires_at or channel.link_code_expires_at >= utcnow_str())
        if not valid:
            failures = self._link_failures.get(channel.id, 0) + 1
            self._link_failures[channel.id] = failures
            if failures >= MAX_LINK_ATTEMPTS and code:
                # Too many guesses: void the code; Settings shows a new one on request.
                await ChatChannel.update_one({'id': channel.id}, {'link_code': ''})
            await self._reply(channel, msg.chat_id,
                              'This bot isn\'t linked yet. Send the code shown in '
                              'Cognitrix under Settings > Chat apps.')
            return
        self._link_failures.pop(channel.id, None)
        await ChatChannel.update_one({'id': channel.id}, {
            'chat_id': msg.chat_id,
            'peer_id': msg.sender_id,
            'peer_name': msg.sender_name,
            'linked_at': utcnow_str(),
            'link_code': '',
            'link_code_expires_at': '',
            'session_id': '',
        })
        channel = await ChatChannel.get(channel.id)
        await self._skip_existing_items(channel)
        agent = await self._agent(channel)
        name = getattr(agent, 'name', None) or 'your agent'
        await self._reply(channel, msg.chat_id,
                          'Linked to Cognitrix. ' + HELP_TEXT.format(agent=name, p=_prefix(channel.kind)))

    async def _command(self, channel: ChatChannel, name: str, arg: str) -> None:
        p = _prefix(channel.kind)
        if name in ('start', 'help'):
            agent = await self._agent(channel)
            await self._reply(channel, channel.chat_id,
                              HELP_TEXT.format(agent=getattr(agent, 'name', 'your agent'), p=p))
        elif name == 'new':
            await ChatChannel.update_one({'id': channel.id}, {'session_id': ''})
            agent = await self._agent(channel)
            await self._reply(channel, channel.chat_id,
                              f'Started a new conversation with {getattr(agent, "name", "your agent")}.')
        elif name == 'stop':
            turn = self._turns.get(channel.id)
            if turn is not None and not turn.done():
                turn.cancel()
            else:
                await self._reply(channel, channel.chat_id, 'Nothing is running.')
        elif name == 'agents':
            agents = await self._agents(channel.user_id)
            current = await self._agent(channel)
            if not agents:
                await self._reply(channel, channel.chat_id, 'You have no agents yet.')
                return
            lines = [
                f'{i}. {a.name}' + (' (current)' if current is not None and a.id == current.id else '')
                for i, a in enumerate(agents, 1)
            ]
            await self._reply(channel, channel.chat_id,
                              '\n'.join(lines) + f'\n\nSend {p}agent NAME or {p}agent NUMBER to switch.')
        elif name == 'agent':
            agents = await self._agents(channel.user_id)
            chosen = None
            if arg.isdigit() and 1 <= int(arg) <= len(agents):
                chosen = agents[int(arg) - 1]
            else:
                chosen = next((a for a in agents if a.name.lower() == arg.lower()), None)
            if chosen is None:
                await self._reply(channel, channel.chat_id, f'No agent called "{arg}". Send {p}agents to see the list.')
                return
            await ChatChannel.update_one({'id': channel.id}, {'agent_id': str(chosen.id), 'session_id': ''})
            await self._reply(channel, channel.chat_id, f'Now chatting with {chosen.name} in a new conversation.')
        else:
            await self._reply(channel, channel.chat_id, f'Unknown command. Send {p}help for the list.')

    # --- chat turns ------------------------------------------------------------

    async def _agents(self, user_key: str) -> list:
        from cognitrix.agents import access as agent_access

        agents = await agent_access.visible_agents(user_key)
        own = [a for a in agents if str(getattr(a, 'owner_id', '') or '') == user_key]
        shared = [a for a in agents if a not in own]

        def created(a):
            return str(getattr(a, 'created_at', '') or '')

        return sorted(own, key=created) + sorted(shared, key=created)

    async def _agent(self, channel: ChatChannel):
        from cognitrix.agents import access as agent_access

        if channel.agent_id:
            agent = await agent_access.find_visible_agent(channel.user_id, agent_id=channel.agent_id)
            if agent is not None:
                return agent
        from cognitrix.tasks.standing import primary_first

        # No agent picked yet: the person's main assistant, when they chose one.
        agents = primary_first(await self._agents(channel.user_id), channel.user_id)
        return agents[0] if agents else None

    async def _thread_session(self, channel: ChatChannel, agent, chat_id: str, thread_id: str, guest: bool):
        """The conversation behind one thread of a shared channel."""
        query = {'channel_id': channel.id, 'chat_id': chat_id, 'thread_id': thread_id}
        row = await ChannelThread.find_one(query)
        # A thread someone else started never continues as the person's own
        # conversation (and the other way round): each keeps its limits.
        stored = row.session_id if row is not None and bool(row.guest) == guest else ''

        async def remember(session_id: str) -> None:
            if row is not None:
                await ChannelThread.update_one({'id': row.id}, {'session_id': session_id, 'guest': guest})
            else:
                await ChannelThread(**query, session_id=session_id, guest=guest).save()

        return await self._session(channel, agent, stored=stored, remember=remember, incognito=guest)

    async def _session(self, channel: ChatChannel, agent, *, stored: str | None = None,
                       remember: Any = None, incognito: bool = False):
        from cognitrix.session_ownership import (
            OwnershipConflict,
            OwnershipNotFound,
            claim_new,
            require_active_owned,
        )
        from cognitrix.sessions.base import Session

        stored = channel.session_id if stored is None else stored
        if stored:
            try:
                binding = await require_active_owned(stored, channel.user_id, str(agent.id))
                session = await Session.get(stored)
                if session is not None and str(session.agent_id or '') == binding.agent_id:
                    return session
            except (OwnershipNotFound, OwnershipConflict):
                pass
        session = Session(agent_id=agent.id, user_id=channel.user_id, incognito=True if incognito else None)
        await session.save()
        try:
            await claim_new(str(session.id), channel.user_id, str(agent.id))
        except BaseException:
            await Session.delete_many({'id': str(session.id)})
            raise
        if remember is not None:
            await remember(str(session.id))
        else:
            await ChatChannel.update_one({'id': channel.id}, {'session_id': str(session.id)})
        return session

    async def _keep_typing(self, channel: ChatChannel, chat_id: str) -> None:
        transport = self._transports.get(channel.id)
        if transport is None:
            return
        while True:
            await transport.typing(chat_id)
            await asyncio.sleep(TYPING_INTERVAL)

    async def _turn(self, channel: ChatChannel, text: str, *, chat_id: str = '', thread_id: str = '',
                    guest: bool = False, sender_name: str = '', key: str = '') -> None:
        """One reply. With ``chat_id`` it answers an @mention in a shared
        channel (in ``thread_id``); ``guest`` means someone other than the
        linked person asked."""
        from cognitrix.api.routes.agents import CHAT_TIMEOUT
        from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
        from cognitrix.safety.approval_gate import web_turn_ctx
        from cognitrix.safety.permissions import guest_run, read_only_run
        from cognitrix.sessions import runs as chat_runs
        from cognitrix.tools.utils import ToolExecutionContext, signed_in_host_process_mode

        shared = bool(chat_id)
        chat_id = chat_id or channel.chat_id
        key = key or channel.id
        where = {'thread_id': thread_id} if thread_id else {}
        agent = await self._agent(channel)
        if agent is None:
            await self._reply(channel, chat_id,
                              'You have no agents yet. Create one in Cognitrix first.', **where)
            return
        if shared:
            session = await self._thread_session(channel, agent, chat_id, thread_id, guest)
            person = channel.peer_name or 'the person you work for'
            if guest:
                # These context variables live in this turn's own task.
                read_only_run.set(GUEST_REASON)
                guest_run.set(True)
                self._guest_turns.add(key)
                text = (f'[{sender_name or "Someone"} asked this in a shared {channel.kind.capitalize()} channel. '
                        f'They are not {person}. Answer from what you can look up; you cannot change anything '
                        f'for them, and do not share private details about {person}.]\n\n{text}')
            else:
                text = (f'[{person} asked this in a shared {channel.kind.capitalize()} channel: '
                        f'everyone there reads your reply.]\n\n{text}')
        else:
            session = await self._session(channel, agent)
        existing = chat_runs.get_run(channel.user_id, str(session.id))
        if existing is not None and existing.running and not guest and existing.steerable:
            # The agent is mid-task: hand it this message after its current
            # step instead of turning the person away.
            try:
                queued = existing.steer(text, source=channel.kind)
            except (chat_runs.InboxFull, ValueError):
                queued = None
            if queued is not None:
                await self._reply(channel, chat_id, STEER_ACK, **where)
                return
        if existing is not None and existing.running:
            await self._reply(channel, chat_id,
                              'This conversation is busy in Cognitrix. Try again in a moment, '
                              f'or send {_prefix(channel.kind)}new.', **where)
            return

        me = asyncio.current_task()
        run = chat_runs.start_run(
            channel.user_id, str(agent.id), str(session.id), prompt=text,
            history_len=len(getattr(session, 'chat', None) or []),
            stop=lambda: bool(me and me.cancel()),
        )
        self._turn_runs[key] = run
        collected: list[str] = []

        async def flush() -> None:
            reply = strip_reasoning(''.join(collected))
            collected.clear()
            if reply and await self._still_linked(channel):
                await self._reply(channel, chat_id, reply, **where)

        async def output(payload: Any = None, *args, **kwargs) -> None:
            if not isinstance(payload, dict):
                return
            run.record({**payload, 'session_id': str(session.id)})
            if payload.get('type') == 'generate' and payload.get('content'):
                collected.append(str(payload['content']))

        async def emit(event: dict[str, Any]) -> None:
            run.record({**event, 'session_id': str(session.id)})
            kind = event.get('type')
            if kind in ('approval_request', 'question_request') and not await self._still_linked(channel):
                # The chat was unlinked: never send its prompts to the old chat.
                raise asyncio.CancelledError
            if kind == 'approval_request':
                await flush()
                rid = str(event.get('request_id'))
                await self._reply(
                    channel, chat_id,
                    f'{agent.name} wants to run **{event.get("tool_name")}** '
                    f'({event.get("risk_level", "")} risk).\n'
                    f'```\n{_params_preview(event.get("params"))}\n```',
                    [Button('Approve', f'ca:{rid}:1', 'primary'), Button('Deny', f'ca:{rid}:0', 'danger')],
                    **where,
                )
            elif kind == 'question_request':
                await flush()
                rid = str(event.get('request_id'))
                self._questions[key] = rid
                options = event.get('options') or []
                body = str(event.get('prompt') or 'Question')
                if event.get('details'):
                    body += f'\n\n{event["details"]}'
                if event.get('allow_free_text'):
                    body += '\n\nTap an option or type your answer.' if options else '\n\nType your answer.'
                buttons = [Button(str(o.get('label'))[:60], f'q:{rid}:{i}') for i, o in enumerate(options)][:8]
                if guest:
                    # Buttons are the linked person's; a guest answers by mentioning the bot again.
                    buttons = []
                    body += '\n\nMention me with your answer.'
                await self._reply(channel, chat_id, body, buttons or None, **where)

        typing = asyncio.create_task(self._keep_typing(channel, chat_id))
        approval_token = web_turn_ctx.set({
            'emit': emit, 'session_id': str(session.id), 'bypass': False, 'user_key': channel.user_id,
            'agent_name': agent.name, 'origin': f'channel:{channel.id}',
        })
        question_token = question_turn_ctx.set(QuestionTurnContext(
            emit=emit, session_id=str(session.id),
            stream_id=f'channel-{key}', user_key=channel.user_id,
        ))
        terminal = {'type': 'turn_complete', 'content': '', 'session_id': str(session.id)}
        # A guest's turn reads nothing from the inbox (see the check above).
        run.steerable = not guest
        run_token = chat_runs.current_run.set(run)
        try:
            await asyncio.wait_for(
                session(text, agent, interface='web', stream=True, output=output,
                        wsquery={'type': 'generate', 'action': 'chat_message'},
                        tool_context=ToolExecutionContext(
                            session_id=str(session.id), user_id=channel.user_id,
                            agent_id=str(agent.id), host_process_mode=signed_in_host_process_mode(),
                        )),
                timeout=CHAT_TIMEOUT,
            )
        except asyncio.CancelledError:
            terminal = {'type': 'turn_stopped', 'content': '', 'session_id': str(session.id)}
            collected.append('\n\n(Stopped.)')
        except TimeoutError:
            terminal = {'type': 'error', 'content': 'timed out', 'session_id': str(session.id)}
            collected.append('\n\n(That took too long and was stopped.)')
            try:
                await session.save()
            except Exception:
                logger.exception('Could not save session %s after a timeout', session.id)
        except Exception as exc:
            logger.exception('Chat channel turn failed')
            terminal = {'type': 'error', 'content': str(exc), 'session_id': str(session.id)}
            collected.append(f'\n\nSomething went wrong: {exc}')
        finally:
            web_turn_ctx.reset(approval_token)
            question_turn_ctx.reset(question_token)
            typing.cancel()
            self._questions.pop(key, None)
            self._guest_turns.discard(key)
            chat_runs.current_run.reset(run_token)
            run.finish(terminal)
            if self._turn_runs.get(key) is run:
                self._turn_runs.pop(key, None)
            try:
                await flush()
            except Exception:
                logger.exception('Could not send the reply to chat channel %s', channel.id)
            # Messages that came in too late for this turn start the next one
            # (unless the person stopped it).
            if run.unsent and terminal.get('type') != 'turn_stopped' and await self._still_linked(channel):
                follow_up = '\n\n'.join(str(m.get('content') or '') for m in run.unsent)
                self._turns[key] = asyncio.create_task(self._turn(
                    channel, follow_up, chat_id=chat_id if shared else '', thread_id=thread_id,
                    sender_name=sender_name, key=key,
                ))

    # --- buttons ---------------------------------------------------------------

    async def _on_button(self, channel_id: str, press: ButtonPress) -> None:
        channel = await ChatChannel.get(channel_id)
        transport = self._transports.get(channel_id)
        if channel is None or transport is None:
            return
        if not (self._is_owner(channel, press.chat_id, press.sender_id)
                or self._is_person(channel, press.sender_id)):
            await transport.answer_button(press, 'Only the linked account can answer this.')
            return
        if not await self._owner_active(channel):
            await transport.answer_button(press, 'This account is disabled.')
            return
        kind, _, rest = press.data.partition(':')
        item_id, _, choice = rest.rpartition(':')
        outcome = ''
        if kind == 'ta':
            outcome = await self._answer_task_approval(channel, item_id, choice == '1')
        elif kind == 'ca':
            from cognitrix.safety.approval_gate import resolve_web_approval

            ok = resolve_web_approval(item_id, approved=choice == '1', user_key=channel.user_id)
            if ok:
                await self._close_delivery(channel.id, 'chat_approval', item_id)
            outcome = ('Approved.' if choice == '1' else 'Denied.') if ok else 'This request has already ended.'
        elif kind == 'q':
            outcome = await self._answer_question(channel, item_id, choice)
        await transport.answer_button(press, outcome)
        if outcome:
            original = self._button_text.pop((channel.id, press.chat_id, press.message_ref), '')
            await transport.edit(press.chat_id, press.message_ref,
                                 f'{original}\n\n{outcome}'.strip())

    async def _answer_task_approval(self, channel: ChatChannel, approval_id: str, approved: bool) -> str:
        from cognitrix.tasks import approvals

        row = await approvals.TaskApproval.get(approval_id)
        if row is None or str(row.user_id) != channel.user_id:
            return 'This approval was not found.'
        if row.status != approvals.PENDING:
            return f'Already {row.status}.'
        decided = await approvals.decide(approval_id, row.run_id, approved)
        if decided is None:
            return 'This approval was already answered.'
        await self._close_delivery(channel.id, 'approval', approval_id)
        return 'Approved.' if approved else 'Denied.'

    async def _answer_question(self, channel: ChatChannel, request_id: str, choice: str) -> str:
        from cognitrix.questions import broker
        from cognitrix.questions.models import QuestionAction

        pending = broker._PENDING.get(request_id)
        if pending is None or not choice.isdigit() or int(choice) >= len(pending.spec.options):
            return 'This question has already ended.'
        option = pending.spec.options[int(choice)]
        ok = await broker.resolve_question(request_id, channel.user_id, QuestionAction.ANSWER,
                                           option_id=option.id)
        if ok:
            for key in [k for k, v in self._questions.items() if v == request_id]:
                self._questions.pop(key, None)
            return f'Answered: {option.label}'
        return 'This question has already ended.'

    # --- Inbox delivery ----------------------------------------------------------

    async def _delivered_set(self, channel_id: str) -> set[tuple[str, str]]:
        if channel_id not in self._delivered:
            rows = await ChannelDelivery.find({'channel_id': channel_id}) or []
            self._delivered[channel_id] = {(r.item_kind, r.item_id) for r in rows}
        return self._delivered[channel_id]

    async def _record(self, channel_id: str, kind: str, item_id: str, ref: str = '',
                      closed: bool = False) -> None:
        await ChannelDelivery(channel_id=channel_id, item_kind=kind, item_id=item_id,
                              message_ref=ref, closed=closed).save()
        (await self._delivered_set(channel_id)).add((kind, item_id))

    async def _close_delivery(self, channel_id: str, kind: str, item_id: str) -> None:
        await ChannelDelivery.update_one(
            {'channel_id': channel_id, 'item_kind': kind, 'item_id': item_id}, {'closed': True})

    async def _push(self, channel: ChatChannel, body: str, buttons: list[Button] | None = None) -> str | None:
        """Send an Inbox item to the linked chat. None when the app refuses
        it for good; the item then simply stays in the Inbox."""
        try:
            return await self._reply(channel, channel.chat_id, body, buttons)
        except ChannelUndeliverable as exc:
            logger.info('Chat channel %s could not deliver an Inbox item: %s', channel.id, exc)
            return None

    async def _pending_items(self, channel: ChatChannel) -> tuple[list, list]:
        from cognitrix.tasks import approvals
        from cognitrix.tasks.automation import unread_notifications

        notes = await unread_notifications(channel.user_id)
        rows = await approvals.TaskApproval.find(
            {'user_id': channel.user_id, 'status': approvals.PENDING}) or []
        return notes, rows

    async def _skip_existing_items(self, channel: ChatChannel) -> None:
        """Items already waiting when a chat is linked stay in the Inbox only."""
        notes, rows = await self._pending_items(channel)
        seen = await self._delivered_set(channel.id)
        for kind, items in (('notification', notes), ('approval', rows)):
            for item in items:
                if (kind, str(item.id)) not in seen:
                    await self._record(channel.id, kind, str(item.id), closed=True)

    async def _task_title(self, task_id: str | None, cache: dict[str, str]) -> str:
        if not task_id:
            return ''
        if task_id not in cache:
            from cognitrix.tasks.base import Task

            task = await Task.get(task_id)
            cache[task_id] = getattr(task, 'title', '') or ''
        return cache[task_id]

    async def _deliver_chat_approvals(self, channel: ChatChannel, seen: set[tuple[str, str]]) -> int:
        """Approvals from chats nobody answered on the chat page in time."""
        from cognitrix.safety import approval_alerts

        sent = 0
        for rid, entry in approval_alerts.due_chat_approvals(channel.user_id):
            if ('chat_approval', rid) in seen:
                continue
            if entry.get('origin') == f'channel:{channel.id}':
                # This chat started the turn and already has the buttons.
                await self._record(channel.id, 'chat_approval', rid, closed=True)
                continue
            body = (f'Approval needed: {approval_alerts.headline(entry)} '
                    f'({entry.get("risk_level", "")} risk).\n'
                    f'```\n{_params_preview(entry.get("params"))}\n```')
            ref = await self._push(channel, body, [
                Button('Approve', f'ca:{rid}:1', 'primary'),
                Button('Deny', f'ca:{rid}:0', 'danger'),
            ])
            await self._record(channel.id, 'chat_approval', rid, ref or '', closed=ref is None)
            sent += 1
        return sent

    async def deliver_once(self) -> int:
        """Send new alerts and approvals to every linked, connected channel."""
        from cognitrix.safety import approval_alerts
        from cognitrix.tasks import approvals

        sent = 0
        titles: dict[str, str] = {}
        for channel in await ChatChannel.find({'enabled': True}) or []:
            if not channel.chat_id or channel.id not in self._connected:
                continue
            try:
                if not await self._owner_active(channel):
                    continue
                seen = await self._delivered_set(channel.id)
                notes, rows = await self._pending_items(channel)
                if channel.send_alerts:
                    for note in notes:
                        if ('notification', str(note.id)) in seen:
                            continue
                        title = await self._task_title(note.task_id, titles)
                        body = f'🔔 **{note.title}**'
                        if note.message:
                            body += f'\n{note.message}'
                        if title:
                            body += f'\n\nTask: {title}'
                        ref = await self._push(channel, body)
                        await self._record(channel.id, 'notification', str(note.id), ref or '', closed=True)
                        sent += 1
                if channel.send_approvals:
                    for row in rows:
                        if ('approval', str(row.id)) in seen:
                            continue
                        title = await self._task_title(row.task_id, titles)
                        body = (f'Approval needed{f" for task **{title}**" if title else ""}: '
                                f'run **{row.tool_name}** ({row.risk_level} risk)?\n'
                                f'```\n{_params_preview(row.params)}\n```')
                        ref = await self._push(channel, body, [
                            Button('Approve', f'ta:{row.id}:1', 'primary'),
                            Button('Deny', f'ta:{row.id}:0', 'danger'),
                        ])
                        await self._record(channel.id, 'approval', str(row.id), ref or '', closed=ref is None)
                        sent += 1
                    sent += await self._deliver_chat_approvals(channel, seen)
                # Approvals answered elsewhere: drop their buttons.
                for delivery in await ChannelDelivery.find(
                        {'channel_id': channel.id, 'closed': False}) or []:
                    if delivery.item_kind == 'chat_approval':
                        if approval_alerts.still_pending(delivery.item_id):
                            continue
                        status = 'answered'
                    elif delivery.item_kind == 'approval':
                        row = await approvals.TaskApproval.get(delivery.item_id)
                        if row is not None and row.status == approvals.PENDING:
                            continue
                        status = getattr(row, 'status', 'gone')
                    else:
                        continue
                    transport = self._transports.get(channel.id)
                    if transport is not None and delivery.message_ref:
                        original = self._button_text.pop(
                            (channel.id, channel.chat_id, delivery.message_ref), '')
                        await transport.edit(channel.chat_id, delivery.message_ref,
                                             f'{original}\n\n{str(status).capitalize()}.'.strip())
                    await ChannelDelivery.update_one({'id': delivery.id}, {'closed': True})
            except Exception:
                logger.exception('Inbox delivery to chat channel %s failed', channel.id)
        return sent

    async def _delivery_loop(self) -> None:
        while True:
            try:
                await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if 'no such table' not in str(exc).lower():
                    logger.exception('Chat channel delivery pass failed')
            await asyncio.sleep(DELIVERY_INTERVAL)


_service: ChannelService | None = None


def get_service() -> ChannelService | None:
    return _service


async def start_channels() -> ChannelService | None:
    global _service
    if not channels_enabled():
        return None
    _service = ChannelService()
    await _service.start()
    return _service


async def stop_channels() -> None:
    global _service
    if _service is not None:
        await _service.stop()
        _service = None
