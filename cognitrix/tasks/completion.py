"""Durable completion projection and webhook delivery for task runs."""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.repository import RunRepository
from cognitrix.tasks.run import TaskRunHead, TaskRunStatus
from cognitrix.utils.webhooks import notify_completion

logger = logging.getLogger("cognitrix.log")

_TASK_STATUS = {
    TaskRunStatus.QUEUED: TaskStatus.IN_PROGRESS,
    TaskRunStatus.RUNNING: TaskStatus.IN_PROGRESS,
    TaskRunStatus.CANCELLING: TaskStatus.IN_PROGRESS,
    TaskRunStatus.COMPLETED: TaskStatus.COMPLETED,
    TaskRunStatus.FAILED: TaskStatus.FAILED,
    TaskRunStatus.CANCELLED: TaskStatus.CANCELLED,
}


async def project_task_status(
    task_id: str,
    *,
    repository: RunRepository | None = None,
) -> TaskStatus | None:
    """Project the latest authoritative run onto the legacy Task cache."""
    repository = repository or RunRepository()
    for _ in range(8):
        latest = await repository.latest_run(task_id)
        if latest is None:
            return None
        status = _TASK_STATUS[latest.status]
        await Task.update_one(
            {"id": task_id},
            {"status": status.value},
        )
        after = await repository.latest_run(task_id)
        if (
            after is not None
            and after.id == latest.id
            and after.version == latest.version
            and after.status == latest.status
        ):
            return status
    raise RuntimeError(f"Task {task_id} latest run changed too frequently")


async def reconcile_terminal_task_statuses(
    *,
    repository: RunRepository | None = None,
) -> list[str]:
    """Repair stale legacy ``Task.status`` caches from terminal run heads.

    A worker can commit the authoritative terminal ``TaskRun`` and then stop
    before projecting that state onto the legacy ``Task`` row.  Recovery scans
    the compact head table so that those already-terminal runs are repaired on
    a later pass as well as runs terminalized by the current pass.
    """
    repository = repository or RunRepository()
    repaired: list[str] = []
    for head in await TaskRunHead.all():
        if not head.latest_run_id:
            continue
        latest = await repository.latest_run(head.task_id)
        if latest is None or latest.id != head.latest_run_id:
            continue
        if latest.status not in {
            TaskRunStatus.COMPLETED,
            TaskRunStatus.FAILED,
            TaskRunStatus.CANCELLED,
        }:
            continue
        expected = _TASK_STATUS[latest.status]
        task = await Task.get(head.task_id)
        if task is None:
            continue
        current = getattr(task.status, "value", task.status)
        if current == expected.value:
            continue
        if await project_task_status(head.task_id, repository=repository):
            repaired.append(head.task_id)
    return repaired


async def deliver_completion_notification(
    run_id: str,
    *,
    repository: RunRepository | None = None,
    owner: str | None = None,
) -> bool:
    """Claim and deliver one terminal notification; safe under concurrency."""
    repository = repository or RunRepository()
    owner = owner or f"completion:{uuid.uuid4()}"
    claimed = await repository.claim_completion_notification(
        run_id,
        owner=owner,
    )
    if claimed is None:
        return False

    task = await Task.get(claimed.task_id)
    if task is None:
        task = SimpleNamespace(id=claimed.task_id)
    delivered = await notify_completion(task, claimed)
    await repository.finish_completion_notification(
        run_id,
        owner=owner,
        delivered=delivered,
    )
    return delivered


async def recover_completion_notifications(
    *,
    repository: RunRepository | None = None,
) -> list[str]:
    """Retry all pending/expired notification claims once."""
    repository = repository or RunRepository()
    delivered: list[str] = []
    for run in await repository.completion_notification_candidates():
        try:
            if await deliver_completion_notification(
                run.id,
                repository=repository,
            ):
                delivered.append(run.id)
        except Exception:
            logger.exception(
                "Completion notification recovery failed for run %s",
                run.id,
            )
    return delivered
