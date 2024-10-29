import asyncio
from celery import Celery
from cognitrix.providers.session import Session
from cognitrix.tasks.base import Task
from cognitrix.teams.base import Team
from cognitrix.config import run_configure

run_configure()

celery = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

@celery.task(name="generic_task")
def run_task(task_id): 
    task = Task.get(task_id)  # Change here: use asyncio.run

    result = None
    
    if task:
        result = asyncio.run(task.start())  # Call the method here
    
    return result

@celery.task(name="team_task")
def run_team_task(team_id: str, task_id: str): 
    team = Team.get(team_id)
    task = Task.get(task_id)
    if task and team:
        from cognitrix.utils.core import get_websocket_manager
        websocket_manager = get_websocket_manager(task_id)
        if websocket_manager:
            try:    
                team.assign_task(task.id)
                task_session = Session(team_id=team.id, task_id=task.id)
                task_session.save()
                # await team.work_on_task(task.id, task_session, self)
            finally:
                from cognitrix.utils.core import unregister_websocket_manager
                unregister_websocket_manager(task_id)

            atask = asyncio.create_task(team.work_on_task(task.id, task_session, websocket_manager))  # Call the method here
    
    return atask

if __name__ == '__main__':
    celery.start()