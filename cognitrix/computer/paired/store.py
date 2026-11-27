"""Pairing codes, pairing requests and paired PC records (server side).

A pairing code is made on the web by a signed-in person, lives 10 minutes in
this process, and is used once: the PC shows the person's name from it, then
sends it back with the folders and powers its owner chose and gets a token.

A pairing request goes the other way and needs no code. A PC on the tailnet
asks through the Tailscale private address, where Tailscale says which login
it is. When that login is linked to an account (Settings > Sign-ins), the
request shows on that account's Computers page; once approved there, the PC
sends its folders and powers and gets its token. Requests live 10 minutes too.
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
MAX_REQUESTS_PER_USER = 5
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


@dataclass
class PairRequest:
    """A PC on the tailnet asking to pair, waiting for its owner's yes on the web."""
    id: str
    secret_hash: str    # only the PC that asked has the secret
    user_id: str
    account: str
    login: str          # the Tailscale login the PC is signed in as
    node: str           # its machine name on the tailnet, '' when unknown
    address: str
    name: str
    platform: str
    client: str
    expires: float
    status: str = 'pending'   # pending | approved | denied | used
    approved_by_sid: str = ''


_codes: dict[str, PairCode] = {}
_requests: dict[str, PairRequest] = {}
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


def _kind(client: str) -> str:
    return 'desktop' if client == 'desktop' else 'cli'


def _pc_label(name: str, client: str) -> str:
    return f"{name} ({'desktop app' if client == 'desktop' else 'cognitrix connect'})"[:80]


async def _make_device(user_id: str, *, name: str, platform: str, folders: Any, powers: Any,
                       version: str, sign_in_sid: str, tailnet_node: str = '') -> tuple[Any, str]:
    from cognitrix.models.paired_device import PairedDevice

    existing = await PairedDevice.find({'user_id': user_id}) or []
    if len(existing) >= MAX_DEVICES_PER_USER:
        raise PairingError('too_many_devices', f'This account already has {MAX_DEVICES_PER_USER} paired PCs. '
                           'Unpair one on the web first.', 409)
    token = new_token()
    stamp = now_stamp()
    device = PairedDevice(
        user_id=user_id,
        name=(str(name or '').strip() or 'My PC')[:80],
        platform=str(platform or '')[:80],
        token_hash=hash_token(token),
        folders_json=json.dumps(clean_folders(folders)),
        powers=','.join(clean_powers(powers)),
        version=str(version or '')[:40],
        paired_at=stamp,
        last_seen_at=stamp,
        sign_in_sid=sign_in_sid,
        tailnet_node=str(tailnet_node or '')[:80],
    )
    await device.save()
    return device, token


async def pair(code: str, address: str, *, name: str, platform: str, folders: Any, powers: Any,
               version: str = '', client: str = '', tailnet_node: str = '') -> tuple[Any, str]:
    """Use the code: make the device record and return (device, token).

    The PC also shows in Settings, Sign-ins as a linked device, so it can be
    signed out (unpaired) from there like any other device."""
    from types import SimpleNamespace

    from cognitrix import signins

    item = _live(code, address)
    with _lock:
        if item.device_id:
            raise PairingError('used_code', 'That code was already used. Make a new one on the web.', 409)
        item.device_id = 'pending'
    try:
        pc_name = (str(name or '').strip() or 'My PC')[:80]
        sid = await signins.start(SimpleNamespace(id=item.user_id), method='pairing', address=address,
                                  name=_pc_label(pc_name, client), kind=_kind(client))
        device, token = await _make_device(item.user_id, name=pc_name, platform=platform, folders=folders,
                                           powers=powers, version=version, sign_in_sid=sid, tailnet_node=tailnet_node)
    except BaseException:
        item.device_id = None
        raise
    item.device_id = str(device.id)
    return device, token


async def pair_with_sign_in_code(code: str, address: str, *, name: str, platform: str, folders: Any, powers: Any,
                                 version: str = '', client: str = '', tailnet_node: str = '') -> tuple[Any, str]:
    """A code from Settings, Sign-ins signs the PC in and pairs it in one go."""
    from cognitrix import signins

    pc_name = (str(name or '').strip() or 'My PC')[:80]
    try:
        user, sid = await signins.redeem_code(code, address=address, name=_pc_label(pc_name, client),
                                              kind=_kind(client))
    except signins.SignInError as exc:
        raise PairingError(exc.code, exc.message, exc.status) from None
    try:
        return await _make_device(str(user.id), name=pc_name, platform=platform, folders=folders, powers=powers,
                                  version=version, sign_in_sid=sid, tailnet_node=tailnet_node)
    except BaseException:
        await signins.end(str(user.id), sid)
        raise


# ---------------------------------------------------------------------------
# Pairing without a code: a PC on the tailnet asks, its owner says yes on the web
# ---------------------------------------------------------------------------

def _request_view(item: PairRequest) -> dict[str, Any]:
    return {'id': item.id, 'name': item.name, 'platform': item.platform, 'login': item.login, 'node': item.node,
            'address': item.address, 'client': item.client, 'status': item.status,
            'expires_in': max(0, int(item.expires - time.time()))}


