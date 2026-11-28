"""Settings > Tailscale (cognitrix/tailscale/service.py).

Anyone signed in may see how Tailscale stands; only an admin may change it.
``/tailscale/door`` is open to all: it only says which way the request came
in, which is how an address is checked from outside.

Who a visitor from the tailnet is (cognitrix/tailscale/identity.py):

    GET    /tailscale/me                 the Tailscale login this request is seen as (no session needed)
    POST   /tailscale/sign-in            sign in as the account linked to it -> {user, access_token}
    GET    /tailscale/link               my linked login
    PUT    /tailscale/link               link the login I am seen as to my account
    DELETE /tailscale/link
    GET    /tailscale/apps/{slug}        open an App shared with the tailnet, with no account -> {url}

Fly computers on the tailnet (cognitrix/tailscale/computers.py), admins only:

    POST   /tailscale/computers/check    try the saved OAuth client

Devices agents may run commands on (cognitrix/tailscale/devices.py), admins only:

    GET    /tailscale/devices            the tailnet's machines, what agents may do on each, this server's SSH key
    PUT    /tailscale/devices/{node}     allow a machine: {users, path}
    DELETE /tailscale/devices/{node}     take it off the list
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
              'listen_local': app_settings.get_bool('COGNITRIX_LISTEN_LOCAL', False),
              'signin': app_settings.get_bool('COGNITRIX_TAILSCALE_SIGNIN', False)}
    if can_edit:
        from cognitrix.tailscale import computers
        answer['computers'] = computers.view()
        answer['devices'] = tailscale.devices_choice()
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
    signin: bool | None = None
    sandbox: str | None = Field(default=None, max_length=20)
    sandbox_tag: str | None = Field(default=None, max_length=80)
    oauth_id: str | None = Field(default=None, max_length=200)
    oauth_secret: str | None = Field(default=None, max_length=400)
    devices: str | None = Field(default=None, max_length=20)


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
    if body.signin is not None:
        values['COGNITRIX_TAILSCALE_SIGNIN'] = body.signin
    if body.sandbox is not None:
        if body.sandbox not in (tailscale.NOBODY, tailscale.ADMINS, tailscale.EVERYONE):
            raise HTTPException(status_code=400, detail='Pick nobody, admins or everyone.')
        values['COGNITRIX_TAILSCALE_SANDBOX'] = body.sandbox
    if body.sandbox_tag is not None:
        values['COGNITRIX_TAILSCALE_SANDBOX_TAG'] = body.sandbox_tag.strip()
    if body.oauth_id is not None:
        values['COGNITRIX_TAILSCALE_OAUTH_ID'] = body.oauth_id.strip()
    if body.oauth_secret is not None:
        values['COGNITRIX_TAILSCALE_OAUTH_SECRET'] = body.oauth_secret.strip()
    if body.devices is not None:
        if body.devices not in (tailscale.OFF, tailscale.ADMINS, tailscale.EVERYONE):
            raise HTTPException(status_code=400, detail='Pick off, admins or everyone.')
        values['COGNITRIX_TAILSCALE_DEVICES'] = body.devices
    try:
        await asyncio.to_thread(app_settings.set_many, values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    snapshot = await service.settings_changed(request.app)
    if any(v is not None for v in (body.enabled, body.sandbox, body.sandbox_tag, body.oauth_id, body.oauth_secret)):
        # The Fly manager and the API token were built from the old values.
        from cognitrix.computer.sandbox.client import forget_fly_manager
        from cognitrix.tailscale import api as tailscale_api_client
        tailscale_api_client.forget_token()
        forget_fly_manager()
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
    if body.signin is not None:
        changed.append(f"sign in from the tailnet {'on' if body.signin else 'off'}")
    if body.listen_local is not None:
        changed.append(f"only reachable through Tailscale {'on' if body.listen_local else 'off'}")
    if body.sandbox is not None:
        changed.append(f'Fly computers on the tailnet: {body.sandbox}')
    if body.sandbox_tag is not None:
        changed.append(f"computers' tag: {tailscale.sandbox_tag()}")
    if body.oauth_id is not None or body.oauth_secret is not None:
        changed.append('Tailscale OAuth client saved' if (body.oauth_secret or body.oauth_id) else
                       'Tailscale OAuth client removed')
    if body.devices is not None:
        changed.append(f'agents may use tailnet devices: {body.devices}')
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


# ---------------------------------------------------------------------------
# Who a visitor from the tailnet is
# ---------------------------------------------------------------------------

def _identity_error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@tailscale_api.get('/me')
async def tailscale_me(request: Request) -> dict[str, Any]:
    """Who Tailscale says is asking. Empty unless the request came through the
    private address. No session needed: the Login page asks."""
    from cognitrix.tailscale import identity

    return await identity.view(request)


class TailnetSignIn(BaseModel):
    device_name: str = Field(default='', max_length=80)
    device_kind: str = Field(default='', max_length=20)


@tailscale_api.post('/sign-in')
async def tailscale_sign_in(body: TailnetSignIn, request: Request) -> dict[str, Any]:
    from cognitrix.api.routes.auth import _signed_in_body
    from cognitrix.tailscale import identity

    try:
        user, token = await identity.sign_in(request, name=body.device_name, kind=body.device_kind)
    except identity.IdentityError as exc:
        raise _identity_error(exc) from None
    return await _signed_in_body(user, token)


@tailscale_api.get('/link')
async def read_link(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.tailscale import identity

    return await identity.view(request, ctx.user)


@tailscale_api.put('/link')
async def make_link(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.tailscale import identity

    try:
        return await identity.link(request, ctx.user, sid=ctx.sid or '')
    except identity.IdentityError as exc:
        raise _identity_error(exc) from None


@tailscale_api.delete('/link')
async def remove_link(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.tailscale import identity

    return await identity.unlink(request, ctx.user)


@tailscale_api.get('/apps/{slug}')
async def open_app_as_guest(slug: str, request: Request) -> dict[str, Any]:
    """A way in to an App for a visitor from the tailnet who has no account,
    when its owner shared it with their login or with the whole tailnet."""
    from cognitrix.hosted_apps import store
    from cognitrix.tailscale import identity

    seen = identity.visitor(request, own_page=True)
    app = await store.by_slug(slug.lower()) if seen and store.guests_allowed() else None
    if seen is None or app is None or store.role_of(app, seen.key) is None:
        raise HTTPException(status_code=404, detail='Not found')
    return {'url': store.app_path(app.slug, store.open_token(app.slug, seen.key)), 'title': app.title,
            'login': seen.login}


# ---------------------------------------------------------------------------
# Fly computers on the tailnet, and the devices agents may use
# ---------------------------------------------------------------------------

@tailscale_api.post('/computers/check')
async def check_computers(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Try the saved OAuth client: does Tailscale give it a join key with the computers' tag?"""
    await _admin(ctx)
    from cognitrix.tailscale import computers

    return await asyncio.to_thread(computers.check)


