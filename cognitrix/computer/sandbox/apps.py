"""App hosting: the processes behind the Apps people open from Cognitrix.

An app is a folder of code plus a start command that serves HTTP on the port
in ``$PORT``. ``AppHost`` keeps every published version of every app on disk
and runs the live one:

    <root>/releases/<slug>/v<N>/   the code of version N (unpacked upload)
    <root>/data/<slug>/            $APP_DATA_DIR, kept across versions (SQLite etc.)
    <root>/logs/<slug>/v<N>.log    what version N printed
    <root>/apps.json               which version is live, its command and env

A new version starts next to the live one on its own port; only when it
answers HTTP does it take over, so a broken version never replaces a working
one. Stopped apps (asleep) start again on the next request.

It runs in two places: inside a user's sandbox container, behind the
supervisor's /apps endpoints (the usual case), and in the engine itself when
the sandboxed computer is off (``cognitrix.apps.runner.LocalRunner``).

Standard library only: the sandbox image ships just ``cognitrix.computer``.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger('cognitrix.apps')

SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,47}$')
KEEP_RELEASES = int(os.environ.get('COGNITRIX_APPS_KEEP_RELEASES', '5'))
START_TIMEOUT = float(os.environ.get('COGNITRIX_APPS_START_TIMEOUT', '90'))
INSTALL_TIMEOUT = float(os.environ.get('COGNITRIX_APPS_INSTALL_TIMEOUT', '900'))
MAX_UPLOAD = int(os.environ.get('COGNITRIX_APPS_MAX_UPLOAD', str(100 * 1024 * 1024)))
MAX_RESTARTS = 3
"""Crashes in a row before an app is left stopped."""
LOG_BYTES = 2 * 1024 * 1024
"""A log file is cut back to its last half past this size."""

SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.mypy_cache', '.pytest_cache',
             '.ruff_cache', '.next', '.turbo', '.cache'}
"""Never uploaded; the install command recreates what's needed."""


class AppHostError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def check_slug(slug: str) -> str:
    if not SLUG.match(slug or ''):
        raise AppHostError('bad_slug', 'App names are lowercase letters, digits and dashes.')
    return slug


