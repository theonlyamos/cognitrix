"""Working folders: which folders an agent may use in a chat or task run.

A chat (Session) holds a list of folders. Each folder is read only or
read & write, and one of them is the primary folder: relative paths and the
shell start there. New chats copy the agent's default folders (``Agent.folders``).

The list for the running turn travels in ``ToolExecutionContext.folders``, so
every file tool and the shell resolve paths through :func:`resolve_path` here.
When a turn has no folder list (CLI, internal calls) the app's working folder
(``settings.tools_root``) is the single read & write folder, as before.
"""

from __future__ import annotations

import contextvars
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognitrix.common.safe_exec import PathEscapesRoot

READ = 'read'
WRITE = 'write'
MAX_FOLDERS = 20


class FolderError(ValueError):
    """A folder can't be added (missing, blocked or not allowed)."""


class FolderReadOnly(PathEscapesRoot):
    """A write was refused because the folder is read only.

    Subclasses PathEscapesRoot so every file tool that already reports path
    refusals reports this one the same way.
    """


@dataclass(frozen=True)
class Folder:
    path: str
    access: str = READ
    name: str = ''
    primary: bool = False

    @property
    def writable(self) -> bool:
        return self.access == WRITE

    @property
    def root(self) -> Path:
        return Path(self.path)

    @property
    def label(self) -> str:
        return self.name or self.root.name or self.path

    def to_dict(self) -> dict[str, Any]:
        return {'path': self.path, 'access': self.access, 'name': self.label, 'primary': self.primary}


# ---------------------------------------------------------------------------
# Blocked and allowed locations
# ---------------------------------------------------------------------------

def _settings():
    from cognitrix.config import settings
    return settings


def workspaces_root() -> Path:
    """Where private scratch folders live (inside the data folder)."""
    return (Path(_settings().workdir) / 'workspaces').resolve()


def blocked_paths() -> list[Path]:
    """Locations that can never be added or touched, even inside an allowed folder."""
    home = Path.home()
    names = [
        '.ssh', '.gnupg', '.aws', '.azure', '.kube', '.docker',
        '.config/gcloud', '.config/gh', '.netrc', '.git-credentials', '.pypirc',
        '.mozilla', '.config/google-chrome', '.config/chromium',
        'Library/Keychains', 'Library/Application Support/Google/Chrome',
        'AppData/Local/Google/Chrome/User Data', 'AppData/Roaming/Mozilla',
        'AppData/Local/Microsoft/Edge/User Data', 'AppData/Roaming/Microsoft/Credentials',
    ]
    paths = [home / n for n in names]
    paths.append(Path(_settings().workdir))  # database, keys, uploads, logs
    if os.name == 'nt':
        windir = os.environ.get('SystemRoot') or 'C:\\Windows'
        paths.append(Path(windir))
    else:
        paths += [Path(p) for p in ('/etc', '/proc', '/sys', '/dev', '/boot', '/var/run', '/run')]
    out = []
    for p in paths:
        try:
            out.append(p.expanduser().resolve())
        except OSError:
            continue
    return out


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def is_blocked(path: Path) -> bool:
    path = Path(os.path.normcase(str(path)))
    scratch = Path(os.path.normcase(str(workspaces_root())))
    if _inside(path, scratch):
        return False
    for blocked in blocked_paths():
        b = Path(os.path.normcase(str(blocked)))
        # Blocked if the path is inside a blocked location, or would contain
        # one (adding the home folder itself is fine: blocked parts stay
        # blocked by the per-path check in resolve_path).
        if _inside(path, b):
            return True
    if os.name != 'nt' and str(path) == '/':
        return True
    if os.name == 'nt' and re.fullmatch(r'[a-z]:\\?', str(path)):
        return True  # a whole drive
    return False


def allowed_roots() -> list[Path] | None:
    """Folders people may add folders from. ``None`` means anywhere not blocked.

    - ``COGNITRIX_ALLOWED_FOLDERS`` (os.pathsep separated) wins when set.
    - The desktop app runs on the person's own machine: anywhere not blocked.
    - A web server: only inside the app's working folder, so one user can't
      point an agent at the server's disk.
    """
    raw = os.environ.get('COGNITRIX_ALLOWED_FOLDERS', '').strip()
    if raw:
        return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    if os.environ.get('COGNITRIX_DESKTOP') == '1':
        return None
    return [Path(_settings().tools_root).expanduser().resolve()]


