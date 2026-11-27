"""What an app's own server code may use: connected apps, saved sign-ins, secrets.

An app's pages can already ask whoever is looking for their connected apps
(``window.cognitrix.apps``, see ``bridge.py``). That needs a person in front of
it. This module is the other half, for work with nobody there (a nightly sync,
a webhook, a form that files a ticket): the **owner** picks, on the app's page,
what the app's server code may use, and it then acts as the owner.

Three things, all off until the owner turns them on:

- **Connected apps** (MCP servers, signed-in apps, Composio): per connected app,
  ``read`` (only tools that change nothing) or ``all`` (changes too, with nobody
  asked each time, which is why the owner has to pick it).
- **Saved sign-ins**: the app never gets the value. It asks Cognitrix to send
  the request, with ``{{secret:NAME}}`` or ``credential`` where the value goes,
  and the same rules as the Http Request tool apply (only the credential's
  hosts, never across a redirect, echoed values hidden).
- **Secrets**: values only this app needs. These *are* handed to the app, as
  environment variables, when a version starts.

The app reaches the first two with the key it is started with
(``COGNITRIX_API_URL`` / ``COGNITRIX_API_KEY``) at ``/api/v1/app-access``; every
call is checked against the owner's choices as they are at that moment, so
taking something away works at once.

The agent that builds an app can only *ask* (Publish App ``connected_apps``,
``credentials``, ``secrets``); the asks show on the app's page. When someone
other than the owner publishes the app's code, everything here is paused until
the owner looks, because that code would otherwise act as the owner.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from cognitrix.common.secret_box import seal, unseal_or_empty
from cognitrix.hosted_apps.models import ACCESS_LEVELS, HostedApp, HostedAppAccess

logger = logging.getLogger('cognitrix.apps')

MAX_SECRETS = 30
MAX_SECRET_LENGTH = 8000
MAX_RESPONSE = 5 * 1024 * 1024
_SECRET_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}')
_RESERVED = {'PORT', 'HOST', 'PATH', 'HOME', 'APP_DATA_DIR', 'APP_VERSION', 'APP_TITLE', 'APP_TEST'}
_WANT_KINDS = ('apps', 'credentials', 'secrets')


class AccessError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> str:
    from cognitrix.hosted_apps.store import now

    return now()


def _fold(name: Any) -> str:
    return ' '.join(str(name or '').split()).casefold()


def _cred_key(name: Any) -> str:
    """A saved sign-in's name the way the vault keeps it ('github token' -> GITHUB_TOKEN)."""
    return re.sub(r'[\s\-]+', '_', str(name or '').strip()).upper()


# ------------------------------------------------------------------ the row

async def row_for(app: HostedApp, create: bool = False) -> HostedAppAccess | None:
    rows = await HostedAppAccess.find({'app_id': str(app.id)}) or []
    if rows:
        return rows[0]
    if not create:
        return None
    stamp = _now()
    # An app that already had a key got it from Publish App's use_cognitrix_api.
    row = HostedAppAccess(app_id=str(app.id), full_api=bool(app.api_key_id), created=stamp, updated=stamp)
    await row.save()
    return (await HostedAppAccess.find({'app_id': str(app.id)}) or [row])[0]


async def _set(row: HostedAppAccess, **changes: Any) -> HostedAppAccess:
    changes['updated'] = _now()
    await HostedAppAccess.update_one({'id': str(row.id)}, changes)
    for key, value in changes.items():
        setattr(row, key, value)
    return row


async def forget(app: HostedApp) -> None:
    await HostedAppAccess.delete_many({'app_id': str(app.id)})


def open_secrets(row: HostedAppAccess | None) -> dict[str, str]:
    text = unseal_or_empty(row.secrets) if row is not None and row.secrets else ''
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def granted(row: HostedAppAccess | None) -> bool:
    """The owner allowed a connected app or a saved sign-in."""
    return bool(row is not None and (row.apps or row.credentials))


def has_anything(row: HostedAppAccess | None) -> bool:
    return granted(row) or bool(row is not None and row.secrets)


