"""Shared safety boundaries for tool/skill execution.

Two responsibilities, deliberately kept in one small module so there is a single
place to audit and a single place to fix:

- ``run_whitelisted`` / ``run_whitelisted_async`` — run a shell command only if its
  base command is on an allow-list, never through a shell (``shell=False``), with
  shell metacharacters rejected up front. This is the boundary for the ``bash`` tool
  and the skills dynamic-context ``!`cmd`` feature.
- ``resolve_within_root`` — resolve a user/agent-supplied path and refuse anything
  that escapes a configured root (including via ``..`` or symlinks). This is the
  boundary for the ``Read``/``Write``/``Edit`` file tools.
"""

from __future__ import annotations

import asyncio
import functools
import os
import re
import shlex
import subprocess
from pathlib import Path

from cognitrix.common.encoding import decode_output, utf8_child_env
from cognitrix.common.process_security import (
    HostProcessMode,
    require_host_process_authority,
)

# Base commands permitted for tool/skill-driven execution. Read-mostly,
# developer-workflow, and benign filesystem-scaffolding commands only; nothing
# that mutates the system broadly. Notably absent: rm/del (destructive) — path
# deletion goes through the gated delete tools, not the shell. Path arguments
# are still confined by the forbidden-metacharacter check (no `..`, no
# redirection, no chaining), so mkdir/mv/cp operate within the working tree.
DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    'ls', 'dir', 'pwd', 'cat', 'type', 'head', 'tail', 'find', 'grep', 'rg',
    'wc', 'sort', 'uniq', 'awk', 'sed', 'file', 'which', 'where', 'echo',
    'git', 'npm', 'pnpm', 'pip', 'pip3', 'python', 'python3', 'node', 'uv',
    'date', 'whoami', 'hostname', 'uname',
    # Benign filesystem scaffolding for real build/coding workflows.
    'mkdir', 'rmdir', 'mv', 'cp', 'touch',
})

# Shell metacharacters / patterns that must never reach execution. Rejected even
# though we always run with shell=False — defense in depth against argv smuggling
# and path traversal in command arguments.
_FORBIDDEN = re.compile(r"""[;&|`$><\n\r]|\.\.""")

# Flags that turn an otherwise-safe base command into arbitrary code execution
# (or in-place mutation). Rejected even when the base command is allowed, so
# `python script.py` works but `python -c '...'` does not. Base command -> set
# of flag tokens; a token that *starts with* one of these is rejected (covers
# `sed -i.bak`). Matched case-sensitively on the split argv.
_DANGEROUS_ARG_TOKENS: dict[str, frozenset[str]] = {
    'python': frozenset({'-c'}),
    'python3': frozenset({'-c'}),
    'node': frozenset({'-e', '--eval', '-p', '--print'}),
    'find': frozenset({'-exec', '-execdir', '-ok', '-okdir', '-delete', '-fprintf', '-fprint'}),
    # rg --pre/--pre-glob and sort --compress-program run a program.
    'rg': frozenset({'--pre'}),
    'sort': frozenset({'--compress-program'}),
    'sed': frozenset({'-i', '--in-place'}),
}

# git options that run arbitrary programs (config overrides can set
# core.pager/core.sshCommand/alias.*; pack options name a helper to execute).
_GIT_GLOBAL_DANGEROUS = frozenset({'-c', '--config-env', '--exec-path'})
_GIT_ANYWHERE_DANGEROUS = (
    '--upload-pack', '--receive-pack', '--exec=', '--config-env',
    # git grep -O/--open-files-in-pager=<cmd> runs a program.
    '-O', '--open-files-in-pager',
)

# Without GNU sed's --sandbox, refuse scripts that could use the e (execute),
# w/W (write file) or r/R (read file) commands, or the s///e and s///w flags.
def _sed_scripts(args: list[str]) -> list[str]:
    """The script parts of a sed argv: -e values, else the first operand."""
    scripts, first_operand, take_next = [], None, False
    for tok in args:
        if take_next:
            scripts.append(tok)
            take_next = False
        elif tok in ('-e', '--expression'):
            take_next = True
        elif tok.startswith('--expression='):
            scripts.append(tok.split('=', 1)[1])
        elif tok.startswith('-e') and len(tok) > 2:
            scripts.append(tok[2:])
        elif not tok.startswith('-') and first_operand is None:
            first_operand = tok
    return scripts or ([first_operand] if first_operand is not None else [])


