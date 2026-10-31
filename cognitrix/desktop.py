"""Entry point for the engine bundled inside the desktop app.

The desktop shell (Tauri, see ``desktop/``) ships this module frozen with
PyInstaller as one executable, ``cognitrix-engine``. That single executable
has two jobs, picked by its first argument:

    cognitrix-engine engine --port 48210 --parent-pid 1234
        Runs the FastAPI server (API + the built React UI) on 127.0.0.1.

    cognitrix-engine worker --pool=solo ...
        Runs the Celery task worker. The engine starts this itself through
        ``celery_worker.ensure_local_worker`` when a task run is queued; a
        frozen app has no ``python -m celery`` to call, so it re-runs its own
        executable with ``worker`` instead.

It also works unfrozen: ``python -m cognitrix.desktop engine --port 8000``.
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger('cognitrix.desktop')

DEFAULT_PORT = 48210


def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def default_working_folder() -> Path:
    """Where the shell tool and file tools may work when the user hasn't picked
    a folder. A frozen app's cwd is the install dir (or System32 when started
    by Windows at sign-in), which is never what anyone wants."""
    return Path.home() / 'Cognitrix'


def _prepare_environment(working_folder: str | None) -> None:
    os.environ['COGNITRIX_DESKTOP'] = '1'
    # Provider keys for the desktop app live in ~/.cognitrix/.env (the app has
    # no project folder of its own to hold one). Real env vars still win.
    env_file = Path.home() / '.cognitrix' / '.env'
    if env_file.is_file():
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    # A desktop app shouldn't sign you out every half hour. Sessions still end
    # with "Sign out of all devices" in Settings.
    os.environ.setdefault('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', str(60 * 24 * 30))
    folder = working_folder or os.environ.get('COGNITRIX_TOOLS_ROOT')
    if not folder:
        path = default_working_folder()
        path.mkdir(parents=True, exist_ok=True)
        folder = str(path)
    os.environ['COGNITRIX_TOOLS_ROOT'] = folder
    # Start the agent's shell in the working folder too, so relative paths
    # line up with what the Settings screen shows.
    try:
        os.chdir(folder)
    except OSError:
        logger.warning('Could not change into working folder %s', folder)
    _reload_settings()


def _reload_settings() -> None:
    """Importing this module already imported the cognitrix package, which
    built `settings` from the environment as it was then. Re-read it in place
    (everything holds the same object) so the values set above apply. Run this
    before the API modules are imported: some copy settings at import time."""
    from cognitrix.config import settings

    settings.__init__()
    try:
        from cognitrix.common import constants
        constants.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_access_token_expire_minutes
    except ImportError:
        pass


def _parent_alive(pid: int) -> bool:
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _shutdown_worker() -> None:
    try:
        from cognitrix.celery_worker import stop_local_worker
        stop_local_worker()
    except Exception:
        logger.exception('Could not stop the task worker')


def _watch_parent(pid: int, interval: float = 2.0, on_exit=_shutdown_worker) -> None:
    """Exit when the parent goes away, even if it crashed and never told us
    to stop. The engine watches the desktop shell (an orphaned engine would
    keep the port busy); the worker watches the engine."""
    def loop():
        while True:
            time.sleep(interval)
            if not _parent_alive(pid):
                logger.warning('Parent process %s is gone; exiting', pid)
                if on_exit is not None:
                    on_exit()
                os._exit(0)

    threading.Thread(target=loop, name='parent-watchdog', daemon=True).start()


def run_engine(args: argparse.Namespace) -> int:
    _prepare_environment(args.working_folder)
    if args.parent_pid:
        _watch_parent(args.parent_pid)
    # The task worker inherits this and exits with the engine (see run_worker).
    os.environ['COGNITRIX_ENGINE_PID'] = str(os.getpid())

    import uvicorn

    from cognitrix.api.main import app

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    finally:
        _shutdown_worker()
    return 0


def run_worker(worker_args: list[str]) -> int:
    engine_pid = os.environ.get('COGNITRIX_ENGINE_PID', '')
    if engine_pid.isdigit():
        _watch_parent(int(engine_pid), on_exit=None)

    from cognitrix.celery_worker import celery

    celery.worker_main(['worker', *worker_args])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='cognitrix-engine', description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    engine = sub.add_parser('engine', help='Run the API server for the desktop app')
    engine.add_argument('--host', default='127.0.0.1')
    engine.add_argument('--port', type=int, default=DEFAULT_PORT)
    engine.add_argument('--parent-pid', type=int, default=None,
                        help='Exit when this process (the desktop shell) exits')
    engine.add_argument('--working-folder', default=None,
                        help='Folder the shell and file tools are limited to')
    engine.add_argument('--log-level', default='info')

    sub.add_parser('worker', help='Run the background task worker', add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `worker` passes everything after it straight to Celery, flags included.
    if argv and argv[0] == 'worker':
        return run_worker(argv[1:])
    args = build_parser().parse_args(argv)
    if args.command == 'engine':
        return run_engine(args)
    return 2


if __name__ == '__main__':
    sys.exit(main())
