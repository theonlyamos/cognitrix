# Durable Task-Run Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream task-step text, tool activity, and lifecycle state from Celery workers to TaskDetail with durable replay, then reconcile the live view with canonical session history and render completed output as Markdown.

**Architecture:** A new append-only `TaskRunEvent` model is written by a run-scoped emitter shared across parallel step coroutines. An authenticated SSE route replays events by per-run sequence and tails the database across the worker/API process boundary. The frontend uses a reusable authenticated event reader, a pure task-event reducer, and canonical transcript reconciliation.

**Tech Stack:** Python 3.11-3.13, asyncio, FastAPI, sse-starlette, Celery, odbms/SQLite, React 18, TypeScript 5.5, Vitest 3, Testing Library, react-markdown.

## Global Constraints

- Preserve Redis and filesystem Celery broker support; Redis must not become required.
- `Session.chat` remains the canonical completed transcript.
- The first text chunk and all tool/lifecycle events must be visible before the step returns.
- Batch later text chunks; never persist one database row per provider token.
- Evaluator score JSON must not be emitted to the live task UI.
- Event persistence failures must not change the task's outcome.
- Keep synthesis non-streaming and render its completed output as Markdown.
- Keep five-second REST polling as a fallback.
- Cap tool parameters/results at the existing 4,000-character preview limit.
- Do not enable raw HTML in Markdown.
- Do not modify the user's unrelated edits in `cognitrix/cli/args.py` or `frontend/src/pages/AgentPage.tsx`.
- Browser verification must target `http://localhost:5173` exactly.

---

## File Structure

- `cognitrix/tasks/events.py` owns the persisted event schema, projection, ordering, and text batching.
- `cognitrix/tasks/orchestrator.py` emits domain events and persists parallel completions as they arrive.
- `cognitrix/api/routes/tasks.py` validates access and exposes run-scoped SSE.
- `frontend/src/lib/sse.ts` parses incremental SSE frames without React concerns.
- `frontend/src/hooks/useEventStream.ts` owns authenticated fetch streaming and reconnect behavior.
- `frontend/src/hooks/useSSE.ts` remains the chat-specific compatibility wrapper.
- `frontend/src/lib/task-run-events.ts` owns task-event types and the pure live-transcript reducer.
- `frontend/src/hooks/useTaskRunEvents.ts` binds the generic stream to the run-event API.
- `frontend/src/components/TranscriptView.tsx` renders completed Markdown and live tool/text states.
- `frontend/src/pages/TaskDetail.tsx` coordinates selection, live overlays, REST fallback, and canonical reconciliation.

---

### Task 1: Persist ordered, batched run events

**Files:**
- Create: `cognitrix/tasks/events.py`
- Create: `tests/test_task_run_events.py`
- Modify: `cognitrix/config.py:290-325`
- Modify: `cognitrix/cli/core.py:36-47`
- Modify: `cognitrix/tasks/__init__.py`

**Interfaces:**
- Consumes: odbms `Model.save()` and the existing SQLite compatibility patch.
- Produces:
  - `TaskRunEvent`
  - `TaskRunEventEmitter(run_id: str)`
  - `emit(kind, *, session_id=None, step_index=None, agent_name=None, data=None)`
  - `text_delta(*, session_id, step_index, agent_name, turn_id, attempt, content)`
  - `flush_text(*, session_id, turn_id)`
  - `events_after(run_id: str, sequence: int) -> list[TaskRunEvent]`
  - `event_payload(event: TaskRunEvent) -> dict[str, Any]`

- [ ] **Step 1: Write failing emitter and SQLite round-trip tests**

Create `tests/test_task_run_events.py`:

```python
import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_emitter_serializes_concurrent_sequences(monkeypatch):
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []

    async def fake_save(self):
        saved.append(self)

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    emitter = TaskRunEventEmitter('run-1')

    await asyncio.gather(*[
        emitter.emit('step_status', step_index=index, data={'status': 'running'})
        for index in range(4)
    ])

    assert sorted(event.sequence for event in saved) == [1, 2, 3, 4]
    assert {event.run_id for event in saved} == {'run-1'}


@pytest.mark.asyncio
async def test_text_is_immediate_then_batched_and_flushed(monkeypatch):
    import cognitrix.tasks.events as events
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []
    now = [10.0]

    async def fake_save(self):
        saved.append(self)

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    monkeypatch.setattr(events.time, 'monotonic', lambda: now[0])
    emitter = TaskRunEventEmitter('run-1')
    common = {
        'session_id': 'session-1',
        'step_index': 0,
        'agent_name': 'Researcher',
        'turn_id': 'session-1:1',
        'attempt': 1,
    }

    await emitter.text_delta(content='first', **common)
    await emitter.text_delta(content=' second', **common)
    assert [event.data['content'] for event in saved] == ['first']

    now[0] += 0.2
    await emitter.text_delta(content=' third', **common)
    await emitter.text_delta(content=' remainder', **common)
    await emitter.flush_text(session_id='session-1', turn_id='session-1:1')

    assert [event.data['content'] for event in saved] == [
        'first',
        ' second third',
        ' remainder',
    ]


@pytest.mark.asyncio
async def test_event_write_failure_is_non_fatal(monkeypatch):
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    async def fail_save(self):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(TaskRunEvent, 'save', fail_save)
    emitter = TaskRunEventEmitter('run-1')

    assert await emitter.emit('run_status', data={'status': 'running'}) is None


@pytest.mark.asyncio
async def test_task_run_event_sqlite_round_trip(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.events import TaskRunEvent, event_payload

    db_file = str(tmp_path / 'events.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    create = getattr(TaskRunEvent, '_create_table_async', None) or TaskRunEvent.create_table
    await create()

    event = TaskRunEvent(
        run_id='run-1',
        session_id='session-1',
        step_index=0,
        sequence=1,
        kind='tool_started',
        agent_name='Researcher',
        data={'tool_call_id': 'call-1', 'params': '{"q":"x"}'},
    )
    await event.save()

    loaded = await TaskRunEvent.find_one({'run_id': 'run-1'})
    assert loaded is not None
    assert loaded.data == {'tool_call_id': 'call-1', 'params': '{"q":"x"}'}
    assert event_payload(loaded)['type'] == 'task_run_event'
    assert event_payload(loaded)['sequence'] == 1


def test_task_run_event_is_registered_in_api_and_cli_startup():
    root = Path(__file__).resolve().parents[1]
    config_source = (root / 'cognitrix' / 'config.py').read_text()
    cli_source = (root / 'cognitrix' / 'cli' / 'core.py').read_text()

    assert 'from cognitrix.tasks.events import TaskRunEvent' in config_source
    assert 'TaskRunEvent' in config_source.split('for model in (', 1)[1]
    assert 'from cognitrix.tasks.events import TaskRunEvent' in cli_source
    assert 'TaskRunEvent' in cli_source.split('for model in (', 1)[1]
```

- [ ] **Step 2: Run the focused backend test and verify RED**

```powershell
poetry run pytest tests/test_task_run_events.py -q
```

Expected: collection FAILS because `cognitrix.tasks.events` does not exist.

- [ ] **Step 3: Implement the event model and emitter**

Create `cognitrix/tasks/events.py` with these concrete fields and batching rules:

```python
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
```

Export `TaskRunEvent` from `cognitrix/tasks/__init__.py`. Add `TaskRunEvent` to the model tuples in `cognitrix/config.py::_ensure_schema` and `cognitrix/cli/core.py::run_configuration`. The Celery worker already calls `initialize_database()`, so no separate worker-only registration is needed.

- [ ] **Step 4: Run focused tests and verify GREEN**

```powershell
poetry run pytest tests/test_task_run_events.py tests/test_odbms_shims.py -q
```

Expected: all focused tests PASS.

- [ ] **Step 5: Commit the event foundation**

```powershell
git add cognitrix/tasks/events.py cognitrix/tasks/__init__.py cognitrix/config.py cognitrix/cli/core.py tests/test_task_run_events.py
git commit -m "feat(tasks): add durable run events"
```

---

### Task 2: Emit live work and persist parallel completions immediately

**Files:**
- Modify: `cognitrix/tasks/orchestrator.py:230-250`
- Modify: `cognitrix/tasks/orchestrator.py:266-387`
- Modify: `cognitrix/tasks/orchestrator.py:580-680`
- Modify: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `TaskRunEventEmitter` from Task 1.
- Produces:
  - `_run_agent_turn(..., emitter=None, publish=False, attempt=1)`
  - `_execute_step(..., emitter=None)`
  - `_run_step_guarded(..., emitter=None)`
  - `_consume_step_outcomes(run, awaitables, dep_results, emitter) -> tuple[bool, str | None]`

- [ ] **Step 1: Write failing live-callback and completion-order tests**

