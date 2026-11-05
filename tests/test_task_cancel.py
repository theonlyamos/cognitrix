"""Race regressions for task-run cancellation routes."""

import copy
from types import SimpleNamespace

import cognitrix.api.routes.tasks as task_routes
import pytest
from fastapi import HTTPException
from cognitrix.common.security import AuthContext
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.run import TaskRun, TaskRunStatus


def _jwt_ctx() -> AuthContext:
    return AuthContext(user=SimpleNamespace(id='user-1'), api_key=None)


def _task() -> Task:
    task = Task(title='Task', description='Work', status=TaskStatus.IN_PROGRESS)
    task.id = 'task-1'
    return task


def _run(status: TaskRunStatus, *, result: str | None = None) -> TaskRun:
    run = TaskRun(
        task_id='task-1',
        status=status,
        result=result,
        acl_version=1,
    )
    run.id = 'run-1'
    return run


def _stored(value):
    return value.value if hasattr(value, 'value') else value


class _RunStore:
    """Minimal status-CAS store for the authoritative run row."""

    def __init__(self, run: TaskRun):
        self.run = run

    async def get(self, _run_id: str) -> TaskRun:
        return copy.deepcopy(self.run)

    async def update_one(self, query: dict, values: dict) -> int:
        matches = all(
            _stored(getattr(self.run, key, None)) == _stored(expected)
            for key, expected in query.items()
        )
        if not matches:
            return 0
        for key, value in values.items():
            if key == 'status':
                value = TaskRunStatus(value)
            setattr(self.run, key, value)
        return 1


def _install_route_state(monkeypatch, task: Task, stale_run: TaskRun, store: _RunStore):
    task_writes = []

    async def get_task(_task_id):
        return task

    async def active_run(_task_id):
        return stale_run

    async def save_task(self):
        task_writes.append(('save', self.status))

    async def update_task(query, values):
        task_writes.append(('update', query, values))
        return 1

    monkeypatch.setattr(Task, 'get', staticmethod(get_task))
    monkeypatch.setattr(task_routes, '_active_run', active_run)
    monkeypatch.setattr(TaskRun, 'get', staticmethod(store.get))
    monkeypatch.setattr(TaskRun, 'update_one', staticmethod(store.update_one))
    monkeypatch.setattr(Task, 'save', save_task)
    monkeypatch.setattr(Task, 'update_one', staticmethod(update_task))
    return task_writes


async def test_cancel_running_does_not_overwrite_terminal_race(monkeypatch):
    task = _task()
    stale_run = _run(TaskRunStatus.RUNNING)
    store = _RunStore(_run(TaskRunStatus.COMPLETED, result='worker result'))
    task_writes = _install_route_state(monkeypatch, task, stale_run, store)

    response = await task_routes.cancel_task(task.id, _jwt_ctx())

    assert response['id'] == stale_run.id
    assert response['task_id'] == task.id
    assert response['status'] == TaskRunStatus.COMPLETED
    assert 'result' not in response
    assert store.run.status == TaskRunStatus.COMPLETED
    assert task.status == TaskStatus.IN_PROGRESS
    assert task_writes == []


async def test_force_cancel_does_not_overwrite_terminal_race(monkeypatch):
    task = _task()
    stale_run = _run(TaskRunStatus.CANCELLING)
    store = _RunStore(_run(TaskRunStatus.COMPLETED, result='worker result'))
    task_writes = _install_route_state(monkeypatch, task, stale_run, store)

    response = await task_routes.cancel_task(task.id, _jwt_ctx())

    assert response['id'] == stale_run.id
    assert response['task_id'] == task.id
    assert response['status'] == TaskRunStatus.COMPLETED
    assert 'result' not in response
    assert store.run.status == TaskRunStatus.COMPLETED
    assert task.status == TaskStatus.IN_PROGRESS
    assert task_writes == []


async def test_active_run_lookup_includes_queued(monkeypatch):
    queued = _run(TaskRunStatus.QUEUED)

    async def find_runs(query):
        assert query == {'task_id': queued.task_id}
        return [queued]

    monkeypatch.setattr(TaskRun, 'find', staticmethod(find_runs))

    assert await task_routes._active_run(queued.task_id) is queued


async def test_cancel_queued_run_terminalizes_it_before_worker_claim(monkeypatch):
    task = _task()
    stale_run = _run(TaskRunStatus.QUEUED)
    store = _RunStore(_run(TaskRunStatus.QUEUED))
    task_writes = _install_route_state(monkeypatch, task, stale_run, store)

    class FakeRepository:
        def force_cancel_ready(self, _run):
            return False

        async def request_cancel(self, run_id):
            updated = await store.update_one(
                {'id': run_id, 'status': TaskRunStatus.QUEUED.value},
                {
                    'status': TaskRunStatus.CANCELLED.value,
                    'error': 'cancelled by user',
                    'completed_at': '2030-06-01 12:00:00',
                },
            )
            return await store.get(run_id) if updated == 1 else None

        async def force_cancel(self, run_id):
            return await self.request_cancel(run_id)

    monkeypatch.setattr(task_routes, 'RunRepository', FakeRepository, raising=False)

    response = await task_routes.cancel_task(task.id, _jwt_ctx())

    assert response['status'] == TaskRunStatus.CANCELLED
    assert response['completed_at'] == '2030-06-01 12:00:00'
    assert store.run.status == TaskRunStatus.CANCELLED
    assert any(
        write[0] == 'update'
        and write[2].get('status') == TaskStatus.CANCELLED.value
        for write in task_writes
    )


async def test_cancel_active_run_uses_immutable_run_acl(monkeypatch):
    task = _task()
    task.team_id = 'team-current'
    run = _run(TaskRunStatus.RUNNING)
    run.acl_team_id = 'team-at-start'

    async def get_task(_task_id):
        return task

    async def active_run(_task_id):
        return run

    class RejectRepositoryUse:
        async def request_cancel(self, _run_id):
            raise AssertionError('denied caller must not mutate the run')

    key = SimpleNamespace(
        id='key-1',
        team_allowed=lambda team_id: team_id == 'team-current',
        agent_allowed=lambda _agent_id: True,
    )
    ctx = AuthContext(user=SimpleNamespace(id='user-1'), api_key=key)
    monkeypatch.setattr(Task, 'get', staticmethod(get_task))
    monkeypatch.setattr(task_routes, '_active_run', active_run)
    monkeypatch.setattr(task_routes, 'RunRepository', RejectRepositoryUse)

    with pytest.raises(HTTPException) as exc_info:
        await task_routes.cancel_task(task.id, ctx)

    assert exc_info.value.status_code == 403
