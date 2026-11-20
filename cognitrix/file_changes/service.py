"""A chat's file changes: list them as diffs, accept hunks, put changes back.

Three kinds of source, each with a key:
- ``local``: files Write/Edit changed on the machine Cognitrix runs on.
- ``pc:<device id>``: the same on a paired PC (the PC's file tools do the
  reading and writing).
- ``sandbox:<project>``: a chat folder's copy in the agent's sandbox, read from
  the sandbox's own record of what changed since it was copied in. That copy
  belongs to the agent, so every chat of the agent working on that folder
  sees the same changes.

Putting a change back ("Reject") always goes through the file tools' Edit
with the hunk's own lines, so the rest of the file stays byte for byte.
"""
from __future__ import annotations

import asyncio
import logging
import ntpath
import posixpath
import shlex
from contextlib import asynccontextmanager
from typing import Any

from cognitrix.file_changes import diff as d
from cognitrix.file_changes import record
from cognitrix.file_changes.models import LOCAL, PC, SANDBOX, FileChange, FileReview

logger = logging.getLogger('cognitrix.log')

MAX_FILES = 300


class ChangeError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def _pathmod(path: str):
    return ntpath if ('\\' in path or (len(path) > 1 and path[1] == ':')) else posixpath


def _common_root(paths: list[str]) -> str:
    if not paths:
        return ''
    mod = _pathmod(paths[0])
    try:
        root = mod.commonpath([mod.dirname(p) for p in paths])
    except ValueError:
        return ''
    return root


def _rel(path: str, root: str) -> str:
    if not root:
        return path
    mod = _pathmod(path)
    try:
        return mod.relpath(path, root).replace('\\', '/')
    except ValueError:
        return path


def _file_entry(path: str, full_path: str, status: str, hunks: list[dict], accepted: set[str],
                *, binary: bool = False, too_big: bool = False) -> dict:
    added, removed = d.counts(hunks)
    ids = [h['id'] for h in hunks]
    return {
        'path': path,
        'full_path': full_path,
        'status': status,
        'added': added,
        'removed': removed,
        'binary': binary,
        'too_big': too_big,
        'hunks': hunks,
        'accepted': [i for i in ids if i in accepted],
        'state': 'accepted' if ids and all(i in accepted for i in ids) else 'pending',
    }


async def _reviews(session_id: str) -> dict[str, set[str]]:
    rows = await FileReview.find({'session_id': session_id}) or []
    return {r.key: {x for x in (r.accepted or '').split(',') if x} for r in rows}


async def _recorded_sources(session_id: str, reviews: dict[str, set[str]]) -> list[dict]:
    rows = list(await FileChange.find({'session_id': session_id}) or [])
    groups: dict[str, list[Any]] = {}
    for row in rows:
        key = LOCAL if row.location == LOCAL else f'{PC}:{row.device_id}'
        groups.setdefault(key, []).append(row)
    out = []
    for key, items in groups.items():
        items.sort(key=lambda r: r.path)
        root = _common_root([r.path for r in items])
        files = []
        for row in items[:MAX_FILES]:
            hunks = d.hunks_from_texts(row.path, row.before if row.existed else None, row.after)
            if not hunks:
                continue
            files.append(_file_entry(_rel(row.path, root), row.path, 'modified' if row.existed else 'added',
                                     hunks, reviews.get(f'{key}|{row.path}', set())))
        if not files:
            continue
        device_id = items[0].device_id if items[0].location == PC else ''
        out.append({'key': key, 'kind': PC if device_id else LOCAL, 'device_id': device_id,
                    'label': await _device_name(device_id) if device_id else 'This computer',
                    'root': root, 'files': files})
    return out


async def _device_name(device_id: str) -> str:
    try:
        from cognitrix.models.paired_device import PairedDevice
        row = await PairedDevice.find_one({'id': device_id})
        return (row.name if row else '') or 'Your PC'
    except Exception:
        return 'Your PC'


def _chat_folders(session: Any) -> list[Any]:
    from cognitrix.common.folders import normalize_folders
    try:
        folders = normalize_folders(session.folders or [], validate=False, scratch=session.scratch_path())
    except Exception:
        return []
    return [f for f in folders if getattr(f, 'name', '') != 'scratch']


def _sandbox_work(session: Any):
    from cognitrix.common import sandbox_work as sw
    if not session.agent_id or not sw.sandbox_available():
        return None
    return sw.SandboxWork(session.user_id, session.agent_id)


def _sandbox_projects(ws: Any, session: Any) -> list[str]:
    names = []
    for folder in _chat_folders(session):
        try:
            name = ws.project_for(folder, copy=False)
        except Exception:
            name = None
        if name and name not in names:
            names.append(name)
    return names


