import asyncio
import inspect
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from odbms import Model
from pydantic import Field

logger = logging.getLogger('cognitrix.log')

TEXT_FLUSH_SECONDS = 0.15
TEXT_FLUSH_CHARS = 256
EVENT_PAGE_SIZE = 256
MAX_EVENT_PAGE_SIZE = 1000


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


async def _cursor_records(cursor) -> list[dict[str, Any]]:
    if cursor is None:
        return []
    description = getattr(cursor, 'description', None)
    rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else cursor
    if inspect.isawaitable(rows):
        rows = await rows
    columns = [item[0] for item in (description or [])]
    records = []
    for row in rows or []:
        if isinstance(row, Mapping):
            records.append(dict(row))
            continue
        try:
            records.append(dict(row))
        except (TypeError, ValueError):
            records.append(dict(zip(columns, row)))
    return records


async def _relational_event_page(database, statement, params) -> list[dict]:
    """Fetch before pooled ODBMS cursors leave their connection lease."""
    pool = getattr(database, '_pool', None)
    if pool is None:
        return await _cursor_records(await database.query(statement, params))
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(statement, params)
            return await _cursor_records(cursor)


async def events_after(
    run_id: str,
    sequence: int,
    *,
    limit: int = EVENT_PAGE_SIZE,
) -> list[TaskRunEvent]:
    """Read one indexed event page after a durable sequence cursor."""
    if not run_id:
        raise ValueError('run_id is required')
    if isinstance(limit, bool) or not 1 <= limit <= MAX_EVENT_PAGE_SIZE:
        raise ValueError(
            f'limit must be between 1 and {MAX_EVENT_PAGE_SIZE}'
        )

    from odbms import DBMS

    database = DBMS.Database
    dbms = getattr(database, 'dbms', '')
    if dbms == 'mongodb':
        rows = await database.find(
            TaskRunEvent.table_name(),
            {'run_id': run_id, 'sequence': {'$gt': sequence}},
            limit=limit,
            sort=[('sequence', 1)],
        )
    elif dbms in ('sqlite', 'postgresql', 'mysql'):
        marker = ':' if dbms == 'sqlite' else '%('
        suffix = '' if dbms == 'sqlite' else ')s'

        def parameter(name: str) -> str:
            return f'{marker}{name}{suffix}'

        rows = await _relational_event_page(
            database,
            f'SELECT * FROM {TaskRunEvent.table_name()} '
            f'WHERE run_id = {parameter("run_id")} '
            f'AND sequence > {parameter("sequence")} '
            'ORDER BY sequence ASC '
            f'LIMIT {parameter("limit")}',
            {'run_id': run_id, 'sequence': sequence, 'limit': limit},
        )
    else:
        raise RuntimeError(
            f'Indexed task event paging is unsupported for database {dbms!r}'
        )
    return [TaskRunEvent(**TaskRunEvent.normalise(row)) for row in rows]


def _event_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    return json.dumps(value, ensure_ascii=False, default=str)


def project_step_tool_calls(
    events: list[TaskRunEvent],
    step_index: int,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    pending_by_id: dict[str, int] = {}
    pending_by_name: dict[str, deque[int]] = defaultdict(deque)

    for event in events:
        if event.step_index != step_index or event.kind not in {
            'tool_started', 'tool_completed',
        }:
            continue
        data = event.data or {}
        call_id = _event_text(data.get('tool_call_id')).strip() or None
        name = _event_text(data.get('tool_name')).strip() or 'tool'

        if event.kind == 'tool_started':
            index = len(calls)
            calls.append({
                'id': call_id,
                'name': name,
                'args': _event_text(data.get('params')),
                'status': 'running',
                'result': None,
            })
            if call_id:
                pending_by_id[call_id] = index
            pending_by_name[name].append(index)
            continue

        index = pending_by_id.pop(call_id, None) if call_id else None
        if index is None and call_id is None:
            queue = pending_by_name[name]
            while queue and calls[queue[0]]['status'] != 'running':
                queue.popleft()
            index = queue.popleft() if queue else None

        status = 'error' if _event_text(data.get('status')) == 'error' else 'done'
        result = _event_text(data.get('result'))
        if index is None:
            calls.append({
                'id': call_id,
                'name': name,
                'args': '',
                'status': status,
                'result': result,
            })
        else:
            calls[index] = {**calls[index], 'status': status, 'result': result}

    return calls


async def step_tool_calls(run_id: str, step_index: int) -> list[dict[str, Any]]:
    sequence = 0
    tool_events: list[TaskRunEvent] = []
    while True:
        page = await events_after(run_id, sequence)
        if not page:
            break
        sequence = page[-1].sequence
        tool_events.extend(
            event for event in page
            if event.step_index == step_index
            and event.kind in {'tool_started', 'tool_completed'}
        )
        if len(page) < EVENT_PAGE_SIZE:
            break
    return project_step_tool_calls(tool_events, step_index)


class TaskRunEventEmitter:
    def __init__(self, run_id: str, *, claim=None):
        self.run_id = run_id
        self._claim = claim
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
        if self._claim is not None:
            from cognitrix.tasks.repository import LeaseLost, RunRepository

            try:
                return await RunRepository().emit_event(
                    self.run_id,
                    claim=self._claim,
                    kind=kind,
                    session_id=session_id,
                    step_index=step_index,
                    agent_name=agent_name,
                    data=data,
                )
            except LeaseLost:
                raise
            except Exception:
                logger.exception(
                    'Could not persist durable task-run event %s for %s',
                    kind,
                    self.run_id,
                )
                return None

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
