import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from cognitrix.common.security import get_current_user
from cognitrix.tasks import Task
from cognitrix.tasks.base import TaskStatus
from cognitrix.tasks.run import TaskRun, TaskRunStatus

from ...celery_worker import broker_available, ensure_local_worker, run_task

_ACTIVE_RUN_STATUSES = (TaskRunStatus.RUNNING, TaskRunStatus.CANCELLING)


def _run_summary(run: TaskRun) -> dict:
    """Run projection for list views: full per-step results (~8KB each) are
    execution-side data (dependency prompts, resume) — polling clients only
    need statuses. Long descriptions are trimmed too."""
    data = run.json()
    data['plan'] = [
        {
            'index': s.get('index'),
            'title': s.get('title'),
            'description': (s.get('description') or '')[:200],
            'agent_name': s.get('agent_name'),
            'dependencies': s.get('dependencies') or [],
            'status': s.get('status'),
            'attempts': s.get('attempts'),
            'gate': s.get('gate'),
        }
        for s in (run.plan or [])
    ]
    return data


async def _active_run(task_id: str) -> TaskRun | None:
    runs = await TaskRun.find({'task_id': task_id})
    return next((r for r in runs if r.status in _ACTIVE_RUN_STATUSES), None)

logger = logging.getLogger('cognitrix.log')

tasks_api = APIRouter(
    prefix='/tasks',
    dependencies=[Depends(get_current_user)]
)

@tasks_api.get('')
async def list_tasks():
    tasks = await Task.all()
    return [task.json() for task in tasks]

@tasks_api.post('')
async def save_task(request: Request, task: Task, background_tasks: BackgroundTasks):
    await task.save()

    if task.autostart:
        background_tasks.add_task(task.start)

    return task.json()

@tasks_api.get('/start/{task_id}')
async def update_task_status(request: Request, task_id: str, resume: bool = False):
    task = await Task.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Guard both signals: an active TaskRun AND task IN_PROGRESS — the latter
    # covers the enqueue→pickup window before the worker creates the run row
    # (a duplicate start there would execute the task twice).
    if task.status == TaskStatus.IN_PROGRESS or await _active_run(task_id) is not None:
        raise HTTPException(status_code=409, detail="Task already has an active run. Cancel it first.")

    # Enqueue on the Celery broker. If the broker is unreachable, surface a
    # clear 503 instead of a 500, and do NOT mark the task in-progress. Probe
    # first so a down broker returns promptly rather than blocking on retries.
    # On the filesystem fallback broker, ensure_local_worker spawns a consumer
    # if none is running; on Redis it is a no-op (workers are external).
    broker_down_detail = "Task queue unavailable. Is the Celery broker (Redis) running and a worker started?"
    if not await asyncio.to_thread(ensure_local_worker):
        raise HTTPException(
            status_code=503,
            detail="Task queue unavailable — the local fallback worker failed to start. Check celery-queue/worker.log in the cognitrix workdir.",
        )
    if not await asyncio.to_thread(broker_available):
        raise HTTPException(status_code=503, detail=broker_down_detail)
    try:
        result = run_task.apply_async(args=[task_id], kwargs={'resume': resume}, retry=False)
    except Exception as exc:
        # Log the exception type only — the message can embed the broker URL
        # (with credentials, if configured).
        logger.error("Failed to enqueue task %s: %s", task_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail=broker_down_detail)

    task.status = TaskStatus.IN_PROGRESS
    task.pid = result.id
    await task.save()
    return task.json()


@tasks_api.post('/{task_id}/cancel')
async def cancel_task(task_id: str):
    task = await Task.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    run = await _active_run(task_id)
    if run is not None:
        if run.status == TaskRunStatus.CANCELLING:
            # Second cancel = force-finalize: a dead worker never honors the
            # flag, and a stuck 'cancelling' run would 409-block ▶ Run forever.
            # Compare-and-set: a worker finishing in this window writes a
            # terminal status that must not be relabeled 'cancelled'.
            fresh = await TaskRun.get(run.id)
            if fresh and fresh.status == TaskRunStatus.CANCELLING:
                await TaskRun.update_one({'id': run.id}, {
                    'status': TaskRunStatus.CANCELLED.value,
                    'error': 'force-cancelled (worker did not respond)',
                })
                task.status = TaskStatus.CANCELLED
                await task.save()
            fresh = await TaskRun.get(run.id)
            return fresh.json() if fresh else run.json()
        # Partial update only — a full-row save here would clobber plan/step
        # statuses the worker wrote since our read.
        await TaskRun.update_one({'id': run.id}, {'status': TaskRunStatus.CANCELLING.value})
        fresh = await TaskRun.get(run.id)
        return fresh.json() if fresh else run.json()

    if task.status == TaskStatus.IN_PROGRESS:
        # Enqueued but never picked up (or the worker died pre-run): cancel the
        # task directly; the orchestrator's entry guard makes a late pickup a
        # no-op and the prerun guard won't resurrect it.
        task.status = TaskStatus.CANCELLED
        await task.save()
        return task.json()

    raise HTTPException(status_code=409, detail="Nothing to cancel — no active run.")


@tasks_api.get('/{task_id}/runs')
async def list_task_runs(task_id: str):
    runs = await TaskRun.find({'task_id': task_id})
    runs.sort(key=lambda r: r.json().get('created_at') or '', reverse=True)
    return [_run_summary(r) for r in runs]

@tasks_api.get('/{task_id}')
async def load_task(task_id: str):
    task = await Task.get(task_id)
    return task.json() if task else {}

@tasks_api.delete('/{task_id}')
async def delete_task(task_id: str):
    task = await Task.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await Task.delete_many({'id': task_id})
    return {'message': 'Task deleted successfully'}
