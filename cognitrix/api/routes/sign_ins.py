"""Signed-in devices and signing in with a code (cognitrix/signins.py).

Signed in (browser sessions only, not API keys):

    GET    /auth/sign-ins                     this account's signed-in devices
    DELETE /auth/sign-ins/{id}                sign that device out
    POST   /auth/sign-ins/sign-out            sign this device out
    POST   /auth/sign-ins/sign-out-others     sign out every device but this one
    POST   /auth/sign-ins/codes               a one-time code to sign in another device (5 minutes)
    GET    /auth/sign-ins/codes/{code}        waiting | used (with the device) | expired
    DELETE /auth/sign-ins/codes/{code}        cancel it

On the new device (no session; the code is the proof):

    POST   /auth/code-sign-in                 {code, device_name?, device_kind?} -> {user, access_token}
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cognitrix.common.security import AuthContext, jwt_only

sign_ins_api = APIRouter(prefix='/auth')


def _error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@sign_ins_api.get('/sign-ins')
async def list_sign_ins(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import signins
    return {
        'sign_ins': await signins.listing(ctx.user, ctx.sid),
        'current_listed': bool(ctx.sid),
        'code_sign_in': signins.code_signin_on(),
    }


@sign_ins_api.post('/sign-ins/sign-out')
async def sign_out_here(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import signins
    if ctx.sid:
        await signins.end(str(ctx.user.id), ctx.sid)
    return {'ok': True}


@sign_ins_api.post('/sign-ins/sign-out-others')
async def sign_out_others(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import audit, signins
    count = await signins.end_all(str(ctx.user.id), keep_sid=ctx.sid)
    audit.record('settings', f'Signed out {count} other device(s)', user_id=str(ctx.user.id),
                 actor=str(getattr(ctx.user, 'email', '') or ''), outcome='ok')
    return {'ok': True, 'signed_out': count}


@sign_ins_api.post('/sign-ins/codes')
async def make_sign_in_code(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import signins
    try:
        return await signins.make_code(ctx.user, ctx.sid, server=str(request.base_url).rstrip('/'))
    except signins.SignInError as exc:
        raise _error(exc) from None


@sign_ins_api.get('/sign-ins/codes/{code}')
async def sign_in_code_status(code: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import signins
    return await signins.code_status(ctx.user, code)


@sign_ins_api.delete('/sign-ins/codes/{code}')
async def cancel_sign_in_code(code: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import signins
    await signins.cancel_code(ctx.user, code)
    return {'ok': True}


@sign_ins_api.delete('/sign-ins/{sid}')
async def end_sign_in(sid: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix import audit, signins
    from cognitrix.models.sign_in import SignIn
    row = await SignIn.find_one({'sid': sid})
    if row is None or str(row.user_id) != str(ctx.user.id) or not await signins.end(str(ctx.user.id), sid):
        raise HTTPException(status_code=404, detail='That device is not signed in')
    audit.record('settings', f'Signed out {row.name}', user_id=str(ctx.user.id),
                 actor=str(getattr(ctx.user, 'email', '') or ''), outcome='ok')
    return {'ok': True, 'was_current': sid == ctx.sid}


class CodeSignIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    device_name: str = Field(default='', max_length=80)
    device_kind: str = Field(default='', max_length=20)


@sign_ins_api.post('/code-sign-in')
async def code_sign_in(body: CodeSignIn, request: Request) -> dict[str, Any]:
    from cognitrix import signins
    from cognitrix.api.routes.auth import _signed_in_body
    try:
        user, token = await signins.use_code(body.code, request, name=body.device_name, kind=body.device_kind)
    except signins.SignInError as exc:
        raise _error(exc) from None
    return await _signed_in_body(user, token)
