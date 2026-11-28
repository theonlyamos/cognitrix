"""Tailscale's web API, with the OAuth client saved in Settings > Tailscale.

Only what adding the agents' Fly computers to a tailnet needs:

- ``join_key`` a key a new machine joins with: used once, good for ten
  minutes, already approved, and carrying the computers' tag;
- ``devices`` the tailnet's machines;
- ``remove_device`` take one off the tailnet (a computer that was reset).

The OAuth client is made in Tailscale's admin console (Settings, OAuth
clients) with the **auth keys** scope (and **devices**, to remove a reset
computer) and the computers' tag. Its secret is kept sealed with the other
secret settings and never shown again. The access token it gives lasts an
hour and is kept in memory only.

``tailnet`` is ``-``: the tailnet the OAuth client belongs to.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from cognitrix.app_settings import get as app_setting

API_URL = 'https://api.tailscale.com/api/v2'
KEY_TTL_S = 600

_lock = threading.Lock()
_token: tuple[str, str, float] | None = None  # (client id, access token, good until)
_transport: httpx.BaseTransport | None = None  # tests put a stand-in here


class ApiError(RuntimeError):
    """Words for the person; ``code`` for callers."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def client_id() -> str:
    return (app_setting('COGNITRIX_TAILSCALE_OAUTH_ID', '') or '').strip()


def _secret() -> str:
    return (app_setting('COGNITRIX_TAILSCALE_OAUTH_SECRET', '') or '').strip()


def configured() -> bool:
    return bool(client_id() and _secret())


def forget_token() -> None:
    global _token
    with _lock:
        _token = None


def _http() -> httpx.Client:
    return httpx.Client(timeout=20, transport=_transport)


def _words(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        text = data.get('message') or data.get('error_description') or data.get('error') or ''
    else:
        text = resp.text
    return str(text or f'HTTP {resp.status_code}')[:300]


def access_token() -> str:
    """A token for the saved OAuth client (kept until shortly before it runs out)."""
    global _token
    if not configured():
        raise ApiError('not_configured', 'Save a Tailscale OAuth client in Settings, Tailscale first.')
    ident = client_id()
    with _lock:
        if _token is not None and _token[0] == ident and _token[2] > time.time() + 60:
            return _token[1]
    try:
        with _http() as http:
            resp = http.post(f'{API_URL}/oauth/token', data={'grant_type': 'client_credentials', 'client_id': ident,
                                                             'client_secret': _secret()})
    except httpx.HTTPError as exc:
        raise ApiError('unreachable', f"Couldn't reach Tailscale ({type(exc).__name__}). Try again in a moment.") from None
    if resp.status_code in (400, 401, 403):
        raise ApiError('refused', 'Tailscale refused the OAuth client. Check its ID and secret in Settings, '
                                  f'Tailscale. ({_words(resp)})')
    if resp.status_code >= 400:
        raise ApiError('failed', f"Tailscale couldn't sign the OAuth client in: {_words(resp)}")
    try:
        data = resp.json()
        token = str(data['access_token'])
        lasts = float(data.get('expires_in') or 3600)
    except (ValueError, KeyError, TypeError):
        raise ApiError('failed', "Tailscale's answer wasn't understood.") from None
    with _lock:
        _token = (ident, token, time.time() + lasts)
    return token


def _call(method: str, path: str, body: dict | None = None, ok_missing: bool = False) -> Any:
    token = access_token()
    try:
        with _http() as http:
            resp = http.request(method, f'{API_URL}{path}', json=body, headers={'Authorization': f'Bearer {token}'})
    except httpx.HTTPError as exc:
        raise ApiError('unreachable', f"Couldn't reach Tailscale ({type(exc).__name__}). Try again in a moment.") from None
    if resp.status_code == 404 and ok_missing:
        return None
    if resp.status_code == 401:
        forget_token()
    if resp.status_code in (401, 403):
        raise ApiError('not_allowed', "The Tailscale OAuth client isn't allowed to do that. It needs the auth keys "
                                      f"scope, the devices scope and the computers' tag. ({_words(resp)})")
    if resp.status_code >= 400:
        raise ApiError('failed', f"Tailscale couldn't do that: {_words(resp)}")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def join_key(tag: str, description: str = '') -> str:
    """A key one new machine can join the tailnet with, carrying ``tag``."""
    body = {
        'capabilities': {'devices': {'create': {
            'reusable': False, 'ephemeral': False, 'preauthorized': True, 'tags': [tag]}}},
        'expirySeconds': KEY_TTL_S,
        'description': ''.join(c for c in (description or 'Cognitrix computer')
                               if c.isascii() and (c.isalnum() or c in ' -_'))[:50],
    }
    out = _call('POST', '/tailnet/-/keys', body)
    key = str((out or {}).get('key') or '')
    if not key:
        raise ApiError('failed', "Tailscale didn't give a join key.")
    return key


def devices() -> list[dict[str, Any]]:
    out = _call('GET', '/tailnet/-/devices')
    rows = (out or {}).get('devices')
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def remove_device(device_id: str) -> None:
    if device_id:
        _call('DELETE', f'/device/{device_id}', ok_missing=True)
