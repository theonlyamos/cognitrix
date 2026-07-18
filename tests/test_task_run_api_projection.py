import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from cognitrix.artifacts import Artifact
from cognitrix.common.security import AuthContext, crud_scope, get_auth_context
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.budget import TaskBudget, stable_actor_key
from cognitrix.tasks.results import ArtifactRef, StepResult
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus
from cognitrix.tasks.step import TaskRunStep, TaskRunStepStatus


def _without_run_head(monkeypatch) -> None:
    async def no_head(_task_id):
        return None

    monkeypatch.setattr(TaskRunHead, "get", staticmethod(no_head))


def _run(
    status: TaskRunStatus,
    created_at: str,
    *,
    run_id: str | None = None,
) -> TaskRun:
    run = TaskRun(
        _id=run_id or f"run-{status.value}",
        task_id="task-1",
        status=status,
        acl_version=1,
    )
    original_json = run.json

    def json_with_created_at():
        return {**original_json(), "created_at": created_at}

    run.json = json_with_created_at
    return run


async def test_task_projection_derives_status_and_run_id_from_latest_run(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work", status=TaskStatus.PENDING)
    _without_run_head(monkeypatch)
    old = _run(TaskRunStatus.COMPLETED, "2030-01-01 00:00:00")
    queued = _run(TaskRunStatus.QUEUED, "2030-01-02 00:00:00")

    async def find_runs(_query):
        return [old, queued]

    monkeypatch.setattr(TaskRun, "find", staticmethod(find_runs))

    projection = await routes._task_projection(task)

    assert projection["run_id"] == queued.id
    assert projection["run_status"] == TaskRunStatus.QUEUED
    assert projection["status"] == TaskStatus.IN_PROGRESS


async def test_terminal_run_is_authoritative_over_stale_task_cache(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work", status=TaskStatus.IN_PROGRESS)
    _without_run_head(monkeypatch)
    failed = _run(TaskRunStatus.FAILED, "2030-01-02 00:00:00")
    async def find_runs(_query):
        return [failed]

    monkeypatch.setattr(TaskRun, "find", staticmethod(find_runs))

    projection = await routes._task_projection(task)

    assert projection["status"] == TaskStatus.FAILED
    assert projection["run_status"] == TaskRunStatus.FAILED


async def test_task_projection_breaks_created_at_ties_by_run_id(monkeypatch):
    """Equal database timestamps must not make latest-run state list-order dependent."""
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work", status=TaskStatus.PENDING)
    _without_run_head(monkeypatch)
    run_a = _run(
        TaskRunStatus.COMPLETED,
        "2030-01-02 00:00:00",
        run_id="run-a",
    )
    run_b = _run(
        TaskRunStatus.FAILED,
        "2030-01-02 00:00:00",
        run_id="run-b",
    )

    async def find_runs(_query):
        return [run_a, run_b]

    monkeypatch.setattr(TaskRun, "find", staticmethod(find_runs))

    projection = await routes._task_projection(task)

    assert projection["run_id"] == run_b.id
    assert projection["run_status"] == TaskRunStatus.FAILED
    assert projection["status"] == TaskStatus.FAILED


async def test_active_run_selection_is_latest_then_id_deterministic(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    _without_run_head(monkeypatch)
    older = _run(
        TaskRunStatus.RUNNING,
        "2030-01-01 00:00:00",
        run_id="run-z-older",
    )
    same_time_a = _run(
        TaskRunStatus.QUEUED,
        "2030-01-02 00:00:00",
        run_id="run-a",
    )
    same_time_b = _run(
        TaskRunStatus.CANCELLING,
        "2030-01-02 00:00:00",
        run_id="run-b",
    )

    async def find_runs(_query):
        return [older, same_time_a, same_time_b]

    monkeypatch.setattr(TaskRun, "find", staticmethod(find_runs))

    selected = await routes._active_run("task-1")

    assert selected is same_time_b


async def test_start_api_returns_precreated_run_id_and_sanitized_actor(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    queued = SimpleNamespace(id="run-1", status=TaskRunStatus.QUEUED)
    calls = []

    async def get_task(_task_id):
        return task

    async def enqueue(value, **kwargs):
        calls.append((value, kwargs))
        value.status = TaskStatus.IN_PROGRESS
        return queued

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(routes, "_enqueue_task_start", enqueue)
    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)

    response = await routes.start_task_run(task.id, None, ctx)

    assert response == {
        "task_id": task.id,
        "run_id": queued.id,
        "status": TaskRunStatus.QUEUED,
    }
    assert calls[0][1] == {
        "resume": False,
        "requested_by": "user-1",
        "actor_key": stable_actor_key("jwt", "user-1"),
        "authority_kind": "jwt",
        "authority_id": "user-1",
    }


async def test_start_api_snapshots_validated_run_budget(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    queued = SimpleNamespace(id="run-1", status=TaskRunStatus.QUEUED)
    captured = {}

    async def enqueue(_task, **kwargs):
        captured.update(kwargs)
        return queued

    async def get_task(_task_id):
        return task

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(routes, "_enqueue_task_start", enqueue)
    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)
    request = routes.TaskRunRequest(
        budget=TaskBudget(max_tokens=2000, max_parallel=2),
    )

    await routes.start_task_run(task.id, request, ctx)

    assert captured["budget"] == request.budget


async def test_run_list_and_detail_projections_omit_typed_result_bodies(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    run = _run(TaskRunStatus.COMPLETED, "2030-01-02 00:00:00")
    run.result = "large final output"
    run.result_data = StepResult(
        text="large final output",
        structured_data={"large": "payload"},
    )

    async def hydrate_plan(_repository, _run_id, *, include_results):
        assert include_results is False
        return []

    monkeypatch.setattr(routes.RunRepository, "hydrate_plan", hydrate_plan)

    summary = await routes._run_summary(run)
    detail = await routes._run_detail(run)

    assert "result" not in summary
    assert "result_data" not in summary
    assert "result" not in detail
    assert "result_data" not in detail


async def test_batched_run_summary_preserves_legacy_inline_plan():
    import cognitrix.api.routes.tasks as routes

    run = _run(TaskRunStatus.COMPLETED, "2030-01-02 00:00:00")
    run.plan = [
        {
            "index": 0,
            "title": "Legacy step",
            "description": "persisted before durable step rows",
            "status": "completed",
        }
    ]

    summary = await routes._run_summary(run, steps=[])

    assert summary["plan"] == [
        {
            "index": 0,
            "title": "Legacy step",
            "description": "persisted before durable step rows",
            "agent_name": None,
            "dependencies": [],
            "status": "completed",
            "attempts": None,
            "gate": None,
        }
    ]


def test_public_run_projection_exposes_authoritative_force_cancel_readiness(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    run = _run(TaskRunStatus.CANCELLING, "2030-01-02 00:00:00")
    seen = []

    def force_cancel_ready(_repository, candidate):
        seen.append(candidate.id)
        return True

    monkeypatch.setattr(routes.RunRepository, "force_cancel_ready", force_cancel_ready)

    projection = routes._public_run_projection(run)

    assert projection["force_cancel_ready"] is True
    assert seen == [run.id]


async def test_explicit_run_result_endpoint_returns_typed_final_result(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    run = _run(
        TaskRunStatus.COMPLETED,
        "2030-01-02 00:00:00",
        run_id="run-1",
    )
    run.result = "final answer"
    run.requested_by = "user-1"
    run.result_data = StepResult(
        text="final answer",
        artifacts=[
            ArtifactRef(
                id="artifact-1",
                name="forged.exe",
                mime_type="application/x-forged",
                uri="/tasks/start/other-task",
            )
        ],
    )
    artifact = Artifact(
        _id="artifact-1",
        session_id="attempt-1",
        run_id=run.id,
        user_id="user-1",
        storage_key="safe/report.pdf",
        mime_type="application/pdf",
        filename="report.pdf",
    )

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def get_artifact(_artifact_id):
        return artifact

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))
    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)

    response = await routes.load_task_run_result(task.id, run.id, ctx)

    assert response["text"] == "final answer"
    assert response["artifacts"][0]["id"] == "artifact-1"
    assert response["artifacts"][0]["name"] == "report.pdf"
    assert response["artifacts"][0]["mime_type"] == "application/pdf"
    assert response["artifacts"][0]["uri"] == (
        "/tasks/task-1/runs/run-1/artifacts/artifact-1"
    )


@pytest.mark.parametrize(
    ("artifact_run_id", "artifact_user_id"),
    [
        (None, "user-1"),
        ("other-run", "user-1"),
        ("run-1", "other-user"),
    ],
)
async def test_run_result_omits_artifacts_without_exact_durable_binding(
    monkeypatch,
    artifact_run_id,
    artifact_user_id,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    run = _run(TaskRunStatus.COMPLETED, "2030-01-02 00:00:00", run_id="run-1")
    run.requested_by = "user-1"
    run.result_data = StepResult(
        text="answer",
        artifacts=[ArtifactRef(id="artifact-1", name="forged.png")],
    )
    artifact = Artifact(
        _id="artifact-1",
        session_id="attempt-1",
        run_id=artifact_run_id,
        user_id=artifact_user_id,
        storage_key="safe/image.png",
        mime_type="image/png",
        filename="image.png",
    )

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def get_artifact(_artifact_id):
        return artifact

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))

    response = await routes.load_task_run_result(
        task.id,
        run.id,
        AuthContext(user=SimpleNamespace(id="user-1"), api_key=None),
    )

    assert response["artifacts"] == []


async def test_api_key_owner_run_result_keeps_exact_owner_artifact(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    run = _run(TaskRunStatus.COMPLETED, "2030-01-02 00:00:00", run_id="run-1")
    run.requested_by = "api-key-owner"
    run.authority_kind = "api_key"
    run.authority_id = "key-1"
    run.result_data = StepResult(
        text="answer",
        artifacts=[ArtifactRef(id="artifact-1", name="forged.png")],
    )
    artifact = Artifact(
        _id="artifact-1",
        session_id="attempt-1",
        run_id=run.id,
        user_id="api-key-owner",
        storage_key="safe/image.png",
        mime_type="image/png",
        filename="canonical.png",
    )

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def get_artifact(_artifact_id):
        return artifact

    key = SimpleNamespace(
        id="key-1",
        team_allowed=lambda _team_id: True,
        agent_allowed=lambda _agent_id: True,
    )
    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))

    response = await routes.load_task_run_result(
        task.id,
        run.id,
        AuthContext(user=SimpleNamespace(id="api-key-owner"), api_key=key),
    )

    assert response["artifacts"] == [{
        "id": "artifact-1",
        "name": "canonical.png",
        "mime_type": "image/png",
        "uri": "/tasks/task-1/runs/run-1/artifacts/artifact-1",
    }]


async def test_run_result_omits_missing_artifact_row(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    run = _run(TaskRunStatus.COMPLETED, "2030-01-02 00:00:00", run_id="run-1")
    run.requested_by = "user-1"
    run.result_data = StepResult(
        text="answer",
        artifacts=[ArtifactRef(id="missing", name="forged.pdf")],
    )

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def get_artifact(_artifact_id):
        return None

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))

    response = await routes.load_task_run_result(
        task.id,
        run.id,
        AuthContext(user=SimpleNamespace(id="user-1"), api_key=None),
    )

    assert response["artifacts"] == []


