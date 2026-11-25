"""Calls a tool only runs after a person approved them (the full shell, Coding
Agent) always reach the person as an approval card: an "allow" permission
level, an auto-approve or the action reviewer must not wave them through,
because the tool would then refuse with no card for anyone to answer."""

from types import SimpleNamespace

import pytest

from cognitrix.common import shell as sh
from cognitrix.common.folders import WRITE, Folder
from cognitrix.common.process_security import HostProcessMode
from cognitrix.config import settings
from cognitrix.safety import action_reviewer
from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, web_turn_ctx
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
from cognitrix.safety.permissions import needs_person
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


def _tool(name: str):
    return SimpleNamespace(name=name, internal_params=['approved'])


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv('COGNITRIX_SANDBOX_SHELL', raising=False)
    monkeypatch.delenv('COGNITRIX_SHELL_MODE', raising=False)


def test_coding_agent_run_needs_a_person(clean_env):
    tools = (_tool('Coding Agent'),)
    assert needs_person(tools, {'task': 'fix the tests', 'agent': 'codex'})
    assert needs_person(tools, {'task': 'fix the tests'})
    # Listing what's installed doesn't start anything.
    assert not needs_person(tools, {'agent': 'list'})


def test_coding_agent_in_sandbox_shell_mode_runs_without_a_person(clean_env, monkeypatch):
    monkeypatch.setenv('COGNITRIX_SANDBOX_SHELL', '1')
    assert not needs_person((_tool('Coding Agent'),), {'task': 'x'})


def test_full_shell_is_planned_against_the_chat_folder(clean_env, tmp_path, monkeypatch):
    """The gate plans a command against the folder the Bash tool will use, not
    the app's tools folder, so a path outside the chat's folder asks first."""
    app_root = tmp_path / 'app'
    chat = tmp_path / 'chat'
    app_root.mkdir()
    chat.mkdir()
    (app_root / 'notes.txt').write_text('x', encoding='utf-8')
    monkeypatch.setattr(settings, 'tools_root', app_root.resolve())
    folders = (Folder(path=str(chat.resolve()), access=WRITE, primary=True),)
    token = set_execution_context(ToolExecutionContext(
        user_id='u1', session_id='s-root', folders=folders, host_process_mode=HostProcessMode.TRUSTED_LOCAL,
    ))
    try:
        outside = str((app_root / 'notes.txt').resolve())
        args = {'command': f'cat {outside}'}
        assert sh.plan_call(args['command'], sh.bash_root(args)).kind == 'shell'
        assert needs_person((_tool('Bash'),), args)
        level, details = sh.shell_risk(args)
        assert level == 'high' and details.startswith('Runs in the full shell')
        # A path in the chat's folder stays on the allowlist.
        (chat / 'a.txt').write_text('y', encoding='utf-8')
        assert not needs_person((_tool('Bash'),), {'command': 'cat a.txt'})
    finally:
        reset_execution_context(token)


@pytest.mark.asyncio
async def test_reviewer_never_answers_a_person_only_call(tmp_path, monkeypatch):
    gate = ApprovalGate(cache_dir=str(tmp_path))
    reviewed: list[str] = []

    async def review(*_args, **_kwargs):
        reviewed.append('called')
        return ApprovalResult(approved=True, auto=True, reviewed=True)

    asked: list[bool] = []

    async def web_approval(tool_call, risk, timeout, person_only=False, **_kwargs):
        asked.append(person_only)
        return ApprovalResult(approved=True)

    monkeypatch.setattr(gate, '_review', review)
    monkeypatch.setattr(gate, '_web_approval', web_approval)
    monkeypatch.setattr(action_reviewer, 'should_review', lambda *_a, **_k: True)
    risk = RiskAssessment(RiskLevel.HIGH, ['coding_agent'], 'Hands the work to Codex')
    call = SimpleNamespace(tool_name='Coding Agent', params={'task': 'x'}, app=None, access=None)
    token = web_turn_ctx.set({'emit': None, 'session_id': 's1', 'bypass': True, 'user_key': 'u1'})
    try:
        result = await gate.check_approval(call, risk, interface='web', scope='a1', person_only=True)
    finally:
        web_turn_ctx.reset(token)
    assert result.approved and not result.auto
    assert reviewed == [] and asked == [True]
