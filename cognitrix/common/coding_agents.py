"""Coding agent CLIs that Cognitrix agents can hand work to.

An agent with the Coding Agent tool can give a coding job to a coding agent
installed on this computer (Claude Code, Codex, Gemini CLI). The CLI runs
headless in a working folder, in the background, through the same registry as
``Bash(run_in_background=true)``, so its output is read with Bash Output and
it is stopped with Kill Shell.

Each CLI is started with a fixed argv. The task text goes in on stdin, never on
the command line, so nothing in it is parsed by a shell (npm installs these
CLIs as ``.cmd`` files on Windows, which run through cmd.exe).

Modes map to each CLI's own permission settings:

- ``read_only``: may read and answer, not change files (reviews, plans,
  questions about the code).
- ``edit``: may edit files in the folder. Claude Code accepts edits but its
  shell commands are refused; Codex runs commands in its workspace sandbox;
  Gemini CLI auto-approves edits.
- ``full``: no limits. The CLI can run any command without asking.

The CLIs sign in with their own login (``claude login``, ``codex login``,
``gemini``) or the usual API key variables in the environment.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MODES = ('read_only', 'edit', 'full')
DEFAULT_MODE = 'edit'
DEFAULT_TIME_LIMIT = 60 * 60
MAX_WAIT = 600
MAX_TASK_CHARS = 100_000

_MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@\-\[\]]{0,99}$')


@dataclass(frozen=True)
class CodingAgentCLI:
    key: str
    label: str
    program: str
    install_hint: str
    build: Callable[[str, str | None], list[str]]  # (mode, model) -> args after the program
    mode_notes: dict[str, str]


def _claude_args(mode: str, model: str | None) -> list[str]:
    permission = {'read_only': 'plan', 'edit': 'acceptEdits', 'full': 'bypassPermissions'}[mode]
    args = ['-p', '--output-format', 'text', '--permission-mode', permission]
    if model:
        args += ['--model', model]
    return args


def _codex_args(mode: str, model: str | None) -> list[str]:
    sandbox = {'read_only': 'read-only', 'edit': 'workspace-write', 'full': 'danger-full-access'}[mode]
    args = ['exec', '--skip-git-repo-check', '--color', 'never', '--sandbox', sandbox]
    if model:
        args += ['--model', model]
    return args + ['-']  # read the task from stdin


def _gemini_args(mode: str, model: str | None) -> list[str]:
    approval = {'read_only': 'default', 'edit': 'auto_edit', 'full': 'yolo'}[mode]
    args = ['--approval-mode', approval]
    if model:
        args += ['--model', model]
    return args


CLIS: dict[str, CodingAgentCLI] = {
    'claude': CodingAgentCLI(
        key='claude', label='Claude Code', program='claude',
        install_hint='npm install -g @anthropic-ai/claude-code, then run `claude` once to sign in',
        build=_claude_args,
        mode_notes={
            'read_only': 'plan mode: reads and answers, no edits or commands',
            'edit': 'edits files; its shell commands are refused',
            'full': 'skips every permission check',
        },
    ),
    'codex': CodingAgentCLI(
        key='codex', label='Codex', program='codex',
        install_hint='npm install -g @openai/codex, then run `codex login`',
        build=_codex_args,
        mode_notes={
            'read_only': 'read-only sandbox',
            'edit': 'edits files and runs commands inside the folder sandbox',
            'full': 'no sandbox',
        },
    ),
    'gemini': CodingAgentCLI(
        key='gemini', label='Gemini CLI', program='gemini',
        install_hint='npm install -g @google/gemini-cli, then run `gemini` once to sign in',
        build=_gemini_args,
        mode_notes={
            'read_only': 'tools that need approval are skipped',
            'edit': 'auto-approves file edits',
            'full': 'auto-approves everything (yolo)',
        },
    ),
}

# Order tried when the agent asks for 'auto'.
PREFERENCE = ('claude', 'codex', 'gemini')

_ALIASES = {
    'claude': 'claude', 'claude_code': 'claude', 'claude code': 'claude', 'claude-code': 'claude',
    'codex': 'codex', 'openai codex': 'codex', 'codex cli': 'codex', 'codex-cli': 'codex',
    'gemini': 'gemini', 'gemini cli': 'gemini', 'gemini-cli': 'gemini', 'gemini_cli': 'gemini',
}


class CodingAgentError(ValueError):
    pass


def normalise_agent(name: str | None) -> str:
    """'auto', 'list' or a key of ``CLIS``. Raises on an unknown name."""
    value = re.sub(r'\s+', ' ', str(name or 'auto').strip().lower())
    if value in ('', 'auto', 'any', 'default'):
        return 'auto'
    if value == 'list':
        return 'list'
    if value in _ALIASES:
        return _ALIASES[value]
    known = ', '.join(CLIS)
    raise CodingAgentError(f"Unknown coding agent '{name}'. Use one of: {known}, or 'auto'.")


def normalize_agent_limits(value: object) -> dict:
    """Clean an agent's coding_agents setting. Raises CodingAgentError."""
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise CodingAgentError('coding_agents must be an object')
    cleaned: dict = {}
    clis = value.get('clis')
    if clis is not None:
        if not isinstance(clis, list):
            raise CodingAgentError('coding_agents.clis must be a list')
        keys = []
        for name in clis:
            key = _ALIASES.get(str(name or '').strip().lower())
            if key is None:
                raise CodingAgentError(f"Unknown coding agent '{name}'. Use one of: {', '.join(CLIS)}.")
            if key not in keys:
                keys.append(key)
        cleaned['clis'] = keys
    if value.get('max_mode') not in (None, ''):
        cleaned['max_mode'] = normalise_mode(value.get('max_mode'))
    return cleaned


