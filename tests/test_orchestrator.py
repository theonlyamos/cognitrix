"""Unit tests for the task orchestrator (plan/assign/gate/schedule logic).

LLM and DB surfaces are stubbed; e2e behavior is covered by the manual runs in
the implementation plan.
"""

from types import SimpleNamespace

import pytest

import cognitrix.tasks.orchestrator as orch
from cognitrix.providers.base import LLM


def _llm():
    return LLM(provider="openai", base_url="http://x", api_key="k", model="m")


# ---------------------------------------------------------------- parsing

def test_parse_finalscore_variants():
    assert orch._parse_finalscore('{"finalscore": "7/10", "suggestions": ["a"]}') == (7.0, ["a"])
    assert orch._parse_finalscore('```json\n{"finalscore": "9"}\n```') == (9.0, [])
    assert orch._parse_finalscore('{"finalscore": null, "suggestions": ["s"]}') == (None, ["s"])
    assert orch._parse_finalscore('no json here') == (None, [])
    assert orch._parse_finalscore('') == (None, [])


def test_extract_json_prefers_fenced():
    text = 'noise {"a": 1} noise ```json\n{"b": 2}\n``` tail'
    assert orch._extract_json(text) == {"b": 2}


# ---------------------------------------------------------------- scheduling

def _plan_with_deps(deps: dict[int, list[int]]):
    return [orch._new_step(i, f"s{i}", f"s{i}", d) for i, d in sorted(deps.items())]


def test_dependency_batches_diamond():
    plan = _plan_with_deps({0: [], 1: [0], 2: [0], 3: [1, 2]})
    assert orch._dependency_batches(plan) == [[0], [1, 2], [3]]


def test_dependency_batches_cycle_falls_back_sequential():
    plan = _plan_with_deps({0: [1], 1: [0]})
    batches = orch._dependency_batches(plan)
    assert sorted(i for b in batches for i in b) == [0, 1]
    assert all(len(b) == 1 for b in batches)


# ---------------------------------------------------------------- plan build

def test_template_plan_chains_dependencies():
    task = SimpleNamespace(step_instructions={'0': {'step': 'one'}, '1': {'step': 'two'}, '2': {'step': 'three'}})
    plan = orch._template_plan(task)
    assert [s['dependencies'] for s in plan] == [[], [0], [1]]
    assert [s['status'] for s in plan] == ['pending'] * 3


@pytest.mark.asyncio
async def test_planner_plan_invalid_falls_back_to_single_step(monkeypatch):
    class _StubPlanner:
        def __init__(self, llm): pass
        async def create_plan(self, *a, **kw):
            return SimpleNamespace(steps=[])
    monkeypatch.setattr('cognitrix.planning.structured_planner.StructuredPlanner', _StubPlanner)
    task = SimpleNamespace(id='t', title='Title', description='Do the thing', step_instructions={})
    agent = SimpleNamespace(name='A', llm=_llm(), tools=[])
    plan = await orch._planner_plan(task, [agent], agent)
    assert len(plan) == 1 and plan[0]['description'] == 'Do the thing'


@pytest.mark.asyncio
async def test_planner_plan_normalizes_deps_and_names(monkeypatch):
    steps = [
        SimpleNamespace(step_number=1, title='research', description='r', dependencies=[],
                        assigned_agent='auto', expected_output='', verification_criteria=''),
        SimpleNamespace(step_number=2, title='build', description='b', dependencies=[1],
                        assigned_agent='backend engineer', expected_output='', verification_criteria=''),
    ]

    class _StubPlanner:
        def __init__(self, llm): pass
        async def create_plan(self, *a, **kw):
            return SimpleNamespace(steps=steps)
    monkeypatch.setattr('cognitrix.planning.structured_planner.StructuredPlanner', _StubPlanner)
    task = SimpleNamespace(id='t', title='T', description='d', step_instructions={})
    roster = [SimpleNamespace(name='Backend Engineer', llm=_llm(), tools=[]),
              SimpleNamespace(name='QA', llm=_llm(), tools=[])]
    plan = await orch._planner_plan(task, roster, roster[0])
    assert plan[1]['dependencies'] == [0]           # 1-based -> 0-based
    assert plan[1]['agent_name'] == 'Backend Engineer'  # case-insensitive roster match
    assert plan[0]['agent_name'] == ''              # "auto" stays unassigned


