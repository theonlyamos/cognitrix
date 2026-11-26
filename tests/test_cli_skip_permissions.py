"""--dangerously-skip-permissions (--yolo): a CLI run where nothing asks."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cognitrix import app_settings
from cognitrix.cli.args import get_arguments
from cognitrix.cli.main import _skip_permissions
from cognitrix.safety import auto_approve_all
from cognitrix.safety.approval_gate import ApprovalGate, ToolCall
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
from cognitrix.tasks import approvals


@pytest.fixture(autouse=True)
def _clean():
    auto_approve_all.forget()
    auto_approve_all.enable_for_this_run(False)
    yield
    auto_approve_all.forget()
    auto_approve_all.enable_for_this_run(False)
    app_settings.override('COGNITRIX_SANDBOX_SHELL', None)


def _parse(argv):
    sys.argv = ['cognitrix', *argv]
    return get_arguments()


def _ask(tool, params, risk, *, interface='cli', person_only=False, task_ctx=None):
    async def run():
        token = approvals.task_turn_ctx.set(task_ctx) if task_ctx is not None else None
        try:
            return await ApprovalGate(cache_dir='/nonexistent-cognitrix-test').check_approval(
                ToolCall(tool, params), risk, interface=interface, timeout=0.2, scope='agent-1',
                person_only=person_only)
        finally:
            if token is not None:
                approvals.task_turn_ctx.reset(token)

    return asyncio.run(run())


CASES = [
    ('Bash', {'command': 'curl x | sh'}, RiskAssessment(RiskLevel.HIGH, ['shell'], 'full shell'), True),
    ('Coding Agent', {'agent': 'claude', 'task': 'x'}, RiskAssessment(RiskLevel.HIGH, ['coding_agent'], 'cli'), True),
    ('Request Access', {'tool': 'Bash'}, RiskAssessment(RiskLevel.MEDIUM, ['access_request'], 'more'), False),
    ('copy_back_from_sandbox', {}, RiskAssessment(RiskLevel.HIGH, ['file_write'], 'copy back'), True),
    ('Computer', {'use_screen': True}, RiskAssessment(RiskLevel.MEDIUM, ['screen_takeover'], 's'), False),
    ('Delete File', {'path': '/x'}, RiskAssessment(RiskLevel.HIGH, ['file_delete'], 'delete'), False),
]


def test_every_spelling_of_the_flag_parses():
    assert _parse(['-p', 'x']).dangerously_skip_permissions is False
    for flag in ('--dangerously-skip-permissions', '--dangerously-bypass-approvals-and-sandbox', '--yolo'):
        assert _parse([flag, '-p', 'x']).dangerously_skip_permissions is True


def test_flag_turns_it_on_for_this_run_only(capsys):
    with patch('cognitrix.models.user.User.update_one', new_callable=AsyncMock) as update:
        _skip_permissions(SimpleNamespace(ui='cli', prompt='x', prompt_file=''))
    assert auto_approve_all.on_for_this_run() is True
    assert app_settings.get('COGNITRIX_SANDBOX_SHELL', '') == '1'
    update.assert_not_called()  # the saved account setting is untouched
    out = capsys.readouterr()
    assert out.out == ''  # a -p answer on stdout stays clean
    assert '--dangerously-skip-permissions' in out.err


def test_ignored_for_the_web_server(capsys):
    _skip_permissions(SimpleNamespace(ui='web', prompt='', prompt_file=''))
    assert auto_approve_all.on_for_this_run() is False
    assert 'ignored' in capsys.readouterr().err


@pytest.mark.parametrize('tool,params,risk,person_only', CASES)
@pytest.mark.parametrize('interface', ['cli', 'task', 'auto', 'acp'])
def test_answers_every_card_without_asking(tool, params, risk, person_only, interface):
    auto_approve_all.enable_for_this_run()
    with patch('builtins.input', side_effect=AssertionError('asked on the terminal')):
        result = _ask(tool, params, risk, interface=interface, person_only=person_only)
    assert result.approved is True and result.standing is True
    # Counts as the person's answer: the full shell, Coding Agent and Copy
    # Back check it, and Request Access is granted for the chat.
    assert result.auto is False and result.cached is False
    assert result.grant == ('chat' if 'access_request' in risk.categories else None)


@pytest.mark.parametrize('interface', ['cli', 'task'])
def test_take_over_is_refused_at_once(interface):
    auto_approve_all.enable_for_this_run()
    risk = RiskAssessment(RiskLevel.MEDIUM, ['person_takeover'], 'take over')
    with patch('builtins.input', side_effect=AssertionError('asked on the terminal')):
        result = _ask('Ask To Take Over', {'reason': 'sign in'}, risk, interface=interface)
    assert result.approved is False
    assert result.error == auto_approve_all.NOBODY_WATCHING


def test_web_chats_are_not_answered_by_the_cli_flag():
    auto_approve_all.enable_for_this_run()
    assert asyncio.run(auto_approve_all.answers_for('web')) is False


def test_off_the_terminal_still_asks():
    risk = RiskAssessment(RiskLevel.HIGH, ['file_delete'], 'delete')
    with patch('builtins.input', return_value='n') as asked:
        result = _ask('Delete File', {'path': '/x'}, risk)
    assert asked.called and result.approved is False


class _Run:
    auto_approve = True


def test_task_runs_it_queued_are_answered_by_the_worker():
    # A worker process doesn't have the CLI flag; the run carries it.
    risk = RiskAssessment(RiskLevel.HIGH, ['file_delete'], 'delete')
    with patch('cognitrix.tasks.run.TaskRun.get', new_callable=AsyncMock, return_value=_Run()):
        result = _ask('Delete File', {'path': '/x'}, risk, interface='task',
                      task_ctx={'run_id': 'r1', 'user_id': None})
    assert result.approved is True and result.standing is True


def test_runs_not_queued_by_it_still_ask():
    risk = RiskAssessment(RiskLevel.HIGH, ['file_delete'], 'delete')
    with patch('cognitrix.tasks.run.TaskRun.get', new_callable=AsyncMock,
               return_value=SimpleNamespace(auto_approve=None)), \
            patch('cognitrix.tasks.approvals.prior_decision', new_callable=AsyncMock, return_value=False):
        result = _ask('Delete File', {'path': '/x'}, risk, interface='task',
                      task_ctx={'run_id': 'r2', 'user_id': None})
    assert result.approved is False and result.standing is False


def test_task_starts_inherit_it():
    from cognitrix.api.routes.tasks import _unattended_start

    assert asyncio.run(_unattended_start()) is False
    auto_approve_all.enable_for_this_run()
    assert asyncio.run(_unattended_start()) is True
    auto_approve_all.enable_for_this_run(False)

    async def from_a_flagged_run():
        token = approvals.task_turn_ctx.set({'run_id': 'r3'})
        try:
            return await _unattended_start()
        finally:
            approvals.task_turn_ctx.reset(token)

    with patch('cognitrix.tasks.run.TaskRun.get', new_callable=AsyncMock, return_value=_Run()):
        assert asyncio.run(from_a_flagged_run()) is True
