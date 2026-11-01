"""Shell engine behind the Bash, Bash Output and Kill Shell tools.

Two ways to run a command:

- **Allowlisted**: every part of the command line is an allowlisted program
  (see ``safe_exec.DEFAULT_ALLOWED_COMMANDS``) joined by ``|``, ``&&``, ``||``
  or ``;``, plus the ``cd`` and ``pwd`` builtins. Cognitrix runs these itself
  with ``shell=False``, so no shell ever parses the text, and paths stay
  inside the working folder. Read-only ones run without asking.
- **Full shell**: anything else (redirection, ``$VAR``, scripts, installs,
  PowerShell cmdlets) runs in a real shell — bash/zsh/sh on Linux and macOS,
  PowerShell on Windows — but only after the person approved that exact call,
  or when ``COGNITRIX_SANDBOX_SHELL`` marks the machine as a throwaway sandbox.

Shared behaviour: the working directory persists between calls of a session
(``cd`` sticks, and is pulled back into the working folder if it wanders out),
stdin is closed so nothing hangs on a prompt, a timeout kills the whole process
tree and still returns the output so far, long output keeps its head and tail,
and long-running commands can run in the background and be read or stopped
later.
"""

from __future__ import annotations

import atexit
import base64
import collections
import itertools
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from cognitrix.common.encoding import decode_output, utf8_child_env
from cognitrix.common.safe_exec import CommandNotAllowed, build_argv

IS_WINDOWS = os.name == 'nt'

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
MAX_OUTPUT_CHARS = 30_000
MAX_BACKGROUND_PER_OWNER = 8
MAX_BACKGROUND_BUFFER = 2 * 1024 * 1024

# cmd.exe builtins that have no executable of their own on Windows.
_CMD_BUILTINS = frozenset({'dir', 'type', 'echo'})

# Programs that only read. Anything else on the allowlist (python, npm, git
# commit, mkdir, mv, ...) still runs without a shell but asks first.
_READ_ONLY = frozenset({
    'ls', 'dir', 'pwd', 'cat', 'type', 'head', 'tail', 'grep', 'rg', 'wc',
    'file', 'which', 'where', 'echo', 'date', 'whoami', 'uname', 'find', 'sed',
})
_GIT_READ_ONLY = frozenset({
    'status', 'log', 'diff', 'show', 'rev-parse', 'ls-files', 'blame', 'grep',
    'describe', 'shortlog',
})
_GIT_BRANCH_LIST_FLAGS = frozenset({
    '-a', '-r', '-v', '-vv', '--list', '--all', '--remotes', '--show-current',
})
# Reading these hands secrets to the model, so it always asks.
_SECRET_HINTS = ('.env', 'id_rsa', 'id_ed25519', '.ssh', '.pem', 'credentials', '.netrc', '.pgpass')


class ShellSyntaxError(ValueError):
    """The command uses syntax only a real shell understands."""


def shell_mode() -> str:
    """``ask`` (default): commands outside the allowlist run in a real shell
    once the person approves them. ``allowlist``: they are refused."""
    mode = os.getenv('COGNITRIX_SHELL_MODE', 'ask').strip().lower()
    return mode if mode in ('ask', 'allowlist') else 'ask'


def sandbox_shell_enabled() -> bool:
    return os.getenv('COGNITRIX_SANDBOX_SHELL', '').strip().lower() in ('1', 'true', 'yes')


# ── Parsing ──────────────────────────────────────────────────────────────

