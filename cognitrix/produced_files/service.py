"""Sharing files, finding the files a turn made, and keeping copies."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cognitrix.produced_files import kinds, previews, sources, storage
from cognitrix.produced_files.models import PC, SANDBOX, SERVER, ProducedFile
from cognitrix.produced_files.sources import SourceError

logger = logging.getLogger('cognitrix.log')

MAX_SHARE = 10
"""Files one Share File call takes."""
MAX_SHELF = 40
"""Files one turn's shelf lists."""
MAX_PICKED = 6
"""When the agent shared nothing, results are shown as cards only when at most this many turned up."""
MAX_ROWS_PER_OWNER = 5000
SCAN_TIMEOUT_S = 45

# Card details sent to the chat (the rest stays for the viewer).
_CARD_INFO = ('width', 'height', 'duration', 'pages', 'rows', 'sheets', 'preview', 'peaks', 'count',
              'unpacked_size', 'first', 'slides', 'titles', 'excerpt')


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')


def ref(row: ProducedFile) -> dict[str, Any]:
    """What a chat card needs to show a file."""
    info = row.info
    card_info = {k: info[k] for k in _CARD_INFO if k in info}
    if isinstance(card_info.get('excerpt'), str):
        card_info['excerpt'] = card_info['excerpt'][:600]
    return {
        'id': str(row.id), 'name': row.name, 'kind': row.kind, 'mime_type': row.mime_type,
        'size': int(row.size_bytes or 0), 'source': row.source, 'shared': bool(row.shared),
        'shown': bool(row.shown), 'caption': row.caption, 'thumb': bool(row.thumb_key), 'info': card_info,
    }


# ---------------------------------------------------------------- storing a copy

