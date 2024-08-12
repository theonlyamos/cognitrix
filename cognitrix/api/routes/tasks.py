import json
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from celery.result import AsyncResult

from ...celery_worker import run_task
from cognitrix.llms.session import Session
from cognitrix.tasks import Task
from ...llms import LLM

tasks_api = APIRouter(
    prefix='/tasks'
)

@tasks_api.get('')
async def list_tasks():
    tasks = await Task.list_tasks()
    response = [task.dict() for task in tasks]
    return JSONResponse(response)

@tasks_api.post('')
async def save_task(request: Request, task: Task, background_tasks: BackgroundTasks):
    await task.save()
    
    if task.autostart:
        background_tasks.add_task(task.start)
    
    return JSONResponse(task.dict())

@tasks_api.get('/start/{task_id}')
async def update_task_status(request: Request, task_id: str, background_tasks: BackgroundTasks):
    
    response = {}

    if task_id:
        task = await Task.get(task_id)

        if task:
            result = run_task.delay(task_id)
            print('[+] Task process', result)
            task.status = 'in-progress'
            task.pid = result.id
            await task.save()
            response = task.dict()
    
    return JSONResponse(response)

@tasks_api.get('/{task_id}')
async def load_task(task_id: str):
    task = await Task.get(task_id)
    response = {}
    if task:
        if task.pid:
            task_result = AsyncResult(task.pid)
            print('[+] Task Result',task_result)
        response = task.dict()
    
    return JSONResponse(response)