"""RED public contracts for durable task-run creation and identity."""

from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from cognitrix.common.security import AuthContext
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.tasks.events import TaskRunEvent
from cognitrix.tasks.repository import RunRepository
from cognitrix.tasks.run import TaskRun, TaskRunHead, TaskRunStatus


@pytest.fixture
async def launch_db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    database = str(tmp_path / 'task-run-launch.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=database)
    else:
        DBMS.initialize('sqlite', database=database)
    _patch_odbms_sqlite()
    for model in (Task, TaskRun, TaskRunHead, TaskRunEvent):
        create = getattr(model, '_create_table_async', None) or model.create_table
        await create()


def _ctx():
    return AuthContext(user=SimpleNamespace(id='user-1'), api_key=None)


async def test_start_response_keeps_exact_run_id_after_fast_completion(
    launch_db,
    monkeypatch,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(title='fast task', description='finishes before response')
    await task.save()
    published_run_ids = []

    def publish(*args, **kwargs):
        run_id = kwargs['args'][0]
        published_run_ids.append(run_id)
        return SimpleNamespace(id='celery-fast')

    async def finish_before_enqueue_returns(self, run_id, job_id):
        await TaskRun.update_one(
            {'id': run_id},
            {
                'status': TaskRunStatus.COMPLETED.value,
                'queue_job_id': job_id,
                'completed_at': '2030-01-01 00:00:00',
            },
        )
        await Task.update_one(
            {'id': task.id},
            {'status': TaskStatus.COMPLETED.value},
        )
        return await TaskRun.get(run_id)

    monkeypatch.setattr(routes, 'ensure_local_worker', lambda: True)
    monkeypatch.setattr(routes, 'broker_available', lambda: True)
    monkeypatch.setattr(routes.run_task, 'apply_async', publish)
    monkeypatch.setattr(
        RunRepository,
        'attach_queue_job_id',
        finish_before_enqueue_returns,
    )

    response = await routes.start_task_run(task.id, None, _ctx())

    assert len(published_run_ids) == 1
    assert response['run_id'] == published_run_ids[0]
    assert response['status'] == TaskRunStatus.COMPLETED
    stored_task = await Task.get(task.id)
    assert stored_task.status == TaskStatus.COMPLETED
    assert stored_task.pid == 'celery-fast'


async def test_autostart_response_has_durable_queued_identity(
    launch_db,
    monkeypatch,
):
    import cognitrix.api.routes.tasks as routes

    task = Task(
        title='autostart task',
        description='return its durable identity',
        autostart=True,
    )
    background = BackgroundTasks()
    monkeypatch.setattr(routes, 'ensure_local_worker', lambda: True)
    monkeypatch.setattr(routes, 'broker_available', lambda: True)
    monkeypatch.setattr(
        routes.run_task,
        'apply_async',
        lambda *args, **kwargs: SimpleNamespace(id='celery-autostart'),
    )

    response = await routes.save_task(None, task, background, _ctx())

    assert response['run_id'] is not None
    run = await TaskRun.get(response['run_id'])
    assert run is not None
    assert run.task_id == task.id
    assert run.status == TaskRunStatus.QUEUED