# ---------------------------------------------------------------- assignment

@pytest.mark.asyncio
async def test_assign_agents_single_roster_short_circuits():
    plan = [orch._new_step(0, 'a', 'a', [])]
    agent = SimpleNamespace(name='Solo', llm=_llm(), tools=[], system_prompt='x')
    await orch._assign_agents(plan, [agent], agent)
    assert plan[0]['agent_name'] == 'Solo'


@pytest.mark.asyncio
async def test_assign_agents_parses_mapping_and_falls_back(monkeypatch):
    async def fake_generate(agent, prompt):
        return 'Sure: ```json\n{"0": "backend engineer", "1": "Nonexistent"}\n```'
    monkeypatch.setattr(orch, '_collect_generation', fake_generate)
    plan = [orch._new_step(0, 'a', 'a', []), orch._new_step(1, 'b', 'b', [])]
    roster = [SimpleNamespace(name='Backend Engineer', llm=_llm(), tools=[], system_prompt=''),
              SimpleNamespace(name='Lead', llm=_llm(), tools=[], system_prompt='')]
    await orch._assign_agents(plan, roster, roster[1])
    assert plan[0]['agent_name'] == 'Backend Engineer'
    assert plan[1]['agent_name'] == 'Lead'  # unmatched -> leader


# ---------------------------------------------------------------- gate

def _agent():
    return SimpleNamespace(name='A', llm=_llm())


@pytest.mark.asyncio
async def test_gate_passes_at_threshold(monkeypatch):
    async def fake_turn(session, agent, prompt, interface, *args, **kwargs):
        return '{"finalscore": "7/10"}'
    monkeypatch.setattr(orch, '_run_agent_turn', fake_turn)
    step = orch._new_step(0, 's', 's', [])
    passed, suggestions = await orch._gate(None, _agent(), step, 'answer', 'web')
    assert passed and step['gate'] == 'passed'


@pytest.mark.asyncio
async def test_gate_low_score_returns_suggestions(monkeypatch):
    async def fake_turn(session, agent, prompt, interface, *args, **kwargs):
        return '{"finalscore": "4/10", "suggestions": ["do better"]}'
    monkeypatch.setattr(orch, '_run_agent_turn', fake_turn)
    step = orch._new_step(0, 's', 's', [])
    passed, suggestions = await orch._gate(None, _agent(), step, 'answer', 'web')
    assert not passed and suggestions == ['do better'] and step['gate'] is None


@pytest.mark.asyncio
async def test_gate_unparseable_twice_passes_unverified(monkeypatch):
    async def fake_turn(session, agent, prompt, interface, *args, **kwargs):
        return ''
    monkeypatch.setattr(orch, '_run_agent_turn', fake_turn)
    step = orch._new_step(0, 's', 's', [])
    passed, _ = await orch._gate(None, _agent(), step, 'answer', 'web')
    assert passed and step['gate'] == 'unverified'


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
async def test_run_agent_turn_treats_provider_error_chunks_as_dead_turn(monkeypatch):
    class _ErrSession:
        id = 'session-error'
        step_index = 0
        chat: list = []

        async def __call__(self, prompt, agent, interface, stream, output, wsquery):
            await output({'content': 'Streaming error: Upstream error from X: ResourceExhausted'})

    out = await orch._run_agent_turn(_ErrSession(), SimpleNamespace(name='A', llm=_llm()), 'p', 'web')
    assert out == ''


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

    try:
        await asyncio.wait_for(persisted.wait(), timeout=1)
        assert turn.done() is False
        assert saved[0].kind == 'text_delta'
        assert saved[0].data['content'] == 'live text'
    finally:
        release.set()
    assert await turn == 'live text'
    assert saved[-1].kind == 'turn_completed'


