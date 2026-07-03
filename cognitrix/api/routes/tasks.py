import asyncio
import logging

from celery.result import AsyncResult
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from cognitrix.common.security import get_current_user
from cognitrix.tasks import Task
from cognitrix.tasks.base import TaskStatus

from ...celery_worker import broker_available, run_task

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
async def update_task_status(request: Request, task_id: str):
    task = await Task.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Enqueue on the Celery broker. If the broker (Redis) is unreachable, surface
    # a clear 503 instead of a 500, and do NOT mark the task in-progress. Probe
    # first so a down broker returns promptly rather than blocking on retries.
    broker_down_detail = "Task queue unavailable. Is the Celery broker (Redis) running and a worker started?"
    if not await asyncio.to_thread(broker_available):
        raise HTTPException(status_code=503, detail=broker_down_detail)
    try:
        result = run_task.apply_async(args=[task_id], retry=False)
    except Exception as exc:
        # Log the exception type only — the message can embed the broker URL
        # (with credentials, if configured).
        logger.error("Failed to enqueue task %s: %s", task_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail=broker_down_detail)

    task.status = TaskStatus.IN_PROGRESS
    task.pid = result.id
    await task.save()
    return task.json()

@tasks_api.get('/{task_id}')
async def load_task(task_id: str):
    task = await Task.get(task_id)
    response = {}
    if task:
        if task.pid:
            task_result = AsyncResult(task.pid)
            print(task_result.result, task_result.state, task_result.info, task_result.traceback)
            print('[+] Task Result',task_result)
        response = task.json()

    return response

@tasks_api.delete('/{task_id}')
async def delete_task(task_id: str):
    task = await Task.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await Task.delete_many({'id': task_id})
    return {'message': 'Task deleted successfully'}