def split_command(command: str) -> list[tuple[str, str]]:
    """Split a command line on ``| && || ;`` and newlines outside quotes.

    Returns ``[(operator_before, segment_text), ...]`` (the first operator is
    ``''``). Raises ``ShellSyntaxError`` for syntax that needs a real shell:
    ``&`` (background), ``$``, backticks, redirection, subshells, globs.
    """
    segments: list[tuple[str, str]] = []
    current: list[str] = []
    op = ''
    quote: str | None = None
    i, n = 0, len(command)

    def flush(next_op: str) -> None:
        nonlocal op, current
        text = ''.join(current).strip()
        if not text:
            if next_op in (';', '\n') and not segments and op == '':
                current = []
                return
            if next_op in (';', '\n') and op in (';', '\n'):
                current = []
                return
            raise ShellSyntaxError('empty command between operators')
        segments.append((';' if op == '\n' else op, text))
        op = next_op
        current = []

    while i < n:
        ch = command[i]
        if quote:
            if ch == quote:
                quote = None
            elif quote == '"' and ch in '$`' and not IS_WINDOWS:
                # A shell would expand these inside double quotes.
                raise ShellSyntaxError(f"'{ch}' inside double quotes needs a real shell")
            elif ch == '\\' and quote == '"' and not IS_WINDOWS and i + 1 < n:
                current.append(ch)
                i += 1
                ch = command[i]
            current.append(ch)
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            current.append(ch)
        elif ch == '\\' and not IS_WINDOWS:
            if i + 1 < n and command[i + 1] == '\n':
                i += 2  # line continuation
                continue
            current.append(ch)
            if i + 1 < n:
                current.append(command[i + 1])
                i += 1
        elif command.startswith('&&', i) or command.startswith('||', i):
            flush(command[i:i + 2])
            i += 2
            continue
        elif ch in '|;\n':
            flush(ch)
        elif ch == '&':
            raise ShellSyntaxError("'&' background jobs need run_in_background")
        elif ch in '$`<>(){}*?[]~' or (ch == '#' and (not current or current[-1].isspace())):
            raise ShellSyntaxError(f"'{ch}' needs a real shell")
        else:
            current.append(ch)
        i += 1

    if quote:
        raise ShellSyntaxError('unterminated quote')
    text = ''.join(current).strip()
    if text:
        segments.append((';' if op == '\n' else op, text))
    elif op in ('&&', '||', '|'):
        raise ShellSyntaxError(f"command ends with '{op}'")
    if not segments:
        raise ShellSyntaxError('empty command')
    return segments


@dataclass
class Step:
    op: str                 # operator before this pipeline: '', ';', '&&', '||'
    pipeline: list[list[str]]  # argv per stage; ['cd', path] / ['pwd'] are builtins


@dataclass
class ShellPlan:
    kind: str               # 'allowlisted' or 'shell'
    read_only: bool = False
    steps: list[Step] = field(default_factory=list)
    reason: str = ''


def _argv_read_only(argv: list[str]) -> bool:
    base = argv[0].lower()
    args = argv[1:]
    positional = [a for a in args if not a.startswith('-')]
    if base in ('cd', 'pwd'):
        return True
    if base == 'git':
        sub = next((a for a in args if not a.startswith('-')), '')
        if any(a.startswith('--output') or a in ('--ext-diff', '--textconv') for a in args):
            return False
        if sub == 'branch':
            rest = args[args.index('branch') + 1:]
            return all(a in _GIT_BRANCH_LIST_FLAGS for a in rest)
        return sub in _GIT_READ_ONLY
    if base == 'sort':
        return not any(a == '-o' or a.startswith('--output') or a.startswith('-o') for a in args)
    if base == 'uniq':
        return len(positional) <= 1
    if base == 'hostname':
        return not positional
    if base == 'sed':
        return True  # -i is refused and GNU sed runs with --sandbox
    return base in _READ_ONLY


def plan_command(command: str, root: str | os.PathLike[str]) -> ShellPlan:
    """Decide how ``command`` would run. Never raises."""
    if not command or not command.strip():
        return ShellPlan(kind='shell', reason='empty command')
    try:
        segments = split_command(command)
    except ShellSyntaxError as e:
        return ShellPlan(kind='shell', reason=str(e))

    steps: list[Step] = []
    read_only = True
    for op, text in segments:
        try:
            if re.match(r'^(cd|pwd)(\s|$)', text):
                argv = _split_builtin(text)
                if argv[0] == 'cd' and len(argv) > 2:
                    raise CommandNotAllowed('cd takes one folder')
                if argv[0] == 'pwd':
                    argv = ['pwd']
            else:
                argv = build_argv(text, root=root, quoted_metachars_ok=not IS_WINDOWS)
                if IS_WINDOWS and re.search(r'[%^!]', text):
                    raise CommandNotAllowed("'%', '^' and '!' need a real shell on Windows")
        except (CommandNotAllowed, ValueError) as e:
            reason = str(e)
            if '. Allowed:' in reason:  # keep the long allowlist out of the reply
                reason = reason.split('. Allowed:', 1)[0].replace(' is not allowed', " isn't on the allowlist")
            return ShellPlan(kind='shell', reason=reason)
        read_only = read_only and _argv_read_only(argv)
        if op == '|':
            if argv[0] == 'cd' or steps[-1].pipeline[-1][0] == 'cd':
                return ShellPlan(kind='shell', reason="'cd' can't be piped")
            steps[-1].pipeline.append(argv)
        else:
            steps.append(Step(op=op, pipeline=[argv]))
    return ShellPlan(kind='allowlisted', read_only=read_only, steps=steps)