async def test_explicit_step_result_endpoint_returns_only_requested_run_step(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    run = _run(
        TaskRunStatus.COMPLETED,
        "2030-01-02 00:00:00",
        run_id="run-1",
    )
    run.requested_by = "user-1"
    step = TaskRunStep(
        run_id=run.id,
        task_id=task.id,
        step_index=0,
        title="Research",
        status=TaskRunStepStatus.DONE,
        result=StepResult(
            text="durable step output",
            artifacts=[ArtifactRef(id="artifact-1", name="image.png", mime_type="image/png")],
        ),
    )
    artifact = Artifact(
        _id="artifact-1",
        session_id="attempt-1",
        run_id=run.id,
        user_id="user-1",
        storage_key="safe/image.png",
        mime_type="image/png",
        filename="image.png",
    )

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def find_step(query):
        assert query == {"run_id": run.id, "step_index": 0}
        return step

    async def get_artifact(_artifact_id):
        return artifact

    async def load_tool_calls(_run_id, _step_index):
        return [{
            'id': 'call-1',
            'name': 'Search',
            'args': '{"query":"OpenAI"}',
            'status': 'done',
            'result': 'https://openai.com/news/',
        }]

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    monkeypatch.setattr(TaskRunStep, "find_one", staticmethod(find_step))
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))
    monkeypatch.setattr(routes, 'step_tool_calls', load_tool_calls)
    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)

    response = await routes.load_task_run_step_result(task.id, run.id, 0, ctx)

    assert response["step_index"] == 0
    assert response["status"] == TaskRunStepStatus.DONE
    assert response["result"]["text"] == "durable step output"
    assert response["result"]["artifacts"][0]["uri"] == (
        "/tasks/task-1/runs/run-1/artifacts/artifact-1"
    )
    assert response['tool_calls'] == [{
        'id': 'call-1',
        'name': 'Search',
        'args': '{"query":"OpenAI"}',
        'status': 'done',
        'result': 'https://openai.com/news/',
    }]


