"""Durable lifecycle record for a single task run.

``TaskRunStep`` rows are authoritative for new executions. ``plan`` remains a
compatibility projection and the fallback store for historical rows.

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

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from odbms import Model
from pydantic import Field, validator

from cognitrix.tasks.results import StepResult


RUN_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime(RUN_TIMESTAMP_FORMAT)


def final_result_update(value: StepResult | str | dict[str, Any]) -> dict[str, Any]:
    """Build one patch for the typed result and its legacy text projection."""
    result = StepResult.from_stored(value)
    return {
        "result": result.text,
        "result_data": result.model_dump(mode="json"),
    }


def run_acl_snapshot(*, team_id: str | None, agent_ids: list[str] | None) -> dict[str, Any]:
    """Build the immutable, secret-free resource ACL captured at enqueue."""
    return {
        "acl_version": 1,
        "acl_team_id": str(team_id) if team_id else None,
        "acl_agent_ids": sorted({str(value) for value in (agent_ids or [])}),
    }


def same_run_acl(left: "TaskRun", right: "TaskRun") -> bool:
    """Require an explicit identical ACL before copying historical outputs."""
    return bool(
        left.acl_version == right.acl_version == 1
        and left.acl_team_id == right.acl_team_id
        and set(left.acl_agent_ids) == set(right.acl_agent_ids)
    )


def run_acl_allowed(run: "TaskRun", authorization: Any) -> bool:
    """Authorize historical data without consulting the mutable current task.

    Pre-snapshot rows are visible only to the original JWT owner. API keys and
    ownerless rows fail closed because their historical resource scope cannot
    be reconstructed safely.
    """
    if run.acl_version != 1:
        if getattr(authorization, "api_key", None) is not None:
            return False
        user = getattr(authorization, "user", None)
        return bool(
            run.requested_by
            and user is not None
            and str(getattr(user, "id", "")) == str(run.requested_by)
        )
    if run.acl_team_id and not authorization.team_allowed(run.acl_team_id):
        return False
    return all(authorization.agent_allowed(value) for value in run.acl_agent_ids)


class TaskRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CANCELLING = "cancelling"


class TaskRunHead(Model):
    """One durable serialization row per task.

    The inherited primary-key ``id`` is set to ``task_id``. That constraint is
    portable across every supported database and makes active-run reservation
    cross-process, while ``latest_run_id`` gives API projections an O(1) read.
    """

    task_id: str
    latest_run_id: str | None = None
    active_run_id: str | None = None
    deleted_at: str | None = None
    """Admission tombstone serialized with active-run reservation CAS."""
    version: int = 0

    @validator("version", pre=True)
    def _coerce_null_version(cls, value):
        return 0 if value is None else value


class TaskRun(Model):
    task_id: str
    """The task this run executed"""

    status: TaskRunStatus = TaskRunStatus.RUNNING
    """Lifecycle state of the run"""

    plan: list[dict[str, Any]] = Field(default_factory=list)
    """Executed plan snapshot — see module docstring for the step schema"""

    result: str | None = None
    """Final synthesis of the run"""

    result_data: StepResult | None = None
    """Authoritative typed final result; ``result`` remains the text projection."""

    requested_by: str | None = None
    """Sanitized initiating identity, never a credential."""

    actor_key: str | None = None
    """Stable concurrency identity (JWT/API-key/scheduler/system)."""

    authority_kind: str = "system"
    """Credential class used to enqueue the run; contains no credential."""

    authority_id: str | None = None
    """Server-side identity reference (user or API-key id), never a secret."""

    acl_version: int = 0
    """Version of the immutable team/agent access snapshot (0 is legacy)."""

    acl_team_id: str | None = None
    """Team resource authorized when the run was queued."""

    acl_agent_ids: list[str] = Field(default_factory=list)
    """Agent resources authorized when the run was queued."""

    callback_url: str | None = None
    """Per-run webhook destination snapshot; never exposed by API projections."""

    callback_key_id: str | None = None
    """Per-run signing-key snapshot; never exposed by API projections."""

    completion_notification_state: str | None = None
    completion_notification_owner: str | None = None
    completion_notification_expires_at: str | None = None
    completion_notification_next_at: str | None = None
    completion_notification_attempts: int = 0
    completion_notified_at: str | None = None

    resume_from_run_id: str | None = None

    queue_job_id: str | None = None
    queued_at: str | None = None

    lease_owner: str | None = None
    lease_generation: int = 0
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    cancel_requested_at: str | None = None

    version: int = 0
    next_event_sequence: int = 0
    event_outbox: list[dict[str, Any]] = Field(default_factory=list)

    budget: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)

    error_code: str | None = None

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

    @validator("result_data", pre=True)
    def _parse_result_data(cls, value):
        if value is None or isinstance(value, StepResult):
            return value
        return StepResult.from_stored(value)

    @validator("event_outbox", pre=True)
    def _coerce_null_outbox(cls, value):
        return [] if value is None else value

    @validator("acl_agent_ids", pre=True)
    def _coerce_null_acl_agents(cls, value):
        return [] if value is None else value

    @validator("budget", "usage", pre=True)
    def _coerce_null_dict(cls, value):
        return {} if value is None else value

    @validator(
        "lease_generation",
        "version",
        "next_event_sequence",
        "completion_notification_attempts",
        "acl_version",
        pre=True,
    )
    def _coerce_null_integer(cls, value):
        return 0 if value is None else value
