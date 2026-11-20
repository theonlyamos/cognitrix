"""Saved "Always allow" answers for commands a person approves one by one:
the full shell on this machine and every command on a paired PC.

A rule is a command prefix (the program and, when it has one, its
subcommand: ``git status``, ``npm test``, ``ls``) for one person and one
computer. A later command runs without asking only when it starts with
that prefix and is a single plain command: no ``;``, ``&&``, ``||``, pipes,
redirects, ``$(...)`` or backticks, so ``git status && rm -rf ~`` never
rides on an approved ``git status``.

Persistence rule: ``save()`` once at creation; rules are deleted, never changed.
"""
from __future__ import annotations

import re
import shlex
from datetime import UTC, datetime
from typing import Any

from odbms import Model

MAX_RULES_PER_USER = 200
LOCAL = 'local'
"""The computer key for this machine; a paired PC is 'pc:<device id>'."""

_UNSAFE = re.compile(r'[;&|<>`\n\r]|\$\(|\$\{')
_WORD = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:+@/-]*$')
# Programs whose first argument is a subcommand worth keeping in the prefix.
_SUBCOMMANDS = {
    'git', 'npm', 'pnpm', 'yarn', 'bun', 'npx', 'pip', 'pip3', 'uv', 'poetry', 'cargo', 'go', 'docker',
    'kubectl', 'make', 'dotnet', 'gradle', 'mvn', 'brew', 'apt', 'apt-get', 'gh', 'python', 'python3',
    'node', 'deno', 'rustup', 'composer', 'bundle', 'rails', 'mix', 'swift', 'flutter', 'dart', 'terraform',
}


# Script runners: only "python build.py" style, never a bare "python" that
# would cover "python -c ...".
_INTERPRETERS = {'python', 'python3', 'node', 'deno', 'bun', 'npx', 'ruby', 'perl', 'php', 'go'}
# Never offered: one approval would cover too much.
_NEVER = {'rm', 'rmdir', 'dd', 'mkfs', 'shutdown', 'reboot', 'kill', 'killall', 'pkill', 'chmod', 'chown',
          'curl', 'wget', 'scp', 'ssh', 'rsync', 'format', 'del', 'erase', 'reg', 'diskpart', 'mv', 'cp'}


class CommandRule(Model):
    user_id: str
    computer: str = LOCAL
    prefix: str
    created: str = ''


MODELS = (CommandRule,)


def _tokens(command: str) -> list[str] | None:
    text = str(command or '').strip()
    if not text or _UNSAFE.search(text):
        return None
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return None


def prefix_for(command: Any) -> str | None:
    """The prefix "Always allow" saves for this command, or None when the
    command can't be allowed this way (several commands chained, redirects)."""
    tokens = _tokens(command)
    if not tokens or not _WORD.match(tokens[0]) or '=' in tokens[0]:
        return None
    program = tokens[0]
    base = program.rsplit('/', 1)[-1].lower()
    if base in ('sudo', 'su', 'doas', 'env', 'sh', 'bash', 'zsh', 'pwsh', 'powershell', 'cmd', 'eval', 'xargs'):
        return None  # these run whatever comes after them
    if base in _NEVER:
        return None
    if base in _INTERPRETERS and (len(tokens) < 2 or tokens[1].startswith('-') or not _WORD.match(tokens[1])):
        return None
    if base in _SUBCOMMANDS and len(tokens) > 1 and _WORD.match(tokens[1]) and not tokens[1].startswith('-'):
        return f'{program} {tokens[1]}'
    return program


def matches(prefix: str, command: Any) -> bool:
    tokens = _tokens(command)
    want = str(prefix or '').split()
    return bool(tokens and want) and tokens[:len(want)] == want


def computer_key(device_id: Any = None) -> str:
    return f'pc:{device_id}' if device_id else LOCAL


async def rules_of(user_id: str) -> list[CommandRule]:
    rows = list(await CommandRule.find({'user_id': str(user_id)}) or [])
    return sorted(rows, key=lambda r: r.created)


async def allowed(user_id: Any, computer: str, command: Any) -> bool:
    if not user_id or not command:
        return False
    try:
        rows = await CommandRule.find({'user_id': str(user_id), 'computer': computer}) or []
    except Exception:
        return False
    return any(matches(r.prefix, command) for r in rows)


async def add(user_id: Any, computer: str, command: Any) -> CommandRule | None:
    prefix = prefix_for(command)
    if not user_id or not prefix:
        return None
    rows = await rules_of(str(user_id))
    for row in rows:
        if row.computer == computer and row.prefix == prefix:
            return row
    if len(rows) >= MAX_RULES_PER_USER:
        return None
    row = CommandRule(user_id=str(user_id), computer=computer, prefix=prefix,
                      created=datetime.now(UTC).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S'))
    await row.save()
    return row


async def remove(user_id: str, rule_id: str) -> bool:
    row = await CommandRule.get(str(rule_id))
    if row is None or str(row.user_id) != str(user_id):
        return False
    await CommandRule.delete_many({'id': row.id})
    return True


def view(row: CommandRule) -> dict[str, Any]:
    return {'id': str(row.id), 'computer': row.computer, 'prefix': row.prefix, 'created': row.created}
