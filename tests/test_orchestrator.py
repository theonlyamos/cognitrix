"""Unit tests for durable task planning, assignment, and run lifecycle logic.

LLM and DB surfaces are stubbed; e2e behavior is covered by the manual runs in
the implementation plan.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cognitrix.tasks.orchestrator as orch
from cognitrix.providers.base import LLM


@pytest.mark.asyncio
async def test_changed_team_leader_cannot_escape_authorized_roster(monkeypatch):
    allowed = SimpleNamespace(id="allowed")
    outside = SimpleNamespace(id="outside")
    task = SimpleNamespace(team_id="team-1")
    monkeypatch.setattr(
        "cognitrix.teams.base.Team.get",
        AsyncMock(return_value=SimpleNamespace(leader_id=outside.id)),
    )
    monkeypatch.setattr(
        orch.Agent,
        "get",
        AsyncMock(side_effect=AssertionError("must not load outside roster")),
    )

    assert await orch._resolve_leader(task, [allowed]) is allowed


def _llm():
    return LLM(provider="openai", base_url="http://x", api_key="k", model="m")


def test_only_durable_step_execution_engine_is_exposed():
    legacy_symbols = {
        "_dependency_batches",
        "_summarize_recent_activity",
        "_run_agent_turn",
        "_parse_finalscore",
        "_gate",
        "_step_prompt",
        "_execute_step",
        "_run_step_guarded",
        "_consume_step_outcomes",
        "_save_plan",
        "_cancel_pending",
        "_copy_plan_for_resume",
        "_synthesize",
    }

    assert callable(orch._execute_compiled_steps)
    assert legacy_symbols.isdisjoint(vars(orch))


def test_extract_json_prefers_fenced():
    text = 'noise {"a": 1} noise ```json\n{"b": 2}\n``` tail'
    assert orch._extract_json(text) == {"b": 2}


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


# ---------------------------------------------------------------- run lifecycle

@pytest.mark.asyncio
async def test_set_run_status_persists_claimed_terminal_event_atomically(monkeypatch):
    from cognitrix.tasks.repository import LeaseClaim
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    run = TaskRun(_id='run-1', task_id='task-1', status=TaskRunStatus.RUNNING)
    claim = LeaseClaim(run_id=run.id, owner='worker-1', generation=3)
    mutations = []

    async def get_run(_run_id):
        return run

    class RecordingRepository:
        async def mutate(self, run_id, **kwargs):
            mutations.append((run_id, kwargs))
            return run

    monkeypatch.setattr(TaskRun, 'get', staticmethod(get_run))
    monkeypatch.setattr(orch, 'RunRepository', RecordingRepository)

    applied = await orch._set_run_status(
        run,
        TaskRunStatus.COMPLETED,
        result='done',
        completed=True,
        claim=claim,
    )

    assert applied is True
    assert len(mutations) == 1
    run_id, mutation = mutations[0]
    assert run_id == run.id
    assert mutation['claim'] == claim
    assert mutation['updates']['status'] == TaskRunStatus.COMPLETED.value
    assert mutation['expected_statuses'] == {TaskRunStatus.RUNNING}
    assert mutation['event'] == {
        'kind': 'run_status',
        'data': {'status': TaskRunStatus.COMPLETED.value},
    }


@pytest.mark.asyncio
async def test_direct_run_creates_queued_record_and_claims_before_execution(monkeypatch):
    from cognitrix.tasks.base import TaskStatus
    from cognitrix.tasks.repository import LeaseClaim
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    timeline = []
    queued = TaskRun(
        _id='run-direct',
        task_id='task-direct',
        status=TaskRunStatus.QUEUED,
        acl_version=1,
        acl_agent_ids=[],
    )

    class RecordingRepository:
        async def create_queued(self, **kwargs):
            timeline.append(('create_queued', kwargs))
            return queued

        async def claim(self, run_id, *, owner, **kwargs):
            timeline.append(('claim', run_id, owner))
            queued.status = TaskRunStatus.RUNNING
            queued.lease_owner = owner
            queued.lease_generation = 1
            return LeaseClaim(run_id=run_id, owner=owner, generation=1)

        async def heartbeat(self, *args, **kwargs):
            return queued

        async def record_metric(self, _run_id, *, claim, metric):
            return metric

        async def emit_event(self, _run_id, *, claim, kind, **kwargs):
            return SimpleNamespace(kind=kind, **kwargs)

        async def mutate(self, run_id, **kwargs):
            timeline.append(('mutate', run_id, kwargs))
            for key, value in kwargs['updates'].items():
                setattr(queued, key, value)
            return queued

    repository = RecordingRepository()

    class FakeTask(SimpleNamespace):
        @staticmethod
        async def get(_id):
            return None

        @staticmethod
        async def update_one(*args, **kwargs):
            return 1

    task = FakeTask(
        id='task-direct',
        title='Direct task',
        description='No agents assigned',
        status=TaskStatus.PENDING,
        step_instructions={},
        assigned_agents=[],
        results=[],
        team_id=None,
    )

    async def team():
        return []

    async def no_runs(_query):
        return []

    async def get_run(_run_id):
        return queued

    async def reject_raw_save(self):
        raise AssertionError('direct execution must not save an already-running TaskRun')

    async def no_op(*args, **kwargs):
        return None

    task.team = team
    monkeypatch.setattr(orch, 'RunRepository', lambda: repository)
    monkeypatch.setattr(
        'cognitrix.tasks.repository.RunRepository',
        lambda: repository,
    )
    monkeypatch.setattr(TaskRun, 'find', staticmethod(no_runs))
    monkeypatch.setattr(TaskRun, 'get', staticmethod(get_run))
    monkeypatch.setattr(TaskRun, 'save', reject_raw_save)
    monkeypatch.setattr(orch, 'deliver_completion_notification', no_op)

    with pytest.raises(RuntimeError, match='no agents assigned'):
        await orch.run(task)

    assert [entry[0] for entry in timeline] == [
        'create_queued',
        'claim',
        'mutate',
    ]
    assert timeline[0][1]['task_id'] == task.id
    assert timeline[1][1] == queued.id
    terminal = timeline[2][2]
    assert terminal['claim'].run_id == queued.id
    assert terminal['updates']['status'] == TaskRunStatus.FAILED.value
    assert terminal['event'] == {
        'kind': 'run_status',
        'data': {'status': TaskRunStatus.FAILED.value},
    }
    assert queued.status == TaskRunStatus.FAILED.value


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
async def test_run_uses_one_emitter_and_publishes_authoritative_terminal_status(monkeypatch):
    from cognitrix.tasks.base import TaskStatus
    from cognitrix.tasks.repository import LeaseClaim
    from cognitrix.tasks.results import StepResult
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    emitter_instances = []
    step_emitters = []
    task_statuses = []
    events = []
    timeline = []

    class RecordingEmitter:
        def __init__(self, run_id, *, claim=None):
            self.run_id = run_id
            self.claim = claim
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
        assigned_agents=['agent-1'],
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

    queued = TaskRun(
        _id='run-1',
        task_id=task.id,
        status=TaskRunStatus.QUEUED,
        acl_version=1,
        acl_agent_ids=['agent-1'],
    )

    class RecordingRepository:
        async def claim(self, run_id, *, owner, **kwargs):
            queued.status = TaskRunStatus.RUNNING
            return LeaseClaim(run_id=run_id, owner=owner, generation=1)

        async def heartbeat(self, *args, **kwargs):
            return queued

        async def persist_usage(self, run_id, *, snapshot, **kwargs):
            queued.usage = dict(snapshot)
            return queued

        async def record_metric(self, _run_id, *, claim, metric):
            return metric

        async def compile_steps(self, run_id, plan, **kwargs):
            return [
                SimpleNamespace(
                    step_index=int(entry['index']),
                    to_plan_entry=lambda entry=entry: {
                        **entry,
                        'status': 'pending',
                        'attempts': 0,
                        'result': None,
                        'gate': None,
                    },
                )
                for entry in plan
            ]

    async def get_run(_run_id):
        return queued

    async def no_runs(_query):
        return []

    async def no_op(*args, **kwargs):
        return None

    async def record_task_status(_task, status, **kwargs):
        task_statuses.append(status)

    async def false_cancel(_run):
        return False

    async def resolve_leader(_task, roster):
        return roster[0]

    async def finish_steps(
        task, run, rows, repository, claim, emitter, *args, **kwargs
    ):
        step_emitters.append(emitter)
        await emitter.emit('step_status', data={'status': 'running'})
        await emitter.emit('step_status', data={'status': 'done'})
        return {0: StepResult(text='step result')}

    async def external_cancel_wins(run, status, **kwargs):
        assert status == TaskRunStatus.COMPLETED
        run.status = TaskRunStatus.CANCELLED
        timeline.append(('status_write', run.status.value))
        return False

    monkeypatch.setattr(TaskRun, 'find', staticmethod(no_runs))
    monkeypatch.setattr(TaskRun, 'get', staticmethod(get_run))
    monkeypatch.setattr(orch, 'RunRepository', RecordingRepository)
    monkeypatch.setattr(orch, '_set_task_status', record_task_status)
    monkeypatch.setattr(orch, '_cancel_requested', false_cancel)
    monkeypatch.setattr(orch, '_resolve_leader', resolve_leader)
    monkeypatch.setattr(orch, '_execute_compiled_steps', finish_steps)
    monkeypatch.setattr(orch, '_set_run_status', external_cancel_wins)
    monkeypatch.setattr(orch, 'deliver_completion_notification', no_op)

    run = await orch.run(task, run_record=queued)

    assert run is not None and run.status == TaskRunStatus.CANCELLED
    assert len(emitter_instances) == 1
    assert step_emitters == [emitter_instances[0]]
    assert task_statuses == [TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED]
    assert [(kind, data['data']['status']) for kind, data in events] == [
        ('run_status', 'running'),
        ('step_status', 'running'),
        ('step_status', 'done'),
    ]
    assert timeline[-1] == ('status_write', 'cancelled')


@pytest.mark.asyncio
async def test_run_stops_after_authoritative_force_cancelled_state(monkeypatch):
    from cognitrix.tasks.base import TaskStatus
    from cognitrix.tasks.repository import LeaseClaim
    from cognitrix.tasks.results import StepResult
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    executed_steps = []
    task_statuses = []
    events = []

    class RecordingEmitter:
        def __init__(self, run_id, *, claim=None):
            self.run_id = run_id
            self.claim = claim

        async def emit(self, kind, **kwargs):
            events.append((kind, kwargs))

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
        step_instructions={
            '0': {'step': 'First'},
            '1': {'step': 'Second'},
        },
        assigned_agents=['agent-1'],
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
    stored = TaskRun(
        _id='run-1',
        task_id=task.id,
        status=TaskRunStatus.QUEUED,
        acl_version=1,
        acl_agent_ids=['agent-1'],
    )

    class RecordingRepository:
        async def claim(self, run_id, *, owner, **kwargs):
            stored.status = TaskRunStatus.RUNNING
            return LeaseClaim(run_id=run_id, owner=owner, generation=1)

        async def heartbeat(self, *args, **kwargs):
            return stored

        async def persist_usage(self, run_id, *, snapshot, **kwargs):
            stored.usage = dict(snapshot)
            return stored

        async def record_metric(self, _run_id, *, claim, metric):
            return metric

        async def compile_steps(self, run_id, plan, **kwargs):
            return [
                SimpleNamespace(
                    step_index=int(entry['index']),
                    to_plan_entry=lambda entry=entry: {
                        **entry,
                        'status': 'pending',
                        'attempts': 0,
                        'result': None,
                        'gate': None,
                    },
                )
                for entry in plan
            ]

    async def no_runs(_query):
        return []

    async def get_run(_run_id):
        return stored

    async def fail_run_update(*args, **kwargs):
        raise AssertionError('authoritative terminal run must not be rewritten')

    async def no_op(*args, **kwargs):
        return None

    async def record_task_status(_task, status, **kwargs):
        task_statuses.append(status)

    async def resolve_leader(_task, roster):
        return roster[0]

    async def finish_steps(
        task, run, rows, repository, claim, emitter, *args, **kwargs
    ):
        executed_steps.append(0)
        run.plan[0]['status'] = 'done'
        run.plan[1]['status'] = 'cancelled'
        stored.status = TaskRunStatus.CANCELLED
        stored.error = 'force-cancelled (worker did not respond)'
        stored.completed_at = 'cancelled-at'
        await emitter.emit('step_status', data={'status': 'running'})
        await emitter.emit('step_status', data={'status': 'done'})
        raise orch.DagExecutionCancelled({0: StepResult(text='result-0')})

    async def keep_authoritative_cancel(*args, **kwargs):
        return None

    monkeypatch.setattr(TaskRun, 'find', staticmethod(no_runs))
    monkeypatch.setattr(TaskRun, 'get', staticmethod(get_run))
    monkeypatch.setattr(TaskRun, 'update_one', staticmethod(fail_run_update))
    monkeypatch.setattr(orch, 'RunRepository', RecordingRepository)
    monkeypatch.setattr(orch, '_set_task_status', record_task_status)
    monkeypatch.setattr(orch, '_resolve_leader', resolve_leader)
    monkeypatch.setattr(orch, '_execute_compiled_steps', finish_steps)
    monkeypatch.setattr(orch, '_cancel_unfinished_steps', keep_authoritative_cancel)
    monkeypatch.setattr(orch, 'deliver_completion_notification', no_op)

    run = await orch.run(task, run_record=stored)

    assert run is not None
    assert executed_steps == [0]
    assert [step['status'] for step in run.plan] == ['done', 'cancelled']
    assert run.status == TaskRunStatus.CANCELLED
    assert run.error == 'force-cancelled (worker did not respond)'
    assert run.completed_at == 'cancelled-at'
    assert task_statuses == [TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED]
    assert [(kind, data['data']['status']) for kind, data in events] == [
        ('run_status', 'running'),
        ('step_status', 'running'),
        ('step_status', 'done'),
    ]
