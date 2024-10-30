import json
from fastapi import APIRouter, Depends, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from celery.result import AsyncResult

from cognitrix.common.security import get_current_user
from cognitrix.tasks.base import TaskStatus

from ...celery_worker import run_task
from cognitrix.providers.session import Session
from cognitrix.tasks import Task
from ...providers import LLM

tasks_api = APIRouter(
    prefix='/tasks',
    dependencies=[Depends(get_current_user)]
)

@tasks_api.get('')
async def list_tasks():
    tasks = Task.all()
    response = [task.dict() for task in tasks]
    return JSONResponse(response)

@tasks_api.post('')
async def save_task(request: Request, task: Task, background_tasks: BackgroundTasks):
    task.save()
    
    if task.autostart:
        background_tasks.add_task(task.start)
    
    return JSONResponse(task.dict())

@tasks_api.get('/start/{task_id}')
async def update_task_status(request: Request, task_id: str, background_tasks: BackgroundTasks):
    
    response = {}

    if task_id:
        task = Task.get(task_id)

        if task:
            result = run_task.delay(task_id)
            print('[+] Task process', result)
            task.status = TaskStatus.IN_PROGRESS
            task.pid = result.id
            task.save()
            response = task.model_dump()
    
    return JSONResponse(response)

@tasks_api.get('/{task_id}')
async def load_task(task_id: str):
    task = Task.get(task_id)
    response = {}
    if task:
        if task.pid:
            task_result = AsyncResult(task.pid)
            print(task_result.result, task_result.state)
            print('[+] Task Result',task_result)
        response = task.model_dump()
    
    return JSONResponse(response)

@tasks_api.delete('/{task_id}')
async def delete_task(task_id: str):
    Task.remove(query={'id': task_id})
    return JSONResponse({'message': 'Task deleted successfully'})