def _split_builtin(text: str) -> list[str]:
    import shlex
    argv = shlex.split(text, posix=not IS_WINDOWS)
    if IS_WINDOWS:
        argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a for a in argv]
    # `cd ..` is fine: the target is checked against the working folder when it runs.
    return argv


SHELL_TOOL_NAMES = frozenset({'bash'})
SHELL_HELPER_TOOL_NAMES = frozenset({'bash output', 'kill shell'})


def shell_risk(params: dict | None) -> tuple[str, str]:
    """Risk of a Bash call as ``(level, details)``; level is low/medium/high.

    low: allowlisted, read-only, runs at once. medium: allowlisted but runs a
    program or changes files. high: needs the full shell (asks every time),
    or reads something that looks like a secret.
    """
    from cognitrix.config import settings

    command = str((params or {}).get('command') or '')
    plan = plan_command(command, Path(settings.tools_root).expanduser().resolve())
    if plan.kind == 'shell':
        if shell_mode() == 'allowlist' and not sandbox_shell_enabled():
            # The tool refuses it without running anything; don't ask first.
            return 'low', 'Not on the command allowlist; it will be refused'
        return 'high', f'Runs in the full shell ({plan.reason})'
    if mentions_secret(command):
        return 'high', 'May print secrets (keys, .env, credentials)'
    if plan.read_only:
        return 'low', 'Read-only command'
    return 'medium', 'Runs a program or changes files in the working folder'


def mentions_secret(command: str) -> bool:
    lowered = (command or '').lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


# ── Session working directories ─────────────────────────────────────────

_CWDS: collections.OrderedDict[str, Path] = collections.OrderedDict()
_CWDS_LOCK = threading.Lock()


def session_cwd(key: str, root: Path) -> Path:
    with _CWDS_LOCK:
        cwd = _CWDS.get(key)
    if cwd is None or not cwd.is_dir() or not _inside(cwd, root):
        return root
    return cwd


def set_session_cwd(key: str, cwd: Path) -> None:
    with _CWDS_LOCK:
        _CWDS[key] = cwd
        _CWDS.move_to_end(key)
        while len(_CWDS) > 512:
            _CWDS.popitem(last=False)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


# ── Processes ───────────────────────────────────────────────────────────

def child_env() -> dict[str, str]:
    env = utf8_child_env()
    env['GIT_PAGER'] = 'cat'
    env['PAGER'] = 'cat'
    env['GIT_TERMINAL_PROMPT'] = '0'
    env.setdefault('TERM', 'dumb')
    return env


def _group_kwargs() -> dict:
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        return {'creationflags': flags}
    return {'start_new_session': True}


