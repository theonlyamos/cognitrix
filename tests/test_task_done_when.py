"""'Done when': each run's final result is checked against what the person
said a finished result must have, and sent back for fixing while it falls short."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import cognitrix.tasks.orchestrator as orchestrator
from cognitrix.tasks import done_when
from cognitrix.tasks.evaluation import StepEvaluation
from cognitrix.tasks.results import StepResult
from cognitrix.tasks.run import TaskRun, TaskRunStatus
from tests.test_task_orchestrator_runtime import (
    _agent,
    _common_stubs,
    _queued,
    _task,
)
from tests.test_task_orchestrator_runtime import orchestration_db as orchestration_db  # noqa: F401

TASK = SimpleNamespace(title='Vendors', description='Find vendors', done_when='A table of 5 vendors with prices')


def _passing(score=9.0):
    return StepEvaluation(passed=True, gate='passed', finalscore=score)


def _failing(*notes):
    return StepEvaluation(passed=False, gate='failed', finalscore=4.0, suggestions=list(notes))


async def test_no_done_when_means_no_check():
    async def never(*_a):
        raise AssertionError('should not be called')

    task = SimpleNamespace(title='t', description='d', done_when='  ')
    result, record = await done_when.check_and_fix(task, StepResult(text='x'), check=never, fix=never)
    assert record is None and result.text == 'x'
    assert done_when.with_done_when('d', task) == 'd'
    assert 'The task is done when:\nA table' in done_when.with_done_when('d', TASK)


async def test_met_first_time_is_not_fixed():
    async def check(_r):
        return _passing(8)

    async def fix(*_a):
        raise AssertionError('no fix needed')

    result, record = await done_when.check_and_fix(TASK, StepResult(text='table'), check=check, fix=fix)
    assert record['status'] == 'met' and record['fixes'] == 0 and record['score'] == 8
    assert result.warnings == []


async def test_a_short_result_is_fixed_until_it_meets_done_when():
    seen = []

    async def check(r):
        return _passing() if 'five' in r.text else _failing('Only 3 vendors')

    async def fix(r, record, round_number):
        seen.append((r.text, record['notes'], round_number))
        return StepResult(text='five vendors')

    result, record = await done_when.check_and_fix(TASK, StepResult(text='three vendors'), check=check, fix=fix)
    assert result.text == 'five vendors'
    assert record['status'] == 'met' and record['fixes'] == 1
    assert seen == [('three vendors', ['Only 3 vendors'], 1)]


async def test_still_short_after_the_last_fix_keeps_a_warning():
    rounds = []

    async def check(_r):
        return _failing('No prices')

    async def fix(r, record, round_number):
        rounds.append(round_number)
        return StepResult(text=f'attempt {round_number}')

    result, record = await done_when.check_and_fix(
        TASK, StepResult(text='first'), check=check, fix=fix, fixes_allowed=2)
    assert rounds == [1, 2]
    assert record['status'] == 'not_met' and record['fixes'] == 2
    assert result.text == 'attempt 2'
    assert result.warnings and 'Not done yet' in result.warnings[-1] and 'No prices' in result.warnings[-1]


async def test_a_failed_fix_keeps_the_last_result_and_reviewer_problems_are_unverified():
    async def check(_r):
        return _failing('x')

    async def broken_fix(*_a):
        raise RuntimeError('model down')

    result, record = await done_when.check_and_fix(TASK, StepResult(text='kept'), check=check, fix=broken_fix)
    assert result.text == 'kept' and record['status'] == 'not_met' and record['fixes'] == 1

    async def unsure(_r):
        return StepEvaluation(passed=True, gate='unverified', error_code='evaluation_unverified')

    result, record = await done_when.check_and_fix(TASK, StepResult(text='kept'), check=unsure, fix=broken_fix)
    assert record['status'] == 'unverified' and result.warnings == []


async def test_cancelling_during_a_fix_stops_the_run():
    import asyncio

    async def check(_r):
        return _failing('x')

    async def cancelled(*_a):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await done_when.check_and_fix(TASK, StepResult(text='a'), check=check, fix=cancelled)


@pytest.mark.asyncio
@pytest.mark.usefixtures('orchestration_db')
async def test_a_run_checks_its_result_fixes_it_and_saves_the_check(monkeypatch):
    agent = _agent()
    task = _task(agent)
    task.done_when = 'The answer names five vendors'
    queued = await _queued(task.id)
    _common_stubs(monkeypatch)
    prompts = []

    class Executor:
        def __init__(self, snapshot, **kwargs):
            self.kwargs = kwargs

        async def execute(self, prompt, *, tool_context=None, attempt=1):
            prompts.append((self.kwargs.get('step_index'), prompt))
            if self.kwargs.get('step_index') is None:
                return StepResult(text='five vendors: a, b, c, d, e')
            return StepResult(text='three vendors: a, b, c')

    async def evaluate(_llm, _task_text, answer, criteria, **_kwargs):
        if criteria == task.done_when:
            return _passing() if 'five' in answer else _failing('Only three vendors are named')
        return _passing()

    async def record_status(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator, 'TaskStepExecutor', Executor)
    monkeypatch.setattr(orchestrator, 'evaluate_step', evaluate)
    monkeypatch.setattr(orchestrator, '_set_task_status', record_status)

    result = await orchestrator.run(task, run_record=queued)

    stored = await TaskRun.get(queued.id)
    assert result is not None and result.status == TaskRunStatus.COMPLETED
    assert stored.result == 'five vendors: a, b, c, d, e'
    assert stored.done_when_check['status'] == 'met'
    assert stored.done_when_check['fixes'] == 1
    step_prompt = prompts[0][1]
    assert 'The task is done when:\nThe answer names five vendors' in step_prompt
    fix_index, fix_text = prompts[1]
    assert fix_index is None
    assert 'Only three vendors are named' in fix_text and 'three vendors: a, b, c' in fix_text
