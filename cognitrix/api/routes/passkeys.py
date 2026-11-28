"""Passkeys (cognitrix/passkeys.py).

Signed in (browser sessions only, not API keys):

    GET    /auth/passkeys                       this account's passkeys
    POST   /auth/passkeys/register/options      start adding one -> {ceremony, options}
    POST   /auth/passkeys/register              {ceremony, credential, name?} -> the passkey
    PATCH  /auth/passkeys/{id}                  {name}
    DELETE /auth/passkeys/{id}

Signing in (no session; the passkey is the proof):

    POST   /auth/passkey-sign-in/options        -> {ceremony, options}
    POST   /auth/passkey-sign-in                {ceremony, credential, device_name?, device_kind?} -> {user, access_token}

The desktop app, whose window can't use passkeys, does the step in the
default browser. Only on the desktop engine, and only from this computer:

    POST   /auth/passkey-sign-in/handoff        {device_name?} -> {handoff, secret, path}   (signed out)
    POST   /auth/passkeys/handoff               -> {handoff, secret, path}   (signed in: add a passkey)
    POST   /auth/passkeys/handoff/collect       {secret} -> waiting | opened | expired | done (+ token or passkey)
    POST   /auth/passkeys/handoff/cancel        {secret}
    GET    /auth/passkeys/handoff/{handoff}     the browser page: which step (and whose account)
    POST   /auth/passkeys/handoff/{handoff}/options
    POST   /auth/passkeys/handoff/{handoff}/finish   {ceremony, credential, name?}
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cognitrix.common.security import AuthContext, jwt_only

passkeys_api = APIRouter(prefix='/auth')


def _error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


class Finish(BaseModel):
    ceremony: str = Field(min_length=1, max_length=64)
    credential: dict[str, Any]
    name: str = Field(default='', max_length=80)
    device_name: str = Field(default='', max_length=80)
    device_kind: str = Field(default='', max_length=20)


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class StartHandoff(BaseModel):
    device_name: str = Field(default='', max_length=80)


class Secret(BaseModel):
    secret: str = Field(min_length=1, max_length=100)


@passkeys_api.get('/passkeys')
async def list_passkeys(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import passkeys
    return {'passkeys': await passkeys.listing(ctx.user), 'enabled': passkeys.passkeys_on(),
            'handoff': passkeys.on_desktop_engine(),
            # A passkey works only at the address it was made at; this one always can.
            'secure_address': passkeys.secure_address() if passkeys.passkeys_on() else ''}


@passkeys_api.post('/passkeys/register/options')
async def register_options(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.registration_options(ctx.user, request)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.post('/passkeys/register')
async def register(body: Finish, request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.register(ctx.user, request, body.ceremony, body.credential, body.name)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.post('/passkey-sign-in/options')
async def sign_in_options(request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.sign_in_options(request)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.post('/passkey-sign-in')
async def passkey_sign_in(body: Finish, request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    from cognitrix.api.routes.auth import _signed_in_body
    try:
        user, token = await passkeys.sign_in(request, body.ceremony, body.credential,
                                             name=body.device_name, kind=body.device_kind)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None
    return await _signed_in_body(user, token)


# ---- Desktop app: the passkey step in the default browser -----------------------

@passkeys_api.post('/passkeys/handoff')
async def start_register_handoff(body: StartHandoff, request: Request,
                                 ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.start_handoff(request, 'register', user=ctx.user, sid=ctx.sid)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.post('/passkey-sign-in/handoff')
async def start_sign_in_handoff(body: StartHandoff, request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.start_handoff(request, 'sign_in', device_name=body.device_name)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.post('/passkeys/handoff/collect')
async def collect_handoff(body: Secret, request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    from cognitrix.api.routes.auth import _signed_in_body
    try:
        out = await passkeys.collect_handoff(request, body.secret)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None
    if out.get('status') == 'done' and out.get('kind') == 'sign_in':
        return {'status': 'done', 'kind': 'sign_in', **await _signed_in_body(out['user'], out['access_token'])}
    return out


@passkeys_api.post('/passkeys/handoff/cancel')
async def cancel_handoff(body: Secret, request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        await passkeys.cancel_handoff(request, body.secret)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None
    return {'ok': True}


@passkeys_api.get('/passkeys/handoff/{handoff}')
async def handoff_info(handoff: str, request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.handoff_info(request, handoff)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.post('/passkeys/handoff/{handoff}/options')
async def handoff_options(handoff: str, request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.handoff_options(request, handoff)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.post('/passkeys/handoff/{handoff}/finish')
async def handoff_finish(handoff: str, body: Finish, request: Request) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.handoff_finish(request, handoff, body.ceremony, body.credential, body.name)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


# These two come after the /handoff routes so 'handoff' is never read as a passkey id.
@passkeys_api.patch('/passkeys/{passkey_id}')
async def rename_passkey(passkey_id: str, body: Rename, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        return await passkeys.rename(ctx.user, passkey_id, body.name)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None


@passkeys_api.delete('/passkeys/{passkey_id}')
async def remove_passkey(passkey_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import passkeys
    try:
        await passkeys.remove(ctx.user, passkey_id)
    except passkeys.PasskeyError as exc:
        raise _error(exc) from None
    return {'ok': True}