def pack_folder(folder: str, limit: int = MAX_UPLOAD) -> bytes:
    """A gzipped tar of ``folder`` without dependency and cache folders."""
    root = os.path.abspath(folder)
    if not os.path.isdir(root):
        raise AppHostError('no_folder', f'{folder} is not a folder.')
    buf = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for here, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(here, d)))
            for name in sorted(files):
                path = os.path.join(here, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                total += os.path.getsize(path)
                if total > limit:
                    raise AppHostError('too_big', f'The app folder is over {limit // (1024 * 1024)} MB '
                                       '(dependency folders like node_modules are already left out).')
                tar.add(path, arcname=os.path.relpath(path, root), recursive=False)
    data = buf.getvalue()
    if not data:
        raise AppHostError('empty', 'The app folder is empty.')
    return data


def unpack(data: bytes, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    real = os.path.realpath(dest)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue  # folders are made as needed; links and devices never
            target = os.path.realpath(os.path.join(real, member.name))
            if not target.startswith(real + os.sep):
                raise AppHostError('bad_archive', f'{member.name!r} points outside the app folder.')
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with open(target, 'wb') as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def answers_http(port: int, timeout: float = 2.0) -> bool:
    """Anything that speaks HTTP on the port counts, error pages too."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=timeout):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False


def _tail(path: str, lines: int = 60) -> str:
    try:
        with open(path, 'rb') as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 64 * 1024))
            text = fh.read().decode('utf-8', 'replace')
    except OSError:
        return ''
    return '\n'.join(text.splitlines()[-lines:])


@dataclass
class Running:
    version: int
    port: int
    proc: subprocess.Popen
    started: float = field(default_factory=time.time)
    restarts: int = 0


class AppHost:
    def __init__(self, root: str, base_env: dict[str, str] | None = None):
        self.root = os.path.abspath(root)
        self.base_env = dict(base_env if base_env is not None else os.environ)
        for key in ('COGNITRIX_SANDBOX_TOKEN',):
            self.base_env.pop(key, None)  # apps never see the container's token
        self.running: dict[str, Running] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        os.makedirs(self.root, exist_ok=True)
        self._state = self._load()
        self._watch = threading.Thread(target=self._watchdog, name='app-watchdog', daemon=True)
        self._watch.start()

    # -- state ----------------------------------------------------------------
    def _state_file(self) -> str:
        return os.path.join(self.root, 'apps.json')

    def _load(self) -> dict[str, dict]:
        try:
            with open(self._state_file()) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self._state_file() + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(self._state, fh)
        os.replace(tmp, self._state_file())

    def _lock(self, slug: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(slug, threading.Lock())

    def release_dir(self, slug: str, version: int) -> str:
        return os.path.join(self.root, 'releases', slug, f'v{int(version)}')

    def data_dir(self, slug: str) -> str:
        return os.path.join(self.root, 'data', slug)

    def log_file(self, slug: str, version: int) -> str:
        return os.path.join(self.root, 'logs', slug, f'v{int(version)}.log')

    # -- processes ------------------------------------------------------------
    def _spawn(self, slug: str, version: int, spec: dict, port: int) -> subprocess.Popen:
        cwd = self.release_dir(slug, version)
        os.makedirs(self.data_dir(slug), exist_ok=True)
        log_path = self.log_file(slug, version)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._trim(log_path)
        env = dict(self.base_env)
        env.update({str(k): str(v) for k, v in (spec.get('env') or {}).items()})
        env.update({'PORT': str(port), 'HOST': '127.0.0.1', 'APP_DATA_DIR': self.data_dir(slug),
                    'APP_NAME': slug, 'APP_VERSION': str(version), 'PYTHONUNBUFFERED': '1'})
        log = open(log_path, 'ab')  # noqa: SIM115 (the child keeps it)
        log.write(f'\n--- starting v{version} on port {port}: {spec["command"]}\n'.encode())
        log.flush()
        try:
            return subprocess.Popen(  # noqa: S603
                ['/bin/sh', '-c', spec['command']] if os.name != 'nt' else ['cmd', '/c', spec['command']],
                cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=os.name != 'nt')
        finally:
            log.close()

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name != 'nt':
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name != 'nt':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except OSError:
                pass

    def _start_and_wait(self, slug: str, version: int, spec: dict, timeout: float) -> Running:
        port = int(spec.get('port') or 0) or free_port()
        proc = self._spawn(slug, version, spec, port)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AppHostError('exited', f'Version {version} stopped right away (exit code {proc.returncode}).')
            if answers_http(port, timeout=1.5):
                return Running(version, port, proc)
            time.sleep(0.4)
        self._kill(proc)
        raise AppHostError('no_answer', f'Version {version} did not answer HTTP on $PORT ({port}) within '
                           f'{int(timeout)} seconds. The start command must serve on the port in $PORT.')

    @staticmethod
    def _trim(path: str) -> None:
        try:
            if os.path.getsize(path) > LOG_BYTES:
                with open(path, 'rb') as fh:
                    fh.seek(-LOG_BYTES // 2, os.SEEK_END)
                    keep = fh.read()
                with open(path, 'wb') as fh:
                    fh.write(b'[earlier output cut]\n' + keep)
        except OSError:
            pass

    # -- public API -----------------------------------------------------------
    def deploy(self, slug: str, version: int, archive: bytes, command: str, install: str = '',
               env: dict | None = None, port: int = 0) -> dict:
        """Unpack, install and start ``version``; it goes live only when it answers HTTP."""
        check_slug(slug)
        command = str(command or '').strip()
        if not command:
            raise AppHostError('no_command', 'A start command is needed, e.g. "python app.py" or "npm start".')
        if len(archive) > MAX_UPLOAD * 2:
            raise AppHostError('too_big', 'The upload is too big.')
        with self._lock(slug):
            dest = self.release_dir(slug, version)
            shutil.rmtree(dest, ignore_errors=True)
            unpack(archive, dest)
            log_path = self.log_file(slug, version)
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            spec = {'command': command, 'install': str(install or '').strip(), 'env': dict(env or {}),
                    'port': int(port or 0)}
            if spec['install']:
                with open(log_path, 'ab') as log:
                    log.write(f'--- installing v{version}: {spec["install"]}\n'.encode())
                    log.flush()
                    try:
                        done = subprocess.run(  # noqa: S603
                            ['/bin/sh', '-c', spec['install']] if os.name != 'nt' else ['cmd', '/c', spec['install']],
                            cwd=dest, env={**self.base_env, **spec['env'], 'APP_DATA_DIR': self.data_dir(slug)},
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=INSTALL_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        raise AppHostError('install_timeout', f'The install command ran over {int(INSTALL_TIMEOUT)} '
                                           'seconds.') from None
                if done.returncode != 0:
                    return self._failed(slug, version, f'The install command failed (exit code {done.returncode}).')
            old = self.running.get(slug)
            if old is not None and spec['port'] and old.port == spec['port']:
                self._kill(old.proc)  # a fixed port can't run two versions side by side
                self.running.pop(slug, None)
            try:
                new = self._start_and_wait(slug, version, spec, START_TIMEOUT)
            except AppHostError as exc:
                return self._failed(slug, version, exc.message)
            if old is not None:
                self._kill(old.proc)
            self.running[slug] = new
            self._state[slug] = {**spec, 'version': version, 'updated': time.time()}
            self._save()
            self._prune(slug, version)
            return {'ok': True, 'status': 'online', 'version': version, 'port': new.port,
                    'log': _tail(log_path, 20)}

    def _failed(self, slug: str, version: int, message: str) -> dict:
        live = self._state.get(slug, {}).get('version')
        note = f' Version {live} keeps serving.' if live and slug in self.running else ''
        return {'ok': False, 'status': 'failed', 'version': version, 'live_version': live,
                'error': message + note, 'log': _tail(self.log_file(slug, version), 60)}

    def _prune(self, slug: str, live: int) -> None:
        base = os.path.join(self.root, 'releases', slug)
        try:
            versions = sorted(int(n[1:]) for n in os.listdir(base) if n.startswith('v') and n[1:].isdigit())
        except OSError:
            return
        for number in versions[:-KEEP_RELEASES]:
            if number != live:
                shutil.rmtree(self.release_dir(slug, number), ignore_errors=True)

    def has_release(self, slug: str, version: int) -> bool:
        return os.path.isdir(self.release_dir(slug, version))

    def promote(self, slug: str, version: int) -> dict:
        """Make an earlier version live again (roll back)."""
        check_slug(slug)
        with self._lock(slug):
            spec = self._state.get(slug)
            if spec is None or not self.has_release(slug, version):
                raise AppHostError('no_version', f'Version {version} is no longer kept.')
            try:
                new = self._start_and_wait(slug, version, spec, START_TIMEOUT)
            except AppHostError as exc:
                return self._failed(slug, version, exc.message)
            old = self.running.pop(slug, None)
            if old is not None:
                self._kill(old.proc)
            self.running[slug] = new
            self._state[slug] = {**spec, 'version': version, 'updated': time.time()}
            self._save()
            return {'ok': True, 'status': 'online', 'version': version, 'port': new.port}

    def start(self, slug: str) -> dict:
        """Wake an app: start its live version if it isn't running."""
        check_slug(slug)
        with self._lock(slug):
            run = self.running.get(slug)
            if run is not None and run.proc.poll() is None:
                return {'ok': True, 'status': 'online', 'version': run.version, 'port': run.port}
            spec = self._state.get(slug)
            if spec is None:
                raise AppHostError('no_app', 'This app has no running version yet.')
            try:
                new = self._start_and_wait(slug, int(spec['version']), spec, START_TIMEOUT)
            except AppHostError as exc:
                return self._failed(slug, int(spec['version']), exc.message)
            self.running[slug] = new
            return {'ok': True, 'status': 'online', 'version': new.version, 'port': new.port}

    def stop(self, slug: str) -> dict:
        """Put an app to sleep: its process stops, its files and data stay."""
        check_slug(slug)
        with self._lock(slug):
            run = self.running.pop(slug, None)
            if run is not None:
                self._kill(run.proc)
        return {'ok': True, 'status': 'asleep'}

    def remove(self, slug: str) -> dict:
        check_slug(slug)
        self.stop(slug)
        with self._lock(slug):
            self._state.pop(slug, None)
            self._save()
            for part in ('releases', 'data', 'logs'):
                shutil.rmtree(os.path.join(self.root, part, slug), ignore_errors=True)
        return {'ok': True}

    def status(self, slug: str) -> dict:
        check_slug(slug)
        spec = self._state.get(slug)
        run = self.running.get(slug)
        alive = run is not None and run.proc.poll() is None
        return {'ok': True, 'slug': slug, 'exists': spec is not None,
                'status': 'online' if alive else ('asleep' if spec else 'none'),
                'version': (run.version if alive else (spec or {}).get('version')),
                'port': run.port if alive else None}

    def list(self) -> list[dict]:
        return [self.status(slug) for slug in sorted(self._state)]

    def logs(self, slug: str, lines: int = 200, version: int = 0) -> str:
        check_slug(slug)
        if not version:
            run = self.running.get(slug)
            version = run.version if run else int((self._state.get(slug) or {}).get('version') or 0)
        return _tail(self.log_file(slug, version), max(1, min(int(lines or 200), 2000))) if version else ''

    def port_for(self, slug: str, wake: bool = True) -> int | None:
        """The live port, starting the app if it's asleep and ``wake`` is set."""
        run = self.running.get(slug)
        if run is not None and run.proc.poll() is None:
            return run.port
        if not wake:
            return None
        out = self.start(slug)
        return out.get('port') if out.get('ok') else None

    def shutdown(self) -> None:
        for slug in list(self.running):
            try:
                self.stop(slug)
            except AppHostError:
                pass

    # -- crashes --------------------------------------------------------------
    def _watchdog(self) -> None:
        while True:
            time.sleep(3)
            for slug, run in list(self.running.items()):
                if run.proc.poll() is None:
                    if time.time() - run.started > 120:
                        run.restarts = 0
                    continue
                lock = self._lock(slug)
                if not lock.acquire(blocking=False):
                    continue
                try:
                    if self.running.get(slug) is not run:
                        continue
                    spec = self._state.get(slug)
                    if spec is None or run.restarts >= MAX_RESTARTS:
                        self.running.pop(slug, None)
                        logger.warning('app %s stopped after %d restarts', slug, run.restarts)
                        continue
                    time.sleep(min(2 ** run.restarts, 10))
                    try:
                        new = self._start_and_wait(slug, run.version, spec, START_TIMEOUT)
                        new.restarts = run.restarts + 1
                        self.running[slug] = new
                    except AppHostError:
                        run.restarts += 1
                        if run.restarts >= MAX_RESTARTS:
                            self.running.pop(slug, None)
                finally:
                    lock.release()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text or '', validate=True)
    except ValueError:
        raise AppHostError('bad_archive', 'The upload was not valid base64.') from None
