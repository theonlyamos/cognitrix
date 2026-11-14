"""Coding work inside the sandbox container: a shell, files and projects.

Runs in the container (stdlib only, like the rest of this package's
container side). Each agent gets its own folder, ``~/work/<agent>``, where its
commands start and its projects live. The container is the sandbox, so
commands run without the engine's allowlist; they can't reach the person's
computer.

Projects are folders copied in from the person's computer. Each one gets a
private git baseline (outside the project, so the project's own ``.git`` is
left alone) that records the files as they were copied in. ``changes`` diffs
the project against it; ``export`` hands changed files to the engine, which
copies them back after the person approves; ``synced`` moves the baseline
forward for the files that went back.

Every command's output is also appended to a terminal log that an xterm on
the agent's desktop follows, so a person watching sees what runs.
"""
from __future__ import annotations

import base64
import fnmatch
import io
import os
import re
import shutil
import signal
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass, field

from cognitrix.computer.sandbox import check_name

HOME = os.path.expanduser('~')
WORK_ROOT = os.environ.get('COGNITRIX_SANDBOX_WORK', os.path.join(HOME, 'work'))
STATE_ROOT = os.path.join(HOME, '.cognitrix-sandbox')
MAX_OUTPUT = 30_000  # characters a command returns at most (head and tail kept)
MAX_TIMEOUT = 1800
MAX_JOBS = 40  # running background jobs per container
MAX_READ_BYTES = 5_000_000
MAX_IMPORT_BYTES = int(os.environ.get('COGNITRIX_SANDBOX_MAX_IMPORT', str(500 * 1024 * 1024)))
MAX_EXPORT_BYTES = 50 * 1024 * 1024
MAX_PATCH = 200_000
_PROJECT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$')

# Never part of a project's baseline (installed or built inside the sandbox).
DEFAULT_EXCLUDES = (
    'node_modules/', '.venv/', 'venv/', '__pycache__/', '*.pyc', '.pytest_cache/', '.mypy_cache/',
    '.ruff_cache/', '.tox/', '.next/', '.nuxt/', '.turbo/', '.parcel-cache/', '.gradle/', '.cache/',
    '.DS_Store',
)

# Tools people expect on PATH: user installs (pip --user, npm -g into the
# home folder, cargo, go) persist in the home volume.
_EXTRA_PATH = ('~/.local/bin', '~/.npm-global/bin', '~/.cargo/bin', '~/go/bin', '/usr/local/go/bin',
               '~/.bun/bin', '~/.deno/bin')


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def project_name(raw: str) -> str:
    name = str(raw or '').strip()
    if not _PROJECT.match(name) or name in ('.', '..'):
        raise WorkspaceError('bad_name', f"'{raw}' can't be a project name (letters, numbers, . _ - and spaces).")
    return name


def child_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ('COGNITRIX_SANDBOX_TOKEN', 'DISPLAY')}
    path = env.get('PATH', '/usr/local/bin:/usr/bin:/bin')
    extra_path = ':'.join(os.path.expanduser(p) for p in _EXTRA_PATH)
    env.update({
        'PATH': f'{extra_path}:{path}', 'HOME': HOME, 'TERM': 'dumb', 'CI': '1',
        'NPM_CONFIG_PREFIX': os.path.join(HOME, '.npm-global'), 'PIP_BREAK_SYSTEM_PACKAGES': '1',
        'PYTHONUNBUFFERED': '1', 'FORCE_COLOR': '0', 'NO_COLOR': '1', 'GIT_TERMINAL_PROMPT': '0',
    })
    for key, value in (extra or {}).items():
        if isinstance(key, str) and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key) and isinstance(value, str):
            env[key] = value
    return env


