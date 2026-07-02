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
import os
import re
import shlex
import subprocess
from pathlib import Path

# Base commands permitted for tool/skill-driven execution. Read-mostly and
# developer-workflow commands only; nothing that mutates the system broadly.
DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    'ls', 'dir', 'pwd', 'cat', 'type', 'head', 'tail', 'find', 'grep', 'rg',
    'wc', 'sort', 'uniq', 'awk', 'sed', 'file', 'which', 'where', 'echo',
    'git', 'npm', 'pnpm', 'pip', 'pip3', 'python', 'python3', 'node', 'uv',
    'date', 'whoami', 'hostname', 'uname',
})

# Shell metacharacters / patterns that must never reach execution. Rejected even
# though we always run with shell=False — defense in depth against argv smuggling
# and path traversal in command arguments.
_FORBIDDEN = re.compile(r"""[;&|`$><\n\r]|\.\.""")

DEFAULT_TIMEOUT = 30


class CommandNotAllowed(Exception):
    """Raised when a command is not on the allow-list or contains forbidden syntax."""


class PathEscapesRoot(Exception):
    """Raised when a resolved path falls outside the permitted root directory."""


def build_argv(command: str, allowed: frozenset[str] | None = None) -> list[str]:
    """Validate ``command`` and return its argv, or raise ``CommandNotAllowed``.

    Rejects empty input, shell metacharacters (``; & | ` $ > < .. newline``), and
    any base command not present in ``allowed``. Splits with ``shlex`` so the caller
    can execute with ``shell=False``.
    """
    allowed = allowed if allowed is not None else DEFAULT_ALLOWED_COMMANDS

    if not command or not command.strip():
        raise CommandNotAllowed("Empty command")

    if _FORBIDDEN.search(command):
        raise CommandNotAllowed("Command contains forbidden shell metacharacters")

    argv = shlex.split(command, posix=(os.name != 'nt'))
    if not argv:
        raise CommandNotAllowed("Empty command")

    base = argv[0].lower()
    if base not in allowed:
        raise CommandNotAllowed(
            f"Command '{base}' is not allowed. Allowed: {', '.join(sorted(allowed))}"
        )
    return argv


def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    if out:
        return f"{out}\n\n[stderr: {err}]" if err and returncode != 0 else out
    if returncode != 0:
        return f"Command failed (exit code {returncode}): {err}" if err else f"Command failed (exit code {returncode})"
    return "Command executed successfully (no output)"


def run_whitelisted(
    command: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    allowed: frozenset[str] | None = None,
) -> str:
    """Synchronously run a whitelisted command with ``shell=False``. Returns output text."""
    argv = build_argv(command, allowed)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=cwd, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    return _format_output(proc.stdout, proc.stderr, proc.returncode)


async def run_whitelisted_async(
    command: str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    allowed: frozenset[str] | None = None,
) -> str:
    """Asynchronously run a whitelisted command with ``shell=False``. Returns output text."""
    argv = build_argv(command, allowed)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return f"Command timed out after {timeout}s"
    return _format_output(
        stdout.decode('utf-8', errors='replace'),
        stderr.decode('utf-8', errors='replace'),
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
    for bad in ("git status; rm -rf .", "git $(id)", "cat a | sh", "echo x > f",
                "cat ../secret", "rm -rf /", "curl http://x"):
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
