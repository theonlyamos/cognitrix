"""Provider API keys saved from Settings, Models and keys.

Keys live in ``<data folder>/provider_keys.json``, each value sealed with
``secret_box`` and the file readable by its owner only. A file (not a DB row)
because the key is needed in sync code (``_resolve_runtime_config``) and in
every process: the web server, the Celery worker and the desktop engine all
share the data folder.

An environment variable (``<PROVIDER>_API_KEY`` or a key the settings load
from ``.env``) always wins over a saved key, so an operator's config is never
silently replaced from the UI.

Agents keep the key they were resolved with, and ``LLMManager`` caches
resolved LLMs. So when a saved key is replaced or removed, a fingerprint of
the old one is kept under ``_retired``; ``refresh_key`` swaps a retired key
for the current one just before a request goes out. Keys someone typed into
an agent themselves are never in that list, so they are left alone.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from cognitrix.common.secret_box import seal, unseal_or_empty

logger = logging.getLogger('cognitrix.log')

MAX_KEY_LENGTH = 512
MAX_RETIRED = 20
RETIRED = '_retired'

_lock = threading.Lock()
_cache: tuple[int, tuple[dict[str, str], dict[str, set[str]]]] | None = None


def keys_file() -> Path:
    from cognitrix.config import settings

    return Path(settings.workdir) / 'provider_keys.json'


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def _read_file() -> dict:
    path = keys_file()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Couldn't read saved provider keys at %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> tuple[dict[str, str], dict[str, set[str]]]:
    """(saved keys unsealed, retired fingerprints). Re-read only when the file changes."""
    global _cache
    path = keys_file()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = -1
    with _lock:
        if _cache is not None and _cache[0] == mtime:
            return _cache[1]
        data = _read_file()
        keys = {str(p): unseal_or_empty(v) for p, v in data.items() if isinstance(v, str)}
        keys = {p: v for p, v in keys.items() if v}
        retired_raw = data.get(RETIRED) if isinstance(data.get(RETIRED), dict) else {}
        retired = {str(p): {str(h) for h in hs} for p, hs in retired_raw.items() if isinstance(hs, list)}
        _cache = (mtime, (keys, retired))
        return keys, retired


def get_saved_key(provider: str) -> str:
    return _load()[0].get(provider.strip().lower(), '')


def saved_providers() -> dict[str, str]:
    return dict(_load()[0])


def refresh_key(provider: str, api_key: str) -> str:
    """The key to send now: the engine's current key if ``api_key`` is a
    saved key that has since been replaced or removed ('' if nothing replaced
    it), else ``api_key`` unchanged."""
    provider = (provider or '').strip().lower()
    if not api_key:
        return api_key
    retired = _load()[1].get(provider)
    if not retired or _fingerprint(api_key) not in retired:
        return api_key
    # Removed with nothing in its place: stop sending it.
    return env_key(provider) or get_saved_key(provider)


def set_saved_key(provider: str, key: str) -> None:
    """Save ``key`` for ``provider``; an empty key removes it."""
    global _cache
    provider = provider.strip().lower()
    key = key.strip()
    if len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
        raise ValueError('That API key looks wrong.')
    path = keys_file()
    with _lock:
        raw = _read_file()
        old = unseal_or_empty(raw[provider]) if isinstance(raw.get(provider), str) else ''
        if key:
            raw[provider] = seal(key)
        else:
            raw.pop(provider, None)
        retired = raw.get(RETIRED) if isinstance(raw.get(RETIRED), dict) else {}
        hashes = [h for h in retired.get(provider, []) if isinstance(h, str)]
        if old and old != key:
            hashes.append(_fingerprint(old))
        if key:
            # A key saved again is current, not retired.
            hashes = [h for h in hashes if h != _fingerprint(key)]
        if hashes:
            retired[provider] = hashes[-MAX_RETIRED:]
        else:
            retired.pop(provider, None)
        if retired:
            raw[RETIRED] = retired
        else:
            raw.pop(RETIRED, None)
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
