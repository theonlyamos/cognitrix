"""Where copies of produced files are kept, and how much."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from cognitrix.app_settings import get as app_setting
from cognitrix.config import settings


def _mb(name: str, default: int) -> int:
    try:
        return max(1, int(app_setting(name, '') or default)) * 1024 * 1024
    except ValueError:
        return default * 1024 * 1024


def max_file_bytes() -> int:
    """Largest file copied into Cognitrix (video needs room)."""
    return _mb('COGNITRIX_PRODUCED_FILE_MAX_MB', 500)


def max_pc_file_bytes() -> int:
    """Largest file copied over from a paired PC (it comes in small pieces)."""
    return min(max_file_bytes(), _mb('COGNITRIX_PRODUCED_PC_FILE_MAX_MB', 100))


def quota_bytes() -> int:
    """Copies kept per person; the oldest copies go first past this."""
    return _mb('COGNITRIX_PRODUCED_FILES_QUOTA_MB', 5000)


def root() -> Path:
    path = Path(settings.workdir) / 'produced'
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name: str) -> str:
    base = os.path.basename(str(name or '').replace('\\', '/')) or 'file'
    base = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', base).strip(' .') or 'file'
    return base[:180]


def folder_key(owner_id: str, file_id: str) -> str:
    owner = hashlib.sha256(str(owner_id).encode('utf-8')).hexdigest()[:16]
    return f'{owner}/{re.sub(r"[^A-Za-z0-9_-]", "_", str(file_id))}'


def resolve(key: str) -> Path:
    """The path of a storage key, refusing anything outside the store."""
    if not key:
        raise ValueError('No stored copy')
    relative = Path(key)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Bad storage key')
    base = root().resolve()
    path = (base / relative).resolve()
    if base not in path.parents:
        raise ValueError('Bad storage key')
    return path


def remove_folder(owner_id: str, file_id: str) -> None:
    import shutil
    try:
        shutil.rmtree(resolve(folder_key(owner_id, file_id)), ignore_errors=True)
    except ValueError:
        pass
