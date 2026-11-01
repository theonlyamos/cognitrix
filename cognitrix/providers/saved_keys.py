"""Provider API keys saved from Settings, Models and keys.

Keys live in ``<data folder>/provider_keys.json``, each value sealed with
``secret_box`` and the file readable by its owner only. A file (not a DB row)
because the key is needed in sync code (``_resolve_runtime_config``) and in
every process: the web server, the Celery worker and the desktop engine all
share the data folder.

An environment variable (``<PROVIDER>_API_KEY`` or a key the settings load
from ``.env``) always wins over a saved key, so an operator's config is never
silently replaced from the UI.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from cognitrix.common.secret_box import seal, unseal_or_empty

logger = logging.getLogger('cognitrix.log')

MAX_KEY_LENGTH = 512

_lock = threading.Lock()
_cache: tuple[float, dict[str, str]] | None = None


def keys_file() -> Path:
    from cognitrix.config import settings

    return Path(settings.workdir) / 'provider_keys.json'


def _read_raw() -> dict[str, str]:
    path = keys_file()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Couldn't read saved provider keys at %s: %s", path, exc)
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _all() -> dict[str, str]:
    """Every saved key, unsealed. Re-read only when the file changes."""
    global _cache
    path = keys_file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    with _lock:
        if _cache is not None and _cache[0] == mtime:
            return _cache[1]
        keys = {p: unseal_or_empty(v) for p, v in _read_raw().items()}
        keys = {p: v for p, v in keys.items() if v}
        _cache = (mtime, keys)
        return keys


def get_saved_key(provider: str) -> str:
    return _all().get(provider.strip().lower(), '')


def saved_providers() -> dict[str, str]:
    return dict(_all())


def set_saved_key(provider: str, key: str) -> None:
    """Save ``key`` for ``provider``; an empty key removes it."""
    global _cache
    provider = provider.strip().lower()
    key = key.strip()
    if len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
        raise ValueError('That API key looks wrong.')
    path = keys_file()
    with _lock:
        raw = _read_raw()
        if key:
            raw[provider] = seal(key)
        else:
            raw.pop(provider, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.provider_keys.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(raw, fh, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _cache = None


def env_key(provider: str) -> str:
    """The key the engine's environment or .env gives ``provider``, if any."""
    from cognitrix.config import settings

    value = os.getenv(f'{provider.upper()}_API_KEY', '')
    if value:
        return value
    try:
        return settings.get_api_key(provider) or ''
    except Exception:
        return ''