@dataclass
class Job:
    id: str
    agent: str
    command: str
    label: str
    cwd: str
    proc: subprocess.Popen
    log: str
    started: float = field(default_factory=time.time)
    ended: float | None = None
    read_at: int = 0  # bytes of the log already returned
    killed: str = ''  # why it was stopped ('timeout', 'stopped')
    cwd_file: str = ''

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def status(self) -> dict:
        code = self.proc.poll()
        return {'id': self.id, 'command': self.label, 'cwd': self.cwd, 'running': code is None,
                'exit_code': code, 'killed': self.killed, 'started': self.started,
                'seconds': round((self.ended or time.time()) - self.started, 1)}


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit - len(head)):]
    return f'{head}\n\n[... {len(text) - len(head) - len(tail):,} characters cut ...]\n\n{tail}'


class Workspace:
    """Shell, files and projects for every agent in this container."""

    def __init__(self, terminal_opener=None) -> None:
        self.jobs: dict[str, Job] = {}
        self._guard = threading.Lock()
        self._seq = 0
        self._cwd: dict[str, str] = {}
        # Called with (agent, terminal log path) when a command starts, so the
        # supervisor can show it on the agent's desktop.
        self.terminal_opener = terminal_opener
        os.makedirs(WORK_ROOT, exist_ok=True)
        os.makedirs(STATE_ROOT, exist_ok=True)

    # -- places ---------------------------------------------------------------
    def root(self, agent: str) -> str:
        path = os.path.join(WORK_ROOT, check_name(agent))
        os.makedirs(path, exist_ok=True)
        return path

    def resolve(self, agent: str, path: str | None, base: str | None = None) -> str:
        """An absolute path in the container. Relative paths start at the
        agent's current folder. The whole container is the sandbox, so any
        path is allowed."""
        raw = os.path.expanduser(str(path or '.'))
        start = base or self.cwd(agent)
        return os.path.normpath(raw if os.path.isabs(raw) else os.path.join(start, raw))

    def cwd(self, agent: str) -> str:
        got = self._cwd.get(agent)
        return got if got and os.path.isdir(got) else self.root(agent)

    def terminal_log(self, agent: str) -> str:
        folder = os.path.join(STATE_ROOT, 'terminal')
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f'{check_name(agent)}.log')

    def _open_terminal(self, agent: str) -> None:
        try:
            self.terminal_opener(agent, self.terminal_log(agent))
        except Exception:
            pass

    def _terminal(self, agent: str, text: str) -> None:
        try:
            with open(self.terminal_log(agent), 'a', encoding='utf-8', errors='replace') as fh:
                fh.write(text)
        except OSError:
            pass

    # -- commands -------------------------------------------------------------
    def start(self, agent: str, command: str, cwd: str | None = None, env: dict | None = None,
              stdin: str | None = None, label: str | None = None, max_seconds: int = MAX_TIMEOUT * 4) -> Job:
        command = str(command or '')
        if not command.strip():
            raise WorkspaceError('bad_request', 'Give a command to run.')
        with self._guard:
            running = sum(1 for j in self.jobs.values() if j.running)
            if running >= MAX_JOBS:
                raise WorkspaceError('limit', f'{running} commands are already running here; stop some first.')
            self._seq += 1
            job_id = f'sbx_{self._seq}'
        work = self.resolve(agent, cwd) if cwd else self.cwd(agent)
        if not os.path.isdir(work):
            raise WorkspaceError('not_found', f'No folder {work} in the sandbox.')
        logs = os.path.join(STATE_ROOT, 'jobs')
        os.makedirs(logs, exist_ok=True)
        log = os.path.join(logs, f'{job_id}.log')
        cwd_file = os.path.join(logs, f'{job_id}.cwd')
        # Remember where the command ended up, so the next one starts there.
        script = f'{command}\n__cognitrix_ec=$?\npwd > {_sh_quote(cwd_file)} 2>/dev/null\nexit $__cognitrix_ec\n'
        if self.terminal_opener is not None:
            # Starting the desktop takes a few seconds; the command doesn't wait.
            threading.Thread(target=self._open_terminal, args=(agent,), daemon=True).start()
        shown = label or command
        self._terminal(agent, f'\n\x1b[1;32m{_short(work)} $\x1b[0m {shown}\n')
        out = open(log, 'wb')  # noqa: SIM115 - the pump thread closes it
        proc = subprocess.Popen(  # noqa: S603
            ['bash', '-c', script], cwd=work, env=child_env(env), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            start_new_session=True)
        job = Job(job_id, agent, command, shown, work, proc, log, cwd_file=cwd_file)
        with self._guard:
            self.jobs[job_id] = job
            self._prune()
        if stdin is not None:
            def feed() -> None:
                try:
                    proc.stdin.write(stdin.encode('utf-8'))
                    proc.stdin.close()
                except OSError:
                    pass
            threading.Thread(target=feed, daemon=True).start()
        threading.Thread(target=self._pump, args=(job, out, max_seconds), daemon=True).start()
        return job

    def _pump(self, job: Job, out, max_seconds: int) -> None:
        deadline = job.started + max_seconds
        timer = threading.Timer(max(1, deadline - time.time()), lambda: self._kill(job, 'timeout'))
        timer.daemon = True
        timer.start()
        try:
            while True:
                chunk = job.proc.stdout.read1(65536) if hasattr(job.proc.stdout, 'read1') else job.proc.stdout.read(4096)
                if not chunk:
                    break
                out.write(chunk)
                out.flush()
                self._terminal(job.agent, chunk.decode('utf-8', errors='replace'))
        finally:
            out.close()
            job.proc.wait()
            timer.cancel()
            job.ended = time.time()
            code = job.proc.returncode
            note = f'stopped ({job.killed})' if job.killed else f'exit {code}'
            colour = '32' if code == 0 and not job.killed else '31'
            self._terminal(job.agent, f'\x1b[{colour}m[{note}]\x1b[0m\n')
            try:
                with open(job.cwd_file, encoding='utf-8') as fh:
                    where = fh.read().strip()
                if where and os.path.isdir(where):
                    self._cwd[job.agent] = where
            except OSError:
                pass

    def _kill(self, job: Job, why: str) -> None:
        if job.proc.poll() is not None:
            return
        job.killed = why
        try:
            os.killpg(job.proc.pid, signal.SIGTERM)
        except OSError:
            return
        for _ in range(30):
            if job.proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            os.killpg(job.proc.pid, signal.SIGKILL)
        except OSError:
            pass

    def _prune(self) -> None:
        done = [j for j in self.jobs.values() if not j.running]
        for old in sorted(done, key=lambda j: j.started)[:-200]:
            self.jobs.pop(old.id, None)
            for path in (old.log, old.cwd_file):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def run(self, agent: str, command: str, cwd: str | None = None, timeout: float = 120,
            env: dict | None = None, background: bool = False, stdin: str | None = None,
            label: str | None = None) -> dict:
        """Run a command. Waits up to ``timeout`` seconds unless ``background``."""
        timeout = max(1, min(float(timeout or 120), MAX_TIMEOUT))
        job = self.start(agent, command, cwd, env, stdin, label,
                         max_seconds=MAX_TIMEOUT * 4 if background else int(timeout) + 1)
        if background:
            time.sleep(0.5)  # let fast failures show up
            return {**job.status(), 'output': self.read(job.id, agent)['output']}
        deadline = time.time() + timeout + 5
        while job.ended is None and time.time() < deadline:
            time.sleep(0.05)
        if job.ended is None:
            self._kill(job, 'timeout')
            time.sleep(0.2)
        return {**job.status(), 'output': self.read(job.id, agent)['output'], 'cwd_after': self.cwd(agent)}

    def get(self, job_id: str, agent: str) -> Job:
        job = self.jobs.get(str(job_id or ''))
        if job is None or job.agent != agent:
            raise WorkspaceError('not_found', f"No command '{job_id}' in the sandbox.")
        return job

    def read(self, job_id: str, agent: str, filter_re: str | None = None) -> dict:
        """Output printed since the last read."""
        job = self.get(job_id, agent)
        try:
            with open(job.log, 'rb') as fh:
                fh.seek(job.read_at)
                data = fh.read()
        except OSError:
            data = b''
        job.read_at += len(data)
        text = data.decode('utf-8', errors='replace')
        if filter_re:
            pattern = re.compile(filter_re)
            text = '\n'.join(line for line in text.splitlines() if pattern.search(line))
        return {**job.status(), 'output': _clip(text)}

    def full_output(self, job_id: str, agent: str, limit: int = 400_000) -> str:
        job = self.get(job_id, agent)
        try:
            with open(job.log, 'rb') as fh:
                size = os.fstat(fh.fileno()).st_size
                if size > limit:
                    fh.seek(size - limit)
                return fh.read().decode('utf-8', errors='replace')
        except OSError:
            return ''

    def list_jobs(self, agent: str) -> list[dict]:
        return [j.status() for j in self.jobs.values() if j.agent == agent]

    def stop(self, job_id: str, agent: str) -> dict:
        job = self.get(job_id, agent)
        self._kill(job, 'stopped')
        return job.status()

    def wait(self, job_id: str, agent: str, seconds: float) -> dict:
        job = self.get(job_id, agent)
        deadline = time.time() + max(0.0, min(float(seconds or 0), 600))
        while job.ended is None and time.time() < deadline:
            time.sleep(0.1)
        return job.status()

    def shutdown(self) -> None:
        for job in list(self.jobs.values()):
            self._kill(job, 'stopped')

    # -- files ----------------------------------------------------------------
    def read_file(self, agent: str, path: str, start: int = 1, end: int | None = None,
                  numbers: bool = True, limit: int = MAX_OUTPUT) -> dict:
        full = self.resolve(agent, path)
        if not os.path.exists(full):
            raise WorkspaceError('not_found', f'File not found in the sandbox: {path}')
        if not os.path.isfile(full):
            raise WorkspaceError('not_a_file', f'Not a file: {path}')
        size = os.path.getsize(full)
        if size > MAX_READ_BYTES:
            raise WorkspaceError('too_big', f'The file is {size:,} bytes; read it in parts with Bash (head, sed -n).')
        start = max(1, int(start or 1))
        lines: list[str] = []
        used = 0
        total = 0
        resume = None
        with open(full, encoding='utf-8', errors='replace') as fh:
            for number, line in enumerate(fh, 1):
                total = number
                if number < start or (end is not None and number > int(end)) or resume is not None:
                    continue
                line = line.rstrip('\n')
                if len(line) > 2000:
                    line = line[:2000] + ' [line cut]'
                shown = f'{number:6d}: {line}' if numbers else line
                if used + len(shown) + 1 > limit:
                    resume = number
                    continue
                lines.append(shown)
                used += len(shown) + 1
        if total and start > total:
            raise WorkspaceError('bad_request', f'start_line ({start}) is past end of file ({total})')
        last = start + len(lines) - 1 if lines else start
        return {'path': full, 'text': '\n'.join(lines), 'start': start, 'end': last, 'total': total,
                'resume': resume}

    def write_file(self, agent: str, path: str, content: str, append: bool = False) -> dict:
        full = self.resolve(agent, path)
        os.makedirs(os.path.dirname(full) or '/', exist_ok=True)
        existed = os.path.exists(full)
        with open(full, 'a' if append else 'w', encoding='utf-8') as fh:
            fh.write(str(content))
        self._terminal(agent, f'\x1b[2m{"appended to" if append else ("wrote" if existed else "created")} {full}\x1b[0m\n')
        return {'path': full, 'size': os.path.getsize(full), 'existed': existed}

    def edit_file(self, agent: str, path: str, old: str, new: str, replace_all: bool = False,
                  create: bool = False) -> dict:
        full = self.resolve(agent, path)
        if not os.path.exists(full):
            if create:
                os.makedirs(os.path.dirname(full) or '/', exist_ok=True)
                with open(full, 'w', encoding='utf-8') as fh:
                    fh.write(str(new))
                return {'path': full, 'count': 0, 'created': True}
            raise WorkspaceError('not_found', f'File not found in the sandbox: {path}')
        if not old:
            raise WorkspaceError('bad_request', 'old_string cannot be empty')
        with open(full, encoding='utf-8', errors='replace') as fh:
            content = fh.read()
        if old not in content:
            raise WorkspaceError('no_match', f'String not found in file: {old[:50]}...')
        count = content.count(old) if replace_all else 1
        content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        with open(full, 'w', encoding='utf-8') as fh:
            fh.write(content)
        self._terminal(agent, f'\x1b[2medited {full}\x1b[0m\n')
        return {'path': full, 'count': count, 'created': False}

    def glob(self, agent: str, pattern: str, path: str = '.', include_dirs: bool = False,
             max_results: int = 100) -> dict:
        base = self.resolve(agent, path)
        if not os.path.isdir(base):
            raise WorkspaceError('not_found', f'No folder {path} in the sandbox.')
        pattern = str(pattern or '*')
        regex = _glob_regex(pattern if ('/' in pattern or pattern.startswith('**')) else f'**/{pattern}')
        found: list[tuple[float, str]] = []
        seen = 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ('.git', 'node_modules', '.venv', '__pycache__')]
            names = filenames + (dirnames if include_dirs else [])
            for name in names:
                seen += 1
                rel = os.path.relpath(os.path.join(dirpath, name), base)
                if regex.match(rel):
                    try:
                        found.append((os.path.getmtime(os.path.join(dirpath, name)), rel))
                    except OSError:
                        continue
            if seen > 200_000:
                break
        found.sort(reverse=True)
        limit = max(1, min(int(max_results or 100), 1000))
        return {'base': base, 'matches': [r for _, r in found[:limit]], 'total': len(found)}

    def grep(self, agent: str, pattern: str, path: str = '.', include: str | None = None,
             exclude: str | None = None, context: int = 0, ignore_case: bool = True,
             max_results: int = 100) -> dict:
        base = self.resolve(agent, path)
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise WorkspaceError('bad_request', f'Invalid regex: {exc}') from None
        limit = max(1, min(int(max_results or 100), 1000))
        context = max(0, min(int(context or 0), 10))
        files = [base] if os.path.isfile(base) else None
        out: list[str] = []
        hits = 0

        def candidates():
            if files is not None:
                yield from files
                return
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in ('.git', 'node_modules', '.venv', '__pycache__')]
                for name in filenames:
                    if include and not fnmatch.fnmatch(name, include):
                        continue
                    if exclude and fnmatch.fnmatch(name, exclude):
                        continue
                    yield os.path.join(dirpath, name)

        for file in candidates():
            try:
                if os.path.getsize(file) > 2_000_000:
                    continue
                with open(file, 'rb') as fh:
                    data = fh.read()
            except OSError:
                continue
            if b'\0' in data[:4096]:
                continue
            lines = data.decode('utf-8', errors='replace').splitlines()
            rel = os.path.relpath(file, base) if files is None else file
            for i, line in enumerate(lines):
                if regex.search(line):
                    hits += 1
                    if hits > limit:
                        break
                    lo, hi = max(0, i - context), min(len(lines), i + context + 1)
                    for j in range(lo, hi):
                        mark = ':' if j == i else '-'
                        out.append(f'{rel}{mark}{j + 1}{mark}{lines[j][:500]}')
                    if context:
                        out.append('--')
            if hits > limit:
                break
        return {'base': base, 'lines': out, 'hits': min(hits, limit), 'more': hits > limit}

    # -- projects -------------------------------------------------------------
    def project_dir(self, agent: str, name: str) -> str:
        return os.path.join(self.root(agent), project_name(name))

    def _git_dir(self, agent: str, name: str) -> str:
        return os.path.join(STATE_ROOT, 'baselines', check_name(agent), project_name(name) + '.git')

    def _git(self, agent: str, name: str, *args: str, check: bool = True, timeout: float = 300,
             text: bool = True) -> subprocess.CompletedProcess:
        cmd = ['git', f'--git-dir={self._git_dir(agent, name)}', f'--work-tree={self.project_dir(agent, name)}',
               '-c', 'user.name=Cognitrix', '-c', 'user.email=sandbox@cognitrix.local', '-c', 'core.autocrlf=false',
               '-c', 'core.quotepath=false', '-c', 'core.safecrlf=false', '-c', 'commit.gpgsign=false', *args]
        got = subprocess.run(cmd, cwd=self.project_dir(agent, name), capture_output=True, text=text,  # noqa: S603
                             timeout=timeout, env=child_env())
        if check and got.returncode != 0:
            err = got.stderr if text else got.stderr.decode('utf-8', 'replace')
            raise WorkspaceError('git_failed', f'git {args[0]} failed: {(err or "").strip()[-400:]}')
        return got

    def projects(self, agent: str) -> list[dict]:
        base = os.path.join(STATE_ROOT, 'baselines', check_name(agent))
        out = []
        if os.path.isdir(base):
            for entry in sorted(os.listdir(base)):
                if entry.endswith('.git') and os.path.isdir(self.project_dir(agent, entry[:-4])):
                    out.append({'name': entry[:-4], 'path': self.project_dir(agent, entry[:-4]),
                                'source': self.source(agent, entry[:-4])})
        return out

    def import_project(self, agent: str, name: str, archive: bytes, replace: bool = False,
                       source: str = '') -> dict:
        """Unpack a tar.gz of a folder from the person's computer as a project.

        Copying the same folder again refreshes it: files that are gone on the
        computer are removed, ignored ones (installed packages) stay. Refused
        when the sandbox copy has changes nobody copied back, unless ``replace``.
        """
        if len(archive) > MAX_IMPORT_BYTES:
            raise WorkspaceError('too_big', 'The folder is too big to copy in.')
        target = self.project_dir(agent, name)
        git_dir = self._git_dir(agent, name)
        existing = os.path.isdir(git_dir) and os.path.isdir(target)
        if existing and not replace:
            pending = self.changes(agent, name, patch=False)['files']
            if pending:
                listed = ', '.join(f['path'] for f in pending[:8]) + (' ...' if len(pending) > 8 else '')
                raise WorkspaceError('unsynced_changes', f"The sandbox copy of '{name}' has {len(pending)} change(s) "
                                     f'that were not copied back ({listed}). Copy them back first, or copy in '
                                     'again with replace=true to throw them away.')
        try:
            tar = tarfile.open(fileobj=io.BytesIO(archive), mode='r:*')
        except tarfile.TarError as exc:
            raise WorkspaceError('bad_archive', f"Couldn't read the folder archive: {exc}") from None
        with tar:
            members = _safe_members(tar)
            incoming = {m.name for m in members if m.isfile()}
            if existing:
                tracked = self._git(agent, name, 'ls-files', '-z').stdout.split('\0')
                for rel in tracked:
                    if rel and rel not in incoming:
                        try:
                            os.remove(os.path.join(target, rel))
                        except OSError:
                            pass
            else:
                if os.path.isdir(git_dir):
                    shutil.rmtree(git_dir, ignore_errors=True)
                os.makedirs(target, exist_ok=True)
                os.makedirs(os.path.dirname(git_dir), exist_ok=True)
                subprocess.run(['git', 'init', '-q', '--bare', git_dir], check=True,  # noqa: S603,S607
                               capture_output=True, timeout=60)
                self._git(agent, name, 'config', 'core.bare', 'false')
                with open(os.path.join(git_dir, 'info', 'exclude'), 'w', encoding='utf-8') as fh:
                    fh.write('\n'.join(DEFAULT_EXCLUDES) + '\n')
            for member in members:
                dest = os.path.join(target, member.name)
                if member.isdir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    continue
                if os.path.islink(dest):
                    os.remove(dest)
                with open(dest, 'wb') as fh:
                    shutil.copyfileobj(src, fh)
                os.chmod(dest, 0o755 if member.mode & 0o111 else 0o644)
        if source:
            self._git(agent, name, 'config', 'cognitrix.source', str(source)[:1000])
        self._baseline(agent, name, 'Copied in from the computer')
        self._terminal(agent, f'\x1b[2mcopied {len(incoming)} files into {target}\x1b[0m\n')
        return {'path': target, 'files': len(incoming), 'refreshed': existing}

    def _baseline(self, agent: str, name: str, message: str, paths: list[str] | None = None) -> None:
        if paths is None:
            self._git(agent, name, 'add', '-A')
        else:
            self._git(agent, name, 'reset', '-q', check=False)
            for chunk in _chunks(paths, 200):
                self._git(agent, name, 'add', '-A', '--', *chunk)
        self._git(agent, name, 'commit', '-q', '--allow-empty', '--no-verify', '-m', message)

    def source(self, agent: str, name: str) -> str:
        """The folder on the person's computer the project was copied from."""
        got = self._git(agent, name, 'config', '--get', 'cognitrix.source', check=False)
        return got.stdout.strip() if got.returncode == 0 else ''

    def changes(self, agent: str, name: str, patch: bool = True) -> dict:
        """What changed in the project since it was copied in (or last copied back)."""
        if not os.path.isdir(self._git_dir(agent, name)):
            raise WorkspaceError('not_found', f"No project '{name}' in the sandbox. Copy a folder in first.")
        self._git(agent, name, 'add', '-A')
        status = self._git(agent, name, 'diff', '--cached', '--no-renames', '--name-status', '-z', 'HEAD').stdout
        parts = [p for p in status.split('\0')]
        files: dict[str, dict] = {}
        i = 0
        while i + 1 < len(parts):
            code, path = parts[i], parts[i + 1]
            i += 2
            if code:
                files[path] = {'path': path, 'status': {'A': 'added', 'D': 'deleted'}.get(code[0], 'modified'),
                               'added': 0, 'removed': 0, 'binary': False}
        numstat = self._git(agent, name, 'diff', '--cached', '--no-renames', '--numstat', '-z', 'HEAD').stdout
        for row in numstat.split('\0'):
            bits = row.split('\t')
            if len(bits) == 3 and bits[2] in files:
                if bits[0] == '-':
                    files[bits[2]]['binary'] = True
                else:
                    files[bits[2]]['added'], files[bits[2]]['removed'] = int(bits[0]), int(bits[1])
        out = {'project': name, 'path': self.project_dir(agent, name), 'source': self.source(agent, name),
               'files': list(files.values())}
        if patch:
            raw = self._git(agent, name, 'diff', '--cached', '--no-renames', '--no-color', 'HEAD').stdout
            out['patch'] = raw[:MAX_PATCH]
            out['patch_cut'] = len(raw) > MAX_PATCH
        return out

    def export(self, agent: str, name: str, paths: list[str] | None = None) -> dict:
        """Changed files with their content and the baseline blob each one
        started from (so the engine can tell when the computer's copy moved on)."""
        changed = self.changes(agent, name, patch=False)['files']
        wanted = set(paths or [])
        picked = [f for f in changed if not wanted or f['path'] in wanted]
        unknown = sorted(wanted - {f['path'] for f in changed})
        target = self.project_dir(agent, name)
        total = 0
        files = []
        for f in picked:
            item = {'path': f['path'], 'status': f['status'], 'base': None}
            if f['status'] != 'added':
                got = self._git(agent, name, 'rev-parse', f'HEAD:{f["path"]}', check=False)
                item['base'] = got.stdout.strip() if got.returncode == 0 else None
            if f['status'] != 'deleted':
                full = os.path.join(target, f['path'])
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                with open(full, 'rb') as fh:
                    data = fh.read()
                total += len(data)
                if total > MAX_EXPORT_BYTES:
                    raise WorkspaceError('too_big', 'The changes are too big to copy back at once; pick fewer files.')
                item['data'] = base64.b64encode(data).decode()
                item['executable'] = bool(os.stat(full).st_mode & 0o111)
            files.append(item)
        return {'project': name, 'source': self.source(agent, name), 'files': files, 'unknown': unknown}

    def synced(self, agent: str, name: str, paths: list[str]) -> dict:
        """The engine copied these files back: they're the new baseline."""
        if paths:
            self._baseline(agent, name, 'Copied back to the computer', paths)
        return {'ok': True}

    def remove_project(self, agent: str, name: str) -> dict:
        shutil.rmtree(self.project_dir(agent, name), ignore_errors=True)
        shutil.rmtree(self._git_dir(agent, name), ignore_errors=True)
        return {'ok': True}

    def find_files(self, agent: str, base: str, since: float, suffixes: tuple[str, ...], limit: int = 4) -> list[dict]:
        """Files under ``base`` changed after ``since`` (test screenshots)."""
        root = self.resolve(agent, base)
        found: list[tuple[float, str]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ('.git', 'node_modules', '.venv')]
            for name in filenames:
                if name.lower().endswith(suffixes):
                    full = os.path.join(dirpath, name)
                    try:
                        mtime = os.path.getmtime(full)
                    except OSError:
                        continue
                    if mtime >= since:
                        found.append((mtime, full))
        found.sort(reverse=True)
        out = []
        for _, full in found[:limit]:
            try:
                if os.path.getsize(full) > 4_000_000:
                    continue
                with open(full, 'rb') as fh:
                    out.append({'path': full, 'data': base64.b64encode(fh.read()).decode()})
            except OSError:
                continue
        return out


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Regular files and folders with plain relative paths; links and devices
    are skipped, and absolute or ``..`` paths refuse the whole archive."""
    out = []
    for member in tar.getmembers():
        name = member.name.replace('\\', '/')
        while name.startswith('./'):
            name = name[2:]
        norm = os.path.normpath(name) if name else ''
        if not norm or norm == '.':
            continue
        if os.path.isabs(name) or norm == '..' or norm.startswith('../'):
            raise WorkspaceError('bad_archive', f'Refused a path in the folder: {member.name}')
        if not (member.isfile() or member.isdir()):
            continue
        member.name = norm
        out.append(member)
    return out


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = ''
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith('**/', i):
            out += '(?:.*/)?'
            i += 3
            continue
        if pattern.startswith('**', i):
            out += '.*'
            i += 2
            continue
        if c == '*':
            out += '[^/]*'
        elif c == '?':
            out += '[^/]'
        elif c == '{':
            close = pattern.find('}', i)
            if close > i:
                out += '(?:' + '|'.join(re.escape(p) for p in pattern[i + 1:close].split(',')) + ')'
                i = close + 1
                continue
            out += re.escape(c)
        else:
            out += re.escape(c)
        i += 1
    return re.compile(out + r'\Z')


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _short(path: str) -> str:
    return '~' + path[len(HOME):] if path.startswith(HOME) else path


def make_archive(folder: str) -> bytes:
    """tar.gz of a folder (used by tests; the engine builds its own)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for dirpath, _dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(dirpath, name)
                tar.add(full, arcname=os.path.relpath(full, folder))
    return buf.getvalue()


__all__ = ['Workspace', 'WorkspaceError', 'make_archive', 'project_name']