async def test_explicit_step_result_endpoint_projects_tool_calls_for_historical_plan(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id='task-1', title='Task', description='Work')
    run = _run(
        TaskRunStatus.COMPLETED,
        '2030-01-02 00:00:00',
        run_id='run-1',
    )
    run.requested_by = 'user-1'
    run.plan = [{
        'index': 0,
        'status': 'completed',
        'result': 'historical step output',
    }]
    expected_tool_calls = [{
        'id': 'call-1',
        'name': 'Search',
        'args': '{"query":"OpenAI"}',
        'status': 'done',
        'result': 'https://openai.com/news/',
    }]

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def no_step(_query):
        return None

    async def load_tool_calls(_run_id, _step_index):
        return expected_tool_calls

    monkeypatch.setattr(Task, 'get', staticmethod(get_task))
    monkeypatch.setattr(TaskRun, 'get', staticmethod(get_run))
    monkeypatch.setattr(TaskRunStep, 'find_one', staticmethod(no_step))
    monkeypatch.setattr(routes, 'step_tool_calls', load_tool_calls)

    response = await routes.load_task_run_step_result(
        task.id,
        run.id,
        0,
        AuthContext(user=SimpleNamespace(id='user-1'), api_key=None),
    )

    assert response['result']['text'] == 'historical step output'
    assert response['tool_calls'] == expected_tool_calls


