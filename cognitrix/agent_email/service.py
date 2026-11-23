"""Reads each connected mailbox and turns mail into agent chats.

* One background task per mailbox checks for new mail every
  ``COGNITRIX_EMAIL_POLL_SECONDS`` (30) over IMAP. Only mail that arrives
  after the mailbox was connected is read; nothing is ever deleted.
* Mail is for an agent when one of its recipients is that agent's address
  (``box+name@domain``, or ``name@domain`` when the whole domain lands in the
  mailbox). The mailbox's own address goes to the person's chosen agent.
* Only allowed senders give agents work: the person's own account email
  unless they listed others, and (by default) only when the receiving server
  says the mail passed SPF, DKIM or DMARC for the sender's domain, so a forged
  From line isn't enough. Anyone else is ignored, or gets a read-only answer
  when the person turned that on. Bounces, auto-replies and list mail are
  never answered.
* Each email thread is one chat, shown in the web UI like any other. The
  agent's reply goes back from its own address in the same thread. Approvals
  and Ask User questions are sent as mail too: reply APPROVE / DENY or with
  the answer. Approvals also reach the Inbox like any chat's.

Runs in the API process, like chat apps; ``COGNITRIX_EMAIL=0`` turns it off
(needed when several API processes share one database).
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

from cognitrix.app_settings import get as app_setting

from . import mail
from .models import AgentAddress, EmailAccount, EmailLink

logger = logging.getLogger('cognitrix.log')

GUEST_REASON = 'this email came from someone other than your person, so you may only look things up'
BUSY_TEXT = 'Still working on your last email. I will read this one when I finish that step.'
_YES = re.compile(r'^\s*(approve|approved|yes|y|ok|allow|go ahead)\b', re.I)
_NO = re.compile(r'^\s*(deny|denied|no|n|reject|stop|don\'t)\b', re.I)


def email_enabled() -> bool:
    return app_setting('COGNITRIX_EMAIL', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def poll_seconds() -> float:
    try:
        return max(5.0, float(app_setting('COGNITRIX_EMAIL_POLL_SECONDS', '') or 30))
    except ValueError:
        return 30.0


def server_settings(account: EmailAccount) -> dict[str, Any]:
    from cognitrix.common.secret_box import unseal_or_empty

    return {
        'username': account.username or account.address,
        'password': unseal_or_empty(account.password),
    }


async def owner_email(user_key: str) -> str:
    from cognitrix.models import User

    user = await User.get(user_key) or await User.find_one({'email': user_key})
    return str(getattr(user, 'email', '') or '').lower()


async def owner_active(user_key: str) -> bool:
    from cognitrix.models import User

    user = await User.get(user_key) or await User.find_one({'email': user_key})
    return not (user is not None and getattr(user, 'disabled', None))


async def allowed_senders(account: EmailAccount) -> list[str]:
    listed = mail.allowed_list(account.allowed_senders)
    if listed:
        return listed
    own = await owner_email(account.user_id)
    return [own] if own else []


async def address_of(account: EmailAccount, agent_id: str) -> str:
    """The address an agent sends from: its own, or the mailbox's."""
    row = await AgentAddress.find_one({'account_id': account.id, 'agent_id': str(agent_id)})
    if row is not None and row.enabled:
        return mail.agent_address(account.address, account.addressing, row.name)
    return account.address


