"""Finding and copying files where agents work: this machine, the sandbox,
or a paired PC.

Each place has two operations:

* ``scan``: files changed since a time, as ``Found`` rows (noise filtered by
  the caller with ``kinds.noise``).
* ``fetch``: copy one file into a local path (for the store).

All of these block; callers run them with ``asyncio.to_thread`` except the
PC ones, which talk to the PC over the pairing link and are async.
"""
from __future__ import annotations

import base64
import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognitrix.produced_files import kinds

logger = logging.getLogger('cognitrix.log')

MAX_FOUND = 400
# The PC sends a file in pieces this big (base64 must fit one call's answer).
PC_CHUNK = 140_000

# Calls the server makes to a paired PC (cognitrix/computer/paired/connector.py).
PC_NEW_FILES = '__new_files__'
PC_FILE_BYTES = '__file_bytes__'


class SourceError(Exception):
    """A file couldn't be found or copied (the message is for the person)."""


@dataclass
class Found:
    path: str
    size: int
    mtime: float


# ---------------------------------------------------------------- this machine

def walk_new(roots: list[str], since: float, limit: int = MAX_FOUND) -> list[Found]:
    """Files under ``roots`` changed at or after ``since``, newest first.
    Skips hidden and dependency folders; stops after kinds.MAX_VISITED entries."""
    found: list[Found] = []
    visited = 0
    seen: set[str] = set()
    for base in roots:
        base_real = os.path.realpath(base)
        if not os.path.isdir(base_real) or base_real in seen:
            continue
        seen.add(base_real)
        base_depth = base_real.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(base_real):
            visited += len(dirnames) + len(filenames)
            if visited > kinds.MAX_VISITED:
                break
            depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
            dirnames[:] = [] if depth >= kinds.MAX_DEPTH else [
                d for d in dirnames
                if not kinds.skip_dir(d) and not os.path.islink(os.path.join(dirpath, d))]
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                if not os.path.isfile(full) or os.path.islink(full) or st.st_mtime < since:
                    continue
                found.append(Found(full, st.st_size, st.st_mtime))
    found.sort(key=lambda f: f.mtime, reverse=True)
    return found[:limit]


def server_fetch(path: str, dest: Path, max_bytes: int) -> int:
    src = Path(path)
    if not src.is_file() or src.is_symlink():
        raise SourceError(f'{src.name} was moved or deleted.')
    size = src.stat().st_size
    if size > max_bytes:
        raise SourceError(f'{src.name} is {_mb(size)}, more than the {_mb(max_bytes)} files can be.')
    import shutil
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return size


def _mb(size: int) -> str:
    return f'{size / (1024 * 1024):.0f} MB' if size >= 1024 * 1024 else f'{max(1, size // 1024)} KB'


# ---------------------------------------------------------------- the sandbox

def _q(text: str) -> str:
    return shlex.quote(str(text))


def sandbox_scan(ws: Any, since: float, roots: list[str] | None = None) -> list[Found]:
    """New files in the agent's sandbox folder (and any other given folders)."""
    prune = ' -o '.join(f'-name {_q(d)}' for d in sorted(kinds.SKIP_DIRS))
    places = ' '.join(_q(r) for r in (roots or [ws.root]))
    command = (
        f"find {places} -xdev -mindepth 1 \\( -type d \\( -name '.*' -o {prune} \\) -prune \\) -o "
        f"\\( -type f -newermt '@{int(since)}' -printf '%T@\\t%s\\t%p\\n' \\) 2>/dev/null | head -n {MAX_FOUND * 2}"
    )
    out = ws.run(command, None, 60, label='Cognitrix: files this turn made')
    return parse_find(str(out.get('output') or ''), since)


def parse_find(text: str, since: float) -> list[Found]:
    found = []
    for line in text.splitlines():
        parts = line.split('\t', 2)
        if len(parts) != 3:
            continue
        try:
            mtime, size = float(parts[0]), int(parts[1])
        except ValueError:
            continue
        if mtime >= since:
            found.append(Found(parts[2], size, mtime))
    found.sort(key=lambda f: f.mtime, reverse=True)
    return found[:MAX_FOUND]


def sandbox_stat(ws: Any, path: str) -> Found:
    out = ws.run(f"p=$(realpath -e -- {_q(path)}) && stat -L --printf '%Y\\t%s\\t%n\\n' -- \"$p\"", None, 30, label='Cognitrix: share a file')
    text = str(out.get('output') or '').strip()
    rows = parse_find(text, 0)
    if out.get('exit_code') or not rows:
        raise SourceError(f"There's no file at {path} in the sandbox.")
    return rows[0]


