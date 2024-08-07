import asyncio
from celery import Celery
from threading import Thread
from cognitrix.tasks import Task

celery = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

@celery.task(name="generic_task")
def run_task(task_id): 
    task = asyncio.run(Task.get(task_id))  # Change here: use asyncio.run
    print(task)
    result = None
    
    if task:
        # loop = asyncio.get_event_loop()
        result = asyncio.run(task.start())  # Call the method here
    
    return result

def start_celery():
    celery.worker_main(argv=['worker', '--loglevel=info'])

celery_thread = Thread(target=start_celery)
celery_thread.daemon = True