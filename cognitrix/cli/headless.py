"""One-shot runs for scripts, CI and benchmarks (``cognitrix -p ...``).

What other agent CLIs offer for headless use, on top of the turn the CLI
already runs (cognitrix/cli/app_turn.py):

* ``--output-format text|json|stream-json``. text prints only the reply on
  stdout. json prints one result object at the end. stream-json prints one
  JSON object per line as things happen: ``init``, ``text``, ``tool_use``,
  ``tool_result``, then ``result``. Everything else the app prints (tool
  lines, warnings, logs) goes to stderr, so stdout stays parseable.
* ``--resume ID`` (also ``--session ID``) continues a saved chat by id;
  ``cognitrix sessions`` lists them.
* ``--max-turns N`` caps tool rounds, ``--timeout S`` caps the whole run,
  ``--effort`` sets reasoning effort, ``--allowed-tools`` /
  ``--disallowed-tools`` narrow the agent's tools, ``--system-prompt`` /
  ``--append-system-prompt`` change its instructions; all for this run only.
* Exit codes: 0 done, 1 the turn failed, 2 bad arguments, 124 timed out,
  130 interrupted.

Approvals: with a terminal, risky tools ask on stderr. Without one (stdin is
not a TTY) they are refused, unless auto-approve applies (the person's
"Auto-approve everything" setting or ``--dangerously-skip-permissions``).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from argparse import Namespace
from typing import Any

from .app_turn import WSQUERY, TerminalChannel, _err, _interactive, _short

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_TIMEOUT = 124
EXIT_INTERRUPTED = 130

FORMATS = ('text', 'json', 'stream-json')
TASK_FAILED = 'Multi-step task failed:'  # tasks/handler.py

# Per-run agent changes a task run can't carry: its steps reload the saved
# agent by id (Task.team()).
AGENT_RUN_OPTIONS = ('system_prompt', 'append_system_prompt', 'allowed_tools', 'disallowed_tools', 'mcp_config')


def task_conflicts(args: Namespace) -> list[str]:
    """The per-run agent options given with --task (not supported there)."""
    return ['--' + name.replace('_', '-') for name in AGENT_RUN_OPTIONS if getattr(args, name, '')]


class UsageError(Exception):
    """Bad arguments: exit code 2."""


# --- stdout ---------------------------------------------------------------------

class _Stdout:
    """The real stdout, kept for the reply while fd 1 points at stderr.

    Tools print progress with ``print`` and subprocesses inherit fd 1; in a
    one-shot run none of that may land between the reply's bytes."""

    def __init__(self) -> None:
        self.fd: int | None = None
        self.stream: Any = sys.stdout
        self._saved_sys_stdout: Any = None

    def __enter__(self) -> _Stdout:
        try:
            sys.stdout.flush()
            self.fd = os.dup(sys.stdout.fileno())
            os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
            self.stream = os.fdopen(self.fd, 'w', encoding='utf-8', errors='replace', closefd=False)
        except (OSError, ValueError, AttributeError):
            # No real file descriptors (tests, embedded): keep the stream, but
            # still send stray prints to stderr.
            self.fd = None
            self.stream = sys.stdout
        self._saved_sys_stdout = sys.stdout
        sys.stdout = sys.stderr
        return self

    def write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except Exception:
            pass

    def __exit__(self, *exc: Any) -> None:
        sys.stdout = self._saved_sys_stdout
        try:
            self.stream.flush()
        except Exception:
            pass
        if self.fd is not None:
            try:
                sys.stdout.flush()
                os.dup2(self.fd, sys.stdout.fileno())
                os.close(self.fd)
            except (OSError, ValueError):
                pass


# --- the run's events -------------------------------------------------------------

