"""Run an agent on a coding agent CLI that is already signed in on this computer.

Claude Code, Codex, Gemini CLI, GitHub Copilot CLI and OpenCode each come
with their maker's sign-in, so they run on the person's own plan (Claude
Pro/Max, ChatGPT Plus/Pro, Google AI, Copilot) or key. Tools like T3 Code,
Enjoy and Happy work this way: they start the vendor's own, unmodified CLI
and read its machine-readable output. Anthropic's terms say so in as many
words: a person signing in to the unmodified Claude Code binary with their
own subscription is allowed, while handing that sign-in to another app is
not. So Cognitrix never touches these logins; it only starts the CLI.

An agent whose provider is one of ``HARNESSES`` sends each chat turn to the
CLI instead of a model API:

* The CLI runs in the chat's primary working folder, with the agent's
  permission level (read only, edit files, or full) mapped to the CLI's own
  permission flags. It does its own tool work (reading, editing, commands).
* The first turn starts a CLI session with the agent's instructions and the
  chat so far; later turns resume that session and send only the new
  message. The mapping lives in ``<data folder>/harness_sessions.json``.
* Text streams into the chat; what the CLI does (commands, edits) shows as
  the reply's reasoning, so the answer stays clean.

The CLIs start other programs on this computer, so this only works where
the shell does: the desktop app and the command line, not a shared server.

Everything the person types goes in on stdin, never on the command line
(npm installs these CLIs as ``.cmd`` files on Windows, run through cmd.exe).
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognitrix.errors import ExecutionControlError
from cognitrix.utils.llm_response import LLMResponse

logger = logging.getLogger('cognitrix.log')

MODES = ('read_only', 'edit', 'full')
DEFAULT_MODE = 'edit'
TURN_TIMEOUT = 60 * 60
MAX_TRANSCRIPT_CHARS = 60_000
PLACEHOLDER_KEY = 'signed-in-cli'
"""Stands in for an API key in agent configs: the CLI signs itself in."""
WHOLE_MESSAGES = frozenset({'codex', 'opencode'})
"""CLIs that send whole messages, not deltas: the last one is the answer."""


@dataclass
class TurnInfo:
    """What the chat turn knows that a model call doesn't: set by the session."""
    session_id: str | None
    folders: tuple[Any, ...]
    host_process_mode: Any


turn: contextvars.ContextVar[TurnInfo | None] = contextvars.ContextVar('harness_turn', default=None)


class HarnessError(RuntimeError):
    pass


@dataclass(frozen=True)
class Harness:
    key: str
    label: str
    program: str
    install_hint: str
    models: tuple[str, ...]
    """Suggestions for the model picker; '' (the CLI's own default) is always fine."""
    resumes_with_new_id: bool
    """True when we pick the session id up front (Gemini CLI, Copilot CLI)."""


HARNESSES: dict[str, Harness] = {
    'claude-code': Harness(
        'claude-code', 'Claude Code', 'claude',
        'npm install -g @anthropic-ai/claude-code, then run `claude` once and sign in',
        ('sonnet', 'opus', 'haiku', 'claude-sonnet-5-5', 'claude-opus-5-5'), False),
    'codex': Harness(
        'codex', 'Codex', 'codex',
        'npm install -g @openai/codex, then run `codex login` (Sign in with ChatGPT)',
        ('gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-5.5'), False),
    'gemini-cli': Harness(
        'gemini-cli', 'Gemini CLI', 'gemini',
        'npm install -g @google/gemini-cli, then run `gemini` once and sign in',
        ('gemini-3.6-flash', 'gemini-3.1-pro', 'gemini-3.5-flash'), True),
    'copilot-cli': Harness(
        'copilot-cli', 'GitHub Copilot CLI', 'copilot',
        'npm install -g @github/copilot, then run `copilot login` (needs a Copilot plan)',
        ('auto', 'claude-sonnet-5-5', 'gpt-6.1-sol'), True),
    'opencode': Harness(
        'opencode', 'OpenCode', 'opencode',
        'npm install -g opencode-ai, then run `opencode auth login`',
        ('openai/gpt-6.1-sol', 'anthropic/claude-sonnet-5-5', 'github-copilot/gpt-5.5'), False),
}


