"""Authoritative per-step persistence for a task run."""

from enum import Enum
from typing import Any

from odbms import Model
from pydantic import Field, validator

from cognitrix.tasks.results import StepResult
from cognitrix.tasks.runtime import AgentRuntimeSnapshot


class TaskRunStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskRunStep(Model):
    run_id: str
    task_id: str | None = None
    step_index: int
    title: str
    description: str = ""
    expected_output: str = ""
    verification_criteria: str = ""
    agent_name: str = ""
    dependencies: list[int] = Field(default_factory=list)
    required_tools: list[str] | None = None
    runtime_snapshot: AgentRuntimeSnapshot | None = None
    status: TaskRunStepStatus = TaskRunStepStatus.PENDING
    attempts: int = 0
    result: StepResult | None = None
    gate: str | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    @validator("status", pre=True)
    def _parse_status(cls, value):
        if isinstance(value, TaskRunStepStatus):
            return value
        return TaskRunStepStatus(str(value).lower())

    @validator("dependencies", pre=True)
    def _coerce_dependencies(cls, value):
        return [] if value is None else value

    @validator("attempts", pre=True)
    def _coerce_attempts(cls, value):
        return 0 if value is None else value

    @validator("result", pre=True)
    def _parse_result(cls, value):
        if value is None or isinstance(value, StepResult):
            return value
        return StepResult.from_stored(value)

    def to_plan_entry(self) -> dict[str, Any]:
        """Compatibility projection consumed by existing API/UI clients."""
        return {
            "index": self.step_index,
            "title": self.title,
            "description": self.description,
            "expected_output": self.expected_output,
            "verification_criteria": self.verification_criteria,
            "agent_name": self.agent_name,
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "attempts": self.attempts,
            "result": self.result.text if self.result is not None else None,
            "gate": self.gate,
        }
