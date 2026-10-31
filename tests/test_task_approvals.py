"""Parked approvals for risky tool calls in durable task runs."""
import asyncio

import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'approvals.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setenv('COGNITRIX_TASK_APPROVAL_POLL', '0.05')
    return db_file


def _risk():
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

    return RiskAssessment(risk_level=RiskLevel.MEDIUM, categories=['file_write'], details='writes a file')


async def _wait_for_pending(run_id):
    from cognitrix.tasks import approvals

    for _ in range(100):
        rows = await approvals.list_for_run(run_id, approvals.PENDING)
        if rows:
            return rows[0]
        await asyncio.sleep(0.02)
    raise AssertionError('no pending approval appeared')


@pytest.mark.asyncio
async def test_task_tool_call_waits_for_the_owner_and_runs_when_approved(db):
    from cognitrix.safety.approval_gate import ApprovalGate, ToolCall
    from cognitrix.tasks import approvals

    events = []

    async def emit(kind, data):
        events.append((kind, data['status']))

    token = approvals.task_turn_ctx.set({
        'run_id': 'run-1', 'task_id': 'task-1', 'step_index': 0,
        'user_id': 'user-1', 'emit': emit,
    })
    try:
        gate = ApprovalGate()
        call = ToolCall(tool_name='Write', params={'file_path': 'a.txt', 'content': 'x'})
        pending = asyncio.create_task(gate.check_approval(call, _risk(), interface='task'))
        row = await _wait_for_pending('run-1')
        assert row.user_id == 'user-1' and row.tool_name == 'Write'
        assert await approvals.decide(row.id, 'run-1', True) is not None
        result = await asyncio.wait_for(pending, 5)
    finally:
        approvals.task_turn_ctx.reset(token)

    assert result.approved
    assert events == [('approval_requested', 'pending'), ('approval_resolved', 'approved')]

    # The same call later in the run (e.g. after a resume) reuses the answer.
    token = approvals.task_turn_ctx.set({'run_id': 'run-1', 'user_id': 'user-1'})
    try:
        again = await asyncio.wait_for(ApprovalGate().check_approval(call, _risk(), interface='task'), 2)
    finally:
        approvals.task_turn_ctx.reset(token)
    assert again.approved


@pytest.mark.asyncio
async def test_denied_and_expired_approvals_block_the_call(db, monkeypatch):
    from cognitrix.safety.approval_gate import ApprovalGate, ToolCall
    from cognitrix.tasks import approvals

    token = approvals.task_turn_ctx.set({'run_id': 'run-2', 'user_id': 'user-1'})
    try:
        call = ToolCall(tool_name='Edit', params={'file_path': 'b.txt'})
        pending = asyncio.create_task(ApprovalGate().check_approval(call, _risk(), interface='task'))
        row = await _wait_for_pending('run-2')
        await approvals.decide(row.id, 'run-2', False)
        denied = await asyncio.wait_for(pending, 5)
        assert not denied.approved

        monkeypatch.setattr(approvals, 'approval_timeout_seconds', lambda: 0.2)
        other = ToolCall(tool_name='Edit', params={'file_path': 'c.txt'})
        expired = await asyncio.wait_for(ApprovalGate().check_approval(other, _risk(), interface='task'), 5)
        assert not expired.approved and 'timed out' in expired.error
    finally:
        approvals.task_turn_ctx.reset(token)

    # An answered or expired approval can't be answered again.
    rows = await approvals.list_for_run('run-2')
    assert {r.status for r in rows} == {approvals.DENIED, approvals.EXPIRED}
    for r in rows:
        assert await approvals.decide(r.id, 'run-2', True) is None


@pytest.mark.asyncio
async def test_runs_without_a_person_are_still_denied():
    from cognitrix.safety.approval_gate import ApprovalGate, ToolCall
    from cognitrix.tasks import approvals

    token = approvals.task_turn_ctx.set({'run_id': 'run-3', 'user_id': None})
    try:
        result = await ApprovalGate().check_approval(
            ToolCall(tool_name='Write', params={}), _risk(), interface='task'
        )
    finally:
        approvals.task_turn_ctx.reset(token)
    assert not result.approved and result.error == 'approval_required'
