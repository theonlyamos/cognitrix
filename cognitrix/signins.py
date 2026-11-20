"""Signed-in devices, and signing in a new device with a code.

Every session token now names its device: ``sid`` in the token is a
``SignIn`` row. A token whose row is gone or ended stops working within
``CHECK_CACHE_S`` seconds, so one device can be signed out on its own.
Tokens made before this change have no ``sid`` and keep working until they
expire or "Sign out of all devices" ends them.

Sign-in codes: a signed-in device makes a one-time code (``CODE_TTL_S``),
and a new device types it on the sign-in page to get its own session. The
code is a secret for those minutes: whoever types it first signs in. So it
works once, a few open at most per person, wrong tries are limited per
address, and a device that itself signed in with a code can't make one for
its first day. Turn it off for everyone with ``COGNITRIX_CODE_SIGNIN=false``.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

FMT = '%Y-%m-%d %H:%M:%S'
CODE_TTL_S = 300
MAX_OPEN_CODES = 3
# A device that signed in with a code waits this long before it can make one.
NEW_DEVICE_WAIT_S = 24 * 3600
# Wrong or used codes one address may try before it has to wait.
CODE_TRIES = 10
CODE_WINDOW_S = 600
# How long a token's device check is trusted before reading the row again.
CHECK_CACHE_S = 30.0
LAST_SEEN_EVERY_S = 300.0
KINDS = ('browser', 'desktop', 'cli')


class SignInError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def now_stamp(when: datetime | None = None) -> str:
    return (when or datetime.now(UTC)).replace(tzinfo=None).strftime(FMT)


def _parse(stamp: Any) -> datetime | None:
    try:
        return datetime.strptime(str(stamp)[:19].replace('T', ' '), FMT).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def code_signin_on() -> bool:
    return os.environ.get('COGNITRIX_CODE_SIGNIN', '').strip().lower() not in ('0', 'false', 'no', 'off')


# ---------------------------------------------------------------------------
# Naming a device
# ---------------------------------------------------------------------------

def device_name(user_agent: str) -> str:
    """'Chrome on Windows' from a browser's user agent, best effort."""
    ua = str(user_agent or '')
    if not ua:
        return 'Unknown device'
    if 'Cognitrix' in ua and 'Tauri' in ua:
        browser = 'Cognitrix desktop app'
    elif 'Edg/' in ua:
        browser = 'Edge'
    elif 'OPR/' in ua or 'Opera' in ua:
        browser = 'Opera'
    elif 'Firefox/' in ua or 'FxiOS' in ua:
        browser = 'Firefox'
    elif 'Chrome/' in ua or 'CriOS' in ua:
        browser = 'Chrome'
    elif 'Safari/' in ua:
        browser = 'Safari'
    elif re.match(r'(python-)?(httpx|requests|aiohttp|curl)', ua, re.I):
        browser = 'A script'
    else:
        browser = 'A browser'
    if 'iPhone' in ua:
        system = 'iPhone'
    elif 'iPad' in ua:
        system = 'iPad'
    elif 'Android' in ua:
        system = 'Android'
    elif 'Windows' in ua:
        system = 'Windows'
    elif 'Mac OS X' in ua or 'Macintosh' in ua:
        system = 'macOS'
    elif 'CrOS' in ua:
        system = 'ChromeOS'
    elif 'Linux' in ua:
        system = 'Linux'
    else:
        return browser
    return f'{browser} on {system}'


def _clean_name(name: Any) -> str:
    return re.sub(r'\s+', ' ', str(name or '')).strip()[:80]


def mask_address(address: str) -> str:
    address = str(address or '')
    if address.count('.') == 3:
        parts = address.split('.')
        return f'{parts[0]}.{parts[1]}.x.x'
    if ':' in address:
        return ':'.join(address.split(':')[:3]) + ':…'
    return address


# ---------------------------------------------------------------------------
# Sign-in rows
# ---------------------------------------------------------------------------