@pytest.mark.asyncio
async def test_run_agent_turn_forwards_tool_start_and_result(monkeypatch):
    from cognitrix.sessions.base import _tool_preview
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []
    result_preview = _tool_preview('x' * 5000)

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
                'result': result_preview,
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
    assert saved[1].data['result'] == result_preview
    assert result_preview.startswith('x' * 4000)
    assert 'truncated, 5000 chars total' in result_preview


@pytest.mark.asyncio
async def test_run_agent_turn_flushes_batched_text_when_turn_raises(monkeypatch):
    import cognitrix.tasks.events as events
    from cognitrix.tasks.events import TaskRunEvent, TaskRunEventEmitter

    saved = []

    async def fake_save(self):
        saved.append(self)

    class FailingSession:
        id = 'session-1'
        step_index = 0
        chat = []

        async def __call__(self, prompt, agent, interface, stream, output, wsquery):
            await output({'content': 'first'})
            await output({'content': ' remainder'})
            raise RuntimeError('turn crashed')

    monkeypatch.setattr(TaskRunEvent, 'save', fake_save)
    monkeypatch.setattr(events.time, 'monotonic', lambda: 10.0)
    emitter = TaskRunEventEmitter('run-1')

    with pytest.raises(RuntimeError, match='turn crashed'):
        await orch._run_agent_turn(
            FailingSession(),
            SimpleNamespace(name='A', llm=_llm()),
            'prompt',
            'web',
            emitter=emitter,
            publish=True,
            attempt=1,
        )

    assert [event.kind for event in saved] == ['text_delta', 'text_delta']
    assert [event.data['content'] for event in saved] == ['first', ' remainder']


# ---------------------------------------------------------------- step exec

@pytest.mark.asyncio
async def test_set_run_status_respects_external_finalization(monkeypatch):
    """A force-cancelled (terminal) run must never be overwritten by the
    worker's own COMPLETED/FAILED write."""
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    stored = TaskRun(task_id='t', status=TaskRunStatus.CANCELLED)

    async def fake_get(_id):
        return stored
    monkeypatch.setattr(TaskRun, 'get', staticmethod(fake_get))

    async def fail_update(*a, **kw):
        raise AssertionError('must not write over a terminal status')
    monkeypatch.setattr(TaskRun, 'update_one', staticmethod(fail_update))

    live = TaskRun(task_id='t', status=TaskRunStatus.RUNNING)
    live.id = stored.id
    applied = await orch._set_run_status(live, orch.TaskRunStatus.COMPLETED, result='x', completed=True)
    assert applied is False
    assert live.status == TaskRunStatus.CANCELLED  # mirrored the authoritative state


@pytest.mark.asyncio
async def test_set_run_status_reloads_when_force_cancel_wins_cas_race(monkeypatch):
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    running = TaskRun(task_id='t', status=TaskRunStatus.RUNNING)
    running.id = 'run-1'
    cancelled = TaskRun(
        task_id='t',
        status=TaskRunStatus.CANCELLED,
        error='force-cancelled (worker did not respond)',
    )
    cancelled.id = running.id
    reads = iter([running, cancelled])
    updates = []

    async def fake_get(_id):
        return next(reads)

    async def lose_cas(query, values):
        updates.append((query, values))
        return 0

    monkeypatch.setattr(TaskRun, 'get', staticmethod(fake_get))
    monkeypatch.setattr(TaskRun, 'update_one', staticmethod(lose_cas))

    live = TaskRun(task_id='t', status=TaskRunStatus.RUNNING)
    live.id = running.id
    applied = await orch._set_run_status(
        live,
        TaskRunStatus.COMPLETED,
        result='synthesis',
        completed=True,
    )

    assert updates[0][0] == {'id': live.id, 'status': TaskRunStatus.RUNNING.value}
    assert applied is False
    assert live.status == TaskRunStatus.CANCELLED
    assert live.error == 'force-cancelled (worker did not respond)'
    assert live.result is None and live.completed_at is None


