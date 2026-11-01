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
class ChatRun:
    user_key: str
    agent_id: str
    session_id: str
    prompt: str = ''
    # Saved history entries before this turn: a page that reopens the
    # conversation shows only these, then replays the turn on top.
    history_len: int = 0
    stop: Callable[[], bool] | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    )
    status: str = 'running'
    events: list[dict[str, Any]] = field(default_factory=list)
    finished_at: float | None = None
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

    def finish(self, terminal: dict[str, Any]) -> None:
        """Record the single terminal event and mark the run ended."""
        if not self.running:
            return
        self.events.append(dict(terminal))
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