async def start(user, *, method: str, user_agent: str = '', address: str = '',
                name: str = '', kind: str = '', approved_by: str = '', approved_by_sid: str = '') -> str:
    """Record a new signed-in device and return its sid."""
    from cognitrix.models.sign_in import SignIn

    kind = kind if kind in KINDS else ('desktop' if 'Tauri' in str(user_agent) else 'browser')
    stamp = now_stamp()
    row = SignIn(
        sid=secrets.token_urlsafe(18),
        user_id=str(user.id),
        name=_clean_name(name) or device_name(user_agent),
        kind=kind,
        user_agent=str(user_agent or '')[:400],
        address=str(address or '')[:64],
        method=method,
        approved_by=_clean_name(approved_by),
        approved_by_sid=str(approved_by_sid or ''),
        created_at=stamp,
        last_seen_at=stamp,
    )
    await row.save()
    return row.sid


def make_token(user, sid: str | None) -> str:
    from cognitrix.common.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    from cognitrix.common.security import create_access_token

    data: dict[str, Any] = {'sub': user.email}
    if sid:
        data['sid'] = sid
    return create_access_token(data, expires_delta=timedelta(minutes=float(JWT_ACCESS_TOKEN_EXPIRE_MINUTES)))


async def sign_in(user, request=None, *, method: str, name: str = '', kind: str = '',
                  approved_by: str = '', approved_by_sid: str = '') -> str:
    """A session token for a new device, with its row."""
    sid = await start(user, method=method, user_agent=_header(request, 'user-agent'),
                      address=client_address(request), name=name, kind=kind,
                      approved_by=approved_by, approved_by_sid=approved_by_sid)
    return make_token(user, sid)


def _header(request, name: str) -> str:
    try:
        return str(request.headers.get(name) or '') if request is not None else ''
    except Exception:  # noqa: BLE001
        return ''


def client_address(request) -> str:
    if request is None:
        return ''
    if os.environ.get('FLY_APP_NAME') and _header(request, 'fly-client-ip'):
        return _header(request, 'fly-client-ip')
    client = getattr(request, 'client', None)
    return str(getattr(client, 'host', '') or '')


# sid -> (live, user_id, checked at)
_checks: dict[str, tuple[bool, str, float]] = {}
_seen: dict[str, float] = {}
_lock = threading.Lock()


def forget(sid: str) -> None:
    with _lock:
        _checks.pop(sid, None)


async def is_live(sid: str, user_id: str) -> bool:
    """True when the device's row exists, belongs to the user and wasn't signed out."""
    from cognitrix.models.sign_in import SignIn

    now = time.monotonic()
    cached = _checks.get(sid)
    if cached and now - cached[2] < CHECK_CACHE_S:
        live, owner = cached[0], cached[1]
    else:
        row = await SignIn.find_one({'sid': sid})
        live = bool(row is not None and not row.ended_at)
        owner = str(row.user_id) if row is not None else ''
        with _lock:
            _checks[sid] = (live, owner, now)
            if len(_checks) > 5000:
                for key in list(_checks)[:1000]:
                    _checks.pop(key, None)
    if not live or owner != str(user_id):
        return False
    if now - _seen.get(sid, 0.0) >= LAST_SEEN_EVERY_S:
        _seen[sid] = now
        try:
            await SignIn.update_one({'sid': sid}, {'last_seen_at': now_stamp()})
        except Exception:  # noqa: BLE001 - a missed stamp never blocks a request
            pass
    return True


async def end(user_id: str, sid: str) -> bool:
    from cognitrix.models.sign_in import SignIn

    row = await SignIn.find_one({'sid': sid})
    if row is None or str(row.user_id) != str(user_id) or row.ended_at:
        return False
    await SignIn.update_one({'sid': sid}, {'ended_at': now_stamp()})
    forget(sid)
    await _cancel_codes(user_id, made_by=[sid])
    await _unlink_pcs([sid])
    return True