Extend `tests/test_orchestrator.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_turn_publishes_before_turn_returns(monkeypatch):
    import asyncio

    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    persisted = asyncio.Event()
    release = asyncio.Event()
    saved = []

    async def fake_save(self):
        saved.append(self)
        persisted.set()

    class StreamingSession:
        id = 'session-1'
        step_index = 0
        chat = []

        async def __call__(self, prompt, agent, interface, stream, output, wsquery):
            await output({'content': 'live text'})
            await release.wait()

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    emitter = TaskRunEventEmitter('run-1')
    turn = asyncio.create_task(orch._run_agent_turn(
        StreamingSession(),
        SimpleNamespace(name='A', llm=_llm()),
        'prompt',
        'web',
        emitter=emitter,
        publish=True,
        attempt=1,
    ))

    await asyncio.wait_for(persisted.wait(), timeout=1)
    assert turn.done() is False
    assert saved[0].kind == 'text_delta'
    assert saved[0].data['content'] == 'live text'

    release.set()
    assert await turn == 'live text'
    assert saved[-1].kind == 'turn_completed'


@pytest.mark.asyncio
async def test_run_agent_turn_forwards_tool_start_and_result(monkeypatch):
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []

    async def fake_save(self):
        saved.append(self)

    class ToolSession:
        id = 'session-1'
        step_index = 0
        chat = []

        async def __call__(self, prompt, agent, interface, stream, output, wsquery):
            await output({
                'type': 'tool',
                'status': 'started',
                'tool_name': 'Read',
                'tool_call_id': 'call-1',
                'params': '{"path":"README.md"}',
            })
            await output({
                'type': 'tool',
                'status': 'completed',
                'tool_name': 'Read',
                'tool_call_id': 'call-1',
                'result': 'contents',
            })
            await output({'content': 'done'})

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    emitter = TaskRunEventEmitter('run-1')
    await orch._run_agent_turn(
        ToolSession(),
        SimpleNamespace(name='A', llm=_llm()),
        'prompt',
        'web',
        emitter=emitter,
        publish=True,
        attempt=1,
    )

    assert [event.kind for event in saved] == [
        'tool_started',
        'tool_completed',
        'text_delta',
        'turn_completed',
    ]
    assert saved[0].data['tool_call_id'] == 'call-1'
    assert saved[1].data['result'] == 'contents'


@pytest.mark.asyncio
async def test_gate_does_not_publish_evaluator_output(monkeypatch):
    calls = []

    async def fake_turn(session, agent, prompt, interface, **kwargs):
        calls.append(kwargs)
        return '{"finalscore":"8/10"}'

    monkeypatch.setattr(orch, '_run_agent_turn', fake_turn)
    step = orch._new_step(0, 'Research', 'Research', [])

    passed, _ = await orch._gate(
        SimpleNamespace(),
        SimpleNamespace(name='Evaluator source', llm=_llm()),
        step,
        'answer',
        'web',
    )

    assert passed is True
    assert calls == [{}]


@pytest.mark.asyncio
async def test_parallel_outcomes_persist_in_completion_order(monkeypatch):
    import asyncio

    fast = orch._new_step(0, 'fast', 'fast', [])
    slow = orch._new_step(1, 'slow', 'slow', [])
    fast['status'] = slow['status'] = 'running'
    release = asyncio.Event()
    snapshots = []

    async def fast_result():
        return fast, 'done', 'fast result'

    async def slow_result():
        await release.wait()
        return slow, 'done', 'slow result'

    async def save_plan(run):
        snapshots.append([step['status'] for step in run.plan])
        if len(snapshots) == 1:
            release.set()

    class Emitter:
        async def emit(self, *args, **kwargs):
            return None

    monkeypatch.setattr(orch, '_save_plan', save_plan)
    run = SimpleNamespace(plan=[fast, slow])
    dep_results = {}

    cancelled, failure = await orch._consume_step_outcomes(
        run,
        [fast_result(), slow_result()],
        dep_results,
        Emitter(),
    )

    assert snapshots[0] == ['done', 'running']
    assert snapshots[-1] == ['done', 'done']
    assert dep_results == {0: 'fast result', 1: 'slow result'}
    assert cancelled is False and failure is None
```

Update existing monkeypatched `_run_agent_turn` and `_execute_step` fakes in this file to accept `*args, **kwargs` so the new optional keyword-only arguments do not make unrelated tests fail.

- [ ] **Step 2: Run the focused orchestrator tests and verify RED**

```powershell
poetry run pytest tests/test_orchestrator.py -q
```

Expected: FAIL because `_run_agent_turn` has no emitter options and `_consume_step_outcomes` does not exist.

- [ ] **Step 3: Forward executor output to the emitter**

Change the signature and capture logic in `cognitrix/tasks/orchestrator.py`:

```python
async def _run_agent_turn(
    session: Session,
    agent: Agent,
    prompt: str,
    interface: str,
    *,
    emitter: TaskRunEventEmitter | None = None,
    publish: bool = False,
    attempt: int = 1,
) -> str:
    captured = ''
    turn_id = f'{session.id}:{attempt}'

    async def capture(payload=None, *args, **kwargs):
        nonlocal captured
        if isinstance(payload, dict) and payload.get('type') == 'tool':
            if publish and emitter:
                await emitter.flush_text(session_id=session.id, turn_id=turn_id)
                status = str(payload.get('status') or '')
                kind = 'tool_started' if status == 'started' else 'tool_completed'
                data = {
                    'turn_id': turn_id,
                    'tool_call_id': payload.get('tool_call_id'),
                    'tool_name': payload.get('tool_name'),
                }
                if kind == 'tool_started':
                    data['params'] = payload.get('params') or ''
                else:
                    data['result'] = payload.get('result') or ''
                    data['status'] = 'error' if status == 'error' else 'done'
                await emitter.emit(
                    kind,
                    session_id=session.id,
                    step_index=session.step_index,
                    agent_name=agent.name,
                    data=data,
                )
            return

        content = payload.get('content', '') if isinstance(payload, dict) else (
            str(payload) if payload else ''
        )
        if content:
            captured += content
            if publish and emitter:
                await emitter.text_delta(
                    session_id=session.id,
                    step_index=session.step_index,
                    agent_name=agent.name,
                    turn_id=turn_id,
                    attempt=attempt,
                    content=content,
                )

    await session(prompt, agent, interface=interface, stream=True, output=capture, wsquery={})
    if publish and emitter:
        await emitter.flush_text(session_id=session.id, turn_id=turn_id)
        await emitter.emit(
            'turn_completed',
            session_id=session.id,
            step_index=session.step_index,
            agent_name=agent.name,
            data={'turn_id': turn_id, 'attempt': attempt},
        )
    answer = captured.strip()
    if 'Streaming error:' in answer:
        logger.warning(
            'Turn for agent %s hit a provider error: %.120s',
            agent.name,
            answer,
        )
        return ''
    if not answer:
        answer = _summarize_recent_activity(session).strip()
    return answer
```

Pass `publish=True` only from executor and executor-retry calls in `_execute_step`. Keep `_gate` calls unchanged so evaluator output is not published. Thread `emitter` through `_execute_step` and `_run_step_guarded` as an optional keyword-only parameter.

- [ ] **Step 4: Persist outcomes with `asyncio.as_completed`**

Add `_consume_step_outcomes`:

```python
async def _consume_step_outcomes(
    run: TaskRun,
    awaitables,
    dep_results: dict[int, str],
    emitter: TaskRunEventEmitter,
) -> tuple[bool, str | None]:
    cancelled = False
    failure_msg: str | None = None
    tasks = [asyncio.create_task(item) for item in awaitables]
    for completed in asyncio.as_completed(tasks):
        step, outcome, payload = await completed
        if outcome == 'done':
            step['status'] = 'done'
            step['result'] = payload[:RESULT_TRUNCATE]
            dep_results[step['index']] = step['result']
        elif outcome == 'cancelled':
            step['status'] = 'cancelled'
            cancelled = True
        else:
            step['status'] = 'failed'
            failure_msg = failure_msg or payload

        await _save_plan(run)
        await emitter.emit(
            'step_status',
            step_index=step['index'],
            agent_name=step.get('agent_name'),
            data={
                'status': step['status'],
                'title': step['title'],
                'attempts': step.get('attempts', 0),
            },
        )
    return cancelled, failure_msg
```

In `run()`, create one `TaskRunEventEmitter(run_rec.id)` and immediately emit:

```python
emitter = TaskRunEventEmitter(run_rec.id)
await emitter.emit('run_status', data={'status': TaskRunStatus.RUNNING.value})
```

Emit each `running` step after the initial plan save, call `_consume_step_outcomes` for the batch, and emit terminal `run_status` events after the authoritative `_set_run_status` succeeds. Flush pending text before terminal status changes. A force-cancelled run must emit its authoritative status, not an optimistic completed status.

- [ ] **Step 5: Run focused and turn-loop tests and verify GREEN**

```powershell
poetry run pytest tests/test_orchestrator.py tests/test_turn_loop.py tests/test_task_run_events.py -q
```

Expected: all focused tests PASS and existing chat tool-event ordering remains green.

- [ ] **Step 6: Commit orchestrator streaming**

```powershell
git add cognitrix/tasks/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(tasks): emit live step progress"
```