@pytest.mark.asyncio
async def test_set_run_status_cancelling_beats_completion(monkeypatch):
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    cancelling = TaskRun(task_id='t', status=TaskRunStatus.CANCELLING)
    cancelling.id = 'run-1'
    updates = []

    async def fake_get(_id):
        return cancelling

    async def record_update(query, values):
        updates.append((query, values))
        return 1

    monkeypatch.setattr(TaskRun, 'get', staticmethod(fake_get))
    monkeypatch.setattr(TaskRun, 'update_one', staticmethod(record_update))

    live = TaskRun(task_id='t', status=TaskRunStatus.RUNNING)
    live.id = cancelling.id
    applied = await orch._set_run_status(
        live,
        TaskRunStatus.COMPLETED,
        result='synthesis',
        completed=True,
    )

    assert applied is False
    assert updates[0][0] == {
        'id': live.id,
        'status': TaskRunStatus.CANCELLING.value,
    }
    assert updates[0][1]['status'] == TaskRunStatus.CANCELLED.value
    assert live.status == TaskRunStatus.CANCELLED
    assert live.error == 'cancelled by user'
    assert live.result is None and live.completed_at is not None


@pytest.mark.asyncio
async def test_set_run_status_finalizes_cancelling_after_terminal_cas_miss(monkeypatch):
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    running = TaskRun(task_id='t', status=TaskRunStatus.RUNNING)
    running.id = 'run-1'
    cancelling = TaskRun(task_id='t', status=TaskRunStatus.CANCELLING)
    cancelling.id = running.id
    reads = iter([running, cancelling])
    updates = []

    async def fake_get(_id):
        return next(reads)

    async def race_then_cancel(query, values):
        updates.append((query, values))
        return 0 if len(updates) == 1 else 1

    monkeypatch.setattr(TaskRun, 'get', staticmethod(fake_get))
    monkeypatch.setattr(TaskRun, 'update_one', staticmethod(race_then_cancel))

    live = TaskRun(task_id='t', status=TaskRunStatus.RUNNING)
    live.id = running.id
    applied = await orch._set_run_status(
        live,
        TaskRunStatus.COMPLETED,
        result='synthesis',
        completed=True,
    )

    assert applied is False
    assert updates[0][0] == {'id': live.id, 'status': TaskRunStatus.RUNNING.value}
    assert updates[1][0] == {'id': live.id, 'status': TaskRunStatus.CANCELLING.value}
    assert updates[1][1]['status'] == TaskRunStatus.CANCELLED.value
    assert live.status == TaskRunStatus.CANCELLED
    assert live.error == 'cancelled by user'
    assert live.result is None and live.completed_at is not None


@pytest.mark.asyncio
async def test_set_run_status_does_not_rewrite_matching_terminal_state(monkeypatch):
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    stored = TaskRun(
        task_id='t',
        status=TaskRunStatus.CANCELLED,
        error='force-cancelled (worker did not respond)',
    )
    stored.id = 'run-1'

    async def fake_get(_id):
        return stored

    async def fail_update(*args, **kwargs):
        raise AssertionError('an existing terminal row is authoritative')

    monkeypatch.setattr(TaskRun, 'get', staticmethod(fake_get))
    monkeypatch.setattr(TaskRun, 'update_one', staticmethod(fail_update))

    live = TaskRun(task_id='t', status=TaskRunStatus.RUNNING)
    live.id = stored.id
    applied = await orch._set_run_status(
        live,
        TaskRunStatus.CANCELLED,
        error='cancelled by user',
        completed=True,
    )

    assert applied is False
    assert live.status == TaskRunStatus.CANCELLED
    assert live.error == 'force-cancelled (worker did not respond)'


