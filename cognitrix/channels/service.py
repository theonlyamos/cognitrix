"""Runs every connected chat app bot and links it to its owner's agents.

* One background task per channel keeps the bot connected (Telegram long
  polling, Slack Socket Mode) and restarts it after network errors.
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

from cognitrix.models.chat_channel import ChannelDelivery, ChatChannel

from .base import (
    Button,
    ButtonPress,
    ChannelAuthError,
    ChannelConflictError,
    Handlers,
    Incoming,
    Transport,
)
from .formatting import split_text, strip_reasoning

logger = logging.getLogger('cognitrix.log')

KINDS = ('telegram', 'slack')
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
    # Slack treats "/word" as a slash command, so Slack users type "!word".
    return '!' if kind == 'slack' else '/'


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
        self._questions: dict[str, str] = {}
        """channel id -> request id of an Ask User question waiting for a reply."""
        self._button_text: dict[str, str] = {}
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

    def is_connected(self, channel_id: str) -> bool:
        return channel_id in self._connected

    def _start_runner(self, channel_id: str) -> None:
        if channel_id not in self._runners:
            self._runners[channel_id] = asyncio.create_task(self._run_channel(channel_id))

    async def _stop_runner(self, channel_id: str) -> None:
        task = self._runners.pop(channel_id, None)
        turn = self._turns.pop(channel_id, None)
        for item in (task, turn):
            if item is None:
                continue
            item.cancel()
            try:
                await item
            except (asyncio.CancelledError, Exception):
                pass
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
                     buttons: list[Button] | None = None) -> str:
        transport = self._transports.get(channel.id)
        if transport is None:
            return ''
        ref = ''
        parts = split_text(text, transport.text_limit) or ['']
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            ref = await transport.send(chat_id, part, buttons if last else None)
        if buttons and ref:
            self._button_text[ref] = parts[-1]
        return ref

    async def send(self, channel: ChatChannel, chat_id: str, text: str) -> str:
        return await self._reply(channel, chat_id, text)

    def _is_owner(self, channel: ChatChannel, chat_id: str, sender_id: str) -> bool:
        return bool(channel.chat_id) and chat_id == channel.chat_id and sender_id == channel.peer_id

    async def _on_message(self, channel_id: str, msg: Incoming) -> None:
        channel = await ChatChannel.get(channel_id)
        if channel is None or not msg.text.strip():
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
        if turn is not None and not turn.done():
            await self._reply(channel, channel.chat_id,
                              f'Still working on your last message. Send {_prefix(channel.kind)}stop to cancel it.')
            return
        self._turns[channel.id] = asyncio.create_task(self._turn(channel, text))

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
        agents = await self._agents(channel.user_id)
        return agents[0] if agents else None

    async def _session(self, channel: ChatChannel, agent):
        from cognitrix.session_ownership import (
            OwnershipConflict,
            OwnershipNotFound,
            claim_new,
            require_active_owned,
        )
        from cognitrix.sessions.base import Session

        if channel.session_id:
            try:
                binding = await require_active_owned(channel.session_id, channel.user_id, str(agent.id))
                session = await Session.get(channel.session_id)
                if session is not None and str(session.agent_id or '') == binding.agent_id:
                    return session
            except (OwnershipNotFound, OwnershipConflict):
                pass
        session = Session(agent_id=agent.id, user_id=channel.user_id)
        await session.save()
        try:
            await claim_new(str(session.id), channel.user_id, str(agent.id))
        except BaseException:
            await Session.delete_many({'id': str(session.id)})
            raise
        await ChatChannel.update_one({'id': channel.id}, {'session_id': str(session.id)})
        return session

    async def _keep_typing(self, channel: ChatChannel) -> None:
        transport = self._transports.get(channel.id)
        if transport is None:
            return
        while True:
            await transport.typing(channel.chat_id)
            await asyncio.sleep(TYPING_INTERVAL)

    async def _turn(self, channel: ChatChannel, text: str) -> None:
        from cognitrix.api.routes.agents import CHAT_TIMEOUT
        from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
        from cognitrix.safety.approval_gate import web_turn_ctx
        from cognitrix.sessions import runs as chat_runs
        from cognitrix.tools.utils import ToolExecutionContext, signed_in_host_process_mode

        agent = await self._agent(channel)
        if agent is None:
            await self._reply(channel, channel.chat_id,
                              'You have no agents yet. Create one in Cognitrix first.')
            return
        session = await self._session(channel, agent)
        existing = chat_runs.get_run(channel.user_id, str(session.id))
        if existing is not None and existing.running:
            await self._reply(channel, channel.chat_id,
                              'This conversation is busy in Cognitrix. Try again in a moment, '
                              f'or send {_prefix(channel.kind)}new.')
            return

        me = asyncio.current_task()
        run = chat_runs.start_run(
            channel.user_id, str(agent.id), str(session.id), prompt=text,
            history_len=len(getattr(session, 'chat', None) or []),
            stop=lambda: bool(me and me.cancel()),
        )
        collected: list[str] = []

        async def flush() -> None:
            reply = strip_reasoning(''.join(collected))
            collected.clear()
            if reply:
                await self._reply(channel, channel.chat_id, reply)

        async def output(payload: Any = None, *args, **kwargs) -> None:
            if not isinstance(payload, dict):
                return
            run.record({**payload, 'session_id': str(session.id)})
            if payload.get('type') == 'generate' and payload.get('content'):
                collected.append(str(payload['content']))

        async def emit(event: dict[str, Any]) -> None:
            run.record({**event, 'session_id': str(session.id)})
            kind = event.get('type')
            if kind == 'approval_request':
                await flush()
                rid = str(event.get('request_id'))
                await self._reply(
                    channel, channel.chat_id,
                    f'{agent.name} wants to run **{event.get("tool_name")}** '
                    f'({event.get("risk_level", "")} risk).\n'
                    f'```\n{_params_preview(event.get("params"))}\n```',
                    [Button('Approve', f'ca:{rid}:1', 'primary'), Button('Deny', f'ca:{rid}:0', 'danger')],
                )
            elif kind == 'question_request':
                await flush()
                rid = str(event.get('request_id'))
                self._questions[channel.id] = rid
                options = event.get('options') or []
                body = str(event.get('prompt') or 'Question')
                if event.get('details'):
                    body += f'\n\n{event["details"]}'
                if event.get('allow_free_text'):
                    body += '\n\nTap an option or type your answer.' if options else '\n\nType your answer.'
                buttons = [Button(str(o.get('label'))[:60], f'q:{rid}:{i}') for i, o in enumerate(options)][:8]
                await self._reply(channel, channel.chat_id, body, buttons or None)

        typing = asyncio.create_task(self._keep_typing(channel))
        approval_token = web_turn_ctx.set({
            'emit': emit, 'session_id': str(session.id), 'bypass': False, 'user_key': channel.user_id,
        })
        question_token = question_turn_ctx.set(QuestionTurnContext(
            emit=emit, session_id=str(session.id),
            stream_id=f'channel-{channel.id}', user_key=channel.user_id,
        ))
        terminal = {'type': 'turn_complete', 'content': '', 'session_id': str(session.id)}
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
            self._questions.pop(channel.id, None)
            run.finish(terminal)
            try:
                await flush()
            except Exception:
                logger.exception('Could not send the reply to chat channel %s', channel.id)

    # --- buttons ---------------------------------------------------------------

    async def _on_button(self, channel_id: str, press: ButtonPress) -> None:
        channel = await ChatChannel.get(channel_id)
        transport = self._transports.get(channel_id)
        if channel is None or transport is None:
            return
        if not self._is_owner(channel, press.chat_id, press.sender_id):
            await transport.answer_button(press, 'Only the linked account can answer this.')
            return
        kind, _, rest = press.data.partition(':')
        item_id, _, choice = rest.rpartition(':')
        outcome = ''
        if kind == 'ta':
            outcome = await self._answer_task_approval(channel, item_id, choice == '1')
        elif kind == 'ca':
            from cognitrix.safety.approval_gate import resolve_web_approval

            ok = resolve_web_approval(item_id, approved=choice == '1', user_key=channel.user_id)
            outcome = ('Approved.' if choice == '1' else 'Denied.') if ok else 'This request has already ended.'
        elif kind == 'q':
            outcome = await self._answer_question(channel, item_id, choice)
        await transport.answer_button(press, outcome)
        if outcome:
            original = self._button_text.pop(press.message_ref, '')
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
            self._questions.pop(channel.id, None)
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

    async def deliver_once(self) -> int:
        """Send new alerts and approvals to every linked, connected channel."""
        from cognitrix.tasks import approvals

        sent = 0
        titles: dict[str, str] = {}
        for channel in await ChatChannel.find({'enabled': True}) or []:
            if not channel.chat_id or channel.id not in self._connected:
                continue
            try:
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
                        ref = await self._reply(channel, channel.chat_id, body)
                        await self._record(channel.id, 'notification', str(note.id), ref, closed=True)
                        sent += 1
                if channel.send_approvals:
                    for row in rows:
                        if ('approval', str(row.id)) in seen:
                            continue
                        title = await self._task_title(row.task_id, titles)
                        body = (f'Approval needed{f" for task **{title}**" if title else ""}: '
                                f'run **{row.tool_name}** ({row.risk_level} risk)?\n'
                                f'```\n{_params_preview(row.params)}\n```')
                        ref = await self._reply(channel, channel.chat_id, body, [
                            Button('Approve', f'ta:{row.id}:1', 'primary'),
                            Button('Deny', f'ta:{row.id}:0', 'danger'),
                        ])
                        await self._record(channel.id, 'approval', str(row.id), ref)
                        sent += 1
                # Approvals answered elsewhere: drop their buttons.
                for delivery in await ChannelDelivery.find(
                        {'channel_id': channel.id, 'item_kind': 'approval', 'closed': False}) or []:
                    row = await approvals.TaskApproval.get(delivery.item_id)
                    if row is not None and row.status == approvals.PENDING:
                        continue
                    status = getattr(row, 'status', 'gone')
                    transport = self._transports.get(channel.id)
                    if transport is not None and delivery.message_ref:
                        original = self._button_text.pop(delivery.message_ref, '')
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