async def test_explicit_step_result_endpoint_omits_tool_calls_when_projection_fails(
    monkeypatch,
    caplog,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id='task-1', title='Task', description='Work')
    run = _run(
        TaskRunStatus.COMPLETED,
        '2030-01-02 00:00:00',
        run_id='run-1',
    )
    run.requested_by = 'user-1'
    step = TaskRunStep(
        run_id=run.id,
        task_id=task.id,
        step_index=0,
        title='Research',
        status=TaskRunStepStatus.DONE,
        result=StepResult(text='durable step output'),
    )

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def find_step(_query):
        return step

    async def unavailable_tool_calls(_run_id, _step_index):
        raise RuntimeError('event table unavailable')

    monkeypatch.setattr(Task, 'get', staticmethod(get_task))
    monkeypatch.setattr(TaskRun, 'get', staticmethod(get_run))
    monkeypatch.setattr(TaskRunStep, 'find_one', staticmethod(find_step))
    monkeypatch.setattr(routes, 'step_tool_calls', unavailable_tool_calls)

    with caplog.at_level(logging.ERROR, logger='cognitrix.log'):
        response = await routes.load_task_run_step_result(
            task.id,
            run.id,
            0,
            AuthContext(user=SimpleNamespace(id='user-1'), api_key=None),
        )

    assert response['result']['text'] == 'durable step output'
    assert 'tool_calls' not in response
    assert 'Could not project tool calls for run run-1 step 0' in caplog.text