@pytest.mark.asyncio
async def test_parallel_outcomes_persist_in_completion_order(monkeypatch):
    import asyncio

    fast = orch._new_step(0, 'fast', 'fast', [])
    slow = orch._new_step(1, 'slow', 'slow', [])
    fast['status'] = slow['status'] = 'running'
    release = asyncio.Event()
    first_saved = asyncio.Event()
    snapshots = []
    emitted = []

    async def fast_result():
        return fast, 'done', 'fast result'

    async def slow_result():
        await release.wait()
        return slow, 'done', 'slow result'

    async def save_plan(run):
        snapshots.append([step['status'] for step in run.plan])
        first_saved.set()

    class Emitter:
        async def emit(self, kind, **kwargs):
            emitted.append((kind, kwargs))
            return None

    monkeypatch.setattr(orch, '_save_plan', save_plan)
    run = SimpleNamespace(plan=[fast, slow])
    dep_results = {}

    consume = asyncio.create_task(orch._consume_step_outcomes(
        run, [fast_result(), slow_result()], dep_results, Emitter(),
    ))
    try:
        await asyncio.wait_for(first_saved.wait(), timeout=1)
        assert snapshots[0] == ['done', 'running']
        assert consume.done() is False
    finally:
        release.set()
    cancelled, failure = await asyncio.wait_for(consume, timeout=1)

    assert snapshots[-1] == ['done', 'done']
    assert [event[1]['step_index'] for event in emitted] == [0, 1]
    assert [event[1]['data']['status'] for event in emitted] == ['done', 'done']
    assert dep_results == {0: 'fast result', 1: 'slow result'}
    assert cancelled is False and failure is None


@pytest.mark.asyncio
async def test_consume_step_outcomes_cleans_up_siblings_when_persistence_fails(monkeypatch):
    import asyncio

    fast = orch._new_step(0, 'fast', 'fast', [])
    slow = orch._new_step(1, 'slow', 'slow', [])
    fast['status'] = slow['status'] = 'running'
    slow_started = asyncio.Event()
    slow_cancelled = asyncio.Event()
    release_slow = asyncio.Event()

    async def fast_result():
        await slow_started.wait()
        return fast, 'done', 'fast result'

    async def slow_result():
        slow_started.set()
        try:
            await release_slow.wait()
        except asyncio.CancelledError:
            slow_cancelled.set()
            raise
        return slow, 'done', 'slow result'

    async def fail_save(_run):
        raise RuntimeError('plan database unavailable')

    class Emitter:
        async def emit(self, *args, **kwargs):
            return None

    monkeypatch.setattr(orch, '_save_plan', fail_save)

    try:
        with pytest.raises(RuntimeError, match='plan database unavailable'):
            await orch._consume_step_outcomes(
                SimpleNamespace(plan=[fast, slow]),
                [fast_result(), slow_result()],
                {},
                Emitter(),
            )
        assert slow_cancelled.is_set()
        assert slow['status'] == 'cancelled'
    finally:
        release_slow.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_run_uses_one_emitter_and_publishes_authoritative_terminal_status(monkeypatch):
    from cognitrix.tasks.base import TaskStatus
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    emitter_instances = []
    step_emitters = []
    task_statuses = []
    events = []
    timeline = []

    class RecordingEmitter:
        def __init__(self, run_id):
            self.run_id = run_id
            emitter_instances.append(self)

        async def emit(self, kind, **kwargs):
            events.append((kind, kwargs))
            timeline.append(('event', kind, kwargs.get('data', {}).get('status')))

    monkeypatch.setattr(orch, 'TaskRunEventEmitter', RecordingEmitter)

    class FakeTask(SimpleNamespace):
        @staticmethod
        async def get(_id):
            return None

        @staticmethod
        async def update_one(*args, **kwargs):
            return 1

    task = FakeTask(
        id='task-1',
        title='Task',
        description='Do it',
        status=TaskStatus.PENDING,
        step_instructions={'0': {'step': 'Work'}},
        results=[],
        team_id=None,
    )
    agent = SimpleNamespace(
        id='agent-1',
        name='Agent',
        llm=_llm(),
        tools=[],
        system_prompt='',
    )

    async def team():
        return [agent]

    task.team = team

    async def no_runs(_query):
        return []

    async def save_run(self):
        self.id = 'run-1'
        return self

    async def no_op(*args, **kwargs):
        return None

    async def record_task_status(_task, status, **kwargs):
        task_statuses.append(status)

    async def false_cancel(_run):
        return False

    async def resolve_leader(_task, roster):
        return roster[0]

    async def finish_step(task, run, step, agent, dep_results, interface, semaphore, *, emitter=None):
        step_emitters.append(emitter)
        return step, 'done', 'step result'

    async def synthesize(*args, **kwargs):
        return 'synthesis'

    async def external_cancel_wins(run, status, **kwargs):
        assert status == TaskRunStatus.COMPLETED
        run.status = TaskRunStatus.CANCELLED
        timeline.append(('status_write', run.status.value))
        return False

    monkeypatch.setattr(TaskRun, 'find', staticmethod(no_runs))
    monkeypatch.setattr(TaskRun, 'save', save_run)
    monkeypatch.setattr(orch, '_set_task_status', record_task_status)
    monkeypatch.setattr(orch, '_save_plan', no_op)
    monkeypatch.setattr(orch, '_cancel_requested', false_cancel)
    monkeypatch.setattr(orch, '_resolve_leader', resolve_leader)
    monkeypatch.setattr(orch, '_run_step_guarded', finish_step)
    monkeypatch.setattr(orch, '_synthesize', synthesize)
    monkeypatch.setattr(orch, '_set_run_status', external_cancel_wins)
    monkeypatch.setattr(orch, 'notify_completion', no_op)

    run = await orch.run(task)

    assert run is not None and run.status == TaskRunStatus.CANCELLED
    assert len(emitter_instances) == 1
    assert step_emitters == [emitter_instances[0]]
    assert task_statuses == [TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED]
    assert [(kind, data['data']['status']) for kind, data in events] == [
        ('run_status', 'running'),
        ('step_status', 'running'),
        ('step_status', 'done'),
        ('run_status', 'cancelled'),
    ]
    assert timeline[-2:] == [
        ('status_write', 'cancelled'),
        ('event', 'run_status', 'cancelled'),
    ]