def is_harness(provider: str | None) -> bool:
    return (provider or '').strip().lower() in HARNESSES


def normalize_mode(value: Any) -> str:
    text = str(value or '').strip().lower().replace('-', '_')
    return text if text in MODES else DEFAULT_MODE


def find_program(key: str) -> str | None:
    import shutil

    from cognitrix.common.shell import child_env

    harness = HARNESSES[key]
    override = os.environ.get(f"COGNITRIX_{key.upper().replace('-', '_')}_PATH")
    if override:
        return override if Path(override).exists() else None
    return shutil.which(harness.program, path=child_env().get('PATH'))


def installed() -> dict[str, str]:
    found = {}
    for key in HARNESSES:
        program = find_program(key)
        if program:
            found[key] = program
    return found


# --- what to send ----------------------------------------------------------------

def _text(content: Any) -> str:
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            if part.get('type') == 'text':
                parts.append(str(part.get('text') or ''))
            elif part.get('type') == 'image_url':
                parts.append('[an image was attached here; it is not passed to this agent]')
    return '\n'.join(p for p in parts if p)


def split_prompt(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], str, int]:
    """(instructions, earlier messages, the new user message, user message count)."""
    instructions: list[str] = []
    rest: list[dict[str, Any]] = []
    started = False
    for msg in messages:
        if msg.get('role') == 'system' and not started:
            text = _text(msg.get('content'))
            if text:
                instructions.append(text)
            continue
        started = True
        rest.append(msg)
    last_user = max((i for i, m in enumerate(rest) if m.get('role') == 'user'), default=None)
    if last_user is None:
        return '\n\n'.join(instructions), rest, 'Continue.', 0
    new_message = _text(rest[last_user].get('content')) or 'Continue.'
    # Anything after the last user message (system notes) rides along with it.
    tail = [_text(m.get('content')) for m in rest[last_user + 1:] if m.get('role') == 'system']
    if tail:
        new_message += '\n\n' + '\n'.join(t for t in tail if t)
    users = sum(1 for m in rest if m.get('role') == 'user')
    return '\n\n'.join(instructions), rest[:last_user], new_message, users


def _transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for msg in messages:
        role = msg.get('role')
        text = _text(msg.get('content')).strip()
        if role == 'tool':
            text = f'(tool result) {text[:2000]}'
        elif msg.get('tool_calls'):
            names = ', '.join(str((c.get('function') or {}).get('name') or '') for c in msg['tool_calls'])
            text = (text + f'\n(called {names})').strip()
        if not text:
            continue
        who = {'user': 'User', 'assistant': 'You', 'tool': 'Tool', 'system': 'Note'}.get(role, 'Note')
        lines.append(f'{who}: {text}')
    text = '\n\n'.join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = '[earlier messages left out]\n\n' + text[-MAX_TRANSCRIPT_CHARS:]
    return text


def first_message(instructions: str, earlier: list[dict[str, Any]], new_message: str) -> str:
    parts = []
    if instructions.strip():
        parts.append(
            'You are running as an agent inside Cognitrix. Follow these instructions for the whole '
            f'conversation:\n<instructions>\n{instructions.strip()}\n</instructions>'
        )
    history = _transcript(earlier)
    if history:
        parts.append(f'The conversation so far:\n<conversation>\n{history}\n</conversation>')
    parts.append(new_message if not parts else f'The new message:\n{new_message}')
    return '\n\n'.join(parts)


# --- remembered CLI sessions -----------------------------------------------------

_store_lock = threading.Lock()


def _store_file() -> Path:
    from cognitrix.config import settings

    return Path(settings.workdir) / 'harness_sessions.json'


