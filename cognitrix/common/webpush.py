"""Web push: notifications that reach a browser or an installed web app even
when Cognitrix isn't open.

Implements just what is needed with the ``cryptography`` package: a VAPID
key pair that identifies this server (RFC 8292) and the ``aes128gcm`` message
encryption browsers expect (RFC 8291). The key pair is created on first use
and kept in the data folder.

The server only ever posts to the push services browsers use. A subscription
is an address supplied by a browser, so anything else is refused.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger('cognitrix.log')

MAX_SUBSCRIPTIONS_PER_USER = 10
MAX_PAYLOAD_BYTES = 3000
RECORD_SIZE = 4096
TTL_SECONDS = 24 * 60 * 60

# Push services of Chrome/Edge/Android, Firefox, Safari and Windows.
ALLOWED_HOST_SUFFIXES = (
    'fcm.googleapis.com',
    '.push.services.mozilla.com',
    '.push.apple.com',
    '.notify.windows.com',
)

_background: set[asyncio.Task] = set()


def enabled() -> bool:
    if os.getenv('COGNITRIX_DESKTOP', '').strip() == '1':
        return False  # the desktop app raises its own system notifications
    return os.getenv('COGNITRIX_WEB_PUSH', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def b64url_decode(text: str) -> bytes:
    text = (text or '').strip()
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def endpoint_allowed(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
        return False
    return any(host == suffix.lstrip('.') or host.endswith(suffix if suffix.startswith('.') else f'.{suffix}')
               for suffix in ALLOWED_HOST_SUFFIXES)


# ----------------------------------------------------------------- VAPID keys

_private_key: ec.EllipticCurvePrivateKey | None = None


def _key_file():
    from cognitrix.config import settings

    return settings.workdir / 'vapid_private.pem'


def private_key() -> ec.EllipticCurvePrivateKey:
    global _private_key
    if _private_key is not None:
        return _private_key
    pem = os.getenv('COGNITRIX_VAPID_PRIVATE_KEY', '').strip().replace('\\n', '\n').encode()
    path = _key_file()
    if not pem and path.exists():
        pem = path.read_bytes()
    if pem:
        key = serialization.load_pem_private_key(pem, password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    _private_key = key  # type: ignore[assignment]
    return _private_key  # type: ignore[return-value]


def _raw_public(key) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key() -> str:
    """The key a browser subscribes with (``applicationServerKey``)."""
    return b64url(_raw_public(private_key()))


def _subject() -> str:
    subject = os.getenv('COGNITRIX_VAPID_SUBJECT', '').strip()
    if subject:
        return subject
    public_url = os.getenv('COGNITRIX_PUBLIC_URL', '').strip().rstrip('/')
    return public_url if public_url.startswith('https://') else 'https://github.com/theonlyamos/cognitrix'


def vapid_header(endpoint: str) -> str:
    import jwt

    parts = urlsplit(endpoint)
    token = jwt.encode(
        {'aud': f'{parts.scheme}://{parts.netloc}', 'exp': int(time.time()) + 12 * 3600, 'sub': _subject()},
        private_key(), algorithm='ES256')
    return f'vapid t={token}, k={public_key()}'


# ----------------------------------------------------------------- encryption

def _hkdf(salt: bytes, key: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(key)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            sender: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """One aes128gcm record for the browser that owns ``p256dh`` / ``auth``."""
    receiver_raw = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    receiver = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver_raw)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    sender_raw = _raw_public(sender)
    salt = salt or os.urandom(16)

    shared = sender.exchange(ec.ECDH(), receiver)
    ikm = _hkdf(auth_secret, shared, b'WebPush: info\x00' + receiver_raw + sender_raw, 32)
    key = _hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = _hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    # 0x02 marks the last (here the only) record.
    body = AESGCM(key).encrypt(nonce, payload + b'\x02', None)
    return salt + struct.pack('>I', RECORD_SIZE) + bytes([len(sender_raw)]) + sender_raw + body


def decrypt(message: bytes, receiver: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """What a browser does with ``encrypt``'s output. Used by the tests."""
    salt, key_len = message[:16], message[20]
    sender_raw = message[21:21 + key_len]
    body = message[21 + key_len:]
    receiver_raw = _raw_public(receiver)
    shared = receiver.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_raw))
    ikm = _hkdf(b64url_decode(auth), shared, b'WebPush: info\x00' + receiver_raw + sender_raw, 32)
    key = _hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = _hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    return AESGCM(key).decrypt(nonce, body, None).rstrip(b'\x00')[:-1]


# -------------------------------------------------------------------- sending

def _clip(text: str, limit: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


async def _post(subscription, payload: bytes, client: httpx.AsyncClient) -> int:
    body = encrypt(payload, subscription.p256dh, subscription.auth)
    response = await client.post(subscription.endpoint, content=body, headers={
        'Authorization': vapid_header(subscription.endpoint),
        'Content-Encoding': 'aes128gcm',
        'Content-Type': 'application/octet-stream',
        'TTL': str(TTL_SECONDS),
        'Urgency': 'normal',
    })
    return response.status_code


async def send_to_user(user_id: str, title: str, body: str = '', *, url: str = '/inbox', tag: str = '') -> int:
    """Push to every browser this person switched on. Returns how many accepted it.
    Browsers that unsubscribed are forgotten. Never raises."""
    if not enabled() or not user_id:
        return 0
    from cognitrix.models.push_subscription import PushSubscription

    try:
        rows = await PushSubscription.find({'user_id': str(user_id)}) or []
    except Exception:
        logger.exception('Could not read push subscriptions')
        return 0
    if not rows:
        return 0
    payload = json.dumps({
        'title': _clip(title, 120) or 'Cognitrix',
        'body': _clip(body, 400),
        'url': url if url.startswith('/') and not url.startswith('//') else '/inbox',
        'tag': tag[:60],
    }).encode('utf-8')[:MAX_PAYLOAD_BYTES]
    sent = 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for row in rows:
            if not endpoint_allowed(row.endpoint):
                continue
            try:
                status = await _post(row, payload, client)
            except Exception as exc:
                logger.warning('Push to a browser failed: %s', type(exc).__name__)
                continue
            if status in (404, 410):
                await PushSubscription.delete_many({'id': row.id})
            elif 200 <= status < 300:
                sent += 1
            else:
                logger.warning('A push service answered %s', status)
    return sent


def send_soon(user_id: str, title: str, body: str = '', *, url: str = '/inbox', tag: str = '') -> None:
    """Fire-and-forget ``send_to_user`` from code that must not wait on it."""
    if not enabled() or not user_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(send_to_user(user_id, title, body, url=url, tag=tag))
    _background.add(task)
    task.add_done_callback(_background.discard)