class HeadlessChannel(TerminalChannel):
    """The terminal channel, recording the turn for --output-format."""

    def __init__(self, owner: str, fmt: str, out: _Stdout, *, show_tools: bool = True):
        super().__init__(owner, self._write_text, show_tools=show_tools)
        self.fmt = fmt
        self.out = out
        self.text_parts: list[str] = []   # everything the agent said
        self.since_tool: list[str] = []   # what it said after its last tool call
        self.tool_calls = 0
        self.tool_errors = 0
        self.denied: list[str] = []

    # stream-json: one object per line
    def line(self, obj: dict[str, Any]) -> None:
        if self.fmt == 'stream-json':
            self.out.write(json.dumps(obj, ensure_ascii=False, default=str) + '\n')

    def _write_text(self, text: str, end: str = '') -> None:
        text = f"{text}{end}"
        if not text:
            return
        self.text_parts.append(text)
        self.since_tool.append(text)
        if self.fmt == 'text':
            self.out.write(text)
        else:
            self.line({'type': 'text', 'text': text})

    async def emit(self, event: Any = None, *args: Any, **kwargs: Any) -> None:
        if isinstance(event, dict):
            kind = event.get('type')
            if kind == 'tool':
                self._tool(event)
                return
            if kind == 'approval_request' and not _interactive():
                self.denied.append(str(event.get('tool_name') or 'tool'))
            # The local CLI turn ('acp' interface) sends text without a type.
            if kind in (None, '', 'text') and isinstance(event.get('content'), str):
                event = {**event, 'type': WSQUERY['type']}
        elif event and not isinstance(event, str):
            event = str(event)
        await super().emit(event, *args, **kwargs)

    def _tool(self, event: dict[str, Any]) -> None:
        status = event.get('status')
        name = str(event.get('tool_name') or 'tool')
        call_id = str(event.get('tool_call_id') or '')
        if status == 'started':
            self.tool_calls += 1
            self.since_tool = []
            self.line({'type': 'tool_use', 'id': call_id, 'name': name, 'input': _as_json(event.get('params'))})
        else:
            is_error = status == 'error'
            self.tool_errors += int(is_error)
            result = event.get('result')
            self.line({'type': 'tool_result', 'id': call_id, 'name': name, 'is_error': is_error,
                       'content': result if isinstance(result, str) else json.dumps(result, default=str)})
        if self.fmt == 'text' or self.show_tools:
            super()._tool(event)

    @property
    def full_text(self) -> str:
        return ''.join(self.text_parts).strip()

    @property
    def final_text(self) -> str:
        return ''.join(self.since_tool).strip() or self.full_text