def _load_store() -> dict[str, Any]:
    try:
        data = json.loads(_store_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remembered(chat_id: str | None, key: str, model: str, cwd: str) -> dict[str, Any] | None:
    if not chat_id:
        return None
    entry = _load_store().get(chat_id)
    if not isinstance(entry, dict):
        return None
    if (entry.get('harness'), entry.get('model'), entry.get('cwd')) != (key, model, cwd):
        return None
    return entry


def remember(chat_id: str | None, key: str, model: str, cwd: str, session: str, users: int) -> None:
    if not chat_id or not session:
        return
    with _store_lock:
        data = _load_store()
        data[chat_id] = {'harness': key, 'model': model, 'cwd': cwd, 'session': session,
                         'users': users, 'at': time.time()}
        # Keep the file small: the most recent 500 chats.
        if len(data) > 500:
            for old in sorted(data, key=lambda k: data[k].get('at', 0))[:len(data) - 500]:
                data.pop(old, None)
        path = _store_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.harness_sessions.', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)
        os.replace(tmp, path)


# --- command lines ---------------------------------------------------------------

def build_command(key: str, program: str, *, mode: str, model: str, effort: str,
                  resume: str | None, new_session: str | None, cwd: str) -> tuple[list[str], dict[str, str]]:
    """(argv, extra environment). The message itself always goes on stdin."""
    env: dict[str, str] = {}
    if key == 'claude-code':
        permission = {'read_only': 'plan', 'edit': 'acceptEdits', 'full': 'bypassPermissions'}[mode]
        argv = [program, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
                '--permission-mode', permission]
        if model:
            argv += ['--model', model]
        if resume:
            argv += ['--resume', resume]
        return argv, env
    if key == 'codex':
        sandbox = {'read_only': 'read-only', 'edit': 'workspace-write', 'full': 'danger-full-access'}[mode]
        if resume:
            argv = [program, 'exec', 'resume', '--json', '--skip-git-repo-check',
                    '-c', f'sandbox_mode="{sandbox}"']
        else:
            argv = [program, 'exec', '--json', '--skip-git-repo-check', '--sandbox', sandbox, '-C', cwd]
        if model:
            argv += ['-m', model]
        if effort in ('minimal', 'low', 'medium', 'high', 'xhigh'):
            argv += ['-c', f'model_reasoning_effort="{effort}"']
        if resume:
            argv += [resume]
        return argv + ['-'], env
    if key == 'gemini-cli':
        approval = {'read_only': 'plan', 'edit': 'auto_edit', 'full': 'yolo'}[mode]
        argv = [program, '-p', '', '--output-format', 'stream-json', '--approval-mode', approval, '--skip-trust']
        if model:
            argv += ['-m', model]
        argv += ['--resume', resume] if resume else ['--session-id', new_session or str(uuid.uuid4())]
        return argv, env
    if key == 'copilot-cli':
        argv = [program, '--output-format', 'json', '--no-color', '--no-ask-user', '--no-auto-update',
                '--session-id', resume or new_session or str(uuid.uuid4())]
        if mode == 'edit':
            argv += ['--allow-tool', 'write']
        elif mode == 'full':
            argv += ['--allow-all']
        if model:
            argv += ['--model', model]
        if effort in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh'):
            argv += ['--reasoning-effort', effort]
        return argv, env
    if key == 'opencode':
        permission = {
            'read_only': {'edit': 'deny', 'bash': 'deny'},
            'edit': {'edit': 'allow', 'bash': 'deny'},
            'full': {'edit': 'allow', 'bash': 'allow', 'webfetch': 'allow'},
        }[mode]
        env['OPENCODE_CONFIG_CONTENT'] = json.dumps({'permission': permission})
        argv = [program, 'run', '--format', 'json', '--dir', cwd]
        if model:
            argv += ['-m', model]
        if resume:
            argv += ['--session', resume]
        return argv, env
    raise HarnessError(f'Unknown coding agent {key!r}.')


# --- reading the output ------------------------------------------------------------

@dataclass
class Event:
    """One thing worth showing: text, activity, a session id, usage, an error."""
    text: str = ''
    activity: str = ''
    session: str = ''
    usage: dict[str, int] | None = None
    error: str = ''
    final_text: str = ''
    """A whole message, for CLIs that don't stream deltas (used if no deltas came)."""


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit] + '…'


