"""Group chat: the user talks with every agent of a team in one conversation.

How a turn works:

1. The user's message goes into the shared transcript.
2. It picks who answers. Agents @mentioned in the message answer in the order
   they were mentioned; ``@all`` (or ``@everyone``/``@team``) asks every
   member. With no mention, the agent who last spoke answers, or the team
   leader in a new chat, or the first member.
3. Each agent answers with its own model, tools and system prompt. It sees the
   conversation as a group chat: its own messages (with its tool calls) as its
   own, and everything the user and teammates said as named messages.
4. When an agent's reply @mentions teammates, they answer next (a handoff).
   Handoffs stop after ``MAX_AGENT_TURNS`` replies for one user message, and an
   agent answers at most ``MAX_TURNS_PER_AGENT`` times per user message.

Every entry in the shared transcript carries ``speaker_id`` (an agent id, or
``"user"``) and ``speaker`` (a display name).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cognitrix.agents.base import Agent
    from cognitrix.sessions.base import Session
    from cognitrix.teams.base import Team

logger = logging.getLogger('cognitrix.log')

USER_SPEAKER = 'user'
EVERYONE_HANDLES = ('all', 'everyone', 'team')

MAX_AGENT_TURNS = max(1, int(os.getenv('COGNITRIX_GROUP_MAX_TURNS', '6')))
MAX_TURNS_PER_AGENT = 2
# Each agent turn only needs recent context; the window shaper trims further.
VIEW_ENTRIES = 400

_THINK = re.compile(r'<think>.*?</think>', re.DOTALL)
_CODE = re.compile(r'```.*?```|`[^`\n]*`', re.DOTALL)


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def handle(name: str) -> str:
    """The @handle for an agent name: spaces become dashes."""
    return re.sub(r'\s+', '-', (name or '').strip())


def _aliases(name: str) -> set[str]:
    base = (name or '').strip().lower()
    if not base:
        return set()
    squashed = re.sub(r'\s+', ' ', base)
    return {
        squashed,
        squashed.replace(' ', '-'),
        squashed.replace(' ', '_'),
        squashed.replace(' ', ''),
    }


def find_mentions(text: str, agents: Sequence[Agent]) -> list[str]:
    """Agent ids @mentioned in ``text``, in order of first mention.

    Matches the agent's name (spaces allowed) or its handle, case-insensitive,
    and only when the mention ends at a word boundary. ``@all`` expands to
    every agent. Mentions inside code are ignored.
    """
    if not text or '@' not in text:
        return []
    plain = _CODE.sub(' ', _THINK.sub(' ', text))
    lowered = plain.lower()
    candidates: list[tuple[str, str]] = []  # (alias, agent id), longest first
    for agent in agents:
        for alias in _aliases(agent.name):
            candidates.append((alias, str(agent.id)))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)

    found: list[str] = []
    for match in re.finditer(r'(?<![\w@])@', lowered):
        rest = lowered[match.end():]
        everyone = next(
            (h for h in EVERYONE_HANDLES
             if rest.startswith(h) and not _word_char(rest, len(h))),
            None,
        )
        if everyone:
            for agent in agents:
                if str(agent.id) not in found:
                    found.append(str(agent.id))
            continue
        for alias, agent_id in candidates:
            if rest.startswith(alias) and not _word_char(rest, len(alias)):
                if agent_id not in found:
                    found.append(agent_id)
                break
    return found


def _word_char(text: str, index: int) -> bool:
    return index < len(text) and (text[index].isalnum() or text[index] in '_-')


def last_agent_speaker(chat: Sequence[dict[str, Any]], member_ids: set[str]) -> str | None:
    for entry in reversed(chat):
        speaker = str(entry.get('speaker_id') or '')
        if speaker in member_ids:
            return speaker
    return None


def first_responders(
    message: str,
    agents: Sequence[Agent],
    chat: Sequence[dict[str, Any]],
    leader_id: str | None,
) -> list[str]:
    """Who answers a user message (see module docstring)."""
    if not agents:
        return []
    mentioned = find_mentions(message, agents)
    if mentioned:
        return mentioned
    ids = [str(a.id) for a in agents]
    previous = last_agent_speaker(chat, set(ids))
    if previous:
        return [previous]
    if leader_id and str(leader_id) in ids:
        return [str(leader_id)]
    return [ids[0]]


def visible_text(entry: dict[str, Any]) -> str:
    """What other participants see of a transcript entry ('' = nothing)."""
    role = str(entry.get('role', '')).lower()
    kind = entry.get('type', 'text')
    if role == 'user' and kind == 'text':
        return str(entry.get('content') or '').strip()
    if role == 'assistant' and kind == 'text' and not entry.get('tool_calls'):
        if entry.get('turn_error'):
            return ''
        return _THINK.sub('', str(entry.get('content') or '')).strip()
    return ''


def agent_view(
    chat: Sequence[dict[str, Any]],
    agent_id: str,
) -> tuple[list[dict[str, Any]], str]:
    """The transcript as one agent sees it, plus what it should answer.

    The agent's own entries stay as they are (tool calls included), so its
    history is a normal conversation. Everything said by others between two
    of its turns is folded into one named user message. The last such block,
    the news since the agent last spoke, is returned separately as the
    message to answer.
    """
    history: list[dict[str, Any]] = []
    pending: list[str] = []
    for entry in list(chat)[-VIEW_ENTRIES:]:
        speaker = str(entry.get('speaker_id') or '')
        if speaker == str(agent_id):
            if pending:
                history.append(_folded(pending))
                pending = []
            if history or str(entry.get('role', '')).lower() == 'user':
                history.append(_strip_speaker(entry))
            elif visible_text(entry):
                # Never open a window on an assistant entry.
                history.append(_folded([f"(earlier) you said: {visible_text(entry)}"]))
            continue
        text = visible_text(entry)
        if not text:
            continue
        name = entry.get('speaker') or ('User' if speaker == USER_SPEAKER else 'Teammate')
        label = 'User' if speaker == USER_SPEAKER else f'{name} (teammate)'
        pending.append(f'{label}: {text}')
    trigger = '\n\n'.join(pending) or '(Continue the conversation.)'
    return history, trigger


def _folded(lines: list[str]) -> dict[str, Any]:
    return {'role': 'User', 'type': 'text', 'content': '\n\n'.join(lines)}


def _strip_speaker(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in ('speaker_id', 'speaker', 'timestamp')}


def group_system_prompt(agent: Agent, team: Team, members: Sequence[Agent]) -> str:
    others = [m for m in members if str(m.id) != str(agent.id)]
    lines = []
    for member in others:
        role = ' (team lead)' if str(member.id) == str(team.leader_id or '') else ''
        about = (
            getattr(member, 'description', '') or getattr(member, 'system_prompt', '') or ''
        ).strip().splitlines()
        summary = f' - {about[0][:160]}' if about and about[0] else ''
        lines.append(f'- @{handle(member.name)}{role}{summary}')
    roster = '\n'.join(lines) or '- (no teammates)'
    lead = ''
    if str(team.leader_id or '') == str(agent.id):
        lead = ('You are the team lead: when a request needs a teammate\'s skills, '
                'hand that part to them by @mention.\n')
    return (
        f"\n\n## Group chat\n"
        f"You are {agent.name} (@{handle(agent.name)}), in a group chat with the user "
        f"and your teammates on the team \"{team.name}\".\n"
        f"Teammates:\n{roster}\n\n"
        f"{lead}"
        "Messages from others arrive prefixed with who said them. Reply as yourself, "
        "without a name prefix, and do not write messages for anyone else.\n"
        "To hand work to a teammate, @mention them in your reply with their handle and "
        "say exactly what you need. They answer after you, in the order you mention them, "
        "and each sees everything said before, so you can line up several teammates in "
        "one reply (for example: @Researcher find X, then @Writer use it to write Y). "
        "Only mentions in your visible reply count. Only @mention a teammate when you "
        "want them to act now, and never @mention yourself. Ask a teammate before "
        "asking the user. If a teammate already answered part of the request, build "
        "on it instead of repeating it."
    )


Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def run_group_turn(
    session: Session,
    team: Team,
    members: Sequence[Agent],
    message: str,
    *,
    emit: Emit,
    run_agent: Callable[[Agent, Session, str, Emit], Awaitable[None]],
) -> list[str]:
    """Answer one user message with the team. Returns the ids that spoke.

    ``session`` is the shared transcript; the user's message must already be
    in it. ``run_agent`` runs one agent turn on a scratch session whose chat
    is that agent's view and records the agent's new entries in it.
    """
    from cognitrix.sessions.base import Session as SessionModel

    by_id = {str(m.id): m for m in members}
    queue = first_responders(message, members, session.chat[:-1], team.leader_id)
    spoken: list[str] = []
    counts: dict[str, int] = {}

    while queue and len(spoken) < MAX_AGENT_TURNS:
        agent_id = queue.pop(0)
        agent = by_id.get(agent_id)
        if agent is None or counts.get(agent_id, 0) >= MAX_TURNS_PER_AGENT:
            continue
        counts[agent_id] = counts.get(agent_id, 0) + 1

        history, trigger = agent_view(session.chat, agent_id)
        group_agent = agent.model_copy(update={
            'system_prompt': (agent.system_prompt or '') + group_system_prompt(agent, team, members),
        })
        scratch = SessionModel(
            id=session.id,
            chat=history,
            team_id=session.team_id,
            user_id=session.user_id,
        )
        start = len(scratch.chat)
        await emit({
            'type': 'group_speaker',
            'agent_id': agent_id,
            'name': agent.name,
            'handle': handle(agent.name),
        })
        try:
            await run_agent(group_agent, scratch, trigger, emit)
        finally:
            # Keep whatever the agent produced, even when the turn is stopped.
            produced = scratch.chat[start + 1:]
            stamp = now_iso()
            for entry in produced:
                session.update_history({
                    **entry,
                    'speaker_id': agent_id,
                    'speaker': agent.name,
                    'timestamp': stamp,
                })
            await asyncio.shield(session.save())
        spoken.append(agent_id)

        reply = next(
            (visible_text(e) for e in reversed(produced) if visible_text(e)), ''
        )
        for mentioned in find_mentions(reply, members):
            if (
                mentioned != agent_id
                and mentioned not in queue
                and counts.get(mentioned, 0) < MAX_TURNS_PER_AGENT
            ):
                queue.append(mentioned)

    if queue and len(spoken) >= MAX_AGENT_TURNS:
        await emit({
            'type': 'status',
            'content': (
                f'Paused after {MAX_AGENT_TURNS} replies. Send a message to keep going.'
            ),
        })
    return spoken


def transcript_entry(message: str) -> dict[str, Any]:
    return {
        'role': 'User',
        'type': 'text',
        'content': message,
        'speaker_id': USER_SPEAKER,
        'speaker': 'You',
        'timestamp': now_iso(),
    }


def chat_title(chat: Sequence[dict[str, Any]]) -> str:
    for entry in chat:
        if entry.get('speaker_id') == USER_SPEAKER:
            text = str(entry.get('content') or '').strip().splitlines()
            if text:
                return text[0][:80]
    return 'New group chat'