---

### Task 3: Expose authenticated replayable run SSE

**Files:**
- Modify: `cognitrix/api/routes/tasks.py:1-20`
- Modify: `cognitrix/api/routes/tasks.py:340-360`
- Create: `tests/test_task_run_stream.py`

**Interfaces:**
- Consumes: `events_after` and `event_payload` from Task 1; `TaskRun` and task allowlists.
- Produces:
  - `_event_cursor(request: Request, after: int | None) -> int`
  - `_task_run_event_stream(request, run_id, after, poll_interval=0.5)`
  - `GET /tasks/{task_id}/runs/{run_id}/events`

- [ ] **Step 1: Write failing cursor, access, replay, and terminal tests**

Create `tests/test_task_run_stream.py`:

```python
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sse_starlette.sse import EventSourceResponse

from cognitrix.common.security import AuthContext


class RequestStub:
    def __init__(self, last_event_id=None):
        self.headers = {'last-event-id': last_event_id} if last_event_id else {}
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def jwt_ctx():
    return AuthContext(user=SimpleNamespace(id='user-1'), api_key=None)


def test_event_cursor_uses_greatest_non_negative_value():
    from cognitrix.api.routes.tasks import _event_cursor

    assert _event_cursor(RequestStub('7'), 3) == 7
    assert _event_cursor(RequestStub('bad'), 4) == 4
    assert _event_cursor(RequestStub('-2'), -9) == 0


@pytest.mark.asyncio
async def test_stream_route_hides_task_run_mismatch(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    async def task_get(_id):
        return SimpleNamespace(id='task-1', team_id=None, assigned_agents=[])

    async def run_get(_id):
        return SimpleNamespace(id='run-1', task_id='different-task')

    monkeypatch.setattr(routes.Task, 'get', staticmethod(task_get))
    monkeypatch.setattr(routes.TaskRun, 'get', staticmethod(run_get))

    with pytest.raises(HTTPException) as exc:
        await routes.stream_task_run_events(
            RequestStub(), 'task-1', 'run-1', 0, jwt_ctx()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_replays_ordered_events_then_stops_at_terminal(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    rows = [
        SimpleNamespace(
            id='e2', run_id='run-1', session_id='s', step_index=0,
            sequence=2, kind='text_delta', agent_name='A',
            data={'turn_id': 's:1', 'attempt': 1, 'content': 'two'},
            json=lambda: {'created_at': '2026-07-11 00:00:02'},
        ),
        SimpleNamespace(
            id='e1', run_id='run-1', session_id='s', step_index=0,
            sequence=1, kind='text_delta', agent_name='A',
            data={'turn_id': 's:1', 'attempt': 1, 'content': 'one'},
            json=lambda: {'created_at': '2026-07-11 00:00:01'},
        ),
    ]
    calls = 0

    async def event_rows(_run_id, after):
        nonlocal calls
        calls += 1
        return [row for row in sorted(rows, key=lambda item: item.sequence)
                if row.sequence > after] if calls == 1 else []

    async def run_get(_id):
        return SimpleNamespace(status=routes.TaskRunStatus.COMPLETED)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(routes, 'events_after', event_rows)
    monkeypatch.setattr(routes.TaskRun, 'get', staticmethod(run_get))
    monkeypatch.setattr(routes.asyncio, 'sleep', no_sleep)

    stream = routes._task_run_event_stream(
        RequestStub(), 'run-1', 0, poll_interval=0
    )
    first = await anext(stream)
    second = await anext(stream)
    assert [first['id'], second['id']] == ['1', '2']
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_stream_route_returns_event_source(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = SimpleNamespace(id='task-1', team_id=None, assigned_agents=[])
    run = SimpleNamespace(id='run-1', task_id='task-1')

    async def task_get(_id):
        return task

    async def run_get(_id):
        return run

    monkeypatch.setattr(routes.Task, 'get', staticmethod(task_get))
    monkeypatch.setattr(routes.TaskRun, 'get', staticmethod(run_get))

    response = await routes.stream_task_run_events(
        RequestStub(), 'task-1', 'run-1', 0, jwt_ctx()
    )
    assert isinstance(response, EventSourceResponse)
    assert response.ping_interval == 15
```

- [ ] **Step 2: Run the stream tests and verify RED**

```powershell
poetry run pytest tests/test_task_run_stream.py -q
```

Expected: import FAILS because the cursor, generator, and route do not exist.

- [ ] **Step 3: Implement cursor parsing and the tail generator**

In `cognitrix/api/routes/tasks.py`:

```python
import json

from sse_starlette.sse import EventSourceResponse

from cognitrix.tasks.events import event_payload, events_after


def _event_cursor(request: Request, after: int | None) -> int:
    values = [after or 0]
    try:
        values.append(int(request.headers.get('last-event-id', '0')))
    except (TypeError, ValueError):
        pass
    return max(0, *values)


async def _task_run_event_stream(
    request: Request,
    run_id: str,
    after: int,
    *,
    poll_interval: float = 0.5,
):
    last_sequence = after
    terminal_quiet_pass = False
    while not await request.is_disconnected():
        rows = await events_after(run_id, last_sequence)
        if rows:
            terminal_quiet_pass = False
            for row in rows:
                last_sequence = row.sequence
                yield {
                    'event': 'task_run',
                    'id': str(row.sequence),
                    'data': json.dumps(event_payload(row)),
                }

        fresh = await TaskRun.get(run_id)
        terminal = bool(fresh and fresh.status in _TERMINAL_RUN_STATUSES)
        if terminal and not rows:
            if terminal_quiet_pass:
                return
            terminal_quiet_pass = True
        await asyncio.sleep(poll_interval)
```

The second terminal quiet pass prevents a terminal status read from closing the stream in the small window before its final event row is visible.

- [ ] **Step 4: Implement the authorized route**

Add the route before `GET /tasks/{task_id}`:

```python
@tasks_api.get('/{task_id}/runs/{run_id}/events')
async def stream_task_run_events(
    request: Request,
    task_id: str,
    run_id: str,
    after: int | None = None,
    ctx: AuthContext = Depends(get_auth_context),
):
    task = await Task.get(task_id)
    run = await TaskRun.get(run_id)
    if task is None or run is None or run.task_id != task_id:
        raise HTTPException(status_code=404, detail='Task run not found')
    _check_task_allowlists(ctx, task)
    cursor = _event_cursor(request, after)
    return EventSourceResponse(
        _task_run_event_stream(request, run_id, cursor),
        ping=15,
    )
```

- [ ] **Step 5: Run stream and authorization tests and verify GREEN**

```powershell
poetry run pytest tests/test_task_run_stream.py tests/test_api_keys.py -q
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the SSE API**

```powershell
git add cognitrix/api/routes/tasks.py tests/test_task_run_stream.py
git commit -m "feat(api): stream task-run events"
```

---

### Task 4: Extract a replay-aware authenticated event-stream core

**Files:**
- Create: `frontend/src/lib/sse.ts`
- Create: `frontend/src/lib/sse.test.ts`
- Create: `frontend/src/hooks/useEventStream.ts`
- Create: `frontend/src/hooks/useEventStream.test.tsx`
- Modify: `frontend/src/hooks/useSSE.ts`
- Modify: `frontend/src/hooks/useSSE.test.tsx`

**Interfaces:**
- Produces:
  - `SSEFrame { id?: string; event?: string; data: string }`
  - `consumeSSE(buffer: string) -> { frames: SSEFrame[]; rest: string }`
  - `useEventStream<T>({ path, onEvent, enabled, autoReconnect, maxRetries })`
- Preserves: the existing `useSSE(options)` return shape and chat event filter.

- [ ] **Step 1: Write failing incremental parser tests**

Create `frontend/src/lib/sse.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { consumeSSE } from '@/lib/sse';

describe('consumeSSE', () => {
  it('parses ids, event names, comments, and multi-line data', () => {
    const input = [
      ': heartbeat',
      'id: 7',
      'event: task_run',
      'data: {"part":"one"',
      'data: ,"part2":"two"}',
      '',
      '',
    ].join('\n');

    expect(consumeSSE(input)).toEqual({
      frames: [{
        id: '7',
        event: 'task_run',
        data: '{"part":"one"\n,"part2":"two"}',
      }],
      rest: '',
    });
  });

  it('keeps an incomplete frame for the next network chunk', () => {
    const first = consumeSSE('id: 2\ndata: {"value":');
    expect(first.frames).toEqual([]);
    const second = consumeSSE(first.rest + '1}\n\n');
    expect(second.frames).toEqual([
      { id: '2', data: '{"value":1}' },
    ]);
  });
});
```

- [ ] **Step 2: Write failing hook replay and cleanup tests**

Create `frontend/src/hooks/useEventStream.test.tsx` using the reader pattern from `useSSE.test.tsx`:

```tsx
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useEventStream } from '@/hooks/useEventStream';

