"""AGENTS.md: instructions a folder gives every agent that works in it.

Follows the AGENTS.md convention (https://agents.md) the way Codex reads it:

- In each folder the first of :data:`NAMES` found counts (``AGENTS.override.md``
  beats ``AGENTS.md``; ``CLAUDE.md`` and ``.cursorrules`` are read when a
  folder has no AGENTS.md).
- For a working folder inside a git repository, the files from the repository
  root down to the folder are all used, root first, so the closest one comes
  last and wins where they disagree.
- Files deeper inside a working folder apply to the files below them. The
  agent is told where they are, and the first time a turn reads or changes a
  file under one, that file's instructions come back with the tool result
  (:func:`note_for_path`).
- At most :data:`MAX_BYTES` of instructions go into the prompt (Codex's
  default ``project_doc_max_bytes``).

Only folders the turn may use are read, plus the parent folders up to the
repository root where the install allows those folders (never a blocked
location, never above the home folder).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger('cognitrix.agents_md')

NAMES = ('AGENTS.override.md', 'AGENTS.md', 'CLAUDE.md', '.cursorrules')
MAX_BYTES = 32 * 1024
MAX_FOLDERS = 6
MAX_UP = 8
SCAN_DEPTH = 4
SCAN_DIRS = 2000
MAX_NESTED = 20
SCAN_TTL_S = 60.0
SKIP_DIRS = frozenset({
    'node_modules', '__pycache__', 'venv', '.venv', 'env', 'dist', 'build', 'target', 'out',
    'vendor', 'site-packages', 'coverage', '.next', '.nuxt',
})


@dataclass(frozen=True)
class InstructionFile:
    path: Path
    text: str

    @property
    def directory(self) -> Path:
        return self.path.parent


# ---------------------------------------------------------------- reading files

_file_cache: dict[str, tuple[float, int, str]] = {}
_lock = threading.Lock()


def _read(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file() or st.st_size > MAX_BYTES * 4:
        return None
    key = str(path)
    with _lock:
        hit = _file_cache.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        text = path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return None
    with _lock:
        if len(_file_cache) > 500:
            _file_cache.clear()
        _file_cache[key] = (st.st_mtime, st.st_size, text)
    return text


def file_in(directory: Path) -> InstructionFile | None:
    """The instruction file of one folder (the first of NAMES), or None."""
    for name in NAMES:
        path = directory / name
        text = _read(path)
        if text:
            return InstructionFile(path, text)
    return None


# ---------------------------------------------------------------- which folders

def _norm(path: Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(os.fspath(path))))


def _inside(path: Path, root: Path) -> bool:
    path, root = _norm(path), _norm(root)
    return path == root or root in path.parents


def _may_read_parent(path: Path) -> bool:
    """A parent folder above a working folder may be read (for the repo root's file)."""
    from cognitrix.common.folders import allowed_roots, is_blocked

    if is_blocked(path):
        return False
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    if home is not None and not _inside(path, home):
        return False
    roots = allowed_roots()
    return roots is None or any(_inside(path, r) for r in roots)


def repo_root(folder: Path) -> Path | None:
    """The git repository a folder is in (the folder itself counts), if any."""
    current = folder
    for _ in range(MAX_UP + 1):
        if (current / '.git').exists():
            return current
        if current.parent == current or not _may_read_parent(current.parent):
            return None
        current = current.parent
    return None


def chain_for(folder: Path) -> list[InstructionFile]:
    """Instruction files that apply to a whole working folder: from the
    repository root down to the folder, root first."""
    folder = Path(os.path.abspath(os.fspath(folder)))
    root = repo_root(folder)
    dirs = [folder]
    if root is not None and _norm(root) != _norm(folder):
        current = folder.parent
        while True:
            dirs.append(current)
            if _norm(current) == _norm(root) or current.parent == current:
                break
            current = current.parent
    out = []
    for directory in reversed(dirs):
        found = file_in(directory)
        if found is not None:
            out.append(found)
    return out


_scan_cache: dict[str, tuple[float, tuple[Path, ...]]] = {}


def nested_files(folder: Path) -> tuple[Path, ...]:
    """Instruction files in subfolders of a working folder (closest one applies
    to the files below it). A bounded walk, cached for a minute."""
    key = str(_norm(folder))
    now = time.monotonic()
    with _lock:
        hit = _scan_cache.get(key)
    if hit and now - hit[0] < SCAN_TTL_S:
        return hit[1]
    found: list[Path] = []
    seen = 0
    base_depth = len(Path(folder).parts)
    try:
        for current, dirnames, filenames in os.walk(folder):
            seen += 1
            depth = len(Path(current).parts) - base_depth
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith('.') and d not in SKIP_DIRS and depth < SCAN_DEPTH
            )
            if depth > 0:
                names = set(filenames)
                name = next((n for n in NAMES if n in names), None)
                if name:
                    found.append(Path(current) / name)
            if seen >= SCAN_DIRS or len(found) >= MAX_NESTED:
                break
    except OSError:
        pass
    result = tuple(found)
    with _lock:
        if len(_scan_cache) > 200:
            _scan_cache.clear()
        _scan_cache[key] = (now, result)
    return result