def _sandbox_source(ws: Any, name: str, reviews: dict[str, set[str]]) -> dict | None:
    from cognitrix.common import sandbox_work as sw
    try:
        out = ws.changes(name, patch=True)
    except sw.SandboxWorkError:
        return None
    changed = out.get('files') or []
    if not changed:
        return None
    key = f'{SANDBOX}:{name}'
    by_path = d.parse_git_patch(out.get('patch') or '')
    files = []
    for f in changed[:MAX_FILES]:
        path = f['path']
        hunks = by_path.get(path) or []
        files.append(_file_entry(path, path, f.get('status') or 'modified', hunks, reviews.get(f'{key}|{path}', set()),
                                 binary=bool(f.get('binary')), too_big=not hunks and not f.get('binary')))
    return {'key': key, 'kind': SANDBOX, 'project': name, 'label': 'Sandbox copy', 'root': out.get('path') or '',
            'source': out.get('source') or '', 'files': files}


async def chat_changes(session: Any) -> dict:
    """Every change this chat can review, grouped by where it lives."""
    session_id = str(session.id)
    reviews = await _reviews(session_id)
    sources = await _recorded_sources(session_id, reviews)
    ws = _sandbox_work(session)
    if ws is not None:
        def read_sandbox() -> list[dict]:
            got = []
            for name in _sandbox_projects(ws, session):
                src = _sandbox_source(ws, name, reviews)
                if src:
                    got.append(src)
            return got
        try:
            sources = (await asyncio.to_thread(read_sandbox)) + sources
        except Exception:
            logger.debug('Could not read sandbox changes for the changes view', exc_info=True)
    for src in sources:
        src['added'] = sum(f['added'] for f in src['files'])
        src['removed'] = sum(f['removed'] for f in src['files'])
        src['pending'] = sum(1 for f in src['files'] if f['state'] != 'accepted')
    return {'sources': sources}


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------

async def _set_accepted(session_id: str, key: str, ids: set[str]) -> None:
    row = await FileReview.find_one({'session_id': session_id, 'key': key})
    value = ','.join(sorted(ids))
    if row is None:
        if ids:
            await FileReview(session_id=session_id, key=key, accepted=value).save()
    elif ids:
        await FileReview.update_one({'id': row.id}, {'accepted': value})
    else:
        await FileReview.delete_many({'id': row.id})


def _find_file(changes: dict, source: str, path: str) -> tuple[dict, dict]:
    for src in changes['sources']:
        if src['key'] != source:
            continue
        for f in src['files']:
            if f['full_path'] == path or f['path'] == path:
                return src, f
    raise ChangeError('not_found', 'That change is gone. It may have been put back or copied already.', 404)


async def accept(session: Any, source: str, path: str | None, hunk: str | None, on: bool = True) -> None:
    """Mark a hunk, a file, or (no path) every file of a source accepted, or
    take that back."""
    changes = await chat_changes(session)
    if path:
        files = [_find_file(changes, source, path)[1]]
    else:
        src = next((s for s in changes['sources'] if s['key'] == source), None)
        if src is None:
            raise ChangeError('not_found', 'Those changes are gone.', 404)
        files = src['files']
    reviews = await _reviews(str(session.id))
    for f in files:
        key = f'{source}|{f["full_path"]}'
        current = set(reviews.get(key, set()))
        ids = {h['id'] for h in f['hunks']}
        if hunk:
            if hunk not in ids:
                raise ChangeError('not_found', 'That part of the file changed since. Look again.', 404)
            current = current | {hunk} if on else current - {hunk}
        else:
            current = current | ids if on else set()
        await _set_accepted(str(session.id), key, current & ids)


# ---------------------------------------------------------------------------
# Putting back
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _tool_context(session: Any, device_id: str = ''):
    from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context
    folders = tuple(_chat_folders_with_scratch(session))
    ctx = ToolExecutionContext(user_id=str(session.user_id or '') or None, session_id=str(session.id),
                               agent_id=str(session.agent_id or '') or None,
                               task_id=str(getattr(session, 'task_id', '') or '') or None,
                               folders=folders, device=device_id or None)
    token = set_execution_context(ctx)
    try:
        yield ctx
    finally:
        reset_execution_context(token)


def _chat_folders_with_scratch(session: Any) -> list[Any]:
    from cognitrix.common.folders import normalize_folders
    try:
        return list(normalize_folders(session.folders or [], validate=False, scratch=session.scratch_path()))
    except Exception:
        return []


def _check_tool(out: str) -> None:
    if str(out).startswith('Error'):
        raise ChangeError('failed', str(out)[:400].removeprefix('Error: '))


