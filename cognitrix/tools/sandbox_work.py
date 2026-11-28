"""Tools for coding in the sandbox (see cognitrix/common/sandbox_work.py).

Two kinds of code live here:

- ``sb_*`` functions: what Bash, Bash Output, Kill Shell, Read, Write, Edit,
  Glob, Grep and Coding Agent do when the turn runs in the sandbox. Those
  tools call them first (``cognitrix/tools/misc.py``).
- Tools that only make sense with a sandbox: Copy Into Sandbox, Sandbox
  Changes, Copy Back From Sandbox and Run Tests. A chat with Run in sandbox on
  gets them for its turns, like Bash Output.
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from typing import Any

from cognitrix.common import sandbox_work as sw
from cognitrix.common import test_results
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome

SANDBOX_TOOL_NAMES = ('copy_into_sandbox', 'sandbox_changes', 'copy_back_from_sandbox', 'run_tests', 'ask_for_sign_in',
                      'list_credentials')
MAX_TEXT = 30_000


def _err(exc: sw.SandboxWorkError) -> str:
    return f'Error: {exc.message}'


# ---------------------------------------------------------------------------
# The usual tools, in the sandbox
# ---------------------------------------------------------------------------

def _job_line(job: dict) -> str:
    if job.get('running'):
        return f"[{job['id']}: running for {int(job.get('seconds') or 0)}s]"
    if job.get('killed'):
        return f"[{job['id']}: stopped ({job['killed']}) after {int(job.get('seconds') or 0)}s]"
    return f"[{job['id']}: finished, exit code {job.get('exit_code')}]"


def sb_bash(ws: sw.SandboxWork, command: str, timeout: Any, working_dir: str | None, background: bool,
            credentials: Any = None) -> str:
    try:
        timeout_s = int(timeout) if timeout not in (None, '') else 120
    except (TypeError, ValueError):
        return f"Error: Invalid timeout value '{timeout}'. Must be an integer."
    timeout_s = max(1, min(timeout_s, 1800))
    try:
        env = sw.credential_env(sw.credential_names(credentials))
        command = sw.map_command(ws, command)
        working_dir = sw.to_sandbox_path(ws, working_dir)
        out = ws.run(command, working_dir, timeout_s, background, env=env)
    except sw.SandboxWorkError as e:
        return _err(e)
    body = (out.get('output') or '').rstrip('\n')
    if background:
        return (f"Started in the sandbox in the background as {out['id']} (in {out.get('cwd')}).\n"
                f"Read its output with Bash Output (shell_id='{out['id']}') and stop it with Kill Shell.\n\n"
                f"{_job_line(out)}\n{body or '[No output yet]'}")
    parts = [body] if body else []
    if out.get('killed') == 'timeout':
        parts.append(f'[Timed out after {timeout_s}s and was stopped. For servers or watchers, use '
                     'run_in_background=true.]')
    elif out.get('exit_code'):
        parts.append(f"[Exit code {out['exit_code']}]")
    elif not body:
        parts.append('[Command finished with no output]')
    return '\n\n'.join(parts)


def sb_bash_output(ws: sw.SandboxWork, shell_id: str, filter_re: str | None) -> str:
    try:
        if (shell_id or '').strip().lower() == 'list':
            jobs = ws.jobs()
            if not jobs:
                return 'No background commands in the sandbox.'
            return '\n'.join(f"{_job_line(j)} {j.get('command')}" for j in jobs[-30:])
        out = ws.read_job(shell_id, filter_re)
    except sw.SandboxWorkError as e:
        return _err(e)
    body = (out.get('output') or '').rstrip('\n')
    return f"{_job_line(out)}\n{body or '[No new output]'}"


def sb_kill(ws: sw.SandboxWork, shell_id: str) -> str:
    try:
        out = ws.stop_job(shell_id)
    except sw.SandboxWorkError as e:
        return _err(e)
    return f"Stopped {out['id']} in the sandbox." if out.get('killed') else _job_line(out)


def sb_read(ws: sw.SandboxWork, file_path: str, start_line: int, end_line: int | None, numbers: bool) -> str:
    try:
        out = ws.files('read', path=sw.to_sandbox_path(ws, file_path), start=start_line, end=end_line, numbers=numbers)
    except sw.SandboxWorkError as e:
        return _err(e)
    value = f"File: {out['path']} (sandbox)\nLines: {out['start']}-{out['end']} of {out['total']}\n\n{out['text']}"
    if out.get('resume'):
        value += f"\n[Output truncated; continue with start_line={out['resume']}]"
    return value


def sb_write(ws: sw.SandboxWork, file_path: str, content: str, append: bool) -> str:
    try:
        out = ws.files('write', path=sw.to_sandbox_path(ws, file_path), content=content, append=append)
    except sw.SandboxWorkError as e:
        return _err(e)
    return f"{'Appended to' if append else 'Written to'} file: {out['path']} (sandbox)\nSize: {out['size']} bytes"


def sb_edit(ws: sw.SandboxWork, file_path: str, old: str, new: str, replace_all: bool, create: bool) -> str:
    try:
        out = ws.files('edit', path=sw.to_sandbox_path(ws, file_path), old=old, new=new, replace_all=replace_all, create=create)
    except sw.SandboxWorkError as e:
        return _err(e)
    if out.get('created'):
        return f"Created file with content: {out['path']} (sandbox)"
    return f"Replaced {out['count']} occurrence(s) in: {out['path']} (sandbox)"


def sb_glob(ws: sw.SandboxWork, pattern: str, path: str, include_dirs: bool, max_results: int) -> str:
    try:
        out = ws.files('glob', pattern=pattern, path=sw.to_sandbox_path(ws, path), include_dirs=include_dirs, max_results=max_results)
    except sw.SandboxWorkError as e:
        return _err(e)
    if not out['matches']:
        return f"No files matching '{pattern}' in {out['base']} (sandbox)"
    more = f"\n[{out['total'] - len(out['matches'])} more not shown]" if out['total'] > len(out['matches']) else ''
    return f"Found {out['total']} match(es) in {out['base']} (sandbox, newest first):\n" + '\n'.join(out['matches']) + more


def sb_grep(ws: sw.SandboxWork, pattern: str, path: str, include: str | None, exclude: str | None,
            context: int, ignore_case: bool, max_results: int) -> str:
    try:
        out = ws.files('grep', pattern=pattern, path=sw.to_sandbox_path(ws, path), include=include, exclude=exclude, context=context,
                       ignore_case=ignore_case, max_results=max_results)
    except sw.SandboxWorkError as e:
        return _err(e)
    if not out['lines']:
        return f"No matches for '{pattern}' in {out['base']} (sandbox)"
    text = '\n'.join(out['lines'])
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + '\n[Output truncated]'
    more = '\n[More matches not shown; narrow the search]' if out.get('more') else ''
    return f"{out['hits']} match(es) in {out['base']} (sandbox):\n{text}{more}"


def sb_coding_agent(ws: sw.SandboxWork, which: str, run_mode: str, model: str | None, task: str,
                    working_dir: str | None, wait_seconds: Any) -> str:
    from cognitrix.common import coding_agents as ca
    try:
        key = sw.pick_cli(which)
    except ca.CodingAgentError as e:
        return f'Error: {e}'
    cli = ca.CLIS[key]
    argv = ca.build_argv(cli.program, key, run_mode, model)
    command = sw.sandbox_cli_command(key, argv)
    label = f'{cli.label} ({run_mode}): {task.splitlines()[0][:80]}'
    try:
        working_dir = sw.to_sandbox_path(ws, working_dir)
        out = ws.run(command, working_dir, background=True, env=sw.cli_env(key), stdin=task, label=label)
        try:
            wait = max(0, min(int(wait_seconds or 0), ca.MAX_WAIT))
        except (TypeError, ValueError):
            wait = 0
        if wait:
            ws.wait_job(out['id'], wait)
        read = ws.read_job(out['id'])
    except sw.SandboxWorkError as e:
        return _err(e)
    intro = (f"{cli.label} is working in the sandbox in {out.get('cwd')} as {out['id']} (mode: {run_mode}, "
             f"{cli.mode_notes[run_mode]}).")
    # The start call already returned what the CLI printed in its first moments.
    first = (out.get('output') or '') + (read.get('output') or '')
    body = first.rstrip('\n') or '[No output yet]'
    if not read.get('running'):
        return f'{intro}\n\n{_job_line(read)}\n{body}\nSee what it changed with Sandbox Changes or git status.'
    return (f"{intro}\nRead its progress and result with Bash Output (shell_id='{out['id']}') and stop it with "
            f"Kill Shell.\n\n{_job_line(read)}\n{body}")


# ---------------------------------------------------------------------------
# Sandbox-only tools
# ---------------------------------------------------------------------------

def _need_sandbox() -> sw.SandboxWork | ToolOutcome:
    try:
        ws = sw.for_turn()
    except sw.SandboxWorkError as e:
        return ToolOutcome.failure(e.code, e.message)
    if ws is None:
        return ToolOutcome.failure(
            'not_in_sandbox', 'This chat works on this computer, not in the sandbox. Ask the person to turn on '
            'Run in sandbox for the chat (the + menu in the message box) first.')
    return ws


@tool(category='system', retryable=False, max_attempts=1, supported_interfaces=['web', 'ws', 'task'])
def switch_computer_for_code(computer: str = 'person'):
    """Choose where your shell and file tools (Bash, Read, Write, Edit, Glob, Grep, Coding Agent, Run Tests)
    work in this chat. They work in your sandboxed computer by default. Only switch to 'person' when the person
    explicitly asks you to run code or change files on their own computer ("on my computer", "on my laptop").

    Args:
        computer (str): 'person' for the person's own computer (from now on, in this chat), or 'agent' for
            your sandboxed computer again (from the person's next message).

    Returns:
        str: Where the tools work now.
    """
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    which = str(computer or '').strip().lower()
    if which not in ('person', 'agent'):
        return ToolOutcome.failure('bad_computer', "computer is 'person' (the person's computer) or 'agent' (yours).")
    if not ctx.session_id:
        return ToolOutcome.failure('no_chat', 'Only a chat can switch computers.')
    sw.set_chat_switch(ctx.session_id, which == 'agent')
    if which == 'person':
        return ToolOutcome.success(
            "Your shell and file tools now work on the person's computer in this chat, with its usual approvals "
            'and working folders. The person can switch back with Run in sandbox in the + menu.')
    return ToolOutcome.success("From the person's next message, your shell and file tools work in your sandboxed "
                               'computer again.')


@tool(category='system', retryable=False, max_attempts=1)
def copy_into_sandbox(folder: str | None = None, name: str | None = None, replace: bool = False):
    """Copy a folder from the person's computer into your sandbox folder, so you can work on it, run it and
    test it there. Only works when the chat runs in the sandbox.

    Copying the same folder again refreshes the sandbox copy. If the sandbox copy has changes that weren't
    copied back yet, it refuses unless replace=true (which throws those changes away).
    In a git repository only the files git keeps are copied (ignored ones like .env or node_modules stay
    behind), plus the .git folder so git works.

    Args:
        folder (str, optional): The folder on the person's computer: one of the chat's working folders or a
            folder inside one. Defaults to the primary working folder.
        name (str, optional): Name of the copy in the sandbox. Defaults to the folder's name.
        replace (bool, optional): Throw away changes in the sandbox copy that weren't copied back.

    Returns:
        str: Where the copy is in the sandbox.
    """
    from cognitrix.common import folders as folder_paths
    from cognitrix.common.safe_exec import PathEscapesRoot
    from cognitrix.computer.sandbox.workspace import WorkspaceError, project_name

    ws = _need_sandbox()
    if isinstance(ws, ToolOutcome):
        return ws
    try:
        source = folder_paths.resolve_path(folder) if folder else folder_paths.primary_folder()
    except PathEscapesRoot as e:
        return ToolOutcome.failure('outside_folders', str(e))
    if not source.is_dir():
        return ToolOutcome.failure('not_found', f'{folder} is not a folder on this computer.')
    if folder_paths.folder_for(source) is not None and folder_paths.folder_for(source).name == 'scratch':
        return ToolOutcome.failure('scratch', "That's the chat's scratch folder; work in your sandbox folder instead.")
    try:
        label = project_name(name or source.name or 'project')
    except WorkspaceError as e:
        return ToolOutcome.failure(e.code, e.message)
    try:
        out = ws.import_folder(source, label, replace=bool(replace))
    except sw.SandboxWorkError as e:
        return ToolOutcome.failure(e.code, e.message)
    verb = 'Refreshed' if out.get('refreshed') else 'Copied'
    notes = ' '.join(out.get('notes') or [])
    return ToolOutcome.success(
        f"{verb} {out.get('packed', out.get('files'))} files from {source} into the sandbox at {out['path']}. "
        f"{notes} Work there with Bash and the file tools (cd {out['path']}). When you're done, check "
        f"Sandbox Changes (project='{label}') and copy the work back with Copy Back From Sandbox.")


@tool(category='system', retryable=False, max_attempts=1)
def sandbox_changes(project: str, show_diff: bool = True, review: bool = False):
    """Show what changed in a folder you copied into the sandbox since it was copied in (or last copied
    back): the changed files and the diff. The person sees the file list as a card.

    Args:
        project (str): The project name Copy Into Sandbox gave (the folder's name).
        show_diff (bool, optional): Include the full diff, not just the file list. Defaults to true.
        review (bool, optional): Put "Copy back to my computer" and "Keep in the sandbox" buttons on the card,
            for the person to decide.

    Returns:
        str: The changed files and the diff.
    """
    ws = _need_sandbox()
    if isinstance(ws, ToolOutcome):
        return ws
    try:
        out = ws.changes(project, patch=bool(show_diff))
    except sw.SandboxWorkError as e:
        return ToolOutcome.failure(e.code, e.message)
    files = out.get('files') or []
    if not files:
        return ToolOutcome.success(f"No changes in '{project}' since it was copied in (or last copied back).")
    from cognitrix.tools.utils import current_execution_context
    sw.note_reviewed(current_execution_context().session_id, project, out)
    text = f"{len(files)} changed file(s) in {out['path']} (from {out.get('source') or 'the computer'}):\n"
    text += sw.describe_changes(files, 200)
    if show_diff and out.get('patch'):
        text += '\n\n' + out['patch'] + ('\n[Diff cut; read the files for the rest]' if out.get('patch_cut') else '')
    return ToolOutcome.success(text, card=_changes_card(project, out, review=bool(review)))


def _changes_card(project: str, out: dict, review: bool = False) -> dict | None:
    from cognitrix.cards import CardError, normalize
    files = out.get('files') or []
    added = sum(f.get('added') or 0 for f in files)
    removed = sum(f.get('removed') or 0 for f in files)
    rows = [[f['path'], f['status'], 'binary' if f.get('binary') else f"+{f.get('added', 0)} -{f.get('removed', 0)}"]
            for f in files[:50]]
    spec = {
        'title': f'Changes in {project}',
        'subtitle': f"In the sandbox, not yet on your computer ({out.get('source') or ''})"[:140],
        'fields': [{'label': 'Files', 'value': str(len(files))},
                   {'label': 'Lines added', 'value': f'+{added}', 'tone': 'good'},
                   {'label': 'Lines removed', 'value': f'-{removed}', 'tone': 'bad' if removed else 'neutral'}],
        'table': {'columns': ['File', 'Change', 'Lines'], 'rows': rows},
    }
    if review:
        spec['buttons'] = [
            {'label': 'Copy back to my computer', 'action': 'copy_back', 'value': project, 'style': 'primary'},
            {'label': 'Keep in the sandbox', 'action': 'keep_in_sandbox', 'value': project},
        ]
    try:
        return normalize(spec)
    except CardError:
        return None


@tool(category='system', retryable=False, max_attempts=1)
def copy_back_from_sandbox(project: str, files: list[str] | None = None, overwrite: bool = False, *,
                           approved: bool = False):
    """Copy your changes to a project in the sandbox back to the person's computer, into the folder it was
    copied from. The person always approves this first, after seeing which files change.

    Files that also changed on the computer since they were copied in are skipped and listed, unless
    overwrite=true. Call Sandbox Changes first and tell the person what you changed and why.

    Args:
        project (str): The project name Copy Into Sandbox gave.
        files (list[str], optional): Only these paths (relative to the project). Defaults to every change.
        overwrite (bool, optional): Also replace files that changed on the computer meanwhile.

    Returns:
        str: What was copied back, skipped or refused.
    """
    from cognitrix.common import folders as folder_paths
    from cognitrix.common.safe_exec import PathEscapesRoot

    ws = _need_sandbox()
    if isinstance(ws, ToolOutcome):
        return ws
    if not approved:
        return ToolOutcome.failure(
            'approval_required', 'Copying changes back to the computer only runs after the person approves '
            'this call (remembered and automatic approvals don\'t count). Ask them.', denied=True)
    try:
        export = ws.export(project, list(files or []))
    except sw.SandboxWorkError as e:
        return ToolOutcome.failure(e.code, e.message)
    source = export.get('source') or ''
    if not source:
        return ToolOutcome.failure('no_source', f"'{project}' wasn't copied in from the computer, so there's no "
                                   'folder to copy it back to.')
    try:
        target = folder_paths.resolve_path(source, write=True)
    except PathEscapesRoot as e:
        return ToolOutcome.failure('read_only', f'{e} Nothing was copied back.')
    if not export.get('files'):
        unknown = ', '.join(export.get('unknown') or [])
        return ToolOutcome.success(f"Nothing to copy back from '{project}'." + (f' Not changed: {unknown}' if unknown else ''))
    report = sw.apply_export(Path(target), export, overwrite=bool(overwrite))
    if report.applied:
        try:
            ws.synced(project, report.applied)
        except sw.SandboxWorkError:
            pass
    lines = [f'Copied back to {target}: {len(report.written)} file(s) written, {len(report.deleted)} deleted.']
    if report.written:
        lines.append('Written: ' + ', '.join(report.written[:40]) + (' ...' if len(report.written) > 40 else ''))
    if report.deleted:
        lines.append('Deleted: ' + ', '.join(report.deleted[:40]))
    if report.conflicts:
        lines.append('Skipped because they also changed on the computer since they were copied in (copy in '
                     'again to merge, or pass overwrite=true if the person agrees): ' + ', '.join(report.conflicts))
    if report.refused:
        lines.append('Refused (outside the folder or read only): ' + ', '.join(report.refused))
    if export.get('unknown'):
        lines.append('Not changed in the sandbox: ' + ', '.join(export['unknown']))
    return ToolOutcome.success('\n'.join(lines))


def copy_back_details(params: dict) -> str:
    """The approval card's text for Copy Back From Sandbox: which files change."""
    project = str(params.get('project') or '')
    try:
        ws = sw.for_turn()
        if ws is None:
            raise sw.SandboxWorkError('off', '')
        out = ws.changes(project, patch=False)
    except Exception:
        return f"Copies the changes to '{project}' from the sandbox back to your computer"
    files = out.get('files') or []
    wanted = set(params.get('files') or [])
    if wanted:
        files = [f for f in files if f['path'] in wanted]
    where = out.get('source') or 'the folder it came from'
    head = f"Copies {len(files)} changed file(s) from the sandbox into {where} on your computer"
    if params.get('overwrite') in (True, 'true', 1):
        head += ', replacing files that changed there meanwhile'
    return f"{head}:\n{sw.describe_changes(files, 25)}"


@tool(category='system', retryable=False, max_attempts=1)
def run_tests(command: str, working_dir: str | None = None, timeout: int = 600,
              credentials: list[str] | None = None):
    """Run a project's tests in the sandbox and show the person the result as a card: passed, failed and
    skipped counts, the failing tests, and screenshots the tests saved (for example Playwright's).
    Understands pytest, unittest, Jest, Vitest, Mocha, Go, Cargo, RSpec and PHPUnit output; other runners
    are judged by their exit code.

    Install dependencies first (Bash), then run the suite with this. Prefer flags that print a summary
    (pytest prints one by default; for Go use go test -v ./...).

    Args:
        command (str): The test command, e.g. 'pytest -q', 'npm test', 'go test -v ./...'.
        working_dir (str, optional): Where to run it in the sandbox. Defaults to your current folder.
        timeout (int, optional): Seconds before the run is stopped (max 1800). Defaults to 600.
        credentials (list[str], optional): Saved sign-ins (List Credentials) the tests get as environment
            variables: a token as NAME, a username and password as NAME_USERNAME and NAME_PASSWORD. The
            person approves it.

    Returns:
        str: The summary, the failures and the end of the output.
    """
    ws = _need_sandbox()
    if isinstance(ws, ToolOutcome):
        return ws
    try:
        timeout_s = max(10, min(int(timeout or 600), 1800))
    except (TypeError, ValueError):
        timeout_s = 600
    started = time.time()
    try:
        env = sw.credential_env(sw.credential_names(credentials))
        command = sw.map_command(ws, command)
        working_dir = sw.to_sandbox_path(ws, working_dir)
        out = ws.run(command, working_dir, timeout_s, env=env, label=f'{command}  (Run Tests)')
        full = ws.full_output(out['id'])
        shots = ws.files('find', path=out.get('cwd') or '.', since=started - 1,
                         suffixes=['.png', '.jpg', '.jpeg'], limit=2).get('files') or []
    except sw.SandboxWorkError as e:
        return ToolOutcome.failure(e.code, e.message)
    summary = test_results.parse(full)
    timed_out = out.get('killed') == 'timeout'
    images = [img for img in (_thumbnail(s) for s in shots) if img]
    card_spec = test_results.card(summary, command=command, exit_code=out.get('exit_code'), timed_out=timed_out,
                                  seconds=out.get('seconds'), images=images, where=out.get('cwd') or '')
    from cognitrix.cards import CardError, normalize
    try:
        card = normalize(card_spec)
    except CardError:
        card_spec.pop('images', None)
        try:
            card = normalize(card_spec)
        except CardError:
            card = None
    lines = [card_spec['title'] + '.']
    if summary.known:
        lines.append(f'Passed {summary.passed or 0}, failed {summary.failed or 0}, errors {summary.errors or 0}, '
                     f'skipped {summary.skipped or 0} ({summary.framework}).')
    lines.append(f"Exit code: {out.get('exit_code')}" + (' (timed out and stopped)' if timed_out else ''))
    if summary.failures:
        lines.append('Failing tests:\n' + '\n'.join(f'- {n}' + (f': {m}' if m else '') for n, m in summary.failures[:30]))
    if shots:
        lines.append('Screenshots saved during the run: ' + ', '.join(s['path'] for s in shots))
    tail = full[-12_000:] if len(full) > 12_000 else full
    lines.append(('Output (end):\n' if len(full) > 12_000 else 'Output:\n') + tail.rstrip())
    outcome = ToolOutcome.success('\n'.join(lines), card=card)
    if not (summary.known and not summary.broken and out.get('exit_code') == 0) and not (
            not summary.known and out.get('exit_code') == 0):
        outcome.warnings.append('tests failed')
    return outcome


def _thumbnail(shot: dict) -> tuple[str, str] | None:
    """A small JPEG data URL of a screenshot for the card."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(shot['data'])))
        img = img.convert('RGB')
        img.thumbnail((960, 720))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=70, optimize=True)
        data = base64.b64encode(buf.getvalue()).decode()
        if len(data) > 400_000:
            return None
        return f'data:image/jpeg;base64,{data}', Path(shot['path']).name
    except Exception:
        return None


# Hosts for a sign-in saved only for the sandbox (see sandbox_work).
SANDBOX_ONLY_HOST = sw.SANDBOX_ONLY_HOST


@tool(category='system', retryable=False, max_attempts=1, supported_interfaces=['web'],
      occupies_execution_slot=False, approval_mode='assigned_only')
async def ask_for_sign_in(name: str, reason: str, kind: str = 'token', allowed_hosts: list[str] | None = None):
    """Ask the person for a token, API key or username and password you need (to sign in to a website in
    the browser on your computer, to run tests against a real service in the sandbox, or to call an API). They
    type it into a masked field in the chat and it is saved to their Saved sign-ins under `name`; you never see
    the value, only that it was saved. Use this instead of asking them to take over your computer to sign in.

    Use List Credentials first: if a sign-in you can use already exists, use it instead of asking.
    Then type it on the site with type_in_window or set_text ({{secret:NAME.username}},
    {{secret:NAME.password}}, {{secret:NAME}}), or pass the name in Bash or Run Tests `credentials` (sandbox)
    or Http Request `credential`.

    Args:
        name (str): A name like GITHUB_TOKEN or STAGING_DB (letters, numbers, _).
        reason (str): One sentence on what it's for, shown to the person.
        kind (str, optional): 'token' (one secret value, default) or 'basic' (username and password).
        allowed_hosts (list[str], optional): Sites it may be used on: the website you sign in to in your
            browser (like github.com, which also covers its subdomains there) and hosts Http Request may send it
            to (like api.github.com). Leave empty when it's only for commands in the sandbox.

    Returns:
        str: Whether it was saved.
    """
    from cognitrix.credentials import find_row, normalize_host, normalize_name
    from cognitrix.questions.broker import QuestionChannelUnavailable, QuestionTurnCancelled, ask_question
    from cognitrix.questions.models import QuestionSpec, QuestionValidationError
    from cognitrix.tools.utils import current_execution_context

    try:
        clean = normalize_name(name)
    except ValueError as exc:
        return ToolOutcome.failure('bad_name', str(exc))
    kind = 'basic' if str(kind or '').lower() in ('basic', 'password', 'username_password', 'login') else 'token'
    hosts: list[str] = []
    for raw in allowed_hosts or []:
        try:
            host = normalize_host(raw)
        except ValueError as exc:
            return ToolOutcome.failure('bad_host', str(exc))
        if host not in hosts:
            hosts.append(host)
    hosts = hosts[:10] or [SANDBOX_ONLY_HOST]
    owner = current_execution_context().user_id
    existing = await find_row(str(owner) if owner else None, clean)
    what = 'a username and password' if kind == 'basic' else 'a token or key'
    where = ('for commands in the sandbox' if hosts == [SANDBOX_ONLY_HOST]
             else 'for ' + ', '.join(hosts) + ' and commands in the sandbox')
    try:
        spec = QuestionSpec.from_tool_args(
            prompt=f'Share {what}: {clean}',
            details=(f"{str(reason or '').strip()[:500]}\n\nIt's saved to your Saved sign-ins, {where}. "
                     "The agent never sees it." + (' This replaces the one you saved before.' if existing else '')),
            options=[{'id': 'saved', 'label': 'Save'}],
        )
    except QuestionValidationError as exc:
        return ToolOutcome.failure('bad_request', str(exc))
    import dataclasses
    spec = dataclasses.replace(spec, secret={'name': clean, 'kind': kind, 'hosts': hosts,
                                             'description': str(reason or '').strip()[:500],
                                             'replaces': bool(existing)})
    try:
        answer = await ask_question(spec)
    except QuestionChannelUnavailable:
        return ToolOutcome.failure('no_channel', 'Asking for a sign-in needs the person in the web or desktop chat. '
                                   'Tell them to add it in Settings, Saved sign-ins.')
    except QuestionTurnCancelled:
        return ToolOutcome.success(f"The person didn't share {clean}. Carry on without it or ask what to do.")
    if answer.option_id != 'saved':
        return ToolOutcome.success(f"The person didn't share {clean}.")
    saved = await find_row(str(owner) if owner else None, clean)
    if saved is None:
        return ToolOutcome.failure('not_saved', f"{clean} wasn't saved. Ask the person to add it in Settings, Saved sign-ins.")
    if kind == 'basic':
        use = f'{clean}_USERNAME and {clean}_PASSWORD'
    else:
        use = clean
    if kind == 'basic':
        typed = f'{{{{secret:{clean}.username}}}} and {{{{secret:{clean}.password}}}}'
    else:
        typed = f'{{{{secret:{clean}}}}}'
    browser = ('' if hosts == [SANDBOX_ONLY_HOST] else
               f" To sign in on {', '.join(hosts)} in your browser, type {typed} with type_in_window or set_text.")
    return ToolOutcome.success(
        f"Saved {clean}.{browser} In the sandbox pass credentials=['{clean}'] to Bash or Run Tests and read it "
        f"from the variable{'s' if kind == 'basic' else ''} {use}" +
        ('' if hosts == [SANDBOX_ONLY_HOST] else f"; with Http Request use credential='{clean}'") + '.')
