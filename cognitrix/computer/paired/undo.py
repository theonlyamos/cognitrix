"""Undo for agents' file edits on a paired PC.

Before an agent's Write or Edit changes a file on the PC, the connector keeps
the file as it was, once per chat and file (the version before the agent
first touched it). The chat can then list what changed and put files back.

Copies live next to the PC's config in ``paired-undo/<chat>/`` and are dropped
after ``KEEP_DAYS``. Files over ``MAX_BYTES`` aren't copied; they're listed as
changed but can't be put back.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

EDIT_TOOLS = ('Write', 'Edit')
CHANGES = '__changes__'  # not tools: the chat asking for its changed files,
UNDO = '__undo__'        # and to put some (or all) of them back
MAX_BYTES = 20 * 1024 * 1024
KEEP_DAYS = 14


def _root() -> Path:
    from cognitrix.computer.paired.connector import config_path
    return config_path().with_name('paired-undo')


def _chat_dir(session_id: str) -> Path:
    key = hashlib.sha256(str(session_id or 'none').encode()).hexdigest()[:24]
    return _root() / key


def _index(folder: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_index(folder: Path, items: list[dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / 'index.tmp'
    tmp.write_text(json.dumps(items, indent=1), encoding='utf-8')
    os.replace(tmp, folder / 'index.json')


def _prune() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for child in _root().iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


def remember(session_id: str, path: Path) -> None:
    """Keep ``path`` as it is now, unless this chat already kept it."""
    folder = _chat_dir(session_id)
    items = _index(folder)
    key = str(path)
    if any(item.get('path') == key for item in items):
        return
    entry: dict[str, Any] = {'path': key, 'at': time.time(), 'existed': path.exists()}
    if entry['existed']:
        if not path.is_file():
            return
        if path.stat().st_size > MAX_BYTES:
            entry['blob'] = None
        else:
            blob = f'{time.time_ns():x}'
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, folder / blob)
            entry['blob'] = blob
    items.append(entry)
    _save_index(folder, items)
    if len(items) == 1:
        _prune()


def _same(path: Path, folder: Path, entry: dict[str, Any]) -> bool:
    """Is the file the same as before the agent touched it?"""
    if not entry.get('existed'):
        return not path.exists()
    blob = entry.get('blob')
    if not blob or not path.is_file():
        return False
    try:
        old = folder / blob
        return old.stat().st_size == path.stat().st_size and old.read_bytes() == path.read_bytes()
    except OSError:
        return False


def changes(session_id: str) -> list[dict[str, Any]]:
    """The files this chat's agents changed and that still differ."""
    folder = _chat_dir(session_id)
    out = []
    for entry in _index(folder):
        path = Path(entry['path'])
        if _same(path, folder, entry):
            continue
        if not entry.get('existed'):
            kind = 'created'
        elif not path.exists():
            kind = 'deleted'
        else:
            kind = 'changed'
        out.append({'path': entry['path'], 'kind': kind, 'at': entry.get('at'),
                    'restorable': not entry.get('existed') or bool(entry.get('blob'))})
    return out


def restore(session_id: str, paths: list[str] | None, check) -> list[dict[str, Any]]:
    """Put files back as they were. ``check(path)`` raises when the PC no longer
    lets agents write there. Returns one result per file."""
    folder = _chat_dir(session_id)
    items = _index(folder)
    wanted = None if paths is None else {str(p) for p in paths}
    keep, results = [], []
    for entry in items:
        if wanted is not None and entry['path'] not in wanted:
            keep.append(entry)
            continue
        path = Path(entry['path'])
        try:
            check(str(path))
            if entry.get('existed'):
                blob = entry.get('blob')
                if not blob:
                    raise OSError('the file was too big to keep a copy')
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(folder / blob, path)
                (folder / blob).unlink(missing_ok=True)
            elif path.exists():
                path.unlink()
            results.append({'path': entry['path'], 'ok': True})
        except Exception as exc:
            keep.append(entry)
            results.append({'path': entry['path'], 'ok': False, 'error': str(exc)})
    if keep:
        _save_index(folder, keep)
    else:
        shutil.rmtree(folder, ignore_errors=True)
    return results
