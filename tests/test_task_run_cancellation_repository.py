"""Durable repository contracts for requested and forced cancellation."""

from datetime import datetime, timedelta, timezone

import pytest

from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.repository import LeaseLost, RunRepository
from cognitrix.tasks.run import (
    RUN_TIMESTAMP_FORMAT,
    TaskRun,
    TaskRunHead,
    TaskRunStatus,
)


@pytest.fixture
async def repository_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / "task-run-cancellation.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    for model in (TaskRun, TaskRunHead, TaskRunEvent):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


async def _status_events(run_id: str) -> list[TaskRunEvent]:
    events = await TaskRunEvent.find({"run_id": run_id})
    return sorted(events, key=lambda event: event.sequence)


def test_force_cancel_ready_is_side_effect_free_and_uses_durable_timestamps():
    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)

    def timestamp(value: datetime) -> str:
        return value.replace(tzinfo=None).strftime(RUN_TIMESTAMP_FORMAT)

    repository = RunRepository()
    cancelling = TaskRun(
        task_id="task-ready",
        status=TaskRunStatus.CANCELLING,
        cancel_requested_at=timestamp(now - timedelta(seconds=11)),
        lease_expires_at=timestamp(now + timedelta(minutes=1)),
    )
    waiting = cancelling.model_copy(
        update={"cancel_requested_at": timestamp(now - timedelta(seconds=9))}
    )

    assert repository.force_cancel_ready(cancelling, now=now) is True
    assert repository.force_cancel_ready(waiting, now=now) is False
    assert repository.force_cancel_ready(
        waiting.model_copy(
            update={"lease_expires_at": timestamp(now - timedelta(seconds=1))}
        ),
        now=now,
    ) is True
    assert repository.force_cancel_ready(
        cancelling.model_copy(update={"status": TaskRunStatus.RUNNING}),
        now=now,
    ) is False


@pytest.mark.asyncio
async def test_request_cancel_queued_terminalizes_once_and_releases_head(
    repository_db,
):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-queued-cancel")

    cancelled = await repo.request_cancel(created.id)
    repeated = await repo.request_cancel(created.id)

    assert cancelled.status == TaskRunStatus.CANCELLED
    assert cancelled.error == "cancelled by user"
    assert cancelled.cancel_requested_at
    assert cancelled.completed_at == cancelled.cancel_requested_at
    assert repeated.version == cancelled.version
    assert await repo.claim(created.id, owner="late-worker") is None

    events = await _status_events(created.id)
    assert [
        (event.sequence, event.kind, event.data)
        for event in events
    ] == [(1, "run_status", {"status": "cancelled"})]
    head = await TaskRunHead.get(created.task_id)
    assert head is not None and head.active_run_id is None


@pytest.mark.asyncio
async def test_request_cancel_running_preserves_the_worker_lease(repository_db):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-running-cancel")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None

    requested = await repo.request_cancel(created.id)
    repeated = await repo.request_cancel(created.id)

    assert requested.status == TaskRunStatus.CANCELLING
    assert requested.cancel_requested_at
    assert requested.completed_at is None
    assert requested.lease_owner == claim.owner
    assert requested.lease_generation == claim.generation
    assert repeated.version == requested.version

    # Cooperative cancellation keeps the current worker authoritative long
    # enough to checkpoint/finalize the run as cancelled.
    updated = await repo.mutate(
        created.id,
        claim=claim,
        updates={"usage": {"checkpoints": 1}},
        expected_statuses={TaskRunStatus.CANCELLING},
    )
    assert updated.usage == {"checkpoints": 1}

    events = await _status_events(created.id)
    assert [(event.kind, event.data) for event in events] == [
        ("run_status", {"status": "cancelling"})
    ]
    head = await TaskRunHead.get(created.task_id)
    assert head is not None and head.active_run_id == created.id


@pytest.mark.asyncio
async def test_request_cancel_accepts_legacy_running_row_without_lease_owner(
    repository_db,
):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-legacy-running")
    changed = await TaskRun.update_one(
        {"id": created.id, "version": created.version},
        {
            "status": TaskRunStatus.RUNNING.value,
            "version": created.version + 1,
        },
    )
    assert changed == 1

    requested = await repo.request_cancel(created.id)

    assert requested.status == TaskRunStatus.CANCELLING
    assert requested.lease_owner is None
    assert requested.lease_generation == 0
    events = await _status_events(created.id)
    assert [(event.kind, event.data) for event in events] == [
        ("run_status", {"status": "cancelling"})
    ]


