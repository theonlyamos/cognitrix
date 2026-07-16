"""Sanitized immutable phase metrics for task-run observability.

The recorder deliberately captures usage through a task-local context rather
than subtracting run-wide ledger snapshots. Durable DAG steps may overlap; a
global before/after delta would attribute sibling calls to both phases.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from odbms import Model
from pydantic import Field, field_serializer, validator

from cognitrix.tasks.accounting import TaskUsageCollector, capture_task_usage
from cognitrix.tasks.run import RUN_TIMESTAMP_FORMAT, utc_now

logger = logging.getLogger("cognitrix.log")


class TaskRunPhase(str, Enum):
    QUEUE = "queue"
    PLAN = "plan"
    ASSIGN = "assign"
    STEP = "step"
    EVALUATE = "evaluate"
    RETRY = "retry"
    SYNTHESIS = "synthesis"


class TaskRunPhaseStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskRunMetricError(str, Enum):
    WORKER_LOST = "worker_lost"
    LEASE_LOST = "lease_lost"
    QUEUE_TIMEOUT = "queue_timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    TOOL_ERROR = "tool_error"
    AUTHORITY_INVALID = "authority_invalid"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    LIMIT_BACKEND_UNAVAILABLE = "limit_backend_unavailable"
    CONCURRENCY_EXHAUSTED = "concurrency_exhausted"
    VALIDATION_FAILED = "validation_failed"
    PERSISTENCE_ERROR = "persistence_error"
    UNKNOWN = "unknown"


class TaskRunPhaseMetric(Model):
    run_id: str
    step_index: int | None = None
    phase: TaskRunPhase
    attempt: int = Field(default=1, ge=1)
    status: TaskRunPhaseStatus = TaskRunPhaseStatus.RUNNING
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    tool_attempts: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    error_code: TaskRunMetricError | None = None

    @validator("attempt", pre=True)
    def _coerce_attempt(cls, value):
        return 1 if value is None else value

    @validator(
        "duration_ms",
        "prompt_tokens",
        "completion_tokens",
        "llm_calls",
        "tool_calls",
        "tool_attempts",
        pre=True,
    )
    def _coerce_integer(cls, value):
        return 0 if value is None else value

    @validator("cost_usd", pre=True)
    def _coerce_cost(cls, value):
        return Decimal("0") if value is None else value

    @field_serializer("cost_usd")
    def _serialize_cost(self, value: Decimal) -> str:
        return format(value, "f")


class _MetricRepository(Protocol):
    async def record_metric(
        self,
        run_id: str,
        *,
        claim: Any,
        metric: TaskRunPhaseMetric,
    ) -> TaskRunPhaseMetric: ...


ErrorClassifier = Callable[
    [BaseException],
    TaskRunMetricError | str | None,
]


def _duration_ms(started_at: str | None, completed_at: str | None) -> int:
    if not started_at or not completed_at:
        return 0
    try:
        started = datetime.strptime(started_at, RUN_TIMESTAMP_FORMAT)
        completed = datetime.strptime(completed_at, RUN_TIMESTAMP_FORMAT)
    except (TypeError, ValueError):
        return 0
    return max(0, round((completed - started).total_seconds() * 1000))


def _bounded_error_code(
    exc: BaseException,
    classifier: ErrorClassifier | None,
) -> TaskRunMetricError:
    if isinstance(exc, asyncio.CancelledError):
        return TaskRunMetricError.CANCELLED
    if classifier is None:
        return TaskRunMetricError.UNKNOWN
    try:
        value = classifier(exc)
        return TaskRunMetricError(value or TaskRunMetricError.UNKNOWN)
    except (TypeError, ValueError):
        return TaskRunMetricError.UNKNOWN


class TaskRunPhaseRecorder:
    """Measure and persist one immutable metric per real lifecycle phase.

    The repository remains the lease-fencing authority. A metric persistence
    failure propagates when the measured work succeeded, but never replaces an
    exception already raised by the phase (especially cancellation/lease loss).
    """

    def __init__(
        self,
        repository: _MetricRepository,
        *,
        run_id: str,
        claim: Any,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], str] = utc_now,
        error_classifier: ErrorClassifier | None = None,
    ) -> None:
        self.repository = repository
        self.run_id = run_id
        self.claim = claim
        self._clock = clock
        self._now = now
        self._error_classifier = error_classifier

    async def _persist(self, metric: TaskRunPhaseMetric) -> TaskRunPhaseMetric:
        return await self.repository.record_metric(
            self.run_id,
            claim=self.claim,
            metric=metric,
        )

    @asynccontextmanager
    async def measure(
        self,
        phase: TaskRunPhase | str,
        *,
        step_index: int | None = None,
        attempt: int = 1,
    ) -> AsyncIterator[TaskUsageCollector]:
        started_at = self._now()
        started_clock = self._clock()
        raised: BaseException | None = None
        status = TaskRunPhaseStatus.COMPLETED
        error_code: TaskRunMetricError | None = None
        collector: TaskUsageCollector | None = None
        try:
            async with capture_task_usage() as collector:
                try:
                    yield collector
                except asyncio.CancelledError as exc:
                    raised = exc
                    status = TaskRunPhaseStatus.CANCELLED
                    error_code = TaskRunMetricError.CANCELLED
                    raise
                except BaseException as exc:
                    raised = exc
                    status = TaskRunPhaseStatus.FAILED
                    error_code = _bounded_error_code(exc, self._error_classifier)
                    raise
        finally:
            completed_at = self._now()
            elapsed_ms = max(0, round((self._clock() - started_clock) * 1000))
            usage = collector.snapshot() if collector is not None else {}
            try:
                await self._persist(
                    TaskRunPhaseMetric(
                        run_id=self.run_id,
                        step_index=step_index,
                        phase=phase,
                        attempt=attempt,
                        status=status,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_ms=elapsed_ms,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        llm_calls=usage.get("llm_calls", 0),
                        tool_calls=usage.get("tool_calls", 0),
                        tool_attempts=usage.get("tool_attempts", 0),
                        cost_usd=usage.get("cost_usd", "0"),
                        error_code=error_code,
                    )
                )
            except BaseException:
                if raised is None:
                    raise
                logger.exception(
                    "Could not persist %s phase metric for run %s",
                    phase,
                    self.run_id,
                )

    async def record_completed(
        self,
        phase: TaskRunPhase | str,
        *,
        started_at: str | None,
        completed_at: str | None,
        step_index: int | None = None,
        attempt: int = 1,
    ) -> TaskRunPhaseMetric:
        """Persist an already-completed phase such as time spent queued."""
        metric = TaskRunPhaseMetric(
            run_id=self.run_id,
            step_index=step_index,
            phase=phase,
            attempt=attempt,
            status=TaskRunPhaseStatus.COMPLETED,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=_duration_ms(started_at, completed_at),
        )
        return await self._persist(metric)
