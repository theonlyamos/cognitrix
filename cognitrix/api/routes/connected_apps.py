"""Connected apps: sign in to GitHub, Gmail, Google Calendar and other hosted
MCP servers, so agents can use them.

Each connection is an ``MCPServer`` row owned by the person who connected it
(``catalog_id`` names the catalog entry), so it is never visible to or usable
by anyone else, and agents pick it like any other MCP server. Background
tasks use the agent owner's connections.

Sign-in itself lives in ``cognitrix/mcp/oauth.py``. Browser sessions only:
an API key can't connect apps.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cognitrix.agents.access import is_admin, owner_key
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.mcp import catalog, oauth, registry
from cognitrix.models.mcp_server import MCPServer

apps_api = APIRouter(prefix='/mcp')


def _desktop() -> bool:
    return os.environ.get('COGNITRIX_DESKTOP') == '1'


def base_url(request: Request) -> str:
    return os.getenv('COGNITRIX_PUBLIC_URL', '').strip().rstrip('/') or str(request.base_url).rstrip('/')


async def _allow_private(ctx: AuthContext) -> bool:
    return _desktop() or await is_admin(ctx.user)


async def _own_rows(user_key: str) -> list[MCPServer]:
    return [r for r in (await MCPServer.find({'owner_id': user_key}) or [])]


def _connection(row: MCPServer | None) -> dict[str, Any] | None:
    if row is None:
        return None
    status = row.status or ''
    if not status and (row.auth_type or '') == 'header' and row.headers:
        status = 'connected'
    return {
        'server_id': str(row.id),
        'name': row.name,
        'url': row.url,
        'auth_type': row.auth_type or 'none',
        'status': status or 'signed_out',
        'account': row.account or '',
        'enabled': bool(row.enabled),
    }


@apps_api.get('/apps')
async def list_apps(request: Request, ctx: AuthContext = Depends(jwt_only)):
    me = owner_key(ctx.user)
    rows = await _own_rows(me)
    by_app = {r.catalog_id: r for r in rows if r.catalog_id}
    admin = await is_admin(ctx.user)
    base = base_url(request)
    apps = [{**catalog.public(app), 'connection': _connection(by_app.get(app.id))} for app in catalog.CATALOG]
    custom = [
        _connection(r) for r in rows
        if not r.catalog_id and (r.auth_type or '') == 'oauth'
    ]
    return {
        'apps': apps,
        'custom': custom,
        'is_admin': admin,
        # What an admin registers with Google / GitHub (the sign-in callback).
        'callback_urls': {p: oauth.provider_callback_url(base, p) for p in ('google', 'github')} if admin else {},
    }


def _unique_name(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name = f'{base}-{n}'
        n += 1
    return name


async def _row_for(app: catalog.CatalogApp, me: str, auth_type: str) -> MCPServer:
    """The person's row for a catalog app, created on first connect."""
    for row in await _own_rows(me):
        if row.catalog_id == app.id:
            if (row.auth_type or '') != auth_type:
                await MCPServer.update_one({'id': row.id}, {'auth_type': auth_type})
                row.auth_type = auth_type
            return row
    taken = {c.name for c in await registry.visible_servers(me)}
    row = MCPServer(
        name=_unique_name(app.id.replace('google-', 'google_').replace('-', '_'), taken),
        owner_id=me,
        transport='http',
        description=app.description,
        url=app.url,
        enabled=True,
        auth_type=auth_type,
        catalog_id=app.id,
        status='',
        account='',
    )
    await row.save()
    return await MCPServer.find_one({'owner_id': me, 'catalog_id': app.id}) or row


def _app_or_404(app_id: str) -> catalog.CatalogApp:
    app = catalog.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail='No such app')
    return app


def _sign_in_error(exc: oauth.SignInError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.user_message)


@apps_api.post('/apps/{app_id}/connect')
async def connect_app(app_id: str, request: Request, ctx: AuthContext = Depends(jwt_only)):
    """Start signing in. The UI opens ``authorize_url`` and polls the flow."""
    app = _app_or_404(app_id)
    if not catalog.available(app):
        raise HTTPException(status_code=400, detail=f'{app.name} is not set up on this server yet. {catalog.setup_hint(app)}')
    me = owner_key(ctx.user)
    row = await _row_for(app, me, 'oauth')
    try:
        return await oauth.start_sign_in(row, me, base_url(request), allow_private=await _allow_private(ctx))
    except oauth.SignInError as exc:
        raise _sign_in_error(exc)


class TokenBody(BaseModel):
    token: str