@pytest.mark.asyncio
async def test_force_cancel_fences_worker_emits_once_and_releases_head(repository_db):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-force-cancel")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None
    requested = await repo.request_cancel(created.id)

    rapid_repeat = await repo.force_cancel(created.id)
    assert rapid_repeat.status == TaskRunStatus.CANCELLING
    assert rapid_repeat.version == requested.version
    assert [(event.kind, event.data) for event in await _status_events(created.id)] == [
        ("run_status", {"status": "cancelling"})
    ]

    grace_elapsed = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(
        tzinfo=None
    ).strftime(RUN_TIMESTAMP_FORMAT)
    await TaskRun.update_one(
        {"id": created.id},
        {"cancel_requested_at": grace_elapsed},
    )

    cancelled = await repo.force_cancel(created.id)
    repeated = await repo.force_cancel(created.id)

    assert cancelled.status == TaskRunStatus.CANCELLED
    assert cancelled.error == "force-cancelled (worker did not respond)"
    assert cancelled.cancel_requested_at
    assert cancelled.completed_at
    assert cancelled.lease_owner == claim.owner
    assert cancelled.lease_generation == claim.generation + 1
    assert repeated.version == cancelled.version

    with pytest.raises(LeaseLost):
        await repo.mutate(
            created.id,
            claim=claim,
            updates={"result": "late worker result"},
            expected_statuses=None,
        )

    events = await _status_events(created.id)
    assert [(event.sequence, event.kind, event.data) for event in events] == [
        (1, "run_status", {"status": "cancelling"}),
        (2, "run_status", {"status": "cancelled"}),
    ]
    head = await TaskRunHead.get(created.task_id)
    assert head is not None and head.active_run_id is None


@pytest.mark.asyncio
async def test_force_cancel_is_allowed_when_worker_lease_already_expired(repository_db):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-expired-cancel")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None
    requested = await repo.request_cancel(created.id)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).replace(
        tzinfo=None
    ).strftime(RUN_TIMESTAMP_FORMAT)
    await TaskRun.update_one(
        {"id": created.id},
        {"lease_expires_at": expired},
    )

    cancelled = await repo.force_cancel(created.id)

    assert requested.status == TaskRunStatus.CANCELLING
    assert cancelled.status == TaskRunStatus.CANCELLED
    assert cancelled.lease_generation == claim.generation + 1


@pytest.mark.asyncio
async def test_force_cancel_losing_cas_preserves_terminal_winner_and_repairs_head(
    repository_db,
    monkeypatch,
):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-terminal-race")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None
    await repo.request_cancel(created.id)
    grace_elapsed = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(
        tzinfo=None
    ).strftime(RUN_TIMESTAMP_FORMAT)
    await TaskRun.update_one(
        {"id": created.id},
        {"cancel_requested_at": grace_elapsed},
    )

    original_update = TaskRun.update_one
    completion_won = False

    async def complete_before_cancel(query, values):
        nonlocal completion_won
        if values.get("status") == TaskRunStatus.CANCELLED.value and not completion_won:
            completion_won = True
            current = await TaskRun.get(created.id)
            assert current is not None
            changed = await original_update(
                {
                    "id": created.id,
                    "status": TaskRunStatus.CANCELLING.value,
                    "version": current.version,
                },
                {
                    "status": TaskRunStatus.COMPLETED.value,
                    "result": "authoritative worker result",
                    "completed_at": "2030-01-01 00:00:00",
                    "version": current.version + 1,
                },
            )
            assert changed == 1
            return 0
        return await original_update(query, values)

    monkeypatch.setattr(TaskRun, "update_one", staticmethod(complete_before_cancel))

    authoritative = await repo.force_cancel(created.id)

    assert authoritative.status == TaskRunStatus.COMPLETED
    assert authoritative.result == "authoritative worker result"
    assert authoritative.error is None
    assert authoritative.lease_generation == claim.generation
    events = await _status_events(created.id)
    assert [(event.kind, event.data) for event in events] == [
        ("run_status", {"status": "cancelling"})
    ]
    head = await TaskRunHead.get(created.task_id)
    assert head is not None and head.active_run_id is None
