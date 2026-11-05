"""Regression contracts for durable run ownership and recovery."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.repository import (
    ActiveRunExists,
    LeaseClaim,
    LeaseLost,
    RunRepository,
    RunStateConflict,
)
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus


EXPIRED_LEASE = datetime(2000, 1, 1, tzinfo=timezone.utc).replace(
    tzinfo=None
).strftime("%Y-%m-%d %H:%M:%S")


@pytest.mark.parametrize(
    ("dbms", "now_sql", "expiry_sql"),
    [
        (
            "sqlite",
            "STRFTIME('%Y-%m-%d %H:%M:%S', 'NOW')",
            "'+' || :lease_seconds || ' seconds'",
        ),
        (
            "postgresql",
            "CURRENT_TIMESTAMP AT TIME ZONE 'UTC'",
            "%(lease_seconds)s * INTERVAL '1 second'",
        ),
        (
            "mysql",
            "DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')",
            "TIMESTAMPADD(SECOND, %(lease_seconds)s, UTC_TIMESTAMP())",
        ),
    ],
)
@pytest.mark.asyncio
async def test_relational_claim_sets_lease_from_database_clock_in_one_update(
    monkeypatch,
    dbms,
    now_sql,
    expiry_sql,
):
    from odbms import DBMS

    statements = []

    class Cursor:
        rowcount = 1

    class RecordingDatabase:
        async def query(self, statement, params=None):
            statements.append((statement, params or {}))
            return Cursor()

    database = RecordingDatabase()
    database.dbms = dbms
    monkeypatch.setattr(DBMS, "Database", database)
    queued = TaskRun(
        _id="run-db-clock-claim",
        task_id="task-db-clock-claim",
        status=TaskRunStatus.QUEUED,
        version=7,
        lease_generation=3,
    )

    async def get_queued(_run_id):
        return queued

    monkeypatch.setattr(TaskRun, "get", staticmethod(get_queued))

    async def forbid_model_update(*_args, **_kwargs):
        raise AssertionError("relational claim must use one explicit SQL UPDATE")

    monkeypatch.setattr(TaskRun, "update_one", staticmethod(forbid_model_update))

    claim = await RunRepository().claim(
        queued.id,
        owner="worker-a",
        lease_seconds=47,
    )

    assert claim is not None and claim.generation == 4
    assert len(statements) == 1
    statement, params = statements[0]
    assert statement.startswith("UPDATE taskruns SET ")
    assert now_sql in statement
    assert expiry_sql in statement
    assert params["lease_seconds"] == 47
    assert "set_heartbeat_at" not in params
    assert "set_lease_expires_at" not in params


@pytest.mark.parametrize(
    ("dbms", "now_sql", "expiry_sql"),
    [
        (
            "sqlite",
            "STRFTIME('%Y-%m-%d %H:%M:%S', 'NOW')",
            "'+' || :lease_seconds || ' seconds'",
        ),
        (
            "postgresql",
            "CURRENT_TIMESTAMP AT TIME ZONE 'UTC'",
            "%(lease_seconds)s * INTERVAL '1 second'",
        ),
        (
            "mysql",
            "DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')",
            "TIMESTAMPADD(SECOND, %(lease_seconds)s, UTC_TIMESTAMP())",
        ),
    ],
)
@pytest.mark.asyncio
async def test_relational_heartbeat_renews_from_database_clock_in_one_update(
    monkeypatch,
    dbms,
    now_sql,
    expiry_sql,
):
    from odbms import DBMS

    statements = []

    class Cursor:
        rowcount = 1

    class RecordingDatabase:
        async def query(self, statement, params=None):
            statements.append((statement, params or {}))
            return Cursor()

    database = RecordingDatabase()
    database.dbms = dbms
    monkeypatch.setattr(DBMS, "Database", database)
    running = TaskRun(
        _id="run-db-clock-heartbeat",
        task_id="task-db-clock-heartbeat",
        status=TaskRunStatus.RUNNING,
        version=7,
        lease_owner="worker-a",
        lease_generation=3,
        lease_expires_at="2999-01-01 00:00:00",
    )

    async def get_running(_run_id):
        return running

    monkeypatch.setattr(TaskRun, "get", staticmethod(get_running))
    claim = LeaseClaim(
        run_id=running.id,
        owner="worker-a",
        generation=3,
    )

    await RunRepository().heartbeat(
        running.id,
        claim=claim,
        lease_seconds=53,
    )

    assert len(statements) == 1
    statement, params = statements[0]
    assert statement.startswith("UPDATE taskruns SET ")
    assert now_sql in statement
    assert expiry_sql in statement
    assert params["lease_seconds"] == 53
    assert "set_heartbeat_at" not in params
    assert "set_lease_expires_at" not in params


@pytest.mark.asyncio
async def test_repository_schema_setup_runs_once_per_database_instance(monkeypatch):
    from odbms import DBMS

    class RecordingDatabase:
        dbms = "sqlite"

        def __init__(self):
            self.statements: list[str] = []

        async def query(self, statement, _params=None):
            self.statements.append(statement)
            await asyncio.sleep(0)

    first_database = RecordingDatabase()
    monkeypatch.setattr(DBMS, "Database", first_database)

    await asyncio.gather(
        *(RunRepository()._ensure_indexes() for _ in range(8))
    )

    assert len(first_database.statements) == 4
    assert sum("DROP TRIGGER" in sql for sql in first_database.statements) == 1

    second_database = RecordingDatabase()
    monkeypatch.setattr(DBMS, "Database", second_database)
    await RunRepository()._ensure_indexes()

    assert len(second_database.statements) == 4


@pytest.fixture
async def repository_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / "task-run-repository-regressions.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    for model in (TaskRun, TaskRunHead, TaskRunEvent):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


@pytest.mark.asyncio
async def test_relational_writes_defer_lease_expiry_to_database_clock(
    repository_db,
    monkeypatch,
):
    import cognitrix.tasks.repository as repository_module

    repository = RunRepository()
    created = await repository.create_queued(task_id="task-db-clock-writes")
    claim = await repository.claim(
        created.id,
        owner="fast-clock-worker",
        lease_seconds=120,
    )
    assert claim is not None

    class SkewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            skewed = datetime(2099, 1, 1, tzinfo=timezone.utc)
            return skewed if tz is not None else skewed.replace(tzinfo=None)

    monkeypatch.setattr(repository_module, "datetime", SkewedDatetime)

    usage = await repository.persist_usage(
        created.id,
        claim=claim,
        snapshot={"total_tokens": 3},
    )
    writable = await repository._require_step_write(created.id, claim)
    event = await repository.emit_event(
        created.id,
        claim=claim,
        kind="step_status",
        data={"status": "running"},
    )

    assert usage.usage["total_tokens"] == 3
    assert writable.id == created.id
    assert event.run_id == created.id


@pytest.mark.asyncio
async def test_terminal_run_rejects_mutation_from_its_former_lease(repository_db):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-terminal")
    claim = await repo.claim(created.id, owner="worker-a")
    assert claim is not None

    await repo.mutate(
        created.id,
        claim=claim,
        updates={"status": TaskRunStatus.COMPLETED.value, "completed_at": "now"},
        expected_statuses={TaskRunStatus.RUNNING},
    )

    with pytest.raises((LeaseLost, RunStateConflict)):
        await repo.mutate(
            created.id,
            claim=claim,
            updates={"status": TaskRunStatus.FAILED.value, "error": "late write"},
            expected_statuses=None,
        )

    stored = await TaskRun.get(created.id)
    assert stored.status == TaskRunStatus.COMPLETED
    assert stored.error is None


@pytest.mark.asyncio
async def test_terminal_run_preserves_generation_fence_for_late_worker(repository_db):
    repo = RunRepository()
    created = await repo.create_queued(task_id="task-fenced")
    stale_claim = await repo.claim(created.id, owner="worker-a")
    assert stale_claim is not None

    running = await TaskRun.get(created.id)
    changed = await TaskRun.update_one(
        {"id": created.id, "version": running.version},
        {
            "status": TaskRunStatus.FAILED.value,
            "error_code": "worker_lost",
            "lease_owner": "recovery",
            "lease_generation": stale_claim.generation + 1,
            "version": running.version + 1,
        },
    )
    assert changed == 1

    with pytest.raises((LeaseLost, RunStateConflict)):
        await repo.emit_event(
            created.id,
            claim=stale_claim,
            kind="step_status",
            data={"status": "done"},
        )

    stored = await TaskRun.get(created.id)
    assert stored.next_event_sequence == 0
    assert stored.event_outbox == []
    assert await TaskRunEvent.find({"run_id": created.id}) == []


@pytest.mark.asyncio
async def test_worker_mutation_rejects_immutable_run_fields(repository_db):
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-immutable-run")
    claim = await repository.claim(created.id, owner="worker-a")
    assert claim is not None

    immutable_updates = {
        "task_id": "other-task",
        "requested_by": "other-user",
        "actor_key": "other-actor",
        "authority_kind": "api_key",
        "authority_id": "other-authority",
        "acl_version": 99,
        "acl_team_id": "other-team",
        "acl_agent_ids": ["other-agent"],
        "callback_url": "https://attacker.invalid/callback",
        "callback_key_id": "other-key",
        "resume_from_run_id": "other-run",
        "queue_job_id": "other-job",
        "budget": {"max_cost_usd": "999"},
        "lease_owner": "other-worker",
        "lease_generation": claim.generation + 1,
        "lease_expires_at": "2999-01-01 00:00:00",
        "completion_notification_state": "delivered",
    }
    for field, value in immutable_updates.items():
        with pytest.raises(ValueError, match="Immutable task-run fields"):
            await repository.mutate(
                created.id,
                claim=claim,
                updates={field: value},
                expected_statuses={TaskRunStatus.RUNNING},
            )

    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.task_id == created.task_id
    assert stored.lease_owner == claim.owner
    assert stored.lease_generation == claim.generation


@pytest.mark.asyncio
async def test_expired_unrecovered_lease_rejects_usage_write(repository_db):
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-expired-usage")
    claim = await repository.claim(created.id, owner="worker-a")
    assert claim is not None
    await TaskRun.update_one(
        {"id": created.id},
        {"lease_expires_at": EXPIRED_LEASE},
    )

    with pytest.raises(LeaseLost):
        await repository.persist_usage(
            created.id,
            claim=claim,
            snapshot={"total_tokens": 1},
        )

    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.usage == {}


@pytest.mark.asyncio
async def test_expired_unrecovered_lease_rejects_run_mutation(repository_db):
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-expired-mutate")
    claim = await repository.claim(created.id, owner="worker-a")
    assert claim is not None
    await TaskRun.update_one(
        {"id": created.id},
        {"lease_expires_at": EXPIRED_LEASE},
    )

    with pytest.raises(LeaseLost):
        await repository.mutate(
            created.id,
            claim=claim,
            updates={"result": "late worker result"},
            expected_statuses={TaskRunStatus.RUNNING},
        )

    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.result is None


@pytest.mark.asyncio
async def test_expired_unrecovered_lease_rejects_event_write(repository_db):
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-expired-event")
    claim = await repository.claim(created.id, owner="worker-a")
    assert claim is not None
    await TaskRun.update_one(
        {"id": created.id},
        {"lease_expires_at": EXPIRED_LEASE},
    )

    with pytest.raises(LeaseLost):
        await repository.emit_event(
            created.id,
            claim=claim,
            kind="step_status",
            data={"status": "done"},
        )

    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert stored.next_event_sequence == 0
    assert stored.event_outbox == []
    assert await TaskRunEvent.find({"run_id": created.id}) == []


@pytest.mark.asyncio
async def test_terminal_mutation_survives_outbox_delivery_failure(
    repository_db,
    monkeypatch,
):
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-terminal-outbox")
    claim = await repository.claim(created.id, owner="worker-a")
    assert claim is not None

    async def fail_delivery(_run_id):
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(repository, "flush_outbox", fail_delivery)

    completed = await repository.mutate(
        created.id,
        claim=claim,
        updates={"status": TaskRunStatus.COMPLETED.value},
        expected_statuses={TaskRunStatus.RUNNING},
        event={"kind": "run_status", "data": {"status": "completed"}},
    )

    head = await TaskRunHead.get(created.task_id)
    assert completed.status == TaskRunStatus.COMPLETED
    assert head is not None and head.active_run_id is None
    assert len(completed.event_outbox) == 1
    assert await TaskRunEvent.find({"run_id": created.id}) == []


@pytest.mark.asyncio
async def test_terminal_recovery_survives_outbox_delivery_failure(
    repository_db,
    monkeypatch,
):
    repository = RunRepository()
    observed = await repository.create_queued(task_id="task-recovery-outbox")

    async def fail_delivery(_run_id):
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(repository, "flush_outbox", fail_delivery)

    recovered = await repository.recover_terminal(
        observed,
        status=TaskRunStatus.FAILED,
        error_code="queue_timeout",
        error="queue timed out",
        completed_at="2030-01-01 00:00:00",
    )

    head = await TaskRunHead.get(observed.task_id)
    assert recovered is not None and recovered.status == TaskRunStatus.FAILED
    assert head is not None and head.active_run_id is None
    assert len(recovered.event_outbox) == 1
    assert await TaskRunEvent.find({"run_id": observed.id}) == []


@pytest.mark.asyncio
async def test_terminal_cancel_survives_outbox_delivery_failure(
    repository_db,
    monkeypatch,
):
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-cancel-outbox")

    async def fail_delivery(_run_id):
        raise RuntimeError("event store unavailable")

    monkeypatch.setattr(repository, "flush_outbox", fail_delivery)

    cancelled = await repository.request_cancel(created.id)

    head = await TaskRunHead.get(created.task_id)
    assert cancelled.status == TaskRunStatus.CANCELLED
    assert head is not None and head.active_run_id is None
    assert len(cancelled.event_outbox) == 1
    assert await TaskRunEvent.find({"run_id": created.id}) == []


@pytest.mark.asyncio
async def test_recover_outboxes_scans_all_runs_after_process_restart(
    repository_db,
    monkeypatch,
):
    writer = RunRepository()
    created = await writer.create_queued(task_id="task-stranded-outbox")

    async def simulate_crash_before_flush(*args, **kwargs):
        return []

    monkeypatch.setattr(writer, "flush_outbox", simulate_crash_before_flush)
    await writer.mutate(
        created.id,
        claim=None,
        updates={},
        expected_statuses={TaskRunStatus.QUEUED},
        event={"kind": "run_status", "data": {"status": "queued"}},
    )

    async def forbid_full_history_scan():
        raise AssertionError("outbox recovery must not load every TaskRun")

    monkeypatch.setattr(TaskRun, "all", forbid_full_history_scan)

    recovered = RunRepository()
    recover_outboxes = getattr(recovered, "recover_outboxes", None)
    assert recover_outboxes is not None, (
        "startup recovery needs a repository-wide outbox scan"
    )
    await recover_outboxes()
    await recover_outboxes()

    stored = await TaskRun.get(created.id)
    events = await TaskRunEvent.find({"run_id": created.id})
    assert stored.event_outbox == []
    assert [(event.run_id, event.sequence) for event in events] == [
        (created.id, 1)
    ]


@pytest.mark.asyncio
async def test_recover_outboxes_isolates_a_poison_run(monkeypatch):
    repository = RunRepository()
    candidates = [
        type("Run", (), {"id": "poison", "event_outbox": [{"sequence": 1}]})(),
        type("Run", (), {"id": "healthy", "event_outbox": [{"sequence": 1}]})(),
    ]
    attempted = []

    async def outbox_candidates():
        return candidates

    async def flush(run_id):
        attempted.append(run_id)
        if run_id == "poison":
            raise RuntimeError("invalid event envelope")
        return []

    monkeypatch.setattr(repository, "_outbox_candidates", outbox_candidates)
    monkeypatch.setattr(repository, "flush_outbox", flush)

    recovered = await repository.recover_outboxes()

    assert attempted == ["poison", "healthy"]
    assert recovered == ["healthy"]


@pytest.mark.asyncio
async def test_persist_usage_cannot_move_backwards_after_a_concurrent_stale_read(
    repository_db,
    monkeypatch,
):
    repository = RunRepository()
    created = await repository.create_queued(task_id="task-usage-race")
    claim = await repository.claim(created.id, owner="worker-a")
    assert claim is not None

    older = {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
        "llm_calls": 1,
        "tool_calls": 1,
        "tool_attempts": 1,
        "retries": 0,
        "steps": 1,
        "cost_usd": "0.10",
    }
    newer = {
        "prompt_tokens": 8,
        "completion_tokens": 7,
        "total_tokens": 15,
        "llm_calls": 3,
        "tool_calls": 4,
        "tool_attempts": 6,
        "retries": 2,
        "steps": 2,
        "cost_usd": "0.2500",
    }

    original_fenced_update = repository._fenced_run_update
    older_update_ready = asyncio.Event()
    release_older_update = asyncio.Event()
    paused_older_once = False

    async def coordinate_usage_update(run, *, claim, patch):
        nonlocal paused_older_once
        usage = patch.get("usage")
        if (
            usage
            and usage.get("total_tokens") == older["total_tokens"]
            and not paused_older_once
        ):
            paused_older_once = True
            older_update_ready.set()
            await release_older_update.wait()
        return await original_fenced_update(run, claim=claim, patch=patch)

    monkeypatch.setattr(
        repository,
        "_fenced_run_update",
        coordinate_usage_update,
    )

    stale_writer = asyncio.create_task(
        repository.persist_usage(created.id, claim=claim, snapshot=older)
    )
    await asyncio.wait_for(older_update_ready.wait(), timeout=1)
    await repository.persist_usage(created.id, claim=claim, snapshot=newer)
    release_older_update.set()
    await stale_writer

    stored = await TaskRun.get(created.id)
    assert stored is not None
    assert {
        key: stored.usage[key]
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "llm_calls",
            "tool_calls",
            "tool_attempts",
            "retries",
            "steps",
        )
    } == {key: newer[key] for key in newer if key != "cost_usd"}
    assert Decimal(stored.usage["cost_usd"]) == Decimal("0.2500")

    await repository.mutate(
        created.id,
        claim=claim,
        updates={"status": TaskRunStatus.COMPLETED.value},
        expected_statuses={TaskRunStatus.RUNNING},
    )
    with pytest.raises(LeaseLost):
        await repository.persist_usage(
            created.id,
            claim=claim,
            snapshot={**newer, "total_tokens": 99},
        )


@pytest.mark.asyncio
async def test_active_run_uniqueness_survives_independent_event_loops_without_partial_index(
    repository_db,
    monkeypatch,
):
    """Model a backend where a partial unique index is unavailable.

    Each worker owns an independent event loop, so repository correctness may
    not depend on the module's loop-local asyncio lock.
    """

    async def no_optional_indexes(self):
        return None

    monkeypatch.setattr(RunRepository, "_ensure_indexes", no_optional_indexes)

    original_find = TaskRun.find
    reads_complete = threading.Barrier(2, timeout=5)
    worker_state = threading.local()

    async def synchronized_find(query):
        rows = await original_find(query)
        if (
            query == {"task_id": "task-cross-loop"}
            and not getattr(worker_state, "synchronized", False)
        ):
            worker_state.synchronized = True
            await asyncio.to_thread(reads_complete.wait)
        return rows

    monkeypatch.setattr(TaskRun, "find", staticmethod(synchronized_find))

    def create_from_worker():
        return asyncio.run(
            RunRepository().create_queued(task_id="task-cross-loop")
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create_from_worker) for _ in range(2)]
        results = []
        for future in futures:
            try:
                results.append(future.result(timeout=10))
            except Exception as exc:  # preserve both worker outcomes for assertions
                results.append(exc)

    created = [item for item in results if isinstance(item, TaskRun)]
    rejected = [item for item in results if isinstance(item, ActiveRunExists)]
    stored = await original_find({"task_id": "task-cross-loop"})

    assert len(created) == 1
    assert len(rejected) == 1
    assert len(stored) == 1
