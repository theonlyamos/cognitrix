"""Race regressions for task-run cancellation routes."""

import copy
from types import SimpleNamespace

import cognitrix.api.routes.tasks as task_routes
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
    run = TaskRun(task_id='task-1', status=status, result=result)
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
    assert response['result'] == 'worker result'
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
    assert response['result'] == 'worker result'
    assert store.run.status == TaskRunStatus.COMPLETED
    assert task.status == TaskStatus.IN_PROGRESS
    assert task_writes == []