def kill_tree(proc: subprocess.Popen, graceful: bool = False) -> None:
    """Stop a process and everything it started."""
    if proc.poll() is not None and not IS_WINDOWS:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # leftover children
        except OSError:
            pass
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                capture_output=True, timeout=10, **_group_kwargs(),
            )
        else:
            if graceful:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _resolve_program(argv: list[str], cwd: Path, env: dict[str, str]) -> list[str]:
    """argv with the program resolved to a full path (finds npm.cmd etc. on
    Windows, where CreateProcess only looks for .exe)."""
    base = argv[0]
    if IS_WINDOWS and base.lower() in _CMD_BUILTINS:
        return ['cmd.exe', '/d', '/c', *argv]
    found = shutil.which(base, path=env.get('PATH'))
    if found is None and ('/' in base or '\\' in base):
        candidate = (cwd / base)
        if candidate.exists():
            found = str(candidate)
    if found is None:
        raise FileNotFoundError(base)
    return [found, *argv[1:]]


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep the start and the end (errors usually come last)."""
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n\n[... {omitted:,} characters omitted. Narrow the command "
        f"(grep, head, tail) to see the middle ...]\n\n{text[-tail:]}"
    )


@dataclass
class RunResult:
    output: str
    exit_code: int | None
    timed_out: bool = False
    cwd: Path | None = None
    notes: list[str] = field(default_factory=list)


def _communicate(procs: list[subprocess.Popen], deadline: float) -> tuple[bytes, bytes, bool]:
    last = procs[-1]
    try:
        out, err = last.communicate(timeout=max(0.1, deadline - time.monotonic()))
        for p in procs[:-1]:
            p.wait(timeout=max(0.1, deadline - time.monotonic()))
        return out or b'', err or b'', False
    except subprocess.TimeoutExpired:
        for p in procs:
            kill_tree(p)
        try:
            out, err = last.communicate(timeout=5)
        except Exception:
            out, err = b'', b''
        return out or b'', err or b'', True


def run_allowlisted(plan: ShellPlan, cwd: Path, root: Path, timeout: int) -> RunResult:
    env = child_env()
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    status = 0
    timed_out = False
    for step in plan.steps:
        if step.op == '&&' and status != 0:
            continue
        if step.op == '||' and status == 0:
            continue
        first = step.pipeline[0]
        if first[0] == 'cd':
            target_text = first[1] if len(first) > 1 else str(root)
            target = (cwd / Path(target_text).expanduser()).resolve()
            if not target.is_dir():
                chunks.append(f'cd: no such folder: {target_text}')
                status = 1
            elif not _inside(target, root):
                chunks.append(f"cd: '{target_text}' is outside the working folder {root}")
                status = 1
            else:
                cwd = target
                status = 0
            continue
        if len(step.pipeline) == 1 and first[0] == 'pwd':
            chunks.append(str(cwd))
            status = 0
            continue

        procs: list[subprocess.Popen] = []
        with tempfile.TemporaryFile() as errf:
            try:
                prev = None
                for argv in step.pipeline:
                    if argv[0] == 'pwd':
                        argv = ['echo', str(cwd)] if not IS_WINDOWS else ['cmd.exe', '/d', '/c', 'cd']
                        program = argv if IS_WINDOWS else _resolve_program(argv, cwd, env)
                    else:
                        program = _resolve_program(argv, cwd, env)
                    p = subprocess.Popen(
                        program if IS_WINDOWS else [argv[0], *program[1:]],
                        executable=None if IS_WINDOWS else program[0],
                        stdin=prev.stdout if prev else subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=errf,
                        cwd=str(cwd), env=env, **_group_kwargs(),
                    )
                    if prev is not None and prev.stdout is not None:
                        prev.stdout.close()  # lets the writer see a closed pipe
                    procs.append(p)
                    prev = p
            except FileNotFoundError as e:
                for p in procs:
                    kill_tree(p)
                chunks.append(f'{e.args[0]}: command not found')
                status = 127
                continue
            out, _, timed_out = _communicate(procs, deadline)
            errf.seek(0)
            err = errf.read()
        text = decode_output(out).rstrip('\n')
        err_text = decode_output(err).rstrip('\n')
        if text:
            chunks.append(text)
        if err_text:
            chunks.append(err_text)
        status = procs[-1].returncode if procs[-1].returncode is not None else -1
        if timed_out:
            break
    return RunResult(
        output='\n'.join(chunks), exit_code=None if timed_out else status,
        timed_out=timed_out, cwd=cwd,
    )


# ── Full shell ──────────────────────────────────────────────────────────

def detect_shell() -> tuple[str, str]:
    """Return ``(path, flavor)`` where flavor is posix, powershell or cmd.

    ``COGNITRIX_SHELL`` overrides it, e.g. Git Bash's ``bash.exe`` on Windows.
    """
    override = os.getenv('COGNITRIX_SHELL', '').strip()
    if override:
        name = Path(override).name.lower()
        if 'pwsh' in name or 'powershell' in name:
            return override, 'powershell'
        if name.startswith('cmd'):
            return override, 'cmd'
        return override, 'posix'
    if IS_WINDOWS:
        for candidate in ('pwsh.exe', 'powershell.exe'):
            found = shutil.which(candidate)
            if found:
                return found, 'powershell'
        return os.environ.get('COMSPEC', 'cmd.exe'), 'cmd'
    user_shell = os.environ.get('SHELL', '')
    if Path(user_shell).name in ('bash', 'zsh') and Path(user_shell).exists():
        return user_shell, 'posix'
    for candidate in ('/bin/bash', '/usr/bin/bash', '/bin/zsh', '/bin/sh'):
        if Path(candidate).exists():
            return candidate, 'posix'
    return 'sh', 'posix'


def shell_label() -> str:
    path, flavor = detect_shell()
    if flavor == 'powershell':
        return 'PowerShell'
    if flavor == 'cmd':
        return 'cmd.exe'
    return Path(path).name


_POSIX_WRAPPER = (
    "trap '__cog_s=$?; "
    "{ cygpath -w \"$PWD\" 2>/dev/null || pwd -P; } > \"$COGNITRIX_CWD_FILE\" 2>/dev/null; "
    "exit $__cog_s' EXIT\n"
)

_PS_WRAPPER = """$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$__cogOk = $true
$__cogCode = 0
try {
%s
  $__cogOk = $?
  $__cogCode = $LASTEXITCODE
} catch {
  $__cogOk = $false
  Write-Error $_
} finally {
  (Get-Location).ProviderPath | Set-Content -LiteralPath $env:COGNITRIX_CWD_FILE -Encoding utf8
}
if ($__cogCode) { exit $__cogCode }
if (-not $__cogOk) { exit 1 }
exit 0
"""


def build_shell_argv(command: str) -> list[str]:
    path, flavor = detect_shell()
    if flavor == 'powershell':
        script = _PS_WRAPPER % command
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        return [path, '-NoLogo', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded]
    if flavor == 'cmd':
        return [path, '/d', '/s', '/c', command]
    return [path, '-c', _POSIX_WRAPPER + command]


def _read_cwd_file(path: str, fallback: Path, root: Path, notes: list[str]) -> Path:
    try:
        text = Path(path).read_text(encoding='utf-8-sig').strip()
    except OSError:
        return fallback
    if not text:
        return fallback
    try:
        new = Path(text).resolve()
    except (OSError, RuntimeError):
        return fallback
    if not new.is_dir():
        return fallback
    if not _inside(new, root):
        notes.append(f'The shell left the working folder; the next command starts in {root}.')
        return root
    return new


def run_full_shell(command: str, cwd: Path, root: Path, timeout: int) -> RunResult:
    env = child_env()
    fd, cwd_file = tempfile.mkstemp(prefix='cognitrix-cwd-', suffix='.txt')
    os.close(fd)
    env['COGNITRIX_CWD_FILE'] = cwd_file
    notes: list[str] = []
    try:
        proc = subprocess.Popen(
            build_shell_argv(command), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(cwd), env=env, **_group_kwargs(),
        )
        out, err, timed_out = _communicate([proc], time.monotonic() + timeout)
        new_cwd = _read_cwd_file(cwd_file, cwd, root, notes)
    finally:
        try:
            os.unlink(cwd_file)
        except OSError:
            pass
    chunks = [t for t in (decode_output(out).rstrip('\n'), decode_output(err).rstrip('\n')) if t]
    return RunResult(
        output='\n'.join(chunks), exit_code=None if timed_out else proc.returncode,
        timed_out=timed_out, cwd=new_cwd, notes=notes,
    )


def format_result(result: RunResult, timeout: int) -> str:
    parts: list[str] = []
    body = truncate_output(result.output.strip('\n'))
    if body:
        parts.append(body)
    if result.timed_out:
        parts.append(
            f'[Timed out after {timeout}s and was stopped. For servers or watchers, '
            'use run_in_background=true.]'
        )
    elif result.exit_code:
        parts.append(f'[Exit code {result.exit_code}]')
    elif not body:
        parts.append('[Command finished with no output]')
    parts.extend(f'[{note}]' for note in result.notes)
    return '\n\n'.join(parts)


# ── Background processes ────────────────────────────────────────────────

@dataclass
class BackgroundShell:
    id: str
    owner: str
    command: str
    proc: subprocess.Popen
    started: float
    buffer: bytearray = field(default_factory=bytearray)
    dropped: int = 0          # bytes discarded from the front of the buffer
    read_pos: int = 0         # absolute offset already returned to the model
    killed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader: threading.Thread | None = None

    def status(self) -> str:
        code = self.proc.poll()
        if code is None:
            return 'running'
        if self.killed:
            return 'stopped'
        return f'exited with code {code}'


_BACKGROUND: dict[str, BackgroundShell] = {}
_BG_LOCK = threading.Lock()
_BG_IDS = itertools.count(1)


def _pump(shell: BackgroundShell) -> None:
    stream = shell.proc.stdout
    if stream is None:
        return
    while True:
        try:
            chunk = stream.read1(65536) if hasattr(stream, 'read1') else stream.read(65536)
        except (OSError, ValueError):
            break
        if not chunk:
            break
        with shell.lock:
            shell.buffer.extend(chunk)
            overflow = len(shell.buffer) - MAX_BACKGROUND_BUFFER
            if overflow > 0:
                del shell.buffer[:overflow]
                shell.dropped += overflow


def start_background(argv: list[str], *, owner: str, command: str, cwd: Path) -> BackgroundShell:
    with _BG_LOCK:
        running = [s for s in _BACKGROUND.values() if s.owner == owner and s.proc.poll() is None]
        if len(running) >= MAX_BACKGROUND_PER_OWNER:
            raise RuntimeError(
                f'{len(running)} background commands are already running. '
                'Stop one with Kill Shell first.'
            )
    env = child_env()
    env['COGNITRIX_CWD_FILE'] = os.devnull
    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, cwd=str(cwd), env=env, **_group_kwargs(),
    )
    shell = BackgroundShell(
        id=f'shell_{next(_BG_IDS)}', owner=owner, command=command, proc=proc,
        started=time.time(),
    )
    shell.reader = threading.Thread(target=_pump, args=(shell,), daemon=True)
    shell.reader.start()
    with _BG_LOCK:
        _BACKGROUND[shell.id] = shell
    return shell


def get_background(shell_id: str, owner: str) -> BackgroundShell | None:
    with _BG_LOCK:
        shell = _BACKGROUND.get((shell_id or '').strip())
    if shell is None or shell.owner != owner:
        return None
    return shell


def list_background(owner: str) -> list[BackgroundShell]:
    with _BG_LOCK:
        return [s for s in _BACKGROUND.values() if s.owner == owner]


def read_background(shell: BackgroundShell, pattern: str | None = None) -> str:
    """New output since the last read, optionally only lines matching ``pattern``."""
    if shell.proc.poll() is not None and shell.reader is not None:
        shell.reader.join(timeout=2)
    with shell.lock:
        start = max(shell.read_pos, shell.dropped)
        skipped = start - shell.read_pos
        data = bytes(shell.buffer[start - shell.dropped:])
        shell.read_pos = shell.dropped + len(shell.buffer)
    text = decode_output(data)
    if pattern:
        regex = re.compile(pattern)
        text = '\n'.join(line for line in text.splitlines() if regex.search(line))
    parts = [f'[{shell.id}: {shell.status()}]']
    if skipped:
        parts.append(f'[{skipped:,} bytes of older output were discarded]')
    parts.append(truncate_output(text.rstrip('\n')) if text.strip() else '[No new output]')
    return '\n'.join(parts)


def stop_background(shell: BackgroundShell) -> str:
    if shell.proc.poll() is not None:
        return f'{shell.id} had already {shell.status()}.'
    shell.killed = True
    kill_tree(shell.proc, graceful=True)
    return f'Stopped {shell.id} ({shell.command}).'


@atexit.register
def _stop_all_background() -> None:  # pragma: no cover - process exit
    with _BG_LOCK:
        shells = list(_BACKGROUND.values())
    for shell in shells:
        if shell.proc.poll() is None:
            kill_tree(shell.proc)
