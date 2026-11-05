"""Upgrade contracts for rebuilding TaskRunHead from legacy TaskRun rows."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import cognitrix.tasks.repository as repository_module
from cognitrix.tasks.repository import (
    ActiveRunExists,
    RunRepository,
    TaskRunHeadInvariantError,
    _insert_with_explicit_id,
)
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus


@pytest.fixture
async def repository_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / "task-run-head-reconciliation.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    for model in (TaskRun, TaskRunHead):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


async def _legacy_run(
    run_id: str,
    task_id: str,
    status: TaskRunStatus,
    *,
    created_at: datetime,
) -> TaskRun:
    run = TaskRun(
        _id=run_id,
        task_id=task_id,
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )
    run.id = run_id
    await _insert_with_explicit_id(TaskRun, run)
    return run


@pytest.mark.asyncio
async def test_missing_head_is_seeded_from_existing_active_run_and_enqueue_rejects(
    repository_db,
):
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    active = await _legacy_run(
        "legacy-active",
        "task-active",
        TaskRunStatus.RUNNING,
        created_at=created_at,
    )

    head = await RunRepository().reconcile_task_head("task-active")

    assert head is not None
    assert head.latest_run_id == active.id
    assert head.active_run_id == active.id
    with pytest.raises(ActiveRunExists):
        await RunRepository().create_queued(task_id="task-active")
    assert [run.id for run in await TaskRun.find({"task_id": "task-active"})] == [
        active.id
    ]


@pytest.mark.asyncio
async def test_terminal_history_seeds_latest_projection_without_an_active_run(
    repository_db,
):
    await _legacy_run(
        "run-old",
        "task-terminal",
        TaskRunStatus.FAILED,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    newest = await _legacy_run(
        "run-new",
        "task-terminal",
        TaskRunStatus.COMPLETED,
        created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )

    head = await RunRepository().reconcile_task_head("task-terminal")

    assert head is not None
    assert head.latest_run_id == newest.id
    assert head.active_run_id is None
    assert (await RunRepository().latest_run("task-terminal")).id == newest.id


@pytest.mark.asyncio
async def test_duplicate_legacy_active_runs_fail_deterministically(repository_db):
    await _legacy_run(
        "run-older",
        "task-corrupt",
        TaskRunStatus.RUNNING,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    await _legacy_run(
        "run-newer",
        "task-corrupt",
        TaskRunStatus.QUEUED,
        created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )

    with pytest.raises(TaskRunHeadInvariantError) as first:
        await RunRepository().reconcile_task_head("task-corrupt")
    with pytest.raises(TaskRunHeadInvariantError) as second:
        await RunRepository().create_queued(task_id="task-corrupt")

    assert str(first.value) == str(second.value)
    assert "run-newer, run-older" in str(first.value)
    assert await TaskRunHead.get("task-corrupt") is None
    assert len(await TaskRun.find({"task_id": "task-corrupt"})) == 2


@pytest.mark.asyncio
async def test_head_reconciliation_is_idempotent(repository_db):
    newest = await _legacy_run(
        "run-only",
        "task-idempotent",
        TaskRunStatus.COMPLETED,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    repository = RunRepository()

    first = await repository.reconcile_task_head("task-idempotent")
    second = await repository.reconcile_task_head("task-idempotent")

    assert first is not None and second is not None
    assert first.latest_run_id == second.latest_run_id == newest.id
    assert first.version == second.version


@pytest.mark.asyncio
async def test_concurrent_startup_reconciliation_and_enqueue_serialize_on_sqlite(
    repository_db,
):
    await _legacy_run(
        "run-terminal",
        "task-race",
        TaskRunStatus.COMPLETED,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

    reconciled, queued = await asyncio.gather(
        RunRepository().reconcile_heads(batch_size=1),
        RunRepository().create_queued(task_id="task-race"),
    )

    assert reconciled >= 1
    active = [
        run
        for run in await TaskRun.find({"task_id": "task-race"})
        if run.status
        in {
            TaskRunStatus.QUEUED,
            TaskRunStatus.RUNNING,
            TaskRunStatus.CANCELLING,
        }
    ]
    head = await TaskRunHead.get("task-race")
    assert [run.id for run in active] == [queued.id]
    assert head is not None
    assert head.latest_run_id == queued.id
    assert head.active_run_id == queued.id


@pytest.mark.parametrize("dbms", ["sqlite", "postgresql", "mysql"])
@pytest.mark.asyncio
async def test_repository_wide_reconciliation_uses_bounded_keyset_pages(
    monkeypatch,
    dbms,
):
    from odbms import DBMS

    class Database:
        pass

    database = Database()
    database.dbms = dbms
    monkeypatch.setattr(DBMS, "Database", database)
    calls: list[tuple[str, dict]] = []

    async def relational_records(_database, statement, params=None):
        calls.append((statement, dict(params or {})))
        return ([{"task_id": "task-a"}, {"task_id": "task-b"}]
                if len(calls) == 1 else [])

    reconciled: list[str] = []

    async def reconcile_task_head(task_id):
        reconciled.append(task_id)
        return None

    monkeypatch.setattr(repository_module, "_relational_records", relational_records)
    repository = RunRepository()
    monkeypatch.setattr(repository, "reconcile_task_head", reconcile_task_head)

    count = await repository.reconcile_heads(batch_size=2)

    marker = ":after_task" if dbms == "sqlite" else "%(after_task)s"
    assert count == 2
    assert reconciled == ["task-a", "task-b"]
    assert len(calls) == 2
    assert marker in calls[0][0]
    assert "ORDER BY task_id" in calls[0][0]
    assert "LIMIT 2" in calls[0][0]
    assert "OFFSET" not in calls[0][0].upper()
    assert calls[0][1] == {"after_task": ""}
    assert calls[1][1] == {"after_task": "task-b"}


@pytest.mark.parametrize("batch_size", [True, 0, -1, 1.5, "2"])
@pytest.mark.asyncio
async def test_repository_wide_reconciliation_requires_integer_batch_size(
    repository_db,
    batch_size,
):
    with pytest.raises(ValueError, match="positive integer"):
        await RunRepository().reconcile_heads(batch_size=batch_size)


@pytest.mark.asyncio
async def test_initialize_database_reconciles_heads_before_outboxes(monkeypatch):
    from odbms import DBMS

    import cognitrix.config as config

    calls: list[str] = []

    async def initialize_async(*_args, **_kwargs):
        calls.append("initialize")

    async def ensure_schema():
        calls.append("schema")

    async def reconcile_heads(self):
        calls.append("heads")
        return 0

    async def recover_outboxes(self):
        calls.append("outboxes")
        return []

    if hasattr(DBMS, "initialize_async"):
        monkeypatch.setattr(DBMS, "initialize_async", initialize_async)
    else:
        monkeypatch.setattr(
            DBMS,
            "initialize",
            lambda *_args, **_kwargs: calls.append("initialize"),
        )
    monkeypatch.setattr(config, "_patch_odbms_sqlite", lambda: None)
    monkeypatch.setattr(config, "_ensure_schema", ensure_schema)
    monkeypatch.setattr(RunRepository, "reconcile_heads", reconcile_heads)
    monkeypatch.setattr(RunRepository, "recover_outboxes", recover_outboxes)

    await config.initialize_database()

    assert calls == ["initialize", "schema", "heads", "outboxes"]


@pytest.mark.asyncio
async def test_initialize_database_skips_head_reconciliation_for_mongodb(monkeypatch):
    from odbms import DBMS

    import cognitrix.config as config

    calls: list[str] = []

    class MongoDatabase:
        dbms = "mongodb"

    monkeypatch.setattr(DBMS, "Database", MongoDatabase())
    if hasattr(DBMS, "initialize_async"):

        async def initialize_async(*_args, **_kwargs):
            calls.append("initialize")

        monkeypatch.setattr(DBMS, "initialize_async", initialize_async)
    else:
        monkeypatch.setattr(
            DBMS,
            "initialize",
            lambda *_args, **_kwargs: calls.append("initialize"),
        )

    async def ensure_schema():
        calls.append("schema")

    async def reconcile_heads(self):
        calls.append("heads")
        return 0

    async def recover_outboxes(self):
        calls.append("outboxes")
        return []

    monkeypatch.setattr(config, "_patch_odbms_sqlite", lambda: None)
    monkeypatch.setattr(config, "_ensure_schema", ensure_schema)
    monkeypatch.setattr(RunRepository, "reconcile_heads", reconcile_heads)
    monkeypatch.setattr(RunRepository, "recover_outboxes", recover_outboxes)

    await config.initialize_database()

    assert calls == ["initialize", "schema", "outboxes"]