# ---------------------------------------------------------------- P3: resume/cancel/timeout

def test_copy_plan_for_resume_keeps_done_resets_rest():
    plan = [
        {**orch._new_step(0, 'a', 'a', []), 'status': 'done', 'result': 'kept', 'attempts': 2, 'gate': 'passed'},
        {**orch._new_step(1, 'b', 'b', [0]), 'status': 'failed', 'result': 'junk', 'attempts': 2, 'gate': None},
        {**orch._new_step(2, 'c', 'c', [1]), 'status': 'cancelled'},
    ]
    new = orch._copy_plan_for_resume(plan)
    assert new[0]['status'] == 'done' and new[0]['result'] == 'kept'
    assert new[1]['status'] == 'pending' and new[1]['result'] is None and new[1]['attempts'] == 0
    assert new[2]['status'] == 'pending'
    assert plan[1]['status'] == 'failed'  # original untouched (deep copy)


@pytest.mark.asyncio
async def test_run_step_guarded_cancel_before_launch(monkeypatch):
    async def cancelling(run):
        return True
    monkeypatch.setattr(orch, '_cancel_requested', cancelling)
    step = orch._new_step(0, 's', 's', [])
    out = await orch._run_step_guarded(
        SimpleNamespace(id='t'), SimpleNamespace(id='r'), step,
        SimpleNamespace(id='a', name='A', llm=_llm()), {}, 'web', __import__('asyncio').Semaphore(1))
    assert out == (step, 'cancelled', '')


