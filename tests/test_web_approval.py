"""P4 web approval bridge: prompt→resolve, bypass, and cross-user isolation."""

import asyncio

from cognitrix.safety.approval_gate import (
    _WEB_PENDING,
    ApprovalGate,
    ToolCall,
    resolve_web_approval,
    web_turn_ctx,
)
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel


def _high_risk():
    return RiskAssessment(risk_level=RiskLevel.HIGH, categories=['code_execution'], details='rm -rf')


def test_web_approval_emits_and_resolves():
    async def run():
        gate = ApprovalGate()
        emitted = []

        async def emit(payload):
            emitted.append(payload)
            # Browser "approves" — resolve the pending future.
            resolve_web_approval(payload['request_id'], approved=True, user_key='u1')

        token = web_turn_ctx.set({'emit': emit, 'session_id': 's1', 'bypass': False, 'user_key': 'u1'})
        try:
            result = await gate.check_approval(
                ToolCall('Bash', {'command': 'rm x'}), _high_risk(),
                interface='web', timeout=5, scope='u1',
            )
        finally:
            web_turn_ctx.reset(token)
        return result, emitted

    result, emitted = asyncio.run(run())
    assert result.approved is True
    assert emitted and emitted[0]['type'] == 'approval_request'
    assert emitted[0]['risk_level'] == 'high'
    assert emitted[0]['tool_name'] == 'Bash'


def test_web_bypass_auto_approves_without_channel():
    async def run():
        gate = ApprovalGate()
        # bypass on, no emit channel — must still auto-approve (approvals only).
        token = web_turn_ctx.set({'emit': None, 'session_id': 's', 'bypass': True, 'user_key': 'u'})
        try:
            return await gate.check_approval(
                ToolCall('Bash', {'command': 'rm x'}), _high_risk(), interface='web', timeout=5,
            )
        finally:
            web_turn_ctx.reset(token)

    result = asyncio.run(run())
    assert result.approved is True and result.auto is True


def test_web_no_channel_denies():
    async def run():
        gate = ApprovalGate()
        # No web_turn_ctx bound (e.g. programmatic generate) → deny risky tool.
        return await gate.check_approval(
            ToolCall('Bash', {'command': 'rm x'}), _high_risk(), interface='web', timeout=1,
        )

    result = asyncio.run(run())
    assert result.approved is False and result.error


def test_resolve_is_user_scoped():
    async def run():
        fut = asyncio.get_event_loop().create_future()
        _WEB_PENDING['rid-x'] = {'future': fut, 'user_key': 'owner'}
        # A different user cannot answer someone else's prompt.
        assert resolve_web_approval('rid-x', approved=True, user_key='intruder') is False
        assert not fut.done()
        # The owner can.
        assert resolve_web_approval('rid-x', approved=True, user_key='owner') is True
        assert fut.done() and fut.result().approved is True
        _WEB_PENDING.pop('rid-x', None)
        # Unknown id → False.
        assert resolve_web_approval('nope', approved=True, user_key='owner') is False

    asyncio.run(run())
