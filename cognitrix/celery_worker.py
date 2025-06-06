import asyncio
from celery import Celery
from celery.signals import task_prerun, task_postrun, task_success, task_failure
from cognitrix.sessions.base import Session
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.teams.base import Team
from cognitrix.config import run_configure
import logging

run_configure()

celery = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger('cognitrix.log')

celery.conf.update(
    worker_send_task_events=True,
    task_send_sent_event=True,
    task_track_started=True,
    task_track_received=True
)

@task_prerun.connect
def task_prerun_handler(task_id, task, *args, **kwargs):
    logger.info(f"Task started: {task_id}")
    # Update task status in your database if needed
    loop = asyncio.get_event_loop()
    task_obj = loop.run_until_complete(Task.find_one({'pid': task_id}))
    if task_obj:
        task_obj.status = TaskStatus.IN_PROGRESS
        loop.run_until_complete(task_obj.save())


@task_postrun.connect
def task_postrun_handler(task_id, task, *args, retval=None, state=None, **kwargs):
    logger.info(f"Task completed: {task_id}, State: {state}")
    # Update task status in your database if needed
    # get async loop
    loop = asyncio.get_event_loop()
    task_obj = loop.run_until_complete(Task.find_one({'pid': task_id}))
    if task_obj:
        loop.run_until_complete(task_obj.save())


        task_obj.status = TaskStatus.COMPLETED if state == 'SUCCESS' else TaskStatus.PENDING
        loop.run_until_complete(task_obj.save())
        

@task_success.connect
def task_success_handler(sender=None, result=None, **kwargs):
    logger.info(f"Task succeeded: {sender.request.id}") # type: ignore

@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, **kwargs):
    logger.error(f"Task failed: {task_id}, Exception: {exception}")

@celery.task(name="generic_task")
def run_task(task_id): 
    loop = asyncio.get_event_loop()
    task = loop.run_until_complete(Task.get(task_id))  # Change here: use asyncio.run


    result = None
    
    if task:
        result = loop.run_until_complete(task.start())  # Call the method here

    return result

@celery.task(name="team_task")
def run_team_task(team_id: str, task_id: str): 
    loop = asyncio.get_event_loop()
    team = loop.run_until_complete(Team.get(team_id))
    task = loop.run_until_complete(Task.get(task_id))
    if task and team:
        from cognitrix.utils.core import get_websocket_manager

        websocket_manager = get_websocket_manager(task_id)
        if websocket_manager:
            try:    
                loop.run_until_complete(team.assign_task(task.id))
                task_session = Session(team_id=team.id, task_id=task.id)
                loop.run_until_complete(task_session.save())
                # await team.work_on_task(task.id, task_session, self)
            finally:
                from cognitrix.utils.core import unregister_websocket_manager

                unregister_websocket_manager(task_id)

            return loop.run_until_complete(team.work_on_task(task.id, task_session, websocket_manager))  # Call the method here


if __name__ == '__main__':
    celery.start()