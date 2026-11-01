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


class CredentialUpdate(BaseModel):
    description: str | None = None
    allowed_hosts: list[str] | None = None
    header: str | None = None
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
