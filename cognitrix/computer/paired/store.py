"""Pairing codes and paired PC records (server side).

A pairing code is made on the web by a signed-in person, lives 10 minutes in
this process, and is used once: the PC shows the person's name from it, then
sends it back with the folders and powers its owner chose and gets a token.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cognitrix.computer.paired import (
    CODE_TTL_S,
    IDLE_DAYS,
    clean_folders,
    clean_powers,
    hash_token,
    new_code,
    new_token,
    normalize_code,
)

FMT = '%Y-%m-%d %H:%M:%S'
MAX_CODES_PER_USER = 5
MAX_DEVICES_PER_USER = 20
# Wrong or used codes one address may try before it has to wait.
CLAIM_TRIES = 20
CLAIM_WINDOW_S = 600


class PairingError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class PairCode:
    code: str
    user_id: str
    account: str  # "Name · email", shown on the PC
    expires: float
    device_id: str | None = None


_codes: dict[str, PairCode] = {}
_tries: dict[str, list[float]] = {}
_lock = threading.Lock()


def now_stamp(when: datetime | None = None) -> str:
    return (when or datetime.now(UTC)).replace(tzinfo=None).strftime(FMT)


def _parse(stamp: Any) -> datetime | None:
    try:
        return datetime.strptime(str(stamp)[:19].replace('T', ' '), FMT)
    except (TypeError, ValueError):
        return None


def _prune(now: float) -> None:
    for key, item in list(_codes.items()):
        if item.expires < now - 3600:  # kept an hour past expiry for the web's status check
            _codes.pop(key, None)


def make_code(user_id: str, account: str) -> PairCode:
    now = time.time()
    with _lock:
        _prune(now)
        mine = [c for c in _codes.values() if c.user_id == user_id and c.expires > now and not c.device_id]
        for old in sorted(mine, key=lambda c: c.expires)[:max(0, len(mine) - MAX_CODES_PER_USER + 1)]:
            _codes.pop(old.code, None)
        code = new_code()
        while code in _codes:
            code = new_code()
        item = PairCode(code=code, user_id=user_id, account=account, expires=now + CODE_TTL_S)
        _codes[code] = item
        return item


def code_status(user_id: str, code: str) -> dict[str, Any]:
    item = _codes.get(normalize_code(code))
    if item is None or item.user_id != user_id:
        return {'status': 'expired'}
    if item.device_id:
        return {'status': 'paired', 'device_id': item.device_id}
    if item.expires < time.time():
        return {'status': 'expired'}
    return {'status': 'waiting', 'expires_in': int(item.expires - time.time())}


def _count_try(address: str) -> None:
    now = time.time()
    with _lock:
        recent = [t for t in _tries.get(address, []) if t > now - CLAIM_WINDOW_S]
        if len(recent) >= CLAIM_TRIES:
            raise PairingError('too_many', 'Too many pairing tries from this address. Wait 10 minutes.', 429)
        recent.append(now)
        _tries[address] = recent


def _live(code: str, address: str) -> PairCode:
    _count_try(address)
    item = _codes.get(normalize_code(code))
    if item is None or item.expires < time.time():
        raise PairingError('bad_code', "That code isn't right or has expired. Make a new one on the web.", 404)
    if item.device_id:
        raise PairingError('used_code', 'That code was already used. Make a new one on the web.', 409)
    return item


def claim(code: str, address: str) -> dict[str, Any]:
    """What the PC shows before its owner allows anything: whose account."""
    item = _live(code, address)
    return {'account': item.account, 'expires_in': int(item.expires - time.time())}


async def pair(code: str, address: str, *, name: str, platform: str, folders: Any, powers: Any,
               version: str = '') -> tuple[Any, str]:
    """Use the code: make the device record and return (device, token)."""
    from cognitrix.models.paired_device import PairedDevice

    item = _live(code, address)
    with _lock:
        if item.device_id:
            raise PairingError('used_code', 'That code was already used. Make a new one on the web.', 409)
        item.device_id = 'pending'
    try:
        existing = await PairedDevice.find({'user_id': item.user_id}) or []
        if len(existing) >= MAX_DEVICES_PER_USER:
            raise PairingError('too_many_devices', f'This account already has {MAX_DEVICES_PER_USER} paired PCs. '
                               'Unpair one on the web first.', 409)
        token = new_token()
        stamp = now_stamp()
        device = PairedDevice(
            user_id=item.user_id,
            name=(str(name or '').strip() or 'My PC')[:80],
            platform=str(platform or '')[:80],
            token_hash=hash_token(token),
            folders_json=json.dumps(clean_folders(folders)),
            powers=','.join(clean_powers(powers)),
            version=str(version or '')[:40],
            paired_at=stamp,
            last_seen_at=stamp,
        )
        await device.save()
    except BaseException:
        item.device_id = None
        raise
    item.device_id = str(device.id)
    return device, token


async def device_for_token(token: str):
    """The device a PC's token belongs to, or None (unknown, or idle too long)."""
    from cognitrix.models.paired_device import PairedDevice

    if not token or not str(token).startswith('cgxd_'):
        return None
    device = await PairedDevice.find_one({'token_hash': hash_token(token)})
    if device is None:
        return None
    seen = _parse(device.last_seen_at)
    if seen is not None and seen < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=IDLE_DAYS):
        return None
    return device


async def devices_of(user_id: str) -> list[Any]:
    from cognitrix.models.paired_device import PairedDevice

    rows = await PairedDevice.find({'user_id': str(user_id)}) or []
    return sorted(rows, key=lambda d: str(d.paired_at or ''))


async def device_of(user_id: Any, device_id: Any):
    """The person's own device by id, or None."""
    from cognitrix.models.paired_device import PairedDevice

    if not user_id or not device_id:
        return None
    device = await PairedDevice.find_one({'id': str(device_id)})
    if device is None or str(device.user_id) != str(user_id):
        return None
    return device


def folders_of(device: Any) -> list[dict[str, Any]]:
    try:
        return clean_folders(json.loads(device.folders_json or '[]'))
    except (TypeError, ValueError):
        return []


def powers_of(device: Any) -> list[str]:
    return clean_powers(str(device.powers or '').split(','))


def view(device: Any) -> dict[str, Any]:
    """A device as the web shows it, with live details while it's online."""
    from cognitrix.computer.paired.hub import hub

    conn = hub.get(device.id)
    return {
        'id': str(device.id),
        'name': device.name,
        'platform': device.platform,
        'online': conn is not None,
        'paused': conn.paused if conn is not None else bool(device.paused),
        'folders': conn.folders if conn is not None else folders_of(device),
        'powers': conn.powers if conn is not None else powers_of(device),
        'version': device.version,
        'paired_at': device.paired_at,
        'last_seen_at': now_stamp() if conn is not None else device.last_seen_at,
    }
