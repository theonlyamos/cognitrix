"""Tools that let an agent start new chats and talk with other agents in them.

See ``cognitrix/sessions/agent_chats.py`` for how the chats run and the limits.
"""
from __future__ import annotations

from typing import Any

from cognitrix.sessions import agent_chats
from cognitrix.sessions.agent_chats import AgentChatError
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import EntityRef, ToolOutcome, current_execution_context

CHAT_TOOL_CATEGORY = 'system'


def _who() -> str:
    """The person the calling turn works for; refuses turns without one."""
    ctx = current_execution_context()
    if ctx.run_id or ctx.task_id:
        raise AgentChatError(
            'not_in_task', 'Task runs cannot start chats. Use Notify Me to alert the person instead.')
    if ctx.restricted:
        raise AgentChatError('not_allowed', 'API key calls cannot start chats.')
    if not ctx.user_id:
        raise AgentChatError('no_owner', 'Starting a chat needs a signed-in person to own it.')
    return str(ctx.user_id)


def _agent_name(parent: Any) -> str:
    return str(getattr(parent, 'name', '') or 'An agent')


async def _own_chat(user_key: str, chat_id: str, parent: Any):
    """A chat the calling agent started for this person."""
    from cognitrix.session_ownership import OwnershipConflict, OwnershipNotFound, require_active_owned
    from cognitrix.sessions.base import Session

    chat_id = str(chat_id or '').strip()
    if not chat_id:
        raise AgentChatError('chat_required', 'Give the chat id that Start Chat returned.')
    session = None
    try:
        binding = await require_active_owned(chat_id, user_key)
        session = await Session.get(chat_id)
        if session is not None and str(session.agent_id or '') != binding.agent_id:
            session = None
    except (OwnershipNotFound, OwnershipConflict):
        session = None
    started_by = (getattr(session, 'started_by', None) or {}) if session is not None else {}
    parent_id = str(getattr(parent, 'id', '') or '')
    if session is None or not parent_id or str(started_by.get('agent_id') or '') != parent_id:
        raise AgentChatError('chat_not_found', f'No chat {chat_id} that you started.')
    return session


def _failure(exc: AgentChatError) -> ToolOutcome:
    return ToolOutcome.failure(exc.code, exc.message)


def _message(text: Any) -> str:
    text = str(text or '').strip()
    if not text:
        raise AgentChatError('message_required', 'Write the message to send.')
    if len(text) > agent_chats.MESSAGE_MAX_CHARS:
        raise AgentChatError(
            'message_too_long', f'Messages hold at most {agent_chats.MESSAGE_MAX_CHARS} characters.')
    return text


@tool(category=CHAT_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web'])
async def start_chat(message: str, agent: str = '', report_back: bool = True, *, parent=None) -> ToolOutcome:
    """Start a new chat that works on its own in the background, with yourself or another agent.
    Use it to hand off a separate piece of work, or to talk with another agent.
    The person sees the chat in their chat list and can open it.

    :param message: The first message, with everything the chat needs to know to do the work.
    :param agent: Name or id of the agent to talk to. Leave empty to start a chat with yourself.
    :param report_back: Send the chat's reply back to this chat when it is ready (default true).
    """
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions.base import Session

    try:
        user_key = _who()
        text = _message(message)
        ctx = current_execution_context()
        current = await Session.get(ctx.session_id) if ctx.session_id else None
        depth = agent_chats.depth_of(current) + 1 if current is not None else 1
        if depth > agent_chats.MAX_DEPTH:
            raise AgentChatError(
                'too_deep', 'This chat was itself started by an agent and cannot start more chats.')
        if agent_chats.running_agent_chats(user_key) >= agent_chats.MAX_RUNNING_PER_USER:
            raise AgentChatError(
                'too_many_chats',
                f'{agent_chats.MAX_RUNNING_PER_USER} agent chats are already working. '
                'Wait for one to finish (Check Chat).')
        ref = str(agent or '').strip()
        target = parent
        if ref:
            target = (await find_visible_agent(user_key, agent_id=ref)
                      or await find_visible_agent(user_key, name=ref))
            if target is None:
                raise AgentChatError('agent_not_found', f'No agent called "{ref}". List Agents shows who you can talk to.')
        elif target is None or not getattr(target, 'id', None):
            raise AgentChatError('agent_required', 'Name the agent to start the chat with.')
        if current is None or current.group_chat or current.task_id:
            report_back = False
        started_by = {
            'agent_id': str(getattr(parent, 'id', '') or ''),
            'agent_name': _agent_name(parent),
            'session_id': str(current.id) if current is not None else None,
            'depth': depth,
            'report_back': bool(report_back),
            'exchanges': 1,
        }
        session = await agent_chats.create_chat(user_key, target, started_by)
    except AgentChatError as exc:
        return _failure(exc)

    who = 'yourself' if str(target.id) == str(getattr(parent, 'id', '')) else target.name
    agent_chats.send_turn(user_key, target, session,
                          f'{_agent_name(parent)} (agent) started this chat:\n\n{text}')
    after = ('Its reply will be sent to you here when it is ready.' if report_back
             else 'Use Check Chat to read its reply.')
    return ToolOutcome.success(
        f'Started a chat with {who} (chat id {session.id}). It is working in the background. {after}',
        entities=[EntityRef(type='session', id=str(session.id), name=target.name)])


@tool(category=CHAT_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web'])
async def check_chat(chat_id: str, *, parent=None) -> ToolOutcome:
    """See whether a chat you started is still working, and read its latest reply.

    :param chat_id: The chat id that Start Chat returned.
    """
    try:
        user_key = _who()
        session = await _own_chat(user_key, chat_id, parent)
    except AgentChatError as exc:
        return _failure(exc)
    if agent_chats.is_running(user_key, str(session.id)):
        return ToolOutcome.success(f'Chat {session.id} is still working. Check again later.')
    reply = agent_chats.last_reply(session)
    if len(reply) > agent_chats.REPLY_PREVIEW_CHARS:
        reply = reply[:agent_chats.REPLY_PREVIEW_CHARS] + '\n… (cut short)'
    return ToolOutcome.success(
        f'Chat {session.id} is done. Latest reply:\n\n{reply or "(no reply yet)"}')


@tool(category=CHAT_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web'])
async def message_chat(chat_id: str, message: str, *, parent=None) -> ToolOutcome:
    """Send another message to a chat you started, for example to answer it or ask a follow-up.
    It runs in the background like the first message did.

    :param chat_id: The chat id that Start Chat returned.
    :param message: What to say.
    """
    from cognitrix.agents.access import find_visible_agent

    try:
        user_key = _who()
        text = _message(message)
        session = await _own_chat(user_key, chat_id, parent)
        if agent_chats.is_running(user_key, str(session.id)):
            raise AgentChatError('chat_busy', 'That chat is still working. Wait for its reply first.')
        target = await find_visible_agent(user_key, agent_id=str(session.agent_id or ''))
        if target is None:
            raise AgentChatError('agent_not_found', 'The agent in that chat no longer exists.')
        await agent_chats.bump_exchanges(session)
    except AgentChatError as exc:
        return _failure(exc)
    agent_chats.send_turn(user_key, target, session, f'{_agent_name(parent)} (agent):\n\n{text}')
    return ToolOutcome.success(f'Sent. Chat {session.id} is working on it in the background.')