def _device_error(exc) -> HTTPException:
    return HTTPException(status_code=404 if exc.code == 'unknown' else 400, detail=str(exc))


def _note_relay(request: Request) -> None:
    """Where a task worker without Tailscale of its own finds this process."""
    try:
        server = request.scope.get('server') or ()
        if doors.door_of(request) == doors.DIRECT and len(server) > 1 and server[1]:
            from cognitrix.computer.paired.relay import remember_address
            remember_address(int(server[1]))
    except Exception:  # noqa: BLE001 - only a convenience for workers
        pass


@tailscale_api.get('/devices')
async def read_devices(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    await _admin(ctx)
    from cognitrix.tailscale import devices

    _note_relay(request)
    return {'scope': tailscale.devices_choice(), 'devices': await devices.listing(),
            'key': await asyncio.to_thread(devices.public_key), 'ssh': devices.find_ssh() is not None}


class DeviceBody(BaseModel):
    users: list[str] = Field(default_factory=list, max_length=10)
    path: str = Field(default='tailscale', max_length=20)


@tailscale_api.put('/devices/{node}')
async def allow_device(node: str, body: DeviceBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    await _admin(ctx)
    from cognitrix import audit
    from cognitrix.tailscale import devices

    try:
        saved = await devices.allow(node, body.users, body.path, by=owner_key(ctx.user))
    except devices.DeviceError as exc:
        raise _device_error(exc) from None
    audit.record('settings', 'Changed a tailnet device agents may use', user_id=owner_key(ctx.user),
                 detail=f"{saved['name']}: {', '.join(saved['users']) or 'no login names'} ({saved['path']})"[:500])
    return saved


@tailscale_api.delete('/devices/{node}')
async def remove_device(node: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    await _admin(ctx)
    from cognitrix import audit
    from cognitrix.tailscale import devices

    if not await devices.remove(node):
        raise HTTPException(status_code=404, detail="That device isn't on the list.")
    await asyncio.to_thread(devices.forget_host, node)
    audit.record('settings', 'Took a tailnet device off the list agents may use', user_id=owner_key(ctx.user),
                 detail=node[:100])
    return {'removed': True}
