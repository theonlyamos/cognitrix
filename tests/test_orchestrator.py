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
    async def fake_turn(session, agent, prompt, interface):
        return '{"finalscore": "7/10"}'
    monkeypatch.setattr(orch, '_run_agent_turn', fake_turn)
    step = orch._new_step(0, 's', 's', [])
    passed, suggestions = await orch._gate(None, _agent(), step, 'answer', 'web')
    assert passed and step['gate'] == 'passed'


@pytest.mark.asyncio
async def test_gate_low_score_returns_suggestions(monkeypatch):
    async def fake_turn(session, agent, prompt, interface):
        return '{"finalscore": "4/10", "suggestions": ["do better"]}'
    monkeypatch.setattr(orch, '_run_agent_turn', fake_turn)
    step = orch._new_step(0, 's', 's', [])
    passed, suggestions = await orch._gate(None, _agent(), step, 'answer', 'web')
    assert not passed and suggestions == ['do better'] and step['gate'] is None


@pytest.mark.asyncio
async def test_gate_unparseable_twice_passes_unverified(monkeypatch):
    async def fake_turn(session, agent, prompt, interface):
        return ''
    monkeypatch.setattr(orch, '_run_agent_turn', fake_turn)
    step = orch._new_step(0, 's', 's', [])
    passed, _ = await orch._gate(None, _agent(), step, 'answer', 'web')
    assert passed and step['gate'] == 'unverified'


@pytest.mark.asyncio
async def test_run_agent_turn_treats_provider_error_chunks_as_dead_turn(monkeypatch):
    class _ErrSession:
        chat: list = []

        async def __call__(self, prompt, agent, interface, stream, output, wsquery):
            await output({'content': 'Streaming error: Upstream error from X: ResourceExhausted'})

    out = await orch._run_agent_turn(_ErrSession(), SimpleNamespace(name='A', llm=_llm()), 'p', 'web')
    assert out == ''


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

    async def empty_turn(session, agent, prompt, interface):
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

    async def scripted_turn(session, agent, prompt, interface):
        return next(replies)
    monkeypatch.setattr(orch, '_run_agent_turn', scripted_turn)

    task = SimpleNamespace(id='t', title='T', description='d')
    step = orch._new_step(0, 's', 's', [])
    out = await orch._execute_step(task, SimpleNamespace(id='r'), step,
                                   SimpleNamespace(id='a', name='A', llm=_llm()), {}, 'web')
    assert out == 'better draft'
    assert step['gate'] == 'passed'
    assert step['attempts'] == 2


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

    async def scripted_turn(session, agent, prompt, interface):
        return next(replies)
    monkeypatch.setattr(orch, '_run_agent_turn', scripted_turn)

    step = orch._new_step(0, 's', 's', [])
    with pytest.raises(orch.StepFailure):
        await orch._execute_step(SimpleNamespace(id='t', title='T', description='d'),
                                 SimpleNamespace(id='r'), step,
                                 SimpleNamespace(id='a', name='A', llm=_llm()), {}, 'web')