def _host_folders(folders: Iterable[Any]) -> list[Path]:
    out: list[Path] = []
    for f in folders:
        raw = getattr(f, 'path', None) or (f.get('path') if isinstance(f, dict) else None)
        if not raw:
            continue
        path = Path(str(raw))
        if path.is_dir() and all(_norm(path) != _norm(p) for p in out):
            out.append(path)
    return out[:MAX_FOLDERS]


# ---------------------------------------------------------------- the prompt block

INTRO = (
    'These instructions come from AGENTS.md files in the working folders. Follow them for work in '
    'those folders. Where they disagree, the file closest to the files you change wins, and what the '
    'person asks in this chat wins over all of them.'
)


def _same(a: str, b: str) -> bool:
    return ' '.join(a.split()) == ' '.join(b.split())


def instructions_block(folders: Iterable[Any], *, skip_texts: Iterable[str] = ()) -> str:
    """The "## AGENTS.md" block for a turn's working folders ('' when none)."""
    roots = _host_folders(folders)
    if not roots:
        return ''
    skip = [s for s in skip_texts if s and s.strip()]
    files: list[InstructionFile] = []
    seen: set[Path] = set()
    nested: list[Path] = []
    for root in roots:
        for f in chain_for(root):
            if _norm(f.path) in seen or any(_same(f.text, s) for s in skip):
                continue
            seen.add(_norm(f.path))
            files.append(f)
        for path in nested_files(root):
            if _norm(path) not in seen and path not in nested:
                nested.append(path)
    if not files and not nested:
        return ''
    lines = ['## AGENTS.md', INTRO]
    budget = MAX_BYTES
    for f in files:
        text = f.text
        size = len(text.encode('utf-8'))
        if budget <= 0:
            lines += ['', f'### {f.path}', '(Not included: too long. Read it with Read before working there.)']
            continue
        if size > budget:
            text = text.encode('utf-8')[:budget].decode('utf-8', errors='ignore') + \
                '\n[Cut short. Read the whole file with Read.]'
        budget -= size
        lines += ['', f'### {f.path}', text]
    if nested:
        lines += ['', 'These subfolders have their own instructions for the files inside them. '
                      'Before you change files there, read the closest one (it is also shown the first '
                      'time you read or change a file there):']
        lines += [f'- {p}' for p in nested]
    return '\n'.join(lines)


# ---------------------------------------------------------------- per file, on first touch

_shown: OrderedDict[str, set[tuple[str, float]]] = OrderedDict()
MAX_SESSIONS = 500


def _turn_key() -> str:
    try:
        from cognitrix.tools.utils import current_execution_context

        ctx = current_execution_context()
        return str(ctx.session_id or ctx.task_id or ctx.run_id or '')
    except Exception:
        return ''