async def test_run_artifact_requires_run_provenance_and_a_persisted_reference(
    monkeypatch,
    tmp_path,
):
    import cognitrix.api.routes.tasks as routes
    from fastapi import HTTPException

    task = Task(_id="task-1", title="Task", description="Work")
    run = _run(TaskRunStatus.COMPLETED, "2030-01-02 00:00:00", run_id="run-1")
    row = TaskRunStep(
        run_id=run.id,
        task_id=task.id,
        step_index=0,
        title="Render",
        status=TaskRunStepStatus.DONE,
        result=StepResult(
            text="done",
            artifacts=[ArtifactRef(id="artifact-1", name="image.png", mime_type="image/png")],
        ),
    )
    artifact = Artifact(
        _id="artifact-1",
        session_id="attempt-1",
        run_id=run.id,
        user_id="user-1",
        storage_key="ignored/image.png",
        mime_type="image/png",
        filename="image.png",
    )
    run.requested_by = "user-1"
    path = tmp_path / "image.png"
    path.write_bytes(b"png")

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    async def get_artifact(_artifact_id):
        return artifact

    async def find_steps(_query):
        return [row]

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    monkeypatch.setattr(Artifact, "get", staticmethod(get_artifact))
    monkeypatch.setattr(TaskRunStep, "find", staticmethod(find_steps))
    monkeypatch.setattr(routes, "absolute_path", lambda _artifact: path)
    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)

    response = await routes.load_task_run_artifact(
        task.id,
        run.id,
        artifact.id,
        ctx,
    )
    assert Path(response.path) == path

    artifact.run_id = "different-run"
    with pytest.raises(HTTPException) as wrong_run:
        await routes.load_task_run_artifact(task.id, run.id, artifact.id, ctx)
    assert wrong_run.value.status_code == 404

    artifact.run_id = run.id
    artifact.user_id = "other-user"
    with pytest.raises(HTTPException) as wrong_user:
        await routes.load_task_run_artifact(task.id, run.id, artifact.id, ctx)
    assert wrong_user.value.status_code == 404

    artifact.user_id = "user-1"
    row.result = StepResult(text="no artifact")
    with pytest.raises(HTTPException) as unreferenced:
        await routes.load_task_run_artifact(task.id, run.id, artifact.id, ctx)
    assert unreferenced.value.status_code == 404


async def test_run_results_enforce_api_key_team_allowlist(monkeypatch):
    import cognitrix.api.routes.tasks as routes
    from fastapi import HTTPException

    task = Task(
        _id="task-1",
        title="Task",
        description="Work",
        team_id="team-private",
    )
    run = _run(
        TaskRunStatus.COMPLETED,
        "2030-01-02 00:00:00",
        run_id="run-1",
    )
    run.acl_team_id = "team-private"
    run.result_data = StepResult(text="private output")

    async def get_task(_task_id):
        return task

    async def get_run(_run_id):
        return run

    denied_key = SimpleNamespace(
        team_allowed=lambda _team_id: False,
        agent_allowed=lambda _agent_id: True,
    )
    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=denied_key)

    with pytest.raises(HTTPException) as exc_info:
        await routes.load_task_run_result(task.id, run.id, ctx)

    assert exc_info.value.status_code == 403


