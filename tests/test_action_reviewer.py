"""The second-model reviewer that screens risky tool calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cognitrix.safety import action_reviewer as ar
from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, ToolCall
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel


def _risk(level=RiskLevel.MEDIUM, categories=None, details='Overwrites a file'):
    return RiskAssessment(level, categories or ['file_overwrite'], details)


class _FakeLLM:
    def __init__(self, reply=None, error=None, raises=None):
        self.provider = 'openai'
        self.model = 'reviewer-model'
        self.temperature = 0.7
        self.reply = reply
        self.error = error
        self.raises = raises
        self.prompts = []

    async def __call__(self, prompt, stream=False, **kw):
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises
        return SimpleNamespace(llm_response=self.reply, error=self.error)


@pytest.fixture
def gate(tmp_path):
    return ApprovalGate(cache_dir=str(tmp_path))


@pytest.fixture
def reviewer(monkeypatch):
    """Turn the reviewer on with a fake model; returns a setter for its reply."""
    holder = {}

    def use(reply=None, mode='always', **kw):
        monkeypatch.setenv('COGNITRIX_REVIEWER', mode)
        llm = _FakeLLM(reply, **kw)
        holder['llm'] = llm
        monkeypatch.setattr(ar, '_load_reviewer_llm', lambda: llm)
        return llm

    monkeypatch.delenv('COGNITRIX_AUTO_APPROVE', raising=False)
    monkeypatch.delenv('COGNITRIX_REVIEWER_MAX_ALLOW', raising=False)
    return use


# ── parsing and settings ────────────────────────────────────────────────

@pytest.mark.parametrize('text,decision', [
    ('{"decision": "allow", "reason": "fits"}', 'allow'),
    ('```json\n{"decision": "BLOCK", "reason": "x"}\n```', 'block'),
    ('Sure! {"decision": "ask"}', 'ask'),
    ('{"decision": "maybe"}', 'ask'),
    ('not json at all', 'ask'),
    ('', 'ask'),
])
def test_parse_verdict(text, decision):
    assert ar.parse_verdict(text).decision == decision


def test_mode_defaults_off(monkeypatch):
    monkeypatch.delenv('COGNITRIX_REVIEWER', raising=False)
    assert ar.reviewer_mode() == 'off'
    monkeypatch.setenv('COGNITRIX_REVIEWER', 'bogus')
    assert ar.reviewer_mode() == 'off'
    monkeypatch.setenv('COGNITRIX_REVIEWER', 'true')
    assert ar.reviewer_mode() == 'unattended'


def test_should_review(monkeypatch):
    monkeypatch.setenv('COGNITRIX_REVIEWER', 'unattended')
    assert ar.should_review(_risk(), 'task')
    assert ar.should_review(_risk(), 'auto')
    assert not ar.should_review(_risk(), 'web')
    assert not ar.should_review(_risk(RiskLevel.LOW), 'task')
    # The person chose "Ask first" for this tool.
    assert not ar.should_review(_risk(categories=['file_overwrite', 'permission_ask']), 'task')
    # Full shell always needs a person.
    assert not ar.should_review(_risk(RiskLevel.HIGH, ['shell'], 'Runs in the full shell (pipes)'), 'task')
    monkeypatch.setenv('COGNITRIX_REVIEWER', 'always')
    assert ar.should_review(_risk(), 'web')


def test_prompt_marks_goal_and_action_as_data():
    prompt = ar.build_review_prompt('Write', {'path': 'a.txt'}, _risk(), 'Fix the README')
    assert prompt[0]['role'] == 'system'
    assert 'never instructions' in prompt[0]['content']
    assert '<goal>\nFix the README\n</goal>' in prompt[1]['content']
    assert '"path": "a.txt"' in prompt[1]['content']


# ── review_action ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_uses_turn_goal(reviewer):
    llm = reviewer('{"decision": "allow", "reason": "ok"}')
    token = ar.turn_goal.set('Tidy up the notes folder')
    try:
        verdict = await ar.review_action('Write', {'path': 'x'}, _risk())
    finally:
        ar.turn_goal.reset(token)
    assert verdict.decision == 'allow'
    assert verdict.model == 'openai/reviewer-model'
    assert 'Tidy up the notes folder' in llm.prompts[0][1]['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('kw', [
    {'raises': RuntimeError('boom')},
    {'error': 'rate limited'},
    {'reply': 'I think it is fine'},
])
async def test_review_failures_become_ask(reviewer, kw):
    reviewer(**kw)
    verdict = await ar.review_action('Write', {}, _risk())
    assert verdict.decision == 'ask'


@pytest.mark.asyncio
async def test_review_missing_model_is_ask(monkeypatch):
    monkeypatch.setattr(ar, '_load_reviewer_llm', lambda: None)
    assert (await ar.review_action('Write', {}, _risk())).decision == 'ask'


@pytest.mark.asyncio
async def test_high_risk_allow_needs_setting(reviewer, monkeypatch):
    reviewer('{"decision": "allow", "reason": "fine"}')
    high = _risk(RiskLevel.HIGH, ['file_deletion'], 'Deletes a file')
    assert (await ar.review_action('Delete File', {}, high)).decision == 'ask'
    monkeypatch.setenv('COGNITRIX_REVIEWER_MAX_ALLOW', 'high')
    assert (await ar.review_action('Delete File', {}, high)).decision == 'allow'


@pytest.mark.asyncio
async def test_high_risk_block_still_blocks(reviewer):
    reviewer('{"decision": "block", "reason": "deletes everything"}')
    high = _risk(RiskLevel.HIGH, ['file_deletion'], 'Deletes a file')
    assert (await ar.review_action('Delete File', {}, high)).decision == 'block'


# ── wired into the approval gate ────────────────────────────────────────

CALL = ToolCall('Write', {'path': 'notes.md', 'content': 'hi'})


@pytest.mark.asyncio
async def test_gate_allow_skips_person(gate, reviewer):
    reviewer('{"decision": "allow", "reason": "matches the goal"}')
    with patch.object(gate, '_cli_approval', new_callable=AsyncMock) as cli:
        result = await gate.check_approval(CALL, _risk(), interface='cli')
    cli.assert_not_called()
    assert result.approved and result.auto and result.reviewed
    assert result.review['decision'] == 'allow'


@pytest.mark.asyncio
async def test_gate_block_refuses_with_reason(gate, reviewer):
    reviewer('{"decision": "block", "reason": "unrelated to the goal"}')
    with patch.object(gate, '_cli_approval', new_callable=AsyncMock) as cli:
        result = await gate.check_approval(CALL, _risk(), interface='cli')
    cli.assert_not_called()
    assert not result.approved and result.reviewed
    assert 'unrelated to the goal' in result.error


@pytest.mark.asyncio
async def test_gate_ask_falls_through_to_person(gate, reviewer):
    reviewer('{"decision": "ask", "reason": "unsure"}')
    with patch.object(gate, '_cli_approval', new_callable=AsyncMock) as cli:
        cli.return_value = ApprovalResult(approved=True)
        result = await gate.check_approval(CALL, _risk(), interface='cli')
    cli.assert_called_once()
    assert result.approved and not result.reviewed


@pytest.mark.asyncio
async def test_gate_off_never_calls_model(gate, reviewer, monkeypatch):
    llm = reviewer('{"decision": "allow"}', mode='off')
    result = await gate.check_approval(CALL, _risk(), interface='auto')
    assert not result.approved
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_gate_unattended_mode_leaves_chat_to_person(gate, reviewer):
    llm = reviewer('{"decision": "allow"}', mode='unattended')
    with patch.object(gate, '_cli_approval', new_callable=AsyncMock) as cli:
        cli.return_value = ApprovalResult(approved=False)
        await gate.check_approval(CALL, _risk(), interface='cli')
    cli.assert_called_once()
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_gate_auto_interface_allowed_by_reviewer(gate, reviewer):
    reviewer('{"decision": "allow", "reason": "ok"}', mode='unattended')
    result = await gate.check_approval(CALL, _risk(), interface='auto')
    assert result.approved and result.reviewed


@pytest.mark.asyncio
async def test_gate_task_allow_skips_parking_and_records_event(gate, reviewer):
    from cognitrix.tasks import approvals

    reviewer('{"decision": "allow", "reason": "part of the step"}', mode='unattended')
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    token = approvals.task_turn_ctx.set({'run_id': 'r1', 'user_id': 'u1', 'emit': emit})
    try:
        with patch.object(approvals, 'request_task_approval', new_callable=AsyncMock) as park:
            result = await gate.check_approval(CALL, _risk(), interface='task')
    finally:
        approvals.task_turn_ctx.reset(token)
    park.assert_not_called()
    assert result.approved
    assert events and events[0][0] == 'action_reviewed'
    assert events[0][1]['decision'] == 'allow'
    assert events[0][1]['tool_name'] == 'Write'


@pytest.mark.asyncio
async def test_gate_task_ask_parks_for_owner(gate, reviewer):
    from cognitrix.tasks import approvals

    reviewer('{"decision": "ask", "reason": "unsure"}', mode='unattended')
    token = approvals.task_turn_ctx.set({'run_id': 'r1', 'user_id': 'u1'})
    try:
        with patch.object(approvals, 'request_task_approval', new_callable=AsyncMock) as park:
            park.return_value = (True, None)
            result = await gate.check_approval(CALL, _risk(), interface='task')
    finally:
        approvals.task_turn_ctx.reset(token)
    park.assert_called_once()
    assert result.approved and not result.reviewed


@pytest.mark.asyncio
async def test_gate_skips_reviewer_for_web_bypass(gate, reviewer):
    from cognitrix.safety.approval_gate import web_turn_ctx

    llm = reviewer('{"decision": "block"}')
    token = web_turn_ctx.set({'bypass': True})
    try:
        result = await gate.check_approval(CALL, _risk(), interface='web')
    finally:
        web_turn_ctx.reset(token)
    assert result.approved
    assert llm.prompts == []


# ── agent loop: blocked call explains why ───────────────────────────────

@pytest.mark.asyncio
async def test_session_sets_turn_goal(monkeypatch):
    from cognitrix.sessions.base import Session

    seen = {}

    async def fake_run(self, message, agent, *a, **kw):
        seen['goal'] = ar.turn_goal.get()

    monkeypatch.setattr(Session, '_run_turn', fake_run)
    session = Session.model_construct()
    await Session.__call__(session, 'Clean up old logs', agent=None)
    assert seen['goal'] == 'Clean up old logs'
    assert ar.turn_goal.get() is None


@pytest.mark.asyncio
async def test_agent_loop_reports_reviewer_block(tmp_path, monkeypatch, reviewer):
    from cognitrix.agents.base import AgentManager
    from cognitrix.config import settings
    from cognitrix.models.agent import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.tools.base import ToolManager

    reviewer('{"decision": "block", "reason": "wipes a file the goal never mentions"}', mode='unattended')
    monkeypatch.setattr(settings, 'tools_root', str(tmp_path))
    target = tmp_path / 'keep.txt'
    target.write_text('important')
    write = ToolManager.get_by_name('Write')
    agent = Agent(name='w', llm=LLM(provider='openai', api_key='k-1234567890', model='m'),
                  tools=[write], system_prompt='s')
    manager = AgentManager(agent)
    monkeypatch.setattr(manager, 'approval_gate', ApprovalGate(cache_dir=str(tmp_path)))
    result = await manager.call_tools(
        [{'name': 'Write', 'tool_call_id': 'c1',
          'arguments': {'file_path': str(target), 'content': ''}}],
        interface='auto',
    )
    entry = result['result'][0]
    assert not entry['success']
    assert 'blocked by the reviewer: wipes a file the goal never mentions' in entry['data']
    assert target.read_text() == 'important'
