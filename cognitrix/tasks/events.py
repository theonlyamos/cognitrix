import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from odbms import Model
from pydantic import Field

logger = logging.getLogger('cognitrix.log')

TEXT_FLUSH_SECONDS = 0.15
TEXT_FLUSH_CHARS = 256


class TaskRunEvent(Model):
    run_id: str
    session_id: str | None = None
    step_index: int | None = None
    sequence: int
    kind: str
    agent_name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class _PendingText:
    content: str = ''
    last_flush: float = 0.0
    emitted: bool = False
    step_index: int | None = None
    agent_name: str | None = None
    attempt: int = 1


def event_payload(event: TaskRunEvent) -> dict[str, Any]:
    return {
        'type': 'task_run_event',
        'id': event.id,
        'run_id': event.run_id,
        'session_id': event.session_id,
        'step_index': event.step_index,
        'sequence': event.sequence,
        'kind': event.kind,
        'agent_name': event.agent_name,
        'data': event.data,
        'created_at': event.json().get('created_at'),
    }


async def events_after(run_id: str, sequence: int) -> list[TaskRunEvent]:
    rows = await TaskRunEvent.find({'run_id': run_id})
    return sorted(
        (row for row in rows if row.sequence > sequence),
        key=lambda row: row.sequence,
    )


class TaskRunEventEmitter:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._pending: dict[tuple[str, str], _PendingText] = {}

    async def _save_locked(
        self,
        kind: str,
        *,
        session_id: str | None = None,
        step_index: int | None = None,
        agent_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> TaskRunEvent | None:
        self._sequence += 1
        event = TaskRunEvent(
            run_id=self.run_id,
            session_id=session_id,
            step_index=step_index,
            sequence=self._sequence,
            kind=kind,
            agent_name=agent_name,
            data=data or {},
        )
        try:
            await event.save()
            return event
        except Exception:
            logger.exception('Could not persist task-run event %s for %s', kind, self.run_id)
            return None

    async def emit(self, kind: str, **kwargs) -> TaskRunEvent | None:
        async with self._lock:
            return await self._save_locked(kind, **kwargs)

    async def text_delta(
        self,
        *,
        session_id: str,
        step_index: int | None,
        agent_name: str | None,
        turn_id: str,
        attempt: int,
        content: str,
    ) -> TaskRunEvent | None:
        if not content:
            return None
        async with self._lock:
            key = (session_id, turn_id)
            pending = self._pending.setdefault(key, _PendingText())
            pending.content += content
            pending.step_index = step_index
            pending.agent_name = agent_name
            pending.attempt = attempt
            now = time.monotonic()
            should_flush = (
                not pending.emitted
                or len(pending.content) >= TEXT_FLUSH_CHARS
                or now - pending.last_flush >= TEXT_FLUSH_SECONDS
            )
            if not should_flush:
                return None
            chunk = pending.content
            pending.content = ''
            pending.emitted = True
            pending.last_flush = now
            return await self._save_locked(
                'text_delta',
                session_id=session_id,
                step_index=step_index,
                agent_name=agent_name,
                data={'turn_id': turn_id, 'attempt': attempt, 'content': chunk},
            )

    async def flush_text(self, *, session_id: str, turn_id: str) -> TaskRunEvent | None:
        async with self._lock:
            pending = self._pending.get((session_id, turn_id))
            if pending is None or not pending.content:
                return None
            chunk = pending.content
            pending.content = ''
            pending.last_flush = time.monotonic()
            return await self._save_locked(
                'text_delta',
                session_id=session_id,
                step_index=pending.step_index,
                agent_name=pending.agent_name,
                data={
                    'turn_id': turn_id,
                    'attempt': pending.attempt,
                    'content': chunk,
                },
            )