def sandbox_fetch(ws: Any, path: str, dest: Path, max_bytes: int) -> int:
    """Copy a file out of the agent's sandbox folder. The agent's own Linux
    user copies it somewhere the computer's 'agent' user can read, then that
    copy is streamed out and removed."""
    from cognitrix.computer.sandbox.client import get_manager

    found = sandbox_stat(ws, path)
    if found.size > max_bytes:
        raise SourceError(f'{os.path.basename(path)} is {_mb(found.size)}, more than the {_mb(max_bytes)} '
                          'files can be.')
    out = ws.run(f"d=$(mktemp -d /tmp/cognitrix-share.XXXXXX) && chmod 755 \"$d\" && cp -- {_q(found.path)} \"$d/f\" "
                 f"&& chmod 644 \"$d/f\" && echo \"$d\"", None, 300, label='Cognitrix: share a file')
    folder = str(out.get('output') or '').strip().splitlines()[-1:] or ['']
    folder = folder[0].strip()
    if out.get('exit_code') or not folder.startswith('/tmp/cognitrix-share.'):
        raise SourceError(f"Couldn't read {os.path.basename(path)} in the sandbox.")
    try:
        mgr = get_manager()
        box = ws.box
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, 'wb') as fh:
            code, err = mgr.exec_out(box, f'cat -- {_q(folder + "/f")}', fh, timeout=900)
        if code != 0:
            raise SourceError(f"Couldn't copy {os.path.basename(path)} out of the sandbox ({err.strip()[:200]}).")
        return dest.stat().st_size
    finally:
        try:
            ws.run(f'rm -rf -- {_q(folder)}', None, 30, label='Cognitrix: share a file')
        except Exception:
            logger.debug('Could not clean up a shared file copy', exc_info=True)


# ---------------------------------------------------------------- a paired PC

async def pc_scan(device_id: str, ctx: dict[str, Any], since: float) -> list[Found]:
    """New files in the folders the PC shared. Older desktop apps don't know
    this call; then nothing is found."""
    import json

    from cognitrix.computer.paired import FILES
    from cognitrix.computer.paired.hub import DeviceUnavailable, hub
    try:
        text = await hub.call(device_id, PC_NEW_FILES, {'since': since}, power=FILES, ctx=ctx, timeout=60)
    except DeviceUnavailable:
        return []
    if text.startswith('Error'):
        return []
    try:
        rows = json.loads(text).get('files') or []
    except (ValueError, AttributeError):
        return []
    out = []
    for row in rows[:MAX_FOUND]:
        try:
            out.append(Found(str(row['path']), int(row['size']), float(row['mtime'])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def pc_fetch(device_id: str, ctx: dict[str, Any], path: str, dest: Path, max_bytes: int) -> int:
    import json

    from cognitrix.computer.paired import FILES
    from cognitrix.computer.paired.hub import DeviceUnavailable, hub

    dest.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    size = None
    with open(dest, 'wb') as fh:
        while size is None or offset < size:
            try:
                text = await hub.call(device_id, PC_FILE_BYTES, {'path': path, 'offset': offset, 'length': PC_CHUNK},
                                      power=FILES, ctx=ctx, timeout=60)
            except DeviceUnavailable as exc:
                raise SourceError(f"The PC isn't reachable right now ({exc}).") from None
            if text.startswith('Error'):
                message = text[len('Error:'):].strip() if text.startswith('Error:') else text
                if 'not available' in message or "doesn't run" in message:
                    message = 'Update the Cognitrix desktop app on the PC to share files from it.'
                raise SourceError(message)
            try:
                piece = json.loads(text)
                data = base64.b64decode(piece.get('data') or '')
                size = int(piece['size'])
            except (ValueError, KeyError, TypeError):
                raise SourceError("The PC's answer couldn't be read.") from None
            if size > max_bytes:
                raise SourceError(f'{os.path.basename(path)} is {_mb(size)}, more than the {_mb(max_bytes)} '
                                  'a file from a PC can be.')
            if not data and offset < size:
                raise SourceError(f'{os.path.basename(path)} changed while it was copied.')
            fh.write(data)
            offset += len(data)
    return offset


# ---------------------------------------------------------------- run on the PC itself

def pc_new_files(since: float) -> dict[str, Any]:
    """On the PC: files changed since ``since`` in the folders this call may use."""
    from cognitrix.common.folders import active_folders
    roots = [str(f.path) for f in active_folders() if getattr(f, 'writable', True)]
    rows = walk_new(roots, float(since or 0))
    return {'files': [{'path': f.path, 'size': f.size, 'mtime': f.mtime}
                      for f in rows if not kinds.noise(f.path, f.size)]}


def pc_file_bytes(path: str, offset: int, length: int) -> dict[str, Any]:
    """On the PC: one piece of a file inside the shared folders."""
    from cognitrix.common.folders import resolve_path
    real = resolve_path(str(path))
    if not real.is_file():
        raise FileNotFoundError(f'No file at {path}')
    size = real.stat().st_size
    length = max(1, min(int(length or PC_CHUNK), PC_CHUNK))
    with open(real, 'rb') as fh:
        fh.seek(max(0, int(offset or 0)))
        data = fh.read(length)
    return {'size': size, 'data': base64.b64encode(data).decode()}