def wants_key(row: HostedAppAccess | None) -> bool:
    """The app should be started with a key: it uses something, or asked to (so that
    saying yes later needs no restart)."""
    if row is None:
        return False
    return bool(row.full_api or granted(row) or row.wants.get('apps') or row.wants.get('credentials'))


def needs_restart(app: HostedApp, row: HostedAppAccess | None) -> bool:
    if row is None or not app.version:
        return False
    return bool(row.pending_restart or (granted(row) and not app.api_key_id))


# ------------------------------------------------------------------ publishing

def check_secret_name(raw: Any) -> str:
    name = str(raw or '').strip()
    if not _SECRET_NAME.fullmatch(name):
        raise AccessError('bad_name', f"'{raw}' can't be a secret's name. Use letters, numbers and underscores, "
                          'like STRIPE_KEY.')
    if name.upper().startswith('COGNITRIX_') or name.upper() in _RESERVED:
        raise AccessError('reserved_name', f'{name} is a name Cognitrix sets itself. Pick another.')
    return name


def _names(values: Any, limit: int = 20) -> list[str]:
    if isinstance(values, str):
        values = [v for v in re.split(r'[,\n]', values)]
    out: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        text = ' '.join(str(value or '').split())[:80]
        if text and _fold(text) not in {_fold(o) for o in out}:
            out.append(text)
    return out[:limit]


async def ask(app: HostedApp, *, apps: Any = None, credentials: Any = None, secrets: Any = None) -> dict[str, list]:
    """Record what the agent building the app says its server code needs. Each
    kind given replaces the earlier ask of that kind. Returns what is still waiting
    for the owner."""
    given = {'apps': apps, 'credentials': credentials, 'secrets': secrets}
    if all(v is None for v in given.values()):
        row = await row_for(app)
        return await waiting(app, row) if row is not None else {k: [] for k in _WANT_KINDS}
    row = await row_for(app, create=True)
    wants = {k: list(row.wants.get(k) or []) for k in _WANT_KINDS}
    for kind, value in given.items():
        if value is None:
            continue
        names = _names(value)
        if kind == 'secrets':
            names = [check_secret_name(n) for n in names]
        wants[kind] = names
    await _set(row, wants=wants)
    return await waiting(app, row)


async def waiting(app: HostedApp, row: HostedAppAccess | None) -> dict[str, list]:
    """What the agent asked for that the owner has not given yet."""
    if row is None:
        return {k: [] for k in _WANT_KINDS}
    configs = {_fold(c.name): str(c.id) for c in await owner_apps(app.owner_id)}
    allowed_creds = {_cred_key(n) for n in row.credentials}
    have = set(open_secrets(row))
    return {
        'apps': [n for n in row.wants.get('apps') or [] if configs.get(_fold(n)) not in row.apps],
        'credentials': [n for n in row.wants.get('credentials') or []
                        if _cred_key(n) not in allowed_creds],
        'secrets': [n for n in row.wants.get('secrets') or [] if n not in have],
    }


async def before_start(app: HostedApp, publisher: str, use_cognitrix_api: bool) -> tuple[HostedAppAccess | None, dict]:
    """Called when a version is about to start. Returns (the row, the secrets to
    start it with)."""
    row = await row_for(app, create=bool(use_cognitrix_api or app.api_key_id))
    if row is None:
        return None, {}
    changes: dict[str, Any] = {}
    if use_cognitrix_api and not row.full_api:
        changes['full_api'] = True
    if publisher != app.owner_id and has_anything(row) and not row.paused:
        # Someone else's code is about to run as the owner: nothing is used until the owner says so.
        changes['paused'] = True
    if changes:
        await _set(row, **changes)
    return row, ({} if row.paused else open_secrets(row))


async def started(row: HostedAppAccess | None, ok: bool) -> None:
    if row is None:
        return
    changes: dict[str, Any] = {'pending_key_id': ''}
    if ok:
        changes['pending_restart'] = False
    await _set(row, **changes)


async def starting_with(row: HostedAppAccess | None, key_id: str) -> None:
    if row is not None and key_id:
        await _set(row, pending_key_id=key_id)


# ------------------------------------------------------------------ the owner's side

