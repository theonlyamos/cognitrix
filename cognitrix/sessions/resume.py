"""Chat turns that carry on after the engine restarts.

A chat turn runs inside the server process (sessions/runs.py), so a restart,
a crash or closing the desktop app used to drop whatever turn was running:
the person came back to a half-finished answer and had to ask again.

Now the turn's progress and the fact that it is running are both stored:

* The chat history is saved after every tool round (sessions/base.py), not
  only at the end of the turn. The saved history is the turn's log: what the
  person asked, each tool call and its result.
* A ``ChatTurnLease`` row marks the turn as running. The process running it
  refreshes ``heartbeat_at`` every ``HEARTBEAT_SECONDS``; the row ends when the
  turn ends (finished, stopped by the person, or failed).

``recovery_loop`` looks for rows that are still running but whose process has
stopped refreshing them (``STALE_SECONDS``; on the desktop app, where one
engine runs at a time, any row from an earlier engine). It claims one with a
compare-and-set, so two processes never pick up the same turn, then:

1. Checks the saved history really is mid-turn (the last entry is the
   person's message, a tool result or a tool call, not a final reply).
2. Closes tool calls that were running when the engine stopped with an
   "interrupted" result, since their outcome is unknown.
3. Adds a short note to the turn and starts it again in the background with
   the same agent (``ContinueTurn``: no new message from the person). The
   page shows the turn as working, as for any background chat.

A turn is picked up at most ``MAX_RESUMES`` times and only within
``MAX_AGE_SECONDS`` of starting, so a turn that crashes the engine can't
loop. When it is not picked up, the person gets an Inbox alert instead.

Only chats a person or an agent started are picked up (web chats and
agent-started chats). Swarm workers have their own recovery
(swarms/service.py) and task runs theirs (tasks/recovery.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from odbms import Model

from cognitrix.app_settings import get as app_setting
from cognitrix.sessions import runs as chat_runs

logger = logging.getLogger('cognitrix.log')

HEARTBEAT_SECONDS = 30.0
STALE_SECONDS = 90.0
MAX_RESUMES = 2
MAX_AGE_SECONDS = 6 * 3600
PROMPT_CHARS = 2000

# This engine process. A lease names the process running its turn.
OWNER = uuid.uuid4().hex

RESUME_NOTE = (
    '[System: the app restarted while you were working on this. Carry on from where you '
    'left off and finish the request above. Tools that were running when it stopped are '
    'marked as interrupted: their result is unknown, so check before running one again.]'
)
INTERRUPTED_TOOL_TEXT = (
    'Interrupted: the app restarted while this was running, so its result is unknown. '
    'Check whether it finished before running it again.'
)

KINDS = ('web', 'agent')


def enabled() -> bool:
    """Settings › Chats › "Carry on after a restart" (on by default)."""
    return str(app_setting('COGNITRIX_RESUME_CHATS', '1') or '1').strip().lower() not in (
        '0', 'false', 'off', 'no')


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(when: datetime) -> str:
    return when.isoformat().replace('+00:00', 'Z')


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class ChatTurnLease(Model):
    """One running chat turn, so another engine can pick it up.

    Persistence rule: instance ``save()`` once at creation, ``update_one`` after.
    """
    user_id: str
    agent_id: str
    session_id: str
    run_id: str
    kind: str = 'web'
    """web (the person's chat) or agent (a chat an agent started)."""
    owner: str = ''
    """The engine process running the turn."""
    status: str = 'running'
    """running, ended, resumed (a later lease carries on) or dropped."""
    heartbeat_at: str = ''
    started_at: str = ''
    """When the person's request was first sent (kept across resumes)."""
    resumes: int = 0
    prompt: str = ''
    """The request being answered, for the action reviewer on resume."""


MODELS = (ChatTurnLease,)


@dataclass
class ContinueTurn:
    """Run a chat turn again without a new message from the person: the saved
    history already ends mid-turn (sessions/base.py)."""
    goal: str = ''


@dataclass
class _Tracked:
    run: chat_runs.ChatRun
    kind: str
    prompt: str
    resumes: int = 0
    started_at: str = ''
    lease_id: str = ''
    writes: list[asyncio.Task] = field(default_factory=list)


_TRACKED: dict[str, _Tracked] = {}
_TASKS: set[asyncio.Task] = set()
_SHUTTING_DOWN = False


def shutting_down() -> None:
    """The engine is stopping: turns cancelled from now on were interrupted,
    not stopped by the person, so their leases stay open to be picked up."""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True


def _spawn(coro) -> asyncio.Task | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return None
    task = loop.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _chain(state: _Tracked, coro) -> None:
    """Run lease writes for one turn in order (created before ended)."""
    previous = state.writes[-1] if state.writes else None

    async def after():
        if previous is not None:
            try:
                await previous
            except Exception:
                pass
        await coro

    task = _spawn(after())
    if task is not None:
        state.writes = [task]


def track(run: chat_runs.ChatRun, kind: str, *, prompt: str = '', resumes: int = 0,
          started_at: str = '') -> None:
    """Mark ``run`` as a turn to pick up if the engine stops mid-way."""
    if kind not in KINDS or run.swarm_id or not enabled():
        return
    state = _Tracked(run=run, kind=kind, prompt=(prompt or '')[:PROMPT_CHARS], resumes=resumes,
                     started_at=started_at or run.started_at)
    _TRACKED[run.run_id] = state
    _chain(state, _create(state))
    run.on_finish.append(_finished)


async def _create(state: _Tracked) -> None:
    run = state.run
    now = _iso(_now())
    try:
        lease = ChatTurnLease(
            user_id=run.user_key, agent_id=run.agent_id, session_id=run.session_id,
            run_id=run.run_id, kind=state.kind, owner=OWNER, heartbeat_at=now,
            started_at=state.started_at or now, resumes=state.resumes, prompt=state.prompt,
        )
        await lease.save()
        state.lease_id = str(lease.id)
    except Exception:
        logger.exception('Could not record running chat turn %s', run.session_id)


def _finished(run: chat_runs.ChatRun) -> None:
    state = _TRACKED.pop(run.run_id, None)
    if state is None or _SHUTTING_DOWN:
        return  # left running: the next engine picks it up
    _chain(state, _end(state, 'ended'))


async def _end(state: _Tracked, status: str) -> None:
    if not state.lease_id:
        return
    try:
        await ChatTurnLease.update_one({'id': state.lease_id}, {'status': status})
    except Exception:
        logger.exception('Could not close the record of chat turn %s', state.run.session_id)


async def heartbeat() -> None:
    """Refresh the leases of the turns this engine is running."""
    now = _iso(_now())
    for state in list(_TRACKED.values()):
        if not state.lease_id or not state.run.running:
            continue
        try:
            await ChatTurnLease.update_one({'id': state.lease_id, 'status': 'running'},
                                           {'heartbeat_at': now, 'owner': OWNER})
        except Exception:
            logger.debug('Could not refresh chat turn lease %s', state.lease_id, exc_info=True)


# --- picking turns up --------------------------------------------------------

def _single_engine() -> bool:
    return os.environ.get('COGNITRIX_DESKTOP') == '1'


def _abandoned(lease: ChatTurnLease, now: datetime) -> bool:
    if lease.owner == OWNER:
        return False
    if _single_engine():
        return True
    beat = _parse(lease.heartbeat_at)
    return beat is None or now - beat >= timedelta(seconds=STALE_SECONDS)


def _role(entry: dict[str, Any]) -> str:
    return str(entry.get('role') or '').lower()


def mid_turn(chat: list[dict[str, Any]]) -> bool:
    """True when the saved history stops partway through a turn."""
    for entry in reversed(chat or []):
        if not isinstance(entry, dict):
            continue
        role, kind = _role(entry), entry.get('type')
        if role == 'system' and kind == 'turn_timing':
            return False
        if role == 'assistant':
            return bool(entry.get('tool_calls'))
        if role in ('tool', 'user'):
            return True
        # Other system rows (summaries, notes) say nothing: look further back.
    return False


def close_open_tool_calls(chat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Results for tool calls of the last round that never got one."""
    answered: set[str] = set()
    for entry in reversed(chat or []):
        if not isinstance(entry, dict):
            continue
        if _role(entry) == 'tool' and entry.get('tool_call_id'):
            answered.add(str(entry['tool_call_id']))
            continue
        if _role(entry) == 'assistant' and entry.get('tool_calls'):
            missing = []
            for call in entry.get('tool_calls') or []:
                call_id = call.get('tool_call_id') if isinstance(call, dict) else None
                if call_id and str(call_id) not in answered:
                    missing.append({
                        'role': 'tool',
                        'tool_call_id': call_id,
                        'content': INTERRUPTED_TOOL_TEXT,
                        'outcome': {
                            'status': 'stopped', 'text': INTERRUPTED_TOOL_TEXT, 'artifacts': [],
                            'entities': [], 'warnings': [], 'error': None,
                        },
                    })
            return missing
        if _role(entry) == 'user':
            return []
    return []


async def _claim(lease: ChatTurnLease) -> bool:
    try:
        changed = await ChatTurnLease.update_one(
            {'id': lease.id, 'status': 'running', 'heartbeat_at': lease.heartbeat_at},
            {'status': 'resuming', 'owner': OWNER},
        )
    except Exception:
        logger.exception('Could not claim chat turn %s', lease.session_id)
        return False
    return changed == 1


async def _drop(lease: ChatTurnLease, why: str) -> None:
    await ChatTurnLease.update_one({'id': lease.id}, {'status': 'dropped'})
    try:
        from cognitrix.tasks.automation import create_notification

        await create_notification(
            lease.user_id, 'A reply was interrupted',
            f'The app restarted while an agent was answering, and {why}. Open the chat to ask again.',
            session_id=lease.session_id,
        )
    except Exception:
        logger.exception('Could not tell %s about interrupted chat %s', lease.user_id, lease.session_id)


async def _on_done_for(lease: ChatTurnLease, agent: Any, session: Any):
    """What happens when a picked-up turn ends: the same as when it started."""
    if lease.kind != 'agent':
        return None
    from cognitrix.sessions import agent_chats

    started_by = getattr(session, 'started_by', None) or {}

    async def done(status: str, reply: str) -> None:
        if started_by.get('report_back'):
            await agent_chats.report_back(lease.user_id, agent, session, status, reply)
        elif status == 'complete':
            await agent_chats._alert_once(lease.user_id, str(session.id), f'{agent.name} finished a chat',
                                          reply[:300] or 'The chat finished.')

    return done


async def resume(lease: ChatTurnLease) -> bool:
    """Pick up one claimed turn. True when it is running again."""
    from cognitrix.agents.access import find_visible_agent
    from cognitrix.sessions import agent_chats
    from cognitrix.sessions.base import Session

    sid, user = str(lease.session_id), str(lease.user_id)
    started = _parse(lease.started_at)
    if lease.resumes >= MAX_RESUMES:
        await _drop(lease, f'it had already been picked up {MAX_RESUMES} times')
        return False
    if started is not None and _now() - started > timedelta(seconds=MAX_AGE_SECONDS):
        await _drop(lease, 'the request was too old to pick up')
        return False
    existing = chat_runs.get_run(user, sid)
    if existing is not None and existing.running:
        await ChatTurnLease.update_one({'id': lease.id}, {'status': 'ended'})
        return False
    session = await Session.get(sid)
    if session is None or not mid_turn(list(session.chat or [])):
        await ChatTurnLease.update_one({'id': lease.id}, {'status': 'ended'})
        return False
    agent = await find_visible_agent(user, agent_id=str(lease.agent_id))
    if agent is None:
        await _drop(lease, 'its agent is no longer available')
        return False

    session.update_history(close_open_tool_calls(list(session.chat or [])))
    session.update_history({
        'role': 'User', 'type': 'steer', 'content': RESUME_NOTE, 'resumed': True,
        'at': _iso(_now()),
    })
    await session.save()

    on_done = await _on_done_for(lease, agent, session)
    run = agent_chats.run_in_background(user, agent, session, ContinueTurn(goal=lease.prompt), on_done=on_done)
    run.started_by_agent = lease.kind == 'agent'
    track(run, lease.kind, prompt=lease.prompt, resumes=lease.resumes + 1, started_at=lease.started_at)
    await ChatTurnLease.update_one({'id': lease.id}, {'status': 'resumed'})
    logger.info('Picked up chat %s after a restart (resume %s)', sid, lease.resumes + 1)
    return True


async def recover_pass() -> int:
    """Pick up every abandoned turn. Returns how many are running again."""
    if not enabled():
        return 0
    now = _now()
    count = 0
    for lease in await ChatTurnLease.find({'status': 'running'}) or []:
        if lease.run_id in {s.run.run_id for s in _TRACKED.values()}:
            continue
        if not _abandoned(lease, now) or not await _claim(lease):
            continue
        try:
            if await resume(lease):
                count += 1
        except Exception:
            logger.exception('Could not pick up chat %s', lease.session_id)
            try:
                await _drop(lease, 'picking it up failed')
            except Exception:
                logger.exception('Could not close chat turn lease %s', lease.id)
    return count


async def tidy(days: int = 7) -> None:
    """Forget leases of turns that ended more than ``days`` ago."""
    cutoff = _now() - timedelta(days=days)
    for lease in await ChatTurnLease.find({}) or []:
        if lease.status == 'running':
            continue
        started = _parse(lease.started_at)
        if started is not None and started < cutoff:
            try:
                await lease.delete()
            except Exception:
                logger.debug('Could not remove old chat turn lease %s', lease.id, exc_info=True)


async def recovery_loop(interval: float = HEARTBEAT_SECONDS) -> None:
    """Keep this engine's leases fresh and pick up turns other engines dropped."""
    passes = 0
    while True:
        try:
            await heartbeat()
            await recover_pass()
            if passes % 2880 == 0:  # about once a day
                await tidy()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Chat turn recovery pass failed')
        passes += 1
        await asyncio.sleep(interval)