def _prune_requests(now: float) -> None:
    for key in [k for k, r in _requests.items() if r.expires < now]:
        _requests.pop(key, None)


def make_request(user_id: str, account: str, *, login: str, node: str, address: str, name: str, platform: str,
                 client: str) -> tuple[PairRequest, str]:
    """A PC asks to pair with the account its Tailscale login is linked to.
    Returns the request and the secret only that PC gets."""
    import secrets

    _count_try(address)
    now = time.time()
    with _lock:
        _prune_requests(now)
        # One open request per machine: asking again replaces the earlier one.
        for key in [k for k, r in _requests.items()
                    if r.user_id == user_id and r.address == address and r.status in ('pending', 'approved')]:
            _requests.pop(key, None)
        waiting = [r for r in _requests.values() if r.user_id == user_id and r.status == 'pending']
        if len(waiting) >= MAX_REQUESTS_PER_USER:
            raise PairingError('too_many', 'This account already has several PCs waiting to be approved. '
                               'Approve or decline them on the web first (Settings, Computers).', 429)
        secret = secrets.token_urlsafe(24)
        item = PairRequest(
            id=secrets.token_urlsafe(9), secret_hash=hash_token(secret), user_id=user_id, account=account,
            login=login, node=str(node or '')[:80], address=address,
            name=(str(name or '').strip() or 'My PC')[:80], platform=str(platform or '')[:80],
            client='desktop' if client == 'desktop' else 'cli', expires=now + CODE_TTL_S)
        _requests[item.id] = item
    return item, secret


def requests_of(user_id: str) -> list[dict[str, Any]]:
    """PCs waiting for this person's yes."""
    now = time.time()
    with _lock:
        _prune_requests(now)
        waiting = [r for r in _requests.values() if r.user_id == str(user_id) and r.status == 'pending']
    return [_request_view(r) for r in sorted(waiting, key=lambda r: r.expires)]


async def decide_request(user_id: str, request_id: str, approve: bool, sid: str = '') -> dict[str, Any]:
    """The owner's answer on the web. A device that itself came in by a code
    less than a day ago can't approve a PC, like it can't make a code."""
    from cognitrix import signins

    item = _requests.get(str(request_id))
    if item is None or item.user_id != str(user_id) or item.expires < time.time() or item.status != 'pending':
        raise PairingError('gone', 'That request is no longer waiting. Ask again from the PC.', 404)
    if approve and await signins.new_from_a_code(sid):
        raise PairingError('too_new', "This device signed in with a code less than a day ago, so it can't approve "
                           'a PC yet. Approve it on a device you signed in to with your password.', 403)
    with _lock:
        if item.status != 'pending':
            raise PairingError('gone', 'That request is no longer waiting. Ask again from the PC.', 404)
        item.status = 'approved' if approve else 'denied'
        item.approved_by_sid = sid if approve else ''
    return _request_view(item)


def _own_request(request_id: str, secret: str, login: str, address: str) -> PairRequest:
    _count_try(address)
    item = _requests.get(str(request_id))
    if item is None or item.secret_hash != hash_token(secret) or item.login != login:
        raise PairingError('gone', 'That pairing request is not known here. Ask again.', 404)
    return item


def request_status(request_id: str, secret: str, login: str, address: str) -> dict[str, Any]:
    """What the PC that asked is told while it waits."""
    item = _own_request(request_id, secret, login, address)
    with _lock:
        # Asking how it stands is not a wrong guess: don't count it against the address.
        tries = _tries.get(address)
        if tries:
            tries.pop()
    expired = item.expires < time.time()
    status = 'expired' if expired and item.status in ('pending', 'approved') else item.status
    return {'status': status, 'account': item.account, 'expires_in': max(0, int(item.expires - time.time()))}


async def pair_approved(request_id: str, secret: str, login: str, address: str, *, name: str, platform: str,
                        folders: Any, powers: Any, version: str = '', client: str = '') -> tuple[Any, str]:
    """Use an approved request: make the device record and return (device, token)."""
    from types import SimpleNamespace

    from cognitrix import signins

    item = _own_request(request_id, secret, login, address)
    if item.expires < time.time():
        raise PairingError('expired', 'That request ran out of time. Ask again.', 410)
    with _lock:
        if item.status != 'approved':
            raise PairingError('not_approved', "That request hasn't been approved on the web.", 409)
        item.status = 'used'
    try:
        pc_name = (str(name or '').strip() or item.name)[:80]
        approver = ''
        if item.approved_by_sid:
            from cognitrix.models.sign_in import SignIn

            row = await SignIn.find_one({'sid': item.approved_by_sid})
            approver = row.name if row is not None else ''
        sid = await signins.start(SimpleNamespace(id=item.user_id), method='pairing', address=address,
                                  name=_pc_label(pc_name, client or item.client), kind=_kind(client or item.client),
                                  approved_by=approver, approved_by_sid=item.approved_by_sid)
        return await _make_device(item.user_id, name=pc_name, platform=platform or item.platform, folders=folders,
                                  powers=powers, version=version, sign_in_sid=sid, tailnet_node=item.node)
    except BaseException:
        item.status = 'approved'
        raise


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
        'tailnet_node': str(getattr(device, 'tailnet_node', '') or ''),
    }
