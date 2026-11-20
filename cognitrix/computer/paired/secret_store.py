"""Where a paired PC keeps its token: the system keychain when there is one.

- Windows: Credential Manager (advapi32 CredWrite/CredRead/CredDelete).
- macOS: the login Keychain, through the ``security`` tool.
- Linux: the Secret Service through ``secret-tool``, when it's installed.

Anything else, or a keychain that refuses, falls back to the config file
(``paired.json``, readable by this user only). ``COGNITRIX_PAIRED_KEYCHAIN=0``
turns the keychain off.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger('cognitrix.paired')

SERVICE = 'Cognitrix paired PC'
ACCOUNT = 'device-token'


def enabled() -> bool:
    return os.environ.get('COGNITRIX_PAIRED_KEYCHAIN', '1') != '0'


def where() -> str:
    """'Credential Manager', 'Keychain', 'Secret Service' or '' (no keychain here)."""
    if not enabled():
        return ''
    if sys.platform == 'win32':
        return 'Credential Manager'
    if sys.platform == 'darwin' and shutil.which('security'):
        return 'Keychain'
    if sys.platform.startswith('linux') and shutil.which('secret-tool') and os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        return 'Secret Service'
    return ''


def save(token: str) -> bool:
    """Put the token in the keychain. False when it couldn't."""
    kind = where()
    try:
        if kind == 'Credential Manager':
            return _win_write(token)
        if kind == 'Keychain':
            return _run(['security', 'add-generic-password', '-U', '-s', SERVICE, '-a', ACCOUNT, '-w', token]) == ''
        if kind == 'Secret Service':
            return _run(['secret-tool', 'store', '--label', SERVICE, 'service', SERVICE, 'account', ACCOUNT],
                        stdin=token) == ''
    except Exception:
        logger.debug('The keychain refused the token', exc_info=True)
    return False


def load() -> str | None:
    kind = where()
    try:
        if kind == 'Credential Manager':
            return _win_read()
        if kind == 'Keychain':
            return _run(['security', 'find-generic-password', '-s', SERVICE, '-a', ACCOUNT, '-w']) or None
        if kind == 'Secret Service':
            return _run(['secret-tool', 'lookup', 'service', SERVICE, 'account', ACCOUNT]) or None
    except Exception:
        logger.debug('Could not read the token from the keychain', exc_info=True)
    return None


def delete() -> None:
    kind = where()
    try:
        if kind == 'Credential Manager':
            _win_delete()
        elif kind == 'Keychain':
            _run(['security', 'delete-generic-password', '-s', SERVICE, '-a', ACCOUNT])
        elif kind == 'Secret Service':
            _run(['secret-tool', 'clear', 'service', SERVICE, 'account', ACCOUNT])
    except Exception:
        logger.debug('Could not remove the token from the keychain', exc_info=True)


def _run(cmd: list[str], stdin: str | None = None) -> str | None:
    """Output of a keychain tool, or None when it failed."""
    out = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=15)
    return out.stdout.strip() if out.returncode == 0 else None


# -- Windows Credential Manager -------------------------------------------------

def _win():
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
            ('Comment', wintypes.LPWSTR), ('LastWritten', FILETIME), ('CredentialBlobSize', wintypes.DWORD),
            ('CredentialBlob', ctypes.POINTER(ctypes.c_char)), ('Persist', wintypes.DWORD),
            ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
            ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR),
        ]

    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    return ctypes, CREDENTIAL, advapi


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


def _target() -> str:
    return f'{SERVICE}/{ACCOUNT}'


def _win_write(token: str) -> bool:
    ctypes, CREDENTIAL, advapi = _win()
    blob = token.encode('utf-16-le')
    cred = CREDENTIAL()
    cred.Type = _CRED_TYPE_GENERIC
    cred.TargetName = _target()
    cred.UserName = ACCOUNT
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_char))
    cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
    return bool(advapi.CredWriteW(ctypes.byref(cred), 0))


def _win_read() -> str | None:
    ctypes, CREDENTIAL, advapi = _win()
    pcred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(_target(), _CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    try:
        size = pcred.contents.CredentialBlobSize
        raw = ctypes.string_at(pcred.contents.CredentialBlob, size)
        return raw.decode('utf-16-le') or None
    finally:
        advapi.CredFree(pcred)


def _win_delete() -> None:
    ctypes, _, advapi = _win()
    advapi.CredDeleteW(_target(), _CRED_TYPE_GENERIC, 0)