async def owner_apps(owner: str) -> list:
    """The owner's connected apps that are switched on."""
    from cognitrix.mcp import composio
    from cognitrix.mcp.registry import config_from_row
    from cognitrix.models.mcp_server import MCPServer

    rows = await MCPServer.find({'owner_id': str(owner)}) or []
    return [config_from_row(r) for r in rows if getattr(r, 'enabled', True) and not composio.is_account(r)]


async def describe(app: HostedApp) -> dict[str, Any]:
    """Everything the owner's Access tab shows. Never a value."""
    from cognitrix import credentials as vault

    row = await row_for(app)
    wants = (row.wants if row is not None else {}) or {}
    asked_apps = {_fold(n) for n in wants.get('apps') or []}
    asked_creds = {_cred_key(n) for n in wants.get('credentials') or []}
    levels = dict(row.apps) if row is not None else {}
    allowed = {_cred_key(n) for n in (row.credentials if row is not None else [])}

    configs = await owner_apps(app.owner_id)
    apps = [{'id': str(c.id), 'name': c.name, 'account': getattr(c, 'account', None) or '',
             'level': levels.get(str(c.id)) if levels.get(str(c.id)) in ACCESS_LEVELS else '',
             'asked': _fold(c.name) in asked_apps} for c in configs]
    known_apps = {_fold(c.name) for c in configs}
    creds = []
    rows = await vault.load_rows(app.owner_id)
    for cred in sorted(rows, key=lambda r: r.name):
        info = vault.describe(cred)
        creds.append({**info, 'allowed': _cred_key(cred.name) in allowed, 'asked': _cred_key(cred.name) in asked_creds})
    known_creds = {_cred_key(r.name) for r in rows}
    values = open_secrets(row)
    asked_secrets = [n for n in wants.get('secrets') or [] if n not in values]
    return {
        'apps': sorted(apps, key=lambda a: a['name'].lower()),
        'credentials': creds,
        'secrets': [{'name': name, 'hint': f'…{value[-4:]}' if len(value) >= 8 else ''}
                    for name, value in sorted(values.items())],
        'asked': {
            # Asked for, but the owner has nothing by that name yet.
            'apps': [n for n in wants.get('apps') or [] if _fold(n) not in known_apps],
            'credentials': [n for n in wants.get('credentials') or []
                            if _cred_key(n) not in known_creds],
            'secrets': asked_secrets,
        },
        'paused': bool(row.paused) if row is not None else False,
        'needs_restart': needs_restart(app, row),
        'can_restart': bool(app.folder),
        'full_api': bool(row.full_api) if row is not None else bool(app.api_key_id),
    }


async def set_access(app: HostedApp, *, apps: dict | None = None, credentials: list | None = None,
                     paused: bool | None = None) -> HostedAppAccess:
    """The owner's choices. ``apps`` maps a connected app's id to 'read', 'all' or '' (off)."""
    from cognitrix import credentials as vault

    row = await row_for(app, create=True)
    changes: dict[str, Any] = {}
    if apps is not None:
        mine = {str(c.id) for c in await owner_apps(app.owner_id)}
        levels = {k: v for k, v in dict(row.apps).items() if k in mine}
        for ref, level in apps.items():
            ref = str(ref)
            if ref not in mine:
                raise AccessError('no_app', 'That connected app is not one of yours.', 404)
            if not level:
                levels.pop(ref, None)
            elif level in ACCESS_LEVELS:
                levels[ref] = level
            else:
                raise AccessError('bad_level', "A connected app is off, 'read' or 'all'.")
        changes['apps'] = levels
    if credentials is not None:
        known = {_cred_key(r.name): r.name for r in await vault.load_rows(app.owner_id)}
        picked = []
        for name in credentials:
            real = known.get(_cred_key(name))
            if real is None:
                raise AccessError('no_credential', f'You have no saved sign-in called {name}.', 404)
            if real not in picked:
                picked.append(real)
        changes['credentials'] = picked
    if paused is not None:
        changes['paused'] = bool(paused)
        if not paused and row.paused and row.secrets:
            changes['pending_restart'] = True  # the paused version was started without its secrets
    if changes:
        await _set(row, **changes)
    return row