describe('useEventStream', () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('sends the replay cursor and delivers parsed JSON frames', async () => {
    const encoder = new TextEncoder();
    const chunks = [
      encoder.encode('id: 8\nevent: task_run\ndata: {"value":"ok"}\n\n'),
    ];
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({ done: false, value: chunks[0] })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => reader },
    });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');
    const onEvent = vi.fn();

    const { unmount } = renderHook(() => useEventStream({
      path: '/tasks/task-1/runs/run-1/events',
      onEvent,
      initialLastEventId: '7',
      autoReconnect: false,
    }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledWith({
      id: '8',
      event: 'task_run',
      data: { value: 'ok' },
    }));
    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({
      Authorization: 'Bearer test-token',
      'Last-Event-ID': '7',
    });
    unmount();
    expect(reader.cancel).toHaveBeenCalledOnce();
  });

  it('reconnects from the last delivered event id', async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    const firstReader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('id: 8\ndata: {"value":"first"}\n\n'),
        })
        .mockRejectedValueOnce(new Error('stream dropped')),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const secondReader = {
      read: vi.fn(() => new Promise(() => undefined)),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        body: { getReader: () => firstReader },
      })
      .mockResolvedValueOnce({
        ok: true,
        body: { getReader: () => secondReader },
      });
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('token', 'test-token');
    const onEvent = vi.fn();

    const { unmount } = renderHook(() => useEventStream({
      path: '/events',
      onEvent,
      maxRetries: 1,
    }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onEvent).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].headers).toMatchObject({
      'Last-Event-ID': '8',
    });
    unmount();
  });

  it('ignores malformed JSON without ending the stream', async () => {
    const encoder = new TextEncoder();
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          done: false,
          value: encoder.encode('data: not-json\n\ndata: {"ok":true}\n\n'),
        })
        .mockResolvedValueOnce({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => reader },
    }));
    localStorage.setItem('token', 'test-token');
    const onEvent = vi.fn();

    renderHook(() => useEventStream({
      path: '/events',
      onEvent,
      autoReconnect: false,
    }));

    await waitFor(() => expect(onEvent).toHaveBeenCalledOnce());
    expect(onEvent.mock.calls[0][0].data).toEqual({ ok: true });
  });
});
```

- [ ] **Step 3: Run parser/hook tests and verify RED**

Run from `frontend`:

```powershell
pnpm test -- src/lib/sse.test.ts src/hooks/useEventStream.test.tsx
```

Expected: import FAILS because both modules are absent.

- [ ] **Step 4: Implement the pure SSE parser**

Create `frontend/src/lib/sse.ts`:

```typescript
export interface SSEFrame {
  id?: string;
  event?: string;
  data: string;
}

export function consumeSSE(buffer: string): {
  frames: SSEFrame[];
  rest: string;
} {
  const frames: SSEFrame[] = [];
  let start = 0;
  const boundary = /\r?\n\r?\n/g;
  let match: RegExpExecArray | null;

  while ((match = boundary.exec(buffer)) !== null) {
    const block = buffer.slice(start, match.index);
    start = match.index + match[0].length;
    const data: string[] = [];
    let id: string | undefined;
    let event: string | undefined;

    for (const line of block.split(/\r?\n/)) {
      if (!line || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      const raw = colon === -1 ? '' : line.slice(colon + 1);
      const value = raw.startsWith(' ') ? raw.slice(1) : raw;
      if (field === 'data') data.push(value);
      else if (field === 'id') id = value;
      else if (field === 'event') event = value;
    }
    if (data.length) frames.push({ id, event, data: data.join('\n') });
  }

  return { frames, rest: buffer.slice(start) };
}
```

- [ ] **Step 5: Implement `useEventStream` and delegate chat SSE to it**

Create `frontend/src/hooks/useEventStream.ts` with this public contract:

```typescript
import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE } from '@/lib/api';
import { consumeSSE } from '@/lib/sse';

export interface JSONSSEFrame<T> {
  id?: string;
  event?: string;
  data: T;
}

export interface UseEventStreamOptions<T> {
  path: string | null;
  onEvent?: (frame: JSONSSEFrame<T>) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
  enabled?: boolean;
  autoReconnect?: boolean;
  maxRetries?: number;
  initialLastEventId?: string;
}

export function useEventStream<T>(options: UseEventStreamOptions<T>) {
  const {
    path,
    onEvent,
    onConnect,
    onDisconnect,
    onError,
    enabled = true,
    autoReconnect = true,
    maxRetries = 5,
    initialLastEventId,
  } = options;
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const onEventRef = useRef(onEvent);
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  onEventRef.current = onEvent;
  onConnectRef.current = onConnect;
  onDisconnectRef.current = onDisconnect;
  onErrorRef.current = onError;

  const readerRef = useRef<ReadableStreamReader<Uint8Array> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const retryCountRef = useRef(0);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastEventIdRef = useRef(initialLastEventId);

  const disconnect = useCallback(() => {
    if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
    retryTimeoutRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    if (readerRef.current) {
      void readerRef.current.cancel().catch(() => undefined);
      readerRef.current = null;
    }
    setIsConnected(false);
    onDisconnectRef.current?.();
  }, []);

  const connect = useCallback(() => {
    if (!enabled || !path) return;
    const token = localStorage.getItem('token');
    if (!token) {
      const missing = new Error('No auth token available');
      setError(missing);
      onErrorRef.current?.(missing);
      return;
    }

    abortRef.current?.abort();
    if (readerRef.current) void readerRef.current.cancel().catch(() => undefined);
    if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);

    const controller = new AbortController();
    abortRef.current = controller;
    const headers: Record<string, string> = {
      Authorization: 'Bearer ' + token,
    };
    if (lastEventIdRef.current) {
      headers['Last-Event-ID'] = lastEventIdRef.current;
    }

    const scheduleReconnect = (streamError?: Error) => {
      if (
        autoReconnect
        && !controller.signal.aborted
        && retryCountRef.current < maxRetries
      ) {
        const delay = Math.min(
          1000 * Math.pow(2, retryCountRef.current),
          30000,
        );
        retryTimeoutRef.current = setTimeout(() => {
          retryCountRef.current += 1;
          connect();
        }, delay);
      } else if (streamError) {
        setError(streamError);
        onErrorRef.current?.(streamError);
      }
    };

    fetch(API_BASE + path, { headers, signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error('SSE connection failed: ' + response.status);
        }
        const reader = response.body?.getReader();
        if (!reader) throw new Error('Failed to get reader from response');
        readerRef.current = reader;
        setIsConnected(true);
        setError(null);
        onConnectRef.current?.();

        const decoder = new TextDecoder();
        let buffer = '';
        const readChunk = () => {
          if (controller.signal.aborted) return;
          reader.read()
            .then(({ done, value }) => {
              if (done || controller.signal.aborted) {
                setIsConnected(false);
                onDisconnectRef.current?.();
                scheduleReconnect();
                return;
              }
              buffer += decoder.decode(value, { stream: true });
              const parsed = consumeSSE(buffer);
              buffer = parsed.rest;
              for (const frame of parsed.frames) {
                let data: T;
                try {
                  data = JSON.parse(frame.data) as T;
                } catch (parseError) {
                  console.error('Failed to parse SSE event:', parseError);
                  continue;
                }
                if (frame.id) lastEventIdRef.current = frame.id;
                retryCountRef.current = 0;
                onEventRef.current?.({
                  id: frame.id,
                  event: frame.event,
                  data,
                });
              }
              readChunk();
            })
            .catch((caught) => {
              if (controller.signal.aborted) return;
              const streamError = caught instanceof Error
                ? caught
                : new Error(String(caught));
              setIsConnected(false);
              scheduleReconnect(streamError);
            });
        };
        readChunk();
      })
      .catch((caught) => {
        if (controller.signal.aborted) return;
        const streamError = caught instanceof Error
          ? caught
          : new Error(String(caught));
        setIsConnected(false);
        scheduleReconnect(streamError);
      });
  }, [autoReconnect, enabled, maxRetries, path]);

  const reconnect = useCallback(() => {
    disconnect();
    retryCountRef.current = 0;
    connect();
  }, [connect, disconnect]);

  const clearError = useCallback(() => setError(null), []);

  useEffect(() => {
    lastEventIdRef.current = initialLastEventId;
    retryCountRef.current = 0;
    if (!enabled || !path) return;
    connect();
    return disconnect;
  }, [connect, disconnect, enabled, initialLastEventId, path]);

  return {
    isConnected,
    error,
    connect,
    disconnect,
    reconnect,
    clearError,
    lastEventId: lastEventIdRef.current,
  };
}
```

Replace `useSSE.ts` with the chat-specific wrapper below:

```typescript
import { useRef, useState } from 'react';
import { useEventStream } from '@/hooks/useEventStream';

const CHAT_EVENT_TYPES = new Set([
  'generate',
  'chat_history',
  'chat',
  'multistep_result',
  'status',
  'error',
  'approval_request',
  'tool',
]);

export interface SSEEvent {
  type: string;
  content?: string;
  action?: string;
  agent_name?: string;
  tool_name?: string;
  status?: string;
  [key: string]: unknown;
}

