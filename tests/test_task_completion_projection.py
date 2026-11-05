from types import SimpleNamespace

import pytest

from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.completion import reconcile_terminal_task_statuses
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus


@pytest.mark.asyncio
async def test_terminal_projection_reconciliation_repairs_prior_crash(monkeypatch):
    head = TaskRunHead(
        _id="task-1",
        task_id="task-1",
        latest_run_id="run-1",
    )
    run = TaskRun(
        _id="run-1",
        task_id="task-1",
        status=TaskRunStatus.COMPLETED,
    )
    task = Task(
        _id="task-1",
        title="Task",
        description="Work",
        status=TaskStatus.IN_PROGRESS,
    )
    writes = []

    async def all_heads():
        return [head]

    async def get_run(_run_id):
        return run

    async def get_task(_task_id):
        return task

    async def update_task(query, values):
        writes.append((query, values))
        task.status = TaskStatus(values["status"])
        return 1

    monkeypatch.setattr(TaskRunHead, "all", staticmethod(all_heads))
    monkeypatch.setattr(TaskRun, "get", staticmethod(get_run))
    monkeypatch.setattr(Task, "get", staticmethod(get_task))
    monkeypatch.setattr(Task, "update_one", staticmethod(update_task))

    class Repository:
        async def latest_run(self, task_id):
            assert task_id == "task-1"
            return run

    repaired = await reconcile_terminal_task_statuses(repository=Repository())

    assert repaired == ["task-1"]
    assert writes == [
        ({"id": "task-1"}, {"status": TaskStatus.COMPLETED.value}),
    ]


@pytest.mark.asyncio
async def test_recovery_pass_reconciles_already_terminal_runs(monkeypatch):
    import cognitrix.tasks.recovery as recovery

    calls = []

    class Repository:
        async def recover_outboxes(self):
            return []

        async def recover_stale_reservations(self, **_kwargs):
            return []

    async def no_stale(**_kwargs):
        return []

    async def reconcile(*, repository):
        calls.append(repository)
        return ["task-from-prior-crash"]

    async def no_notifications(**_kwargs):
        return []

    repository = Repository()
    monkeypatch.setattr(recovery, "recover_stale_runs", no_stale)
    monkeypatch.setattr(recovery, "reconcile_terminal_task_statuses", reconcile, raising=False)
    monkeypatch.setattr(recovery, "recover_completion_notifications", no_notifications)

    await recovery.run_recovery_pass(repository=repository)

    assert calls == [repository]
