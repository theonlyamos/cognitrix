"""Background chat runs that outlive the browser connection that started them.

A web chat turn runs as a server-side task. Its events are recorded here, keyed
by (user, session), so the browser can leave the conversation and later
reattach: the run replays what it has produced so far, then follows live
output until the turn ends. Several sessions can run at once.

Streamed text chunks are merged into one growing event as they arrive, so a
long reply costs one log entry rather than one per token, and a reattaching
reader gets the text so far in a single event.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# A finished run stays readable briefly so a client that reattaches right as
# the turn ends still gets the terminal event, then the saved history takes over.
FINISHED_RETENTION_SECONDS = 120.0
# Safety valve for pathological turns: past this many entries, further
# non-terminal events are not recorded (the turn itself is unaffected).
MAX_EVENTS_PER_RUN = 20_000
MAX_RUNS_PER_USER = 32

TERMINAL_TYPES = frozenset({'turn_complete', 'turn_stopped', 'multistep_result'})
_MERGEABLE_KEYS = frozenset({'type', 'content', 'action', 'complete', 'session_id'})

# Messages sent to a chat while its turn runs wait in the run's inbox and are
# handed to the model after the current tool round ("steering"). Bounded so a
# runaway sender can't grow it without limit.
MAX_INBOX = 20
MAX_INBOX_CHARS = 20_000

# Sentinel yielded by ChatRun.follow when nothing arrived for a while, so the
# HTTP layer can send a keep-alive ping and notice disconnects.
IDLE = object()


def _mergeable(event: dict[str, Any]) -> bool:
    return (
        event.get('type') == 'generate'
        and isinstance(event.get('content'), str)
        and set(event) <= _MERGEABLE_KEYS
    )


def _status_for(terminal: dict[str, Any]) -> str:
    kind = terminal.get('type')
    if kind == 'turn_stopped':
        return 'stopped'
    if kind == 'error':
        return 'error'
    return 'complete'


@dataclass
class InboxMessage:
    """A message sent to a chat while its turn was running."""
    content: str
    source: str = 'web'
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    sent_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    )

    def as_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'content': self.content, 'source': self.source, 'sent_at': self.sent_at}


class InboxFull(Exception):
    pass


@dataclass
class ChatRun:
    user_key: str
    agent_id: str
    session_id: str
    prompt: str = ''
    # Saved history entries before this turn: a page that reopens the
    # conversation shows only these, then replays the turn on top.
    history_len: int = 0
    stop: Callable[[], bool] | None = None
    # True for a chat an agent started (see sessions/agent_chats.py).
    started_by_agent: bool = False
    # Set once the turn loop is reading this run's inbox. Runs started by
    # paths that don't (task mode, team chats) refuse steering instead of
    # holding messages nobody will read.
    steerable: bool = False
    # The code running this turn starts the follow-up for unread messages
    # itself (chat apps, agent-started chats), so pages must not resend them.
    follows_up: bool = False
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    )
    status: str = 'running'
    unsent: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    finished_at: float | None = None
    # Messages waiting to be read by the running turn (see steer()).
    inbox: list[InboxMessage] = field(default_factory=list)
    # Messages the model has already read this turn, oldest first.
    read: list[InboxMessage] = field(default_factory=list)
    _changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def running(self) -> bool:
        return self.status == 'running'

    def _notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def record(self, event: dict[str, Any]) -> None:
        """Append one event the turn emitted (no-op once the run ended)."""
        if not self.running or not isinstance(event, dict):
            return
        if event.get('type') in TERMINAL_TYPES:
            self.finish(event)
            return
        last = self.events[-1] if self.events else None
        if (
            last is not None
            and _mergeable(last)
            and _mergeable(event)
            and all(last.get(k) == event.get(k) for k in ('action', 'complete', 'session_id'))
        ):
            last['content'] += event['content']
        elif len(self.events) < MAX_EVENTS_PER_RUN:
            self.events.append(dict(event))
        else:
            return
        self._notify()

    # --- steering ------------------------------------------------------------

    def steer(self, content: str, source: str = 'web') -> InboxMessage | None:
        """Hold a message for the running turn. None once the run has ended
        (the caller then starts a normal turn instead)."""
        if not self.running:
            return None
        text = (content or '').strip()
        if not text:
            raise ValueError('Message is empty')
        if len(self.inbox) >= MAX_INBOX:
            raise InboxFull('Too many messages are already waiting for this chat')
        message = InboxMessage(content=text[:MAX_INBOX_CHARS], source=source)
        self.inbox.append(message)
        self.record({'type': 'message_queued', **message.as_dict()})
        return message

    def withdraw(self, message_id: str) -> InboxMessage | None:
        """Take a waiting message back. None if it was already read."""
        for i, message in enumerate(self.inbox):
            if message.id == message_id:
                del self.inbox[i]
                self.record({'type': 'message_withdrawn', 'id': message_id})
                return message
        return None

    def take_inbox(self) -> list[InboxMessage]:
        """Everything waiting, oldest first, removed from the inbox."""
        taken, self.inbox = self.inbox, []
        self.read.extend(taken)
        return taken

    def finish(self, terminal: dict[str, Any]) -> None:
        """Record the single terminal event and mark the run ended.

        Messages still in the inbox were never read by the model: they go out
        on the terminal event as ``unsent`` so the sender can send them as the
        next turn, and stay readable via ``unsent`` until then. A run that
        ``follows_up`` itself leaves them off the event, except messages
        typed on the web when it was stopped (its follow-up won't run).
        """
        if not self.running:
            return
        terminal = dict(terminal)
        if self.inbox:
            self.unsent = [m.as_dict() for m in self.inbox]
            self.inbox = []
            shown = self.unsent
            if self.follows_up:
                stopped = terminal.get('type') == 'turn_stopped'
                shown = [m for m in self.unsent if stopped and m.get('source') == 'web']
            if shown:
                terminal['unsent'] = shown
        self.events.append(terminal)
        self.status = _status_for(terminal)
        self.finished_at = time.monotonic()
        self.stop = None
        self._notify()

    def summary(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'session_id': self.session_id,
            'agent_id': self.agent_id,
            'status': self.status,
            'started_at': self.started_at,
            'prompt': self.prompt,
            'history_len': self.history_len,
            'inbox': [m.as_dict() for m in self.inbox],
        }

    async def follow(self, idle_timeout: float = 15.0) -> AsyncIterator[Any]:
        """Yield every event from the start, then live ones until the run ends.

        A growing text event is yielded as deltas: first everything so far,
        then only what was appended since. Yields ``IDLE`` after
        ``idle_timeout`` seconds without news.
        """
        index = 0
        sent = 0  # characters of events[index] already yielded (text events)
        while True:
            progressed = False
            while index < len(self.events):
                event = self.events[index]
                is_last = index == len(self.events) - 1
                if _mergeable(event):
                    content = event['content']
                    if len(content) > sent:
                        yield {**event, 'content': content[sent:]}
                        sent = len(content)
                        progressed = True
                    if is_last and self.running:
                        break  # it may still grow
                    index += 1
                    sent = 0
                    continue
                index += 1
                sent = 0
                progressed = True
                if not _still_relevant(event):
                    continue
                yield event
            if not self.running and index >= len(self.events):
                return
            if progressed:
                continue
            changed = self._changed
            try:
                await asyncio.wait_for(changed.wait(), timeout=idle_timeout)
            except TimeoutError:
                yield IDLE


def _still_relevant(event: dict[str, Any]) -> bool:
    """Skip prompts that were already answered when replaying history."""
    request_id = event.get('request_id')
    if not request_id:
        return True
    if event.get('type') == 'question_request':
        from cognitrix.questions import broker

        return request_id in broker._PENDING
    if event.get('type') == 'approval_request':
        from cognitrix.safety import approval_gate

        return request_id in approval_gate._WEB_PENDING
    return True


_RUNS: dict[tuple[str, str], ChatRun] = {}

# The run whose turn this task is executing, so the turn loop can read its
# inbox (sessions/base.py). Set by whoever starts the turn.
current_run: contextvars.ContextVar[ChatRun | None] = contextvars.ContextVar(
    'cognitrix_current_chat_run', default=None,
)


def _prune() -> None:
    now = time.monotonic()
    for key, run in list(_RUNS.items()):
        if (
            run.finished_at is not None
            and now - run.finished_at >= FINISHED_RETENTION_SECONDS
        ):
            _RUNS.pop(key, None)


def start_run(
    user_key: str,
    agent_id: str,
    session_id: str,
    *,
    prompt: str = '',
    history_len: int = 0,
    stop: Callable[[], bool] | None = None,
) -> ChatRun:
    """Register the run for one turn, replacing a finished one for the session."""
    _prune()
    key = (str(user_key), str(session_id))
    existing = _RUNS.get(key)
    if existing is not None and existing.running:
        # Same session started again while a turn is in flight (e.g. from a
        # second tab). The newer turn takes over the record; the older turn
        # keeps running and still saves its own result.
        existing.finish({'type': 'turn_stopped', 'content': '', 'session_id': session_id})
    run = ChatRun(
        user_key=str(user_key),
        agent_id=str(agent_id),
        session_id=str(session_id),
        prompt=prompt if isinstance(prompt, str) else '',
        history_len=max(0, int(history_len or 0)),
        stop=stop,
    )
    _RUNS[key] = run
    user_runs = [k for k in _RUNS if k[0] == run.user_key]
    if len(user_runs) > MAX_RUNS_PER_USER:
        finished = [k for k in user_runs if not _RUNS[k].running]
        for k in finished[: len(user_runs) - MAX_RUNS_PER_USER]:
            _RUNS.pop(k, None)
    return run


def get_run(user_key: str, session_id: str) -> ChatRun | None:
    _prune()
    return _RUNS.get((str(user_key), str(session_id)))


def active_runs(user_key: str, agent_id: str | None = None) -> list[ChatRun]:
    _prune()
    return [
        run for (owner, _), run in _RUNS.items()
        if owner == str(user_key)
        and run.running
        and (agent_id is None or run.agent_id == str(agent_id))
    ]


def stop_run(user_key: str, session_id: str) -> bool:
    run = get_run(user_key, session_id)
    if run is None or not run.running or run.stop is None:
        return False
    return bool(run.stop())


def steer(user_key: str, session_id: str, content: str, source: str = 'web') -> InboxMessage | None:
    """Send a message into a chat's running turn. None when nothing that can
    read it is running (the caller then starts a normal turn)."""
    run = get_run(user_key, session_id)
    if run is None or not run.running or not run.steerable:
        return None
    return run.steer(content, source)
