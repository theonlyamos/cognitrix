"""The person's "Auto-approve everything" setting answers every card for them."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from cognitrix.safety import auto_approve_all
from cognitrix.safety.approval_gate import ApprovalGate, ToolCall, resolve_web_approval, web_turn_ctx
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel


@pytest.fixture(autouse=True)
def _clean():
    auto_approve_all.forget()
    yield
    auto_approve_all.forget()


def _ask(tool, params, risk, *, ctx, person_only=False, interface='web'):
    async def run():
        emitted = []

        async def emit(payload):
            emitted.append(payload)
            # The person says no on the card, so nothing waits.
            resolve_web_approval(payload['request_id'], approved=False, user_key='u1')

        token = web_turn_ctx.set({'emit': emit, 'session_id': 's1', 'bypass': False, 'user_key': 'u1', **ctx})
        try:
            result = await ApprovalGate().check_approval(
                ToolCall(tool, params), risk, interface=interface, timeout=0.2, scope='agent-1',
                person_only=person_only)
        finally:
            web_turn_ctx.reset(token)
        return result, emitted

    return asyncio.run(run())


CASES = [
    ('Bash', {'command': 'curl x | sh'}, RiskAssessment(RiskLevel.HIGH, ['shell'], 'full shell'), True),
    ('Coding Agent', {'agent': 'claude', 'task': 'x'}, RiskAssessment(RiskLevel.HIGH, ['coding_agent'], 'cli'), True),
    ('Request Access', {'tool': 'Bash'}, RiskAssessment(RiskLevel.MEDIUM, ['access_request'], 'more access'), False),
    ('request_folder', {'path': '/x'}, RiskAssessment(RiskLevel.MEDIUM, ['folder_request'], 'folder'), False),
    ('copy_back_from_sandbox', {}, RiskAssessment(RiskLevel.HIGH, ['file_write'], 'copy back'), True),
    ('Computer', {'use_screen': True}, RiskAssessment(RiskLevel.MEDIUM, ['desktop_control', 'screen_takeover'], 's'), False),
    ('Computer', {'app': 'cognitrix'}, RiskAssessment(RiskLevel.MEDIUM, ['desktop_control', 'own_app'], 'own'), False),
    ('Write File', {'path': '/etc/x'}, RiskAssessment(RiskLevel.HIGH, ['file_write'], 'write'), False),
]


@pytest.mark.parametrize('tool,params,risk,person_only', CASES)
def test_answers_every_kind_of_card_when_on(tool, params, risk, person_only):
    auto_approve_all.remember('u1', True)
    result, emitted = _ask(tool, params, risk, ctx={}, person_only=person_only)
    assert result.approved is True
    assert result.standing is True
    # Counts as the person's answer (the full shell and Coding Agent check it).
    assert result.auto is False and result.cached is False
    assert emitted == []
    if 'access_request' in risk.categories:
        assert result.grant == 'chat'


@pytest.mark.parametrize('tool,params,risk,person_only', CASES)
def test_asks_as_before_when_off(tool, params, risk, person_only):
    auto_approve_all.remember('u1', False)
    result, emitted = _ask(tool, params, risk, ctx={}, person_only=person_only)
    assert result.standing is False
    assert [e['type'] for e in emitted][0] == 'approval_request'


def test_never_answers_a_request_for_the_person_to_take_over():
    auto_approve_all.remember('u1', True)
    risk = RiskAssessment(RiskLevel.MEDIUM, ['person_takeover'], 'take over')
    result, emitted = _ask('ask_to_take_over', {'reason': 'captcha'}, risk, ctx={})
    assert result.standing is False
    assert [e['type'] for e in emitted][0] == 'approval_request'


def test_public_link_visitors_never_get_the_owners_yes():
    auto_approve_all.remember('u1', True)
    risk = RiskAssessment(RiskLevel.HIGH, ['file_write'], 'write')
    result, _ = _ask('Write File', {'path': '/x'}, risk, ctx={'auto_approve': False})
    assert result.approved is False and result.standing is False


def test_only_the_turns_owner_setting_counts():
    auto_approve_all.remember('someone-else', True)
    risk = RiskAssessment(RiskLevel.HIGH, ['file_write'], 'write')
    result, emitted = _ask('Write File', {'path': '/x'}, risk, ctx={})
    assert result.standing is False and emitted


def test_background_tasks_follow_the_person_who_started_them():
    from cognitrix.tasks import approvals

    auto_approve_all.remember('u1', True)
    risk = RiskAssessment(RiskLevel.HIGH, ['shell'], 'full shell')

    async def run():
        token = approvals.task_turn_ctx.set({'run_id': 'r1', 'user_id': 'u1'})
        try:
            with patch.object(approvals, 'request_task_approval', new_callable=AsyncMock) as park:
                result = await ApprovalGate().check_approval(
                    ToolCall('Bash', {'command': 'curl x | sh'}), risk, interface='task', person_only=True)
            return result, park
        finally:
            approvals.task_turn_ctx.reset(token)

    result, park = asyncio.run(run())
    assert result.approved is True and result.standing is True
    park.assert_not_called()


def test_cli_is_not_affected():
    auto_approve_all.remember('u1', True)
    assert asyncio.run(auto_approve_all.answers_for('cli')) is False


def test_setting_is_saved_on_the_account():
    async def run():
        with patch('cognitrix.models.user.User.update_one', new_callable=AsyncMock) as update:
            await auto_approve_all.set_on('u1', True)
        return update

    update = asyncio.run(run())
    update.assert_awaited_once_with({'id': 'u1'}, {'auto_approve_all': True})
    assert asyncio.run(auto_approve_all.is_on('u1')) is True


def test_unknown_person_reads_as_off():
    async def run():
        with patch('cognitrix.models.user.User.get', new_callable=AsyncMock, return_value=None):
            return await auto_approve_all.is_on('nobody')

    assert asyncio.run(run()) is False
    assert asyncio.run(auto_approve_all.is_on('')) is False


def test_turning_it_off_reaches_other_processes_within_seconds():
    # A task worker read "on" earlier; the web app then saved "off".
    auto_approve_all.remember('u1', True)
    key = 'u1'
    on, _ = auto_approve_all._CACHE[key]
    auto_approve_all._CACHE[key] = (on, auto_approve_all.time.monotonic() - auto_approve_all.FRESH_FOR - 1)

    class Row:
        auto_approve_all = False

    async def run():
        with patch('cognitrix.models.user.User.get', new_callable=AsyncMock, return_value=Row()):
            return await auto_approve_all.is_on('u1')

    assert asyncio.run(run()) is False