def validate_folder(path: str, access: str = READ, name: str = '', primary: bool = False) -> Folder:
    """Check one folder a person (or an approved request) wants to add."""
    raw = str(path or '').strip()
    if not raw:
        raise FolderError('Folder path is empty')
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise FolderError(f"'{raw}' is not a full path")
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise FolderError(f"Can't open '{raw}': {exc}") from None
    if not resolved.is_dir():
        raise FolderError(f"'{raw}' is not a folder on this machine")
    if is_blocked(resolved):
        raise FolderError(f"'{raw}' is a protected location and can't be used")
    roots = allowed_roots()
    if roots is not None and not any(_inside(resolved, r) for r in roots):
        raise FolderError(
            f"'{raw}' is outside the folders this server allows "
            f"({', '.join(str(r) for r in roots)})"
        )
    access = WRITE if str(access).lower() in (WRITE, 'readwrite', 'read_write', 'rw') else READ
    label = (name or '').strip()[:60] or resolved.name or str(resolved)
    return Folder(path=str(resolved), access=access, name=label, primary=bool(primary))


def normalize_folders(items: Iterable[Any] | None, *, validate: bool = True) -> list[Folder]:
    """Turn stored or submitted folders into a clean list.

    Drops duplicates, keeps at most MAX_FOLDERS and makes sure exactly one is
    primary. With ``validate`` (API input) each folder is checked and a bad
    one raises FolderError; stored lists skip folders that went missing.
    """
    out: list[Folder] = []
    seen: set[str] = set()
    for item in items or []:
        if isinstance(item, Folder):
            data = item.to_dict()
        elif isinstance(item, dict):
            data = item
        elif isinstance(item, str):
            data = {'path': item}
        else:
            if validate:
                raise FolderError('Each folder needs a path')
            continue
        try:
            folder = validate_folder(
                data.get('path', ''), data.get('access', READ), data.get('name', ''), bool(data.get('primary')),
            )
        except FolderError:
            if validate:
                raise
            continue
        key = os.path.normcase(folder.path)
        if key in seen:
            continue
        seen.add(key)
        out.append(folder)
        if len(out) > MAX_FOLDERS:
            if validate:
                raise FolderError(f'At most {MAX_FOLDERS} folders')
            out = out[:MAX_FOLDERS]
            break
    if out:
        primary_index = next((i for i, f in enumerate(out) if f.primary), 0)
        out = [
            Folder(f.path, f.access, f.name, i == primary_index) for i, f in enumerate(out)
        ]
        # Primary first: it's what relative paths and the shell start from.
        out.sort(key=lambda f: not f.primary)
    return out


def folders_to_dicts(folders: Iterable[Folder]) -> list[dict[str, Any]]:
    return [f.to_dict() for f in folders]


def read_only(folders: Iterable[Folder]) -> tuple[Folder, ...]:
    return tuple(Folder(f.path, READ, f.name, f.primary) for f in folders)


def scratch_folder(key: str) -> Folder:
    """A private read & write folder for one chat or task, made on first use."""
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', str(key or 'default'))[:80] or 'default'
    path = workspaces_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return Folder(path=str(path.resolve()), access=WRITE, name='scratch', primary=True)


def legacy_default_folders() -> tuple[Folder, ...]:
    """The app's working folder, read & write: what every turn used before folders."""
    root = Path(_settings().tools_root).expanduser().resolve()
    return (Folder(path=str(root), access=WRITE, name=root.name or str(root), primary=True),)


# ---------------------------------------------------------------------------
# The running turn's folders
# ---------------------------------------------------------------------------

def active_folders() -> tuple[Folder, ...]:
    from cognitrix.tools.utils import current_execution_context
    folders = current_execution_context().folders
    return tuple(folders) if folders else legacy_default_folders()


def primary_folder() -> Path:
    folders = active_folders()
    return next((f.root for f in folders if f.primary), folders[0].root)


def _norm(path: Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(os.fspath(path))))


