"""Settings > Tailscale (cognitrix/tailscale/service.py).

Anyone signed in may see how Tailscale stands; only an admin may change it.
``/tailscale/door`` is open to all: it only says which way the request came
in, which is how an address is checked from outside.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cognitrix import app_settings, tailscale
from cognitrix.agents.access import is_admin, owner_key
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.tailscale import doors, service

tailscale_api = APIRouter(prefix='/tailscale')


@tailscale_api.get('/door')
async def which_door(request: Request) -> dict[str, str]:
    return {'door': doors.door_of(request)}


async def _admin(ctx: AuthContext) -> None:
    if not await is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can change Tailscale settings.')


def _answer(snapshot: dict[str, Any], can_edit: bool, request: Request) -> dict[str, Any]:
    answer = {**snapshot, 'can_edit': can_edit, 'door': doors.door_of(request),
              'listen_local': app_settings.get_bool('COGNITRIX_LISTEN_LOCAL', False)}
    if not can_edit:
        # The sign-in page would add this machine to the tailnet of whoever opens it: admins only.
        answer['login_url'] = ''
        for part in ('private', 'public'):
            answer[part] = {**answer[part], 'needs_url': '', 'detail': ''}
    return answer


@tailscale_api.get('')
async def read_tailscale(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    snapshot = await asyncio.to_thread(service.snapshot)
    return _answer(snapshot, await is_admin(ctx.user), request)


class TailscaleBody(BaseModel):
    enabled: bool | None = None
    hostname: str | None = Field(default=None, max_length=63)
    private: bool | None = None
    private_port: int | None = Field(default=None, ge=1, le=65535)
    public: bool | None = None
    public_port: int | None = None
    allow: list[str] | None = None
    listen_local: bool | None = None


@tailscale_api.put('')
async def save_tailscale(body: TailscaleBody, request: Request,
                         ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    await _admin(ctx)
    values: dict[str, Any] = {}
    if body.enabled is not None:
        values['COGNITRIX_TAILSCALE'] = body.enabled
    if body.hostname is not None:
        values['COGNITRIX_TAILSCALE_HOSTNAME'] = body.hostname
    if body.private is not None:
        values['COGNITRIX_TAILSCALE_SERVE'] = body.private
    if body.private_port is not None:
        values['COGNITRIX_TAILSCALE_SERVE_PORT'] = body.private_port
    if body.public is not None:
        values['COGNITRIX_TAILSCALE_FUNNEL'] = body.public
    if body.public_port is not None:
        if body.public_port not in tailscale.FUNNEL_PORTS:
            raise HTTPException(status_code=400, detail='Tailscale only offers public addresses on ports '
                                                        '443, 8443 and 10000.')
        values['COGNITRIX_TAILSCALE_FUNNEL_PORT'] = str(body.public_port)
    if body.allow is not None:
        picked = [a for a in tailscale.ALLOW_CHOICES if a in {str(x).strip().lower() for x in body.allow}]
        values['COGNITRIX_TAILSCALE_FUNNEL_ALLOW'] = '\n'.join(picked or [tailscale.ALLOW_WEBHOOKS])
    if body.listen_local is not None:
        values['COGNITRIX_LISTEN_LOCAL'] = body.listen_local
    try:
        await asyncio.to_thread(app_settings.set_many, values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    snapshot = await service.settings_changed(request.app)
    from cognitrix import audit

    changed = []
    if body.enabled is not None:
        changed.append(f"Tailscale {'on' if body.enabled else 'off'}")
    if body.private is not None:
        changed.append(f"private address {'on' if body.private else 'off'}")
    if body.public is not None:
        changed.append(f"public address {'on' if body.public else 'off'}")
    if body.allow is not None:
        changed.append('public address lets through: ' + ', '.join(sorted(tailscale.funnel_allow())))
    if body.listen_local is not None:
        changed.append(f"only reachable through Tailscale {'on' if body.listen_local else 'off'}")
    audit.record('settings', 'Changed Tailscale settings', user_id=owner_key(ctx.user),
                 detail='; '.join(changed)[:500])
    return _answer(snapshot, True, request)


class ConnectBody(BaseModel):
    auth_key: str = Field(default='', max_length=400)


def _refuse(exc: service.TailscaleError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@tailscale_api.post('/connect')
async def connect_tailscale(body: ConnectBody, request: Request,
                            ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Sign in the Tailscale that Cognitrix runs itself (a server in Docker or
    on Fly). The key is used once and never kept."""
    await _admin(ctx)
    if not tailscale.enabled():
        raise HTTPException(status_code=400, detail='Turn Tailscale on first.')
    try:
        snapshot = await asyncio.to_thread(service.connect, body.auth_key)
    except service.TailscaleError as exc:
        raise _refuse(exc) from exc
    await service.start(request.app)
    from cognitrix import audit

    audit.record('settings', 'Signed this server in to Tailscale', user_id=owner_key(ctx.user))
    return _answer(snapshot, True, request)


@tailscale_api.post('/disconnect')
async def disconnect_tailscale(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    await _admin(ctx)
    try:
        snapshot = await asyncio.to_thread(service.disconnect)
    except service.TailscaleError as exc:
        raise _refuse(exc) from exc
    from cognitrix import audit

    audit.record('settings', 'Signed this server out of Tailscale', user_id=owner_key(ctx.user))
    return _answer(snapshot, True, request)


@tailscale_api.post('/check')
async def check_tailscale(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    await _admin(ctx)
    return await asyncio.to_thread(service.check)
