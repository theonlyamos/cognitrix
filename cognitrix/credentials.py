"""Use saved sign-ins without showing them to the model.

How it works:

1. A person saves a credential (``Credential``) in Settings with the hosts it
   may be sent to. The value is encrypted at rest.
2. The model only ever sees its name and description (List Credentials tool).
   To use it, it writes ``{{secret:NAME}}`` (or ``{{secret:NAME.username}}`` /
   ``{{secret:NAME.password}}`` for a username and password) in an HTTP
   Request (Http Request tool), or names it in that tool's ``credential`` field. MCP server header
   values may hold the same placeholders.
3. The value is filled in at the last moment, only for a request to one of the
   credential's allowed hosts. Anywhere else the call is refused, so a prompt
   injection can't make the agent mail the token to another site.
4. Every tool result is scrubbed of the person's saved values before the model
   sees it, so a site (or a file, or a shell command) that echoes a token back
   does not leak it into the conversation.

Placeholders stay in the stored chat history and approval cards; values never
do.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cognitrix.common.secret_box import seal, unseal_or_empty

logger = logging.getLogger('cognitrix.log')

NAME_RE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
PLACEHOLDER_RE = re.compile(
    r'\{\{\s*secret:([A-Za-z][A-Za-z0-9_]{1,63})(?:\.(value|username|password|access_token))?\s*\}\}'
)
_HOST_RE = re.compile(r'^(\*\.)?([a-z0-9-]+\.)*[a-z0-9-]+(:\d{1,5})?$')
MIN_REDACT_LENGTH = 4
_REDACTION_CACHE_SECONDS = 5.0
_redaction_cache: dict[str, tuple[float, dict[str, str]]] = {}


class SecretUseError(ValueError):
    """A credential was asked for where it may not be used."""


def normalize_name(raw: str) -> str:
    name = re.sub(r'[\s\-]+', '_', str(raw or '').strip()).upper()
    if not NAME_RE.match(name):
        raise ValueError('Use 2 to 64 letters, numbers or underscores for the name, starting with a letter')
    return name


def normalize_host(raw: str) -> str:
    """Turn what a person typed (a host or a URL) into a host pattern."""
    text = str(raw or '').strip().lower()
    if '://' in text:
        text = text.split('://', 1)[1]
    text = text.split('/', 1)[0].split('?', 1)[0]
    text = text.rsplit('@', 1)[-1].rstrip('.')
    if not text or text == '*' or not _HOST_RE.match(text):
        raise ValueError(f"'{raw}' isn't a host name. Use something like api.github.com or *.example.com")
    if text.startswith('*.') and '.' not in text[2:]:
        raise ValueError(f"'{raw}' is too broad. Use something like *.example.com")
    return text


def host_allowed(host: str | None, patterns: list[str]) -> bool:
    """True if ``host`` (optionally with :port) matches one of ``patterns``."""
    host = str(host or '').strip().lower().rstrip('.')
    if not host:
        return False
    bare = host.split(':', 1)[0] if host.count(':') == 1 else host
    for pattern in patterns or []:
        pattern = str(pattern or '').strip().lower()
        if not pattern:
            continue
        if ':' in pattern and not pattern.startswith('['):
            # A pattern with a port only matches that port.
            if host == pattern or (pattern.startswith('*.') and host.endswith(pattern[1:])):
                return True
            continue
        if pattern.startswith('*.'):
            suffix = pattern[1:]
            if bare.endswith(suffix) and len(bare) > len(suffix):
                return True
        elif bare == pattern:
            return True
    return False


def open_secret(row: Any) -> dict[str, str]:
    """The decrypted value(s) of a credential row."""
    text = unseal_or_empty(getattr(row, 'secret', '') or '')
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if v not in (None, '')} if isinstance(data, dict) else {}


def seal_secret(values: dict[str, Any]) -> str:
    clean = {k: v for k, v in values.items() if v not in (None, '')}
    return seal(json.dumps(clean)) if clean else ''


def hint_for(values: dict[str, Any]) -> str:
    main = str(values.get('value') or values.get('password') or values.get('access_token') or '')
    return f'…{main[-4:]}' if len(main) >= 8 else ''


def _primary_value(kind: str, values: dict[str, str]) -> str:
    if kind == 'oauth':
        return values.get('access_token', '')
    if kind == 'basic':
        return values.get('password', '')
    return values.get('value', '')


def secret_strings(row: Any, values: dict[str, str] | None = None) -> list[str]:
    """Every string that would give the credential away, for scrubbing output."""
    values = open_secret(row) if values is None else values
    found = [v for k, v in values.items() if k in ('value', 'password', 'access_token', 'refresh_token')]
    if row.kind == 'basic' and values.get('username') and values.get('password'):
        pair = f"{values['username']}:{values['password']}"
        found.append(base64.b64encode(pair.encode('utf-8')).decode('ascii'))
    return [v for v in found if len(v) >= MIN_REDACT_LENGTH]


async def load_rows(owner: str | None) -> list[Any]:
    if not owner:
        return []
    from cognitrix.models.credential import Credential

    try:
        return list(await Credential.find({'owner_id': str(owner)}) or [])
    except Exception:
        logger.exception('Could not read saved credentials')
        return []


async def find_row(owner: str | None, name: str) -> Any | None:
    if not owner:
        return None
    for row in await load_rows(owner):
        if row.name == name:
            return row
    return None


def describe(row: Any) -> dict[str, Any]:
    """What the model (and the settings list) may know about a credential."""
    return {
        'name': row.name,
        'kind': row.kind,
        'description': row.description or '',
        'allowed_hosts': list(row.allowed_hosts or []),
    }


@dataclass
class Resolver:
    """Fills placeholders for one tool call, remembering what it used."""

    owner: str | None
    rows: dict[str, Any]
    used: set[str] = field(default_factory=set)
    _values: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    async def for_owner(cls, owner: str | None) -> Resolver:
        rows = await load_rows(owner)
        return cls(owner=owner, rows={row.name: row for row in rows})

    def _row(self, name: str, host: str | None) -> Any:
        row = self.rows.get(name) or self.rows.get(name.upper())
        if row is None:
            raise SecretUseError(
                f"There is no saved credential called {name}. Use List Credentials to see the saved ones."
            )
        if not host_allowed(host, list(row.allowed_hosts or [])):
            allowed = ', '.join(row.allowed_hosts or []) or 'no hosts'
            raise SecretUseError(
                f"{row.name} may only be sent to {allowed}, not to {host or 'this address'}."
            )
        return row

    def _open(self, row: Any) -> dict[str, str]:
        if row.name not in self._values:
            self._values[row.name] = open_secret(row)
        return self._values[row.name]

    def value(self, name: str, part: str | None, host: str | None) -> str:
        row = self._row(name, host)
        values = self._open(row)
        if part in (None, 'value'):
            text = _primary_value(row.kind, values)
        else:
            text = values.get(part, '')
        if not text:
            raise SecretUseError(f"{row.name} has no {part or 'value'} saved.")
        self.used.add(row.name)
        return text

    @staticmethod
    def names_in(text: Any) -> set[str]:
        if not isinstance(text, str):
            return set()
        return {m.group(1) for m in PLACEHOLDER_RE.finditer(text)}

    def fill(self, text: Any, host: str | None) -> Any:
        """Replace every placeholder in ``text`` (a string) for ``host``."""
        if not isinstance(text, str) or '{{' not in text:
            return text
        return PLACEHOLDER_RE.sub(lambda m: self.value(m.group(1), m.group(2), host), text)

    def auth_headers(self, name: str, host: str | None) -> dict[str, str]:
        """Headers that sign a request in with credential ``name``."""
        row = self._row(name, host)
        values = self._open(row)
        self.used.add(row.name)
        if row.kind == 'basic':
            if not values.get('username') or not values.get('password'):
                raise SecretUseError(f"{row.name} needs both a username and a password.")
            pair = f"{values['username']}:{values['password']}".encode()
            return {'Authorization': 'Basic ' + base64.b64encode(pair).decode('ascii')}
        token = _primary_value(row.kind, values)
        if not token:
            raise SecretUseError(f"{row.name} has no value saved.")
        header = (row.header or '').strip()
        if header and header.lower() != 'authorization':
            return {header: token}
        return {'Authorization': f'Bearer {token}'}

    def redaction_map(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for row in self.rows.values():
            for text in secret_strings(row, self._open(row)):
                found[text] = row.name
        return found

    async def mark_used(self) -> None:
        if not self.used:
            return
        from cognitrix.models.credential import Credential

        stamp = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
        for name in self.used:
            row = self.rows.get(name)
            if row is None:
                continue
            try:
                await Credential.update_one({'id': row.id}, {'last_used_at': stamp})
            except Exception:
                logger.debug('Could not stamp last_used_at for %s', name, exc_info=True)


def redact(text: str, secrets_map: dict[str, str]) -> str:
    """Replace every saved value in ``text`` with a short marker."""
    if not text or not secrets_map:
        return text
    # Longest first, so a value that contains another is replaced whole.
    for value in sorted(secrets_map, key=len, reverse=True):
        if value in text:
            text = text.replace(value, f'[{secrets_map[value]} hidden]')
    return text


async def redaction_map_for(owner: str | None) -> dict[str, str]:
    """Saved values of ``owner``, cached for a few seconds per owner."""
    if not owner:
        return {}
    now = time.monotonic()
    cached = _redaction_cache.get(owner)
    if cached and now - cached[0] < _REDACTION_CACHE_SECONDS:
        return cached[1]
    resolver = await Resolver.for_owner(owner)
    found = resolver.redaction_map()
    _redaction_cache[owner] = (now, found)
    return found


def forget_cached(owner: str | None) -> None:
    _redaction_cache.pop(str(owner or ''), None)


async def fill_mcp_headers(headers: dict[str, str] | None, owner: str | None, url: str | None) -> dict[str, str] | None:
    """Fill ``{{secret:NAME}}`` in an MCP server's headers for its owner.

    Shared servers have no owner, so their headers can't use anyone's vault.
    """
    if not headers:
        return headers
    if not any(Resolver.names_in(v) for v in headers.values()):
        return headers
    from urllib.parse import urlparse

    host = urlparse(url or '').netloc.rsplit('@', 1)[-1] or None
    resolver = await Resolver.for_owner(owner)
    filled = {k: resolver.fill(v, host) for k, v in headers.items()}
    await resolver.mark_used()
    return filled
