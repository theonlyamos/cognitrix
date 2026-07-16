"""RED contracts for durable task-run creation, claims, and event outboxes."""

import asyncio
import importlib
from datetime import datetime, timedelta, timezone

import pytest

from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus


def _repository_api():
    """Import inside each test so the missing Task 2 module is a RED failure,
    not a collection error that hides the remaining suite.
    """
    module = importlib.import_module("cognitrix.tasks.repository")
    return (
        module.RunRepository,
        module.LeaseClaim,
        module.ActiveRunExists,
        module.LeaseLost,
    )


@pytest.fixture
async def repository_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / "task-run-repository.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    for model in (TaskRun, TaskRunHead, TaskRunEvent):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


@pytest.mark.asyncio
async def test_create_queued_persists_nonsecret_run_snapshot(repository_db):
    RunRepository, _, _, _ = _repository_api()
    repo = RunRepository()

    created = await repo.create_queued(
        task_id="task-1",
        requested_by="user-1",
        actor_key="jwt:user-1",
        budget={"max_parallel_steps": 2},
    )

    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.status == TaskRunStatus.QUEUED
    assert stored.queued_at
    assert stored.requested_by == "user-1"
    assert stored.actor_key == "jwt:user-1"
    assert stored.budget == {"max_parallel_steps": 2}
    assert stored.queue_job_id is None
    assert stored.lease_owner is None
    assert stored.lease_generation == 0


@pytest.mark.asyncio
async def test_simultaneous_create_queued_allows_one_active_run(repository_db):
    RunRepository, _, ActiveRunExists, _ = _repository_api()

    results = await asyncio.gather(
        RunRepository().create_queued(task_id="task-1"),
        RunRepository().create_queued(task_id="task-1"),
        return_exceptions=True,
    )

    created = [item for item in results if isinstance(item, TaskRun)]
    rejected = [item for item in results if isinstance(item, ActiveRunExists)]
    stored = await TaskRun.find({"task_id": "task-1"})
    assert len(created) == 1
    assert len(rejected) == 1
    assert len(stored) == 1
    assert stored[0].status == TaskRunStatus.QUEUED


@pytest.mark.asyncio
async def test_inflight_head_reservation_cannot_be_stolen_before_run_insert(
    repository_db,
    monkeypatch,
):
    """A missing run row can mean its creator is between two durable writes."""
    repository_module = importlib.import_module("cognitrix.tasks.repository")
    RunRepository, _, ActiveRunExists, _ = _repository_api()
    original_insert = repository_module._insert_with_explicit_id
    first_insert_started = asyncio.Event()
    release_first_insert = asyncio.Event()
    blocked_once = False

    async def interleaved_insert(model, instance):
        nonlocal blocked_once
        if model is TaskRun and not blocked_once:
            blocked_once = True
            first_insert_started.set()
            await release_first_insert.wait()
        return await original_insert(model, instance)

    monkeypatch.setattr(
        repository_module,
        "_insert_with_explicit_id",
        interleaved_insert,
    )

    first = asyncio.create_task(
        RunRepository().create_queued(task_id="task-reservation-race")
    )
    await asyncio.wait_for(first_insert_started.wait(), timeout=1)
    try:
        second = await RunRepository().create_queued(
            task_id="task-reservation-race"
        )
    except Exception as exc:  # asserted after releasing the blocked creator
        second = exc
    finally:
        release_first_insert.set()

    created = await asyncio.wait_for(first, timeout=1)
    assert isinstance(second, ActiveRunExists)
    head = await TaskRunHead.get("task-reservation-race")
    assert head is not None
    assert head.latest_run_id == created.id
    assert head.active_run_id == created.id
    assert [run.id for run in await TaskRun.find(
        {"task_id": "task-reservation-race"}
    )] == [created.id]


@pytest.mark.asyncio
async def test_claim_is_atomic_and_can_succeed_only_once(repository_db):
    RunRepository, _, _, _ = _repository_api()
    created = await RunRepository().create_queued(task_id="task-1")

    claims = await asyncio.gather(
        RunRepository().claim(created.id, owner="worker-a", lease_seconds=60),
        RunRepository().claim(created.id, owner="worker-b", lease_seconds=60),
    )

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    claim = winners[0]
    assert claim.run_id == created.id
    assert claim.owner in {"worker-a", "worker-b"}
    assert claim.generation == 1

    stored = await TaskRun.get(created.id)
    assert stored.status == TaskRunStatus.RUNNING
    assert stored.lease_owner == claim.owner
    assert stored.lease_generation == claim.generation
    assert stored.heartbeat_at
    assert stored.lease_expires_at


