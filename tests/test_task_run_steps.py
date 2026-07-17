from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def test_task_run_step_coerces_legacy_result_and_projects_legacy_plan_entry():
    from cognitrix.tasks.results import StepResult
    from cognitrix.tasks.step import TaskRunStep, TaskRunStepStatus

    step = TaskRunStep(
        run_id="run-1",
        step_index=2,
        title="Write report",
        description="Prepare the final report",
        expected_output="Markdown",
        verification_criteria="Contains findings",
        agent_name="Researcher",
        dependencies=None,
        status=TaskRunStepStatus.DONE,
        attempts=2,
        result="legacy step result",
        gate="passed",
        runtime_snapshot=None,
    )

    assert isinstance(step.result, StepResult)
    assert step.result.text == "legacy step result"
    assert step.dependencies == []
    assert step.runtime_snapshot is None
    assert step.to_plan_entry() == {
        "index": 2,
        "title": "Write report",
        "description": "Prepare the final report",
        "expected_output": "Markdown",
        "verification_criteria": "Contains findings",
        "agent_name": "Researcher",
        "dependencies": [],
        "status": "done",
        "attempts": 2,
        "result": "legacy step result",
        "gate": "passed",
    }


def test_task_run_step_typed_result_round_trips_through_model_dump():
    from cognitrix.tasks.results import StepResult
    from cognitrix.tasks.runtime import AgentRuntimeSnapshot, LLMRuntimeSnapshot
    from cognitrix.tasks.step import TaskRunStep

    snapshot = AgentRuntimeSnapshot(
        agent_id="agent-1",
        name="Researcher",
        system_prompt="Research carefully.",
        llm=LLMRuntimeSnapshot(provider="test", model="test-model"),
        tool_names=("Search",),
        tool_schemas=({
            "type": "function",
            "function": {
                "name": "Search",
                "description": "Search for relevant sources",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },),
    )

    step = TaskRunStep(
        run_id="run-1",
        step_index=0,
        title="Collect",
        description="Collect data",
        result={
            "text": "collected",
            "artifacts": [{"id": "file-1", "mime_type": "text/csv"}],
            "structured_data": {"rows": 3},
            "citations": [{"url": "https://example.test/data"}],
            "warnings": ["one row omitted"],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        },
        runtime_snapshot=snapshot,
    )

    restored = TaskRunStep(**step.model_dump(mode="json"))

    assert isinstance(restored.result, StepResult)
    assert restored.result.structured_data == {"rows": 3}
    assert restored.result.artifacts[0]["id"] == "file-1"
    assert restored.runtime_snapshot == snapshot


def test_runtime_snapshot_rejects_secret_and_client_fields():
    from cognitrix.tasks.runtime import LLMRuntimeSnapshot

    with pytest.raises(ValidationError):
        LLMRuntimeSnapshot(provider="test", model="m", api_key="secret")
    with pytest.raises(ValidationError):
        LLMRuntimeSnapshot(provider="test", model="m", client=object())


def test_required_tools_preserves_unknown_empty_and_exact_allowlist():
    from cognitrix.tasks.step import TaskRunStep

    unknown = TaskRunStep(run_id="r", step_index=0, title="a", required_tools=None)
    none = TaskRunStep(run_id="r", step_index=1, title="b", required_tools=[])
    exact = TaskRunStep(
        run_id="r", step_index=2, title="c", required_tools=["Search", "Write File"]
    )

    assert unknown.required_tools is None
    assert none.required_tools == []
    assert exact.required_tools == ["Search", "Write File"]


def test_nested_collections_use_json_for_every_sql_backend(monkeypatch):
    import json

    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.run import TaskRun

    _patch_odbms_sqlite()
    monkeypatch.setattr(DBMS, "Database", SimpleNamespace(dbms="postgresql"))
    payload = [{"sequence": 1, "kind": "run_status"}]

    encoded = TaskRun.normalise({"event_outbox": payload}, "params")
    assert json.loads(encoded["event_outbox"]) == payload
    decoded = TaskRun.normalise({"event_outbox": encoded["event_outbox"]})
    assert decoded["event_outbox"] == payload


@pytest.mark.asyncio
async def test_durable_task_schema_migration_is_additive_and_idempotent(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite
    from cognitrix.tasks.events import TaskRunEvent
    from cognitrix.tasks.metrics import TaskRunPhaseMetric
    from cognitrix.tasks.results import StepResult
    from cognitrix.tasks.run import TaskRun, TaskRunStatus
    from cognitrix.tasks.step import TaskRunStep

    db_file = str(tmp_path / "durable-task.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=db_file)
    else:
        DBMS.initialize("sqlite", database=db_file)
    _patch_odbms_sqlite()

    # Simulate an existing pre-durability database. The migration must add to
    # this table without dropping legacy columns or data.
    await DBMS.Database.query(
        "CREATE TABLE taskruns ("
        "id TEXT PRIMARY KEY, task_id TEXT, status TEXT, plan TEXT, "
        "result TEXT, error TEXT, started_at TEXT, completed_at TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    await DBMS.Database.query(
        "INSERT INTO taskruns (id, task_id, status, plan, result) "
        "VALUES ('legacy-run', 'task-legacy', 'completed', '[]', 'legacy')"
    )
    await DBMS.Database.query(
        "CREATE TABLE tasks ("
        "id TEXT PRIMARY KEY, name TEXT, description TEXT, status TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    await DBMS.Database.query(
        "INSERT INTO tasks (id, name, description, status) "
        "VALUES ('task-legacy', 'Legacy', 'Existing task', 'pending')"
    )
    await DBMS.Database.query(
        "CREATE TABLE artifacts ("
        "id TEXT PRIMARY KEY, session_id TEXT, storage_key TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    await DBMS.Database.query(
        "CREATE TABLE taskrunevents ("
        "id TEXT, run_id TEXT, session_id TEXT, step_index INTEGER, "
        "sequence INTEGER, kind TEXT, agent_name TEXT, data TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    for event_id in ("event-1", "event-2"):
        await DBMS.Database.query(
            "INSERT INTO taskrunevents "
            "(id, run_id, sequence, kind, data, created_at, updated_at) "
            f"VALUES ('{event_id}', 'run-legacy', 1, 'status', '{{}}', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

    await _ensure_schema()
    await _ensure_schema()

    columns_cursor = await DBMS.Database.query("PRAGMA table_info(taskruns)")
    columns = {row[1] for row in columns_cursor.fetchall()}
    assert {
        "requested_by",
        "authority_kind",
        "authority_id",
        "acl_version",
        "acl_team_id",
        "acl_agent_ids",
        "callback_url",
        "callback_key_id",
        "completion_notification_state",
        "completion_notification_next_at",
        "completion_notification_attempts",
        "resume_from_run_id",
        "result_data",
        "queue_job_id",
        "lease_owner",
        "lease_generation",
        "heartbeat_at",
        "lease_expires_at",
        "cancel_requested_at",
        "version",
        "next_event_sequence",
        "event_outbox",
        "budget",
        "usage",
        "error_code",
    } <= columns

    task_columns_cursor = await DBMS.Database.query("PRAGMA table_info(tasks)")
    task_columns = {row[1] for row in task_columns_cursor.fetchall()}
    assert {
        "schedule_requested_by",
        "schedule_authority_kind",
        "schedule_authority_id",
    } <= task_columns

    artifact_columns_cursor = await DBMS.Database.query(
        "PRAGMA table_info(artifacts)"
    )
    artifact_columns = {row[1] for row in artifact_columns_cursor.fetchall()}
    assert {"user_id", "run_id"} <= artifact_columns

    tables_cursor = await DBMS.Database.query(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    tables = {row[0] for row in tables_cursor.fetchall()}
    assert TaskRunStep.table_name() in tables
    assert TaskRunPhaseMetric.table_name() in tables

    step_indexes_cursor = await DBMS.Database.query(
        f"PRAGMA index_list({TaskRunStep.table_name()})"
    )
    step_indexes = {row[1]: row[2] for row in step_indexes_cursor.fetchall()}
    assert step_indexes["ux_task_run_steps_run_step"] == 1

    event_indexes_cursor = await DBMS.Database.query(
        f"PRAGMA index_list({TaskRunEvent.table_name()})"
    )
    event_indexes = {row[1]: row[2] for row in event_indexes_cursor.fetchall()}
    assert event_indexes["ux_task_run_events_run_sequence"] == 1
    assert "ix_task_runs_task_created" in {
        row[1] for row in (await DBMS.Database.query("PRAGMA index_list(taskruns)" )).fetchall()
    }
    assert "ix_task_runs_notification_due" in {
        row[1] for row in (await DBMS.Database.query("PRAGMA index_list(taskruns)" )).fetchall()
    }
    assert "ix_task_run_steps_run_status" in step_indexes
    metric_indexes = {
        row[1] for row in (
            await DBMS.Database.query(f"PRAGMA index_list({TaskRunPhaseMetric.table_name()})")
        ).fetchall()
    }
    assert "ix_task_run_metrics_run_phase" in metric_indexes

    duplicates = await DBMS.Database.query(
        "SELECT COUNT(*) FROM taskrunevents WHERE run_id = 'run-legacy' AND sequence = 1"
    )
    assert duplicates.fetchone()[0] == 1

    legacy = await TaskRun.get("legacy-run")
    assert legacy is not None
    assert legacy.result == "legacy"
    assert legacy.authority_kind == "system"
    assert legacy.event_outbox == []
    assert legacy.budget == {}
    assert legacy.usage == {}

    run = TaskRun(
        task_id="task-new",
        status=TaskRunStatus.QUEUED,
        result_data=StepResult(text="typed final"),
        event_outbox=[{"sequence": 1, "kind": "run_status"}],
        budget={"max_wall_seconds": 60},
        usage={"prompt_tokens": 8},
    )
    await run.save()
    loaded_run = await TaskRun.get(run.id)
    assert loaded_run is not None
    assert loaded_run.result_data == StepResult(text="typed final")
    assert loaded_run.event_outbox == [{"sequence": 1, "kind": "run_status"}]
    assert loaded_run.budget == {"max_wall_seconds": 60}
    assert loaded_run.usage == {"prompt_tokens": 8}

    step = TaskRunStep(
        run_id=run.id,
        step_index=0,
        title="Step",
        description="Do it",
        result=StepResult(text="typed step", structured_data={"ok": True}),
    )
    await step.save()
    loaded_step = await TaskRunStep.find_one({"run_id": run.id, "step_index": 0})
    assert loaded_step is not None
    assert loaded_step.result == StepResult(text="typed step", structured_data={"ok": True})

    for index, required_tools in enumerate((None, [], ["Search"])):
        stored = TaskRunStep(
            run_id=run.id,
            step_index=index + 1,
            title=f"Tool mode {index}",
            required_tools=required_tools,
        )
        await stored.save()
        loaded = await TaskRunStep.find_one(
            {"run_id": run.id, "step_index": index + 1}
        )
        assert loaded is not None and loaded.required_tools == required_tools


@pytest.mark.asyncio
async def test_non_sqlite_relational_migration_is_explicit_and_complete():
    from cognitrix.config import _migrate_relational_task_schema

    statements = []

    class Cursor:
        def __init__(self, rows=()):
            self.rows = rows

        def fetchall(self):
            return list(self.rows)

    class Recorder:
        dbms = "postgresql"

        @classmethod
        async def query(cls, statement, params=None):
            statements.append(statement)
            if "pg_index" in statement.lower():
                return Cursor([(True, ["run_id", "sequence"])])
            return Cursor()

    await _migrate_relational_task_schema(Recorder)

    sql = "\n".join(statements).lower()
    assert "alter table taskruns add column if not exists result_data" in sql
    assert "alter table taskruns add column if not exists event_outbox" in sql
    assert "alter table taskruns add column if not exists authority_kind" in sql
    assert "alter table taskruns add column if not exists authority_id" in sql
    assert "alter table tasks add column if not exists schedule_requested_by" in sql
    assert "alter table tasks add column if not exists schedule_authority_kind" in sql
    assert "alter table tasks add column if not exists schedule_authority_id" in sql
    assert "completion_notification_state" in sql
    assert "completion_notification_next_at" in sql
    assert "ix_task_runs_notification_due" in sql
    assert "ux_task_run_events_run_sequence" in sql
    assert "ix_task_runs_task_created" in sql


def test_durable_models_are_registered_in_api_and_cli_startup():
    root = Path(__file__).resolve().parents[1]
    config_source = (root / "cognitrix" / "config.py").read_text()
    cli_source = (root / "cognitrix" / "cli" / "core.py").read_text()

    for name in ("TaskRunStep", "TaskRunPhaseMetric"):
        assert f"import {name}" in config_source
        assert name in config_source.split("for model in (", 1)[1]
        assert f"import {name}" in cli_source
        assert name in cli_source.split("for model in (", 1)[1]