def _sed_script_unsafe(script: str) -> bool:
    """Scan once (no backtracking regex): skip s/// and y/// bodies, flag
    s///e or s///w, and flag any e/E/w/W/r/R left elsewhere in the script."""
    i, n = 0, len(script)
    while i < n:
        ch = script[i]
        if ch in 'sy' and i + 1 < n and script[i + 1] not in ' \t\n;\\':
            delim, i, parts = script[i + 1], i + 2, 0
            while i < n and parts < 2:
                if script[i] == '\\':
                    i += 2
                    continue
                if script[i] == delim:
                    parts += 1
                i += 1
            flags_start = i
            while i < n and script[i].isalnum():
                i += 1
            if ch == 's' and any(f in 'ew' for f in script[flags_start:i]):
                return True
            continue
        if ch in 'eEwWrR':
            return True
        i += 1
    return False


DEFAULT_TIMEOUT = 30


@functools.lru_cache(maxsize=1)
def _gnu_sed() -> bool:
    try:
        proc = subprocess.run(['sed', '--sandbox', '-n', 'p', os.devnull],
                              capture_output=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def _check_paths_within(argv: list[str], root: str | os.PathLike[str]) -> None:
    """Absolute and home-relative paths must stay inside ``root``."""
    root_path = Path(root).resolve()
    for tok in argv[1:]:
        values = [tok]
        if tok.startswith('-') and '=' in tok:
            values.append(tok.split('=', 1)[1])
        for value in values:
            if not (value.startswith(('/', '~', '\\')) or re.match(r'^[A-Za-z]:[\\/]', value)):
                continue
            try:
                resolved = Path(os.path.expanduser(value)).resolve()
            except (OSError, RuntimeError):
                raise CommandNotAllowed(f"Path '{value}' could not be resolved")
            if resolved != root_path and root_path not in resolved.parents:
                raise CommandNotAllowed(f"Path '{value}' is outside the working directory")


def _unquoted_text(command: str) -> str:
    """``command`` with the contents of quoted strings blanked out."""
    out, quote, i = [], None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == '\\' and quote == '"':
                i += 1
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == '\\':
            out.append(ch)
            i += 2
            continue
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


class CommandNotAllowed(Exception):
    """Raised when a command is not on the allow-list or contains forbidden syntax."""


class PathEscapesRoot(Exception):
    """Raised when a resolved path falls outside the permitted root directory."""


def build_argv(
    command: str,
    allowed: frozenset[str] | None = None,
    *,
    root: str | os.PathLike[str] | None = None,
    quoted_metachars_ok: bool = False,
) -> list[str]:
    """Validate ``command`` and return its argv, or raise ``CommandNotAllowed``.

    With ``quoted_metachars_ok`` (POSIX only), ``| ; & $ < >`` and backticks
    are accepted inside quotes, where ``shell=False`` treats them as plain
    text (``grep "a|b" f``). ``..`` is refused everywhere, and awk keeps the
    strict check because its program text can pipe to commands.

    Rejects empty input, shell metacharacters (``; & | ` $ > < .. newline``), and
    any base command not present in ``allowed``. Splits with ``shlex`` so the caller
    can execute with ``shell=False``. With ``root``, absolute or ``~`` paths in
    the arguments must resolve inside it.
    """
    allowed = allowed if allowed is not None else DEFAULT_ALLOWED_COMMANDS

    if not command or not command.strip():
        raise CommandNotAllowed("Empty command")

    argv = shlex.split(command, posix=(os.name != 'nt'))
    if not argv:
        raise CommandNotAllowed("Empty command")

    base = argv[0].lower()
    relaxed = quoted_metachars_ok and os.name != 'nt' and base not in ('awk', 'gawk', 'mawk')
    checked = _unquoted_text(command) if relaxed else command
    if _FORBIDDEN.search(checked) or '..' in command:
        raise CommandNotAllowed("Command contains forbidden shell metacharacters")
    if base not in allowed:
        raise CommandNotAllowed(
            f"Command '{base}' is not allowed. Allowed: {', '.join(sorted(allowed))}"
        )

    dangerous = _DANGEROUS_ARG_TOKENS.get(base)
    if dangerous:
        for tok in argv[1:]:
            if any(tok == d or tok.startswith(d) for d in dangerous):
                raise CommandNotAllowed(
                    f"Flag '{tok}' is not allowed for '{base}' (arbitrary code execution)"
                )
    # awk's system()/getline-pipe give arbitrary execution via the program text.
    if base in ('awk', 'gawk', 'mawk') and any('system(' in tok for tok in argv[1:]):
        raise CommandNotAllowed("awk 'system(...)' is not allowed (arbitrary code execution)")

    if base == 'git':
        for tok in argv[1:]:
            if not tok.startswith('-'):
                break  # global options end at the subcommand
            if tok.split('=', 1)[0] in _GIT_GLOBAL_DANGEROUS:
                raise CommandNotAllowed(f"git option '{tok}' is not allowed (it can run programs)")
        for tok in argv[1:]:
            if tok.startswith(_GIT_ANYWHERE_DANGEROUS):
                raise CommandNotAllowed(f"git option '{tok}' is not allowed (it can run programs)")

    if base == 'sed':
        if _gnu_sed():
            # GNU sed refuses e/r/w commands in sandbox mode.
            argv = [argv[0], '--sandbox', *argv[1:]]
        elif any(_sed_script_unsafe(script) for script in _sed_scripts(argv[1:])):
            raise CommandNotAllowed("sed e/r/w commands are not allowed")
        elif any(tok in ('-f', '--file') or tok.startswith('--file=') for tok in argv[1:]):
            raise CommandNotAllowed("sed script files are not allowed without GNU sed")

    if root is not None:
        _check_paths_within(argv, root)

    return argv


def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    if returncode != 0:
        head = f"Command failed (exit code {returncode})"
        parts = [head + (f": {err}" if err and not out else "")]
        if out:
            parts.append(out)
            if err:
                parts.append(f"[stderr: {err}]")
        return "\n\n".join(parts)
    # Successful commands still report warnings written to stderr.
    if out:
        return f"{out}\n\n[stderr: {err}]" if err else out
    if err:
        return f"[stderr: {err}]"
    return "Command executed successfully (no output)"


def run_whitelisted(
    command: str,
    *,
    host_process_mode: HostProcessMode,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    allowed: frozenset[str] | None = None,
) -> str:
    """Synchronously run a whitelisted command with ``shell=False``. Returns output text."""
    require_host_process_authority(host_process_mode)
    argv = build_argv(command, allowed, root=cwd if cwd is not None else os.getcwd())
    try:
        # Bytes, not text=True: Windows would decode with cp1252 (strict) and
        # raise on UTF-8 output from git/python/node.
        proc = subprocess.run(
            argv, capture_output=True, cwd=cwd, timeout=timeout,
            env=utf8_child_env(),
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    return _format_output(
        decode_output(proc.stdout), decode_output(proc.stderr), proc.returncode,
    )


async def run_whitelisted_async(
    command: str,
    *,
    host_process_mode: HostProcessMode,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    allowed: frozenset[str] | None = None,
) -> str:
    """Asynchronously run a whitelisted command with ``shell=False``. Returns output text."""
    require_host_process_authority(host_process_mode)
    argv = build_argv(command, allowed, root=cwd if cwd is not None else os.getcwd())
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=utf8_child_env(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return f"Command timed out after {timeout}s"
    return _format_output(
        decode_output(stdout),
        decode_output(stderr),
        proc.returncode or 0,
    )


def resolve_within_root(path: str | os.PathLike[str], root: str | os.PathLike[str] | None = None) -> Path:
    """Resolve ``path`` and ensure it stays within ``root`` (default: cwd).

    Relative paths resolve against ``root``; absolute paths must already live under it.
    Raises ``PathEscapesRoot`` for any escape, including via ``..`` or symlinks
    (``Path.resolve`` collapses both before the containment check).
    """
    root_path = Path(root).expanduser().resolve() if root is not None else Path.cwd().resolve()
    candidate = Path(path).expanduser()
    full = candidate.resolve() if candidate.is_absolute() else (root_path / candidate).resolve()

    if full != root_path and root_path not in full.parents:
        raise PathEscapesRoot(f"Path '{path}' resolves outside the permitted root '{root_path}'")
    return full


if __name__ == "__main__":
    # Runnable self-check for the security-critical logic.
    import tempfile

    # Whitelist + metacharacter rejection
    assert build_argv("git status")[0] == "git"
    assert build_argv("python script.py")[:2] == ["python", "script.py"]  # legit use allowed
    assert build_argv("mkdir -p src/pkg")[0] == "mkdir"  # benign fs scaffolding allowed
    for bad in ("git status; rm -rf .", "git $(id)", "cat a | sh", "echo x > f",
                "cat ../secret", "rm -rf /", "curl http://x",
                # Inline-exec / mutation flags rejected even for allowed base cmds:
                "python -c import os", "node -e process.exit()",
                "find . -delete", "sed -i s/a/b/ f", "awk 'BEGIN{system(\"id\")}'"):
        try:
            build_argv(bad)
            raise SystemExit(f"FAIL: should have rejected: {bad!r}")
        except CommandNotAllowed:
            pass

    # Path confinement
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert resolve_within_root("sub/f.txt", root) == (root / "sub" / "f.txt").resolve()
        for bad in ("../../etc/passwd", "/etc/passwd", "..", "sub/../../x"):
            try:
                resolve_within_root(bad, root)
                raise SystemExit(f"FAIL: should have rejected path: {bad!r}")
            except PathEscapesRoot:
                pass

    print("safe_exec self-check OK")
