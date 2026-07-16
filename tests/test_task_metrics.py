from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


@pytest.fixture
async def metric_repository_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.events import TaskRunEvent
    from cognitrix.tasks.metrics import TaskRunPhaseMetric
    from cognitrix.tasks.run import TaskRun, TaskRunHead

    database = str(tmp_path / "task-metric-repository.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=database)
    else:
        DBMS.initialize("sqlite", database=database)
    _patch_odbms_sqlite()

    for model in (TaskRun, TaskRunHead, TaskRunEvent, TaskRunPhaseMetric):
        create = getattr(model, "_create_table_async", None) or model.create_table
        await create()


def test_phase_metric_declares_observable_counts_without_prompt_content():
    from cognitrix.tasks.metrics import TaskRunPhaseMetric

    metric = TaskRunPhaseMetric(
        run_id="run-1",
        step_index=0,
        phase="evaluate",
        attempt=2,
        status="completed",
        started_at="2030-01-01 00:00:00",
        completed_at="2030-01-01 00:00:01",
        duration_ms=1000,
        prompt_tokens=12,
        completion_tokens=3,
        llm_calls=1,
        tool_calls=2,
        tool_attempts=3,
        cost_usd=Decimal("0.004"),
        error_code=None,
    )
    restored = TaskRunPhaseMetric(**metric.model_dump(mode="json"))

    assert restored.run_id == "run-1"
    assert restored.phase == "evaluate"
    assert restored.attempt == 2
    assert restored.duration_ms == 1000
    assert restored.prompt_tokens == 12
    assert restored.completion_tokens == 3
    assert restored.llm_calls == 1
    assert restored.tool_calls == 2
    assert restored.tool_attempts == 3
    assert restored.cost_usd == Decimal("0.004")
    assert "prompt" not in TaskRunPhaseMetric.model_fields
    assert "error" not in TaskRunPhaseMetric.model_fields


def test_phase_metric_null_counts_coerce_to_zero():
    from cognitrix.tasks.metrics import TaskRunPhaseMetric

    metric = TaskRunPhaseMetric(
        run_id="run-1",
        phase="plan",
        attempt=None,
        duration_ms=None,
        prompt_tokens=None,
        completion_tokens=None,
        llm_calls=None,
        tool_calls=None,
        tool_attempts=None,
        cost_usd=None,
    )

    assert metric.attempt == 1
    assert metric.duration_ms == 0
    assert metric.prompt_tokens == 0
    assert metric.completion_tokens == 0
    assert metric.llm_calls == 0
    assert metric.tool_calls == 0
    assert metric.tool_attempts == 0
    assert metric.cost_usd == Decimal("0")


@pytest.mark.asyncio
async def test_phase_recorder_persists_usage_delta_and_elapsed_time():
    from cognitrix.tasks.metrics import (
        TaskRunPhase,
        TaskRunPhaseRecorder,
        TaskRunPhaseStatus,
    )

    recorded = []
    class Repository:
        async def record_metric(self, run_id, *, claim, metric):
            assert run_id == "run-1"
            assert claim.owner == "worker-a"
            recorded.append(metric)
            return metric

    clock_values = iter((50.0, 50.375))
    now_values = iter(("2030-01-01 00:00:00", "2030-01-01 00:00:01"))
    recorder = TaskRunPhaseRecorder(
        Repository(),
        run_id="run-1",
        claim=SimpleNamespace(owner="worker-a"),
        clock=lambda: next(clock_values),
        now=lambda: next(now_values),
    )

    async with recorder.measure(
        TaskRunPhase.STEP,
        step_index=2,
        attempt=3,
    ) as usage:
        usage.record_llm(
            prompt_tokens=22,
            completion_tokens=9,
            duration_seconds=0.2,
            cost_usd=Decimal("0.0075"),
        )
        usage.record_tool_attempt(first_for_call=True)
        usage.record_tool_attempt(first_for_call=True)
        usage.record_tool_attempt(first_for_call=False)

    assert len(recorded) == 1
    metric = recorded[0]
    assert metric.phase == TaskRunPhase.STEP
    assert metric.status == TaskRunPhaseStatus.COMPLETED
    assert metric.step_index == 2
    assert metric.attempt == 3
    assert metric.started_at == "2030-01-01 00:00:00"
    assert metric.completed_at == "2030-01-01 00:00:01"
    assert metric.duration_ms == 375
    assert metric.prompt_tokens == 22
    assert metric.completion_tokens == 9
    assert metric.llm_calls == 1
    assert metric.tool_calls == 2
    assert metric.tool_attempts == 3
    assert metric.cost_usd == Decimal("0.0075")


@pytest.mark.asyncio
async def test_phase_recorder_sanitizes_failed_phase_without_masking_exception():
    from cognitrix.tasks.metrics import (
        TaskRunMetricError,
        TaskRunPhaseRecorder,
        TaskRunPhaseStatus,
    )

    recorded = []

    class Repository:
        async def record_metric(self, _run_id, *, claim, metric):
            recorded.append(metric)
            return metric

    recorder = TaskRunPhaseRecorder(
        Repository(),
        run_id="run-1",
        claim=object(),
        error_classifier=lambda _exc: TaskRunMetricError.PROVIDER_ERROR,
    )

    with pytest.raises(RuntimeError, match="api_key=must-not-persist"):
        async with recorder.measure("evaluate"):
            raise RuntimeError("api_key=must-not-persist")

    assert len(recorded) == 1
    assert recorded[0].status == TaskRunPhaseStatus.FAILED
    assert recorded[0].error_code == TaskRunMetricError.PROVIDER_ERROR
    assert "error" not in recorded[0].model_dump()


@pytest.mark.asyncio
async def test_phase_recorder_reconstructs_completed_queue_duration():
    from cognitrix.tasks.metrics import TaskRunPhaseRecorder, TaskRunPhaseStatus

    recorded = []

    class Repository:
        async def record_metric(self, _run_id, *, claim, metric):
            recorded.append(metric)
            return metric

    recorder = TaskRunPhaseRecorder(
        Repository(),
        run_id="run-1",
        claim=object(),
    )
    await recorder.record_completed(
        "queue",
        started_at="2030-01-01 00:00:00",
        completed_at="2030-01-01 00:00:02",
    )

    assert len(recorded) == 1
    assert recorded[0].status == TaskRunPhaseStatus.COMPLETED
    assert recorded[0].duration_ms == 2000


@pytest.mark.asyncio
async def test_phase_metric_cost_round_trips_through_sqlite(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite
    from cognitrix.tasks.metrics import TaskRunPhaseMetric

    database = str(tmp_path / "task-metrics.db")
    if hasattr(DBMS, "initialize_async"):
        await DBMS.initialize_async("sqlite", database=database)
    else:
        DBMS.initialize("sqlite", database=database)
    _patch_odbms_sqlite()

    create = (
        getattr(TaskRunPhaseMetric, "_create_table_async", None)
        or TaskRunPhaseMetric.create_table
    )
    await create()

    metric = TaskRunPhaseMetric(
        run_id="run-1",
        phase="evaluate",
        status="completed",
        cost_usd=Decimal("0.0042"),
    )
    await metric.save()

    restored = await TaskRunPhaseMetric.get(metric.id)
    assert restored is not None
    assert restored.cost_usd == Decimal("0.0042")


@pytest.mark.asyncio
async def test_repository_records_metric_only_with_live_lease(metric_repository_db):
    from cognitrix.tasks.metrics import TaskRunPhaseMetric
    from cognitrix.tasks.repository import LeaseClaim, RunRepository
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    run = TaskRun(
        task_id="task-metric-live",
        status=TaskRunStatus.RUNNING,
        lease_owner="worker-a",
        lease_generation=2,
        lease_expires_at="2999-01-01 00:00:00",
    )
    await run.save()
    claim = LeaseClaim(run_id=run.id, owner="worker-a", generation=2)
    metric = TaskRunPhaseMetric(
        run_id=run.id,
        phase="evaluate",
        status="completed",
        llm_calls=1,
        cost_usd=Decimal("0.0042"),
    )

    stored = await RunRepository().record_metric(
        run.id,
        claim=claim,
        metric=metric,
    )

    assert stored.id
    assert stored.run_id == run.id
    assert stored.llm_calls == 1
    assert stored.cost_usd == Decimal("0.0042")


@pytest.mark.asyncio
async def test_recovery_fences_metric_insert_after_worker_preflight(
    metric_repository_db,
):
    import asyncio

    from cognitrix.tasks.metrics import TaskRunPhaseMetric
    from cognitrix.tasks.recovery import recover_stale_runs
    from cognitrix.tasks.repository import LeaseClaim, LeaseLost, RunRepository
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    run = TaskRun(
        task_id="task-metric-stale",
        status=TaskRunStatus.RUNNING,
        lease_owner="worker-a",
        lease_generation=1,
        lease_expires_at="2999-01-01 00:00:00",
    )
    await run.save()
    claim = LeaseClaim(run_id=run.id, owner="worker-a", generation=1)
    repository = RunRepository()
    preflight_complete = asyncio.Event()
    let_worker_continue = asyncio.Event()
    original = repository._require_step_write

    async def pause_after_preflight(run_id, claim):
        current = await original(run_id, claim)
        preflight_complete.set()
        await let_worker_continue.wait()
        return current

    repository._require_step_write = pause_after_preflight
    stale_worker = asyncio.create_task(
        repository.record_metric(
            run.id,
            claim=claim,
            metric=TaskRunPhaseMetric(run_id=run.id, phase="step"),
        )
    )

    await asyncio.wait_for(preflight_complete.wait(), timeout=1)
    await TaskRun.update_one(
        {"id": run.id},
        {"lease_expires_at": "2000-01-01 00:00:00"},
    )
    await recover_stale_runs(repository=RunRepository())
    let_worker_continue.set()

    with pytest.raises(LeaseLost):
        await stale_worker

    assert await TaskRunPhaseMetric.all() == []


@pytest.mark.asyncio
async def test_expired_unrecovered_lease_rejects_metric_insert(
    metric_repository_db,
):
    from cognitrix.tasks.metrics import TaskRunPhaseMetric
    from cognitrix.tasks.repository import LeaseClaim, LeaseLost, RunRepository
    from cognitrix.tasks.run import TaskRun, TaskRunStatus

    run = TaskRun(
        task_id="task-metric-expired",
        status=TaskRunStatus.RUNNING,
        lease_owner="worker-a",
        lease_generation=1,
        lease_expires_at="2000-01-01 00:00:00",
    )
    await run.save()
    claim = LeaseClaim(run_id=run.id, owner="worker-a", generation=1)

    with pytest.raises(LeaseLost):
        await RunRepository().record_metric(
            run.id,
            claim=claim,
            metric=TaskRunPhaseMetric(run_id=run.id, phase="step"),
        )

    assert await TaskRunPhaseMetric.all() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "raw prompt content"),
        ("status", "whatever"),
        ("error_code", "provider said api_key=secret"),
        ("prompt_tokens", -1),
        ("cost_usd", Decimal("-0.1")),
    ],
)
def test_phase_metric_rejects_unbounded_or_negative_values(field, value):
    values = {"run_id": "run-1", "phase": "plan", field: value}
    with pytest.raises(ValidationError):
        TaskRunPhaseMetric = __import__(
            "cognitrix.tasks.metrics", fromlist=["TaskRunPhaseMetric"]
        ).TaskRunPhaseMetric
        TaskRunPhaseMetric(**values)


def test_utc_now_uses_stable_database_format():
    from cognitrix.tasks.run import RUN_TIMESTAMP_FORMAT, utc_now

    value = utc_now()

    parsed = datetime.strptime(value, RUN_TIMESTAMP_FORMAT)
    assert parsed.tzinfo is None
