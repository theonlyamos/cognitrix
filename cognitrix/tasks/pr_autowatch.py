"""Watch the pull requests agents open, without being asked.

When a tool call in a chat or task run opens a GitHub pull request, the
chat or task that made it starts watching it (``pr_watch``): reviews,
comments, CI results and the merge come back to the same agent, and the
watch stops on its own when the pull request is merged or closed.

A pull request counts as opened by the agent when

- a GitHub "create pull request" tool answered with it, or
- a tool's call or output shows a create command or message (``gh pr
  create``, "Created pull request", ...) next to the pull request's link,
  and GitHub says it was opened in the last few minutes.

That covers the Bash tool, Coding Agent (via Bash Output), Run Code, the
sandbox, a paired PC and GitHub connections alike, because every one of
them comes back through the agent's tool loop (``Agent.call_tools``).

It is on by default. The person can turn it off for their account
(Automations) or for one project (project settings).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta
from typing import Any

from cognitrix.tasks import pr_watch

logger = logging.getLogger('cognitrix.log')

MAX_AGE = timedelta(minutes=30)
"""A pull request found by a create command must be this new."""
MAX_PER_CALL = 3
TIMEOUT_SECONDS = 20

_URL = re.compile(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)')
_CREATE_TOOL = re.compile(
    r'create[_ ]?pull[_ ]?request|create[_ ]pr\b|pull[_ ]?request[_ ]create|open[_ ]pull[_ ]?request',
    re.IGNORECASE)
_CREATE_SIGNAL = re.compile(
    r'\bgh\s+pr\s+create\b'
    r'|\bhub\s+pull-request\b'
    r'|\bpull\s+request\s+(?:was\s+|has\s+been\s+)?(?:created|opened)\b'
    r'|\b(?:created|opened)\s+(?:a\s+|the\s+|new\s+|your\s+)*(?:draft\s+)?(?:pull\s+request|PR)\b',
    re.IGNORECASE)
_SKIP_TOOLS = re.compile(
    r'watch|web[_ ]?(?:search|fetch|read)|fetch[_ ]?url|browse|search|list|get[_ ]|read',
    re.IGNORECASE)
"""Tools that look things up: a pull request in their answer wasn't made by them."""

_in_flight: set[tuple[str, str, int]] = set()

_SHELL_ID = re.compile(r"shell_id='([^']+)'")
BACKGROUND_KEEP_SECONDS = 6 * 3600
MAX_BACKGROUND = 200
_creating_shells: dict[str, float] = {}
"""Background commands that create a pull request (shell id -> when), so the
link Bash Output shows later still counts as opened."""


def _said(args: dict[str, Any], text: str) -> str:
    pieces: list[str] = []
    _strings({k: v for k, v in args.items() if k not in ('parent', 'interface', 'approved')}, pieces)
    return '\n'.join(pieces) + '\n' + (text or '')


def remember_background(args: dict[str, Any] | None, text: str) -> None:
    """A create command started in the background: its link comes later."""
    import time

    args = args if isinstance(args, dict) else {}
    match = _SHELL_ID.search(text or '')
    if not match or not _CREATE_SIGNAL.search(_said(args, '')):
        return
    now = time.monotonic()
    for key, at in list(_creating_shells.items()):
        if now - at > BACKGROUND_KEEP_SECONDS:
            _creating_shells.pop(key, None)
    if len(_creating_shells) >= MAX_BACKGROUND:
        _creating_shells.pop(next(iter(_creating_shells)), None)
    _creating_shells[match.group(1)] = now


def _background_created(args: dict[str, Any]) -> bool:
    shell_id = str(args.get('shell_id') or '')
    return bool(shell_id) and shell_id in _creating_shells


