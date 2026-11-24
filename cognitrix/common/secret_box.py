"""Encrypt secrets before they are written to the database.

Saved sign-ins, MCP header tokens and pasted voice keys go through ``seal``
on the way in and ``unseal`` on the way out, so a copy of the database (a
backup, a stolen laptop's ``cognitrix.db``) does not give away the secrets.

Values are Fernet tokens (AES-128-CBC + HMAC-SHA256) with an ``enc:v1:``
prefix. Text without the prefix is treated as an old plain-text value and
passed through unchanged, so rows written before encryption keep working and
are re-sealed on startup (see ``seal_legacy_rows``).

The key is made once and kept in ``<data folder>/secrets.key`` (owner read
and write only); nobody sets it. Keys it replaced are kept one per line in
``secrets.old_keys`` so values sealed with them still open.

Older setups set the keys as environment variables (``COGNITRIX_SECRETS_KEY``,
or a key derived from ``JWT_SECRET_KEY`` in production). On the first start
after upgrading, ``adopt_environment_keys`` copies those into the key files,
so saved secrets and sign-ins keep working; after that the environment is
not read again and the variables can be removed.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import time
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


def _old_keys_file() -> Path:
    return _key_file().with_name('secrets.old_keys')


def jwt_key_file() -> Path:
    """Signs sign-in tokens (config.py reads it)."""
    return _key_file().with_name('jwt_secret')


def _adopted_marker() -> Path:
    return _key_file().with_name('.keys-adopted')


RAW = 'fernet:'   # a key file line holding a ready Fernet key, not text to stretch


def _fernet_key(material: str) -> list[bytes]:
    """The Fernet keys one line of a key file stands for."""
    material = material.strip()
    if material.startswith(RAW):
        return [material[len(RAW):].encode('ascii')]
    return [_stretch(material, 'secrets')]


def _write_owner_only(path: Path, text: str, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(str(path), flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_or_create_key(path: Path, *, attempts: int = 50) -> str:
    """The key kept in ``path``, made (random, owner-only) the first time.
    Safe when the web server and the worker start together: the key is
    written to a temporary file and linked into place only if no key is there
    yet, so a process never sees half a key or replaces another's."""
    for _ in range(attempts):
        try:
            text = path.read_text(encoding='utf-8').strip()
        except FileNotFoundError:
            text = None
        if text:
            return text
        if text is None:
            candidate = secrets.token_urlsafe(32)
            if _create_if_missing(path, candidate):
                return candidate
            continue  # another process won: read its key
        time.sleep(0.05)  # an empty file: another process may be writing it
    # Still empty: an earlier crash left it so. Make a key now.
    text = secrets.token_urlsafe(32)
    _write_owner_only(path, text)
    return text


def _create_if_missing(path: Path, text: str) -> bool:
    """Put ``text`` at ``path`` only if nothing is there. True if it was ours."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{secrets.token_hex(4)}')
    _write_owner_only(tmp, text)
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    except OSError:
        # No hard links here (some network or Windows filesystems): create
        # exclusively instead; a reader that sees it empty waits and reads again.
        try:
            _write_owner_only(path, text, exclusive=True)
            return True
        except FileExistsError:
            return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def adopt_environment_keys() -> None:
    """Once, on the first start after upgrading: keep the keys an older setup
    gave as environment variables, by writing them to the key files. Saved
    secrets and sign-ins then still open, and the variables can be removed.
    The environment is never read for keys after this."""
    marker = _adopted_marker()
    try:
        if marker.exists():
            return
        jwt = os.environ.get('JWT_SECRET_KEY', '').strip()
        secrets_env = os.environ.get('COGNITRIX_SECRETS_KEY', '').strip()
        production = os.environ.get('COGNITRIX_ENV', '').strip() == 'production'
        key_file = _key_file()
        from_jwt = RAW + _stretch(jwt, 'secrets-from-jwt').decode('ascii') if jwt else ''
        primary = ''
        if secrets_env:
            primary = secrets_env
        elif from_jwt and (production or not key_file.exists()):
            # Production used to derive the key from JWT_SECRET_KEY. Without a
            # key file this was such a server, even when COGNITRIX_ENV is gone.
            primary = from_jwt
        old = [k.strip() for k in os.environ.get('COGNITRIX_SECRETS_OLD_KEYS', '').split(',') if k.strip()]
        old_lines: list[str] = [from_jwt] if from_jwt and from_jwt != primary else []
        for key in old:
            old_lines += [key, RAW + _stretch(key, 'secrets-from-jwt').decode('ascii')]
        if primary:
            previous = key_file.read_text(encoding='utf-8').strip() if key_file.exists() else ''
            if previous and previous != primary:
                old_lines.append(previous)
            _write_owner_only(key_file, primary)
        if old_lines:
            old_file = _old_keys_file()
            existing = old_file.read_text(encoding='utf-8').splitlines() if old_file.exists() else []
            lines = existing + [line for line in old_lines if line not in existing]
            _write_owner_only(old_file, '\n'.join(lines) + '\n')
        if jwt:
            _write_owner_only(jwt_key_file(), jwt)
        _write_owner_only(marker, 'Keys from environment variables were copied to the key files once.\n')
        if primary or old_lines or jwt:
            logger.warning(
                'Copied the keys set as environment variables into %s. They are not read from the '
                'environment any more; you can remove JWT_SECRET_KEY, COGNITRIX_SECRETS_KEY and '
                'COGNITRIX_SECRETS_OLD_KEYS.', key_file.parent)
    except OSError as exc:
        logger.warning("Couldn't copy keys from environment variables: %s", exc)


def _primary_key() -> bytes:
    adopt_environment_keys()
    path = _key_file()
    try:
        return _fernet_key(read_or_create_key(path))[0]
    except OSError as exc:
        raise SecretBoxError(f"Couldn't read or create the secrets key at {path}: {exc}") from exc


def _old_keys() -> list[bytes]:
    try:
        lines = _old_keys_file().read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    keys: list[bytes] = []
    for line in lines:
        if line.strip():
            keys += _fernet_key(line)
    return keys


def _get_box() -> MultiFernet:
    global _box
    with _lock:
        if _box is None:
            keys = [Fernet(_primary_key())]
            for old in _old_keys():
                try:
                    keys.append(Fernet(old))
                except ValueError:
                    logger.warning('Skipping a malformed line in %s', _old_keys_file())
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
            'A saved secret could not be decrypted. Was secrets.key in the data folder replaced?'
        ) from exc


def unseal_or_empty(value: str | None) -> str:
    """Like ``unseal`` but logs and returns '' instead of raising."""
    try:
        return unseal(value)
    except SecretBoxError:
        logger.error('A saved secret could not be decrypted; treating it as missing.')
        return ''
