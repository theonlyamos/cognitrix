"""Passkeys (WebAuthn): sign in with Windows Hello, Touch ID, a phone or a security key.

A signed-in person adds a passkey in Settings, Sign-ins. On the sign-in page
"Sign in with a passkey" asks the browser for one (no email needed: the
passkey says whose it is), and the server checks its signature against the
public key it saved. Each sign-in gets its own ``SignIn`` row with method
``passkey``, like any other sign-in (cognitrix/signins.py).

The passkey's "relying party" is the page's host name (``example.com``,
``localhost``). A passkey made on one host only works there; browsers refuse
IP addresses, so a server opened as ``http://127.0.0.1:8000`` has to be
opened as ``http://localhost:8000`` instead.

The desktop app's window shows the engine's pages on ``http://127.0.0.1``,
so its webview can never use a passkey (and on macOS and Linux the webviews
have no WebAuthn for apps like this one at all). The app hands the step to
the default browser instead: it starts a *handoff* here and keeps its secret,
opens ``http://localhost:<port>/passkey?handoff=<id>`` in the browser, the
browser does the passkey step, and the app collects the result with the
secret. Handoffs only exist on the desktop engine and only for callers on
this computer, so nobody can send someone a link that signs a stranger in.

Turn passkeys off for everyone with the ``COGNITRIX_PASSKEYS`` setting.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import secrets
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from cognitrix.app_settings import get as app_setting

CEREMONY_TTL_S = 300
HANDOFF_TTL_S = 300
MAX_PASSKEYS = 20
# Passkey prompts one address may start in a window, so nobody fills the table.
START_LIMIT = 30
START_WINDOW_S = 600
RP_NAME = 'Cognitrix'

# Names for the common passkey providers, by the AAGUID they report.
# https://github.com/passkeydeveloper/passkey-authenticator-aaguids
PROVIDERS = {
    'ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4': 'Google Password Manager',
    'adce0002-35bc-c60a-648b-0b25f1f05503': 'Chrome on Mac',
    '08987058-cadc-4b81-b6e1-30de50dcbe96': 'Windows Hello',
    '9ddd1817-af5a-4672-a2b9-3e3dd95000a9': 'Windows Hello',
    '6028b017-b1d4-4c02-b4b3-afcdafc96bb2': 'Windows Hello',
    'fbfc3007-154e-4ecc-8c0b-6e020557d7bd': 'iCloud Keychain',
    'dd4ec289-e01d-41c9-bb89-70fa845d4bf2': 'iCloud Keychain',
    'bada5566-a7aa-401f-bd96-45619a55120d': '1Password',
    'd548826e-79b4-db40-a3d8-11116f7e8349': 'Bitwarden',
    '531126d6-e717-415c-9320-3d9aa6981239': 'Dashlane',
    'b84e4048-15dc-4dd0-8640-f4f60813c8af': 'NordPass',
    '0ea242b4-43c4-4a1b-8b17-dd6d0b6baec6': 'Keeper',
    'f3809540-7f14-49c1-a8b3-8f813b225541': 'Enpass',
    '53414d53-554e-4700-0000-000000000000': 'Samsung Pass',
    'b5397666-4885-aa6b-cebf-e52262a439a2': 'Chromium Browser',
    '771b48fd-d3d4-4f74-9232-fc157ab0507a': 'Edge on Mac',
    'cb69481e-8ff7-4039-93ec-0a2729a154a8': 'YubiKey',
    'ee882879-721c-4913-9775-3dfcce97072a': 'YubiKey',
    'fa2b99dc-9e39-4257-8f92-4a30d23c4118': 'YubiKey',
    '2fc0579f-8113-47ea-b116-bb5a8db9202a': 'YubiKey',
}


class PasskeyError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def passkeys_on() -> bool:
    return app_setting('COGNITRIX_PASSKEYS', '').strip().lower() not in ('0', 'false', 'no', 'off')


def _require_on() -> None:
    if not passkeys_on():
        raise PasskeyError('off', 'Passkeys are turned off on this server.', 403)


def on_desktop_engine() -> bool:
    return os.environ.get('COGNITRIX_DESKTOP') == '1'


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _b64(data: bytes) -> str:
    from webauthn.helpers import bytes_to_base64url
    return bytes_to_base64url(data)


def _unb64(text: str) -> bytes:
    from webauthn.helpers import base64url_to_bytes
    return base64url_to_bytes(text)


def _now() -> int:
    return int(time.time())


def _header(request, name: str) -> str:
    try:
        return str(request.headers.get(name) or '') if request is not None else ''
    except Exception:  # noqa: BLE001
        return ''


# ---------------------------------------------------------------------------
# Which site a passkey belongs to
# ---------------------------------------------------------------------------

def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip('[]'))
        return True
    except ValueError:
        return False


def _is_local_name(host: str) -> bool:
    return host == 'localhost' or host.endswith('.localhost')


def site_for(request) -> tuple[str, str]:
    """(rp_id, origin) for the page that is asking, from its ``Origin`` header.

    The origin has to be this server itself (the host the request came to,
    the public address in Settings, or an allowed CORS origin), and a name
    browsers accept for passkeys: https, or http on localhost.
    """
    origin = _header(request, 'origin').strip().rstrip('/')
    parts = urlsplit(origin) if origin else None
    host = (parts.hostname or '') if parts else ''
    if not parts or parts.scheme not in ('http', 'https') or not host:
        raise PasskeyError('no_origin', 'Open Cognitrix in a browser to use a passkey.')
    if _is_ip(host):
        port = f':{parts.port}' if parts.port else ''
        raise PasskeyError(
            'ip_address',
            f'Passkeys don\'t work on an IP address. Open this page as {parts.scheme}://localhost{port} '
            'or at the server\'s web address instead.' if host in ('127.0.0.1', '::1')
            else 'Passkeys don\'t work on an IP address. Open Cognitrix at its web address (a name like '
                 'cognitrix.example.com) instead.')
    if parts.scheme == 'http' and not _is_local_name(host):
        raise PasskeyError('not_https', 'Passkeys need a secure (https) address.')
    if not _origin_trusted(request, origin, host):
        raise PasskeyError('bad_origin', 'This page is not part of this Cognitrix server.', 403)
    return host, origin


def secure_address() -> str:
    """An address of this server where passkeys work (https and a real name):
    the public address in Settings, else the Tailscale private address. Pages
    opened somewhere passkeys can't work (an IP address, plain http) link here."""
    from cognitrix.common import addresses
    public = addresses.configured()
    if public.startswith('https://') and not _is_ip(urlsplit(public).hostname or ''):
        return public
    return addresses.private_url()