def note_for_path(path: Path) -> str:
    """Instructions from a subfolder's AGENTS.md the first time a chat reads or
    changes a file under it ('' when there are none or it was shown already).

    Only files below the working folder count: the folder's own file and its
    parents are in the system prompt already.
    """
    key = _turn_key()
    if not key:
        return ''
    try:
        from cognitrix.common.folders import folder_for

        folder = folder_for(Path(os.path.abspath(os.fspath(path))))
        if folder is None:
            return ''
        root = _norm(folder.root)
        directory = _norm(Path(os.path.abspath(os.fspath(path))).parent)
        found: InstructionFile | None = None
        while directory != root and root in directory.parents:
            found = file_in(directory)
            if found is not None:
                break
            directory = directory.parent
        if found is None or _norm(found.path) == _norm(path):
            return ''
        mark = (str(_norm(found.path)), os.stat(found.path).st_mtime)
    except Exception:
        logger.debug('AGENTS.md lookup failed', exc_info=True)
        return ''
    with _lock:
        shown = _shown.setdefault(key, set())
        _shown.move_to_end(key)
        while len(_shown) > MAX_SESSIONS:
            _shown.popitem(last=False)
        if mark in shown:
            return ''
        shown.add(mark)
    text = found.text
    if len(text.encode('utf-8')) > MAX_BYTES:
        text = text.encode('utf-8')[:MAX_BYTES].decode('utf-8', errors='ignore') + '\n[Cut short.]'
    return (f'\n\n[Instructions from {found.path}. They apply to files in {found.directory} '
            f'and win over the ones from parent folders.]\n{text}')


# ---------------------------------------------------------------- for the folder picker

def summary(path: str) -> dict[str, Any]:
    """What a folder's chats will follow: the files in its chain and how many
    subfolders have their own (shown next to the folder in the picker)."""
    folder = Path(str(path))
    if not folder.is_dir():
        return {'files': [], 'nested': 0}
    return {
        'files': [str(f.path) for f in chain_for(folder)],
        'nested': len(nested_files(folder)),
    }


# ---------------------------------------------------------------- paired PCs

PC_CACHE_TTL_S = 300.0
_pc_cache: dict[tuple[str, str], tuple[float, str, str]] = {}


async def pc_instructions(device: dict[str, Any]) -> tuple[str, str]:
    """(path, text) of the AGENTS.md in a paired PC's first shared folder, or
    ('', ''). Read with the PC's own Read tool; cached for five minutes."""
    from cognitrix.computer.paired import FILES

    if not device.get('online') or device.get('paused') or FILES not in set(device.get('powers') or []):
        return '', ''
    folders = list(device.get('folders') or [])
    try:
        from cognitrix.projects.context import narrow_pc_folders
        folders = narrow_pc_folders(folders)
    except Exception:
        pass
    if not folders or not folders[0].get('path'):
        return '', ''
    base = str(folders[0]['path']).rstrip('/\\')
    sep = '\\' if '\\' in base else '/'
    key = (str(device.get('id') or ''), base)
    hit = _pc_cache.get(key)
    if hit and time.monotonic() - hit[0] < PC_CACHE_TTL_S:
        return hit[1], hit[2]
    from cognitrix.computer.paired.hub import hub
    from cognitrix.computer.paired.turn import _project_folders

    found = ('', '')
    for name in NAMES:
        path = f'{base}{sep}{name}'
        try:
            out = await hub.call(
                key[0], 'Read', {'file_path': path, 'show_line_numbers': False},
                power=FILES, ctx=_project_folders(), timeout=8.0)
        except Exception:
            logger.debug('Could not read %s on the PC', name, exc_info=True)
            break  # offline or refused: don't try the other names
        text = str(out or '')
        if not text or text.startswith('Error'):
            continue
        # Read's output starts with "File: ...\nLines: ...\n\n".
        if text.startswith('File:') and '\n\n' in text:
            text = text.split('\n\n', 1)[1]
        text = text.strip()
        if text:
            found = (path, text[:MAX_BYTES])
            break
    _pc_cache[key] = (time.monotonic(), *found)
    return found