async def end_all(user_id: str, keep_sid: str = '') -> int:
    """Sign out every device of the user (but ``keep_sid``). Returns how many."""
    from cognitrix.models.sign_in import SignIn

    count = 0
    ended: list[str] = []
    for row in await SignIn.find({'user_id': str(user_id)}) or []:
        if row.ended_at or (keep_sid and row.sid == keep_sid):
            continue
        await SignIn.update_one({'sid': row.sid}, {'ended_at': now_stamp()})
        forget(row.sid)
        ended.append(row.sid)
        count += 1
    # Signing out other devices (or everywhere) also voids every open code, this device's too.
    await _cancel_codes(user_id)
    await _unlink_pcs(ended)
    return count


async def _cancel_codes(user_id: str, made_by: list[str] | None = None) -> None:
    """Cancel the user's open sign-in codes (only the ones those sign-ins made, with ``made_by``)."""
    from cognitrix.models.sign_in import SignInCode

    try:
        rows = await SignInCode.find({'user_id': str(user_id)}) or []
    except Exception:  # noqa: BLE001 - no codes table yet means no codes
        return
    now = int(time.time())
    for c in rows:
        if c.used_at or c.canceled or int(c.expires_at or 0) <= now:
            continue
        if made_by is not None and c.made_by_sid not in made_by:
            continue
        await SignInCode.update_one({'code_hash': c.code_hash}, {'canceled': True})


async def _code_backed(item, user) -> bool:
    """A code only works while the sign-in that made it is still signed in, and only if it
    was made after the account's last sign-out everywhere."""
    from cognitrix.models.sign_in import SignIn

    cutoff = getattr(user, 'tokens_valid_after', None)
    made_at = int(item.expires_at or 0) - CODE_TTL_S
    try:
        if cutoff and made_at < int(cutoff):
            return False
    except (TypeError, ValueError):
        return False
    if item.made_by_sid:
        row = await SignIn.find_one({'sid': item.made_by_sid})
        if row is None or row.ended_at or str(row.user_id) != str(user.id):
            return False
    return True


async def _unlink_pcs(sids: list[str]) -> None:
    """A PC linked by a sign-in stops being paired when that sign-in ends."""
    if not sids:
        return
    from cognitrix.models.paired_device import PairedDevice

    for sid in sids:
        try:
            rows = await PairedDevice.find({'sign_in_sid': sid}) or []
        except Exception:  # noqa: BLE001 - no paired PCs table (yet)
            return
        for device in rows:
            await PairedDevice.delete_many({'id': device.id})
            try:
                from cognitrix.computer.paired.hub import hub
                await hub.drop(device.id, {'type': 'unpaired'}, code=4401)
            except Exception:  # noqa: BLE001 - not connected here
                pass


async def linked_pc_sids(user_id: str) -> set[str]:
    from cognitrix.models.paired_device import PairedDevice

    try:
        rows = await PairedDevice.find({'user_id': str(user_id)}) or []
    except Exception:  # noqa: BLE001
        return set()
    return {str(d.sign_in_sid) for d in rows if getattr(d, 'sign_in_sid', '')}


def _lifetime() -> timedelta:
    from cognitrix.common.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    return timedelta(minutes=float(JWT_ACCESS_TOKEN_EXPIRE_MINUTES))


def view(row, current_sid: str = '', linked: set[str] | None = None) -> dict[str, Any]:
    return {
        'linked_pc': bool(linked and row.sid in linked),
        'id': row.sid,
        'name': row.name or 'Unknown device',
        'kind': row.kind or 'browser',
        'method': row.method or 'password',
        'approved_by': row.approved_by or '',
        'address': mask_address(row.address),
        'created_at': row.created_at,
        'last_seen_at': row.last_seen_at or row.created_at,
        'current': bool(current_sid and row.sid == current_sid),
    }


async def listing(user, current_sid: str = '') -> list[dict[str, Any]]:
    """The user's signed-in devices, the current one first, then the most recently used."""
    from cognitrix.models.sign_in import SignIn

    cutoff = datetime.now(UTC) - _lifetime() - timedelta(days=1)
    valid_after = int(getattr(user, 'tokens_valid_after', 0) or 0)
    rows = []
    for row in await SignIn.find({'user_id': str(user.id)}) or []:
        if row.ended_at:
            continue
        seen = _parse(row.last_seen_at or row.created_at)
        if seen is not None and seen < cutoff:
            continue
        created = _parse(row.created_at)
        if valid_after and created is not None and created.timestamp() < valid_after - 1 and row.sid != current_sid:
            continue
        rows.append(row)
    current = [r for r in rows if r.sid == current_sid]
    others = sorted((r for r in rows if r.sid != current_sid), key=lambda r: str(r.last_seen_at or r.created_at),
                    reverse=True)
    linked = await linked_pc_sids(str(user.id))
    return [view(r, current_sid, linked) for r in current + others]