def _origin_trusted(request, origin: str, host: str) -> bool:
    forwarded = _header(request, 'x-forwarded-host').split(',')[0].strip()
    for candidate in (forwarded, _header(request, 'host')):
        if candidate and (urlsplit(f'//{candidate}').hostname or '') == host:
            return True
    # The public address in Settings and the Tailscale addresses are this
    # server too. The Tailscale ones share a name (only the port differs), so
    # a passkey made on one works on the other.
    from cognitrix.common import addresses
    if origin in addresses.own_origins():
        return True
    try:
        from cognitrix.config import settings
        return origin in [o.rstrip('/') for o in settings.cors_origins]
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

_starts: dict[str, list[float]] = {}
_lock = threading.Lock()


def _count_start(address: str) -> None:
    now = time.time()
    with _lock:
        recent = [t for t in _starts.get(address or 'unknown', []) if t > now - START_WINDOW_S]
        if len(recent) >= START_LIMIT:
            raise PasskeyError('too_many', 'Too many passkey tries from here. Wait a few minutes and try again.', 429)
        recent.append(now)
        _starts[address or 'unknown'] = recent
        if len(_starts) > 5000:
            for key in list(_starts)[:1000]:
                _starts.pop(key, None)


async def _sweep() -> None:
    """Drop finished or expired ceremonies and handoffs (best effort)."""
    from cognitrix.models.sign_in import PasskeyCeremony, PasskeyHandoff

    cutoff = _now() - 3600
    try:
        for row in await PasskeyCeremony.find({}) or []:
            if int(row.expires_at or 0) < cutoff or (row.used and int(row.expires_at or 0) < _now()):
                await PasskeyCeremony.delete_many({'ceremony_id': row.ceremony_id})
        for row in await PasskeyHandoff.find({}) or []:
            if int(row.expires_at or 0) < cutoff:
                await PasskeyHandoff.delete_many({'handoff_id': row.handoff_id})
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Ceremonies (one challenge, used once)
# ---------------------------------------------------------------------------

