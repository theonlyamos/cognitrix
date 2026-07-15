import pytest

from cognitrix.tools.resilient_tool_wrapper import ResilientToolManager
from cognitrix.tools.tool import tool


@pytest.mark.asyncio
async def test_retryable_tool_exception_reaches_recovery_wrapper(monkeypatch):
    calls = []

    @tool(retryable=True, max_attempts=2, approval_mode='assigned_only')
    async def flaky(value: int):
        calls.append(value)
        if value == 1:
            raise ValueError('bad value')
        return 'ok'

    manager = ResilientToolManager(llm=object())
    async def recover(*args, **kwargs):
        return {'value': 2}
    monkeypatch.setattr(manager, '_attempt_param_recovery', recover)

    result = await manager.run_tool(flaky, {'value': 1}, max_retries=2, attempt_recovery=True)
    assert result.success is True
    assert result.attempts == 2
    assert calls == [1, 2]