def _tool_line(name: str, args: Any) -> str:
    if isinstance(args, dict):
        for k in ('command', 'cmd', 'file_path', 'path', 'pattern', 'url', 'query', 'description'):
            if args.get(k):
                return f'› {name}: {_short(args[k])}'
    return f'› {name}' + (f': {_short(args)}' if args else '')


def parse_line(key: str, line: str) -> list[Event]:
    line = line.strip()
    if not line.startswith('{'):
        return []
    try:
        e = json.loads(line)
    except ValueError:
        return []
    if not isinstance(e, dict):
        return []
    t = e.get('type')
    if key == 'claude-code':
        if t == 'system' and e.get('subtype') == 'init':
            return [Event(session=str(e.get('session_id') or ''))]
        if t == 'stream_event':
            ev = e.get('event') or {}
            if e.get('parent_tool_use_id'):
                return []  # a sub-agent's own stream
            if ev.get('type') == 'content_block_delta':
                d = ev.get('delta') or {}
                if d.get('type') == 'text_delta':
                    return [Event(text=d.get('text') or '')]
                if d.get('type') == 'thinking_delta':
                    return [Event(activity=d.get('thinking') or '')]
            if ev.get('type') == 'message_stop':
                return [Event(text='\n\n')]
            return []
        if t == 'assistant':
            out = []
            for block in (e.get('message') or {}).get('content') or []:
                if block.get('type') == 'tool_use':
                    out.append(Event(activity=_tool_line(block.get('name') or 'tool', block.get('input')) + '\n'))
            return out
        if t == 'result':
            usage = e.get('usage') or {}
            counts = {
                'prompt_tokens': int(usage.get('input_tokens') or 0) + int(usage.get('cache_read_input_tokens') or 0)
                + int(usage.get('cache_creation_input_tokens') or 0),
                'completion_tokens': int(usage.get('output_tokens') or 0),
                'cached_tokens': int(usage.get('cache_read_input_tokens') or 0),
            }
            events = [Event(session=str(e.get('session_id') or ''), usage=counts)]
            if e.get('is_error') or (e.get('subtype') not in (None, 'success')):
                events.append(Event(error=_short(e.get('result') or e.get('subtype') or 'Claude Code failed', 400)))
            else:
                events.append(Event(final_text=str(e.get('result') or '')))
            return events
        return []
    if key == 'codex':
        if t == 'thread.started':
            return [Event(session=str(e.get('thread_id') or ''))]
        item = e.get('item') or {}
        kind = item.get('type')
        if t == 'item.completed' and kind == 'agent_message':
            return [Event(text=(item.get('text') or '').rstrip() + '\n\n')]
        if t == 'item.completed' and kind == 'reasoning':
            return [Event(activity=(item.get('text') or '') + '\n')]
        if t == 'item.started' and kind == 'command_execution':
            return [Event(activity=f"› {_short(item.get('command'))}\n")]
        if t == 'item.completed' and kind == 'file_change':
            files = ', '.join(str(c.get('path')) for c in item.get('changes') or [] if isinstance(c, dict))
            return [Event(activity=f'› edited {files or "files"}\n')]
        if t == 'item.completed' and kind == 'mcp_tool_call':
            return [Event(activity=f"› {item.get('server')}.{item.get('tool')}\n")]
        if t == 'item.completed' and kind == 'web_search':
            return [Event(activity=f"› web search: {_short(item.get('query'))}\n")]
        if t == 'turn.completed':
            u = e.get('usage') or {}
            return [Event(usage={'prompt_tokens': int(u.get('input_tokens') or 0),
                                 'completion_tokens': int(u.get('output_tokens') or 0),
                                 'cached_tokens': int(u.get('cached_input_tokens') or 0)})]
        if t in ('turn.failed', 'error'):
            err = e.get('error') or {}
            return [Event(error=_short(err.get('message') if isinstance(err, dict) else e.get('message') or err, 400))]
        return []
    if key == 'gemini-cli':
        if t == 'init':
            return [Event(session=str(e.get('session_id') or ''))]
        if t == 'message' and e.get('role') == 'assistant':
            return [Event(text=str(e.get('content') or ''))]
        if t == 'tool_use':
            return [Event(activity=_tool_line(str(e.get('tool_name') or 'tool'), e.get('parameters')) + '\n')]
        if t == 'result':
            stats = e.get('stats') or {}
            events = [Event(usage={'prompt_tokens': int(stats.get('input_tokens') or 0),
                                   'completion_tokens': int(stats.get('output_tokens') or 0),
                                   'cached_tokens': int(stats.get('cached') or 0)})]
            if e.get('status') == 'error':
                events.append(Event(error=_short((e.get('error') or {}).get('message') or 'Gemini CLI failed', 400)))
            return events
        if t == 'error' and e.get('severity') == 'error':
            return [Event(error=_short(e.get('message') or 'Gemini CLI failed', 400))]
        return []
    if key == 'copilot-cli':
        data = e.get('data') or {}
        if t == 'session.start':
            return [Event(session=str(data.get('sessionId') or ''))]
        if t == 'assistant.message_delta':
            return [Event(text=str(data.get('deltaContent') or ''))]
        if t == 'assistant.message':
            return [Event(final_text=str(data.get('content') or ''))]
        if t == 'assistant.reasoning_delta':
            return [Event(activity=str(data.get('deltaContent') or ''))]
        if t == 'tool.execution_start':
            args = data.get('arguments')
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                pass
            return [Event(activity=_tool_line(str(data.get('toolName') or 'tool'), args) + '\n')]
        if t == 'assistant.usage':
            return [Event(usage={'prompt_tokens': int(data.get('inputTokens') or 0),
                                 'completion_tokens': int(data.get('outputTokens') or 0)})]
        if t == 'session.error':
            return [Event(error=_short(data.get('message') or 'Copilot CLI failed', 400))]
        return []
    if key == 'opencode':
        part = e.get('part') or {}
        events = []
        if e.get('sessionID'):
            events.append(Event(session=str(e['sessionID'])))
        if t == 'text':
            events.append(Event(text=str(part.get('text') or '') + '\n\n'))
        elif t == 'reasoning':
            events.append(Event(activity=str(part.get('text') or '') + '\n'))
        elif t == 'tool_use':
            state = part.get('state') or {}
            events.append(Event(activity=_tool_line(str(part.get('tool') or 'tool'), state.get('input')) + '\n'))
        elif t == 'step_finish':
            tokens = part.get('tokens') or {}
            cache = tokens.get('cache') or {}
            events.append(Event(usage={'prompt_tokens': int(tokens.get('input') or 0) + int(cache.get('read') or 0),
                                       'completion_tokens': int(tokens.get('output') or 0),
                                       'cached_tokens': int(cache.get('read') or 0)}))
        elif t == 'error':
            err = e.get('error') or {}
            msg = (err.get('data') or {}).get('message') if isinstance(err, dict) else None
            events.append(Event(error=_short(msg or err or 'OpenCode failed', 400)))
        return events
    return []