async def _new_ceremony(kind: str, challenge: bytes, rp_id: str, origin: str, *, user_id: str = '',
                        handoff_id: str = '', name: str = '') -> str:
    from cognitrix.models.sign_in import PasskeyCeremony

    await _sweep()
    ceremony_id = secrets.token_urlsafe(18)
    await PasskeyCeremony(ceremony_id=ceremony_id, kind=kind, challenge=_b64(challenge), rp_id=rp_id,
                          origin=origin, user_id=user_id, handoff_id=handoff_id, name=name[:80],
                          expires_at=_now() + CEREMONY_TTL_S).save()
    return ceremony_id


_use_lock = None


async def _take_ceremony(ceremony_id: str, kind: str):
    """The ceremony, marked used. Raises when it is unknown, used or expired."""
    import asyncio

    from cognitrix.models.sign_in import PasskeyCeremony

    global _use_lock
    if _use_lock is None:
        _use_lock = asyncio.Lock()
    expired = PasskeyError('expired', 'That passkey request expired. Try again.')
    if not ceremony_id:
        raise expired
    async with _use_lock:
        row = await PasskeyCeremony.find_one({'ceremony_id': str(ceremony_id)})
        if row is None or row.kind != kind or row.used or int(row.expires_at or 0) <= _now():
            raise expired
        await PasskeyCeremony.update_one({'ceremony_id': row.ceremony_id}, {'used': True})
    return row


# ---------------------------------------------------------------------------
# The person's passkeys
# ---------------------------------------------------------------------------

def _stamp() -> str:
    return datetime.now(UTC).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')


def view(row) -> dict[str, Any]:
    return {
        'id': row.passkey_id,
        'name': row.name or 'Passkey',
        'provider': row.provider or '',
        'site': row.rp_id,
        'synced': bool(row.backed_up),
        'created_at': row.created_at,
        'last_used_at': row.last_used_at or '',
    }


async def listing(user) -> list[dict[str, Any]]:
    from cognitrix.models.sign_in import Passkey

    rows = await Passkey.find({'user_id': str(user.id)}) or []
    rows.sort(key=lambda r: str(r.created_at))
    return [view(r) for r in rows]


async def _owned(user, passkey_id: str):
    from cognitrix.models.sign_in import Passkey

    row = await Passkey.find_one({'passkey_id': str(passkey_id)})
    if row is None or str(row.user_id) != str(user.id):
        raise PasskeyError('not_found', 'That passkey is not on your account.', 404)
    return row


async def rename(user, passkey_id: str, name: str) -> dict[str, Any]:
    from cognitrix.models.sign_in import Passkey

    row = await _owned(user, passkey_id)
    name = ' '.join(str(name or '').split())[:80]
    if not name:
        raise PasskeyError('no_name', 'Give the passkey a name.')
    await Passkey.update_one({'passkey_id': row.passkey_id}, {'name': name})
    row.name = name
    return view(row)


async def remove(user, passkey_id: str) -> None:
    from cognitrix.models.sign_in import Passkey

    row = await _owned(user, passkey_id)
    await Passkey.delete_many({'passkey_id': row.passkey_id})
    from cognitrix import audit
    audit.record('settings', f'Removed the passkey {row.name}', user_id=str(user.id),
                 actor=str(getattr(user, 'email', '') or ''), outcome='ok')


def _user_handle(user) -> bytes:
    return str(user.id).encode('utf-8')[:64]