def apply_agent_limits(which: str, mode: str, limits: object) -> str:
    """Check a call against the calling agent's limits.

    Returns the CLI to use ('auto' becomes the first allowed installed one
    later, via ``pick``; here it is narrowed to the allowed list).
    """
    try:
        limits = normalize_agent_limits(limits)
    except CodingAgentError:
        limits = {}
    max_mode = limits.get('max_mode')
    if max_mode and MODES.index(mode) > MODES.index(max_mode):
        raise CodingAgentError(
            f"This agent may hand off coding work in mode '{max_mode}' at most, not '{mode}'."
        )
    allowed = limits.get('clis')
    if allowed is None or which == 'list':
        return which
    if not allowed:
        raise CodingAgentError('This agent is not allowed to use any coding agent.')
    if which == 'auto':
        return 'auto:' + ','.join(allowed)
    if which not in allowed:
        names = ', '.join(CLIS[k].label for k in allowed)
        raise CodingAgentError(f'This agent may only use: {names}.')
    return which


def normalise_mode(mode: str | None) -> str:
    value = str(mode or DEFAULT_MODE).strip().lower().replace('-', '_').replace(' ', '_')
    if value in ('readonly', 'read', 'plan'):
        value = 'read_only'
    if value not in MODES:
        raise CodingAgentError(f"Unknown mode '{mode}'. Use one of: {', '.join(MODES)}.")
    return value


def validate_model(model: str | None) -> str | None:
    if model in (None, ''):
        return None
    value = str(model).strip()
    if not _MODEL_RE.match(value):
        raise CodingAgentError(f"Invalid model name '{model}'.")
    return value


def find_program(key: str, path: str | None = None) -> str | None:
    """Full path of the CLI, or None when it isn't installed."""
    override = os.environ.get(f'COGNITRIX_{key.upper()}_PATH')
    if override:
        return override if Path(override).exists() else None
    return shutil.which(CLIS[key].program, path=path)


def installed(path: str | None = None) -> dict[str, str]:
    """Installed coding agents, key -> program path, in preference order."""
    found: dict[str, str] = {}
    for key in PREFERENCE:
        program = find_program(key, path)
        if program:
            found[key] = program
    return found