interface UseSSEOptions {
  onMessage?: (event: SSEEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
  autoReconnect?: boolean;
  maxRetries?: number;
  agentId?: string;
  enabled?: boolean;
}

export function useSSE(options: UseSSEOptions = {}) {
  const {
    onMessage,
    onConnect,
    onDisconnect,
    onError,
    autoReconnect = true,
    maxRetries = 5,
    agentId,
    enabled = true,
  } = options;
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
const path = '/agents/sse' + (
  agentId ? '?agent_id=' + encodeURIComponent(agentId) : ''
);
const stream = useEventStream<SSEEvent>({
  path,
  enabled,
  autoReconnect,
  maxRetries,
  onConnect,
  onDisconnect,
  onError,
  onEvent: ({ data: event }) => {
    setLastEvent(event);
    if (CHAT_EVENT_TYPES.has(event.type)) onMessageRef.current?.(event);
  },
});

  return { ...stream, lastEvent };
}
```

- [ ] **Step 6: Run all stream-hook tests and verify GREEN**

```powershell
pnpm test -- src/lib/sse.test.ts src/hooks/useEventStream.test.tsx src/hooks/useSSE.test.tsx
```

Expected: all parser, generic hook, and existing chat SSE tests PASS.

- [ ] **Step 7: Commit the shared transport**

```powershell
git add frontend/src/lib/sse.ts frontend/src/lib/sse.test.ts frontend/src/hooks/useEventStream.ts frontend/src/hooks/useEventStream.test.tsx frontend/src/hooks/useSSE.ts frontend/src/hooks/useSSE.test.tsx
git commit -m "refactor(ui): share SSE transport"
```

---

### Task 5: Render completed transcript Markdown and live tools

**Files:**
- Modify: `frontend/src/lib/transcript.ts:18-25`
- Modify: `frontend/src/components/TranscriptView.tsx`
- Create: `frontend/src/components/TranscriptView.test.tsx`

**Interfaces:**
- Extends `TranscriptEntry`:
  - assistant entries gain `live?: boolean`.
  - tool-call items gain `id?: string`, `status?: 'running' | 'done' | 'error'`, and `result?: string`.
- Preserves: `parseChatEntries(chat)` for canonical history.

- [ ] **Step 1: Write failing Markdown/live-text/tool-state tests**

Create `frontend/src/components/TranscriptView.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { TranscriptView } from '@/components/TranscriptView';

describe('TranscriptView live and Markdown output', () => {
  it('renders completed assistant output as Markdown', async () => {
    render(
      <MemoryRouter future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
        <TranscriptView entries={[{
          kind: 'assistant',
          name: 'Writer',
          content: '# Result\n\n- one\n- two\n\n[task](/tasks/1)\n\n```ts\nconst value = 1;\n```',
        }]} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Result' })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('link', { name: 'task' })).toHaveAttribute(
      'href',
      '/tasks/1',
    );
    expect(screen.getByRole('button', { name: 'copy' })).toBeInTheDocument();
  });

  it('keeps active output plain until completion', async () => {
    render(<TranscriptView entries={[{
      kind: 'assistant',
      content: '# still streaming',
      live: true,
    }]} />);

    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    expect(screen.getByText('# still streaming')).toBeInTheDocument();
  });

  it('shows a tool while running and its result on completion', async () => {
    const { rerender } = render(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [{
        id: 'call-1',
        name: 'read_file',
        args: '{"path":"README.md"}',
        status: 'running',
      }],
    }]} />);
    expect(screen.getByText('running…')).toBeInTheDocument();

    rerender(<TranscriptView entries={[{
      kind: 'tool_calls',
      content: '',
      tools: [{
        id: 'call-1',
        name: 'read_file',
        args: '{"path":"README.md"}',
        status: 'done',
        result: 'contents',
      }],
    }]} />);
    expect(await screen.findByText('contents')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the component test and verify RED**

```powershell
pnpm test -- src/components/TranscriptView.test.tsx
```

Expected: Markdown heading/list assertions and extended transcript types FAIL.

- [ ] **Step 3: Extend transcript types and render states**

In `frontend/src/lib/transcript.ts`:

```typescript
export type TranscriptTool = {
  id?: string;
  name: string;
  args: string;
  result?: string;
  status?: 'running' | 'done' | 'error';
};

export type TranscriptEntry =
  | { kind: 'user'; content: string }
  | { kind: 'assistant'; content: string; name?: string; live?: boolean }
  | { kind: 'tool_calls'; content: string; name?: string; tools: TranscriptTool[] }
  | { kind: 'tool_result'; content: string }
  | { kind: 'timing'; label: string; tokens?: string }
  | { kind: 'summary'; content: string }
  | { kind: 'system'; content: string };
```

Keep canonical parse output non-live and extend the existing tool mapping:

```typescript
tools: (m.tool_calls || []).map((tool) => {
  const result = tool.tool_call_id
    ? resultsById[tool.tool_call_id]
    : undefined;
  return {
    id: tool.tool_call_id || undefined,
    name: tool.name || 'tool',
    args: safeArgs(tool.arguments),
    result,
    status: result === undefined ? undefined : 'done',
  };
}),
```

In `TranscriptView.tsx`, lazy-load `MarkdownMessage` exactly as `ChatMessageRow` does. The assistant branch becomes:

```tsx
const MarkdownMessage = lazy(() => import('@/components/MarkdownMessage'));

{isUser ? (
  <div className="whitespace-pre-wrap break-words leading-relaxed">
    {e.content}
  </div>
) : e.live ? (
  <div className="whitespace-pre-wrap break-words leading-relaxed">
    {e.content}
    <span className="caret" />
  </div>
) : (
  <div className="md break-words">
    <Suspense fallback={
      <div className="whitespace-pre-wrap break-words leading-relaxed">
        {e.content}
      </div>
    }>
      <MarkdownMessage content={e.content} />
    </Suspense>
  </div>
)}
```

Replace the `tool_calls` branch with:

```tsx
case 'tool_calls':
  return (
    <div key={i} className={ROW}>
      <div className={cn(GUTTER, 'text-fg-dim')}>{speaker(e.name)}</div>
      <div className="min-w-0 space-y-1.5">
        {e.content.trim() && (
          <div className="whitespace-pre-wrap break-words leading-relaxed">
            {e.content}
          </div>
        )}
        {e.tools.map((tool, toolIndex) => (
          <details
            key={tool.id || toolIndex}
            className="w-full max-w-2xl font-mono text-[11px]"
          >
            <summary className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 text-accent-ink sm:min-h-0">
              {tool.status === 'running' ? (
                <span className="think-bars"><i /><i /><i /></span>
              ) : tool.status === 'error' ? (
                <span className="text-danger-ink" aria-hidden>✕</span>
              ) : (
                <span className="text-ok" aria-hidden>✓</span>
              )}
              <span>{tool.name.replace(/_/g, ' ')}</span>
              {tool.status === 'running' && (
                <span className="text-fg-dim">running…</span>
              )}
            </summary>
            <div className="mt-1 space-y-2 rounded border border-line bg-panel-2 p-2">
              {tool.args && tool.args !== '{}' && (
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-fg-dim">
                  {tool.args}
                </pre>
              )}
              {tool.status !== 'running' && (
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-fg-dim">
                  {tool.result || '(no output)'}
                </pre>
              )}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
```

Add `role="log" aria-live="polite" aria-relevant="additions text"` to the root transcript element.

- [ ] **Step 4: Run component and parser tests and verify GREEN**

```powershell
pnpm test -- src/components/TranscriptView.test.tsx src/components/responsive-ui.test.tsx
```

Expected: all tests PASS.

- [ ] **Step 5: Commit Markdown transcript rendering**

```powershell
git add frontend/src/lib/transcript.ts frontend/src/components/TranscriptView.tsx frontend/src/components/TranscriptView.test.tsx
git commit -m "feat(tasks): render streamed transcripts"
```

---

### Task 6: Reduce run events into replay-safe live transcripts

**Files:**
- Create: `frontend/src/lib/task-run-events.ts`
- Create: `frontend/src/lib/task-run-events.test.ts`
- Create: `frontend/src/hooks/useTaskRunEvents.ts`
- Create: `frontend/src/hooks/useTaskRunEvents.test.tsx`

**Interfaces:**
- Consumes: `useEventStream` from Task 4 and `TranscriptEntry` from Task 5.
- Produces:
  - `TaskRunEvent`
  - `TaskRunLiveState` and `initialTaskRunLiveState`
  - `taskRunLiveReducer(state, action)`
  - `selectLiveTranscript(state, sessionId) -> TranscriptEntry[]`
  - `useTaskRunEvents({ taskId, runId, onEvent })`

- [ ] **Step 1: Write failing reducer tests**

Create `frontend/src/lib/task-run-events.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import {
  initialTaskRunLiveState,
  selectLiveTranscript,
  taskRunLiveReducer,
  type TaskRunEvent,
} from '@/lib/task-run-events';

const event = (
  sequence: number,
  kind: TaskRunEvent['kind'],
  data: Record<string, unknown>,
): TaskRunEvent => ({
  type: 'task_run_event',
  id: 'event-' + sequence,
  run_id: 'run-1',
  session_id: 'session-1',
  step_index: 0,
  sequence,
  kind,
  agent_name: 'Researcher',
  data,
});

describe('taskRunLiveReducer', () => {
  it('appends text and ignores replayed sequences', () => {
    const first = event(1, 'text_delta', {
      turn_id: 'session-1:1',
      attempt: 1,
      content: 'hello',
    });
    const second = event(2, 'text_delta', {
      turn_id: 'session-1:1',
      attempt: 1,
      content: ' world',
    });
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: first,
    });
    state = taskRunLiveReducer(state, { type: 'event', event: second });
    state = taskRunLiveReducer(state, { type: 'event', event: second });

    expect(selectLiveTranscript(state, 'session-1')).toEqual([{
      kind: 'assistant',
      content: 'hello world',
      name: 'Researcher',
      live: true,
    }]);
    expect(state.lastSequence).toBe(2);
  });

  it('pairs tool completion with its running call', () => {
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'tool_started', {
        turn_id: 'session-1:1',
        tool_call_id: 'call-1',
        tool_name: 'read_file',
        params: '{"path":"README.md"}',
      }),
    });
    state = taskRunLiveReducer(state, {
      type: 'event',
      event: event(2, 'tool_completed', {
        turn_id: 'session-1:1',
        tool_call_id: 'call-1',
        tool_name: 'read_file',
        result: 'contents',
        status: 'done',
      }),
    });

    const entries = selectLiveTranscript(state, 'session-1');
    expect(entries[0]).toMatchObject({
      kind: 'tool_calls',
      tools: [{
        id: 'call-1',
        name: 'read_file',
        status: 'done',
        result: 'contents',
      }],
    });
  });

  it('keeps parallel sessions separate and reconciles one turn', () => {
    const session2 = {
      ...event(2, 'text_delta', {
        turn_id: 'session-2:1',
        attempt: 1,
        content: 'parallel',
      }),
      session_id: 'session-2',
      step_index: 1,
    };
    let state = taskRunLiveReducer(initialTaskRunLiveState, {
      type: 'event',
      event: event(1, 'text_delta', {
        turn_id: 'session-1:1',
        attempt: 1,
        content: 'first',
      }),
    });
    state = taskRunLiveReducer(state, { type: 'event', event: session2 });
    state = taskRunLiveReducer(state, {
      type: 'reconcile',
      sessionId: 'session-1',
      turnId: 'session-1:1',
    });

    expect(selectLiveTranscript(state, 'session-1')).toEqual([]);
    expect(selectLiveTranscript(state, 'session-2')[0]).toMatchObject({
      content: 'parallel',
    });
  });
});
```

- [ ] **Step 2: Write failing run-hook URL and cursor test**

Create `frontend/src/hooks/useTaskRunEvents.test.tsx` and mock `useEventStream`:

```tsx
import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useTaskRunEvents } from '@/hooks/useTaskRunEvents';

const harness = vi.hoisted(() => ({
  useEventStream: vi.fn(() => ({
    isConnected: true,
    error: null,
    reconnect: vi.fn(),
  })),
}));

vi.mock('@/hooks/useEventStream', () => ({
  useEventStream: harness.useEventStream,
}));

describe('useTaskRunEvents', () => {
  it('subscribes to the selected run endpoint', () => {
    const onEvent = vi.fn();
    renderHook(() => useTaskRunEvents({
      taskId: 'task-1',
      runId: 'run-1',
      onEvent,
    }));

    expect(harness.useEventStream).toHaveBeenCalledWith(expect.objectContaining({
      path: '/tasks/task-1/runs/run-1/events',
      enabled: true,
    }));
  });
});
```

- [ ] **Step 3: Run reducer/hook tests and verify RED**

```powershell
pnpm test -- src/lib/task-run-events.test.ts src/hooks/useTaskRunEvents.test.tsx
```

Expected: import FAILS because both task-run modules are absent.

- [ ] **Step 4: Implement typed event state and reducer**

Create `frontend/src/lib/task-run-events.ts` with:

```typescript
import type { TranscriptEntry, TranscriptTool } from '@/lib/transcript';

export type TaskRunEventKind =
  | 'step_status'
  | 'text_delta'
  | 'tool_started'
  | 'tool_completed'
  | 'turn_completed'
  | 'run_status';

export interface TaskRunEvent {
  type: 'task_run_event';
  id: string;
  run_id: string;
  session_id: string | null;
  step_index: number | null;
  sequence: number;
  kind: TaskRunEventKind;
  agent_name: string | null;
  data: Record<string, unknown>;
  created_at?: string | null;
}

type LiveEntry = TranscriptEntry & { turnId: string };

export interface TaskRunLiveState {
  lastSequence: number;
  sessions: Record<string, LiveEntry[]>;
  stepStatuses: Record<number, string>;
  terminalStatus: string | null;
}

export const initialTaskRunLiveState: TaskRunLiveState = {
  lastSequence: 0,
  sessions: {},
  stepStatuses: {},
  terminalStatus: null,
};

export type TaskRunLiveAction =
  | { type: 'event'; event: TaskRunEvent }
  | { type: 'reconcile'; sessionId: string; turnId: string }
  | { type: 'reset' };

const asString = (value: unknown): string =>
  typeof value === 'string' ? value : value == null ? '' : String(value);

export function taskRunLiveReducer(
  state: TaskRunLiveState,
  action: TaskRunLiveAction,
): TaskRunLiveState {
  if (action.type === 'reset') {
    return {
      lastSequence: 0,
      sessions: {},
      stepStatuses: {},
      terminalStatus: null,
    };
  }
  if (action.type === 'reconcile') {
    const entries = state.sessions[action.sessionId] || [];
    return {
      ...state,
      sessions: {
        ...state.sessions,
        [action.sessionId]: entries.filter(
          (entry) => entry.turnId !== action.turnId,
        ),
      },
    };
  }

  const event = action.event;
  if (event.sequence <= state.lastSequence) return state;
  const nextBase: TaskRunLiveState = {
    ...state,
    lastSequence: event.sequence,
  };

  if (event.kind === 'step_status' && event.step_index !== null) {
    return {
      ...nextBase,
      stepStatuses: {
        ...state.stepStatuses,
        [event.step_index]: asString(event.data.status),
      },
    };
  }
  if (event.kind === 'run_status') {
    return {
      ...nextBase,
      terminalStatus: asString(event.data.status) || null,
    };
  }
  if (!event.session_id) return nextBase;

  const sessionId = event.session_id;
  const entries = [...(state.sessions[sessionId] || [])];
  const turnId = asString(event.data.turn_id);
  const withEntries = (updated: LiveEntry[]): TaskRunLiveState => ({
    ...nextBase,
    sessions: { ...state.sessions, [sessionId]: updated },
  });

  if (event.kind === 'text_delta') {
    const content = asString(event.data.content);
    const lastIndex = entries.length - 1;
    const last = entries[lastIndex];
    if (
      last
      && last.kind === 'assistant'
      && last.turnId === turnId
      && last.live
    ) {
      entries[lastIndex] = { ...last, content: last.content + content };
    } else {
      entries.push({
        kind: 'assistant',
        content,
        name: event.agent_name || undefined,
        live: true,
        turnId,
      });
    }
    return withEntries(entries);
  }

  if (event.kind === 'tool_started') {
    const tool: TranscriptTool = {
      id: asString(event.data.tool_call_id) || undefined,
      name: asString(event.data.tool_name) || 'tool',
      args: asString(event.data.params),
      status: 'running',
    };
    const lastIndex = entries.length - 1;
    const last = entries[lastIndex];
    if (last && last.kind === 'tool_calls' && last.turnId === turnId) {
      entries[lastIndex] = { ...last, tools: [...last.tools, tool] };
    } else {
      entries.push({
        kind: 'tool_calls',
        content: '',
        name: event.agent_name || undefined,
        tools: [tool],
        turnId,
      });
    }
    return withEntries(entries);
  }

  if (event.kind === 'tool_completed') {
    const toolId = asString(event.data.tool_call_id);
    const toolName = asString(event.data.tool_name) || 'tool';
    let matched = false;
    const updated = entries.map((entry): LiveEntry => {
      if (entry.kind !== 'tool_calls' || entry.turnId !== turnId) return entry;
      const tools = entry.tools.map((tool) => {
        const same = toolId ? tool.id === toolId : tool.name === toolName;
        if (!same) return tool;
        matched = true;
        return {
          ...tool,
          status: asString(event.data.status) === 'error' ? 'error' : 'done',
          result: asString(event.data.result),
        } satisfies TranscriptTool;
      });
      return { ...entry, tools };
    });
    if (!matched) {
      updated.push({
        kind: 'tool_calls',
        content: '',
        name: event.agent_name || undefined,
        tools: [{
          id: toolId || undefined,
          name: toolName,
          args: '',
          status: asString(event.data.status) === 'error' ? 'error' : 'done',
          result: asString(event.data.result),
        }],
        turnId,
      });
    }
    return withEntries(updated);
  }

  if (event.kind === 'turn_completed') {
    return withEntries(entries.map((entry): LiveEntry => (
      entry.kind === 'assistant' && entry.turnId === turnId
        ? { ...entry, live: false }
        : entry
    )));
  }
  return nextBase;
}

export function selectLiveTranscript(
  state: TaskRunLiveState,
  sessionId: string | null,
): TranscriptEntry[] {
  if (!sessionId) return [];
  return (state.sessions[sessionId] || []).map((entry) => {
    const copy: Partial<LiveEntry> = { ...entry };
    delete copy.turnId;
    return copy as TranscriptEntry;
  });
}
```

- [ ] **Step 5: Implement the run-specific hook**

Create `frontend/src/hooks/useTaskRunEvents.ts`:

```typescript
import { useEventStream } from '@/hooks/useEventStream';
import type { TaskRunEvent } from '@/lib/task-run-events';

interface Options {
  taskId?: string;
  runId: string | null;
  onEvent: (event: TaskRunEvent) => void;
}

export function useTaskRunEvents({ taskId, runId, onEvent }: Options) {
  const path = taskId && runId
    ? '/tasks/' + encodeURIComponent(taskId)
      + '/runs/' + encodeURIComponent(runId) + '/events'
    : null;
  return useEventStream<TaskRunEvent>({
    path,
    enabled: path !== null,
    onEvent: ({ data }) => onEvent(data),
  });
}
```

- [ ] **Step 6: Run reducer/hook tests and verify GREEN**

```powershell
pnpm test -- src/lib/task-run-events.test.ts src/hooks/useTaskRunEvents.test.tsx
```

Expected: all tests PASS.

- [ ] **Step 7: Commit task-run client state**

```powershell
git add frontend/src/lib/task-run-events.ts frontend/src/lib/task-run-events.test.ts frontend/src/hooks/useTaskRunEvents.ts frontend/src/hooks/useTaskRunEvents.test.tsx
git commit -m "feat(ui): reduce live task events"
```

---

### Task 7: Integrate live events and canonical reconciliation in TaskDetail

**Files:**
- Modify: `frontend/src/pages/TaskDetail.tsx`
- Modify: `frontend/src/components/final-accessibility.test.tsx`

**Interfaces:**
- Consumes: `useTaskRunEvents` and `taskRunLiveReducer` from Task 6.
- Produces: no new public component API; TaskDetail combines persisted and live `TranscriptEntry[]`.

- [ ] **Step 1: Extend the existing TaskDetail harness**

In `final-accessibility.test.tsx`, add to the hoisted harness:

```tsx
taskRunOnEvent: null as null | ((event: Record<string, unknown>) => void),
taskRunConnected: true,
taskRunError: null as Error | null,
taskRunReconnect: vi.fn(),
pollingEnabled: false,
```

Add the hook mock:

```tsx
vi.mock('@/hooks/useTaskRunEvents', () => ({
  useTaskRunEvents: ({
    onEvent,
  }: {
    onEvent: (event: Record<string, unknown>) => void;
  }) => {
    harness.taskRunOnEvent = onEvent;
    return {
      isConnected: harness.taskRunConnected,
      error: harness.taskRunError,
      reconnect: harness.taskRunReconnect,
    };
  },
}));
```

Replace the existing polling mock so the fallback remains observable:

```tsx
vi.mock('@/hooks/usePolling', () => ({
  usePolling: (
    _callback: () => void,
    _intervalMs: number,
    enabled: boolean,
  ) => {
    harness.pollingEnabled = enabled;
  },
}));
```

Reset the added fields in `beforeEach`:

```tsx
harness.taskRunOnEvent = null;
harness.taskRunConnected = true;
harness.taskRunError = null;
harness.taskRunReconnect.mockReset();
harness.pollingEnabled = false;
```

- [ ] **Step 2: Write the failing live text/tool/reconciliation test**

Add:

```tsx
it('shows live task text and tools before canonical chat is saved', async () => {
  harness.resources.set('/tasks/task-1', {
    data: { id: 'task-1', title: 'Task', status: 'in_progress' },
  });
  harness.resources.set('/tasks/task-1/runs', {
    data: [{
      id: 'run-live',
      status: 'running',
      plan: [{
        index: 0,
        title: 'Research',
        status: 'running',
        agent_name: 'Researcher',
      }],
    }],
  });
  harness.resources.set('/sessions/tasks/task-1', { data: [] });

  let canonicalChat: unknown[] = [];
  harness.apiGet.mockImplementation((path: string) => {
    if (path === '/sessions/runs/run-live') {
      return Promise.resolve({
        data: [{ id: 'session-1', step_index: 0, step_title: 'Research' }],
      });
    }
    if (path === '/sessions/session-1/chat') {
      return Promise.resolve({ data: canonicalChat });
    }
    return Promise.resolve({ data: [] });
  });
  renderTaskDetail();
  await waitFor(() => expect(harness.taskRunOnEvent).not.toBeNull());

  act(() => harness.taskRunOnEvent?.({
    type: 'task_run_event',
    id: 'event-1',
    run_id: 'run-live',
    session_id: 'session-1',
    step_index: 0,
    sequence: 1,
    kind: 'text_delta',
    agent_name: 'Researcher',
    data: { turn_id: 'session-1:1', attempt: 1, content: 'working now' },
  }));
  expect(await screen.findByText('working now')).toBeInTheDocument();

  act(() => harness.taskRunOnEvent?.({
    type: 'task_run_event',
    id: 'event-2',
    run_id: 'run-live',
    session_id: 'session-1',
    step_index: 0,
    sequence: 2,
    kind: 'tool_started',
    agent_name: 'Researcher',
    data: {
      turn_id: 'session-1:1',
      tool_call_id: 'call-1',
      tool_name: 'read_file',
      params: '{"path":"README.md"}',
    },
  }));
  expect(await screen.findByText('running…')).toBeInTheDocument();

  act(() => harness.taskRunOnEvent?.({
    type: 'task_run_event',
    id: 'event-3',
    run_id: 'run-live',
    session_id: 'session-1',
    step_index: 0,
    sequence: 3,
    kind: 'tool_completed',
    agent_name: 'Researcher',
    data: {
      turn_id: 'session-1:1',
      tool_call_id: 'call-1',
      tool_name: 'read_file',
      result: 'file contents',
      status: 'done',
    },
  }));
  expect(await screen.findByText('file contents')).toBeInTheDocument();

  canonicalChat = [{
    role: 'assistant',
    type: 'text',
    name: 'Researcher',
    content: '# Finished',
  }];
  act(() => harness.taskRunOnEvent?.({
    type: 'task_run_event',
    id: 'event-4',
    run_id: 'run-live',
    session_id: 'session-1',
    step_index: 0,
    sequence: 4,
    kind: 'turn_completed',
    agent_name: 'Researcher',
    data: { turn_id: 'session-1:1', attempt: 1 },
  }));

  expect(await screen.findByRole('heading', { name: 'Finished' })).toBeInTheDocument();
  expect(screen.queryByText('working now')).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Write the failing refresh-failure fallback test**

Add:

```tsx
it('keeps live output when canonical reconciliation fails', async () => {
  harness.resources.set('/tasks/task-1', {
    data: { id: 'task-1', title: 'Task', status: 'in_progress' },
  });
  harness.resources.set('/tasks/task-1/runs', {
    data: [{
      id: 'run-live',
      status: 'running',
      plan: [{
        index: 0,
        title: 'Research',
        status: 'running',
        agent_name: 'Researcher',
      }],
    }],
  });
  harness.resources.set('/sessions/tasks/task-1', { data: [] });

  let failCanonical = false;
  harness.apiGet.mockImplementation((path: string) => {
    if (path === '/sessions/runs/run-live') {
      return Promise.resolve({
        data: [{ id: 'session-1', step_index: 0, step_title: 'Research' }],
      });
    }
    if (path === '/sessions/session-1/chat') {
      return failCanonical
        ? Promise.reject(new Error('offline'))
        : Promise.resolve({ data: [] });
    }
    return Promise.resolve({ data: [] });
  });
  renderTaskDetail();
  await waitFor(() => expect(harness.taskRunOnEvent).not.toBeNull());

  act(() => harness.taskRunOnEvent?.({
    type: 'task_run_event',
    id: 'event-1',
    run_id: 'run-live',
    session_id: 'session-1',
    step_index: 0,
    sequence: 1,
    kind: 'text_delta',
    agent_name: 'Researcher',
    data: {
      turn_id: 'session-1:1',
      attempt: 1,
      content: 'keep this partial output',
    },
  }));
  expect(
    await screen.findByText('keep this partial output'),
  ).toBeInTheDocument();

  failCanonical = true;
  act(() => harness.taskRunOnEvent?.({
    type: 'task_run_event',
    id: 'event-2',
    run_id: 'run-live',
    session_id: 'session-1',
    step_index: 0,
    sequence: 2,
    kind: 'turn_completed',
    agent_name: 'Researcher',
    data: { turn_id: 'session-1:1', attempt: 1 },
  }));

  await waitFor(() => {
    const chatLoads = harness.apiGet.mock.calls.filter(
      ([path]) => path === '/sessions/session-1/chat',
    );
    expect(chatLoads.length).toBeGreaterThanOrEqual(2);
  });
  expect(screen.getByText('keep this partial output')).toBeInTheDocument();
  expect(harness.pollingEnabled).toBe(true);
});
```

- [ ] **Step 4: Run the focused TaskDetail tests and verify RED**

```powershell
pnpm test -- src/components/final-accessibility.test.tsx
```

Expected: FAIL because TaskDetail never consumes `useTaskRunEvents`.

- [ ] **Step 5: Integrate the reducer, session mapping, and overlays**

In `TaskDetail.tsx`:

```tsx
const [liveState, dispatchLive] = useReducer(
  taskRunLiveReducer,
  initialTaskRunLiveState,
);

const loadChat = useCallback(async (sessionId: string): Promise<boolean> => {
  try {
    const res = await api.get<BackendChatEntry[]>(
      '/sessions/' + sessionId + '/chat',
    );
    setChats((previous) => ({ ...previous, [sessionId]: res.data }));
    return true;
  } catch {
    return false;
  }
}, []);
```

Create a stable event callback:

```tsx
const handleRunEvent = useCallback((event: TaskRunEvent) => {
  if (event.run_id !== selectedRunId) return;
  dispatchLive({ type: 'event', event });

  if (event.session_id && event.step_index !== null) {
    setStepSessions((previous) => ({
      ...previous,
      [event.run_id]: {
        ...(previous[event.run_id] || {}),
        [String(event.step_index)]: event.session_id!,
      },
    }));
  }

  if (event.kind === 'step_status') {
    void refetchRuns({ silent: true });
  }

  if (event.kind === 'turn_completed' && event.session_id) {
    const turnId = String(event.data.turn_id || '');
    void loadChat(event.session_id).then((loaded) => {
      if (loaded) {
        dispatchLive({
          type: 'reconcile',
          sessionId: event.session_id!,
          turnId,
        });
      }
    });
  }

  if (event.kind === 'run_status') {
    void refetch({ silent: true });
    void refetchRuns({ silent: true });
    if (selectedRunId) void loadRunSessions(selectedRunId);
  }
}, [loadChat, loadRunSessions, refetch, refetchRuns, selectedRunId]);
```

Subscribe only when the selected run is active:

```tsx
const selectedRunIsLive = !!selectedRun && ACTIVE_RUN.has(selectedRun.status);
const {
  isConnected: runStreamConnected,
  error: runStreamError,
  reconnect: reconnectRunStream,
} = useTaskRunEvents({
  taskId,
  runId: selectedRunIsLive ? selectedRunId : null,
  onEvent: handleRunEvent,
});
```

Reset live reducer state when `selectedRunId` changes:

```tsx
useEffect(() => {
  dispatchLive({ type: 'reset' });
}, [selectedRunId]);
```

Replace the existing `plan` assignment with a non-mutating status overlay:

```tsx
const plan = useMemo(
  () => (selectedRun?.plan || []).map((step) => ({
    ...step,
    status: liveState.stepStatuses[step.index] || step.status,
  })),
  [liveState.stepStatuses, selectedRun?.plan],
);
```

- [ ] **Step 6: Combine canonical and live entries**

Immediately before rendering `TranscriptView`:

```tsx
const canonicalEntries = selectedSessionId && chats[selectedSessionId]
  ? parseChatEntries(chats[selectedSessionId])
  : [];
const liveEntries = selectLiveTranscript(liveState, selectedSessionId);
const transcriptEntries = [...canonicalEntries, ...liveEntries];
```

Make the existing bottom-pinning effect react to persisted messages and every
accepted live sequence:

```tsx
const liveRevision = selectedSessionId && activeRun
  ? (chats[selectedSessionId]?.length ?? 0) + liveState.lastSequence
  : -1;
useEffect(() => {
  if (liveRevision < 0) return;
  const element = scrollRef.current;
  if (element) element.scrollTop = element.scrollHeight;
}, [liveRevision]);
```

Render `transcriptEntries` whenever canonical chat has loaded or a live entry exists:

```tsx
{selectedSessionId ? (
  chats[selectedSessionId] || liveEntries.length > 0 ? (
    <TranscriptView
      entries={transcriptEntries}
      live={
        !!activeRun
        && typeof selected === 'number'
        && plan[selected]?.status === 'running'
      }
    />
  ) : (
    <div
      role="status"
      className="flex items-center gap-2 px-6 py-4 font-mono text-[11px] text-fg-dim"
    >
      <Spinner className="h-3.5 w-3.5" /> loading transcript…
    </div>
  )
) : selectedRun ? (
  <div className="px-6 py-4 font-mono text-[11px] text-fg-dim">
    {plan.length === 0
      ? 'this run failed before a plan was made'
      : 'select a step to view its transcript'}
  </div>
) : (
  !isTaskRunning && (runs || []).length === 0 && (
    <div className="mx-auto flex h-full max-w-2xl flex-col justify-center px-6">
      <p className="font-mono text-[12px] tracking-[0.04em] text-accent-ink">
        &gt;_ no runs yet
      </p>
      <h2 className="mt-3 text-2xl font-bold tracking-tight">
        Run this task
      </h2>
      <p className="mt-2 max-w-md text-fg-dim">
        Hit ▶ Run — the plan's steps land in the panel above, each with its
        own transcript, live while it executes.
      </p>
    </div>
  )
)}
```

Place the connection state immediately above the transcript scroll pane:

```tsx
{selectedRunIsLive && (
  <div className="flex flex-none items-center gap-2 border-b border-line px-6 py-1.5 font-mono text-[10.5px] text-fg-dim">
    <span
      className={cn(
        'h-1.5 w-1.5 rounded-full',
        runStreamConnected ? 'bg-accent' : 'bg-danger',
      )}
    />
    <span>{runStreamConnected ? 'live progress' : 'live progress disconnected'}</span>
    {runStreamError && (
      <button
        type="button"
        onClick={reconnectRunStream}
        className="ml-auto min-h-11 underline underline-offset-2 md:min-h-0"
      >
        reconnect
      </button>
    )}
  </div>
)}
```

- [ ] **Step 7: Run focused TaskDetail tests and verify GREEN**

```powershell
pnpm test -- src/components/final-accessibility.test.tsx src/components/TranscriptView.test.tsx src/lib/task-run-events.test.ts
```

Expected: live text/tool events render, canonical Markdown replaces the completed turn, and failed reconciliation preserves live output.

- [ ] **Step 8: Commit TaskDetail integration**

```powershell
git add frontend/src/pages/TaskDetail.tsx frontend/src/components/final-accessibility.test.tsx
git commit -m "feat(ui): show live task progress"
```

---

### Task 8: Full verification and live browser validation

**Files:**
- Verify only unless a failing regression requires a scoped fix.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified backend, frontend, reconnect, Markdown, and browser behavior.

- [ ] **Step 1: Run the complete backend suite**

```powershell
poetry run pytest -q -p no:cacheprovider
```

Expected: all backend tests PASS.

- [ ] **Step 2: Run Ruff exactly as CI does**

```powershell
poetry run ruff check .
```

Expected: no lint errors and no auto-fix mutation of public imports.

- [ ] **Step 3: Run complete frontend verification**

From `frontend`:

```powershell
pnpm test
pnpm lint
pnpm build
```

Expected: all Vitest tests pass; ESLint reports no errors; TypeScript and Vite build successfully.

- [ ] **Step 4: Verify durable streaming in the in-app browser**

At `http://localhost:5173/tasks`:

1. Open or create a task assigned to a working tool-enabled agent.
2. Start the task and open the running step.
3. Confirm partial agent text appears before the step completes.
4. Trigger a harmless tool such as reading a repository file.
5. Confirm the tool appears as running, then shows its capped result.
6. Confirm another parallel step can finish and update its badge while a slower step remains running.
7. Reload during an active step.
8. Confirm replay restores prior text/tool output once, without duplication.
9. Let the turn complete and confirm the canonical transcript replaces live entries.
10. Confirm headings, lists, links, and fenced code render as Markdown after completion.
11. Confirm the REST polling fallback still updates status when the SSE connection is intentionally interrupted.

- [ ] **Step 5: Inspect browser errors**

Read the in-app browser console after the run. Expected: no uncaught stream parser errors, no repeated reconnect loop, no React key warnings, and no unhandled `AbortError` during unmount/reload.

- [ ] **Step 6: Review the final diff and preserve unrelated edits**

```powershell
git status --short
git diff --check
git diff --stat
```

Expected: task-streaming changes are intentional; `cognitrix/cli/args.py` and `frontend/src/pages/AgentPage.tsx` remain the user's untouched work unless they were independently committed by the user.
