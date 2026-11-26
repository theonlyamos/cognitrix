"""Coding in the sandbox: the engine side.

When a chat (or its agent) has **Run in sandbox** on, the shell, the file
tools, Coding Agent and Run Tests work inside the person's sandboxed computer
(``cognitrix/computer/sandbox``) instead of on the machine Cognitrix runs on.
Each agent has its own folder there (``~/work/<agent>``). Nothing done there
reaches the person's computer, so none of it asks for approval.

Folders from the computer come in with Copy Into Sandbox, which copies a
working folder into the agent's sandbox folder. Changes go back only with
Copy Back From Sandbox, which a person approves after seeing the diff, and
which skips files that changed on the computer in the meantime.

The container side is ``cognitrix/computer/sandbox/workspace.py``.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tarfile
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from cognitrix.app_settings import get as app_setting

logger = logging.getLogger('cognitrix.sandbox')

MAX_COPY_FILES = 50_000
MAX_COPY_BYTES = int(app_setting('COGNITRIX_SANDBOX_MAX_COPY_MB', '400')) * 1024 * 1024
MAX_GIT_DIR_BYTES = 150 * 1024 * 1024
# Left out when a folder without git is copied in (installed or built things
# the sandbox makes again itself).
SKIP_DIRS = frozenset({
    'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox',
    '.next', '.nuxt', '.turbo', '.parcel-cache', '.gradle', '.cache', '.idea', '.vs',
})
SKIP_FILES = frozenset({'.DS_Store', 'Thumbs.db'})

# API keys the coding agent CLIs read, from the engine's provider keys.
CLI_KEYS = {
    'claude': (('anthropic', 'ANTHROPIC_API_KEY'),),
    'codex': (('openai', 'OPENAI_API_KEY'), ('openai', 'CODEX_API_KEY')),
    'gemini': (('google', 'GEMINI_API_KEY'), ('google', 'GOOGLE_API_KEY')),
    # Antigravity signs in with the OS keyring, which the sandbox doesn't
    # have; with modelProvider "gemini" it uses GEMINI_API_KEY instead.
    'antigravity': (('google', 'GEMINI_API_KEY'),),
}
SANDBOX_CLIS = tuple(CLI_KEYS)
# The app's own CLI is in the sandbox image too (sandbox/desktop/Dockerfile).
# A command that runs it gets the person's model keys the same way the coding
# CLIs do: as placeholders the key proxy fills in for each provider's API host.
COGNITRIX_CLI_KEYS = (('anthropic', 'ANTHROPIC_API_KEY'), ('openai', 'OPENAI_API_KEY'),
                      ('google', 'GOOGLE_API_KEY'), ('google', 'GEMINI_API_KEY'))
# `cognitrix` or `cognitrix-python` as a command (at the start, or after ; & | ( $( and
# any VAR=value before it), not a folder or file that happens to be called that.
_COGNITRIX_COMMAND = re.compile(r'(?:^|[;&|(\n]|\$\()\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:/usr/local/bin/)?'
                                r'cognitrix(?:-python)?(?=\s|$|[;&|)])')

# Run before agy in the sandbox: point it at the Gemini API key (merging into
# any settings already in the home volume). jq is in the sandbox image.
AGY_SANDBOX_SETUP = (
    'f="$HOME/.gemini/antigravity-cli/settings.json"; mkdir -p "${f%/*}" && '
    'if [ -s "$f" ]; then jq \'.modelProvider = "gemini"\' "$f" > "$f.tmp" && mv "$f.tmp" "$f"; '
    'else printf \'{"modelProvider": "gemini"}\\n\' > "$f"; fi'
)


def sandbox_cli_command(key: str, argv: list[str]) -> str:
    """The shell command that runs a coding agent CLI in the sandbox."""
    import shlex
    command = shlex.join(argv)
    if key == 'antigravity':
        missing = ("echo \"Antigravity CLI isn't in this sandbox yet. Update the sandbox (Settings, Computer) "
                   "and try again.\" >&2; exit 127")
        return f'if ! command -v agy >/dev/null; then {missing}; fi; {{ {AGY_SANDBOX_SETUP}; }} && {command}'
    return command
PROVIDER_LABELS = {'anthropic': 'an Anthropic', 'openai': 'an OpenAI', 'google': 'a Google (Gemini)'}
# The only sites each provider's API key goes to from the sandbox: the
# container gets a placeholder, and the key proxy fills the real key in
# requests to these hosts (cognitrix/common/key_proxy.py).
PROVIDER_HOSTS = {
    'anthropic': ('api.anthropic.com',),
    'openai': ('api.openai.com',),
    'google': ('generativelanguage.googleapis.com',),
}
# Hosts for a sign-in saved only for the sandbox: the .invalid name never
# resolves, so Http Request can't send it anywhere. These values reach
# sandbox commands as they are (they're for things inside the sandbox).
SANDBOX_ONLY_HOST = 'sandbox.invalid'


class SandboxWorkError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Is this turn in the sandbox?
# ---------------------------------------------------------------------------

def sandbox_available() -> bool:
    try:
        from cognitrix.computer.sandbox.client import sandbox_mode
        return sandbox_mode() is not None
    except Exception:
        return False


def turn_in_sandbox() -> bool:
    """True when this turn's shell and files work in the sandbox."""
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    # Switch Computer For Code moves the rest of the turn to the person's
    # computer: its safety checks and approvals apply again.
    return bool(getattr(ctx, 'sandbox', False)) and chat_switch(getattr(ctx, 'session_id', None)) is not False