def _as_json(value: Any) -> Any:
    """Tool params arrive as a JSON preview string; give scripts the object."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def here_first(session: Any, agent: Any) -> None:
    """The folder this run works in becomes the chat's main folder; folders
    the chat already had (an earlier run's, --add-dir) are kept."""
    from .app_turn import terminal_folders

    folders = terminal_folders(agent)
    here = folders[0]
    kept = [dict(f, primary=False) for f in (getattr(session, 'folders', None) or folders[1:])
            if isinstance(f, dict) and os.path.abspath(str(f.get('path') or '')) != here['path']]
    session.folders = [here, *kept]


# --- approvals for the CLI's own (account-less) turn --------------------------------

async def _terminal_approver(tool_call: Any, risk: Any, person_only: bool) -> Any:
    """Approval for a turn with no account: ask on stderr, or refuse with no terminal."""
    from cognitrix.safety.approval_gate import ApprovalResult

    level = getattr(getattr(risk, 'risk_level', None), 'value', '')
    _err(f"\n⚠ {tool_call.tool_name} needs your OK" + (f" ({level} risk)" if level else ''))
    _err(f"  {_short(tool_call.params, 400)}")
    details = str(getattr(risk, 'details', '') or '')
    if details:
        _err(f"  {_short(details, 400)}")
    if not _interactive():
        _err('  Refused: no terminal to ask in. Use --dangerously-skip-permissions in a throwaway sandbox.')
        return ApprovalResult(approved=False, error='approval_required')
    try:
        answer = (await asyncio.to_thread(input, '  Allow? [y]es / [s]ession / [n]o: ')).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ApprovalResult(approved=False)
    if answer in ('y', 'yes'):
        return ApprovalResult(approved=True)
    if answer in ('s', 'session'):
        return ApprovalResult(approved=True, remember=True)
    return ApprovalResult(approved=False)


async def _local_turn(session: Any, agent: Any, text: str, *, stream: bool, channel: HeadlessChannel) -> None:
    """The CLI's own turn (no account), with structured events: the editor
    (ACP) path, in the folder the CLI runs in."""
    from cognitrix.safety.approval_gate import acp_approver
    from cognitrix.tools.utils import HostProcessMode, ToolExecutionContext

    here_first(session, agent)

    async def approve(tool_call: Any, risk: Any, person_only: bool) -> Any:
        result = await _terminal_approver(tool_call, risk, person_only)
        if not result.approved:
            channel.denied.append(str(tool_call.tool_name))
        return result

    token = acp_approver.set(approve)
    try:
        await session(
            text, agent, 'acp', stream, channel.emit, {}, True,
            tool_context=ToolExecutionContext(host_process_mode=HostProcessMode.TRUSTED_LOCAL),
        )
    finally:
        acp_approver.reset(token)
    if channel.fmt == 'text' and channel.wrote_text:
        channel._text('\n')


# --- per-run agent options ---------------------------------------------------------

def _names(value: str | None) -> list[str]:
    return [n.strip() for n in (value or '').replace(' ', ',').split(',') if n.strip()] if value else []


def apply_run_options(agent: Any, args: Namespace) -> None:
    """Changes to the agent for this run only (it is not saved afterwards)."""
    system_prompt = getattr(args, 'system_prompt', '') or ''
    if system_prompt:
        agent.system_prompt = system_prompt
    extra = getattr(args, 'append_system_prompt', '') or ''
    if extra:
        agent.system_prompt = f"{agent.system_prompt or ''}\n\n{extra}".strip()

    allowed_names = _names(getattr(args, 'allowed_tools', ''))
    denied_names = _names(getattr(args, 'disallowed_tools', ''))
    if allowed_names or denied_names:
        from cognitrix.safety.permissions import run_tool_limits, tool_key

        allowed = {tool_key(n) for n in allowed_names}
        if allowed and getattr(args, 'mcp_config', None):
            allowed |= {'list_mcp_tools', 'run_mcp_tool'}  # how the agent reaches --mcp-config servers
        tools = list(agent.tools or [])
        if allowed:
            from cognitrix.tools.base import ToolManager

            # A tool named in --allowed-tools that the agent doesn't have yet is added.
            have = {tool_key(t.name) for t in tools}
            for tool in ToolManager.list_all_tools():
                if tool_key(tool.name) in allowed and tool_key(tool.name) not in have:
                    tools.append(tool)
                    have.add(tool_key(tool.name))
        agent.tools = tools
        # Applies to every tool a turn offers, the per-chat ones included.
        run_tool_limits.set((frozenset(allowed) if allowed else None, frozenset(tool_key(n) for n in denied_names)))

    max_turns = int(getattr(args, 'max_turns', 0) or 0)
    if max_turns > 0:
        from cognitrix.sessions import base as session_base

        session_base.MAX_TOOL_ROUNDS = max_turns


def set_effort(args: Namespace) -> None:
    """--effort for this run (a per-turn override, like the chat window's picker)."""
    effort = (getattr(args, 'effort', '') or '').strip().lower()
    if effort:
        from cognitrix.providers.base import turn_reasoning_effort

        turn_reasoning_effort.set(effort)


# --- resume ---------------------------------------------------------------------

async def load_resumed(session_id: str, agent: Any, owner: str | None, *, explicit_agent: bool) -> tuple[Any, Any]:
    """The saved chat ``--resume`` names, and the agent to run it with: its own
    agent, unless --agent picked one."""
    from cognitrix.agents import Agent

    from .handlers_sessions import find_session

    session = await find_session(session_id)
    if session is None:
        raise UsageError(f"No saved session {session_id}. `cognitrix sessions` lists them.")
    session_owner = str(getattr(session, 'user_id', '') or '')
    if (owner or '') != session_owner:
        if owner:
            raise UsageError(f"Session {session_id} isn't one of this account's chats.")
        raise UsageError(f"Session {session_id} belongs to an account: add --user with its email.")
    if getattr(session, 'task_id', None) or getattr(session, 'run_id', None):
        raise UsageError(f"Session {session_id} is a task run, not a chat.")
    if not explicit_agent and session.agent_id and str(session.agent_id) != str(agent.id):
        saved = await Agent.find_one({'id': str(session.agent_id)})
        if saved is not None:
            agent = saved
    elif explicit_agent:
        session.agent_id = str(agent.id)
    here_first(session, agent)
    return session, agent


# --- prompt ----------------------------------------------------------------------

def read_prompt(args: Namespace) -> str:
    """--prompt-file wins over -p; '-' for either reads stdin."""
    prompt = args.prompt or ''
    path = getattr(args, 'prompt_file', '') or ''
    if path == '-' or (not path and prompt == '-'):
        return sys.stdin.read()
    if path:
        try:
            with open(path, encoding='utf-8') as f:
                return f.read()
        except OSError as e:
            raise UsageError(f"Couldn't read --prompt-file {path}: {e}") from e
    return prompt


# --- the run ---------------------------------------------------------------------

async def close_mcp() -> None:
    """Close MCP servers the run started (stdio servers are child processes)
    on this loop: left open, they keep the CLI from exiting."""
    from cognitrix.mcp import client as mcp_client

    client = getattr(mcp_client, '_dynamic_client', None)
    if client is None or not getattr(client, 'exit_stacks', None):
        return
    try:
        async with asyncio.timeout(5):
            await client.disconnect_all()
    except BaseException:
        pass

def _usage(session: Any) -> dict[str, Any]:
    data = session.token_usage if isinstance(getattr(session, 'token_usage', None), dict) else {}
    models = (data.get('last_turn') or {}).get('models') or {}
    totals: dict[str, int] = {}
    for row in models.values():
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return {
        'input_tokens': totals.get('prompt_tokens', 0),
        'output_tokens': totals.get('completion_tokens', 0),
        **{k: v for k, v in totals.items() if k not in ('prompt_tokens', 'completion_tokens', 'calls')},
        'model_calls': totals.get('calls', 0),
        'by_model': models,
    }


async def run_prompt(session: Any, agent: Any, prompt: str, args: Namespace) -> int:
    """Run one prompt headlessly; returns the exit code."""
    fmt = getattr(args, 'output_format', 'text') or 'text'
    timeout = float(getattr(args, 'timeout', 0) or 0)
    owner = str(getattr(session, 'user_id', '') or '')
    task_run = bool(getattr(args, 'task', False))
    started = time.monotonic()
    if getattr(session, 'id', None) is None:
        await session.save()  # so the run can name its session id up front

    with _Stdout() as out:
        channel = HeadlessChannel(owner, fmt, out, show_tools=bool(getattr(args, 'verbose', False)) or fmt == 'text')
        llm = getattr(agent, 'llm', None)
        channel.line({
            'type': 'system', 'subtype': 'init', 'session_id': str(session.id), 'agent': agent.name,
            'agent_id': str(agent.id), 'provider': str(getattr(llm, 'provider', '') or ''),
            'model': str(getattr(llm, 'model', '') or ''), 'cwd': os.getcwd(),
            'tools': [t.name for t in agent.tools or []], 'account': bool(owner),
        })

        async def work() -> str | None:
            if task_run:
                from cognitrix.tasks.handler import handle_multi_step_task

                result = str(await handle_multi_step_task(prompt, agent, session, agent.llm, args.stream) or '')
                channel._write_text(result, '\n')
                # The handler reports a failed run as text, not an exception.
                if result.startswith(TASK_FAILED):
                    return result.split('\n', 1)[0]
                return None
            if owner:
                from .app_turn import run_turn

                await run_turn(session, agent, prompt, owner=owner, stream=args.stream, channel=channel)
            else:
                await _local_turn(session, agent, prompt, stream=args.stream, channel=channel)
            return session.last_turn_error

        subtype = 'success'
        error: str | None = None
        work_done = work

        async def work() -> str | None:  # noqa: F811
            try:
                return await work_done()
            finally:
                await close_mcp()

        code = EXIT_OK
        try:
            if timeout > 0:
                error = await asyncio.wait_for(work(), timeout)
            else:
                error = await work()
        except TimeoutError:
            subtype, error, code = 'error_timeout', f'Timed out after {timeout:g}s', EXIT_TIMEOUT
        except (KeyboardInterrupt, asyncio.CancelledError):
            subtype, error, code = 'error_interrupted', 'Interrupted', EXIT_INTERRUPTED
        if error and code == EXIT_OK:
            subtype, code = 'error_during_execution', EXIT_FAILED
        if code != EXIT_OK and fmt == 'text':
            _err(f"\n{error}")

        if fmt in ('json', 'stream-json'):
            result = {
                'type': 'result',
                'subtype': subtype,
                'is_error': code != EXIT_OK,
                'result': channel.final_text,
                'session_id': str(session.id),
                'agent': agent.name,
                'model': str(getattr(llm, 'model', '') or ''),
                'duration_ms': int((time.monotonic() - started) * 1000),
                'tool_calls': channel.tool_calls,
                'tool_errors': channel.tool_errors,
                'usage': _usage(session),
                'exit_code': code,
            }
            if error:
                result['error'] = error
            if channel.denied:
                result['permission_denials'] = channel.denied
            if fmt == 'json':
                result['full_text'] = channel.full_text
                out.write(json.dumps(result, ensure_ascii=False, default=str, indent=2) + '\n')
            else:
                channel.line(result)
    return code