@pytest.mark.asyncio
async def test_run_step_guarded_timeout_is_terminal(monkeypatch):
    import asyncio as _asyncio

    async def not_cancelling(run):
        return False
    monkeypatch.setattr(orch, '_cancel_requested', not_cancelling)

    async def slow_step(*a, **kw):
        await _asyncio.sleep(5)
    monkeypatch.setattr(orch, '_execute_step', slow_step)
    monkeypatch.setattr(orch, 'STEP_TIMEOUT', 0.05)

    step = orch._new_step(0, 's', 's', [])
    _, outcome, payload = await orch._run_step_guarded(
        SimpleNamespace(id='t'), SimpleNamespace(id='r'), step,
        SimpleNamespace(id='a', name='A', llm=_llm()), {}, 'web', _asyncio.Semaphore(1))
    assert outcome == 'failed' and 'timed out' in payload


class _StubSession:
    def __init__(self, **kw):
        self.chat = []
        self.id = 'stub'
        self.__dict__.update(kw)

    async def save(self):
        pass


@pytest.mark.asyncio
async def test_execute_step_empty_turns_fail(monkeypatch):
    monkeypatch.setattr(orch, 'Session', _StubSession)
    monkeypatch.setattr(orch, 'EMPTY_TURN_BACKOFF', 0)

    async def empty_turn(session, agent, prompt, interface, *args, **kwargs):
        return ''
    monkeypatch.setattr(orch, '_run_agent_turn', empty_turn)

    task = SimpleNamespace(id='t', title='T', description='d')
    run = SimpleNamespace(id='r')
    step = orch._new_step(0, 's', 's', [])
    with pytest.raises(orch.StepFailure):
        await orch._execute_step(task, run, step, SimpleNamespace(id='a', name='A', llm=_llm()), {}, 'web')
    assert step['attempts'] == 2


async def _never_cancelling(run):
    return False


@pytest.mark.asyncio
async def test_execute_step_gate_retry_succeeds(monkeypatch):
    monkeypatch.setattr(orch, 'Session', _StubSession)
    monkeypatch.setattr(orch, '_cancel_requested', _never_cancelling)
    replies = iter([
        'first draft',                                        # agent turn
        '{"finalscore": "4/10", "suggestions": ["expand"]}',  # gate 1: low
        'better draft',                                       # gate-retry agent turn
        '{"finalscore": "9/10"}',                             # gate 2: pass
    ])
    calls = []

    async def scripted_turn(session, agent, prompt, interface, **kwargs):
        calls.append(kwargs)
        return next(replies)
    monkeypatch.setattr(orch, '_run_agent_turn', scripted_turn)

    task = SimpleNamespace(id='t', title='T', description='d')
    step = orch._new_step(0, 's', 's', [])
    emitter = object()
    out = await orch._execute_step(task, SimpleNamespace(id='r'), step,
                                   SimpleNamespace(id='a', name='A', llm=_llm()), {}, 'web',
                                   emitter=emitter)
    assert out == 'better draft'
    assert step['gate'] == 'passed'
    assert step['attempts'] == 2
    assert calls == [
        {'emitter': emitter, 'publish': True, 'attempt': 1},
        {},
        {'emitter': emitter, 'publish': True, 'attempt': 2},
        {},
    ]


@pytest.mark.asyncio
async def test_execute_step_gate_fail_after_retry(monkeypatch):
    monkeypatch.setattr(orch, 'Session', _StubSession)
    monkeypatch.setattr(orch, '_cancel_requested', _never_cancelling)
    replies = iter([
        'draft',
        '{"finalscore": "3/10", "suggestions": ["x"]}',
        'still bad',
        '{"finalscore": "2/10"}',
    ])

    async def scripted_turn(session, agent, prompt, interface, *args, **kwargs):
        return next(replies)
    monkeypatch.setattr(orch, '_run_agent_turn', scripted_turn)

    step = orch._new_step(0, 's', 's', [])
    with pytest.raises(orch.StepFailure):
        await orch._execute_step(SimpleNamespace(id='t', title='T', description='d'),
                                 SimpleNamespace(id='r'), step,
                                 SimpleNamespace(id='a', name='A', llm=_llm()), {}, 'web')