def sandbox_choice(session_value: Any, agent: Any) -> tuple[bool, bool]:
    """(on, chosen). The chat's own switch wins, then the agent's setting
    (Run code in the sandbox on, or "on my computer"). With neither, code runs
    in the sandbox whenever the sandboxed computer is on: agents work on their
    own computer unless the person says otherwise. ``chosen`` is False for
    that default."""
    if session_value is not None:
        return bool(session_value), True
    if getattr(agent, 'code_on_my_computer', False):
        return False, True
    if getattr(agent, 'run_in_sandbox', False):
        return True, True
    return sandbox_available(), False


def wants_sandbox(session_value: Any, agent: Any) -> bool:
    return sandbox_choice(session_value, agent)[0]


# Switch Computer For Code: a chat moved to (False) or back from (True) the
# person's computer during a turn. for_turn reads it at once; the chat keeps it
# from its next save (Session._turn_sandbox / take_chat_switch).
_chat_switch: dict[str, bool] = {}
_switch_lock = threading.Lock()


def set_chat_switch(session_id: Any, on: bool) -> None:
    if session_id:
        with _switch_lock:
            _chat_switch[str(session_id)] = bool(on)


def chat_switch(session_id: Any) -> bool | None:
    return _chat_switch.get(str(session_id)) if session_id else None


def take_chat_switch(session_id: Any) -> bool | None:
    """The switch made in the chat's last turn, once (the chat saves it)."""
    if not session_id:
        return None
    with _switch_lock:
        return _chat_switch.pop(str(session_id), None)


def for_turn() -> SandboxWork | None:
    """The sandbox to work in for this turn, or None when the turn works on
    this machine. Raises SandboxWorkError when the chat asks for the sandbox
    but it can't be used."""
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    if not getattr(ctx, 'sandbox', False) or chat_switch(ctx.session_id) is False:
        return None
    if not sandbox_available():
        raise SandboxWorkError(
            'sandbox_off', "This chat runs code in the sandbox, but the sandboxed computer is off. Ask the "
            'person to turn it on in Settings, Computer (it needs Docker), or to turn Run in sandbox off.')
    return SandboxWork(ctx.user_id, ctx.agent_id)


# ---------------------------------------------------------------------------
# Environment variables for the sandbox (Settings, Computer)
# ---------------------------------------------------------------------------

MAX_ENV_VARS = 100
MAX_ENV_VALUE = 32_000
_ENV_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
# Set by the sandbox itself; a saved one would break commands.
_RESERVED_ENV = frozenset({'PATH', 'HOME', 'USER', 'SHELL', 'PWD', 'DISPLAY', 'COGNITRIX_SANDBOX_TOKEN'})


def _env_file() -> Path:
    from cognitrix.config import settings
    return Path(settings.workdir) / 'sandbox-env.json'


