"""Reaching paired PCs from another process (the task worker).

PCs keep their WebSocket open to the web process, so only that process's hub
has them. A task run in the worker sends its PC calls to the web process over
a loopback HTTP call, signed with the server's own secret.

The web process writes where it listens to ``paired-relay.json`` in the work
folder when a PC connects; ``COGNITRIX_INTERNAL_URL`` overrides it (a worker
on another machine).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger('cognitrix.paired')

HEADER = 'X-Cognitrix-Relay'
PATH = '/api/v1/computer/devices/relay'
MAX_SKEW_S = 120


def _secret() -> bytes:
    from cognitrix.config import settings
    return str(settings.jwt_secret_key).encode()


def _file():
    from cognitrix.config import settings
    return settings.workdir / 'paired-relay.json'


def sign(body: bytes, stamp: str) -> str:
    return hmac.new(_secret(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()


def verify(body: bytes, header: str) -> bool:
    try:
        stamp, mac = str(header or '').split(':', 1)
        if abs(time.time() - float(stamp)) > MAX_SKEW_S:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(sign(body, stamp), mac)


def remember_address(port: int) -> None:
    """The web process: say where workers can reach this hub."""
    try:
        path = _file()
        data = {'url': f'http://127.0.0.1:{int(port)}', 'pid': os.getpid()}
        if path.exists() and json.loads(path.read_text(encoding='utf-8')) == data:
            return
        path.write_text(json.dumps(data), encoding='utf-8')
    except (OSError, ValueError, TypeError):
        logger.debug('Could not save the relay address', exc_info=True)


def address() -> str:
    env = os.environ.get('COGNITRIX_INTERNAL_URL', '').strip().rstrip('/')
    if env:
        return env
    try:
        return str(json.loads(_file().read_text(encoding='utf-8')).get('url') or '')
    except (OSError, ValueError):
        return ''


async def send(op: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any] | None:
    """Ask the web process; None when it can't be reached."""
    import aiohttp

    base = address()
    if not base:
        return None
    body = json.dumps({'op': op, **payload}).encode()
    stamp = f'{time.time():.0f}'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as http, http.post(
                base + PATH, data=body,
                headers={'Content-Type': 'application/json', HEADER: f'{stamp}:{sign(body, stamp)}'}) as resp:
            if resp.status != 200:
                logger.debug('Relay answered %s', resp.status)
                return None
            return await resp.json()
    except Exception:
        logger.debug('Could not reach the web process', exc_info=True)
        return None