def _strings(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 4 or len(out) > 50:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _strings(item, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings(item, out, depth + 1)


def _refs(text: str) -> list[tuple[str, int]]:
    seen: list[tuple[str, int]] = []
    for match in _URL.finditer(text or ''):
        repo = f'{match.group(1)}/{match.group(2).removesuffix(".git")}'
        ref = (repo, int(match.group(3)))
        if ref not in seen:
            seen.append(ref)
    return seen


def _from_json(text: str, args: dict[str, Any]) -> list[tuple[str, int]]:
    """A create tool that answered with JSON but no link: number + owner/repo."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict) or not str(data.get('number') or '').isdigit():
        return []
    owner, repo = str(args.get('owner') or ''), str(args.get('repo') or '')
    if '/' in repo and not owner:
        owner, repo = repo.split('/', 1)
    if not owner or not repo:
        return []
    return [(f'{owner}/{repo}', int(data['number']))]


def opened_pull_requests(tool_name: str, args: dict[str, Any] | None,
                         text: str) -> tuple[list[tuple[str, int]], bool]:
    """The pull requests a tool call opened, and whether that is certain.

    Certain: a "create pull request" tool. Otherwise only a create command
    or message counts, and the caller checks with GitHub that the pull
    request is new."""
    name = str(tool_name or '')
    args = args if isinstance(args, dict) else {}
    nested = str(args.get('tool_name') or args.get('tool') or '')  # Run Mcp Tool
    if _CREATE_TOOL.search(name) or _CREATE_TOOL.search(nested):
        refs = _refs(text)
        if not refs:
            inner = args.get('arguments') if isinstance(args.get('arguments'), dict) else args
            refs = _from_json(text, inner)
        return refs[:1], True
    if _SKIP_TOOLS.search(name.replace(' ', '_')) and not re.search(r'bash|shell|output', name, re.IGNORECASE):
        return [], False
    if not _CREATE_SIGNAL.search(_said(args, text)) and not _background_created(args):
        return [], False
    return _refs(text)[:MAX_PER_CALL], False


def maybe_opened(tool_name: str, text: str, args: dict[str, Any] | None = None) -> bool:
    """Cheap first look, so most tool calls cost nothing."""
    args = args if isinstance(args, dict) else {}
    nested = str(args.get('tool_name') or args.get('tool') or '')  # Run Mcp Tool
    return (bool(_CREATE_TOOL.search(str(tool_name or ''))) or bool(_CREATE_TOOL.search(nested))
            or _URL.search(text or '') is not None)


async def _enabled(owner: str) -> bool:
    if not await pr_watch.auto_watch_on(owner):
        return False
    try:
        from cognitrix.projects import context as project_context

        project = project_context.get()
    except Exception:
        project = None
    if project is not None:
        from cognitrix.projects.store import project_of

        row = await project_of(project.user_id, project.id)
        if row is not None and not getattr(row, 'watch_prs', True):
            return False
    return True


def _instructions() -> str:
    where = ''
    try:
        from cognitrix.projects import context as project_context

        project = project_context.get()
        if project is not None:
            where = f' while working in the project "{project.name}"'
    except Exception:
        pass
    return (f'You opened this pull request{where}. Read new reviews and comments and act on what they ask; '
            'when checks fail, find out why and fix it on the same branch. Tell the person what changed, '
            'and when it is merged or closed.')


async def _owner() -> str | None:
    from cognitrix.tools.utils import current_execution_context

    ctx = current_execution_context()
    if ctx.task_id:
        from cognitrix.tasks.base import Task

        task = await Task.get(ctx.task_id)
        return str(getattr(task, 'owner_id', '') or ctx.user_id or '') or None if task is not None else None
    if ctx.session_id and ctx.agent_id and ctx.user_id:
        return str(ctx.user_id)
    return None


async def _watch_one(owner: str, repo: str, number: int, certain: bool) -> str:
    """Start watching; returns a line for the agent ('' when nothing to say)."""
    label = f'{repo}#{number}'
    for watch in await pr_watch.PullRequestWatch.find({'owner_id': owner}) or []:
        if (watch.status == pr_watch.WATCHING and watch.repo.lower() == repo.lower()
                and int(watch.number) == number):
            return ''  # already watched somewhere: one set of news is enough
    row = await pr_watch.github_connection(owner)
    if row is None:
        if not certain:
            return ''  # can't ask GitHub whether it is new: say nothing
        return (f'Cognitrix could not start watching {label} for reviews and checks: '
                'GitHub is not connected (Settings > Connected apps).')
    token = await pr_watch.connection_token(row)
    try:
        text = await pr_watch.start_watch(row, token, f'{repo}#{number}', _instructions(),
                                          auto=True, max_age=None if certain else MAX_AGE)
    except pr_watch.NotNew:
        return ''
    first = text.splitlines()[0] if text else ''
    if not first.startswith('Watching'):
        return ''
    return (f'Cognitrix is now watching {label} for you: {pr_watch_speed_line(text)} '
            'Reviews, comments, CI results and the merge come back here as new messages, '
            'and watching stops when it is merged or closed (Stop Watching Pull Request stops it sooner).')


def pr_watch_speed_line(text: str) -> str:
    first = text.splitlines()[0]
    return first.split('". ', 1)[1] if '". ' in first else ''


async def after_tool(tool_name: str, args: dict[str, Any] | None, text: str) -> str:
    """Watch the pull requests a finished tool call opened.

    Returns a note to add to the tool's result ('' for none). Never raises."""
    try:
        remember_background(args, text)
        if not maybe_opened(tool_name, text, args):
            return ''
        refs, certain = opened_pull_requests(tool_name, args, text)
        if not refs:
            return ''
        owner = await _owner()
        if not owner or not await _enabled(owner):
            return ''
        notes = []
        for repo, number in refs:
            key = (owner, repo.lower(), number)
            if key in _in_flight:
                continue
            _in_flight.add(key)
            try:
                note = await asyncio.wait_for(_watch_one(owner, repo, number, certain), TIMEOUT_SECONDS)
            except pr_watch.WatchError as exc:
                note = f'Cognitrix could not start watching {repo}#{number}: {exc}' if certain else ''
            except Exception:
                logger.debug('Automatic watch of %s#%s failed', repo, number, exc_info=True)
                note = ''
            finally:
                _in_flight.discard(key)
            if note:
                notes.append(note)
        return '\n'.join(notes)
    except Exception:
        logger.debug('Could not look for opened pull requests', exc_info=True)
        return ''
