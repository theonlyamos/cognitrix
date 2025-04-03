import json
from fastapi import APIRouter, Depends, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from celery.result import AsyncResult

from cognitrix.common.security import get_current_user
from cognitrix.tasks.base import TaskStatus

from ...celery_worker import run_task
from cognitrix.sessions.base import Session
from cognitrix.tasks import Task
from ...providers import LLM

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
async def update_task_status(request: Request, task_id: str, background_tasks: BackgroundTasks):
    
    response = {}

    if task_id:
        task = await Task.get(task_id)

        if task:
            result = run_task.delay(task_id)
            print('[+] Task process', result)
            task.status = TaskStatus.IN_PROGRESS
            task.pid = result.id
            await task.save()
            response = task.json()
    
    return response

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
    await Task.remove(query={'id': task_id})
    return {'message': 'Task deleted successfully'}