def lexical_path(value: str, base: Path | None = None) -> Path:
    candidate = Path(str(value)).expanduser()
    if candidate.drive and not candidate.is_absolute():
        raise PathEscapesRoot(f"Path '{value}' is not absolute")
    joined = candidate if candidate.is_absolute() else (base or primary_folder()) / candidate
    return Path(os.path.abspath(os.fspath(joined)))


def folder_for(path: Path, folders: Iterable[Folder] | None = None) -> Folder | None:
    """The most specific listed folder that holds ``path``."""
    target = _norm(path)
    best: Folder | None = None
    for folder in folders if folders is not None else active_folders():
        root = _norm(folder.root)
        if _inside(target, root) and (best is None or len(str(root)) > len(str(_norm(best.root)))):
            best = folder
    return best


def _names(folders: Iterable[Folder]) -> str:
    return ', '.join(f"{f.label} ({f.path})" for f in folders)


def resolve_path(value: str, *, write: bool = False) -> Path:
    """Resolve a tool's path inside this turn's folders.

    Relative paths start at the primary folder. Symlinks are followed before
    the check, so a link can't lead outside. Writes need a read & write folder.
    """
    folders = active_folders()
    lexical = lexical_path(value)
    folder = folder_for(lexical, folders)
    if folder is None:
        raise PathEscapesRoot(
            f"Path '{value}' is outside the working folders for this chat: {_names(folders)}. "
            "Ask the user to add the folder, or use Request Folder."
        )
    resolved = Path(os.path.realpath(os.fspath(lexical)))
    real_folder = folder_for(resolved, folders)
    if real_folder is None:
        raise PathEscapesRoot(f"Path '{value}' leads outside the working folders for this chat")
    # A folder the admin configured inside a protected place (say a test
    # setup) still works; otherwise protected places stay off limits.
    if is_blocked(resolved) and not is_blocked(real_folder.root):
        raise PathEscapesRoot(f"Path '{value}' is in a protected location")
    if write and not (folder.writable and real_folder.writable):
        ro = folder if not folder.writable else real_folder
        raise FolderReadOnly(
            f"'{ro.label}' is read only in this chat, so '{value}' can't be changed. "
            "Ask the user to make it Read & write, or use Request Folder with access='write'."
        )
    return resolved


def folder_prompt(folders: Iterable[Folder]) -> str:
    folders = list(folders)
    if not folders:
        return ''
    lines = ['## Working folders', 'You can work with files in these folders:']
    for f in folders:
        bits = ['read & write' if f.writable else 'read only']
        if f.primary:
            bits.append('primary')
        lines.append(f"- {f.label}: {f.path} ({', '.join(bits)})")
    lines.append(
        'Relative paths and shell commands start in the primary folder. '
        'Paths outside these folders are refused, and read only folders can\'t be changed. '
        'If you need another folder or write access, ask the user (or use the Request Folder tool if you have it).'
    )
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Granting a folder mid-turn (Request Folder, after approval)
# ---------------------------------------------------------------------------

FolderGrantSink = Callable[[Folder], None]
_grant_sink: contextvars.ContextVar[FolderGrantSink | None] = contextvars.ContextVar(
    'folder_grant_sink', default=None,
)


def set_grant_sink(sink: FolderGrantSink | None):
    return _grant_sink.set(sink)


def reset_grant_sink(token) -> None:
    _grant_sink.reset(token)


def grant_folder(folder: Folder) -> bool:
    """Add or upgrade a folder in the running chat. False when no chat is listening."""
    sink = _grant_sink.get()
    if sink is None:
        return False
    sink(folder)
    return True


def merge_folder(folders: list[Folder], new: Folder) -> list[Folder]:
    """Add ``new``, or upgrade the same folder to write access. Keeps the primary."""
    key = os.path.normcase(new.path)
    out: list[Folder] = []
    found = False
    for f in folders:
        if os.path.normcase(f.path) == key:
            found = True
            access = WRITE if (f.writable or new.writable) else READ
            out.append(Folder(f.path, access, f.name, f.primary))
        else:
            out.append(f)
    if not found:
        out.append(Folder(new.path, new.access, new.name, primary=not out))
    return out
