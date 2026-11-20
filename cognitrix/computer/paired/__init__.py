"""Paired computers: a person's own PC that their agents can work on.

A web user pairs a PC once. A small connector on the PC (the desktop app, or
``cognitrix connect``) keeps one outgoing WebSocket open to the server, so the
PC needs no open ports. When a chat is set to that PC, the shell and file
tools (Bash, Bash Output, Kill Shell, Read, Write, Edit, Glob, Grep) run there
instead of on the server, inside the folders the PC's owner shared.

The limits live on the PC: the connector only runs a tool when the owner
switched its power on (files, commands) and only inside the shared folders.
Approvals still happen in the chat, before the call is sent.

- ``hub.py``: the server side, one connection per online PC.
- ``turn.py``: which PC a chat turn uses, and sending tool calls to it.
- ``connector.py``: the PC side.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

TOKEN_PREFIX = 'cgxd_'
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # no 0/O, 1/I/L
CODE_TTL_S = 600
# A paired PC that hasn't connected for this long has to pair again.
IDLE_DAYS = 30
PROTOCOL = 1

FILES = 'files'
COMMANDS = 'commands'
POWERS = (FILES, COMMANDS)

# The tools that run on a paired PC, and the power each one needs.
PC_TOOLS: dict[str, str] = {
    'Bash': COMMANDS,
    'Bash Output': COMMANDS,
    'Kill Shell': COMMANDS,
    'Read': FILES,
    'Write': FILES,
    'Edit': FILES,
    'Glob': FILES,
    'Grep': FILES,
}
# Registered (snake case) names of the same tools, for adding them to a turn.
PC_TOOL_KEYS: dict[str, str] = {
    'bash': COMMANDS, 'bash_output': COMMANDS, 'kill_shell': COMMANDS,
    'read': FILES, 'write': FILES, 'edit': FILES, 'glob': FILES, 'grep': FILES,
}
# Seconds the server waits for a call's answer (Bash may run for 10 minutes).
CALL_TIMEOUT_S = {'Bash': 660.0}
DEFAULT_CALL_TIMEOUT_S = 150.0
MAX_RESULT_CHARS = 200_000


def tool_power(name: Any) -> str | None:
    """The power a tool needs on a paired PC, or None when it doesn't run there."""
    return PC_TOOLS.get(str(name or ''))


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token).encode('utf-8')).hexdigest()


def new_code() -> str:
    raw = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f'{raw[:4]}-{raw[4:]}'


def normalize_code(code: Any) -> str:
    raw = ''.join(ch for ch in str(code or '').upper() if ch.isalnum())
    return f'{raw[:4]}-{raw[4:8]}' if len(raw) == 8 else ''


def clean_powers(value: Any) -> list[str]:
    items = value if isinstance(value, list | tuple | set) else []
    return [p for p in POWERS if p in {str(v) for v in items}]


def clean_folders(value: Any) -> list[dict[str, Any]]:
    """The folder list a PC reports: [{path, writable}] (shown on the web only;
    the PC enforces its own copy)."""
    out = []
    for item in (value if isinstance(value, list) else [])[:50]:
        if isinstance(item, str):
            item = {'path': item}
        if not isinstance(item, dict):
            continue
        path = str(item.get('path') or '').strip()[:1000]
        if path:
            out.append({'path': path, 'writable': bool(item.get('writable', True))})
    return out