@pytest.mark.asyncio
async def test_queue_and_claim_timestamps_use_database_clock_under_worker_skew(
    repository_db,
    monkeypatch,
):
    """A worker clock must not manufacture queue or lease timestamps."""
    repository_module = importlib.import_module("cognitrix.tasks.repository")
    RunRepository, _, _, _ = _repository_api()
    actual_before = datetime.now(timezone.utc).replace(microsecond=0)

    class SkewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            skewed = datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)
            return skewed if tz is not None else skewed.replace(tzinfo=None)

    monkeypatch.setattr(repository_module, "datetime", SkewedDatetime)
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-db-clock-claim")
    claim = await repository.claim(
        created.id,
        owner="skewed-worker",
        lease_seconds=75,
    )
    assert claim is not None

    actual_after = datetime.now(timezone.utc).replace(microsecond=0)
    stored = await TaskRun.get(created.id)
    assert stored is not None
    queued_at = datetime.strptime(stored.queued_at, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    heartbeat_at = datetime.strptime(
        stored.heartbeat_at, "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)
    lease_expires_at = datetime.strptime(
        stored.lease_expires_at, "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)

    tolerance = timedelta(seconds=2)
    assert actual_before - tolerance <= queued_at <= actual_after + tolerance
    assert actual_before - tolerance <= heartbeat_at <= actual_after + tolerance
    assert lease_expires_at - heartbeat_at == timedelta(seconds=75)
    assert stored.started_at == stored.heartbeat_at


@pytest.mark.asyncio
async def test_running_mutation_requires_current_lease_generation(repository_db):
    RunRepository, LeaseClaim, _, LeaseLost = _repository_api()
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-1")
    claim = await repo.claim(created.id, owner="worker-a", lease_seconds=60)
    assert claim is not None

    await repo.mutate(
        created.id,
        claim=claim,
        updates={"usage": {"llm_calls": 1}},
        expected_statuses={TaskRunStatus.RUNNING},
    )

    invalid_claims = (
        LeaseClaim(run_id=created.id, owner="worker-b", generation=claim.generation),
        LeaseClaim(run_id=created.id, owner=claim.owner, generation=claim.generation + 1),
        None,
    )
    for invalid in invalid_claims:
        with pytest.raises(LeaseLost):
            await repo.mutate(
                created.id,
                claim=invalid,
                updates={"usage": {"llm_calls": 99}},
                expected_statuses={TaskRunStatus.RUNNING},
            )

    stored = await TaskRun.get(created.id)
    assert stored.usage == {"llm_calls": 1}


@pytest.mark.asyncio
async def test_concurrent_emitters_allocate_unique_monotonic_sequences(repository_db):
    RunRepository, _, _, _ = _repository_api()
    owner = RunRepository()
    created = await owner.create_queued(task_id="task-1")
    claim = await owner.claim(created.id, owner="worker-a", lease_seconds=60)
    assert claim is not None

    events = await asyncio.gather(*[
        RunRepository().emit_event(
            created.id,
            claim=claim,
            kind="step_status",
            step_index=index,
            data={"status": "running"},
        )
        for index in range(12)
    ])

    assert sorted(event.sequence for event in events) == list(range(1, 13))
    stored_events = await TaskRunEvent.find({"run_id": created.id})
    assert sorted(event.sequence for event in stored_events) == list(range(1, 13))
    stored_run = await TaskRun.get(created.id)
    assert stored_run.next_event_sequence == 12


@pytest.mark.asyncio
async def test_outbox_flush_recovers_idempotently_after_insert_before_ack(
    repository_db,
    monkeypatch,
):
    RunRepository, _, _, _ = _repository_api()
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-1")

    async def leave_for_recovery(*args, **kwargs):
        return []

    monkeypatch.setattr(repo, "flush_outbox", leave_for_recovery)
    await repo.mutate(
        created.id,
        claim=None,
        updates={},
        expected_statuses={TaskRunStatus.QUEUED},
        event={"kind": "run_status", "data": {"status": "queued"}},
    )

    stranded = await TaskRun.get(created.id)
    assert len(stranded.event_outbox) == 1
    envelope = stranded.event_outbox[0]
    assert envelope["run_id"] == created.id
    assert envelope["sequence"] == 1

    # Simulate process death after the event insert but before the outbox head
    # was acknowledged. Recovery must recognize the existing unique event as
    # delivered and remove the head without inserting a duplicate.
    await TaskRunEvent(**envelope).save()
    recovered = RunRepository()
    await recovered.flush_outbox(created.id)
    await recovered.flush_outbox(created.id)

    stored_events = await TaskRunEvent.find({"run_id": created.id})
    stored_run = await TaskRun.get(created.id)
    assert [(event.run_id, event.sequence) for event in stored_events] == [
        (created.id, 1)
    ]
    assert stored_run.event_outbox == []


@pytest.mark.asyncio
async def test_queued_cancel_prevents_late_worker_claim(repository_db):
    RunRepository, _, _, _ = _repository_api()
    created = await RunRepository().create_queued(task_id="task-1")

    cancelled = await RunRepository().cancel_queued(created.id)
    late_claim = await RunRepository().claim(
        created.id,
        owner="late-worker",
        lease_seconds=60,
    )

    assert cancelled is not None
    assert cancelled.status == TaskRunStatus.CANCELLED
    assert cancelled.completed_at
    assert late_claim is None
    stored = await TaskRun.get(created.id)
    assert stored.status == TaskRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_queue_job_id_can_be_attached_after_worker_claim(repository_db):
    """A fast worker may claim between broker publish and result-id storage.

    Queue metadata attachment is a narrowly scoped CAS operation and must not
    mutate or require ownership of the worker's execution lease.
    """
    RunRepository, _, _, _ = _repository_api()
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-1")
    claim = await repo.claim(created.id, owner="fast-worker")
    assert claim is not None

    attached = await repo.attach_queue_job_id(created.id, "celery-1")

    assert attached.queue_job_id == "celery-1"
    assert attached.status == TaskRunStatus.RUNNING
    assert attached.lease_owner == "fast-worker"


@pytest.mark.asyncio
async def test_active_run_reservation_is_durable_and_released(repository_db):
    RunRepository, _, _, _ = _repository_api()
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-1")

    reserved = await TaskRunHead.get("task-1")
    assert reserved is not None
    assert reserved.active_run_id == created.id
    assert reserved.latest_run_id == created.id

    await repo.cancel_queued(created.id)

    released = await TaskRunHead.get("task-1")
    assert released is not None
    assert released.active_run_id is None
    assert released.latest_run_id == created.id
