"""The addresses this Cognitrix can be reached at.

Three kinds, and code asks for the one that fits what it is building:

- ``configured()``   the public address typed in Settings > Advanced
                     (a domain the owner pointed at this server).
- ``private_url()``  the tailnet address (Tailscale Serve): https, reachable
                     only from the owner's tailnet.
- ``funnel_url()``   the public Tailscale address (Tailscale Funnel): https,
                     reachable from the internet, and only for the paths the
                     public door passes (webhooks unless Settings widens it).

The Tailscale ones are read from a small file the web process keeps up to
date (``cognitrix.tailscale.service``), so nothing here runs the Tailscale
client and every process (worker, command line) sees the same answer.
"""
from __future__ import annotations

import json
import threading
from typing import Any
from urllib.parse import urlsplit

from cognitrix.app_settings import get as app_setting

_lock = threading.Lock()
_cache: tuple[int, dict[str, Any]] | None = None


def _file() -> dict[str, Any]:
    global _cache
    from cognitrix import tailscale

    path = tailscale.folder() / 'addresses.json'
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache is not None and _cache[0] == mtime:
            return _cache[1]
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        _cache = (mtime, data)
        return data


def _https(value: Any) -> str:
    text = str(value or '').strip().rstrip('/')
    return text if text.startswith('https://') else ''


def configured() -> str:
    return app_setting('COGNITRIX_PUBLIC_URL', '').strip().rstrip('/')


def private_url() -> str:
    """The tailnet address, '' when it is off or not up yet."""
    from cognitrix import tailscale

    return _https(_file().get('private')) if tailscale.serve_wanted() else ''


def funnel_url() -> str:
    """The public Tailscale address, '' when it is off or not up yet."""
    from cognitrix import tailscale

    return _https(_file().get('public')) if tailscale.funnel_wanted() else ''


def funnel_passes(group: str) -> bool:
    """Whether the public Tailscale address lets a group of paths through."""
    from cognitrix import tailscale

    allow = tailscale.funnel_allow()
    return bool(funnel_url()) and (group in allow or tailscale.ALLOW_APP in allow)


def hooks_url() -> str:
    """Where an outside service (GitHub, Twilio, Meta, Teams) can post to:
    the configured public address, else the public Tailscale address when it
    passes webhooks. '' when there is none; may be http when configured so."""
    from cognitrix import tailscale

    base = configured()
    if base.startswith('https://'):
        return base
    return funnel_url() if funnel_passes(tailscale.ALLOW_WEBHOOKS) else base


def public_links_url() -> str:
    """Where someone outside can open a public link (a shared agent, a
    published page): the configured public address, else the public Tailscale
    address when it passes public links."""
    from cognitrix import tailscale

    return configured() or (funnel_url() if funnel_passes(tailscale.ALLOW_LINKS) else '')


def people_url() -> str:
    """Where a person who uses this server opens it, for links in emails:
    the configured public address, else the tailnet address."""
    return configured() or private_url()


def own_origins() -> list[str]:
    """Every origin (scheme://host[:port]) that is this server."""
    out: list[str] = []
    for url in (configured(), private_url(), funnel_url()):
        parts = urlsplit(url) if url else None
        if parts and parts.scheme and parts.netloc:
            origin = f'{parts.scheme}://{parts.netloc}'
            if origin not in out:
                out.append(origin)
    return out


def view() -> dict[str, Any]:
    """For the app: which address to show for what."""
    from cognitrix import tailscale

    return {
        'configured': configured(),
        'private': private_url(),
        'public': funnel_url(),
        'hooks': hooks_url(),
        'links': public_links_url(),
        'public_passes': sorted(tailscale.funnel_allow()) if funnel_url() else [],
    }
