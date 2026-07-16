"""Task tombstone contracts for durable run history and queue admission."""

import asyncio

from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from cognitrix.common.security import AuthContext
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.repository import RunRepository, _insert_with_explicit_id
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus


@pytest.fixture
async def task_delete_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    database = str(tmp_path / "task-delete.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=database)
    else:
        DBMS.initialize("sqlite", database=database)
    _patch_odbms_sqlite()
    for model in (Task, TaskRun, TaskRunHead, TaskRunEvent):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


def _ctx() -> AuthContext:
    return AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)


async def test_delete_rejects_task_with_active_run(task_delete_db):
    import cognitrix.api.routes.tasks as routes

    task = Task(title="active", description="keep it")
    await task.save()
    run = await RunRepository().create_queued(
        task_id=task.id,
        requested_by="user-1",
    )

    with pytest.raises(HTTPException) as exc:
        await routes.delete_task(task.id)

    assert exc.value.status_code == 409
    assert await TaskRun.get(run.id) is not None
    stored = await Task.get(task.id)
    assert stored is not None
    assert stored.deleted_at is None


async def test_delete_reconciles_legacy_active_run_before_tombstoning(
    task_delete_db,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(title="legacy active", description="missing head")
    await task.save()
    legacy = TaskRun(
        _id="legacy-active-run",
        task_id=task.id,
        status=TaskRunStatus.RUNNING,
        requested_by="user-1",
    )
    legacy.id = "legacy-active-run"
    await _insert_with_explicit_id(TaskRun, legacy)
    assert await TaskRunHead.get(task.id) is None

    with pytest.raises(HTTPException) as exc:
        await routes.delete_task(task.id)

    assert exc.value.status_code == 409
    stored = await Task.get(task.id)
    assert stored is not None
    assert stored.deleted_at is None
    head = await TaskRunHead.get(task.id)
    assert head is not None
    assert head.active_run_id == legacy.id
    assert head.deleted_at is None


async def test_delete_tombstones_task_but_preserves_terminal_run_history(
    task_delete_db,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(
        title="finished",
        description="retain history",
        autostart=True,
        schedule_interval=300,
        schedule_enabled=True,
        next_run_at="2030-01-01 00:05:00",
    )
    await task.save()
    run = await RunRepository().create_queued(
        task_id=task.id,
        requested_by="user-1",
    )
    run = await RunRepository().request_cancel(run.id)
    assert run.status == TaskRunStatus.CANCELLED

    response = await routes.delete_task(task.id)

    assert response == {"message": "Task deleted successfully"}
    stored = await Task.get(task.id)
    assert stored is not None
    assert stored.deleted_at
    assert stored.autostart is False
    assert stored.schedule_enabled is False
    assert stored.next_run_at is None
    assert await TaskRun.get(run.id) is not None
    authorized_task, authorized_run = await routes._authorized_task_run(
        task.id,
        run.id,
        _ctx(),
    )
    assert authorized_task.id == task.id
    assert authorized_run.id == run.id
    assert await routes.load_task(task.id) == {}
    assert await routes.list_tasks() == []


async def test_tombstoned_task_cannot_be_edited_assigned_scheduled_or_started(
    task_delete_db,
    monkeypatch,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(title="deleted", description="hidden")
    await task.save()
    await routes.delete_task(task.id)
    stored = await Task.get(task.id)
    assert stored is not None

    replacement = Task(
        _id=task.id,
        title="resurrected",
        description="must stay deleted",
    )
    blocked_calls = (
        lambda: routes.save_task(None, replacement, BackgroundTasks(), _ctx()),
        lambda: routes.assign_task(
            task.id,
            routes.TaskAssignment(assigned_agents=["agent-1"]),
            _ctx(),
        ),
        lambda: routes.toggle_schedule(
            task.id,
            routes.ScheduleToggle(enabled=False),
            _ctx(),
        ),
        lambda: routes.update_task_status(None, task.id, False, _ctx()),
        lambda: routes.start_task_run(task.id, None, _ctx()),
        lambda: routes.cancel_task(task.id, _ctx()),
    )
    for call in blocked_calls:
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404

    monkeypatch.setattr(routes, "ensure_local_worker", lambda: True)
    monkeypatch.setattr(routes, "broker_available", lambda: True)
    with pytest.raises(HTTPException) as exc:
        await routes._enqueue_task_start(stored)
    assert exc.value.status_code == 404

    unchanged = await Task.get(task.id)
    assert unchanged is not None
    assert unchanged.deleted_at == stored.deleted_at
    assert unchanged.title == "deleted"


async def test_delete_winning_after_save_read_cannot_be_resurrected(
    task_delete_db,
    monkeypatch,
):
    """A save based on a stale live read must lose to a committed delete."""
    import cognitrix.api.routes.tasks as routes

    task = Task(
        title="original",
        description="keep the tombstone",
        schedule_interval=300,
        schedule_enabled=True,
        next_run_at="2030-01-01 00:05:00",
        callback_url="https://hooks.example.test/task",
        callback_key_id="key-1",
    )
    await task.save()

    save_read_live = asyncio.Event()
    allow_save_to_continue = asyncio.Event()
    original_get = Task.get
    get_calls = 0

    async def pause_after_save_read(task_id):
        nonlocal get_calls
        stored = await original_get(task_id)
        get_calls += 1
        if get_calls == 1:
            save_read_live.set()
            await allow_save_to_continue.wait()
        return stored

    monkeypatch.setattr(Task, "get", staticmethod(pause_after_save_read))
    replacement = Task(
        id=task.id,
        title="stale edit",
        description="must not resurface",
        autostart=False,
    )
    saving = asyncio.create_task(
        routes.save_task(None, replacement, BackgroundTasks(), _ctx())
    )
    await save_read_live.wait()

    deleted = await routes.delete_task(task.id)
    assert deleted == {"message": "Task deleted successfully"}
    allow_save_to_continue.set()

    with pytest.raises(HTTPException) as exc:
        await saving
    assert exc.value.status_code == 404

    stored = await original_get(task.id)
    assert stored is not None
    assert stored.deleted_at
    assert stored.title == "original"
    assert stored.autostart is False
    assert stored.schedule_enabled is False
    assert stored.next_run_at is None
    assert stored.callback_url == "https://hooks.example.test/task"
    assert stored.callback_key_id == "key-1"


async def test_enqueue_tombstone_race_cancels_reservation_before_publish(
    task_delete_db,
    monkeypatch,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(title="race", description="delete before publish")
    await task.save()
    published = []
    original_create = routes.RunRepository.create_queued

    async def create_then_delete(self, **kwargs):
        queued = await original_create(self, **kwargs)
        await Task.update_one(
            {"id": task.id},
            {
                "deleted_at": "2030-01-01 00:00:00",
                "autostart": False,
                "schedule_enabled": False,
                "next_run_at": None,
            },
        )
        return queued

    def publish(*args, **kwargs):
        published.append((args, kwargs))
        return SimpleNamespace(id="must-not-publish")

    monkeypatch.setattr(routes, "ensure_local_worker", lambda: True)
    monkeypatch.setattr(routes, "broker_available", lambda: True)
    monkeypatch.setattr(routes.RunRepository, "create_queued", create_then_delete)
    monkeypatch.setattr(routes.run_task, "apply_async", publish)

    with pytest.raises(HTTPException) as exc:
        await routes._enqueue_task_start(task)

    assert exc.value.status_code == 404
    assert published == []
    runs = await TaskRun.find({"task_id": task.id})
    assert len(runs) == 1
    assert runs[0].status == TaskRunStatus.CANCELLED
    assert runs[0].error == "task deleted before queue publication"
    head = await TaskRunHead.get(task.id)
    assert head is not None
    assert head.latest_run_id == runs[0].id
    assert head.active_run_id is None
    stored = await Task.get(task.id)
    assert stored is not None
    assert stored.deleted_at == "2030-01-01 00:00:00"

    repeated = await routes.delete_task(task.id)
    assert repeated == {"message": "Task deleted successfully"}
    assert (await Task.get(task.id)).deleted_at == "2030-01-01 00:00:00"
    assert stored.status == TaskStatus.PENDING


async def test_head_tombstone_and_run_reservation_are_one_atomic_admission_fence(
    task_delete_db,
    monkeypatch,
):
    """A delete that reads an idle head cannot tombstone after enqueue wins CAS."""
    import cognitrix.api.routes.tasks as routes

    assert hasattr(RunRepository, "tombstone_task")
    task = Task(title="head race", description="serialize admission")
    await task.save()
    head = TaskRunHead(task_id=task.id, version=1)
    head.id = task.id
    await _insert_with_explicit_id(TaskRunHead, head)

    delete_cas_started = asyncio.Event()
    reservation_won = asyncio.Event()
    original_update = TaskRunHead.update_one

    async def interleaved_update(conditions, data):
        if data.get("deleted_at"):
            delete_cas_started.set()
            await reservation_won.wait()
        changed = await original_update(conditions, data)
        if data.get("active_run_id"):
            reservation_won.set()
        return changed

    monkeypatch.setattr(
        TaskRunHead,
        "update_one",
        staticmethod(interleaved_update),
    )
    monkeypatch.setattr(routes, "ensure_local_worker", lambda: True)
    monkeypatch.setattr(routes, "broker_available", lambda: True)
    monkeypatch.setattr(
        routes.run_task,
        "apply_async",
        lambda *args, **kwargs: SimpleNamespace(id="celery-race"),
    )

    deleting = asyncio.create_task(routes.delete_task(task.id))
    await delete_cas_started.wait()
    enqueueing = asyncio.create_task(routes._enqueue_task_start(task))
    deleted, queued = await asyncio.gather(
        deleting,
        enqueueing,
        return_exceptions=True,
    )

    assert isinstance(deleted, HTTPException)
    assert deleted.status_code == 409
    assert not isinstance(queued, BaseException)
    stored_head = await TaskRunHead.get(task.id)
    assert stored_head is not None
    assert stored_head.deleted_at is None
    assert stored_head.active_run_id == queued.id
    stored_task = await Task.get(task.id)
    assert stored_task is not None
    assert stored_task.deleted_at is None


async def test_head_tombstone_blocks_enqueue_and_delete_repairs_task_row(
    task_delete_db,
    monkeypatch,
):
    import cognitrix.api.routes.tasks as routes

    assert hasattr(RunRepository, "tombstone_task")
    task = Task(title="repair", description="head committed first")
    await task.save()
    tombstoned = await RunRepository().tombstone_task(
        task.id,
        deleted_at="2030-01-01 00:00:00",
    )
    assert tombstoned.deleted_at == "2030-01-01 00:00:00"
    assert (await Task.get(task.id)).deleted_at is None

    monkeypatch.setattr(routes, "ensure_local_worker", lambda: True)
    monkeypatch.setattr(routes, "broker_available", lambda: True)
    with pytest.raises(HTTPException) as exc:
        await routes._enqueue_task_start(task)
    assert exc.value.status_code == 404
    assert await TaskRun.find({"task_id": task.id}) == []

    response = await routes.delete_task(task.id)
    assert response == {"message": "Task deleted successfully"}
    stored = await Task.get(task.id)
    assert stored is not None
    assert stored.deleted_at == "2030-01-01 00:00:00"


def test_task_schema_migration_includes_tombstone_column():
    from cognitrix.config import (
        _TASK_MIGRATION_COLUMNS,
        _TASKRUN_HEAD_MIGRATION_COLUMNS,
    )

    assert ("deleted_at", "TEXT") in _TASK_MIGRATION_COLUMNS
    assert ("deleted_at", "TEXT") in _TASKRUN_HEAD_MIGRATION_COLUMNS