async def registration_options(user, request, *, handoff_id: str = '') -> dict[str, Any]:
    """Options for navigator.credentials.create(), and the ceremony id to send back."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    from cognitrix.models.sign_in import Passkey

    _require_on()
    rp_id, origin = site_for(request)
    from cognitrix import signins
    _count_start(signins.client_address(request))
    existing = await Passkey.find({'user_id': str(user.id)}) or []
    if len(existing) >= MAX_PASSKEYS:
        raise PasskeyError('too_many_passkeys', f'An account can have {MAX_PASSKEYS} passkeys. Remove one first.')
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=_user_handle(user),
        user_name=str(user.email),
        user_display_name=str(getattr(user, 'name', '') or user.email),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=_unb64(p.credential_id))
                             for p in existing if p.rp_id == rp_id],
    )
    ceremony = await _new_ceremony('register', options.challenge, rp_id, origin, user_id=str(user.id),
                                   handoff_id=handoff_id)
    import json
    return {'ceremony': ceremony, 'options': json.loads(options_to_json(options))}


def _guess_name(provider: str, user_agent: str) -> str:
    if provider:
        return provider
    from cognitrix.signins import device_name
    name = device_name(user_agent)
    return name if name not in ('Unknown device', 'A browser', 'A script') else 'Passkey'


async def register(user, request, ceremony_id: str, credential: dict[str, Any], name: str = '') -> dict[str, Any]:
    """Check the new passkey and save it to the account."""
    from webauthn import verify_registration_response

    from cognitrix.models.sign_in import Passkey

    _require_on()
    ceremony = await _take_ceremony(ceremony_id, 'register')
    if ceremony.user_id != str(user.id):
        raise PasskeyError('expired', 'That passkey request expired. Try again.')
    rp_id, origin = site_for(request)
    if rp_id != ceremony.rp_id or origin != ceremony.origin:
        raise PasskeyError('bad_origin', 'That passkey was made on a different page. Try again.')
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=_unb64(ceremony.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001 - the library raises several kinds
        raise PasskeyError('invalid', f'The passkey could not be checked: {exc}') from None
    credential_id = _b64(verified.credential_id)
    if await Passkey.find_one({'credential_id': credential_id}) is not None:
        raise PasskeyError('exists', 'That passkey is already saved.')
    provider = PROVIDERS.get(str(verified.aaguid or '').lower(), '')
    transports = credential.get('response', {}).get('transports') if isinstance(credential, dict) else None
    row = Passkey(
        passkey_id=secrets.token_urlsafe(12),
        user_id=str(user.id),
        credential_id=credential_id,
        public_key=_b64(verified.credential_public_key),
        sign_count=int(verified.sign_count or 0),
        rp_id=rp_id,
        name=' '.join(str(name or '').split())[:80] or _guess_name(provider, _header(request, 'user-agent')),
        provider=provider,
        aaguid=str(verified.aaguid or ''),
        backed_up=bool(verified.credential_backed_up),
        transports=','.join(str(t) for t in transports or [] if isinstance(t, str))[:120],
        created_at=_stamp(),
    )
    await row.save()
    from cognitrix import audit
    audit.record('settings', f'Added a passkey ({row.name})', user_id=str(user.id),
                 actor=str(getattr(user, 'email', '') or ''), outcome='ok')
    try:
        from cognitrix.tasks.automation import create_notification
        await create_notification(
            str(user.id), 'Passkey added',
            f'A passkey ({row.name}) was added to your account. If this wasn\'t you, remove it in Settings, '
            'Sign-ins, and change your password.', link='/settings/sign-ins')
    except Exception:  # noqa: BLE001 - the notice is best effort
        pass
    return view(row)


async def sign_in_options(request, *, handoff_id: str = '') -> dict[str, Any]:
    """Options for navigator.credentials.get(). No email: the passkey says whose it is."""
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement

    _require_on()
    rp_id, origin = site_for(request)
    from cognitrix import signins
    _count_start(signins.client_address(request))
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    ceremony = await _new_ceremony('sign_in', options.challenge, rp_id, origin, handoff_id=handoff_id)
    import json
    return {'ceremony': ceremony, 'options': json.loads(options_to_json(options))}


async def check_sign_in(request, ceremony_id: str, credential: dict[str, Any], *, handoff_id: str = ''):
    """Check a passkey sign-in. Returns (user, ceremony, passkey) on success."""
    from webauthn import verify_authentication_response

    from cognitrix.models import User
    from cognitrix.models.sign_in import Passkey

    _require_on()
    ceremony = await _take_ceremony(ceremony_id, 'sign_in')
    if (ceremony.handoff_id or '') != handoff_id:
        raise PasskeyError('expired', 'That passkey request expired. Try again.')
    rp_id, origin = site_for(request)
    if rp_id != ceremony.rp_id or origin != ceremony.origin:
        raise PasskeyError('bad_origin', 'That passkey was used on a different page. Try again.')
    unknown = PasskeyError('unknown', 'That passkey isn\'t on any account here. Sign in another way, then add it in '
                                      'Settings, Sign-ins.', 401)
    raw_id = str(credential.get('rawId') or credential.get('id') or '') if isinstance(credential, dict) else ''
    row = await Passkey.find_one({'credential_id': raw_id}) if raw_id else None
    if row is None or row.rp_id != rp_id:
        raise unknown
    user = await User.get(row.user_id)
    if user is None or getattr(user, 'disabled', None):
        raise unknown
    handle = credential.get('response', {}).get('userHandle') if isinstance(credential, dict) else None
    if handle and handle != _b64(_user_handle(user)):
        raise unknown
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=_unb64(ceremony.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=_unb64(row.public_key),
            credential_current_sign_count=int(row.sign_count or 0),
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001
        from cognitrix import audit
        audit.record('sign_in', 'Failed passkey sign-in', user_id=str(user.id), actor=str(user.email),
                     outcome='failed')
        raise PasskeyError('invalid', f'The passkey could not be checked: {exc}', 401) from None
    await Passkey.update_one({'passkey_id': row.passkey_id}, {
        'sign_count': int(verified.new_sign_count or 0),
        'backed_up': bool(verified.credential_backed_up),
        'last_used_at': _stamp(),
    })
    return user, ceremony, row


async def sign_in(request, ceremony_id: str, credential: dict[str, Any], *, name: str = '',
                  kind: str = '') -> tuple[Any, str]:
    """Sign this browser in with a passkey. Returns (user, token)."""
    from cognitrix import audit, signins

    user, _ceremony, passkey = await check_sign_in(request, ceremony_id, credential)
    audit.record('sign_in', f'Signed in with a passkey ({passkey.name})', user_id=str(user.id),
                 actor=str(user.email), outcome='ok')
    token = await signins.sign_in(user, request, method='passkey', name=name, kind=kind,
                                  approved_by=passkey.name)
    return user, token


# ---------------------------------------------------------------------------
# Desktop app: do the passkey step in the default browser
# ---------------------------------------------------------------------------

def _loopback(request) -> bool:
    from cognitrix import signins
    address = signins.client_address(request)
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address == 'localhost'


def _require_handoffs(request) -> None:
    _require_on()
    if not on_desktop_engine() or not _loopback(request):
        raise PasskeyError('no_handoff', 'Only the desktop app can finish a passkey step in the browser.', 403)


def handoff_path(handoff_id: str) -> str:
    return f'/passkey?handoff={handoff_id}'


async def start_handoff(request, kind: str, *, user=None, sid: str = '', device_name: str = '') -> dict[str, Any]:
    """The desktop app asks for a browser handoff. It keeps ``secret``; the browser gets ``path``."""
    from cognitrix.models.sign_in import PasskeyHandoff

    _require_handoffs(request)
    if kind not in ('sign_in', 'register'):
        raise PasskeyError('bad_kind', 'Unknown passkey step.')
    if kind == 'register' and user is None:
        raise PasskeyError('signed_out', 'Sign in first.', 401)
    await _sweep()
    handoff_id = secrets.token_urlsafe(18)
    secret = secrets.token_urlsafe(32)
    await PasskeyHandoff(handoff_id=handoff_id, secret_hash=_hash(secret), kind=kind,
                         user_id=str(user.id) if user is not None else '', made_by_sid=sid,
                         device_name=' '.join(str(device_name or '').split())[:80],
                         expires_at=_now() + HANDOFF_TTL_S).save()
    return {'handoff': handoff_id, 'secret': secret, 'path': handoff_path(handoff_id), 'expires_in': HANDOFF_TTL_S}


async def _open_handoff(handoff_id: str, kind: str | None = None):
    from cognitrix.models.sign_in import PasskeyHandoff

    row = await PasskeyHandoff.find_one({'handoff_id': str(handoff_id or '')}) if handoff_id else None
    if row is None or row.status not in ('waiting', 'opened') or int(row.expires_at or 0) <= _now() \
            or (kind and row.kind != kind):
        raise PasskeyError('expired', 'This link has expired. Start again in the Cognitrix app.', 410)
    return row


async def _register_handoff_user(row):
    """The account a register handoff adds a passkey to, while the device that started it
    is still signed in and the account wasn't signed out everywhere since. Signing that
    device out (or a password change) must also stop a leaked link from adding a passkey."""
    from cognitrix.models import User
    from cognitrix.models.sign_in import SignIn

    expired = PasskeyError('expired', 'This link has expired. Start again in the Cognitrix app.', 410)
    user = await User.get(row.user_id)
    if user is None or getattr(user, 'disabled', None):
        raise expired
    made_at = int(row.expires_at or 0) - HANDOFF_TTL_S
    cutoff = int(getattr(user, 'tokens_valid_after', 0) or 0)
    if cutoff and made_at < cutoff:
        raise expired
    if row.made_by_sid:
        started_by = await SignIn.find_one({'sid': row.made_by_sid})
        if started_by is None or started_by.ended_at or str(started_by.user_id) != str(user.id):
            raise expired
    return user


async def handoff_info(request, handoff_id: str) -> dict[str, Any]:
    """What the browser page shows: which step, and for adding a passkey, whose account."""

    _require_handoffs(request)
    row = await _open_handoff(handoff_id)
    out: dict[str, Any] = {'kind': row.kind, 'expires_in': int(row.expires_at) - _now()}
    if row.kind == 'register':
        user = await _register_handoff_user(row)
        out['account'] = str(getattr(user, 'email', '') or '')
    return out


async def handoff_options(request, handoff_id: str) -> dict[str, Any]:
    from cognitrix.models.sign_in import PasskeyHandoff

    _require_handoffs(request)
    row = await _open_handoff(handoff_id)
    if row.kind == 'register':
        user = await _register_handoff_user(row)
        out = await registration_options(user, request, handoff_id=row.handoff_id)
    else:
        out = await sign_in_options(request, handoff_id=row.handoff_id)
    await PasskeyHandoff.update_one({'handoff_id': row.handoff_id}, {'status': 'opened'})
    return {**out, 'kind': row.kind}


async def handoff_finish(request, handoff_id: str, ceremony_id: str, credential: dict[str, Any],
                         name: str = '') -> dict[str, Any]:
    """The browser finished the passkey step for the app. The app collects the result."""
    from cognitrix import audit, signins
    from cognitrix.models.sign_in import PasskeyCeremony, PasskeyHandoff

    _require_handoffs(request)
    row = await _open_handoff(handoff_id)
    ceremony = await PasskeyCeremony.find_one({'ceremony_id': str(ceremony_id or '')}) if ceremony_id else None
    if ceremony is None or ceremony.handoff_id != row.handoff_id:
        raise PasskeyError('expired', 'That passkey request expired. Try again.')
    if row.kind == 'register':
        user = await _register_handoff_user(row)
        passkey = await register(user, request, ceremony_id, credential, name)
        await PasskeyHandoff.update_one({'handoff_id': row.handoff_id},
                                        {'status': 'done', 'passkey_id': passkey['id']})
        return {'kind': 'register', 'passkey': passkey}
    user, _ceremony, passkey = await check_sign_in(request, ceremony_id, credential, handoff_id=row.handoff_id)
    sid = await signins.start(user, method='passkey', user_agent='Cognitrix Tauri desktop',
                              address=signins.client_address(request), name=row.device_name or 'Cognitrix desktop app',
                              kind='desktop', approved_by=passkey.name)
    await PasskeyHandoff.update_one({'handoff_id': row.handoff_id}, {'status': 'done', 'sid': sid,
                                                                     'user_id': str(user.id)})
    audit.record('sign_in', f'Signed in to the desktop app with a passkey ({passkey.name})', user_id=str(user.id),
                 actor=str(user.email), outcome='ok')
    return {'kind': 'sign_in', 'account': str(user.email)}


async def collect_handoff(request, secret: str) -> dict[str, Any]:
    """The app asks how its handoff went. A finished sign-in gives its token once."""
    from cognitrix import signins
    from cognitrix.models import User
    from cognitrix.models.sign_in import Passkey, PasskeyHandoff

    _require_handoffs(request)
    row = await PasskeyHandoff.find_one({'secret_hash': _hash(str(secret or ''))}) if secret else None
    if row is None or row.status == 'canceled' or row.collected:
        return {'status': 'expired'}
    if row.status != 'done':
        if int(row.expires_at or 0) <= _now():
            return {'status': 'expired'}
        return {'status': 'opened' if row.status == 'opened' else 'waiting',
                'expires_in': int(row.expires_at) - _now()}
    await PasskeyHandoff.update_one({'handoff_id': row.handoff_id}, {'collected': True})
    if row.kind == 'register':
        passkey = await Passkey.find_one({'passkey_id': row.passkey_id}) if row.passkey_id else None
        return {'status': 'done', 'kind': 'register', 'passkey': view(passkey) if passkey is not None else None}
    user = await User.get(row.user_id)
    if user is None or not row.sid or not await signins.is_live(row.sid, str(user.id)):
        return {'status': 'expired'}
    return {'status': 'done', 'kind': 'sign_in', 'user': user, 'access_token': signins.make_token(user, row.sid)}


async def cancel_handoff(request, secret: str) -> None:
    from cognitrix.models.sign_in import PasskeyHandoff

    _require_handoffs(request)
    row = await PasskeyHandoff.find_one({'secret_hash': _hash(str(secret or ''))}) if secret else None
    if row is not None and row.status != 'done':
        await PasskeyHandoff.update_one({'handoff_id': row.handoff_id}, {'status': 'canceled'})
