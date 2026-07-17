"""RED compatibility contract for Celery's run-id-only task payload."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from cognitrix.tasks.run import TaskRunStatus


def test_worker_process_init_fails_closed_when_database_initialization_fails(
    monkeypatch,
):
    monkeypatch.setenv("CELERY_WORKER_MODE", "1")
    import cognitrix.celery_worker as worker

    async def fail_initialization():
        raise RuntimeError("schema reconciliation failed")

    monkeypatch.setattr(worker, "initialize_database", fail_initialization)
    monkeypatch.setattr(worker, "_run", asyncio.run)

    with pytest.raises(RuntimeError, match="schema reconciliation failed"):
        worker.init_worker_process()


def test_celery_task_loads_precreated_run_and_passes_it_to_task_start(monkeypatch):
    import cognitrix.celery_worker as worker
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun

    calls = []
    queued_run = SimpleNamespace(id='run-1', task_id='task-1')
    completed_run = SimpleNamespace(id='run-1')

    def get_run(run_id):
        calls.append(('get_run', run_id))
        return queued_run

    def get_task(task_id):
        calls.append(('get_task', task_id))

        def start(**kwargs):
            calls.append(('start', kwargs))
            return completed_run

        return SimpleNamespace(id=task_id, start=start)

    # Keep this a pure unit test: no worker loop, broker, database, or network.
    monkeypatch.setattr(worker, '_run', lambda value: value)
    monkeypatch.setattr(TaskRun, 'get', staticmethod(get_run))
    monkeypatch.setattr(Task, 'get', staticmethod(get_task))

    result = worker.run_task.run(queued_run.id)

    assert result == completed_run.id
    assert calls == [
        ('get_run', queued_run.id),
        ('get_task', queued_run.task_id),
        ('start', {'run': queued_run}),
    ]


def test_celery_task_missing_run_is_a_noop(monkeypatch):
    import cognitrix.celery_worker as worker
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun

    task_lookups = []
    monkeypatch.setattr(worker, '_run', lambda value: value)
    monkeypatch.setattr(TaskRun, 'get', staticmethod(lambda _run_id: None))
    monkeypatch.setattr(
        Task,
        'get',
        staticmethod(lambda task_id: task_lookups.append(task_id)),
    )

    assert worker.run_task.run('missing-run') is None
    assert task_lookups == []


def test_legacy_task_payload_is_adapted_to_one_precreated_run(monkeypatch):
    import cognitrix.celery_worker as worker
    from cognitrix.tasks.base import Task

    calls = []
    queued = SimpleNamespace(id='run-legacy', task_id='task-legacy')
    completed = SimpleNamespace(id='run-legacy')

    def get_task(task_id):
        calls.append(('get_task', task_id))

        def start(**kwargs):
            calls.append(('start', kwargs))
            return completed

        return SimpleNamespace(id=task_id, start=start)

    class FakeRepository:
        def create_queued(self, **kwargs):
            calls.append(('create_queued', kwargs))
            return queued

    monkeypatch.setattr(worker, '_run', lambda value: value)
    monkeypatch.setattr(Task, 'get', staticmethod(get_task))
    monkeypatch.setattr(worker, 'RunRepository', FakeRepository, raising=False)

    result = worker.run_legacy_task.run('task-legacy', resume=False)

    assert result == completed.id
    assert calls == [
        ('get_task', 'task-legacy'),
        ('create_queued', {'task_id': 'task-legacy', 'actor_key': 'system'}),
        ('start', {'run': queued}),
    ]


def test_legacy_resume_payload_targets_latest_resumable_run(monkeypatch):
    import cognitrix.celery_worker as worker
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun

    calls = []
    prior = SimpleNamespace(
        id='run-failed',
        status=TaskRunStatus.FAILED,
        created_at='2030-01-02 00:00:00',
        json=lambda: {'created_at': '2030-01-02 00:00:00'},
    )
    queued = SimpleNamespace(
        id='run-resumed',
        task_id='task-legacy',
        resume_from_run_id=prior.id,
    )
    completed = SimpleNamespace(id=queued.id)

    def get_task(task_id):
        def start(**kwargs):
            calls.append(('start', kwargs))
            return completed

        return SimpleNamespace(id=task_id, start=start)

    class FakeRepository:
        def create_queued(self, **kwargs):
            calls.append(('create_queued', kwargs))
            return queued

    monkeypatch.setattr(worker, '_run', lambda value: value)
    monkeypatch.setattr(Task, 'get', staticmethod(get_task))
    monkeypatch.setattr(TaskRun, 'find', staticmethod(lambda _query: [prior]))
    monkeypatch.setattr(worker, 'RunRepository', FakeRepository, raising=False)

    result = worker.run_legacy_task.run('task-legacy', resume=True)

    assert result == completed.id
    assert calls == [
        (
            'create_queued',
            {
                'task_id': 'task-legacy',
                'actor_key': 'system',
                'resume_from_run_id': prior.id,
            },
        ),
        ('start', {'run': queued}),
    ]


def test_celery_task_terminalizes_run_when_owning_task_is_missing(monkeypatch):
    import cognitrix.celery_worker as worker
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun

    queued = SimpleNamespace(
        id='run-orphaned',
        task_id='missing-task',
        status=TaskRunStatus.QUEUED,
        error_code=None,
        error=None,
        completed_at=None,
    )
    mutations = []

    class FakeRepository:
        def mutate(self, run_id, **kwargs):
            mutations.append((run_id, kwargs))
            for key, value in kwargs['updates'].items():
                setattr(queued, key, value)
            return queued

    monkeypatch.setattr(worker, '_run', lambda value: value)
    monkeypatch.setattr(TaskRun, 'get', staticmethod(lambda _run_id: queued))
    monkeypatch.setattr(Task, 'get', staticmethod(lambda _task_id: None))
    monkeypatch.setattr(worker, 'RunRepository', FakeRepository, raising=False)

    assert worker.run_task.run(queued.id) is None
    assert queued.status == TaskRunStatus.FAILED.value
    assert queued.error_code == 'task_missing'
    assert queued.completed_at is not None
    assert datetime.strptime(queued.completed_at, '%Y-%m-%d %H:%M:%S')
    assert mutations[0][0] == queued.id
    assert mutations[0][1]['expected_statuses'] == {TaskRunStatus.QUEUED}


def test_postrun_projects_authoritative_recovered_run_not_celery_success(
    monkeypatch,
):
    import cognitrix.celery_worker as worker
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.run import TaskRun

    failed = SimpleNamespace(
        id='run-timeout',
        task_id='task-timeout',
        status=TaskRunStatus.FAILED,
    )
    calls = []
    monkeypatch.setattr(worker, '_run', lambda value: value)
    monkeypatch.setattr(
        TaskRun,
        'find_one',
        staticmethod(lambda query: failed if query == {'queue_job_id': 'job-1'} else None),
    )
    monkeypatch.setattr(
        Task,
        'find_one',
        staticmethod(lambda _query: (_ for _ in ()).throw(
            AssertionError('legacy Celery state must not project the task')
        )),
    )
    monkeypatch.setattr(
        worker,
        'project_task_status',
        lambda task_id: calls.append(('project', task_id)),
    )
    monkeypatch.setattr(
        worker,
        'deliver_completion_notification',
        lambda run_id: calls.append(('deliver', run_id)),
    )

    worker.task_postrun_handler(
        'job-1',
        SimpleNamespace(),
        state='SUCCESS',
    )

    assert calls == [
        ('project', failed.task_id),
        ('deliver', failed.id),
    ]
