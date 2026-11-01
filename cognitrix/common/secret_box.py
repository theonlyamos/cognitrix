"""Encrypt secrets before they are written to the database.

Saved sign-ins, MCP header tokens and pasted voice keys go through ``seal``
on the way in and ``unseal`` on the way out, so a copy of the database (a
backup, a stolen laptop's ``cognitrix.db``) does not give away the secrets.

Values are Fernet tokens (AES-128-CBC + HMAC-SHA256) with an ``enc:v1:``
prefix. Text without the prefix is treated as an old plain-text value and
passed through unchanged, so rows written before encryption keep working and
are re-sealed on startup (see ``seal_legacy_rows``).

Where the key comes from, first match wins:

1. ``COGNITRIX_SECRETS_KEY``: any string; it is stretched to a Fernet key.
2. Outside production, a key generated once and kept in
   ``~/.cognitrix/secrets.key`` (owner read/write only).
3. In production without (1), a key derived from ``JWT_SECRET_KEY``, so an
   existing deployment keeps starting. Set ``COGNITRIX_SECRETS_KEY`` so that
   rotating the JWT secret does not lock the saved secrets.

``COGNITRIX_SECRETS_OLD_KEYS`` (comma separated) still decrypts values sealed
with earlier keys, which lets an operator rotate the key.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger('cognitrix.log')

PREFIX = 'enc:v1:'

_lock = threading.Lock()
_box: MultiFernet | None = None


class SecretBoxError(RuntimeError):
    """A sealed value could not be opened (wrong or missing key)."""


def _stretch(material: str, purpose: str) -> bytes:
    digest = hashlib.sha256(f'cognitrix:{purpose}:'.encode() + material.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def _key_file() -> Path:
    return Path.home() / '.cognitrix' / 'secrets.key'


def _primary_key() -> bytes:
    env_key = os.getenv('COGNITRIX_SECRETS_KEY', '').strip()
    if env_key:
        return _stretch(env_key, 'secrets')

    if os.getenv('COGNITRIX_ENV', 'development') == 'production':
        jwt = os.getenv('JWT_SECRET_KEY', '').strip()
        if jwt:
            logger.warning(
                "COGNITRIX_SECRETS_KEY is not set; saved secrets are encrypted with a key "
                "derived from JWT_SECRET_KEY. Set COGNITRIX_SECRETS_KEY before rotating it."
            )
            return _stretch(jwt, 'secrets-from-jwt')
        raise SecretBoxError('COGNITRIX_SECRETS_KEY must be set when COGNITRIX_ENV=production.')

    path = _key_file()
    try:
        if path.exists():
            text = path.read_text(encoding='utf-8').strip()
            if text:
                return _stretch(text, 'secrets')
        path.parent.mkdir(parents=True, exist_ok=True)
        text = secrets.token_urlsafe(32)
        # Create with owner-only permissions from the start.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        return _stretch(text, 'secrets')
    except FileExistsError:
        # Another process created it between our check and our write.
        return _stretch(path.read_text(encoding='utf-8').strip(), 'secrets')
    except OSError as exc:
        raise SecretBoxError(f"Couldn't read or create the secrets key at {path}: {exc}") from exc


def _get_box() -> MultiFernet:
    global _box
    with _lock:
        if _box is None:
            keys = [Fernet(_primary_key())]
            for old in os.getenv('COGNITRIX_SECRETS_OLD_KEYS', '').split(','):
                if old.strip():
                    keys.append(Fernet(_stretch(old.strip(), 'secrets')))
            _box = MultiFernet(keys)
        return _box


def reset_key_cache() -> None:
    """Forget the loaded key (tests, or after changing the environment)."""
    global _box
    with _lock:
        _box = None


def is_sealed(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(value: str | None) -> str:
    """Encrypt ``value``. Empty stays empty; an already sealed value is kept."""
    if not value:
        return ''
    if is_sealed(value):
        return value
    token = _get_box().encrypt(value.encode('utf-8')).decode('ascii')
    return PREFIX + token


def unseal(value: str | None) -> str:
    """Decrypt ``value``. Plain text (written before encryption) comes back as is."""
    if not value:
        return ''
    if not is_sealed(value):
        return value
    try:
        return _get_box().decrypt(value[len(PREFIX):].encode('ascii')).decode('utf-8')
    except InvalidToken as exc:
        raise SecretBoxError(
            'A saved secret could not be decrypted. Was COGNITRIX_SECRETS_KEY changed?'
        ) from exc


def unseal_or_empty(value: str | None) -> str:
    """Like ``unseal`` but logs and returns '' instead of raising."""
    try:
        return unseal(value)
    except SecretBoxError:
        logger.error('A saved secret could not be decrypted; treating it as missing.')
        return ''