async def set_secrets(app: HostedApp, *, put: dict | None = None, remove: list | None = None) -> HostedAppAccess:
    row = await row_for(app, create=True)
    values = open_secrets(row)
    before = dict(values)
    for name in remove or []:
        values.pop(str(name), None)
    for raw, value in (put or {}).items():
        name = check_secret_name(raw)
        text = str(value if value is not None else '')
        if not text:
            raise AccessError('empty', f'{name} needs a value.')
        if len(text) > MAX_SECRET_LENGTH:
            raise AccessError('too_long', f'{name} is too long (at most {MAX_SECRET_LENGTH} characters).')
        values[name] = text
    if len(values) > MAX_SECRETS:
        raise AccessError('too_many', f'An app keeps at most {MAX_SECRETS} secrets.')
    if values != before:
        await _set(row, secrets=seal(json.dumps(values)) if values else '', pending_restart=bool(app.version))
    return row


# ------------------------------------------------------------------ the app's side

async def calling_app(key_id: str) -> tuple[HostedApp, HostedAppAccess | None]:
    """The app a key was made for, and what its owner allowed."""
    from cognitrix.hosted_apps import store

    key_id = str(key_id or '')
    app = None
    if key_id:
        rows = await HostedApp.find({'api_key_id': key_id}) or []
        app = rows[0] if rows else None
        if app is None:
            starting = await HostedAppAccess.find({'pending_key_id': key_id}) or []
            app = await store.get_app(starting[0].app_id) if starting else None
    if app is None:
        raise AccessError('not_an_app', "This is only for an app's own key (COGNITRIX_API_KEY).", 403)
    return app, await row_for(app)


def _usable(app: HostedApp, row: HostedAppAccess | None) -> HostedAppAccess:
    if row is None or not granted(row):
        raise AccessError('nothing_allowed', f'The owner of "{app.title}" has not let it use any connected app '
                          'or saved sign-in. They can on the app\'s page, under Access.', 403)
    if row.paused:
        raise AccessError('paused', f'Access is paused for "{app.title}" until its owner checks the new version '
                          '(the app\'s page, under Access).', 403)
    return row


async def _allowed_configs(app: HostedApp, row: HostedAppAccess) -> list:
    return [c for c in await owner_apps(app.owner_id) if row.apps.get(str(c.id)) in ACCESS_LEVELS]


async def summary(app: HostedApp, row: HostedAppAccess | None) -> dict[str, Any]:
    """What the app may use right now (names only), and what it is still waiting for."""
    from cognitrix import credentials as vault

    live = row is not None and not row.paused
    apps = [{'app': c.name, 'changes': row.apps.get(str(c.id)) == 'all'}
            for c in await _allowed_configs(app, row)] if live else []
    creds = [vault.describe(r) for r in vault.only_allowed(await vault.load_rows(app.owner_id), list(row.credentials))] \
        if live else []
    return {'app': app.slug, 'paused': bool(row.paused) if row is not None else False,
            'connected_apps': apps, 'credentials': creds, 'waiting': await waiting(app, row)}


async def list_apps(app: HostedApp, row: HostedAppAccess | None) -> list[dict[str, Any]]:
    from cognitrix.api.routes.artifact_pages import _apps_list

    row = _usable(app, row)
    configs = await _allowed_configs(app, row)
    return await _apps_list(app.owner_id, configs=configs,
                            changes_for={str(c.id) for c in configs if row.apps.get(str(c.id)) == 'all'})


async def call_app(app: HostedApp, row: HostedAppAccess | None, name: str, tool: str, arguments: Any) -> Any:
    from cognitrix import audit
    from cognitrix.api.routes.artifact_pages import _apps_call
    from cognitrix.artifact_pages.store import PageError
    from cognitrix.mcp.registry import matches

    row = _usable(app, row)
    configs = await _allowed_configs(app, row)
    config = next((c for c in configs if matches(c, name) or _fold(c.name) == _fold(name)), None)
    if config is None:
        raise AccessError('app_not_allowed', f'"{app.title}" may not use a connected app called "{name}". '
                          'Its owner can allow it on the app\'s page, under Access.', 403)
    changes = row.apps.get(str(config.id)) == 'all'
    try:
        value = await _apps_call(app.owner_id, str(config.id), tool, arguments, confirmed=True if changes else None,
                                 caller=f'App {app.slug} (server)', configs=[config])
    except PageError as exc:
        outcome = 'denied' if exc.status == 403 else 'failed'
        audit.record('tool', f'App used {config.name}', user_id=app.owner_id, target=app.title,
                     detail=str(tool)[:200], outcome=outcome)
        if exc.code == 'not_read_only':
            raise AccessError('read_only', f'"{tool}" can change things, and "{app.title}" may only read '
                              f'{config.name}. Its owner can allow changes on the app\'s page, under Access.',
                              403) from None
        raise AccessError(exc.code, exc.message, exc.status) from None
    audit.record('tool', f'App used {config.name}', user_id=app.owner_id, target=app.title, detail=str(tool)[:200],
                 outcome='ran')
    return value


