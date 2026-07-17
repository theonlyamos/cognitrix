"""Lifecycle contracts for durable task-run recovery."""

import asyncio

import pytest


def test_settings_read_positive_task_recovery_interval(monkeypatch):
    from cognitrix.config import CognitrixSettings

    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("TASK_RECOVERY_INTERVAL_SECONDS", "12.5")
    monkeypatch.setattr(CognitrixSettings, "_ensure_directories", lambda self: None)

    configured = CognitrixSettings()

    assert configured.task_recovery_interval_seconds == 12.5

    for invalid in ("0", "nan", "inf"):
        monkeypatch.setenv("TASK_RECOVERY_INTERVAL_SECONDS", invalid)
        with pytest.raises(ValueError, match="TASK_RECOVERY_INTERVAL_SECONDS"):
            CognitrixSettings()


@pytest.mark.asyncio
async def test_recovery_pass_repairs_authoritative_state_before_outboxes(monkeypatch):
    from cognitrix.tasks import recovery

    calls = []

    class RecordingRepository:
        async def recover_outboxes(self):
            calls.append(("outboxes", self))
            return ["run-outbox"]

        async def recover_stale_reservations(
            self, *, reservation_timeout_seconds
        ):
            calls.append(
                ("reservations", self, reservation_timeout_seconds)
            )
            return ["missing-run"]

    repository = RecordingRepository()

    async def recover_stale_runs(*, repository, queue_timeout_seconds):
        calls.append(("stale", repository, queue_timeout_seconds))
        return [type("Run", (), {"task_id": "task-stale"})()]

    async def reconcile_terminal_task_statuses(*, repository):
        calls.append(("reconcile", repository))
        return ["task-stale"]

    async def recover_completion_notifications(*, repository):
        calls.append(("notifications", repository))
        return ["run-stale"]

    monkeypatch.setattr(recovery, "recover_stale_runs", recover_stale_runs)
    monkeypatch.setattr(
        recovery,
        "reconcile_terminal_task_statuses",
        reconcile_terminal_task_statuses,
    )
    monkeypatch.setattr(
        recovery,
        "recover_completion_notifications",
        recover_completion_notifications,
    )

    outboxes, stale = await recovery.run_recovery_pass(
        repository=repository,
        queue_timeout_seconds=45,
    )

    assert outboxes == ["run-outbox"]
    assert [run.task_id for run in stale] == ["task-stale"]
    assert calls == [
        ("reservations", repository, 45),
        ("stale", repository, 45),
        ("reconcile", repository),
        ("notifications", repository),
        ("outboxes", repository),
    ]


@pytest.mark.asyncio
async def test_recovery_pass_does_not_fail_when_optional_outbox_delivery_fails(
    monkeypatch,
):
    from cognitrix.tasks import recovery

    calls = []

    class PoisonOutboxRepository:
        async def recover_stale_reservations(self, **_kwargs):
            calls.append("reservations")
            return []

        async def recover_outboxes(self):
            calls.append("outboxes")
            raise RuntimeError("poison event envelope")

    repository = PoisonOutboxRepository()

    async def recover_stale_runs(**_kwargs):
        calls.append("stale")
        return []

    async def reconcile_terminal_task_statuses(**_kwargs):
        calls.append("reconcile")
        return []

    async def recover_completion_notifications(**_kwargs):
        calls.append("notifications")
        return []

    monkeypatch.setattr(recovery, "recover_stale_runs", recover_stale_runs)
    monkeypatch.setattr(
        recovery,
        "reconcile_terminal_task_statuses",
        reconcile_terminal_task_statuses,
    )
    monkeypatch.setattr(
        recovery,
        "recover_completion_notifications",
        recover_completion_notifications,
    )

    outboxes, stale = await recovery.run_recovery_pass(repository=repository)

    assert outboxes == []
    assert stale == []
    assert calls == [
        "reservations",
        "stale",
        "reconcile",
        "notifications",
        "outboxes",
    ]


@pytest.mark.asyncio
async def test_recovery_loop_waits_retries_after_failure_and_is_cancellable(monkeypatch):
    from cognitrix.tasks import recovery

    calls = 0
    recovered = asyncio.Event()

    async def run_recovery_pass(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient recovery failure")
        recovered.set()
        return [], []

    monkeypatch.setattr(recovery, "run_recovery_pass", run_recovery_pass)

    loop = asyncio.create_task(recovery.recovery_loop(interval_seconds=0.01))
    await asyncio.sleep(0)
    assert calls == 0

    await asyncio.wait_for(recovered.wait(), timeout=1)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop

    assert calls >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [0, float("nan"), float("inf")])
async def test_recovery_loop_rejects_invalid_interval(interval):
    from cognitrix.tasks.recovery import recovery_loop

    with pytest.raises(ValueError, match="interval_seconds"):
        await recovery_loop(interval_seconds=interval)


@pytest.mark.asyncio
async def test_api_lifespan_runs_initial_recovery_and_stops_one_periodic_loop(
    monkeypatch,
    tmp_path,
):
    from cognitrix import config

    build_dir = tmp_path / "dist"
    for directory in ("css", "assets", "webfonts", "fonts"):
        (build_dir / directory).mkdir(parents=True)
    monkeypatch.setattr(config, "FRONTEND_BUILD_DIR", build_dir)

    from cognitrix.api import main

    events = []
    scheduler_started = asyncio.Event()
    recovery_started = asyncio.Event()

    async def initialize_database():
        events.append("database")

    async def run_recovery_pass():
        events.append("startup-recovery")

    async def scheduler_loop():
        events.append("scheduler-started")
        scheduler_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("scheduler-stopped")

    async def recovery_loop(*, interval_seconds):
        events.append(("recovery-started", interval_seconds))
        recovery_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("recovery-stopped")

    monkeypatch.setattr(main, "initialize_database", initialize_database)
    monkeypatch.setattr(main, "run_recovery_pass", run_recovery_pass)
    monkeypatch.setattr(main, "scheduler_loop", scheduler_loop)
    monkeypatch.setattr(main, "recovery_loop", recovery_loop)
    monkeypatch.setattr(main.settings, "task_recovery_interval_seconds", 17.5)

    async with main.lifespan(main.app):
        await asyncio.wait_for(scheduler_started.wait(), timeout=1)
        await asyncio.wait_for(recovery_started.wait(), timeout=1)
        assert events[:2] == ["database", "startup-recovery"]
        assert events.count(("recovery-started", 17.5)) == 1

    assert "scheduler-stopped" in events
    assert "recovery-stopped" in events
