import asyncio
import logging
import os
import socket
from urllib.parse import urlparse

from celery import Celery
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    task_success,
    worker_process_init,
)

from cognitrix.config import initialize_database
from cognitrix.sessions.base import Session
from cognitrix.tasks.base import Task, TaskStatus
from cognitrix.teams.base import Team

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger('cognitrix.log')

celery = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

celery.conf.update(
    worker_send_task_events=True,
    task_send_sent_event=True,
    task_track_started=True,
    task_track_received=True,
    broker_transport_options={'socket_connect_timeout': 3},
    broker_connection_retry_on_startup=True,
)


def broker_available(timeout: float = 2.0) -> bool:
    """Quick TCP probe of the Celery broker. Lets the API fail fast with a 503
    instead of blocking on Celery's connection-retry loop when the broker
    (Redis) is down — without weakening the worker's own reconnect behaviour."""
    parsed = urlparse(celery.conf.broker_url)
    host, port = parsed.hostname or 'localhost', parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# aiosqlite connections are bound to the event loop that created them, so every
# async DB call in a given process must run on ONE persistent loop. asyncio.run()
# would create/close a fresh loop per call and break the shared connection, and
# asyncio.get_event_loop() is deprecated on 3.12+; hence this explicit loop.
# NOTE: this assumes one process per worker (Celery's default prefork/solo
# pools). A threads/gevent pool would share this loop across threads and break
# run_until_complete — switch to a per-thread loop if you change the pool.
_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def _run(coro):
    """Run a coroutine to completion on this process's persistent loop."""
    return _get_loop().run_until_complete(coro)


def _init_db():
    try:
        _run(initialize_database())
    except Exception:
        logger.exception("Database initialization failed")


# Fallback init for non-worker importers (e.g. the API enqueuing jobs, or a
# standalone script). initialize_database is idempotent, so this is harmless
# and redundant when the host app already inits the DB in its own startup.
# Celery worker children set CELERY_WORKER_MODE and init per process via the
# worker_process_init signal below instead (each fork is a fresh process).
if not os.environ.get('CELERY_WORKER_MODE'):
    _init_db()


@worker_process_init.connect
def init_worker_process(**kwargs):
    _init_db()


@task_prerun.connect
def task_prerun_handler(task_id, task, *args, **kwargs):
    logger.info(f"Task started: {task_id}")
    task_obj = _run(Task.find_one({'pid': task_id}))
    if task_obj:
        task_obj.status = TaskStatus.IN_PROGRESS
        _run(task_obj.save())


@task_postrun.connect
def task_postrun_handler(task_id, task, *args, retval=None, state=None, **kwargs):
    logger.info(f"Task completed: {task_id}, State: {state}")
    task_obj = _run(Task.find_one({'pid': task_id}))
    if task_obj:
        task_obj.status = TaskStatus.COMPLETED if state == 'SUCCESS' else TaskStatus.FAILED
        _run(task_obj.save())


@task_success.connect
def task_success_handler(sender=None, result=None, **kwargs):
    logger.info(f"Task succeeded: {sender.request.id}")  # type: ignore


@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, **kwargs):
    logger.error(f"Task failed: {task_id}, Exception: {exception}")


@celery.task(name="generic_task")
def run_task(task_id):
    task = _run(Task.get(task_id))
    if task:
        return _run(task.start())
    return None


@celery.task(name="team_task")
def run_team_task(team_id: str, task_id: str):
    team = _run(Team.get(team_id))
    task = _run(Task.get(task_id))
    if task and team:
        from cognitrix.utils.core import get_websocket_manager

        websocket_manager = get_websocket_manager(task_id)
        if websocket_manager:
            try:
                _run(team.assign_task(task.id))
                task_session = Session(team_id=team.id, task_id=task.id)
                _run(task_session.save())
            finally:
                from cognitrix.utils.core import unregister_websocket_manager

                unregister_websocket_manager(task_id)

            return _run(team.work_on_task(task.id, task_session, websocket_manager))


if __name__ == '__main__':
    celery.start()
