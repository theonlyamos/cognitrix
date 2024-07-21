import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
async def save_task(request: Request, task: Task):
    data = await request.json()
    await task.save()
    
    return JSONResponse(task.dict())

@tasks_api.get('/{task_id}')
async def load_task(task_id: str):
    task = await Task.get(task_id)
    
    response = {}
    if task:
        response = task.dict()
    
    return JSONResponse(response)