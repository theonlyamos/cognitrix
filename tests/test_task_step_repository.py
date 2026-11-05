"""RED contracts for authoritative task-run steps and compatibility hydration.

Task 3 deliberately exposes a small repository surface:

* ``compile_steps`` inserts one authoritative row per compiled plan entry;
* ``transition_step`` mutates one row and converges ``TaskRun.plan``;
* ``seed_resume_steps`` prefers typed source rows and falls back to legacy plans;
* task-run API projections hydrate from rows but never expose step result bodies.

The tests use real SQLite rows so concurrency and write-scope assertions exercise
the persistence boundary instead of mocks.
"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cognitrix.tasks.repository import (
    LeaseClaim,
    LeaseLost,
    RunRepository,
    RunStateConflict,
)
from cognitrix.tasks.base import Task
from cognitrix.tasks.results import StepResult
from cognitrix.tasks.runtime import AgentRuntimeSnapshot, LLMRuntimeSnapshot
from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus
from cognitrix.tasks.step import TaskRunStep, TaskRunStepStatus


FUTURE_LEASE = datetime(2999, 1, 1, tzinfo=timezone.utc).replace(
    tzinfo=None
).strftime("%Y-%m-%d %H:%M:%S")
EXPIRED_LEASE = datetime(2000, 1, 1, tzinfo=timezone.utc).replace(
    tzinfo=None
).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
async def task_step_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / "task-step-repository.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    for model in (Task, TaskRun, TaskRunHead, TaskRunStep, TaskRunEvent):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


def _plan_entry(
    index: int,
    title: str,
    *,
    dependencies: list[int] | None = None,
    required_tools: list[str] | None = None,
) -> dict:
    return {
        "index": index,
        "title": title,
        "description": f"Do {title.lower()}",
        "expected_output": f"{title} output",
        "verification_criteria": f"{title} is complete",
        "agent_name": "Researcher",
        "dependencies": list(dependencies or []),
        "required_tools": required_tools,
        "status": "pending",
        "attempts": 0,
        "result": None,
        "gate": None,
    }


async def _running_run(
    task_id: str = "task-1",
    *,
    plan: list[dict] | None = None,
) -> tuple[TaskRun, LeaseClaim]:
    run = TaskRun(
        task_id=task_id,
        status=TaskRunStatus.RUNNING,
        plan=list(plan or []),
        lease_owner="worker-a",
        lease_generation=1,
        lease_expires_at=FUTURE_LEASE,
    )
    await run.save()
    return run, LeaseClaim(run_id=run.id, owner="worker-a", generation=1)


async def _steps(run_id: str) -> list[TaskRunStep]:
    rows = await TaskRunStep.find({"run_id": run_id})
    return sorted(rows, key=lambda row: row.step_index)


def _runtime_snapshot(
    *,
    agent_id: str = "agent-1",
    name: str = "Researcher",
) -> AgentRuntimeSnapshot:
    return AgentRuntimeSnapshot(
        agent_id=agent_id,
        name=name,
        system_prompt="Frozen task-step prompt",
        llm=LLMRuntimeSnapshot(provider="openai", model="model-v1"),
    )


async def _save_pending_steps(run: TaskRun, titles: tuple[str, ...]) -> None:
    for index, title in enumerate(titles):
        await TaskRunStep(
            run_id=run.id,
            task_id=run.task_id,
            step_index=index,
            title=title,
            description=f"Do {title.lower()}",
            dependencies=[] if index == 0 else [index - 1],
        ).save()


@pytest.mark.asyncio
async def test_compile_steps_inserts_authoritative_rows_and_compatibility_plan(
    task_step_db,
):
    run, claim = await _running_run()
    plan = [
        _plan_entry(0, "Collect", required_tools=["Search"]),
        _plan_entry(1, "Write", dependencies=[0], required_tools=[]),
    ]

    compiled = await RunRepository().compile_steps(run.id, plan, claim=claim)

    rows = await _steps(run.id)
    stored_run = await TaskRun.get(run.id)
    assert [row.step_index for row in compiled] == [0, 1]
    assert [row.step_index for row in rows] == [0, 1]
    assert [row.task_id for row in rows] == [run.task_id, run.task_id]
    assert rows[0].required_tools == ["Search"]
    assert rows[1].required_tools == []
    assert rows[1].dependencies == [0]
    assert stored_run is not None
    assert stored_run.plan == [row.to_plan_entry() for row in rows]


@pytest.mark.asyncio
async def test_compile_steps_rejects_mismatched_existing_definition(task_step_db):
    run, claim = await _running_run()
    repository = RunRepository()
    original = _plan_entry(0, "Collect", required_tools=["Search"])
    await repository.compile_steps(run.id, [original], claim=claim)

    changed = {
        **original,
        "title": "Collect something else",
        "required_tools": ["Browser"],
    }
    with pytest.raises(RuntimeError, match="definition does not match"):
        await repository.compile_steps(run.id, [changed], claim=claim)

    rows = await _steps(run.id)
    assert len(rows) == 1
    assert rows[0].title == "Collect"
    assert rows[0].required_tools == ["Search"]


@pytest.mark.asyncio
async def test_recovery_fences_step_insert_after_worker_preflight(task_step_db):
    from cognitrix.tasks.recovery import recover_stale_runs

    run, claim = await _running_run()
    repository = RunRepository()
    insert_preflight_complete = asyncio.Event()
    let_worker_continue = asyncio.Event()
    original = repository._require_step_write
    preflights = 0

    async def pause_after_insert_preflight(run_id, claim):
        nonlocal preflights
        current = await original(run_id, claim)
        preflights += 1
        if preflights == 2:
            insert_preflight_complete.set()
            await let_worker_continue.wait()
        return current

    repository._require_step_write = pause_after_insert_preflight
    stale_compiler = asyncio.create_task(
        repository.compile_steps(
            run.id,
            [_plan_entry(0, "Collect")],
            claim=claim,
        )
    )

    await asyncio.wait_for(insert_preflight_complete.wait(), timeout=1)
    await TaskRun.update_one(
        {"id": run.id},
        {"lease_expires_at": EXPIRED_LEASE},
    )
    recovered = await recover_stale_runs(repository=RunRepository())
    let_worker_continue.set()

    with pytest.raises(LeaseLost, match="Lease lost"):
        await stale_compiler
    assert [item.id for item in recovered] == [run.id]
    assert await _steps(run.id) == []


@pytest.mark.asyncio
async def test_transition_step_updates_only_the_target_row(task_step_db):
    from odbms import DBMS

    run, claim = await _running_run()
    await _save_pending_steps(run, ("Collect", "Write"))
    await DBMS.Database.query(
        "CREATE TABLE task_step_update_audit (step_index INTEGER NOT NULL)"
    )
    await DBMS.Database.query(
        f"CREATE TRIGGER audit_task_step_update AFTER UPDATE ON {TaskRunStep.table_name()} "
        "BEGIN INSERT INTO task_step_update_audit (step_index) "
        "VALUES (NEW.step_index); END"
    )

    updated = await RunRepository().transition_step(
        run.id,
        0,
        claim=claim,
        updates={"status": TaskRunStepStatus.RUNNING, "attempts": 1},
        expected_statuses={TaskRunStepStatus.PENDING},
    )

    audit = await DBMS.Database.query(
        "SELECT step_index FROM task_step_update_audit ORDER BY rowid"
    )
    rows = await _steps(run.id)
    assert [row[0] for row in audit.fetchall()] == [0]
    assert updated.status == TaskRunStepStatus.RUNNING
    assert updated.attempts == 1
    assert rows[1].status == TaskRunStepStatus.PENDING
    assert rows[1].attempts == 0


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskRunStepStatus.PENDING, TaskRunStepStatus.DONE),
        (TaskRunStepStatus.DONE, TaskRunStepStatus.RUNNING),
        (TaskRunStepStatus.CANCELLED, TaskRunStepStatus.RUNNING),
    ],
)
@pytest.mark.asyncio
async def test_repository_rejects_illegal_step_status_transitions(
    task_step_db,
    current,
    target,
):
    run, claim = await _running_run()
    await TaskRunStep(
        run_id=run.id,
        task_id=run.task_id,
        step_index=0,
        title="Collect",
        status=current,
    ).save()

    with pytest.raises(RuntimeError, match="Illegal task-run step transition"):
        await RunRepository().transition_step(
            run.id,
            0,
            claim=claim,
            updates={"status": target},
            expected_statuses={current},
        )

    stored = await TaskRunStep.find_one({"run_id": run.id, "step_index": 0})
    assert stored is not None
    assert stored.status == current


@pytest.mark.asyncio
async def test_step_transition_rejects_runtime_definition_rewrites(task_step_db):
    run, claim = await _running_run(task_id="task-immutable-step")
    await _save_pending_steps(run, ("Collect",))
    repository = RunRepository()
    immutable_updates = {
        "title": "Rewritten title",
        "description": "Rewritten instructions",
        "expected_output": "Rewritten output",
        "verification_criteria": "Rewritten criteria",
        "agent_name": "Other agent",
        "dependencies": [99],
        "required_tools": ["Unapproved tool"],
        "runtime_snapshot": _runtime_snapshot(agent_id="other-agent"),
    }

    for field, value in immutable_updates.items():
        with pytest.raises(ValueError, match="Immutable task-run step fields"):
            await repository.transition_step(
                run.id,
                0,
                claim=claim,
                updates={field: value},
                expected_statuses={TaskRunStepStatus.PENDING},
            )

    stored = await TaskRunStep.find_one({"run_id": run.id, "step_index": 0})
    assert stored is not None
    assert stored.title == "Collect"
    assert stored.runtime_snapshot is None


@pytest.mark.asyncio
async def test_runtime_snapshot_backfill_is_one_time_and_idempotent(task_step_db):
    run, claim = await _running_run(task_id="task-runtime-backfill")
    await _save_pending_steps(run, ("Collect",))
    repository = RunRepository()
    snapshot = _runtime_snapshot()

    stored = await repository.backfill_step_runtime(
        run.id,
        0,
        claim=claim,
        agent_name="Researcher",
        runtime_snapshot=snapshot,
    )
    repeated = await repository.backfill_step_runtime(
        run.id,
        0,
        claim=claim,
        agent_name="Researcher",
        runtime_snapshot=snapshot,
    )

    assert stored.runtime_snapshot == snapshot
    assert stored.agent_name == "Researcher"
    assert repeated.id == stored.id

    with pytest.raises(RunStateConflict, match="runtime snapshot"):
        await repository.backfill_step_runtime(
            run.id,
            0,
            claim=claim,
            agent_name="Other agent",
            runtime_snapshot=_runtime_snapshot(
                agent_id="other-agent",
                name="Other agent",
            ),
        )


@pytest.mark.asyncio
async def test_recovery_fences_step_update_after_worker_preflight(task_step_db):
    from cognitrix.tasks.recovery import recover_stale_runs

    run, claim = await _running_run()
    await _save_pending_steps(run, ("Collect",))
    repository = RunRepository()
    preflight_complete = asyncio.Event()
    let_worker_continue = asyncio.Event()
    original = repository._require_step_write

    async def pause_after_preflight(run_id, claim):
        current = await original(run_id, claim)
        if not preflight_complete.is_set():
            preflight_complete.set()
            await let_worker_continue.wait()
        return current

    repository._require_step_write = pause_after_preflight
    stale_worker = asyncio.create_task(
        repository.transition_step(
            run.id,
            0,
            claim=claim,
            updates={"status": TaskRunStepStatus.RUNNING, "attempts": 1},
            expected_statuses={TaskRunStepStatus.PENDING},
        )
    )

    await asyncio.wait_for(preflight_complete.wait(), timeout=1)
    await TaskRun.update_one(
        {"id": run.id},
        {"lease_expires_at": EXPIRED_LEASE},
    )
    recovered = await recover_stale_runs(repository=RunRepository())
    let_worker_continue.set()

    with pytest.raises(LeaseLost, match="Lease lost"):
        await stale_worker

    row = await TaskRunStep.find_one({"run_id": run.id, "step_index": 0})
    assert [recovered_run.id for recovered_run in recovered] == [run.id]
    assert row is not None
    assert row.status == TaskRunStepStatus.PENDING
    assert row.attempts == 0


@pytest.mark.asyncio
async def test_expired_unrecovered_lease_rejects_step_insert(task_step_db):
    run, claim = await _running_run(task_id="task-expired-step-insert")
    await TaskRun.update_one(
        {"id": run.id},
        {"lease_expires_at": EXPIRED_LEASE},
    )

    with pytest.raises(LeaseLost):
        await RunRepository().compile_steps(
            run.id,
            [_plan_entry(0, "Collect")],
            claim=claim,
        )

    assert await TaskRunStep.find({"run_id": run.id}) == []


@pytest.mark.asyncio
async def test_expired_unrecovered_lease_rejects_step_update(task_step_db):
    run, claim = await _running_run(task_id="task-expired-step-update")
    await _save_pending_steps(run, ("Collect",))
    await TaskRun.update_one(
        {"id": run.id},
        {"lease_expires_at": EXPIRED_LEASE},
    )

    with pytest.raises(LeaseLost):
        await RunRepository().transition_step(
            run.id,
            0,
            claim=claim,
            updates={"status": TaskRunStepStatus.RUNNING, "attempts": 1},
            expected_statuses={TaskRunStepStatus.PENDING},
        )

    row = await TaskRunStep.find_one({"run_id": run.id, "step_index": 0})
    assert row is not None
    assert row.status == TaskRunStepStatus.PENDING
    assert row.attempts == 0


@pytest.mark.parametrize("dbms", ["postgresql", "mysql"])
@pytest.mark.asyncio
async def test_server_step_fence_locks_run_row_in_same_statement(monkeypatch, dbms):
    from odbms import DBMS

    statements = []

    class Cursor:
        rowcount = 1

    class RecordingDatabase:
        async def query(self, statement, params):
            statements.append((statement, params))
            return Cursor()

    database = RecordingDatabase()
    database.dbms = dbms
    monkeypatch.setattr(DBMS, "Database", database)
    row = TaskRunStep(
        _id="step-1",
        run_id="run-1",
        task_id="task-1",
        step_index=0,
        title="Collect",
    )
    claim = LeaseClaim(run_id="run-1", owner="worker-a", generation=3)

    changed = await RunRepository()._fenced_step_update(
        row,
        current_status=TaskRunStepStatus.PENDING.value,
        claim=claim,
        patch={"status": TaskRunStepStatus.RUNNING.value},
    )

    statement, params = statements[0]
    assert changed == 1
    assert "FOR UPDATE" in statement
    assert "%(lease_generation)s" in statement
    assert params["lease_generation"] == 3
    if dbms == "postgresql":
        assert "lease_expires_at > TO_CHAR(CURRENT_TIMESTAMP" in statement
    else:
        assert "lease_expires_at > CAST(UTC_TIMESTAMP() AS CHAR)" in statement


@pytest.mark.asyncio
async def test_concurrent_step_completions_keep_rows_authoritative_without_plan_write(
    task_step_db,
):
    run, claim = await _running_run()
    await _save_pending_steps(run, ("Alpha", "Beta"))
    repository = RunRepository()
    for index in (0, 1):
        await repository.transition_step(
            run.id,
            index,
            claim=claim,
            updates={"status": TaskRunStepStatus.RUNNING},
            expected_statuses={TaskRunStepStatus.PENDING},
        )

    alpha, beta = await asyncio.gather(
        RunRepository().transition_step(
            run.id,
            0,
            claim=claim,
            updates={
                "status": TaskRunStepStatus.DONE,
                "attempts": 1,
                "result": StepResult(
                    text="alpha result",
                    structured_data={"branch": "alpha"},
                ),
                "gate": "passed",
            },
            expected_statuses={TaskRunStepStatus.RUNNING},
        ),
        RunRepository().transition_step(
            run.id,
            1,
            claim=claim,
            updates={
                "status": TaskRunStepStatus.DONE,
                "attempts": 2,
                "result": StepResult(
                    text="beta result",
                    structured_data={"branch": "beta"},
                ),
                "gate": "unverified",
            },
            expected_statuses={TaskRunStepStatus.RUNNING},
        ),
    )

    stored_run = await TaskRun.get(run.id)
    rows = await _steps(run.id)
    hydrated = await repository.hydrate_plan(run.id)
    assert alpha.result == StepResult(
        text="alpha result", structured_data={"branch": "alpha"}
    )
    assert beta.result == StepResult(
        text="beta result", structured_data={"branch": "beta"}
    )
    assert [row.status for row in rows] == [
        TaskRunStepStatus.DONE,
        TaskRunStepStatus.DONE,
    ]
    assert stored_run is not None
    # Transition hot paths update only their own step rows. Compatibility
    # readers hydrate from those rows instead of contending on TaskRun.plan.
    assert stored_run.plan == []
    assert [(step["status"], step["result"]) for step in hydrated] == [
        ("done", "alpha result"),
        ("done", "beta result"),
    ]


@pytest.mark.asyncio
async def test_resume_seeding_prefers_authoritative_typed_step_rows(task_step_db):
    stale_source_plan = [
        {**_plan_entry(0, "Collect"), "status": "done", "result": "stale text"},
        {**_plan_entry(1, "Write", dependencies=[0]), "status": "done"},
    ]
    source = TaskRun(
        task_id="task-1",
        status=TaskRunStatus.FAILED,
        plan=stale_source_plan,
    )
    await source.save()
    typed = StepResult(
        text="authoritative text",
        artifacts=[{"id": "artifact-1", "mime_type": "text/plain"}],
        structured_data={"records": 4},
        citations=[{"url": "https://example.test/source"}],
        warnings=["partial input"],
    )
    await TaskRunStep(
        run_id=source.id,
        task_id=source.task_id,
        step_index=0,
        title="Collect",
        status=TaskRunStepStatus.DONE,
        attempts=2,
        result=typed,
        gate="passed",
    ).save()
    await TaskRunStep(
        run_id=source.id,
        task_id=source.task_id,
        step_index=1,
        title="Write",
        dependencies=[0],
        status=TaskRunStepStatus.FAILED,
        attempts=3,
        result=StepResult(text="discard this failed attempt"),
        gate="unverified",
        error="failed gate",
    ).save()
    target, claim = await _running_run()

    seeded = await RunRepository().seed_resume_steps(
        target.id,
        source.id,
        claim=claim,
    )

    rows = await _steps(target.id)
    stored_target = await TaskRun.get(target.id)
    assert [row.step_index for row in seeded] == [0, 1]
    assert rows[0].status == TaskRunStepStatus.DONE
    assert rows[0].attempts == 2
    assert rows[0].result == typed
    assert rows[0].gate == "passed"
    assert rows[1].status == TaskRunStepStatus.PENDING
    assert rows[1].attempts == 0
    assert rows[1].result is None
    assert rows[1].gate is None
    assert rows[1].error is None
    assert stored_target is not None
    assert stored_target.plan[0]["result"] == "authoritative text"
    assert stored_target.plan[1]["result"] is None


@pytest.mark.asyncio
async def test_resume_seeding_falls_back_to_legacy_plan_only_run(task_step_db):
    source = TaskRun(
        task_id="task-legacy",
        status=TaskRunStatus.CANCELLED,
        plan=[
            {
                **_plan_entry(0, "Legacy done"),
                "status": "done",
                "attempts": 1,
                "result": "legacy string result",
                "gate": "passed",
            },
            {
                **_plan_entry(1, "Legacy retry", dependencies=[0]),
                "status": "cancelled",
                "attempts": 4,
                "result": "incomplete result",
                "gate": "unverified",
            },
        ],
    )
    await source.save()
    target, claim = await _running_run(task_id="task-legacy")

    await RunRepository().seed_resume_steps(
        target.id,
        source.id,
        claim=claim,
    )

    rows = await _steps(target.id)
    assert rows[0].status == TaskRunStepStatus.DONE
    assert rows[0].result == StepResult(text="legacy string result")
    assert rows[0].attempts == 1
    assert rows[1].status == TaskRunStepStatus.PENDING
    assert rows[1].result is None
    assert rows[1].attempts == 0
    assert rows[1].gate is None


@pytest.mark.asyncio
async def test_run_list_hydrates_step_rows_without_result_bodies(task_step_db):
    from cognitrix.api.routes import tasks as task_routes
    from cognitrix.common.security import AuthContext

    task = Task(title="API task", description="Work")
    await task.save()
    run = TaskRun(
        task_id=task.id,
        status=TaskRunStatus.RUNNING,
        acl_version=1,
        plan=[{**_plan_entry(0, "Stale"), "status": "pending"}],
    )
    await run.save()
    await TaskRunStep(
        run_id=run.id,
        task_id=run.task_id,
        step_index=0,
        title="Authoritative",
        description="A" * 250,
        status=TaskRunStepStatus.DONE,
        result=StepResult(
            text="PRIVATE FULL STEP BODY",
            structured_data={"private": True},
        ),
    ).save()

    payload = await task_routes.list_task_runs(
        run.task_id,
        AuthContext(user=SimpleNamespace(id="user-1"), api_key=None),
    )

    assert len(payload) == 1
    assert payload[0]["plan"][0]["title"] == "Authoritative"
    assert payload[0]["plan"][0]["status"] == "done"
    assert len(payload[0]["plan"][0]["description"]) == 200
    assert "result" not in payload[0]["plan"][0]
    assert "result_data" not in payload[0]["plan"][0]
    assert "PRIVATE FULL STEP BODY" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_run_detail_hydrates_step_rows_without_result_bodies(task_step_db):
    from cognitrix.api.routes import tasks as task_routes
    from cognitrix.common.security import AuthContext

    task = Task(title="API task", description="Work")
    await task.save()
    run = TaskRun(
        task_id=task.id,
        status=TaskRunStatus.COMPLETED,
        acl_version=1,
        plan=[{**_plan_entry(0, "Stale"), "status": "pending"}],
    )
    await run.save()
    await TaskRunStep(
        run_id=run.id,
        task_id=run.task_id,
        step_index=0,
        title="Authoritative detail",
        description="Full detail description",
        status=TaskRunStepStatus.DONE,
        result=StepResult(
            text="PRIVATE DETAIL STEP BODY",
            artifacts=[{"id": "private-artifact"}],
        ),
    ).save()
    matching_routes = [
        route
        for route in task_routes.tasks_api.routes
        if route.path == "/tasks/{task_id}/runs/{run_id}"
        and "GET" in getattr(route, "methods", set())
    ]

    assert matching_routes, "missing GET /tasks/{task_id}/runs/{run_id}"
    payload = await matching_routes[0].endpoint(
        task_id=run.task_id,
        run_id=run.id,
        ctx=AuthContext(user=SimpleNamespace(id="user-1"), api_key=None),
    )

    assert payload["plan"][0]["title"] == "Authoritative detail"
    assert payload["plan"][0]["description"] == "Full detail description"
    assert payload["plan"][0]["status"] == "done"
    assert "result" not in payload["plan"][0]
    assert "result_data" not in payload["plan"][0]
    assert "PRIVATE DETAIL STEP BODY" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_run_detail_rejects_run_from_another_task(task_step_db):
    from fastapi import HTTPException

    from cognitrix.api.routes import tasks as task_routes
    from cognitrix.common.security import AuthContext

    owner = Task(title="Owner task", description="Work")
    other = Task(title="Other task", description="Work")
    await owner.save()
    await other.save()
    run = TaskRun(task_id=owner.id, status=TaskRunStatus.COMPLETED)
    await run.save()
    matching_routes = [
        route
        for route in task_routes.tasks_api.routes
        if route.path == "/tasks/{task_id}/runs/{run_id}"
        and "GET" in getattr(route, "methods", set())
    ]

    assert matching_routes, "missing GET /tasks/{task_id}/runs/{run_id}"
    with pytest.raises(HTTPException) as exc_info:
        await matching_routes[0].endpoint(
            task_id=other.id,
            run_id=run.id,
            ctx=AuthContext(user=SimpleNamespace(id="user-1"), api_key=None),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Task run not found"