async def _store_from(row: ProducedFile, fetch) -> None:
    """Copy the file in with ``fetch(dest_path)``, then make its preview."""
    folder = storage.folder_key(row.owner_id, str(row.id))
    # The copy sits in its own folder so no file name can collide with the thumbnail.
    key = f'{folder}/file/{storage.safe_name(row.name)}'
    dest = storage.resolve(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        size = await fetch(dest)
    except BaseException:
        storage.remove_folder(row.owner_id, str(row.id))
        raise
    info, thumb = await asyncio.to_thread(previews.make, dest, row.kind)
    thumb_key = ''
    if thumb:
        thumb_key = f'{folder}/thumb.jpg'
        await asyncio.to_thread(storage.resolve(thumb_key).write_bytes, thumb)
    fields = {'stored': True, 'storage_key': key, 'thumb_key': thumb_key, 'size_bytes': int(size),
              'info_json': json.dumps(info)}
    await ProducedFile.update_one({'id': str(row.id)}, fields)
    for name, value in fields.items():
        setattr(row, name, value)
    await _keep_within_quota(row.owner_id, keep=str(row.id))


async def _keep_within_quota(owner_id: str, keep: str = '') -> None:
    rows = [r for r in await ProducedFile.find({'owner_id': owner_id}) or [] if r.stored]
    total = sum(int(r.size_bytes or 0) for r in rows)
    limit = storage.quota_bytes()
    if total <= limit:
        return
    rows.sort(key=lambda r: (r.created or '', str(r.id)))
    for r in rows:
        if total <= limit:
            break
        if str(r.id) == keep:
            continue
        storage.remove_folder(owner_id, str(r.id))
        await ProducedFile.update_one({'id': str(r.id)}, {'stored': False, 'storage_key': '', 'thumb_key': ''})
        total -= int(r.size_bytes or 0)


async def _prune_rows(owner_id: str) -> None:
    rows = await ProducedFile.find({'owner_id': owner_id}) or []
    extra = len(rows) - MAX_ROWS_PER_OWNER
    if extra <= 0:
        return
    rows.sort(key=lambda r: (bool(r.shared or r.shown), r.created or '', str(r.id)))
    for r in rows[:extra]:
        storage.remove_folder(owner_id, str(r.id))
        await ProducedFile.delete_many({'id': str(r.id)})


def _fetcher(row: ProducedFile, path: str, *, ws: Any = None, device_ctx: dict[str, Any] | None = None):
    max_bytes = storage.max_file_bytes()
    if row.source == SANDBOX:
        if ws is not None:
            async def fetch_in_turn(dest: Path) -> int:
                return await asyncio.to_thread(sources.sandbox_fetch, ws, path, dest, max_bytes)
            return fetch_in_turn

        async def fetch(dest: Path) -> int:
            # Outside the turn (a later download): pick the sandbox user the
            # way the turn did, which for a project's shared sandbox is the
            # project's user, not the agent's.
            from cognitrix.common.sandbox_work import SandboxWork
            from cognitrix.projects.context import scope_for
            async with scope_for(await _origin(row), row.owner_id):
                box = SandboxWork(row.owner_id, row.agent_id)
                return await asyncio.to_thread(sources.sandbox_fetch, box, path, dest, max_bytes)
        return fetch
    if row.source == PC:
        ctx = device_ctx or {'session_id': row.session_id or None, 'agent_id': row.agent_id or None,
                             'task_id': row.task_id or None}

        async def fetch(dest: Path) -> int:
            return await sources.pc_fetch(row.device_id, ctx, path, dest, storage.max_pc_file_bytes())
        return fetch

    async def fetch(dest: Path) -> int:
        return await asyncio.to_thread(sources.server_fetch, path, dest, max_bytes)
    return fetch


async def _origin(row: ProducedFile) -> Any:
    """The chat (or task) a file came from, enough for its project scope."""
    from types import SimpleNamespace
    if row.session_id:
        try:
            from cognitrix.sessions.base import Session
            session = await Session.get(row.session_id)
            if session is not None:
                return session
        except Exception:
            logger.debug('Could not load the chat a file came from', exc_info=True)
    return SimpleNamespace(user_id=row.owner_id, project_id='', task_id=row.task_id or None)


async def local_path(row: ProducedFile) -> Path:
    """A local copy to serve: the stored copy, the file itself on this
    machine, or a copy fetched now from the sandbox or PC."""
    if row.stored and row.storage_key:
        try:
            path = storage.resolve(row.storage_key)
            if path.is_file():
                return path
        except ValueError:
            pass
    if row.source == SERVER and not row.path:
        raise SourceError(f'{row.name} is no longer kept (older files are removed to save space).')
    if row.source == SERVER:
        path = Path(row.path)
        if path.is_file() and not path.is_symlink():
            if not row.info_json:
                await _describe_in_place(row, path)
            return path
        raise SourceError(f'{row.name} was moved or deleted.')
    await _store_from(row, _fetcher(row, row.path))
    return storage.resolve(row.storage_key)


async def _describe_in_place(row: ProducedFile, path: Path) -> None:
    """Preview details for a file on this machine that isn't copied in."""
    info, thumb = await asyncio.to_thread(previews.make, path, row.kind)
    fields: dict[str, Any] = {'info_json': json.dumps(info)}
    if thumb:
        folder = storage.folder_key(row.owner_id, str(row.id))
        thumb_key = f'{folder}/thumb.jpg'
        target = storage.resolve(thumb_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, thumb)
        fields['thumb_key'] = thumb_key
    await ProducedFile.update_one({'id': str(row.id)}, fields)
    for name, value in fields.items():
        setattr(row, name, value)


# ---------------------------------------------------------------- Share File

def _where() -> tuple[str, Any]:
    """Where this turn's files are: (source, sandbox work or device id)."""
    from cognitrix.common import sandbox_work as sw
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    ws = sw.for_turn()
    if ws is not None:
        return SANDBOX, ws
    if getattr(ctx, 'device', None):
        return PC, str(ctx.device)
    return SERVER, None


def _device_ctx(ctx: Any) -> dict[str, Any]:
    out = {'session_id': ctx.session_id, 'agent_id': ctx.agent_id, 'task_id': ctx.task_id}
    try:
        from cognitrix.computer.paired.turn import _project_folders
        out.update(_project_folders())
    except Exception:
        pass
    return out


async def share(paths: list[str], caption: str = '') -> tuple[list[ProducedFile], list[str]]:
    """Copy files the agent names into Cognitrix and return their rows, plus
    one line per file that couldn't be shared."""
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    if ctx.restricted or not ctx.user_id:
        return [], ['Files can only be shared in a chat or task that belongs to a person.']
    source, where = _where()
    rows: list[ProducedFile] = []
    errors: list[str] = []
    seen: set[str] = set()
    turn_at = _turn_started.get(turn_key(ctx.session_id, ctx.task_id), time.time())
    for raw in [str(p).strip() for p in paths if str(p or '').strip()][:MAX_SHARE]:
        try:
            path, size, mtime = await _locate(source, where, raw, ctx)
        except SourceError as exc:
            errors.append(f'{raw}: {exc}')
            continue
        except Exception as exc:
            errors.append(f'{raw}: {exc}')
            continue
        if path in seen:
            continue
        seen.add(path)
        mime, kind = kinds.kind_of(path)
        row = ProducedFile(
            owner_id=str(ctx.user_id), agent_id=str(ctx.agent_id or ''),
            session_id='' if ctx.task_id else str(ctx.session_id or ''), task_id=str(ctx.task_id or ''),
            run_id=str(ctx.run_id or ''), name=storage.safe_name(os.path.basename(path.replace('\\', '/'))),
            path=path, source=source, device_id=str(where) if source == PC else '', mime_type=mime, kind=kind,
            size_bytes=int(size), mtime=float(mtime), shared=True, shown=True, caption=str(caption or '')[:300],
            turn_at=turn_at, created=now(),
        )
        await row.save()
        try:
            await _store_from(row, _fetcher(row, path, ws=where if source == SANDBOX else None,
                                            device_ctx=_device_ctx(ctx) if source == PC else None))
        except Exception as exc:
            await ProducedFile.delete_many({'id': str(row.id)})
            errors.append(f'{raw}: {exc}')
            continue
        rows.append(row)
    if rows:
        await _prune_rows(str(ctx.user_id))
    return rows, errors


async def share_image(data: bytes, name: str, caption: str = '') -> ProducedFile:
    """Keep an image made in memory (a screenshot) and return its row, shown
    as a card like a shared file."""
    from cognitrix.tools.utils import current_execution_context
    ctx = current_execution_context()
    if ctx.restricted or not ctx.user_id:
        raise SourceError('Screenshots can only be shown in a chat or task that belongs to a person.')
    if len(data) > storage.max_file_bytes():
        raise SourceError('The screenshot is too big to keep.')
    mime, kind = kinds.kind_of(name)
    row = ProducedFile(
        owner_id=str(ctx.user_id), agent_id=str(ctx.agent_id or ''),
        session_id='' if ctx.task_id else str(ctx.session_id or ''), task_id=str(ctx.task_id or ''),
        run_id=str(ctx.run_id or ''), name=storage.safe_name(name), path='', source=SERVER, mime_type=mime,
        kind=kind, size_bytes=len(data), mtime=time.time(), shared=True, shown=True,
        caption=str(caption or '')[:300], turn_at=_turn_started.get(turn_key(ctx.session_id, ctx.task_id), time.time()),
        created=now(),
    )
    await row.save()

    async def write(dest: Path) -> int:
        await asyncio.to_thread(dest.write_bytes, data)
        return len(data)
    try:
        await _store_from(row, write)
    except BaseException:
        await ProducedFile.delete_many({'id': str(row.id)})
        raise
    await _prune_rows(str(ctx.user_id))
    return row


async def _locate(source: str, where: Any, raw: str, ctx: Any) -> tuple[str, int, float]:
    """(full path, size, modified time) of a file the agent named."""
    if source == SANDBOX:
        from cognitrix.common.sandbox_work import to_sandbox_path
        mapped = await asyncio.to_thread(to_sandbox_path, where, raw)
        found = await asyncio.to_thread(sources.sandbox_stat, where, mapped or raw)
        return found.path, found.size, found.mtime
    if source == PC:
        # The PC checks the path against its shared folders when it sends the
        # file; its size comes with the first piece.
        return raw, 0, time.time()
    from cognitrix.common.folders import resolve_path
    try:
        real = resolve_path(raw)
    except Exception as exc:
        raise SourceError(str(exc)) from None
    if not real.is_file():
        raise SourceError('there is no file there.')
    st = real.stat()
    return str(real), st.st_size, st.st_mtime


# ---------------------------------------------------------------- end of a turn

# chat or task id -> when its current turn started (epoch seconds), so
# Share File can mark its files as this turn's.
_turn_started: dict[str, float] = {}


def turn_key(session_id: Any, task_id: Any) -> str:
    return str(task_id or session_id or '')


def turn_started(key: str, at: float) -> None:
    if key:
        _turn_started[key] = at
        if len(_turn_started) > 5000:
            for k in list(_turn_started)[:1000]:
                _turn_started.pop(k, None)


async def files_made(*, owner_id: str, agent_id: str, session_id: str, task_id: str, run_id: str,
                     since: float, folders: list[Any], sandbox_ws: Any = None, device_id: str = '',
                     device_ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The files this turn made, for the shelf under the reply: what the
    agent shared plus what turned up where it worked. When it shared nothing,
    likely results are copied in and shown as cards."""
    if not owner_id:
        return []
    scope = {'task_id': task_id} if task_id else {'session_id': session_id}
    if not task_id and not session_id:
        return []
    shared = [r for r in await ProducedFile.find({'owner_id': owner_id, **scope}) or []
              if r.shared and float(r.turn_at or 0) >= since - 1 and (not run_id or r.run_id == run_id)]
    shared_paths = {r.path for r in shared}
    try:
        found = await asyncio.wait_for(_scan(since, folders, sandbox_ws, device_id, device_ctx or {}),
                                       SCAN_TIMEOUT_S)
    except TimeoutError:
        logger.info('Looking for the files a turn made took too long')
        found = []
    except Exception:
        logger.debug('Could not look for the files a turn made', exc_info=True)
        found = []
    source = SANDBOX if sandbox_ws is not None else PC if device_id else SERVER
    found = [f for f in found if f.path not in shared_paths and not kinds.noise(f.path, f.size)
             and not _inside_store(f.path)][:MAX_SHELF]
    rows: list[ProducedFile] = []
    existing = {r.path: r for r in await ProducedFile.find({'owner_id': owner_id, **scope}) or []
                if not r.shared and r.source == source}
    for f in found:
        mime, kind = kinds.kind_of(f.path)
        row = existing.get(f.path)
        if row is not None:
            fields = {'size_bytes': f.size, 'mtime': f.mtime, 'turn_at': since, 'run_id': run_id}
            if float(row.mtime or 0) != f.mtime and row.stored:
                storage.remove_folder(owner_id, str(row.id))
                fields.update(stored=False, storage_key='', thumb_key='', info_json='')
            await ProducedFile.update_one({'id': str(row.id)}, fields)
            for name, value in fields.items():
                setattr(row, name, value)
        else:
            row = ProducedFile(
                owner_id=owner_id, agent_id=agent_id, session_id='' if task_id else session_id, task_id=task_id,
                run_id=run_id, name=storage.safe_name(os.path.basename(f.path.replace('\\', '/'))), path=f.path,
                source=source, device_id=device_id if source == PC else '', mime_type=mime, kind=kind,
                size_bytes=f.size, mtime=f.mtime, turn_at=since, created=now(),
            )
            await row.save()
        rows.append(row)
    if not shared:
        picked = [r for r in rows if kinds.looks_like_result(r.name, int(r.size_bytes or 0))]
        if 0 < len(picked) <= MAX_PICKED:
            for row in picked:
                try:
                    if source == SERVER:
                        await _describe_in_place(row, Path(row.path))
                    else:
                        await _store_from(row, _fetcher(row, row.path, ws=sandbox_ws, device_ctx=device_ctx))
                except Exception:
                    logger.debug('Could not copy in a file the turn made', exc_info=True)
                    continue
                await ProducedFile.update_one({'id': str(row.id)}, {'shown': True})
                row.shown = True
    if rows:
        await _prune_rows(owner_id)
    return [ref(r) for r in shared] + [ref(r) for r in rows]


def _inside_store(path: str) -> bool:
    try:
        base = str(storage.root().resolve())
    except OSError:
        return False
    return os.path.realpath(path).startswith(base + os.sep)


async def _scan(since: float, folders: list[Any], sandbox_ws: Any, device_id: str,
                device_ctx: dict[str, Any]) -> list[sources.Found]:
    if sandbox_ws is not None:
        return await asyncio.to_thread(sources.sandbox_scan, sandbox_ws, since)
    if device_id:
        return await sources.pc_scan(device_id, device_ctx, since)
    roots = [str(getattr(f, 'path', f)) for f in folders
             if getattr(f, 'writable', True)]
    return await asyncio.to_thread(sources.walk_new, roots, since)


# ---------------------------------------------------------------- listing

async def for_chat(owner_id: str, *, session_id: str = '', task_id: str = '') -> list[ProducedFile]:
    query: dict[str, Any] = {'owner_id': owner_id}
    if task_id:
        query['task_id'] = task_id
    elif session_id:
        query['session_id'] = session_id
    rows = await ProducedFile.find(query) or []
    rows.sort(key=lambda r: (r.created or '', str(r.id)), reverse=True)
    return rows


async def forget(row: ProducedFile) -> None:
    storage.remove_folder(row.owner_id, str(row.id))
    await ProducedFile.delete_many({'id': str(row.id)})