@apps_api.post('/apps/{app_id}/token')
async def connect_with_token(app_id: str, body: TokenBody, ctx: AuthContext = Depends(jwt_only)):
    """Connect with a personal access token instead of signing in (GitHub)."""
    from cognitrix.common.secret_box import seal

    app = _app_or_404(app_id)
    if not app.token_help:
        raise HTTPException(status_code=400, detail=f'{app.name} only connects by signing in')
    token = body.token.strip()
    if not token or any(c.isspace() for c in token):
        raise HTTPException(status_code=400, detail='Paste the token on its own')
    account = ''
    if app.provider == 'github':
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get('https://api.github.com/user', headers={
                    'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="Couldn't reach GitHub to check the token")
        if response.status_code == 401:
            raise HTTPException(status_code=400, detail="GitHub didn't accept that token")
        try:
            account = str(response.json().get('login') or '')
        except (ValueError, AttributeError):
            account = ''
    me = owner_key(ctx.user)
    row = await _row_for(app, me, 'header')
    import json

    await MCPServer.update_one({'id': row.id}, {
        'headers': seal(json.dumps({'Authorization': f'Bearer {token}'})),
        'oauth': '',
        'status': 'connected',
        'account': account,
        'enabled': True,
    })
    await _disconnect(str(row.id))
    return _connection(await MCPServer.get(row.id))


async def _disconnect(server_id: str) -> None:
    from cognitrix.mcp.client import get_dynamic_client

    client = await get_dynamic_client()
    if client.is_connected(server_id):
        await client.disconnect_from_server(server_id)


async def _own_row(server_id: str, ctx: AuthContext) -> MCPServer:
    row = await MCPServer.get(server_id)
    if row is None or str(row.owner_id or '') != owner_key(ctx.user):
        raise HTTPException(status_code=404, detail='App not found')
    return row


@apps_api.post('/apps/{app_id}/disconnect')
async def disconnect_app(app_id: str, ctx: AuthContext = Depends(jwt_only)):
    """Sign out. The row stays, so agents that use the app keep it picked."""
    app = _app_or_404(app_id)
    me = owner_key(ctx.user)
    row = next((r for r in await _own_rows(me) if r.catalog_id == app.id), None)
    if row is None:
        return {'disconnected': True}
    await oauth.sign_out(row)
    # Back to the default way in, so the card offers Connect / Use a token again.
    await MCPServer.update_one({'id': row.id}, {'headers': '', 'status': '', 'account': '', 'auth_type': 'oauth'})
    return {'disconnected': True}


class CustomAppBody(BaseModel):
    name: str
    url: str
    client_id: str | None = None
    client_secret: str | None = None


@apps_api.post('/apps/custom')
async def add_custom_app(body: CustomAppBody, request: Request, ctx: AuthContext = Depends(jwt_only)):
    """Add any remote MCP server by address. Starts a sign-in when it needs one."""
    from cognitrix.api.routes import mcp as mcp_routes

    me = owner_key(ctx.user)
    admin = await is_admin(ctx.user)
    allow_private = _desktop() or admin
    name, url = await mcp_routes._validate(
        name=body.name, transport='http', url=body.url, command=None, owner=me, admin=admin,
    )
    signs_in = await oauth.needs_sign_in(url, allow_private=allow_private)
    row = MCPServer(
        name=name, owner_id=me, transport='http', url=url, enabled=True,
        auth_type='oauth' if signs_in else 'none', status='' if signs_in else 'connected', account='',
    )
    await row.save()
    row = await MCPServer.find_one({'owner_id': me, 'name': name}) or row
    result: dict[str, Any] = {'connection': _connection(row), 'flow': None}
    if signs_in:
        try:
            result['flow'] = await oauth.start_sign_in(
                row, me, base_url(request), allow_private=allow_private,
                client_id=body.client_id, client_secret=body.client_secret,
            )
        except oauth.SignInError as exc:
            result['error'] = exc.user_message
    return result


class SignInBody(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None


@apps_api.post('/servers/{server_id}/sign-in')
async def sign_in_server(server_id: str, request: Request, body: SignInBody | None = None,
                         ctx: AuthContext = Depends(jwt_only)):
    """Sign in (again) to one of your own servers."""
    row = await _own_row(server_id, ctx)
    if (row.transport or 'http') == 'stdio':
        raise HTTPException(status_code=400, detail="Local servers don't sign in")
    if (row.auth_type or '') != 'oauth':
        await MCPServer.update_one({'id': row.id}, {'auth_type': 'oauth'})
        row.auth_type = 'oauth'
    body = body or SignInBody()
    try:
        return await oauth.start_sign_in(
            row, owner_key(ctx.user), base_url(request), allow_private=await _allow_private(ctx),
            client_id=body.client_id, client_secret=body.client_secret,
        )
    except oauth.SignInError as exc:
        raise _sign_in_error(exc)


@apps_api.post('/servers/{server_id}/sign-out')
async def sign_out_server(server_id: str, ctx: AuthContext = Depends(jwt_only)):
    row = await _own_row(server_id, ctx)
    await oauth.sign_out(row)
    return {'disconnected': True}


@apps_api.get('/oauth/flows/{flow_id}')
async def sign_in_status(flow_id: str, ctx: AuthContext = Depends(jwt_only)):
    status = oauth.flow_status(flow_id, owner_key(ctx.user))
    if status is None:
        raise HTTPException(status_code=404, detail='This sign-in has expired. Start again.')
    return status


@apps_api.get('/oauth/callback')
async def sign_in_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """Where apps send the browser back after signing in (no session needed)."""
    name, problem = await oauth.finish_sign_in(state, code, error)
    return oauth.callback_page(name, problem)


@apps_api.get('/oauth/client.json')
async def client_metadata_document(request: Request):
    """This engine's OAuth client ID metadata document (its URL is the client ID)."""
    return JSONResponse(oauth.client_metadata(base_url(request)))