async def _revert_recorded(session: Any, source: str, f: dict, hunks: list[dict]) -> None:
    device_id = source.split(':', 1)[1] if source.startswith(f'{PC}:') else ''
    where = PC if device_id else LOCAL
    row = await FileChange.find_one({'session_id': str(session.id), 'location': where, 'device_id': device_id,
                                     'path': f['full_path']})
    if row is None:
        raise ChangeError('not_found', 'That change is gone.', 404)
    async with _tool_context(session, device_id):
        try:
            snap = await record.read_file(row.path)
        except record.Unreadable as exc:
            raise ChangeError('unreadable', f"Couldn't read the file to put it back ({exc}).") from None
        if snap.text is None:
            raise ChangeError('missing', 'The file is gone, so there is nothing to put back.')
        if snap.text != row.after:
            raise ChangeError('changed', 'The file changed since this was shown. Look again.')
        whole = len(hunks) == len(f['hunks'])
        if whole and not row.existed:
            await _delete_created(row.path, device_id)
            await FileChange.delete_many({'id': row.id})
            return
        text = snap.text
        # Last hunk first, so earlier hunks' lines are where they were.
        for h in sorted(hunks, key=lambda h: h['new_start'], reverse=True):
            old, new = d.sides(h)
            try:
                text_next = d.apply_revert(text, h)
            except ValueError as exc:
                if str(exc) == 'nothing to find' or not new:
                    # The file was emptied: write the old lines back.
                    out = await record.run_file_tool('Write', {'file_path': row.path, 'content': '\n'.join(old)})
                    _check_tool(out)
                    text = '\n'.join(old)
                    continue
                raise ChangeError('changed', "That part of the file couldn't be found exactly once, so it "
                                  'was left alone.') from None
            find, replace = d.revert_edit(h)
            out = await record.run_file_tool('Edit', {'file_path': row.path, 'old_string': find,
                                                      'new_string': replace})
            _check_tool(out)
            text = text_next
        try:
            final = await record.read_file(row.path)
            text = final.text if final.text is not None else text
        except record.Unreadable:
            pass
    if row.existed and text == row.before:
        await FileChange.delete_many({'id': row.id})
    else:
        await FileChange.update_one({'id': row.id}, {'after': text, 'updated': record.now()})


async def _delete_created(path: str, device_id: str) -> None:
    """Remove a file the chat created."""
    if device_id:
        raise ChangeError('cant_delete', "Files the agent created on your PC can't be deleted from here yet. "
                          'Delete it on the PC.')
    import os

    from cognitrix.common import folders as folder_paths
    from cognitrix.common.safe_exec import PathEscapesRoot
    try:
        target = folder_paths.resolve_path(path, write=True)
    except (PathEscapesRoot, Exception) as exc:
        raise ChangeError('refused', str(exc)) from None
    try:
        os.remove(target)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ChangeError('failed', f"Couldn't delete it: {exc}") from None


def _revert_sandbox(ws: Any, src: dict, f: dict, hunks: list[dict]) -> None:
    from cognitrix.common import sandbox_work as sw
    full = posixpath.join(src['root'], f['path'])
    try:
        whole = len(hunks) == len(f['hunks'])
        if f['status'] == 'added' and whole:
            ws.run(f'rm -f -- {shlex.quote(f["path"])}', cwd=src['root'], timeout=30)
            return
        if f['status'] == 'deleted':
            old_lines = [t for h in f['hunks'] for op, t in h['lines'] if op in (' ', '-')]
            ws.files('write', path=full, content='\n'.join(old_lines) + '\n')
            return
        if f['binary'] or f['too_big']:
            raise ChangeError('too_big', "This file can't be put back part by part. Ask the agent to restore it.")
        current = None
        try:
            got = ws.files('read', path=full, numbers=False, limit=2_000_000)
            if not got.get('resume'):
                current = got.get('text') or ''
        except sw.SandboxWorkError:
            current = None
        for h in sorted(hunks, key=lambda h: h['new_start'], reverse=True):
            old, new = d.sides(h)
            if not new:
                ws.files('write', path=full, content='\n'.join(old) + ('\n' if old else ''))
                continue
            find, replace = d.revert_edit(h)
            if current is not None:
                if current.count(find) != 1:
                    raise ChangeError('changed', "That part of the file couldn't be found exactly once, so it "
                                      'was left alone.')
                current = current.replace(find, replace, 1)
            ws.files('edit', path=full, old=find, new=replace)
    except sw.SandboxWorkError as exc:
        raise ChangeError('failed', exc.message) from None


async def reject(session: Any, source: str, path: str | None, hunk: str | None) -> None:
    """Put a hunk, a file, or (no path) every file of a source back."""
    changes = await chat_changes(session)
    src = next((s for s in changes['sources'] if s['key'] == source), None)
    if src is None:
        raise ChangeError('not_found', 'Those changes are gone.', 404)
    if path:
        _, f = _find_file(changes, source, path)
        targets = [(f, [h for h in f['hunks'] if not hunk or h['id'] == hunk])]
        if hunk and not targets[0][1]:
            raise ChangeError('not_found', 'That part of the file changed since. Look again.', 404)
    else:
        targets = [(f, f['hunks']) for f in src['files']]
    errors = []
    for f, hunks in targets:
        try:
            if src['kind'] == SANDBOX:
                ws = _sandbox_work(session)
                if ws is None:
                    raise ChangeError('no_sandbox', 'The sandbox is off.')
                await asyncio.to_thread(_revert_sandbox, ws, src, f, hunks)
            else:
                await _revert_recorded(session, source, f, hunks)
        except ChangeError as exc:
            if path:
                raise
            errors.append(f'{f["path"]}: {exc.message}')
    if errors:
        raise ChangeError('partly', 'Some files were left as they are. ' + ' '.join(errors[:5]))

