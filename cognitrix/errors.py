"""Cross-layer exceptions that must stop task execution immediately."""


class ExecutionControlError(RuntimeError):
    """A task boundary rejected new work; callers must not downgrade it."""


class ProviderExecutionError(ExecutionControlError):
    """A provider transport/protocol failure cannot be treated as task text."""
