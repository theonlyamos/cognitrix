"""RED contracts for durable task-run lease recovery."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.repository import LeaseClaim, LeaseLost, RunRepository
from cognitrix.tasks.run import RUN_TIMESTAMP_FORMAT, TaskRun, TaskRunHead, TaskRunStatus


NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(tzinfo=None).strftime(
        RUN_TIMESTAMP_FORMAT
    )


@pytest.fixture
async def repository_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / "task-run-recovery.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    for model in (TaskRun, TaskRunHead, TaskRunEvent):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


async def _set_run(run_id: str, **updates) -> TaskRun:
    run = await TaskRun.get(run_id)
    assert run is not None
    updates["version"] = run.version + 1
    changed = await TaskRun.update_one(
        {"id": run_id, "version": run.version},
        updates,
    )
    assert changed == 1
    stored = await TaskRun.get(run_id)
    assert stored is not None
    return stored


async def _events(run_id: str) -> list[TaskRunEvent]:
    events = await TaskRunEvent.find({"run_id": run_id})
    return sorted(events, key=lambda event: event.sequence)


@pytest.mark.asyncio
async def test_heartbeat_renews_current_lease_and_rejects_stale_claim(repository_db):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-heartbeat")
    claim = await repo.claim(created.id, owner="worker-a", lease_seconds=60)
    assert claim is not None

    renewed = await repo.heartbeat(created.id, claim=claim, lease_seconds=120)

    assert renewed.status == TaskRunStatus.RUNNING
    assert renewed.lease_owner == claim.owner
    assert renewed.lease_generation == claim.generation
    assert renewed.heartbeat_at
    assert renewed.lease_expires_at > renewed.heartbeat_at

    stale = LeaseClaim(
        run_id=created.id,
        owner=claim.owner,
        generation=claim.generation + 1,
    )
    with pytest.raises(LeaseLost):
        await repo.heartbeat(created.id, claim=stale)


@pytest.mark.asyncio
async def test_heartbeat_uses_database_clock_under_worker_skew(
    repository_db,
    monkeypatch,
):
    """Renewal eligibility and timestamps share the database clock."""
    import cognitrix.tasks.repository as repository_module

    repo = RunRepository()
    created = await repo.create_queued(task_id="task-skewed-heartbeat")
    claim = await repo.claim(created.id, owner="worker-a", lease_seconds=60)
    assert claim is not None
    actual_before = datetime.now(timezone.utc).replace(microsecond=0)

    class SkewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            skewed = datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)
            return skewed if tz is not None else skewed.replace(tzinfo=None)

    monkeypatch.setattr(repository_module, "datetime", SkewedDatetime)
    renewed = await repo.heartbeat(created.id, claim=claim, lease_seconds=90)

    actual_after = datetime.now(timezone.utc).replace(microsecond=0)
    heartbeat_at = datetime.strptime(
        renewed.heartbeat_at, RUN_TIMESTAMP_FORMAT
    ).replace(tzinfo=timezone.utc)
    lease_expires_at = datetime.strptime(
        renewed.lease_expires_at, RUN_TIMESTAMP_FORMAT
    ).replace(tzinfo=timezone.utc)
    tolerance = timedelta(seconds=2)
    assert actual_before - tolerance <= heartbeat_at <= actual_after + tolerance
    assert lease_expires_at - heartbeat_at == timedelta(seconds=90)


@pytest.mark.asyncio
async def test_recovery_leaves_healthy_lease_untouched(
    repository_db,
    monkeypatch,
):
    from cognitrix.tasks.recovery import recover_stale_runs

    repo = RunRepository()
    created = await repo.create_queued(task_id="task-healthy")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None
    healthy = await _set_run(
        created.id,
        heartbeat_at=_timestamp(NOW),
        lease_expires_at=_timestamp(NOW + timedelta(minutes=5)),
    )

    async def forbid_full_history_scan():
        raise AssertionError("run recovery must query only active statuses")

    monkeypatch.setattr(TaskRun, "all", forbid_full_history_scan)

    recovered = await recover_stale_runs(repository=repo, now=NOW)

    assert recovered == []
    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.status == TaskRunStatus.RUNNING
    assert stored.version == healthy.version
    assert stored.lease_generation == claim.generation
    head = await TaskRunHead.get(created.task_id)
    assert head is not None and head.active_run_id == created.id
    assert await _events(created.id) == []


@pytest.mark.asyncio
async def test_recovery_terminal_cas_uses_database_clock_for_live_lease(
    repository_db,
):
    from cognitrix.tasks.recovery import recover_stale_runs

    repo = RunRepository()
    created = await repo.create_queued(task_id="task-db-clock-lease")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None
    database_now = datetime.now(timezone.utc)
    live_until = database_now + timedelta(hours=1)
    await _set_run(
        created.id,
        heartbeat_at=_timestamp(database_now),
        lease_expires_at=_timestamp(live_until),
    )

    recovered = await recover_stale_runs(
        repository=repo,
        now=live_until + timedelta(days=365),
    )

    assert recovered == []
    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.status == TaskRunStatus.RUNNING
    assert stored.lease_generation == claim.generation


@pytest.mark.asyncio
async def test_recovery_terminal_cas_uses_database_clock_for_queue_timeout(
    repository_db,
):
    from cognitrix.tasks.recovery import recover_stale_runs

    repo = RunRepository()
    created = await repo.create_queued(task_id="task-db-clock-queue")
    database_now = datetime.now(timezone.utc)
    await _set_run(created.id, queued_at=_timestamp(database_now))

    recovered = await recover_stale_runs(
        repository=repo,
        now=database_now + timedelta(days=365),
        queue_timeout_seconds=60,
    )

    assert recovered == []
    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.status == TaskRunStatus.QUEUED


@pytest.mark.parametrize(
    "stale_status",
    [TaskRunStatus.RUNNING, TaskRunStatus.CANCELLING],
)
@pytest.mark.asyncio
async def test_recovery_fails_expired_lease_and_fences_late_worker(
    repository_db,
    stale_status,
):
    from cognitrix.tasks.recovery import recover_stale_runs

    repo = RunRepository()
    created = await repo.create_queued(task_id=f"task-expired-{stale_status.value}")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None
    database_now = datetime.now(timezone.utc)
    await _set_run(
        created.id,
        status=stale_status.value,
        cancel_requested_at=None,
        heartbeat_at=_timestamp(database_now - timedelta(minutes=2)),
        lease_expires_at=_timestamp(database_now - timedelta(minutes=1)),
    )

    first = await recover_stale_runs(repository=repo, now=NOW)
    second = await recover_stale_runs(repository=repo, now=NOW)

    assert [run.id for run in first] == [created.id]
    assert second == []
    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.status == TaskRunStatus.FAILED
    assert stored.error_code == "worker_lost"
    assert stored.completed_at
    assert stored.lease_generation == claim.generation + 1
    with pytest.raises(LeaseLost):
        await repo.heartbeat(created.id, claim=claim)

    events = await _events(created.id)
    assert [(event.kind, event.data) for event in events] == [
        ("run_status", {"status": "failed", "error_code": "worker_lost"})
    ]
    head = await TaskRunHead.get(created.task_id)
    assert head is not None and head.active_run_id is None


@pytest.mark.asyncio
async def test_recovery_fails_stranded_queued_run_with_queue_timeout(repository_db):
    from cognitrix.tasks.recovery import recover_stale_runs

    repo = RunRepository()
    created = await repo.create_queued(task_id="task-queue-timeout")
    database_now = datetime.now(timezone.utc)
    await _set_run(
        created.id,
        queued_at=_timestamp(database_now - timedelta(minutes=10)),
    )

    recovered = await recover_stale_runs(
        repository=repo,
        now=NOW,
        queue_timeout_seconds=60,
    )

    assert [run.id for run in recovered] == [created.id]
    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.status == TaskRunStatus.FAILED
    assert stored.error_code == "queue_timeout"
    assert await repo.claim(created.id, owner="late-worker") is None
    events = await _events(created.id)
    assert [(event.kind, event.data) for event in events] == [
        ("run_status", {"status": "failed", "error_code": "queue_timeout"})
    ]
    head = await TaskRunHead.get(created.task_id)
    assert head is not None and head.active_run_id is None


@pytest.mark.asyncio
async def test_recovery_releases_only_expired_missing_run_reservations(
    repository_db,
    monkeypatch,
):
    repo = RunRepository()
    await repo._reserve_head("task-stale-reservation", "missing-run")
    fresh_now = datetime.now(timezone.utc)

    async def forbid_full_head_scan():
        raise AssertionError("reservation recovery must query only active heads")

    monkeypatch.setattr(TaskRunHead, "all", forbid_full_head_scan)

    before_expiry = await repo.recover_stale_reservations(
        now=fresh_now,
        reservation_timeout_seconds=300,
    )
    still_reserved = await TaskRunHead.get("task-stale-reservation")

    after_expiry = await repo.recover_stale_reservations(
        now=fresh_now + timedelta(seconds=301),
        reservation_timeout_seconds=300,
    )
    released = await TaskRunHead.get("task-stale-reservation")

    assert before_expiry == []
    assert still_reserved is not None
    assert still_reserved.active_run_id == "missing-run"
    assert after_expiry == ["missing-run"]
    assert released is not None
    assert released.active_run_id is None


@pytest.mark.asyncio
async def test_requested_cancellation_wins_over_worker_lost(repository_db):
    from cognitrix.tasks.recovery import recover_stale_runs

    repo = RunRepository()
    created = await repo.create_queued(task_id="task-cancel-wins")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None
    requested = await repo.request_cancel(created.id)
    database_now = datetime.now(timezone.utc)
    await _set_run(
        created.id,
        lease_expires_at=_timestamp(database_now - timedelta(minutes=1)),
    )

    recovered = await recover_stale_runs(repository=repo, now=NOW)

    assert requested.cancel_requested_at
    assert [run.id for run in recovered] == [created.id]
    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.status == TaskRunStatus.CANCELLED
    assert stored.error_code == "cancelled"
    assert stored.error == "cancelled by user"
    assert stored.lease_generation == claim.generation + 1
    with pytest.raises(LeaseLost):
        await repo.heartbeat(created.id, claim=claim)
    events = await _events(created.id)
    assert [(event.kind, event.data) for event in events] == [
        ("run_status", {"status": "cancelling"}),
        ("run_status", {"status": "cancelled", "error_code": "cancelled"}),
    ]


@pytest.mark.asyncio
async def test_lease_controller_heartbeats_for_async_context_lifetime():
    from cognitrix.tasks.recovery import LeaseController

    calls = []
    repeated = asyncio.Event()
    claim = LeaseClaim(run_id="run-1", owner="worker-a", generation=1)

    with pytest.raises(ValueError, match="heartbeat_interval"):
        LeaseController(claim, heartbeat_interval=0)

    class RecordingRepository:
        async def heartbeat(self, run_id, *, claim, lease_seconds):
            calls.append((run_id, claim, lease_seconds))
            if len(calls) >= 2:
                repeated.set()
            return None

    controller = LeaseController(
        claim,
        repository=RecordingRepository(),
        lease_seconds=30,
        heartbeat_interval=0.01,
    )

    async with controller:
        await asyncio.wait_for(repeated.wait(), timeout=1)

    assert len(calls) >= 2
    assert all(call == (claim.run_id, claim, 30) for call in calls)


def test_lease_controller_default_heartbeat_is_ten_seconds_and_below_lease():
    from cognitrix.tasks.recovery import LeaseController

    claim = LeaseClaim(run_id="run-1", owner="worker-a", generation=1)

    assert LeaseController(claim).heartbeat_interval == 10
    assert LeaseController(claim, lease_seconds=6).heartbeat_interval == 2
    with pytest.raises(ValueError, match="less than lease_seconds"):
        LeaseController(
            claim,
            lease_seconds=5,
            heartbeat_interval=5,
        )


@pytest.mark.asyncio
async def test_lease_controller_signals_background_lease_loss_immediately():
    from cognitrix.tasks.recovery import LeaseController

    calls = 0
    claim = LeaseClaim(run_id="run-1", owner="worker-a", generation=1)

    class FailingRepository:
        async def heartbeat(self, run_id, *, claim, lease_seconds):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise LeaseLost("worker lease was fenced")
            return None

    controller = LeaseController(
        claim,
        repository=FailingRepository(),
        heartbeat_interval=0.01,
    )

    with pytest.raises(LeaseLost, match="worker lease was fenced"):
        async with controller:
            await asyncio.wait_for(controller.wait_failed(), timeout=1)

    with pytest.raises(LeaseLost, match="worker lease was fenced"):
        controller.checkpoint()


@pytest.mark.asyncio
async def test_lease_controller_treats_same_claim_terminalization_as_graceful(
    monkeypatch,
):
    """A worker's own terminal CAS may race its final heartbeat."""
    from cognitrix.tasks.recovery import LeaseController

    calls = 0
    claim = LeaseClaim(run_id="run-1", owner="worker-a", generation=3)
    terminal = TaskRun(
        _id=claim.run_id,
        task_id="task-1",
        status=TaskRunStatus.COMPLETED,
        lease_owner=claim.owner,
        lease_generation=claim.generation,
    )

    class TerminalRaceRepository:
        async def heartbeat(self, run_id, *, claim, lease_seconds):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise LeaseLost("run is already terminal")
            return None

    async def get_terminal(_run_id):
        return terminal

    monkeypatch.setattr(TaskRun, "get", staticmethod(get_terminal))
    controller = LeaseController(
        claim,
        repository=TerminalRaceRepository(),
        heartbeat_interval=0.01,
    )

    async with controller:
        await asyncio.wait_for(controller._stop.wait(), timeout=1)

    controller.checkpoint()