def pick(agent: str, path: str | None = None) -> tuple[str, str]:
    """(key, program path) for ``agent`` ('auto' picks the first installed;
    'auto:claude,codex' the first installed of those)."""
    if agent == 'auto' or agent.startswith('auto:'):
        found = installed(path)
        if agent.startswith('auto:'):
            allowed = agent[5:].split(',')
            found = {k: v for k, v in found.items() if k in allowed}
            if not found:
                names = ', '.join(CLIS[k].label for k in allowed if k in CLIS)
                raise CodingAgentError(f'None of the coding agents this agent may use is installed: {names}.')
        if not found:
            raise CodingAgentError(
                'No coding agent CLI is installed on this computer. Install one of: '
                + '; '.join(f'{c.label}: {c.install_hint}' for c in CLIS.values())
            )
        key = next(iter(found))
        return key, found[key]
    program = find_program(agent, path)
    if not program:
        cli = CLIS[agent]
        raise CodingAgentError(f'{cli.label} is not installed here. Install it with: {cli.install_hint}')
    return agent, program


def build_argv(program: str, key: str, mode: str, model: str | None) -> list[str]:
    return [program, *CLIS[key].build(mode, model)]


def describe_installed(path: str | None = None) -> str:
    found = installed(path)
    lines = []
    for key, cli in CLIS.items():
        if key in found:
            lines.append(f'- {key} ({cli.label}): installed at {found[key]}')
        else:
            lines.append(f'- {key} ({cli.label}): not installed ({cli.install_hint})')
    lines.append('')
    lines.append("Modes: read_only (no changes), edit (default; may edit files), full (no limits).")
    return '\n'.join(lines)


def changed_files(cwd: Path, limit: int = 50) -> str:
    """Short `git status` of ``cwd`` after a run, or '' outside a git repo."""
    git = shutil.which('git')
    if not git:
        return ''
    try:
        from cognitrix.common.shell import _group_kwargs, child_env
        proc = subprocess.run(
            [git, 'status', '--porcelain=v1', '--untracked-files=all'],
            cwd=str(cwd), capture_output=True, timeout=15, env=child_env(), **_group_kwargs(),
        )
    except Exception:
        return ''
    if proc.returncode != 0:
        return ''
    from cognitrix.common.encoding import decode_output
    lines = [line for line in decode_output(proc.stdout).splitlines() if line.strip()]
    if not lines:
        return '[No files changed in the folder (git status is clean)]'
    shown = lines[:limit]
    more = f'\n... and {len(lines) - limit} more' if len(lines) > limit else ''
    return '[Files changed in the folder (git status):]\n' + '\n'.join(shown) + more


def time_limit() -> int:
    try:
        value = int(os.environ.get('COGNITRIX_CODING_AGENT_TIMEOUT', DEFAULT_TIME_LIMIT))
    except ValueError:
        value = DEFAULT_TIME_LIMIT
    return max(60, value)


def risk(params: dict | None) -> tuple[str, str]:
    """(level, details) for the approval card. Listing runs; a run always asks."""
    params = params if isinstance(params, dict) else {}
    try:
        agent = normalise_agent(params.get('agent'))
    except CodingAgentError:
        agent = str(params.get('agent') or 'auto')
    if agent == 'list':
        return 'low', 'Lists the coding agents installed on this computer'
    try:
        mode = normalise_mode(params.get('mode'))
    except CodingAgentError:
        mode = str(params.get('mode'))
    label = CLIS[agent].label if agent in CLIS else 'the first installed coding agent'
    folder = params.get('working_dir') or 'the working folder'
    task = str(params.get('task') or '').strip().replace('\n', ' ')
    if len(task) > 300:
        task = task[:300] + '...'
    what = {
        'read_only': 'read the code (no changes)',
        'edit': 'edit files',
        'full': 'edit files and run any command without asking',
    }.get(mode, f'run in mode {mode}')
    level = 'high' if mode == 'full' else 'medium'
    return level, f'Hands a coding job to {label}, which can {what} in {folder}: {task}'