# ---------------------------------------------------------------------------
# Sign-in codes
# ---------------------------------------------------------------------------

def _hash(code: str) -> str:
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def new_code() -> str:
    from cognitrix.computer.paired import new_code as paired_code

    return paired_code()


def normalize(code: Any) -> str:
    from cognitrix.computer.paired import normalize_code

    return normalize_code(code)


async def make_code(user, sid: str = '', server: str = '') -> dict[str, Any]:
    """A one-time code another device can sign in with."""
    from cognitrix.models.sign_in import SignIn, SignInCode

    if not code_signin_on():
        raise SignInError('off', 'Signing in with a code is turned off on this server.', 403)
    made_by = ''
    if sid:
        row = await SignIn.find_one({'sid': sid})
        if row is not None:
            made_by = row.name
            created = _parse(row.created_at)
            if row.method == 'code' and created is not None and \
                    (datetime.now(UTC) - created).total_seconds() < NEW_DEVICE_WAIT_S:
                raise SignInError(
                    'too_new',
                    'This device signed in with a code less than a day ago, so it can\'t make one yet. '
                    'Make the code on a device you signed in to with your password.', 403)
    now = int(time.time())
    open_codes = [c for c in await SignInCode.find({'user_id': str(user.id)}) or []
                  if not c.used_at and not c.canceled and int(c.expires_at or 0) > now]
    open_codes.sort(key=lambda c: int(c.expires_at or 0))
    for old in open_codes[:max(0, len(open_codes) - MAX_OPEN_CODES + 1)]:
        await SignInCode.update_one({'code_hash': old.code_hash}, {'canceled': True})
    code = new_code()
    await SignInCode(code_hash=_hash(code), user_id=str(user.id), made_by_sid=sid or '',
                     made_by_name=made_by or 'a signed-in device', expires_at=now + CODE_TTL_S).save()
    from cognitrix import audit
    audit.record('sign_in', 'Made a sign-in code', user_id=str(user.id), actor=str(user.email), outcome='ok')
    return {'code': code, 'expires_in': CODE_TTL_S, 'server': server}


async def code_status(user, code: str) -> dict[str, Any]:
    from cognitrix.models.sign_in import SignIn, SignInCode

    item = await SignInCode.find_one({'code_hash': _hash(normalize(code))}) if normalize(code) else None
    if item is None or str(item.user_id) != str(user.id) or item.canceled:
        return {'status': 'expired'}
    if item.used_at:
        out: dict[str, Any] = {'status': 'used'}
        row = await SignIn.find_one({'sid': item.used_sid}) if item.used_sid else None
        if row is not None:
            out['sign_in'] = view(row, '', await linked_pc_sids(str(user.id)))
            out['sign_in']['ended'] = bool(row.ended_at)
        return out
    left = int(item.expires_at or 0) - int(time.time())
    if left <= 0:
        return {'status': 'expired'}
    return {'status': 'waiting', 'expires_in': left}


async def cancel_code(user, code: str) -> None:
    from cognitrix.models.sign_in import SignInCode

    item = await SignInCode.find_one({'code_hash': _hash(normalize(code))}) if normalize(code) else None
    if item is not None and str(item.user_id) == str(user.id) and not item.used_at:
        await SignInCode.update_one({'code_hash': item.code_hash}, {'canceled': True})


_tries: dict[str, list[float]] = {}
_use_lock = None


