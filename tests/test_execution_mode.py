import pytest

from cognitrix.tasks.execution_mode import ExecutionMode, parse_execution_mode


def test_execution_mode_defaults_to_chat():
    assert parse_execution_mode(None) is ExecutionMode.CHAT


@pytest.mark.parametrize("value", ["chat", ExecutionMode.CHAT])
def test_execution_mode_accepts_chat(value):
    assert parse_execution_mode(value) is ExecutionMode.CHAT


@pytest.mark.parametrize("value", ["task", ExecutionMode.TASK])
def test_execution_mode_accepts_task(value):
    assert parse_execution_mode(value) is ExecutionMode.TASK


@pytest.mark.parametrize("value", ["auto", "", 1, True])
def test_execution_mode_rejects_unknown_values(value):
    with pytest.raises(ValueError, match="execution_mode must be 'chat' or 'task'"):
        parse_execution_mode(value)
