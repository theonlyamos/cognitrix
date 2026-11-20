"""Remember what Write and Edit changed, so the person can review and undo it.

Around each Write or Edit on this machine or on a paired PC, the file is read
before (once per chat) and after, through the same file tools, so a PC file is
read on the PC. Changes in the sandbox aren't recorded here: the sandbox's own
copy of the folder already knows them (cognitrix.file_changes.service).

Recording never gets in the way of the tool: any problem just skips it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger('cognitrix.log')

WATCHED_TOOLS = frozenset({'Write', 'Edit'})
MAX_TEXT = 400_000

_HEAD = re.compile(r'^File: (.*)\nLines: \d+-\d+ of \d+\n\n', re.S)
_CUT_MARKS = ('[Output truncated', '[line truncated', ' [line cut]')


class Unreadable(Exception):
    """The file can't be read whole as text (too big, a PDF, an error)."""


@dataclass
class Snapshot:
    path: str
    text: str | None
    """None when the file doesn't exist."""


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')


def tool_text(value: Any) -> str:
    content = getattr(value, 'content', value)
    if isinstance(content, str):
        return content
    return str(content)


async def run_file_tool(name: str, params: dict[str, Any]) -> str:
    """Run Read, Write or Edit where this turn's file tools run: the paired PC
    when the turn is on one, else here (sandbox turns go to the sandbox inside
    the tools themselves)."""
    from cognitrix.computer.paired.turn import run_remote
    from cognitrix.tools.base import ToolManager

    tool = ToolManager.get_by_name(name)
    if tool is None:
        raise Unreadable(f'no {name} tool')
    remote = await run_remote(tool, params)
    if remote is not None:
        return remote
    validated = tool.validate_parameters(dict(params)) if hasattr(tool, 'validate_parameters') else dict(params)
    return tool_text(await tool.run(**validated))


def parse_read(path: str, out: str) -> Snapshot:
    """A Read tool answer (without line numbers) as a snapshot."""
    if out.startswith('Error: File not found'):
        return Snapshot(path, None)
    if re.match(r'^Error: start_line \(1\) is past end of file \(0\)', out):
        return Snapshot(path, '')
    m = _HEAD.match(out)
    if not m:
        raise Unreadable(out[:200])
    body = out[m.end():]
    if any(mark in body for mark in _CUT_MARKS) or len(body) > MAX_TEXT:
        raise Unreadable('too big')
    return Snapshot(m.group(1).strip(), body)


def _on_pc() -> bool:
    from cognitrix.tools.utils import current_execution_context
    return bool(getattr(current_execution_context(), 'device', '') or '')


def _whole_local_text(snap: Snapshot) -> Snapshot:
    """Read's answer drops whether the file ends with a line break, so on
    this machine the file it found is read again whole."""
    if snap.text is None:
        return snap
    try:
        with open(snap.path, encoding='utf-8', errors='replace') as fh:
            text = fh.read(MAX_TEXT + 1)
    except OSError as exc:
        raise Unreadable(str(exc)) from None
    if len(text) > MAX_TEXT:
        raise Unreadable('too big')
    return Snapshot(snap.path, text)


async def read_file(path: str) -> Snapshot:
    out = await run_file_tool('Read', {'file_path': path, 'show_line_numbers': False})
    snap = parse_read(path, out)
    return snap if _on_pc() else _whole_local_text(snap)


@dataclass
class Watch:
    where: str
    device_id: str
    param_path: str
    before: Snapshot | None


def _watched(tool: Any) -> bool:
    if str(getattr(tool, 'name', '') or '') not in WATCHED_TOOLS:
        return False
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    if not ctx.session_id or getattr(ctx, 'device_read_only', False) or getattr(ctx, 'device_host', False):
        return False
    from cognitrix.common.sandbox_work import turn_in_sandbox
    return not turn_in_sandbox()


async def before(tool: Any, params: dict[str, Any]) -> Watch | None:
    """Read the file before a Write or Edit (only the first time this chat
    changes it). None when this call isn't recorded."""
    try:
        if not _watched(tool):
            return None
        from cognitrix.file_changes.models import LOCAL, PC, FileChange
        from cognitrix.tools.utils import current_execution_context
        ctx = current_execution_context()
        path = str(params.get('file_path') or '')
        if not path:
            return None
        device = str(getattr(ctx, 'device', '') or '')
        where = PC if device else LOCAL
        snap = await read_file(path)
        row = await FileChange.find_one({'session_id': str(ctx.session_id), 'location': where,
                                         'device_id': device, 'path': snap.path})
        return Watch(where, device, path, None if row else snap)
    except Unreadable:
        return None
    except Exception:
        logger.debug('Could not read a file before changing it', exc_info=True)
        return None


async def after(watch: Watch | None, ok: bool) -> None:
    """Read the file again and keep the change."""
    if watch is None or not ok:
        return
    try:
        from cognitrix.file_changes.models import FileChange
        from cognitrix.tools.utils import current_execution_context
        ctx = current_execution_context()
        snap = await read_file(watch.param_path)
        if snap.text is None:
            return
        query = {'session_id': str(ctx.session_id), 'location': watch.where, 'device_id': watch.device_id,
                 'path': snap.path}
        row = await FileChange.find_one(query)
        if row is None:
            first = watch.before
            if first is None:
                return
            if first.text == snap.text:
                return
            await FileChange(
                session_id=str(ctx.session_id), user_id=str(ctx.user_id or ''), agent_id=str(ctx.agent_id or ''),
                task_id=str(ctx.task_id or ''), location=watch.where, device_id=watch.device_id, path=snap.path,
                before=first.text or '', existed=first.text is not None, after=snap.text, updated=now(),
            ).save()
            return
        if row.existed and snap.text == row.before:
            await FileChange.delete_many({'id': row.id})
            return
        await FileChange.update_one({'id': row.id}, {'after': snap.text, 'updated': now()})
    except Unreadable:
        return
    except Exception:
        logger.debug('Could not record a file change', exc_info=True)


def succeeded(result_text: str) -> bool:
    return not str(result_text or '').startswith('Error')
