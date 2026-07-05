"""Execution record for a single task run.

A run is one execution of a Task by the orchestrator. Its ``plan`` field is the
executed snapshot — ``Task.step_instructions`` stays the authoring template,
per-run progress lives here. One Session per plan step links back via
``Session.run_id``/``step_index``.

Plan-step schema (list entries of ``plan``)::

    {
        'index': int,               # 0-based
        'title': str,
        'description': str,
        'expected_output': str,     # '' for template steps
        'verification_criteria': str,
        'agent_name': str,          # assigned executor
        'dependencies': list[int],  # indexes this step waits on
        'status': 'pending' | 'running' | 'done' | 'failed' | 'skipped' | 'cancelled',
        'attempts': int,
        'result': str | None,       # truncated ~8000 chars; feeds dependency
                                    # prompts + resume, server-side only
        'gate': 'passed' | 'unverified' | None,
    }

Persistence rule: instance ``save()`` exactly once at creation. Every later
write MUST be a partial ``TaskRun.update_one({'id': run.id}, {...})`` —
``Model.save()`` writes the full row and would clobber a concurrently written
``cancelling`` status from the cancel endpoint.
"""

from enum import Enum
from typing import Any

from odbms import Model
from pydantic import Field, validator


class TaskRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLING = "cancelling"


class TaskRun(Model):
    task_id: str
    """The task this run executed"""

    status: TaskRunStatus = TaskRunStatus.RUNNING
    """Lifecycle state of the run"""

    plan: list[dict[str, Any]] = Field(default_factory=list)
    """Executed plan snapshot — see module docstring for the step schema"""

    result: str | None = None
    """Final synthesis of the run"""

    error: str | None = None
    """Failure/cancellation detail"""

    started_at: str | None = None
    """When the run started"""

    completed_at: str | None = None
    """When the run reached a terminal state"""

    @validator("status", pre=True)
    def parse_status(cls, value):
        if isinstance(value, TaskRunStatus):
            return value
        if isinstance(value, str):
            return TaskRunStatus(value.lower())
        return value

    @validator("plan", pre=True)
    def _coerce_null_plan(cls, value):
        return [] if value is None else value