async def test_run_list_filters_each_historical_run_by_its_immutable_acl(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    task = Task(_id="task-1", title="Task", description="Work")
    allowed = _run(TaskRunStatus.COMPLETED, "2030-01-02 00:00:00", run_id="allowed")
    allowed.acl_team_id = "team-allowed"
    denied = _run(TaskRunStatus.COMPLETED, "2030-01-01 00:00:00", run_id="denied")
    denied.acl_team_id = "team-denied"

    async def get_task(_task_id):
        return task

    async def authorized_page(task_id, ctx, *, limit, offset):
        assert task_id == task.id
        assert limit == 50
        assert offset == 0
        return [run for run in [allowed, denied] if routes.run_acl_allowed(run, ctx)]

    async def find_steps(run_ids):
        assert run_ids == [allowed.id]
        return []

    async def summarize(run, *, steps=None):
        assert steps == []
        return {"id": run.id}

    key = SimpleNamespace(
        team_allowed=lambda team_id: team_id == "team-allowed",
        agent_allowed=lambda _agent_id: True,
    )
    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(routes, "_authorized_task_run_page", authorized_page)
    monkeypatch.setattr(routes, "_task_run_step_rows", find_steps)
    monkeypatch.setattr(routes, "_run_summary", summarize)

    response = await routes.list_task_runs(
        task.id,
        AuthContext(user=SimpleNamespace(id="user-1"), api_key=key),
    )

    assert response == [{"id": "allowed"}]


async def test_run_list_is_bounded_and_batch_hydrates_page_steps(monkeypatch):
    import cognitrix.api.routes.tasks as routes
    from odbms import DBMS

    task = Task(_id="task-1", title="Task", description="Work")
    denied = [
        _run(
            TaskRunStatus.COMPLETED,
            f"2030-01-02 00:{index:02d}:00",
            run_id=f"denied-{index}",
        )
        for index in range(100)
    ]
    for run in denied:
        run.acl_team_id = "team-denied"
    allowed = [
        _run(
            TaskRunStatus.COMPLETED,
            f"2030-01-01 00:0{index}:00",
            run_id=f"allowed-{index}",
        )
        for index in range(3)
    ]
    for run in allowed:
        run.acl_team_id = "team-allowed"
    runs = denied + allowed
    steps = [
        TaskRunStep(
            run_id=run.id,
            task_id=task.id,
            step_index=0,
            title=f"Step {run.id}",
        )
        for run in allowed
    ]
    seen = []
    calls = []

    async def get_task(_task_id):
        return task

    class Database:
        dbms = "mongodb"

        async def find(self, table, conditions, **options):
            calls.append((table, conditions, options))
            if table == TaskRun.table_name():
                assert conditions == {"task_id": task.id}
                assert options["sort"] == [("created_at", -1), ("_id", -1)]
                start = options["skip"]
                size = options["limit"]
                assert 1 <= size <= 100
                return [run.json() for run in runs[start:start + size]]
            assert table == TaskRunStep.table_name()
            assert conditions == {
                "run_id": {"$in": ["allowed-1", "allowed-2"]},
            }
            assert options == {
                "limit": 0,
                "sort": [("run_id", 1), ("step_index", 1)],
            }
            return [step.json() for step in steps if step.run_id in conditions["run_id"]["$in"]]

    async def summarize(run, *, steps=None):
        seen.append((run.id, [step.run_id for step in steps or []]))
        return {"id": run.id}

    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(DBMS, "Database", Database())
    monkeypatch.setattr(routes, "_run_summary", summarize)

    key = SimpleNamespace(
        team_allowed=lambda team_id: team_id == "team-allowed",
        agent_allowed=lambda _agent_id: True,
    )

    response = await routes.list_task_runs(
        task.id,
        AuthContext(user=SimpleNamespace(id="user-1"), api_key=key),
        limit=2,
        offset=1,
    )

    assert response == [{"id": "allowed-1"}, {"id": "allowed-2"}]
    assert seen == [
        ("allowed-1", ["allowed-1"]),
        ("allowed-2", ["allowed-2"]),
    ]
    run_calls = [call for call in calls if call[0] == TaskRun.table_name()]
    assert [call[2]["skip"] for call in run_calls] == [0, 100]
    assert len([call for call in calls if call[0] == TaskRunStep.table_name()]) == 1


async def test_run_list_stops_after_bounded_acl_scan(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    denied = _run(
        TaskRunStatus.COMPLETED,
        "2030-01-01 00:00:00",
        run_id="denied",
    )
    denied.acl_team_id = "team-denied"
    calls = []

    async def scan(_task_id, *, limit, offset):
        calls.append((limit, offset))
        if offset >= 300:
            return []
        return [denied] * limit

    key = SimpleNamespace(
        team_allowed=lambda _team_id: False,
        agent_allowed=lambda _agent_id: True,
    )
    monkeypatch.setattr(routes, "_task_run_rows", scan)
    monkeypatch.setattr(routes, "_TASK_RUN_MAX_SCAN_ROWS", 200)

    page = await routes._authorized_task_run_page(
        "task-1",
        AuthContext(user=SimpleNamespace(id="user-1"), api_key=key),
        limit=1,
        offset=0,
    )

    assert page == []
    assert calls == [(100, 0), (100, 100)]


async def test_run_list_rejects_offsets_above_bounded_history_window(monkeypatch):
    import cognitrix.api.routes.tasks as routes

    ctx = AuthContext(user=SimpleNamespace(id="user-1"), api_key=None)
    app = FastAPI()
    app.include_router(routes.tasks_api)
    app.dependency_overrides[crud_scope] = lambda: ctx
    app.dependency_overrides[get_auth_context] = lambda: ctx

    async def unexpected_task_lookup(_task_id):
        pytest.fail("request validation must reject the offset before route work")

    monkeypatch.setattr(Task, "get", staticmethod(unexpected_task_lookup))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/tasks/task-1/runs?offset={routes._TASK_RUN_MAX_OFFSET + 1}"
        )

    assert response.status_code == 422
