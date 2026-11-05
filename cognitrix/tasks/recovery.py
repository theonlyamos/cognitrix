"""Lease heartbeats and idempotent recovery for durable task runs."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cognitrix.tasks.metrics import TaskRunMetricError
from cognitrix.tasks.completion import (
    reconcile_terminal_task_statuses,
    recover_completion_notifications,
)
from cognitrix.tasks.repository import LeaseClaim, RunRepository
from cognitrix.tasks.run import RUN_TIMESTAMP_FORMAT, TaskRun, TaskRunStatus


DEFAULT_QUEUE_TIMEOUT_SECONDS = 300
DEFAULT_RECOVERY_INTERVAL_SECONDS = 30.0
MAX_RECOVERY_CAS_ATTEMPTS = 8

logger = logging.getLogger("cognitrix.log")


@dataclass(frozen=True)
class _RecoveryAction:
    status: TaskRunStatus
    error_code: str
    error: str


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).replace(tzinfo=None).strftime(RUN_TIMESTAMP_FORMAT)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, RUN_TIMESTAMP_FORMAT)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return _utc(parsed)


def _recovery_action(
    run: TaskRun,
    *,
    now: datetime,
    queue_timeout_seconds: int,
) -> _RecoveryAction | None:
    if run.status == TaskRunStatus.QUEUED:
        stored = run.json()
        queued_at = _parse_timestamp(run.queued_at or stored.get("created_at"))
        timed_out = queued_at is None or now >= queued_at + timedelta(
            seconds=queue_timeout_seconds
        )
        if not timed_out:
            return None
        return _RecoveryAction(
            status=TaskRunStatus.FAILED,
            error_code=TaskRunMetricError.QUEUE_TIMEOUT.value,
            error="task run exceeded queue wait timeout",
        )

    if run.status not in (TaskRunStatus.RUNNING, TaskRunStatus.CANCELLING):
        return None

    lease_expires_at = _parse_timestamp(run.lease_expires_at)
    if lease_expires_at is not None and lease_expires_at > now:
        return None

    # The durable timestamp, rather than a status alone, proves that a user
    # cancellation request committed before recovery chose a terminal cause.
    if run.cancel_requested_at:
        return _RecoveryAction(
            status=TaskRunStatus.CANCELLED,
            error_code=TaskRunMetricError.CANCELLED.value,
            error="cancelled by user",
        )

    return _RecoveryAction(
        status=TaskRunStatus.FAILED,
        error_code=TaskRunMetricError.WORKER_LOST.value,
        error="worker lease expired",
    )


async def recover_run(
    run_id: str,
    *,
    repository: RunRepository | None = None,
    now: datetime | None = None,
    queue_timeout_seconds: int = DEFAULT_QUEUE_TIMEOUT_SECONDS,
) -> TaskRun | None:
    """Recover one stale run, re-evaluating any state that wins the CAS race."""
    if queue_timeout_seconds < 0:
        raise ValueError("queue_timeout_seconds cannot be negative")
    repository = repository or RunRepository()
    current_time = _utc(now)

    for _ in range(MAX_RECOVERY_CAS_ATTEMPTS):
        run = await TaskRun.get(run_id)
        if run is None:
            return None
        action = _recovery_action(
            run,
            now=current_time,
            queue_timeout_seconds=queue_timeout_seconds,
        )
        if action is None:
            return None

        recovered = await repository.recover_terminal(
            run,
            status=action.status,
            error_code=action.error_code,
            error=action.error,
            completed_at=_timestamp(current_time),
            queue_timeout_seconds=queue_timeout_seconds,
        )
        if recovered is not None:
            return recovered
        await asyncio.sleep(0)
    return None


async def recover_stale_runs(
    *,
    repository: RunRepository | None = None,
    now: datetime | None = None,
    queue_timeout_seconds: int = DEFAULT_QUEUE_TIMEOUT_SECONDS,
) -> list[TaskRun]:
    """Scan all runs once and return only rows terminalized by this scan."""
    if queue_timeout_seconds < 0:
        raise ValueError("queue_timeout_seconds cannot be negative")
    repository = repository or RunRepository()
    current_time = _utc(now)
    recovered: list[TaskRun] = []
    runs = await repository.recovery_candidates()
    for run in runs:
        stored = await recover_run(
            run.id,
            repository=repository,
            now=current_time,
            queue_timeout_seconds=queue_timeout_seconds,
        )
        if stored is not None:
            recovered.append(stored)
    return recovered


async def run_recovery_pass(
    *,
    repository: RunRepository | None = None,
    queue_timeout_seconds: int = DEFAULT_QUEUE_TIMEOUT_SECONDS,
) -> tuple[list[str], list[TaskRun]]:
    """Repair authoritative state before attempting optional event delivery."""
    repository = repository or RunRepository()
    await repository.recover_stale_reservations(
        reservation_timeout_seconds=queue_timeout_seconds,
    )
    recovered_runs = await recover_stale_runs(
        repository=repository,
        queue_timeout_seconds=queue_timeout_seconds,
    )
    await reconcile_terminal_task_statuses(repository=repository)
    await recover_completion_notifications(repository=repository)
    try:
        recovered_outboxes = await repository.recover_outboxes()
    except Exception:
        logger.warning(
            "Task recovery completed with pending event outboxes",
            exc_info=True,
        )
        recovered_outboxes = []
    return recovered_outboxes, recovered_runs


async def recovery_loop(
    *,
    interval_seconds: float = DEFAULT_RECOVERY_INTERVAL_SECONDS,
    repository: RunRepository | None = None,
    queue_timeout_seconds: int = DEFAULT_QUEUE_TIMEOUT_SECONDS,
) -> None:
    """Run recovery periodically until the owning application cancels us."""
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    repository = repository or RunRepository()
    logger.info("Task recovery started (scan every %ss)", interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await run_recovery_pass(
                repository=repository,
                queue_timeout_seconds=queue_timeout_seconds,
            )
        except Exception:
            logger.exception("Task recovery pass failed")


class LeaseController:
    """Keep a lease alive for the lifetime of an async context manager."""

    def __init__(
        self,
        claim: LeaseClaim,
        *,
        repository: RunRepository | None = None,
        lease_seconds: int = 60,
        heartbeat_interval: float | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        interval = (
            min(10.0, lease_seconds / 3)
            if heartbeat_interval is None
            else heartbeat_interval
        )
        if interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        if interval >= lease_seconds:
            raise ValueError(
                "heartbeat_interval must be less than lease_seconds"
            )
        self.claim = claim
        self.repository = repository or RunRepository()
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = interval
        self._stop = asyncio.Event()
        self._failed = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._error: Exception | None = None

    async def heartbeat(self) -> TaskRun:
        return await self.repository.heartbeat(
            self.claim.run_id,
            claim=self.claim,
            lease_seconds=self.lease_seconds,
        )

    async def _terminalized_by_current_claim(self) -> bool:
        """Recognize the harmless final-heartbeat race after our own CAS.

        Worker terminalization deliberately retains the final owner/generation
        token. Recovery and force-cancel advance the generation, so matching a
        terminal row to this exact claim cannot disguise an external fence.
        """
        try:
            run = await TaskRun.get(self.claim.run_id)
        except Exception:
            return False
        return bool(
            run is not None
            and run.status in {
                TaskRunStatus.COMPLETED,
                TaskRunStatus.FAILED,
                TaskRunStatus.CANCELLED,
            }
            and run.lease_owner == self.claim.owner
            and run.lease_generation == self.claim.generation
        )

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.heartbeat_interval,
                )
            except TimeoutError:
                try:
                    await self.heartbeat()
                except Exception as exc:
                    if await self._terminalized_by_current_claim():
                        # The body committed its terminal CAS while this final
                        # heartbeat was in flight.  The durable outcome wins;
                        # there is no live lease left to maintain.
                        self._stop.set()
                        return
                    self._error = exc
                    self._failed.set()
                    self._stop.set()
                    return

    def checkpoint(self) -> None:
        """Raise a heartbeat failure at a cooperative execution boundary."""
        if self._error is not None:
            raise self._error

    async def wait_failed(self) -> None:
        """Wait until heartbeat fails, then raise the underlying lease error."""
        await self._failed.wait()
        self.checkpoint()

    async def __aenter__(self) -> LeaseController:
        if self._task is not None:
            raise RuntimeError("LeaseController is already running")
        self._stop.clear()
        self._failed.clear()
        self._error = None
        await self.heartbeat()
        self._task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            await task
        if exc_type is None and self._error is not None:
            raise self._error