# --- running ---------------------------------------------------------------------

Runner = Callable[[list[str], str, str, dict[str, str]], Any]


def _spawn(argv: list[str], stdin_text: str, cwd: str, env_extra: dict[str, str],
           loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> subprocess.Popen:
    """Start the CLI; a thread feeds its stdout lines (then None) to ``queue``."""
    from cognitrix.common.shell import _group_kwargs, child_env

    env = child_env()
    env.update(env_extra)
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **_group_kwargs(),
    )
    errors: list[str] = []

    def write() -> None:
        try:
            proc.stdin.write(stdin_text.encode('utf-8'))
            proc.stdin.close()
        except OSError:
            pass

    def read_err() -> None:
        for raw in proc.stderr:
            if len(errors) < 200:
                errors.append(raw.decode('utf-8', 'replace').rstrip())

    def read_out() -> None:
        for raw in proc.stdout:
            loop.call_soon_threadsafe(queue.put_nowait, raw.decode('utf-8', 'replace'))
        proc.wait()
        err_thread.join(timeout=5)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    err_thread = threading.Thread(target=read_err, daemon=True)
    threading.Thread(target=write, daemon=True).start()
    err_thread.start()
    threading.Thread(target=read_out, daemon=True).start()
    proc.stderr_lines = errors  # type: ignore[attr-defined]
    return proc


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            from cognitrix.common.shell import kill_tree

            kill_tree(proc)
        except Exception:  # noqa: BLE001
            proc.kill()


