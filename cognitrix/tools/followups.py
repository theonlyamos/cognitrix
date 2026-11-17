"""Follow Up Later: come back to this chat at a set time and check on something.

Added to each signed-in chat turn (see ``Session._run_turn``), never in task
runs or incognito chats. See ``cognitrix/assistant/followups.py``.
"""
from __future__ import annotations

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

FOLLOWUP_TOOL_CATEGORY = 'system'


@tool(category=FOLLOWUP_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def follow_up_later(what: str, minutes: int = 0, why: str = '', action: str = 'schedule',
                          follow_up_id: str = '', *, parent=None) -> ToolOutcome:
    """Come back to this chat later and check on something, without the person having to ask.

    Use it when you are waiting for something that will change: a reply you or the person is
    waiting for, a deploy or build that is still running, a price or page to recheck, a deadline.
    At that time you get a turn in this same chat, check it with your tools, and tell the person
    only if something needs them.

    action: "schedule" (default), "list" (follow-ups waiting in this chat) or "cancel" (follow_up_id).
    what: what to check, specific enough that you know what to do when you come back
          ("Check whether Sam replied to the invoice email and draft a nudge if not").
    minutes: how long from now (5 minutes to 30 days). For "tomorrow morning", work it out from
          the date and time you know.
    why: one line on why it matters, to remind yourself."""
    ctx = current_execution_context()
    if ctx.task_id or ctx.run_id or not ctx.user_id or not ctx.session_id:
        return ToolOutcome.failure('chat_only', 'Follow Up Later works in a chat with a signed-in person.')
    from cognitrix.assistant import followups
    from cognitrix.sessions.base import Session

    session = await Session.get(str(ctx.session_id))
    if session is None:
        return ToolOutcome.failure('no_chat', 'This chat is not saved, so there is nothing to come back to.')
    if getattr(session, 'incognito', False):
        return ToolOutcome.failure('incognito', "Incognito chats can't have follow-ups.")
    agent_id = str(getattr(parent, 'id', '') or getattr(session, 'agent_id', '') or '')
    user_id = str(ctx.user_id)
    action = str(action or 'schedule').strip().lower()
    if action in ('list', 'show'):
        rows = await followups.pending_for(user_id, str(ctx.session_id))
        if not rows:
            return ToolOutcome.success('No follow-ups are waiting in this chat.')
        return ToolOutcome.success('\n'.join(f'- {r.what} (id {r.id}, due {r.due_at} UTC)' for r in rows))
    if action in ('cancel', 'delete', 'remove'):
        rows = await followups.pending_for(user_id, str(ctx.session_id))
        row = next((r for r in rows if str(r.id) == str(follow_up_id)), None)
        if row is None:
            return ToolOutcome.failure('not_found', 'No waiting follow-up with that id in this chat.')
        await followups.cancel(row)
        return ToolOutcome.success(f'Cancelled the follow-up: {row.what}')
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return ToolOutcome.failure('invalid_minutes', 'minutes must be a whole number.')
    try:
        row = await followups.schedule(user_id, agent_id, str(ctx.session_id), minutes=minutes, what=what, why=why)
    except ValueError as exc:
        return ToolOutcome.failure('invalid', str(exc))
    hours = minutes / 60
    when = f'{minutes} minutes' if minutes < 120 else f'{hours:.0f} hours' if hours < 48 else f'{hours / 24:.0f} days'
    return ToolOutcome.success(
        f'I will come back to this chat in about {when} (id {row.id}) to check: {row.what}. '
        'Tell the person briefly that you will follow up.')
