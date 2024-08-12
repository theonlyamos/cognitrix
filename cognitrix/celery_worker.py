import asyncio
from celery import Celery
from cognitrix.tasks import Task

celery = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

@celery.task(name="generic_task")
def run_task(task_id): 
    task = asyncio.run(Task.get(task_id))  # Change here: use asyncio.run

    result = None
    
    if task:
        result = asyncio.run(task.start())  # Call the method here
    
    return result

if __name__ == '__main__':
    celery.start()