def _stderr_hint(proc: subprocess.Popen) -> str:
    lines = [ln for ln in getattr(proc, 'stderr_lines', []) if ln.strip()]
    for ln in reversed(lines):
        low = ln.lower()
        if any(w in low for w in ('login', 'log in', 'sign in', 'auth', 'unauthor', 'credential', 'api key')):
            return ln.strip()[:300]
    return lines[-1].strip()[:300] if lines else ''


async def stream(llm: Any, messages: list[dict[str, Any]], effort: str = '') -> AsyncIterator[LLMResponse]:
    """Run one chat turn on the CLI, yielded like base.py's stream handler."""
    from cognitrix.providers.base import record_usage

    key = str(llm.provider).lower()
    harness = HARNESSES[key]
    response = LLMResponse()
    proc: subprocess.Popen | None = None
    in_reasoning = False

    def fail(msg: str) -> LLMResponse:
        err = LLMResponse(llm_response=msg, error=msg)
        err.current_chunk = msg
        return err

    try:
        from cognitrix.common import folders as folder_paths
        from cognitrix.common.process_security import HostProcessAccessError, require_host_process_authority
        from cognitrix.tools.utils import current_execution_context

        info = turn.get()
        host_mode = info.host_process_mode if info is not None else current_execution_context().host_process_mode
        try:
            require_host_process_authority(host_mode)
        except HostProcessAccessError:
            yield fail(
                f'{harness.label} runs on this computer, so it works in the desktop app and the command line, '
                'not on a shared server. Pick a model provider for this agent instead.'
            )
            return
        program = find_program(key)
        if not program:
            yield fail(f'{harness.label} is not installed here. Install it with: {harness.install_hint}.')
            return

        mode = normalize_mode(getattr(llm, 'harness_mode', '') or DEFAULT_MODE)
        chat_folders = tuple(info.folders) if info is not None and info.folders else folder_paths.active_folders()
        primary = next((f for f in chat_folders if getattr(f, 'primary', False)), chat_folders[0] if chat_folders else None)
        cwd = str(primary.root) if primary is not None else str(folder_paths.primary_folder())
        if primary is not None and mode != 'read_only' and not primary.writable:
            mode = 'read_only'
            note = f"'{primary.label}' is read only in this chat, so {harness.label} can't change files.\n"
            response.add_reasoning_chunk(note)
            response.current_chunk = '<think>' + note
            in_reasoning = True
            yield response

        model = str(getattr(llm, 'model', '') or '').strip()
        if model in ('default', 'auto') and key != 'copilot-cli':
            model = ''
        chat_id = info.session_id if info is not None else None
        instructions, earlier, new_message, users = split_prompt(messages)
        known = remembered(chat_id, key, model, cwd)
        if known and known.get('users') == users - 1 and known.get('session'):
            resume, text = known['session'], new_message
        else:
            resume, text = None, first_message(instructions, earlier, new_message)
        new_session = str(uuid.uuid4()) if harness.resumes_with_new_id and not resume else None

        argv, env_extra = build_command(key, program, mode=mode, model=model, effort=effort,
                                        resume=resume, new_session=new_session, cwd=cwd)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        proc = _spawn(argv, text, cwd, env_extra, loop, queue)
        session = resume or new_session or ''
        usage: dict[str, int] | None = None
        errors: list[str] = []
        final_text = ''
        pending = ''
        deadline = time.monotonic() + TURN_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append(f'{harness.label} took longer than {TURN_TIMEOUT // 60} minutes and was stopped.')
                break
            line = await asyncio.wait_for(queue.get(), timeout=remaining)
            if line is None:
                break
            for event in parse_line(key, line):
                if event.session:
                    session = event.session
                if event.usage:
                    usage = event.usage
                if event.error:
                    errors.append(event.error)
                if event.final_text:
                    final_text = event.final_text
                if event.activity:
                    if pending:
                        # Text followed by more work was a progress note, not the answer.
                        event.activity, pending = pending.strip() + '\n' + event.activity, ''
                    response.add_reasoning_chunk(event.activity)
                    response.current_chunk = event.activity if in_reasoning else '<think>' + event.activity
                    in_reasoning = True
                    yield response
                if event.text and key in WHOLE_MESSAGES:
                    if pending:
                        response.add_reasoning_chunk(pending.strip() + '\n')
                        response.current_chunk = (pending.strip() + '\n') if in_reasoning else '<think>' + pending.strip() + '\n'
                        in_reasoning = True
                        yield response
                    pending = event.text
                    continue
                if event.text:
                    chunk = event.text
                    if not response.llm_response:
                        chunk = chunk.lstrip()
                        if not chunk:
                            continue
                    response.add_chunk(chunk)
                    response.current_chunk = ('\n</think>\n' + chunk) if in_reasoning else chunk
                    in_reasoning = False
                    yield response
        code = proc.wait(timeout=30)
        if pending.strip():
            chunk = pending.strip()
            response.add_chunk(chunk)
            response.current_chunk = ('\n</think>\n' + chunk) if in_reasoning else chunk
            in_reasoning = False
            yield response
        closing = '\n</think>\n' if in_reasoning else ''
        if not (response.llm_response or '').strip() and final_text.strip():
            response.add_chunk(final_text.strip())
            closing += final_text.strip()
        if errors or (code not in (0, None) and not (response.llm_response or '').strip()):
            detail = errors[-1] if errors else (_stderr_hint(proc) or f'it exited with code {code}')
            msg = f'{harness.label} failed: {detail}'
            if any(w in detail.lower() for w in ('login', 'log in', 'sign in', 'auth', 'credential')):
                msg += f' Sign in to {harness.label} on this computer first ({harness.install_hint.split(", then ")[-1]}).'
            yield fail(msg)
            return
        remember(chat_id, key, model, cwd, session, users)
        response.llm_response = (response.llm_response or '').rstrip()
        response.finish_reason = 'stop'
        if usage:
            response.usage = usage
            record_usage(usage)
        response.current_chunk = closing
        yield response
    except ExecutionControlError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - shown in the chat
        logger.exception('Coding agent turn failed: %s', exc)
        yield fail(f'{harness.label} failed: {exc}')
    finally:
        if proc is not None:
            _stop(proc)


async def respond(llm: Any, messages: list[dict[str, Any]], effort: str = '') -> LLMResponse:
    last: LLMResponse | None = None
    async for item in stream(llm, messages, effort):
        last = item
    if last is None:
        msg = 'The coding agent sent no reply.'
        return LLMResponse(llm_response=msg, error=msg)
    last.current_chunk = ''
    return last
