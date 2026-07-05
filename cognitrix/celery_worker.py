import asyncio
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from celery import Celery
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    task_success,
    worker_process_init,
)

from cognitrix.config import COGNITRIX_WORKDIR, initialize_database
from cognitrix.tasks.base import Task, TaskStatus

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%d-%b-%y %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger('cognitrix.log')

REDIS_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')

# Filesystem-broker fallback: queue messages as files under the cognitrix
# workdir, consumed by a locally auto-spawned worker (see ensure_local_worker).
QUEUE_DIR = Path(COGNITRIX_WORKDIR) / 'celery-queue'
WORKER_PIDFILE = QUEUE_DIR / 'worker.pid'
WORKER_LOGFILE = QUEUE_DIR / 'worker.log'


def _redis_reachable(timeout: float = 2.0) -> bool:
    parsed = urlparse(REDIS_BROKER_URL)
    host, port = parsed.hostname or 'localhost', parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Broker mode is decided once, at import: Redis when it is reachable (or was
# explicitly configured via CELERY_BROKER_URL), otherwise the filesystem
# fallback. The auto-spawned fallback worker inherits CELERY_FORCE_FILESYSTEM=1
# so the enqueuing process and the worker always agree on the broker, even if
# Redis comes up in between.
USE_FILESYSTEM_BROKER = (
    bool(os.environ.get('CELERY_FORCE_FILESYSTEM'))
    or ('CELERY_BROKER_URL' not in os.environ and not _redis_reachable())
)

if USE_FILESYSTEM_BROKER:
    try:
        for _sub in ('out', 'control'):
            (QUEUE_DIR / _sub).mkdir(parents=True, exist_ok=True)
        # Queue messages and worker.log accumulate agent/LLM output — keep them
        # owner-only on shared POSIX machines (no-op on Windows).
        os.chmod(QUEUE_DIR, 0o700)
    except OSError:
        # Unwritable workdir: leave the dirs missing. broker_available() then
        # reports False and the Run route degrades to its 503.
        logger.exception("Could not prepare the filesystem broker directory")
    celery = Celery('tasks', broker='filesystem://')
    celery.conf.update(
        broker_transport_options={
            'data_folder_in': str(QUEUE_DIR / 'out'),
            'data_folder_out': str(QUEUE_DIR / 'out'),
            'control_folder': str(QUEUE_DIR / 'control'),
        },
        # No result backend on the filesystem broker — task state lives in the
        # app DB (via the signal handlers below), results are never read.
        task_ignore_result=True,
    )
    logger.info('Celery: Redis unreachable, using filesystem broker at %s', QUEUE_DIR)
else:
    celery = Celery('tasks', broker=REDIS_BROKER_URL, backend=REDIS_BROKER_URL)
    celery.conf.update(
        worker_send_task_events=True,
        task_send_sent_event=True,
        task_track_started=True,
        task_track_received=True,
        broker_transport_options={'socket_connect_timeout': 3},
        broker_connection_retry_on_startup=True,
    )


def broker_available(timeout: float = 2.0) -> bool:
    """Quick probe of the Celery broker. Lets the API fail fast with a 503
    instead of blocking on Celery's connection-retry loop when the broker is
    down — without weakening the worker's own reconnect behaviour."""
    if USE_FILESYSTEM_BROKER:
        return os.access(QUEUE_DIR / 'out', os.W_OK)
    return _redis_reachable(timeout)


def _pid_alive(pid: int) -> bool:
    """True if `pid` plausibly is our worker. On Windows this also checks the
    process image looks like python/celery, so a recycled PID from a stale
    pidfile can't masquerade as a running worker."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
                return False
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                image = Path(buf.value).name.lower()
                return 'python' in image or 'celery' in image
            return True
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # alive, just not ours to signal
    except OSError:
        return False


_worker_proc: subprocess.Popen | None = None


def ensure_local_worker() -> bool:
    """On the filesystem fallback broker, make sure a local solo-pool worker is
    running, spawning one if needed. Returns False when the worker could not be
    started (callers should report the queue as unavailable). No-op (True) on a
    real (Redis) broker, where workers are managed externally.

    ponytail: pidfile check has a small race while a fresh worker boots, so two
    rapid Run clicks can spawn two workers; the queue still delivers each
    message once. Add a lockfile if that ever matters.
    """
    global _worker_proc
    if not USE_FILESYSTEM_BROKER:
        return True
    try:
        # Our own child first: poll() is ground truth, and reaps the zombie a
        # crashed worker leaves on POSIX (which would fool the pidfile check).
        if _worker_proc is not None and _worker_proc.poll() is None:
            return True
        # A worker started outside this process (e.g. before an API restart).
        try:
            if _pid_alive(int(WORKER_PIDFILE.read_text().strip())):
                return True
        except (OSError, ValueError):
            pass
        # The pid is dead — remove the stale pidfile ourselves. Celery's own
        # stale check can't on Windows (os.kill(pid, 0) never raises the
        # ESRCH/EPERM it treats as stale), so a leftover file would make every
        # new worker exit with "Pidfile already exists".
        WORKER_PIDFILE.unlink(missing_ok=True)

        env = {**os.environ, 'CELERY_WORKER_MODE': '1', 'CELERY_FORCE_FILESYSTEM': '1'}
        # Pin the child's imports to this process's package set: celery's
        # re-exec on Windows can land on the venv's base interpreter (billiard
        # uses sys.executable with no venv fix-up), and PYTHONPATH wins over
        # whatever site-packages that interpreter has.
        env['PYTHONPATH'] = os.pathsep.join(p for p in sys.path if p)
        kwargs: dict = {}
        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs['start_new_session'] = True
        with open(WORKER_LOGFILE, 'ab') as log:
            proc = subprocess.Popen(
                [sys.executable, '-m', 'celery', '-A', 'cognitrix.celery_worker', 'worker',
                 '--pool=solo', '--loglevel=info', f'--pidfile={WORKER_PIDFILE}'],
                stdout=log, stderr=log, env=env, **kwargs,
            )
        # Catch instant deaths (missing deps, bad CLI) so the route can 503
        # instead of enqueueing to nobody. Runs off the event loop (to_thread).
        time.sleep(1.0)
        if proc.poll() is not None:
            logger.error(
                "Fallback worker exited immediately (code %s) — see %s",
                proc.returncode, WORKER_LOGFILE,
            )
            return False
        _worker_proc = proc
        logger.info('Spawned local Celery fallback worker (log: %s)', WORKER_LOGFILE)
        return True
    except Exception:
        logger.exception("Could not start the local fallback worker")
        return False


# aiosqlite connections are bound to the event loop that created them, so every
# async DB call in a given process must run on ONE persistent loop. asyncio.run()
# would create/close a fresh loop per call and break the shared connection, and
# asyncio.get_event_loop() is deprecated on 3.12+; hence this explicit loop.
# NOTE: this assumes one process per worker (Celery's default prefork pool, or
# the solo pool the fallback worker uses). A threads/gevent pool would share
# this loop across threads and break run_until_complete — switch to a
# per-thread loop if you change the pool.
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
    # run_until_complete can't drive a loop that's already running — e.g. when
    # the async web app imports this module inside asyncio.run(). Attempting it
    # there raises and leaks an un-awaited initialize_database() coroutine. The
    # host app initialises the DB in its own startup, so just skip.
    try:
        asyncio.get_running_loop()
        return
    except RuntimeError:
        pass
    try:
        _run(initialize_database())
    except Exception:
        logger.exception("Database initialization failed")


# Fallback init for non-worker importers (e.g. the API enqueuing jobs, or a
# standalone script). initialize_database is idempotent, so this is harmless
# and redundant when the host app already inits the DB in its own startup.
# Celery worker children set CELERY_WORKER_MODE and init per process via the
# signals below instead (each fork is a fresh process).
if not os.environ.get('CELERY_WORKER_MODE'):
    _init_db()


# worker_process_init covers every pool: it fires in each prefork child and —
# despite the name — also in the solo pool's main process (celery solo.py sends
# it from TaskPool.__init__). worker_init is deliberately NOT hooked: it fires
# pre-fork in the prefork parent, and initialising there would create this
# module's event loop before the fork, leaving every child sharing the
# parent's loop (see the loop note above).
@worker_process_init.connect
def init_worker_process(**kwargs):
    _init_db()


@task_prerun.connect
def task_prerun_handler(task_id, task, *args, **kwargs):
    logger.info(f"Task started: {task_id}")
    task_obj = _run(Task.find_one({'pid': task_id}))
    # Never resurrect a terminal task: a job whose task was cancelled while
    # still queued must stay CANCELLED so the orchestrator's entry guard can
    # skip the run. The orchestrator sets IN_PROGRESS itself at entry anyway.
    if task_obj and task_obj.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
        task_obj.status = TaskStatus.IN_PROGRESS
        _run(task_obj.save())


@task_postrun.connect
def task_postrun_handler(task_id, task, *args, retval=None, state=None, **kwargs):
    logger.info(f"Task completed: {task_id}, State: {state}")
    task_obj = _run(Task.find_one({'pid': task_id}))
    # Backstop only — the orchestrator writes terminal statuses itself. Touch
    # the task ONLY when still IN_PROGRESS (a pre-terminal crash): anything
    # else would clobber CANCELLED (cancel returns SUCCESS) or an
    # orchestrator-written FAILED/COMPLETED.
    if task_obj and task_obj.status == TaskStatus.IN_PROGRESS:
        task_obj.status = TaskStatus.COMPLETED if state == 'SUCCESS' else TaskStatus.FAILED
        _run(task_obj.save())


@task_success.connect
def task_success_handler(sender=None, result=None, **kwargs):
    logger.info(f"Task succeeded: {sender.request.id}")  # type: ignore


@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, **kwargs):
    logger.error(f"Task failed: {task_id}, Exception: {exception}")


@celery.task(name="generic_task")
def run_task(task_id, resume=False):
    task = _run(Task.get(task_id))
    if task:
        run = _run(task.start(resume=resume))
        # Return a plain id, never the TaskRun model: on Redis deployments the
        # result backend JSON-serializes the retval, and a pydantic model would
        # raise EncodeError AFTER a successful run (flipping it to FAILED).
        return getattr(run, 'id', None)
    return None


if __name__ == '__main__':
    celery.start()
