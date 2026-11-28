"""Saved sign-ins: tokens, API keys and passwords agents use by name.

Values go in and never come back out: responses carry the name, kind, hosts,
description and the last four characters. Each credential is private to the
person who saved it. Browser sessions only, so an API key can't read the list
or add a credential that sends a token somewhere new.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents.access import owner_key
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.credentials import (
    describe,
    forget_cached,
    hint_for,
    load_rows,
    normalize_host,
    normalize_name,
    open_secret,
    seal_secret,
)
from cognitrix.models.credential import Credential

credentials_api = APIRouter(prefix='/credentials')

MAX_VALUE_LENGTH = 8000
MAX_HOSTS = 20
_HEADER_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')


class CredentialBody(BaseModel):
    name: str
    kind: Literal['token', 'basic'] = 'token'
    description: str = ''
    allowed_hosts: list[str]
    header: str = ''
    value: str | None = None
    username: str | None = None
    password: str | None = None
    # Agents may type it into sign-in pages on all its sites, in their browser
    # (models/credential.py browser_hosts).
    use_in_browser: bool = False


class CredentialUpdate(BaseModel):
    description: str | None = None
    allowed_hosts: list[str] | None = None
    header: str | None = None
    use_in_browser: bool | None = None
    value: str | None = None
    username: str | None = None
    password: str | None = None


def _project(row: Any) -> dict[str, Any]:
    data = describe(row)
    values = open_secret(row)
    data.update({
        'id': str(row.id),
        'header': row.header or '',
        'hint': row.hint or '',
        'username': values.get('username', '') if row.kind == 'basic' else '',
        'last_used_at': row.last_used_at,
        'placeholder': '{{secret:' + row.name + '}}',
    })
    return data


def _sites(hosts: list[str]) -> list[str]:
    """Hosts that are websites (not the sandbox-only marker)."""
    return [h for h in hosts if h != 'sandbox.invalid']


def _hosts(raw: list[str]) -> list[str]:
    hosts: list[str] = []
    for item in raw:
        try:
            host = normalize_host(item)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if host not in hosts:
            hosts.append(host)
    if not hosts:
        raise HTTPException(status_code=400, detail='Add at least one host this sign-in may be sent to')
    if len(hosts) > MAX_HOSTS:
        raise HTTPException(status_code=400, detail=f'Use at most {MAX_HOSTS} hosts')
    return hosts


def _header(raw: str) -> str:
    header = (raw or '').strip()
    if header and not set(header) <= _HEADER_CHARS:
        raise HTTPException(status_code=400, detail="That header name looks wrong. Use something like X-API-Key")
    return '' if header.lower() == 'authorization' else header


def _check_value(label: str, value: str | None, *, exact: bool = False) -> str:
    # Passwords are kept exactly as typed; spaces at either end can be real.
    value = (value or '') if exact else (value or '').strip()
    if len(value) > MAX_VALUE_LENGTH:
        raise HTTPException(status_code=400, detail=f'The {label} is too long')
    if '\n' in value or '\r' in value:
        raise HTTPException(status_code=400, detail=f'The {label} must be on one line')
    return value


def _values(kind: str, value: str | None, username: str | None, password: str | None) -> dict[str, str]:
    if kind == 'basic':
        values = {'username': _check_value('username', username), 'password': _check_value('password', password, exact=True)}
        if not values['username'] or not values['password']:
            raise HTTPException(status_code=400, detail='Enter both a username and a password')
        return values
    token = _check_value('value', value)
    if not token:
        raise HTTPException(status_code=400, detail='Enter the token or API key')
    return {'value': token}


async def _own(credential_id: str, ctx: AuthContext) -> Any:
    owner = owner_key(ctx.user)
    for row in await load_rows(owner):
        if str(row.id) == str(credential_id):
            return row
    raise HTTPException(status_code=404, detail='Saved sign-in not found')


@credentials_api.get('')
async def list_credentials(ctx: AuthContext = Depends(jwt_only)):
    rows = await load_rows(owner_key(ctx.user))
    return {'credentials': [_project(row) for row in sorted(rows, key=lambda r: r.name)]}


@credentials_api.post('')
async def add_credential(body: CredentialBody, ctx: AuthContext = Depends(jwt_only)):
    owner = owner_key(ctx.user)
    try:
        name = normalize_name(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if any(row.name == name for row in await load_rows(owner)):
        raise HTTPException(status_code=409, detail=f'You already have a sign-in called {name}')
    values = _values(body.kind, body.value, body.username, body.password)
    row = Credential(
        owner_id=owner,
        name=name,
        kind=body.kind,
        description=body.description.strip()[:500],
        allowed_hosts=_hosts(body.allowed_hosts),
        header=_header(body.header) if body.kind == 'token' else '',
        secret=seal_secret(values),
        hint=hint_for(values),
        browser_hosts=_sites(_hosts(body.allowed_hosts)) if body.use_in_browser else [],
    )
    await row.save()
    forget_cached(owner)
    saved = await Credential.find_one({'owner_id': owner, 'name': name})
    return _project(saved or row)


@credentials_api.patch('/{credential_id}')
async def update_credential(credential_id: str, body: CredentialUpdate, ctx: AuthContext = Depends(jwt_only)):
    row = await _own(credential_id, ctx)
    changes: dict[str, Any] = {}
    if body.description is not None:
        changes['description'] = body.description.strip()[:500]
    if body.allowed_hosts is not None:
        changes['allowed_hosts'] = _hosts(body.allowed_hosts)
    if body.header is not None and row.kind == 'token':
        changes['header'] = _header(body.header)
    hosts_now = changes.get('allowed_hosts', list(row.allowed_hosts or []))
    kept = [h for h in (row.browser_hosts or []) if h in hosts_now]
    if body.use_in_browser is False:
        changes['browser_hosts'] = []
    elif body.use_in_browser and not kept:
        # Turned on: the form says "only on the hosts above", every site it lists.
        changes['browser_hosts'] = _sites(hosts_now)
    elif 'allowed_hosts' in changes or body.use_in_browser:
        # Already on: the sites allowed before stay the only ones (a host added
        # for requests isn't one); a removed host stops being one.
        changes['browser_hosts'] = kept

    current = open_secret(row)
    if row.kind == 'basic' and (body.username is not None or body.password is not None):
        values = _values(
            'basic',
            None,
            body.username if body.username is not None else current.get('username'),
            body.password if body.password else current.get('password'),
        )
        changes.update(secret=seal_secret(values), hint=hint_for(values))
    elif row.kind == 'token' and body.value:
        values = _values('token', body.value, None, None)
        changes.update(secret=seal_secret(values), hint=hint_for(values))

    if changes:
        await Credential.update_one({'id': row.id}, changes)
        forget_cached(owner_key(ctx.user))
    return _project(await Credential.get(row.id))


@credentials_api.delete('/{credential_id}')
async def delete_credential(credential_id: str, ctx: AuthContext = Depends(jwt_only)):
    row = await _own(credential_id, ctx)
    # delete_many: odbms' sqlite delete_one emits DELETE ... LIMIT, which SQLite rejects.
    await Credential.delete_many({'id': row.id})
    forget_cached(owner_key(ctx.user))
    return {'deleted': True}


class SignInAnswer(BaseModel):
    value: str | None = None
    username: str | None = None
    password: str | None = None


@credentials_api.post('/requests/{request_id}')
async def answer_sign_in_request(request_id: str, body: SignInAnswer, ctx: AuthContext = Depends(jwt_only)):
    """Answer an agent's Ask For Sign-in card: save what the person typed to
    their saved sign-ins (new, or replacing the one with that name), then tell
    the agent it was saved. The value never reaches the agent or the chat."""
    from cognitrix.questions.broker import pending_secret, resolve_question

    user_key = str(getattr(ctx.user, 'id', None) or getattr(ctx.user, 'email', 'anon'))
    wanted = pending_secret(request_id, user_key)
    if wanted is None:
        raise HTTPException(status_code=404, detail='That request is no longer open.')
    owner = owner_key(ctx.user)
    try:
        name = normalize_name(str(wanted.get('name') or ''))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    kind = 'basic' if wanted.get('kind') == 'basic' else 'token'
    values = _values(kind, body.value, body.username, body.password)
    existing = next((row for row in await load_rows(owner) if row.name == name), None)
    if existing is not None:
        changes: dict[str, Any] = {'secret': seal_secret(values), 'hint': hint_for(values), 'kind': kind}
        hosts = [h for h in (wanted.get('hosts') or []) if h != 'sandbox.invalid']
        if hosts:
            changes['allowed_hosts'] = _hosts(list(dict.fromkeys([*existing.allowed_hosts, *hosts])))
        if wanted.get('browser') and hosts:
            # The card said it may be typed into these sites' sign-in pages:
            # these, not the other sites it was already saved for.
            changes['browser_hosts'] = list(dict.fromkeys([*(existing.browser_hosts or []), *_hosts(hosts)]))
        await Credential.update_one({'id': existing.id}, changes)
    else:
        hosts = [h for h in (wanted.get('hosts') or []) if h != 'sandbox.invalid']
        row = Credential(
            owner_id=owner, name=name, kind=kind,
            description=str(wanted.get('description') or '')[:500] or 'Asked for by an agent',
            allowed_hosts=_hosts(list(wanted.get('hosts') or ['sandbox.invalid'])),
            secret=seal_secret(values), hint=hint_for(values),
            browser_hosts=_hosts(hosts) if wanted.get('browser') and hosts else [],
        )
        await row.save()
    forget_cached(owner)
    await resolve_question(request_id, user_key, 'answer', option_id='saved')
    return {'saved': True, 'name': name}
