from enum import StrEnum


class ExecutionMode(StrEnum):
    CHAT = "chat"
    TASK = "task"


def parse_execution_mode(value: object) -> ExecutionMode:
    if value is None:
        return ExecutionMode.CHAT
    try:
        return ExecutionMode(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("execution_mode must be 'chat' or 'task'") from exc
