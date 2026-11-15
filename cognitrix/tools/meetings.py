"""Tools for agents in live calls (cognitrix/meetings/live.py).

Join Meeting sends the calling agent into a Google Meet, Teams or Zoom call
from the browser on its desktop in the agents' sandboxed computer. It asks
the person first (see destructive_ops). Once in, the agent listens, keeps a
transcript, answers when someone says its name, and writes notes when the
call ends. Meeting Transcript reads what was said so far, Say In Meeting
speaks or writes in the call, and Leave Meeting brings it back.

A chat's agent gets these tools for its turns while the sandboxed computer
is on (``sessions/base.py``).
"""
from __future__ import annotations

from typing import Any

from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome

MEETING_TOOL_CATEGORY = 'meetings'
MEETING_TOOL_NAMES = ('join_meeting', 'meeting_transcript', 'say_in_meeting', 'leave_meeting')


def _who(parent: Any) -> tuple[str, str]:
    from cognitrix.tools.utils import current_execution_context

    ctx = current_execution_context()
    agent_id = str(getattr(parent, 'id', None) or ctx.agent_id or '')
    return str(ctx.user_id or ''), agent_id


async def _current(user_id: str, agent_id: str, meeting_id: str | None):
    from cognitrix.meetings import live
    from cognitrix.meetings.notes import MeetingNote

    if meeting_id:
        row = await MeetingNote.get(meeting_id)
        if row is None or str(row.user_id) != user_id:
            return None
        return row
    rows = [r for r in await MeetingNote.find({'user_id': user_id, 'kind': 'live'}) or []
            if str(r.agent_id) == agent_id]
    rows.sort(key=lambda r: (live.running(str(r.id)), str(getattr(r, 'created_at', '') or '')), reverse=True)
    return rows[0] if rows else None


@tool(category=MEETING_TOOL_CATEGORY, supported_interfaces=['web', 'ws', 'task'])
async def join_meeting(url: str, reply_mode: str = 'chat', name: str = '', title: str = '',
                       announce: bool = True, *, parent: Any | None = None):
    """Join a live Google Meet, Microsoft Teams or Zoom call from the browser on your sandboxed computer.
    You listen, keep a transcript, answer when someone in the call says your name, and write notes when the
    call ends (the person gets them in their Inbox and on the Meetings page). Returns right away; use
    Meeting Transcript to follow the call. The person is asked first.
    Args:
        url (str): The meeting link, starting with https://.
        reply_mode (str, optional): How to answer when someone says your name: "chat" (the call's chat,
            default), "voice" (out loud) or "off" (only take notes).
        name (str, optional): The name you join as; your own name by default.
        title (str, optional): A title for the meeting's notes.
        announce (bool, optional): Say in the call's chat that you are taking notes (default true).
    """
    from cognitrix.api.routes.meetings import start_live
    from cognitrix.meetings.notes import MeetingError

    user_id, agent_id = _who(parent)
    if not user_id:
        return ToolOutcome.failure('no_owner', 'Only a signed-in person can send an agent into a call.')
    try:
        row = await start_live(user_id, url=url, agent_id=agent_id, title=title, name=name,
                               reply_mode=reply_mode, announce=announce)
    except MeetingError as exc:
        return ToolOutcome.failure('meeting', str(exc))
    return (f'Joining "{row.title}" as {row.bot_name} (meeting {row.id}). It takes a minute or two to get in; '
            'if the host has to let you in, you wait in the lobby for up to 15 minutes. Check with Meeting '
            'Transcript. The person can watch on your computer.')


@tool(category=MEETING_TOOL_CATEGORY, supported_interfaces=['web', 'ws', 'task'])
async def meeting_transcript(meeting_id: str | None = None, last_lines: int = 60, *,
                             parent: Any | None = None):
    """Read where your current (or a given) live call is and what has been said so far.
    Args:
        meeting_id (str, optional): The meeting from Join Meeting; your latest call by default.
        last_lines (int, optional): How many of the newest transcript lines to read (default 60).
    """
    from cognitrix.meetings import live

    user_id, agent_id = _who(parent)
    row = await _current(user_id, agent_id, meeting_id)
    if row is None:
        return ToolOutcome.failure('not_found', 'You are not in a call. Join one with Join Meeting.')
    state = row.status if live.running(str(row.id)) else ('ended' if row.status == 'done' else row.status)
    lines = [ln for ln in (row.transcript or '').splitlines() if ln.strip()]
    keep = max(1, min(int(last_lines or 60), 400))
    head = f'Meeting {row.id} "{row.title}": {state}'
    if row.progress:
        head += f' ({row.progress})'
    if row.error:
        head += f'. {row.error}'
    body = '\n'.join(lines[-keep:]) if lines else '(nothing heard yet)'
    if len(lines) > keep:
        body = f'[{len(lines) - keep} earlier lines not shown]\n' + body
    out = f'{head}\n\n{body}'
    if row.notes and not live.running(str(row.id)):
        out += f'\n\nNotes:\n{row.notes}'
    return out


@tool(category=MEETING_TOOL_CATEGORY, supported_interfaces=['web', 'ws', 'task'])
async def say_in_meeting(text: str, how: str = '', meeting_id: str | None = None, *, parent: Any | None = None):
    """Say something in the live call you are in: in its chat, or out loud.
    Args:
        text (str): What to say, a few sentences at most.
        how (str, optional): "chat" or "voice"; the way the call was joined with by default.
        meeting_id (str, optional): The meeting; your current call by default.
    """
    from cognitrix.meetings import live

    user_id, agent_id = _who(parent)
    row = await _current(user_id, agent_id, meeting_id)
    text = (text or '').strip()[:live.MAX_ANSWER_CHARS]
    if not text:
        return ToolOutcome.failure('empty', 'Say what to say.')
    if row is None or not live.queue_say(str(row.id), text, how if how in ('chat', 'voice') else None):
        return ToolOutcome.failure('not_in_call', 'You are not in a call right now.')
    return 'Queued: it goes out in the call within a few seconds and shows in the transcript.'


@tool(category=MEETING_TOOL_CATEGORY, supported_interfaces=['web', 'ws', 'task'])
async def leave_meeting(meeting_id: str | None = None, *, parent: Any | None = None):
    """Leave the live call you are in. The notes are written from the transcript and sent to the person.
    Args:
        meeting_id (str, optional): The meeting; your current call by default.
    """
    from cognitrix.meetings import live

    user_id, agent_id = _who(parent)
    row = await _current(user_id, agent_id, meeting_id)
    if row is None or not live.request_leave(str(row.id)):
        return ToolOutcome.failure('not_in_call', 'You are not in a call right now.')
    return 'Leaving the call. The notes will be on the Meetings page in a minute.'