def _hide(data: bytes, secrets_map: dict[str, str]) -> bytes:
    """Bytes that aren't text in the encoding the answer names: hide a saved value
    in any of the usual ways of writing text as bytes."""
    for value in sorted(secrets_map, key=len, reverse=True):
        marker = f'[{secrets_map[value]} hidden]'
        for encoding in ('utf-8', 'utf-16-le', 'utf-16-be'):
            raw = value.encode(encoding)
            if raw in data:
                data = data.replace(raw, marker.encode(encoding))
    return data


def _text(data: bytes, encoding: str | None) -> str | None:
    """The answer as text, or None when it isn't text."""
    for name in dict.fromkeys(filter(None, (encoding, 'utf-8'))):
        try:
            return data.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


async def send(app: HostedApp, row: HostedAppAccess | None, *, url: str, method: str = 'GET',
               headers: dict | None = None, body: str | None = None, json_body: Any = None,
               credential: str | None = None) -> dict[str, Any]:
    """Send a request for the app with one of its allowed saved sign-ins filled in."""
    from cognitrix import audit
    from cognitrix.credentials import Resolver, redact
    from cognitrix.tools.http import SendError, _host, _mentions_secret, send_signed

    row = _usable(app, row)
    if not (credential or _mentions_secret(url, headers, body, json_body)):
        raise AccessError('no_credential', 'This is for requests that need a saved sign-in: name one in '
                          '"credential" or write {{secret:NAME}} where the value goes. Send other requests yourself.')
    resolver = await Resolver.for_owner(app.owner_id, list(row.credentials))
    host = _host(str(url))
    try:
        sent = await send_signed(resolver, str(url), method=method, headers=headers, body=body, json_body=json_body,
                                 credential=credential)
    except SendError as exc:
        audit.record('tool', 'App used a saved sign-in', user_id=app.owner_id, target=app.title,
                     detail=f'{str(method).upper()} {host}: {exc.code}', outcome='denied' if exc.denied else 'failed')
        raise AccessError(exc.code, exc.message, 403 if exc.denied else 400) from None
    audit.record('tool', 'App used a saved sign-in', user_id=app.owner_id, target=app.title,
                 detail=f"{sent.method} {host} with {', '.join(sorted(resolver.used))}", outcome='ran')
    secrets_map = resolver.redaction_map()
    response = sent.response
    data = response.content or b''
    out: dict[str, Any] = {
        'status': response.status_code,
        'reason': response.reason_phrase,
        'headers': {k: redact(v, secrets_map) for k, v in response.headers.items()
                    if k.lower() not in ('set-cookie', 'content-encoding', 'content-length', 'transfer-encoding')},
        'used': sorted(resolver.used),
    }
    # Read it as text the way the answer says it is written (it may be UTF-16), and hide
    # saved values in that text. Scrub before cutting, so a value split by the cut can't
    # leak its start.
    text = _text(data, response.encoding)
    if text is not None:
        text = redact(text, secrets_map)
        out['truncated'] = len(text) > MAX_RESPONSE
        out['body'] = text[:MAX_RESPONSE]
    else:
        data = _hide(data, secrets_map)
        out['truncated'] = len(data) > MAX_RESPONSE
        out['body'] = ''
        out['body_base64'] = base64.b64encode(data[:MAX_RESPONSE]).decode('ascii')
    if sent.stopped_at:
        out['redirect_not_followed'] = redact(sent.stopped_at, secrets_map)
    return out