class EmailService:
    def __init__(self, *, fetcher: Callable[..., mail.Fetched] = mail.fetch,
                 sender: Callable[..., None] = mail.send):
        self._fetch = fetcher
        self._send = sender
        self._runners: dict[str, asyncio.Task] = {}
        self._turns: dict[str, asyncio.Task] = {}
        """session id -> the turn answering that email thread."""
        self._turn_runs: dict[str, Any] = {}
        self._questions: dict[str, str] = {}
        """session id -> Ask User request waiting for a reply by mail."""
        self._question_options: dict[str, list[dict]] = {}
        """session id -> the options of that question, so a reply can pick one."""
        self._approvals: dict[str, str] = {}
        """session id -> approval request waiting for APPROVE / DENY by mail."""

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        for account in await EmailAccount.all() or []:
            if account.enabled and account.imap_host:
                self._start_runner(account.id)

    async def stop(self) -> None:
        tasks = [*self._runners.values(), *self._turns.values()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._runners.clear()
        self._turns.clear()

    async def reload(self, account_id: str) -> None:
        task = self._runners.pop(account_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        account = await EmailAccount.get(account_id)
        if account is not None and account.enabled and account.imap_host:
            self._start_runner(account_id)

    def is_running(self, account_id: str) -> bool:
        task = self._runners.get(account_id)
        return task is not None and not task.done()

    def _start_runner(self, account_id: str) -> None:
        if account_id not in self._runners:
            self._runners[account_id] = asyncio.create_task(self._run_account(account_id))

    async def _set_status(self, account_id: str, status: str, error: str = '') -> None:
        from cognitrix.tasks.automation import utcnow_str

        try:
            await EmailAccount.update_one({'id': account_id}, {
                'status': status, 'last_error': error[:300], 'last_checked_at': utcnow_str()})
        except Exception:
            logger.exception('Could not save the status of mailbox %s', account_id)

    async def _run_account(self, account_id: str) -> None:
        backoff = poll_seconds()
        while True:
            account = await EmailAccount.get(account_id)
            if account is None or not account.enabled or not account.imap_host:
                return
            try:
                await self.poll_once(account)
                await self._set_status(account_id, 'connected')
                wait = poll_seconds()
                backoff = wait
            except asyncio.CancelledError:
                raise
            except mail.MailAuthError as exc:
                await self._set_status(account_id, 'error', str(exc))
                return
            except Exception as exc:
                logger.warning('Mailbox %s check failed: %s', account_id, exc)
                await self._set_status(account_id, 'error', str(exc) or type(exc).__name__)
                wait = backoff
                backoff = min(backoff * 2, 600.0)
            await asyncio.sleep(wait)

    # --- reading -------------------------------------------------------------

    async def poll_once(self, account: EmailAccount) -> int:
        """Read new mail once; returns how many messages were handed on."""
        fetched = await asyncio.to_thread(
            self._fetch, host=account.imap_host, port=account.imap_port, security=account.imap_security,
            folder=account.folder or 'INBOX', after_uid=account.last_uid, uid_validity=account.uid_validity,
            **server_settings(account))
        if fetched.uid_validity != account.uid_validity or fetched.last_uid != account.last_uid:
            await EmailAccount.update_one({'id': account.id}, {
                'uid_validity': fetched.uid_validity, 'last_uid': fetched.last_uid})
        for _uid, raw in fetched.messages:
            try:
                await self.handle(account, raw)
            except Exception:
                logger.exception('Could not handle a mail in mailbox %s', account.id)
        return len(fetched.messages)

    async def _thread_link(self, account: EmailAccount, inbound: mail.Inbound) -> EmailLink | None:
        for message_id in inbound.thread_ids():
            link = await EmailLink.find_one({'account_id': account.id, 'message_id': message_id})
            if link is not None:
                return link
        return None

    async def _route(self, account: EmailAccount, inbound: mail.Inbound) -> str:
        """The agent the mail was addressed to, or '' when it is for none of them."""
        for recipient in inbound.recipients:
            name = mail.name_for(account.address, account.addressing, recipient)
            if name is None:
                continue
            if name == '':
                agent = await self._default_agent(account)
                return str(agent.id) if agent is not None else ''
            row = await AgentAddress.find_one({'account_id': account.id, 'name': name})
            if row is not None and row.enabled:
                return str(row.agent_id)
        return ''

    async def _default_agent(self, account: EmailAccount):
        from cognitrix.agents import access as agent_access

        if account.agent_id:
            agent = await agent_access.find_visible_agent(account.user_id, agent_id=account.agent_id)
            if agent is not None:
                return agent
        from cognitrix.channels.service import ChannelService
        from cognitrix.tasks.standing import primary_first

        agents = primary_first(await ChannelService()._agents(account.user_id), account.user_id)
        return agents[0] if agents else None

    async def _is_own(self, account: EmailAccount, address: str) -> bool:
        """Mail sent by the mailbox itself or one of its agents (never answered).
        With a catch-all every address on the domain routes here, so only the
        mailbox's own address and registered agent addresses count."""
        name = mail.name_for(account.address, account.addressing, address)
        if name is None:
            return False
        if name == '' or account.addressing == 'plus':
            return True  # every plus variant is this same mailbox
        return await AgentAddress.find_one({'account_id': account.id, 'name': name}) is not None

    async def handle(self, account: EmailAccount, raw: bytes) -> str:
        """Act on one message. Returns what happened (for logs and tests)."""
        from cognitrix.agents import access as agent_access

        inbound = mail.parse(raw)
        if inbound.automated:
            return 'automated'
        if await self._is_own(account, inbound.sender):
            return 'own'
        if not await owner_active(account.user_id):
            return 'disabled'
        link = await self._thread_link(account, inbound)
        agent_id = link.agent_id if link is not None else await self._route(account, inbound)
        if not agent_id:
            return 'not-for-agent'
        agent = await agent_access.find_visible_agent(account.user_id, agent_id=agent_id)
        if agent is None:
            return 'no-agent'
        listed = mail.sender_listed(inbound.sender, await allowed_senders(account))
        trusted = listed and (not account.require_auth or mail.authenticated(inbound.auth_results, inbound.sender))
        guest = not trusted
        if guest and not account.answer_others:
            if link is not None and not link.guest:
                await self._note_reply(account, agent, link, inbound, listed)
            logger.info('Mailbox %s ignored mail from %s (not an allowed sender)', account.id, inbound.sender)
            return 'ignored'
        if link is not None and link.guest != guest:
            link = None  # never continue someone else's chat with other powers
        text = mail.new_text(inbound.body)
        session = await self._session(account, agent, link, inbound, guest)
        if inbound.message_id:
            await EmailLink(account_id=account.id, message_id=inbound.message_id, session_id=str(session.id),
                            agent_id=str(agent.id), guest=guest, subject=inbound.subject,
                            peer=inbound.sender).save()
        thread = _Thread(account=account, agent=agent, session_id=str(session.id), peer=inbound.sender,
                         subject=inbound.subject, reply_to=inbound.message_id,
                         references=[*inbound.references, *([inbound.in_reply_to] if inbound.in_reply_to else [])],
                         guest=guest)

        sid = str(session.id)
        if sid in self._approvals and not guest:
            approved = bool(_YES.match(text))
            if approved or _NO.match(text):
                from cognitrix.safety.approval_gate import resolve_web_approval

                rid = self._approvals.pop(sid)
                if resolve_web_approval(rid, approved=approved, user_key=account.user_id):
                    return 'approved' if approved else 'denied'
        if sid in self._questions and (not guest or link is not None):
            from cognitrix.questions.broker import resolve_question
            from cognitrix.questions.models import QuestionAction

            options = self._question_options.get(sid) or []
            picked = _pick_option(text, options)
            answer = {'option_id': picked} if picked else {'text': text}
            if await resolve_question(self._questions[sid], account.user_id, QuestionAction.ANSWER, **answer):
                self._questions.pop(sid, None)
                self._question_options.pop(sid, None)
                return 'answered'
        prompt = self._prompt(inbound, text, guest, account)
        turn = self._turns.get(sid)
        if turn is None or turn.done():
            # The same chat may be running in the web app right now.
            from cognitrix.sessions import runs as chat_runs

            existing = chat_runs.get_run(account.user_id, sid)
            if existing is not None and existing.running:
                if not guest and existing.steerable:
                    try:
                        if existing.steer(prompt, source='email') is not None:
                            return 'steered'
                    except Exception:
                        pass
                await self.reply(thread, BUSY_TEXT)
                return 'busy'
        if turn is not None and not turn.done():
            run = self._turn_runs.get(sid)
            if not guest and run is not None and run.running and run.steerable:
                try:
                    if run.steer(prompt, source='email') is not None:
                        return 'steered'
                except Exception:
                    pass
            await self.reply(thread, BUSY_TEXT)
            return 'busy'
        self._turns[sid] = asyncio.create_task(self._turn(thread, session, prompt))
        return 'started'

    def _prompt(self, inbound: mail.Inbound, text: str, guest: bool, account: EmailAccount) -> str:
        who = f'{inbound.sender_name} <{inbound.sender}>' if inbound.sender_name else inbound.sender
        head = f'[Email from {who}, subject: {inbound.subject or "(none)"}.'
        if guest:
            head += (' They are not the person you work for. Answer from what you can look up; you cannot '
                     'change anything for them, and do not share private details about your person.')
        head += ' Your reply is sent back to them by email.]'
        parts = [head, text or '(no text)']
        for item in inbound.attachments:
            if item.text:
                parts.append(f'--- Attachment {item.name} ---\n{item.text}')
            else:
                parts.append(f'(Attachment {item.name}, {item.content_type}, {item.size} bytes: not readable here.)')
        return '\n\n'.join(parts)

    async def _session(self, account: EmailAccount, agent, link: EmailLink | None,
                       inbound: mail.Inbound, guest: bool):
        from cognitrix.session_ownership import (
            OwnershipConflict,
            OwnershipNotFound,
            claim_new,
            require_active_owned,
        )
        from cognitrix.sessions.base import Session

        if link is not None:
            try:
                binding = await require_active_owned(link.session_id, account.user_id, str(agent.id))
                session = await Session.get(link.session_id)
                if session is not None and str(session.agent_id or '') == binding.agent_id:
                    return session
            except (OwnershipNotFound, OwnershipConflict):
                pass
        title = (inbound.subject or 'Email')[:120]
        session = Session(agent_id=agent.id, user_id=account.user_id, title=title,
                          incognito=True if guest else None)
        await session.save()
        try:
            await claim_new(str(session.id), account.user_id, str(agent.id))
        except BaseException:
            await Session.delete_many({'id': str(session.id)})
            raise
        return session

    async def _note_reply(self, account: EmailAccount, agent, link: EmailLink,
                          inbound: mail.Inbound, listed: bool) -> None:
        """Someone who may not give the agent work answered its email: tell the person."""
        from cognitrix.tasks.automation import create_notification

        why = ("the mail didn't pass the sender checks" if listed else "they aren't an allowed sender")
        snippet = mail.new_text(inbound.body)[:600]
        try:
            await create_notification(
                account.user_id, f'{inbound.sender} replied to {agent.name}',
                f'{snippet}\n\n{agent.name} did not read this because {why}.',
                session_id=link.session_id, link=f'/home?chat={link.session_id}')
        except Exception:
            logger.exception('Could not save the email reply notice')

    # --- writing -------------------------------------------------------------

    async def send_mail(self, account: EmailAccount, agent, *, to: str, subject: str, body: str,
                        session_id: str, in_reply_to: str = '', references: list[str] | None = None,
                        guest: bool = False, auto: bool = True) -> str:
        """Send from the agent's address and remember the message for its thread."""
        sender = await address_of(account, str(agent.id))
        message = mail.build(sender=sender, sender_name=str(agent.name), to=to, subject=subject, body=body,
                             in_reply_to=in_reply_to, references=references, auto=auto)
        settings = server_settings(account)
        await asyncio.to_thread(self._send, message, host=account.smtp_host, port=account.smtp_port,
                                security=account.smtp_security, **settings)
        message_id = str(message['Message-ID'])
        if session_id:
            await EmailLink(account_id=account.id, message_id=message_id, session_id=session_id,
                            agent_id=str(agent.id), guest=guest, subject=subject, peer=to).save()
        return message_id

    async def reply(self, thread: _Thread, text: str) -> None:
        try:
            message_id = await self.send_mail(
                thread.account, thread.agent, to=thread.peer, subject=mail.reply_subject(thread.subject),
                body=text, session_id=thread.session_id, in_reply_to=thread.reply_to,
                references=thread.references, guest=thread.guest)
        except mail.MailError as exc:
            logger.warning('Could not send email reply from mailbox %s: %s', thread.account.id, exc)
            await self._set_status(thread.account.id, 'error', str(exc))
            return
        # Later mail in this thread answers the latest message.
        thread.references = [*thread.references, thread.reply_to][-20:] if thread.reply_to else thread.references
        thread.reply_to = message_id

    # --- turns ---------------------------------------------------------------

    async def _turn(self, thread: _Thread, session, text: str) -> None:
        from cognitrix.api.routes.agents import CHAT_TIMEOUT
        from cognitrix.cards import to_markdown
        from cognitrix.channels.formatting import strip_reasoning
        from cognitrix.questions.broker import QuestionTurnContext, question_turn_ctx
        from cognitrix.safety.approval_gate import web_turn_ctx
        from cognitrix.safety.permissions import guest_run, read_only_run
        from cognitrix.sessions import runs as chat_runs
        from cognitrix.tools.utils import ToolExecutionContext, signed_in_host_process_mode

        account, agent = thread.account, thread.agent
        sid = str(session.id)
        if thread.guest:
            read_only_run.set(GUEST_REASON)
            guest_run.set(True)
        me = asyncio.current_task()
        run = chat_runs.start_run(
            account.user_id, str(agent.id), sid, prompt=text,
            history_len=len(getattr(session, 'chat', None) or []),
            stop=lambda: bool(me and me.cancel()),
        )
        self._turn_runs[sid] = run
        collected: list[str] = []

        async def output(payload: Any = None, *args, **kwargs) -> None:
            if not isinstance(payload, dict):
                return
            run.record({**payload, 'session_id': sid})
            if payload.get('type') == 'generate' and payload.get('content'):
                collected.append(str(payload['content']))
            elif payload.get('type') == 'tool' and payload.get('status') == 'completed':
                outcome = payload.get('outcome')
                card = outcome.get('card') if isinstance(outcome, dict) else None
                if isinstance(card, dict):
                    collected.append('\n\n' + to_markdown(card, numbered_buttons=True) + '\n\n')

        async def emit(event: dict[str, Any]) -> None:
            run.record({**event, 'session_id': sid})
            kind = event.get('type')
            if kind == 'approval_request':
                rid = str(event.get('request_id'))
                self._approvals[sid] = rid
                from cognitrix.channels.service import _params_preview

                await self.reply(thread, (
                    f'{agent.name} wants to run {event.get("tool_name")} '
                    f'({event.get("risk_level", "")} risk):\n\n{_params_preview(event.get("params"))}\n\n'
                    'Reply APPROVE or DENY. You can also answer it in Cognitrix.'))
            elif kind == 'question_request':
                rid = str(event.get('request_id'))
                self._questions[sid] = rid
                options = [o for o in event.get('options') or [] if isinstance(o, dict)]
                self._question_options[sid] = options
                body = str(event.get('prompt') or 'Question')
                if event.get('details'):
                    body += f'\n\n{event["details"]}'
                if options:
                    body += '\n\n' + '\n'.join(f'{i}. {o.get("label")}' for i, o in enumerate(options, 1))
                body += '\n\nReply with the number or your answer.' if options else '\n\nReply with your answer.'
                await self.reply(thread, body)

        approval_token = web_turn_ctx.set({
            'emit': emit, 'session_id': sid, 'bypass': False, 'user_key': account.user_id,
            'agent_name': agent.name, 'origin': f'email:{account.id}',
        })
        question_token = question_turn_ctx.set(QuestionTurnContext(
            emit=emit, session_id=sid, stream_id=f'email-{sid}', user_key=account.user_id,
        ))
        terminal = {'type': 'turn_complete', 'content': '', 'session_id': sid}
        run.steerable = not thread.guest
        run.follows_up = True
        run_token = chat_runs.current_run.set(run)
        try:
            await asyncio.wait_for(
                session(text, agent, interface='web', stream=True, output=output,
                        wsquery={'type': 'generate', 'action': 'chat_message'},
                        tool_context=ToolExecutionContext(
                            session_id=sid, user_id=account.user_id, agent_id=str(agent.id),
                            host_process_mode=signed_in_host_process_mode(),
                        )),
                timeout=CHAT_TIMEOUT,
            )
        except asyncio.CancelledError:
            terminal = {'type': 'turn_stopped', 'content': '', 'session_id': sid}
            collected.append('\n\n(Stopped.)')
        except TimeoutError:
            terminal = {'type': 'error', 'content': 'timed out', 'session_id': sid}
            collected.append('\n\n(That took too long and was stopped.)')
            try:
                await session.save()
            except Exception:
                logger.exception('Could not save session %s after a timeout', sid)
        except Exception as exc:
            logger.exception('Email turn failed')
            terminal = {'type': 'error', 'content': str(exc), 'session_id': sid}
            collected.append(f'\n\nSomething went wrong: {exc}')
        finally:
            web_turn_ctx.reset(approval_token)
            question_turn_ctx.reset(question_token)
            chat_runs.current_run.reset(run_token)
            self._questions.pop(sid, None)
            self._question_options.pop(sid, None)
            self._approvals.pop(sid, None)
            run.finish(terminal)
            if self._turn_runs.get(sid) is run:
                self._turn_runs.pop(sid, None)
            reply = strip_reasoning(''.join(collected)).strip()
            if reply:
                await self.reply(thread, reply)
            if run.unsent and terminal.get('type') != 'turn_stopped':
                follow_up = '\n\n'.join(str(m.get('content') or '') for m in run.unsent)
                self._turns[sid] = asyncio.create_task(self._turn(thread, session, follow_up))


def _pick_option(text: str, options: list[dict]) -> str:
    """The id of the option a reply names, by number or by label; '' if none."""
    first = (text or '').strip().split('\n', 1)[0].strip().rstrip('.!').lower()
    if not first or not options:
        return ''
    if first.isdigit() and 1 <= int(first) <= len(options):
        return str(options[int(first) - 1].get('id') or '')
    for option in options:
        if str(option.get('label') or '').strip().lower() == first:
            return str(option.get('id') or '')
    return ''


class _Thread:
    """Where a turn's mail goes: the agent's address, the other side, the thread."""

    def __init__(self, *, account: EmailAccount, agent, session_id: str, peer: str, subject: str,
                 reply_to: str, references: list[str], guest: bool):
        self.account = account
        self.agent = agent
        self.session_id = session_id
        self.peer = peer
        self.subject = subject
        self.reply_to = reply_to
        self.references = [r for r in references if r]
        self.guest = guest


_service: EmailService | None = None


def get_service() -> EmailService | None:
    return _service


async def start_email() -> EmailService | None:
    global _service
    if not email_enabled():
        return None
    _service = EmailService()
    await _service.start()
    return _service


async def stop_email() -> None:
    global _service
    if _service is not None:
        await _service.stop()
        _service = None