def _read_env_file() -> dict:
    import json
    try:
        data = json.loads(_env_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _env_key(user_id: Any) -> str:
    from cognitrix.computer.sandbox.client import user_key
    return user_key(user_id)


def _unpack(raw: Any) -> tuple[str, list[str]] | None:
    """A saved entry: a sealed string, or {'v': sealed, 'hosts': [...]}."""
    from cognitrix.common.secret_box import unseal_or_empty
    if isinstance(raw, str):
        return unseal_or_empty(raw), []
    if isinstance(raw, dict) and isinstance(raw.get('v'), str):
        hosts = [str(h) for h in (raw.get('hosts') or []) if isinstance(h, str) and h.strip()]
        return unseal_or_empty(raw['v']), hosts
    return None


def sandbox_env_hosts(user_id: Any) -> dict[str, list[str]]:
    """Each saved variable's "Only sent to" sites (empty: a plain variable)."""
    raw = _read_env_file().get(_env_key(user_id)) or {}
    out = {}
    for name, item in raw.items():
        got = _unpack(item) if isinstance(name, str) and _ENV_NAME.match(name) else None
        if got is not None:
            out[name] = got[1]
    return out


def sandbox_env(user_id: Any) -> dict[str, str]:
    """The person's saved variables, unsealed: every command, test run and
    coding agent in their sandbox gets them. A variable with "Only sent to"
    sites comes back as a HiddenValue, which reaches the container as a
    placeholder the key proxy fills in for those sites only."""
    from cognitrix.common.key_proxy import hidden_if_hosts
    raw = _read_env_file().get(_env_key(user_id)) or {}
    out = {}
    for name, item in raw.items():
        got = _unpack(item) if isinstance(name, str) and _ENV_NAME.match(name) else None
        if got is not None:
            out[name] = hidden_if_hosts(got[0], got[1], name)
    return out


def check_env_name(name: str) -> str:
    name = str(name or '').strip()
    if not _ENV_NAME.match(name):
        raise ValueError(f"'{name}' can't be a variable name (letters, numbers and _, not starting with a number).")
    if name.upper() in _RESERVED_ENV:
        raise ValueError(f'{name} is set by the sandbox itself.')
    return name


# One process writes the shared file; the lock stops two saves losing each other.
_ENV_LOCK = threading.Lock()


def save_sandbox_env(user_id: Any, variables: list[dict]) -> dict[str, str]:
    """Replace the person's variables. An entry without a value keeps the
    saved one (the UI never gets values back); names left out are removed.
    ``hosts`` (optional) limits a value to those sites: commands then get a
    placeholder and only requests to those sites carry the real value."""
    with _ENV_LOCK:
        return _save_sandbox_env(user_id, variables)


def _clean_hosts(raw: Any, name: str) -> list[str]:
    from cognitrix.credentials import normalize_host
    if raw in (None, '', []):
        return []
    items = raw if isinstance(raw, list) else str(raw).split(',')
    hosts: list[str] = []
    for item in items:
        if not str(item or '').strip():
            continue
        try:
            host = normalize_host(str(item))
        except ValueError as exc:
            raise ValueError(f'{name}: {exc}') from None
        if host not in hosts:
            hosts.append(host)
    if len(hosts) > 10:
        raise ValueError(f'{name}: at most 10 sites.')
    return hosts


def _save_sandbox_env(user_id: Any, variables: list[dict]) -> dict[str, str]:
    current = sandbox_env(user_id)
    current_hosts = sandbox_env_hosts(user_id)
    new: dict[str, str] = {}
    hosts: dict[str, list[str]] = {}
    for item in variables or []:
        name = check_env_name(item.get('name', ''))
        value = item.get('value')
        if value is None:
            if name not in current:
                raise ValueError(f'Give {name} a value.')
            value = current[name]
        value = str(value)
        if len(value) > MAX_ENV_VALUE:
            raise ValueError(f'{name} is too long ({len(value):,} characters, max {MAX_ENV_VALUE:,}).')
        new[name] = value
        hosts[name] = (_clean_hosts(item['hosts'], name) if 'hosts' in item and item['hosts'] is not None
                       else current_hosts.get(name, []))
    if len(new) > MAX_ENV_VARS:
        raise ValueError(f'At most {MAX_ENV_VARS} variables.')
    _write_env_entry(user_id, new, hosts)
    return sandbox_env(user_id)


def _write_env_entry(user_id: Any, new: dict[str, str], hosts: dict[str, list[str]] | None = None) -> None:
    import json
    import tempfile

    from cognitrix.common.secret_box import seal
    data = _read_env_file()
    key = _env_key(user_id)
    if new:
        data[key] = {k: ({'v': seal(str(v)), 'hosts': (hosts or {})[k]} if (hosts or {}).get(k) else seal(str(v)))
                     for k, v in new.items()}
    else:
        data.pop(key, None)
    path = _env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.sandbox-env.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def env_preview(value: str) -> str:
    """What the UI shows instead of a value."""
    if len(value) <= 4:
        return '•' * len(value)
    return value[:2] + '•' * min(8, len(value) - 4) + value[-2:]


# ---------------------------------------------------------------------------
# Saved sign-ins for one command
# ---------------------------------------------------------------------------

MAX_CREDENTIALS = 10


def credential_names(raw: Any) -> list[str]:
    from cognitrix.credentials import normalize_name
    if raw in (None, '', []):
        return []
    items = raw if isinstance(raw, list) else [raw]
    names: list[str] = []
    for item in items:
        for part in str(item or '').split(','):
            if part.strip():
                name = normalize_name(part.strip())
                if name not in names:
                    names.append(name)
    if len(names) > MAX_CREDENTIALS:
        raise SandboxWorkError('too_many', f'Pass at most {MAX_CREDENTIALS} saved sign-ins to one command.')
    return names


def credential_vars(name: str, kind: str, values: dict[str, str], hosts: list[str] | None = None) -> dict[str, str]:
    """Environment variables for one saved sign-in: NAME for a token (or an
    app sign-in's access token), NAME_USERNAME and NAME_PASSWORD for a
    username and password. With allowed ``hosts`` the values are hidden: the
    command gets placeholders the key proxy fills in for those sites only. A
    sandbox-only sign-in (or one without sites) arrives as it is."""
    from cognitrix.common.key_proxy import hidden_if_hosts
    sites = [h for h in (hosts or []) if h and h != SANDBOX_ONLY_HOST]
    if kind == 'basic':
        out = {}
        if values.get('username'):
            # A username isn't secret, and tools often print it.
            out[f'{name}_USERNAME'] = values['username']
        if values.get('password'):
            out[f'{name}_PASSWORD'] = hidden_if_hosts(values['password'], sites, name)
        return out
    value = values.get('access_token') if kind == 'oauth' else values.get('value')
    return {name: hidden_if_hosts(value, sites, name)} if value else {}


async def _load_credentials(owner: Any, agent_id: Any, names: list[str]) -> dict[str, str]:
    from cognitrix.credentials import agent_allowed_credentials, load_rows, only_allowed, open_secret
    allowed = None
    if agent_id:
        try:
            from cognitrix.agents import Agent
            agent = await Agent.find_one({'id': str(agent_id)})
            allowed = agent_allowed_credentials(agent)
        except Exception:
            allowed = None
    rows = {row.name: row for row in only_allowed(await load_rows(str(owner) if owner else None), allowed)}
    env: dict[str, str] = {}
    missing = []
    for name in names:
        row = rows.get(name)
        if row is None:
            missing.append(name)
            continue
        env.update(credential_vars(name, row.kind, open_secret(row), list(getattr(row, 'allowed_hosts', None) or [])))
    if missing:
        raise SandboxWorkError('unknown_credential', f"No saved sign-in this agent may use is called {', '.join(missing)}. "
                               'Use List Credentials to see the names.')
    return env


def credential_env(names: list[str]) -> dict[str, str]:
    """The values of saved sign-ins (Settings, Saved sign-ins) as variables for
    one sandbox command. The model never sees them, and they're hidden in the
    command's output."""
    if not names:
        return {}
    from cognitrix.tools.tool import run_on_calling_loop
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    if not ctx.user_id:
        raise SandboxWorkError('no_owner', 'Saved sign-ins need a signed-in person.')
    return run_on_calling_loop(_load_credentials(ctx.user_id, ctx.agent_id, names))


# ---------------------------------------------------------------------------
# Talking to the container
# ---------------------------------------------------------------------------

class SandboxWork:
    """One agent's folder, shell and projects in the user's sandbox."""

    def __init__(self, user_id: Any, agent_id: Any):
        from cognitrix.computer.sandbox import desktop_name
        from cognitrix.projects.context import sandbox_identity
        self.user_id = user_id
        # In a project with the shared sandbox, every agent uses the project's
        # desktop, Linux user and ~/work folder.
        self.agent = desktop_name(sandbox_identity(agent_id))
        self._box = None

    @property
    def box(self):
        if self._box is None:
            from cognitrix.computer import DriverError, Unsupported
            from cognitrix.computer.sandbox.client import get_manager
            try:
                self._box = get_manager().sandbox(self.user_id)
            except (DriverError, Unsupported) as exc:
                raise SandboxWorkError('no_sandbox', str(exc)) from None
        return self._box

    def _call(self, method: str, path: str, body: dict | None = None, *, timeout: float = 130,
              content: bytes | None = None, params: dict | None = None) -> dict:
        from cognitrix.computer import DriverError
        try:
            out = self.box.call(method, f'/work/{self.agent}{path}', body, content=content, params=params,
                                timeout=timeout)
        except DriverError as exc:
            raise SandboxWorkError('no_sandbox', str(exc)) from None
        if not out.get('ok'):
            raise SandboxWorkError(str(out.get('error') or 'failed'), str(out.get('message') or 'It failed.'))
        return out

    @staticmethod
    def _q(name: str) -> str:
        from urllib.parse import quote
        return quote(name, safe='')

    # -- shell ---------------------------------------------------------------
    def run(self, command: str, cwd: str | None = None, timeout: float = 120, background: bool = False,
            env: dict | None = None, stdin: str | None = None, label: str | None = None) -> dict:
        # The person's saved variables (Settings, Computer), then the call's own.
        base = {**cognitrix_cli_env(), **self._main_assistant_env()} if runs_cognitrix(command) else {}
        merged = self.hide_keys({**base, **sandbox_env(self.user_id), **(env or {})})
        body = {'command': command, 'cwd': cwd, 'timeout': timeout, 'background': background,
                'env': merged, 'stdin': stdin, 'label': label}
        return self._call('POST', '/run', body, timeout=(15 if background else float(timeout) + 30))

    def _main_assistant_env(self) -> dict[str, str]:
        """The person's main assistant for the CLI in the sandbox, which has
        no account of its own (cognitrix/cli/main_assistant.py). Run from a
        tool's worker thread; nothing when there is no loop to ask on."""
        import asyncio

        from cognitrix.tools.tool import calling_loop
        loop = calling_loop.get()
        if loop is None or loop.is_closed():
            return {}
        try:
            if asyncio.get_running_loop() is loop:
                return {}  # on the loop itself: waiting here would hang it
        except RuntimeError:
            pass
        from cognitrix.cli.main_assistant import profile_env
        try:
            return asyncio.run_coroutine_threadsafe(profile_env(self.user_id), loop).result(10)
        except Exception:
            logger.debug("Could not pass the main assistant to the sandbox CLI", exc_info=True)
            return {}

    def hide_keys(self, env: dict[str, str]) -> dict[str, str]:
        """Swap every hidden value for its placeholder and send the command's
        HTTPS through the key proxy, which fills the real values in for their
        own sites only. The real values never reach the container."""
        from cognitrix.common.key_proxy import HiddenValue, hiding_enabled
        hidden = {k: v for k, v in env.items() if isinstance(v, HiddenValue)}
        if not hidden:
            return env
        if not hiding_enabled():
            return {k: str(v) for k, v in env.items()}
        from cognitrix.common.key_proxy import get_proxy
        from cognitrix.computer import DriverError, Unsupported
        from cognitrix.computer.sandbox.client import get_manager, user_key
        try:
            extra = get_manager().key_proxy_env(self.user_id)
        except (DriverError, Unsupported) as exc:
            raise SandboxWorkError('no_sandbox', str(exc)) from None
        except Exception as exc:
            logger.warning("The sandbox key proxy didn't start", exc_info=True)
            raise SandboxWorkError('key_proxy', "This command needs keys the sandbox only gets through Cognitrix's key "
                                   f"proxy, and the proxy didn't start ({exc}). Ask the person to check Docker, or "
                                   'to turn off "Hide keys from the computer" in Settings, Computers.') from None
        proxy, person = get_proxy(), user_key(self.user_id)
        out = {k: (proxy.placeholder(person, k, v) if isinstance(v, HiddenValue) else v) for k, v in env.items()}
        out.update(extra)
        return out

    def read_job(self, job_id: str, filter_re: str | None = None) -> dict:
        return self._call('POST', f'/jobs/{self._q(job_id)}/read', {'filter': filter_re})

    def wait_job(self, job_id: str, seconds: float) -> dict:
        return self._call('POST', f'/jobs/{self._q(job_id)}/wait', {'seconds': seconds}, timeout=float(seconds) + 30)

    def stop_job(self, job_id: str) -> dict:
        return self._call('POST', f'/jobs/{self._q(job_id)}/stop', {})

    def full_output(self, job_id: str) -> str:
        return str(self._call('POST', f'/jobs/{self._q(job_id)}/full', {}).get('output') or '')

    def jobs(self) -> list[dict]:
        return list(self._call('GET', '/jobs').get('jobs') or [])

    # -- files ---------------------------------------------------------------
    def files(self, op: str, **body: Any) -> dict:
        return self._call('POST', f'/files/{op}', body, timeout=180)

    def where(self) -> dict:
        return self.files('where')

    # -- projects -------------------------------------------------------------
    def import_folder(self, folder: Path, name: str, replace: bool = False) -> dict:
        archive, count, notes = pack_folder(folder)
        out = self._call('PUT', f'/projects/{self._q(name)}', content=archive, timeout=900,
                         params={'replace': '1' if replace else '0', 'source': str(folder)})
        out['packed'] = count
        out['notes'] = notes
        return out

    def changes(self, name: str, patch: bool = True) -> dict:
        return self._call('POST', f'/projects/{self._q(name)}/changes', {'patch': patch}, timeout=300)

    def export(self, name: str, paths: list[str] | None = None) -> dict:
        return self._call('POST', f'/projects/{self._q(name)}/export', {'paths': paths or []}, timeout=300)

    def synced(self, name: str, paths: list[str]) -> dict:
        return self._call('POST', f'/projects/{self._q(name)}/synced', {'paths': paths}, timeout=300)

    def projects(self) -> list[dict]:
        return list(self._call('GET', '/projects').get('projects') or [])

    # -- the chat's folders, copied in the first time they're used ------------
    @property
    def root(self) -> str:
        return f'{SANDBOX_WORK_ROOT}/{self.agent}'

    def project_for(self, folder: Any, *, copy: bool = True) -> str | None:
        """The project that holds a copy of one of the chat's folders. The
        first time, the folder is copied in (Copy Into Sandbox without being
        asked). None when it isn't there and ``copy`` is False."""
        source = os.path.normcase(os.path.abspath(str(folder.path)))
        key = (str(self.user_id), self.agent, source)
        if key in _copied:
            return _copied[key]
        projects = self.projects()
        for p in projects:
            if p.get('source') and os.path.normcase(os.path.abspath(str(p['source']))) == source:
                _copied[key] = str(p['name'])
                return _copied[key]
        if not copy:
            return None
        base = default_project_name(folder)
        taken = {str(p.get('name')) for p in projects}
        name, n = base, 2
        while name in taken:
            name, n = f'{base}-{n}', n + 1
        self.import_folder(Path(str(folder.path)), name)
        _copied[key] = name
        return name


# (user, agent, folder on the computer) -> its project in the sandbox.
_copied: dict[tuple[str, str, str], str] = {}
SANDBOX_WORK_ROOT = '/home/agent/work'


# (chat, project) -> the changes the person last saw on a card, so the
# end-of-turn review shows each set of changes once.
_reviewed: dict[tuple[str, str], Any] = {}


def _changes_key(out: dict) -> Any:
    """What identifies a set of changes: the content of the sandbox copy (its
    git tree) when the sandbox reports it, else the changed files."""
    files = out.get('files') or []
    return out.get('tree') or hash(tuple((f.get('path'), f.get('status'), f.get('added'), f.get('removed'))
                                         for f in files))


def note_reviewed(session_id: Any, project: str, out: dict) -> None:
    if session_id:
        _reviewed[(str(session_id), str(project))] = _changes_key(out)


def changes_to_review(ws: SandboxWork, session_id: Any, folders: list[Any]) -> list[str]:
    """Projects copied in from these folders whose changes the person hasn't
    seen on a card yet (nothing copied back)."""
    out = []
    for folder in folders:
        name = ws.project_for(folder, copy=False)
        if not name:
            continue
        got = ws.changes(name, patch=False)
        if got.get('files') and _reviewed.get((str(session_id), name)) != _changes_key(got):
            out.append(name)
    return out


def default_project_name(folder: Any) -> str:
    from cognitrix.computer.sandbox.workspace import WorkspaceError, project_name
    raw = getattr(folder, 'name', '') or Path(str(folder.path)).name or 'project'
    try:
        return project_name(raw)
    except WorkspaceError:
        return project_name('project')


def chat_folders() -> list[Any]:
    """This turn's folders on the person's computer that can be copied in."""
    from cognitrix.tools.utils import current_execution_context
    return [f for f in (current_execution_context().folders or ()) if getattr(f, 'name', '') != 'scratch']


def _host_inside(path: str, folder: Any) -> str | None:
    """``path`` relative to the folder (posix), when it's inside it."""
    try:
        rel = Path(os.path.abspath(os.path.expanduser(path))).relative_to(Path(os.path.abspath(str(folder.path))))
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def to_sandbox_path(ws: SandboxWork, path: str | None) -> str | None:
    """A path the agent gave, in the sandbox. A path inside one of the chat's
    folders on the person's computer points at that folder's copy, which is
    made the first time; so does the copy's own sandbox path."""
    if not path:
        return path
    folders = chat_folders()
    if not folders:
        return path
    raw = str(path)
    for folder in sorted(folders, key=lambda f: len(str(f.path)), reverse=True):
        rel = _host_inside(raw, folder) if not raw.startswith(SANDBOX_WORK_ROOT + '/') else None
        if rel is not None:
            name = ws.project_for(folder)
            return f'{ws.root}/{name}' + ('' if rel in ('', '.') else f'/{rel}')
    return _copy_named(ws, raw, folders)


def _copy_named(ws: SandboxWork, text: str, folders: list[Any]) -> str:
    """Copy in a chat folder whose sandbox path (as the prompt gives it) is
    used, and point that path at the folder's actual copy (which can have
    another name when the folder was copied in under one, or the name was
    taken)."""
    for folder in folders:
        guess = f'{ws.root}/{default_project_name(folder)}'
        pattern = re.escape(guess) + r'(?![A-Za-z0-9._-])'
        if re.search(pattern, text):
            actual = f'{ws.root}/{ws.project_for(folder)}'
            if actual != guess:
                text = re.sub(pattern, lambda _m, to=actual: to, text)
    return text


def map_command(ws: SandboxWork, command: str) -> str:
    """A shell command, with paths to the chat's folders on the person's
    computer pointed at their copies in the sandbox (copied in first)."""
    folders = chat_folders()
    if not folders or not command:
        return command
    for folder in sorted(folders, key=lambda f: len(str(f.path)), reverse=True):
        host = os.path.abspath(str(folder.path))
        spellings = {host, host.replace('\\', '/')}
        hit = [sp for sp in spellings if sp and sp in command]
        if hit:
            name = ws.project_for(folder)
            for sp in sorted(hit, key=len, reverse=True):
                command = command.replace(sp, f'{ws.root}/{name}')
    return _copy_named(ws, command, folders)


# ---------------------------------------------------------------------------
# Copying a folder in
# ---------------------------------------------------------------------------

def _git_files(folder: Path) -> list[str] | None:
    """Files git would keep (tracked plus new, not ignored), or None when the
    folder isn't in a git repository."""
    git = shutil.which('git')
    if not git:
        return None
    try:
        got = subprocess.run(  # noqa: S603
            [git, '-C', str(folder), 'ls-files', '-co', '--exclude-standard', '-z'],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if got.returncode != 0:
        return None
    return [p for p in got.stdout.decode('utf-8', 'replace').split('\0') if p]


def _walk_files(folder: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != '.git']
        for name in filenames:
            if name in SKIP_FILES:
                continue
            out.append(os.path.relpath(os.path.join(dirpath, name), folder).replace(os.sep, '/'))
            if len(out) > MAX_COPY_FILES:
                return out
    return out


def _dir_size(path: Path, stop_at: int) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            if total > stop_at:
                return total
    return total


def pack_folder(folder: Path) -> tuple[bytes, int, list[str]]:
    """A tar.gz of the folder for the sandbox: (archive, files, notes).

    In a git repository only the files git keeps go (so ignored ones such as
    .env, node_modules and build output stay on the computer), plus .git
    itself when it isn't huge, so git works in the sandbox. Otherwise
    everything except installed packages and caches. Links are skipped.
    """
    folder = Path(folder)
    notes: list[str] = []
    listed = _git_files(folder)
    git_dir = folder / '.git'
    if listed is None:
        names = _walk_files(folder)
        notes.append('No git here, so every file was copied except installed packages and caches.')
    else:
        names = listed
        notes.append('Files git ignores (like .env and installed packages) stayed on the computer.')
    if len(names) > MAX_COPY_FILES:
        raise SandboxWorkError('too_big', f'The folder has more than {MAX_COPY_FILES:,} files. Copy a smaller '
                               'subfolder in instead.')
    include_git = git_dir.is_dir() and _dir_size(git_dir, MAX_GIT_DIR_BYTES) <= MAX_GIT_DIR_BYTES
    if git_dir.is_dir() and not include_git:
        notes.append('The .git folder is too big to copy, so git history is not in the sandbox.')
    buf = io.BytesIO()
    total = 0
    count = 0
    with tarfile.open(fileobj=buf, mode='w:gz', compresslevel=5) as tar:
        def add(rel: str) -> None:
            nonlocal total, count
            full = folder / rel
            try:
                if full.is_symlink() or not full.is_file():
                    return
                size = full.stat().st_size
            except OSError:
                return
            total += size
            if total > MAX_COPY_BYTES:
                raise SandboxWorkError('too_big', f'The folder is bigger than {MAX_COPY_BYTES // (1024 * 1024)} MB. '
                                       'Copy a smaller subfolder in instead.')
            info = tarfile.TarInfo(rel)
            info.size = size
            try:
                info.mode = 0o755 if os.access(full, os.X_OK) and os.name != 'nt' else 0o644
                info.mtime = int(full.stat().st_mtime)
                with open(full, 'rb') as fh:
                    tar.addfile(info, fh)
            except OSError:
                return
            count += 1

        for rel in names:
            if '..' in PurePosixPath(rel).parts:
                continue
            add(rel)
        project_files = count
        if include_git:
            for dirpath, _dirs, files in os.walk(git_dir):
                for name in files:
                    add(os.path.relpath(os.path.join(dirpath, name), folder).replace(os.sep, '/'))
    return buf.getvalue(), project_files, notes


# ---------------------------------------------------------------------------
# Copying changes back
# ---------------------------------------------------------------------------

def git_blob_id(data: bytes) -> str:
    """The id git gives a file's content (to compare with the baseline)."""
    return hashlib.sha1(b'blob %d\0' % len(data) + data, usedforsecurity=False).hexdigest()


def _clean_rel(raw: str) -> str | None:
    rel = PurePosixPath(str(raw).replace('\\', '/'))
    if rel.is_absolute() or not rel.parts or '..' in rel.parts or rel.parts[0] == '.git':
        return None
    return rel.as_posix()


@dataclass
class CopyBackReport:
    written: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def applied(self) -> list[str]:
        return self.written + self.deleted


def apply_export(folder: Path, export: dict, overwrite: bool = False) -> CopyBackReport:
    """Write the sandbox's changed files into ``folder`` on this computer.

    A file that changed on the computer since it was copied in is a conflict
    and is left alone unless ``overwrite``. Paths go through the working
    folder rules, so a read only folder (or a path outside) is refused.
    """
    from cognitrix.common import folders as folder_paths
    from cognitrix.common.safe_exec import PathEscapesRoot

    report = CopyBackReport()
    for item in export.get('files') or []:
        rel = _clean_rel(item.get('path', ''))
        if rel is None:
            report.refused.append(str(item.get('path')))
            continue
        try:
            target = folder_paths.resolve_path(str(Path(folder) / rel), write=True)
        except PathEscapesRoot:
            report.refused.append(rel)
            continue
        status = item.get('status')
        base = item.get('base')
        try:
            current = target.read_bytes() if target.is_file() else None
        except OSError:
            current = None
        if status == 'deleted':
            if current is None:
                continue
            if base and git_blob_id(current) != base and not overwrite:
                report.conflicts.append(rel)
                continue
            try:
                target.unlink()
                report.deleted.append(rel)
            except OSError:
                report.refused.append(rel)
            continue
        try:
            data = base64.b64decode(item.get('data') or '')
        except ValueError:
            report.refused.append(rel)
            continue
        if current is not None and current == data:
            report.written.append(rel)  # already the same: counts as synced
            continue
        if not overwrite:
            moved_on = (status == 'added' and current is not None) or (
                status == 'modified' and (current is None or (base and git_blob_id(current) != base)))
            if moved_on:
                report.conflicts.append(rel)
                continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f'.{target.name}.cognitrix-tmp')
            tmp.write_bytes(data)
            if os.name != 'nt' and item.get('executable'):
                os.chmod(tmp, 0o755)
            elif os.name != 'nt' and current is not None:
                try:
                    os.chmod(tmp, target.stat().st_mode & 0o7777)
                except OSError:
                    pass
            os.replace(tmp, target)
            report.written.append(rel)
        except OSError:
            report.refused.append(rel)
    return report


def describe_changes(files: list[dict], limit: int = 40) -> str:
    lines = []
    for f in files[:limit]:
        mark = {'added': 'A', 'deleted': 'D'}.get(f.get('status'), 'M')
        size = 'binary' if f.get('binary') else f"+{f.get('added', 0)} -{f.get('removed', 0)}"
        lines.append(f"{mark} {f.get('path')} ({size})")
    if len(files) > limit:
        lines.append(f'... and {len(files) - limit} more')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Coding agent CLIs in the sandbox
# ---------------------------------------------------------------------------

def cli_env(key: str) -> dict[str, str]:
    """API key variables for a coding agent CLI run in the sandbox."""
    from cognitrix.common.key_proxy import HiddenValue
    from cognitrix.providers.saved_keys import env_key, get_saved_key
    env: dict[str, str] = {}
    for provider, var in CLI_KEYS.get(key, ()):
        value = env_key(provider) or get_saved_key(provider)
        if value:
            # The CLI gets a placeholder; the key proxy fills the real key in
            # only for the provider's own API host.
            env[var] = HiddenValue(value, PROVIDER_HOSTS[provider], var)
    return env


def runs_cognitrix(command: str) -> bool:
    """True when a shell command runs the app's own CLI."""
    return bool(_COGNITRIX_COMMAND.search(command or ''))


def cognitrix_cli_env() -> dict[str, str]:
    """Model keys for the app's CLI run in the sandbox, as hidden values
    (placeholders there; the key proxy fills the real key in only for that
    provider's API host). Nothing is baked into the image."""
    from cognitrix.common.key_proxy import HiddenValue
    from cognitrix.providers.saved_keys import env_key, get_saved_key
    env: dict[str, str] = {}
    for provider, var in COGNITRIX_CLI_KEYS:
        try:
            value = env_key(provider) or get_saved_key(provider)
        except Exception:
            value = ''
        if value:
            env[var] = HiddenValue(value, PROVIDER_HOSTS[provider], var)
    if env:
        from cognitrix.providers.saved_keys import SANDBOX_CLI_MARKER
        env[SANDBOX_CLI_MARKER] = '1'  # tells that CLI to take its keys from these
    return env


def pick_cli(which: str) -> str:
    """The CLI to run in the sandbox for 'auto', 'auto:<keys>' or a key:
    the first one this engine has an API key for."""
    from cognitrix.common.coding_agents import CLIS, CodingAgentError
    if which == 'auto' or which.startswith('auto:'):
        allowed = which[5:].split(',') if which.startswith('auto:') else list(SANDBOX_CLIS)
        for key in SANDBOX_CLIS:
            if key in allowed and cli_env(key):
                return key
        names = ', '.join(CLIS[k].label for k in SANDBOX_CLIS if k in allowed)
        raise CodingAgentError(f'No API key is saved for the coding agents in the sandbox ({names}). Add an '
                               'Anthropic, OpenAI or Google key in Settings, Models and keys.')
    if which not in SANDBOX_CLIS:
        raise CodingAgentError(f'{CLIS[which].label} is not in the sandbox. Use one of: {", ".join(SANDBOX_CLIS)}.')
    if not cli_env(which):
        provider = PROVIDER_LABELS[CLI_KEYS[which][0][0]]
        raise CodingAgentError(f'{CLIS[which].label} in the sandbox needs {provider} API key. Add one in '
                               'Settings, Models and keys.')
    return which


# ---------------------------------------------------------------------------
# What the agent is told
# ---------------------------------------------------------------------------

def sandbox_prompt(folders: Any, agent_id: Any, user_id: Any = None) -> str:
    from cognitrix.computer.sandbox import desktop_name
    from cognitrix.projects.context import sandbox_identity
    root = f'/home/agent/work/{desktop_name(sandbox_identity(agent_id))}'
    lines = [
        '## Coding in the sandbox',
        "Your shell and file tools (Bash, Read, Write, Edit, Glob, Grep, Coding Agent, Run Tests) work in the "
        "person's sandboxed computer: a Debian Linux container that is yours to use. Nothing you run there can "
        "touch the person's own computer, so commands and file changes there don't ask for approval.",
        f'- Your folder is {root}; commands start there (or where your last cd went). Use Linux paths.',
        '- Installed: Python 3 (pip, venv, uv), Node 22 (npm, pnpm, yarn), git, gcc/make, ripgrep, jq, sqlite3, '
        'Chromium and Firefox. Install more without root: pip install --user, npm install -g, or Go, Rust '
        'and Java into your home folder; installs stay between chats.',
        "- `cognitrix` is this app's own command-line tool (same code as the image; long-term vector memory is "
        "left out). Use it to check a skill (`cognitrix skills --validate SKILL.md`), run a one-off headless agent "
        "(`cognitrix --provider anthropic --model <model> -p \"...\" --no-stream`; add "
        "--dangerously-skip-permissions only for work that should stay inside this sandbox), or run Python "
        "that imports the cognitrix package with `cognitrix-python`. It has its own data in your home folder, "
        "not the person's agents, chats or memories. A command that runs it gets the person's model keys as "
        'placeholders (see below).',
        '- Long or never-ending commands (dev servers, watchers) go in the background: Bash with '
        'run_in_background=true, then Bash Output / Kill Shell.',
        '- Use Run Tests for test suites: it reports passed and failed tests to the person as a card.',
        '- A server you start there is at http://localhost:<port> inside the sandbox; open it in the '
        "sandbox's browser with open_app to check it.",
    ]
    try:
        from cognitrix.tools.utils import current_execution_context
        names = sorted(sandbox_env(user_id if user_id is not None else current_execution_context().user_id))
    except Exception:
        names = []
    if names:
        lines.append('- Environment variables the person set for the sandbox (every command gets them; '
                     "don't print their values): " + ', '.join(names[:60]))
    lines.append('- API keys and tokens with approved sites (coding agent keys, saved sign-ins, some variables) '
                 'arrive as placeholders containing "cgxph". They work as they are: HTTPS requests to the approved '
                 'sites carry the real value, filled in outside the sandbox. Use them normally; don\'t try to '
                 'decode them, and keep the proxy variables (HTTPS_PROXY, SSL_CERT_FILE) as they are.')
    lines.append('- To use a saved sign-in (List Credentials) in a command, pass its name in Bash or Run Tests '
                 '`credentials`: a token arrives as the variable NAME, a username and password as NAME_USERNAME '
                 'and NAME_PASSWORD, only for that command, and the person approves it. Never print them.')
    listed = [f for f in (folders or []) if getattr(f, 'name', '') != 'scratch']
    if listed:
        lines.append("The chat's folders on the person's computer, and where their copies are in the sandbox. A "
                     'folder is copied in automatically the first time you use it (a path on the computer or the '
                     'sandbox path, in a file tool, a working_dir or a command):')
        for f in listed:
            access = 'read & write' if getattr(f, 'writable', False) else 'read only'
            lines.append(f'- {f.label}: {f.path} ({access}) -> {root}/{default_project_name(f)}')
        lines.append(
            'When you finish a task that changed a copied folder, end with Copy Back From Sandbox: the person '
            'reviews the changed files on its approval card before they land on their computer (only into read & '
            'write folders). Sandbox Changes shows the diff first if you need it.')
    else:
        lines.append('No folders from the person\'s computer are attached to this chat; work in your folder, '
                     'or git clone a repository there.')
    lines.append('If the person explicitly asks you to run code or change files on their own computer ("my '
                 "computer\", \"my laptop\"), call Switch Computer For Code with computer='person' first. Otherwise "
                 'always work in the sandbox.')
    return '\n'.join(lines)