def _count_try(address: str) -> None:
    now = time.time()
    with _lock:
        recent = [t for t in _tries.get(address, []) if t > now - CODE_WINDOW_S]
        if len(recent) >= CODE_TRIES:
            raise SignInError('too_many', 'Too many sign-in code tries from here. Wait 10 minutes and try again.', 429)
        recent.append(now)
        _tries[address] = recent


def _forgive(address: str) -> None:
    with _lock:
        recent = _tries.get(address, [])
        if recent:
            recent.pop()


async def peek_code(code: str, address: str) -> dict[str, Any]:
    """Whose account a sign-in code is for, without using it (a PC shows this before linking)."""
    from cognitrix.models import User
    from cognitrix.models.sign_in import SignInCode

    if not code_signin_on():
        raise SignInError('off', 'Signing in with a code is turned off on this server.', 403)
    _count_try(address or 'unknown')
    key = normalize(code)
    item = await SignInCode.find_one({'code_hash': _hash(key)}) if key else None
    user = await User.get(item.user_id) if item is not None else None
    if item is None or item.canceled or item.used_at or int(item.expires_at or 0) <= int(time.time()) \
            or user is None or getattr(user, 'disabled', None) or not await _code_backed(item, user):
        raise SignInError('bad_code', "That code isn't right or has expired. Make a new one on your signed-in device.",
                          400)
    _forgive(address or 'unknown')
    name = str(getattr(user, 'name', '') or '').strip()
    email = str(getattr(user, 'email', '') or '').strip()
    return {'account': ' · '.join(p for p in (name, email) if p)[:160] or 'your account',
            'expires_in': int(item.expires_at) - int(time.time())}


async def redeem_code(code: str, *, address: str = '', user_agent: str = '', name: str = '',
                      kind: str = '') -> tuple[Any, str]:
    """Use a sign-in code: record the new device and return (user, sid)."""
    import asyncio

    from cognitrix.models import User
    from cognitrix.models.sign_in import SignInCode

    global _use_lock
    if not code_signin_on():
        raise SignInError('off', 'Signing in with a code is turned off on this server.', 403)
    address = address or 'unknown'
    _count_try(address)
    bad = SignInError('bad_code', "That code isn't right or has expired. Make a new one on your signed-in device.", 400)
    key = normalize(code)
    if not key:
        raise bad
    if _use_lock is None:
        _use_lock = asyncio.Lock()
    async with _use_lock:  # one use per code, even when two devices race
        item = await SignInCode.find_one({'code_hash': _hash(key)})
        if item is None or item.canceled or item.used_at or int(item.expires_at or 0) <= int(time.time()):
            raise bad
        user = await User.get(item.user_id)
        if user is None or getattr(user, 'disabled', None) or not await _code_backed(item, user):
            raise bad
        await SignInCode.update_one({'code_hash': item.code_hash}, {'used_at': int(time.time())})
    _forgive(address)
    sid = await start(user, method='code', user_agent=user_agent, address=address,
                      name=name, kind=kind, approved_by=item.made_by_name, approved_by_sid=item.made_by_sid)
    await SignInCode.update_one({'code_hash': item.code_hash}, {'used_sid': sid})
    from cognitrix.models.sign_in import SignIn
    row = await SignIn.find_one({'sid': sid})
    device = row.name if row is not None else 'A new device'
    from cognitrix import audit
    audit.record('sign_in', f'Signed in on {device} with a code from {item.made_by_name}',
                 user_id=str(user.id), actor=str(user.email), outcome='ok')
    try:
        from cognitrix.tasks.automation import create_notification

        await create_notification(
            str(user.id), f'New sign-in: {device}',
            f'{device} signed in to your account with a code made on {item.made_by_name}. '
            "If this wasn't you, sign it out in Settings, Sign-ins.",
            link='/settings/sign-ins')
    except Exception:  # noqa: BLE001 - the notice is best effort
        pass
    return user, sid


async def use_code(code: str, request=None, *, name: str = '', kind: str = '') -> tuple[Any, str]:
    """Sign a new device in with a code. Returns (user, token)."""
    user, sid = await redeem_code(code, address=client_address(request), user_agent=_header(request, 'user-agent'),
                                  name=name, kind=kind)
    return user, make_token(user, sid)
