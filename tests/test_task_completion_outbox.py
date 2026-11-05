"""Durable, idempotent completion-notification contracts."""

import asyncio
from types import SimpleNamespace

import pytest

from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.repository import RunRepository
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus


@pytest.fixture
async def completion_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / "completion-outbox.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()
    for model in (Task, TaskRun, TaskRunHead, TaskRunEvent):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


async def _terminal_run(*, callback: bool = True) -> TaskRun:
    repository = RunRepository()
    run = await repository.create_queued(
        task_id=f"task-callback-{callback}",
        callback_url="https://hooks.example.test/done" if callback else None,
        callback_key_id="key-1" if callback else None,
    )
    return await repository.mutate(
        run.id,
        claim=None,
        updates={
            "status": TaskRunStatus.COMPLETED.value,
            "completed_at": "2030-01-01 00:00:00",
        },
        expected_statuses={TaskRunStatus.QUEUED},
    )


@pytest.mark.asyncio
async def test_terminal_transition_atomically_arms_or_skips_delivery(completion_db):
    pending = await _terminal_run(callback=True)
    skipped = await _terminal_run(callback=False)

    assert pending.completion_notification_state == "pending"
    assert pending.callback_url == "https://hooks.example.test/done"
    assert pending.callback_key_id == "key-1"
    assert skipped.completion_notification_state == "skipped"


@pytest.mark.asyncio
async def test_concurrent_delivery_claims_send_once_and_ack_durably(
    completion_db,
    monkeypatch,
):
    from cognitrix.tasks import completion

    run = await _terminal_run()
    calls = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def notify(task, claimed):
        calls.append((task.id, claimed.id))
        entered.set()
        await release.wait()
        return True

    async def get_task(task_id):
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(completion, "notify_completion", notify)
    monkeypatch.setattr(completion.Task, "get", staticmethod(get_task))

    first = asyncio.create_task(
        completion.deliver_completion_notification(run.id, owner="worker-a")
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = await completion.deliver_completion_notification(
        run.id,
        owner="worker-b",
    )
    release.set()

    assert await first is True
    assert second is False
    assert calls == [(run.task_id, run.id)]
    stored = await TaskRun.get(run.id)
    assert stored is not None
    assert stored.completion_notification_state == "delivered"
    assert stored.completion_notification_attempts == 1
    assert stored.completion_notified_at


@pytest.mark.asyncio
async def test_failed_delivery_returns_to_outbox_for_recovery(
    completion_db,
    monkeypatch,
):
    from cognitrix.tasks import completion

    run = await _terminal_run()
    outcomes = iter((False, True))

    async def notify(_task, _run):
        return next(outcomes)

    async def get_task(task_id):
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(completion, "notify_completion", notify)
    monkeypatch.setattr(completion.Task, "get", staticmethod(get_task))

    assert await completion.deliver_completion_notification(run.id) is False
    pending = await TaskRun.get(run.id)
    assert pending is not None
    assert pending.completion_notification_state == "pending"
    assert pending.completion_notification_next_at
    await TaskRun.update_one(
        {"id": run.id},
        {"completion_notification_next_at": "2000-01-01 00:00:00"},
    )

    delivered = await completion.recover_completion_notifications()
    stored = await TaskRun.get(run.id)
    assert delivered == [run.id]
    assert stored is not None
    assert stored.completion_notification_state == "delivered"
    assert stored.completion_notification_attempts == 2


@pytest.mark.asyncio
async def test_delivery_stops_after_bounded_attempt_budget(
    completion_db,
    monkeypatch,
):
    from cognitrix.tasks import completion

    run = await _terminal_run()
    await TaskRun.update_one(
        {"id": run.id},
        {"completion_notification_attempts": 7},
    )

    async def fail(_task, _run):
        return False

    async def get_task(task_id):
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(completion, "notify_completion", fail)
    monkeypatch.setattr(
        completion.Task,
        "get",
        staticmethod(get_task),
    )

    assert await completion.deliver_completion_notification(run.id) is False
    stored = await TaskRun.get(run.id)
    assert stored is not None
    assert stored.completion_notification_attempts == 8
    assert stored.completion_notification_state == "failed"
    assert stored.completion_notification_next_at is None
    assert await completion.recover_completion_notifications() == []


@pytest.mark.asyncio
async def test_task_projection_retries_when_same_run_transitions_mid_write(
    monkeypatch,
):
    from cognitrix.tasks import completion

    queued = TaskRun(
        _id="run-race",
        task_id="task-race",
        status=TaskRunStatus.QUEUED,
        version=1,
    )
    completed = queued.model_copy(
        update={"status": TaskRunStatus.COMPLETED, "version": 2}
    )
    current = [queued]
    writes = []

    class Repository:
        async def latest_run(self, _task_id):
            return current[0].model_copy(deep=True)

    async def update_task(_query, patch):
        writes.append(patch["status"])
        if len(writes) == 1:
            current[0] = completed
        return 1

    monkeypatch.setattr(completion.Task, "update_one", staticmethod(update_task))

    status = await completion.project_task_status(
        "task-race",
        repository=Repository(),
    )

    assert status.value == "completed"
    assert writes == ["in_progress", "completed"]


@pytest.mark.asyncio
async def test_recovery_projects_failure_and_delivers_webhook_once(
    completion_db,
    monkeypatch,
):
    from cognitrix.tasks import completion
    from cognitrix.tasks.recovery import run_recovery_pass

    task = Task(
        title="Recovered task",
        description="Times out before pickup",
        status=TaskStatus.IN_PROGRESS,
        callback_url="https://hooks.example.test/done",
        callback_key_id="key-1",
    )
    await task.save()
    repository = RunRepository()
    run = await repository.create_queued(
        task_id=task.id,
        callback_url=task.callback_url,
        callback_key_id=task.callback_key_id,
    )
    await TaskRun.update_one(
        {"id": run.id},
        {"queued_at": "2000-01-01 00:00:00"},
    )
    delivered = []

    async def notify(callback_task, terminal):
        delivered.append((callback_task.id, terminal.id, terminal.status))
        return True

    monkeypatch.setattr(completion, "notify_completion", notify)

    _outboxes, recovered = await run_recovery_pass(
        repository=repository,
        queue_timeout_seconds=1,
    )
    # A repeated pass neither terminalizes nor delivers the same run again.
    await run_recovery_pass(repository=repository, queue_timeout_seconds=1)

    stored_task = await Task.get(task.id)
    stored_run = await TaskRun.get(run.id)
    assert [item.id for item in recovered] == [run.id]
    assert stored_task is not None and stored_task.status == TaskStatus.FAILED
    assert stored_run is not None
    assert stored_run.completion_notification_state == "delivered"
    assert delivered == [(task.id, run.id, TaskRunStatus.FAILED)]
