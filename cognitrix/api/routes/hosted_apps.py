"""Apps: live web apps agents built (see ``cognitrix/hosted_apps``).

    GET    /api/v1/hosted-apps                      apps I can open
    GET    /api/v1/hosted-apps/{id}                 one app, its versions
    PATCH  /api/v1/hosted-apps/{id}                 title, pinned, sleep_after
    PUT    /api/v1/hosted-apps/{id}/sharing         people / everyone on this server
    POST   /api/v1/hosted-apps/{id}/open            a link to open it with (12 h)
    POST   /api/v1/hosted-apps/{id}/start|stop      wake / sleep
    POST   /api/v1/hosted-apps/{id}/promote         make a kept version live again
    POST   /api/v1/hosted-apps/{id}/redeploy        publish its folder again as a new version
    GET    /api/v1/hosted-apps/{id}/logs            the end of its output
    DELETE /api/v1/hosted-apps/{id}

The app itself is served at ``/a/<slug>/~<token>/...`` (``app_proxy`` below,
mounted at the app root): the token in the path says who is looking, and the
response's Content-Security-Policy sandboxes the page into an opaque origin so
the app's code can never read this app's storage or act as the viewer.
``/a/<slug>/`` without a token sends people to ``/apps/open/<slug>``, which
signs them in and comes back with one.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.hosted_apps import store
from cognitrix.hosted_apps.models import HostedApp
from cognitrix.hosted_apps.runner import RunnerError, default_runtime, runner_for
from cognitrix.session_ownership import principal_key

logger = logging.getLogger('cognitrix.apps')

hosted_apps_api = APIRouter(prefix='/hosted-apps')
app_proxy = APIRouter()


def _me(ctx: AuthContext) -> str:
    return principal_key(ctx.user)


def _bad(exc: store.AppError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


async def _app(ref: str, ctx: AuthContext) -> tuple[HostedApp, str]:
    try:
        return await store.app_for(ref, _me(ctx))
    except store.AppError as exc:
        raise _bad(exc) from None


def _owner_only(role: str) -> None:
    if role != store.ROLE_OWNER:
        raise HTTPException(status_code=403, detail='Only the person who owns this app can do that')


def _editor_only(role: str) -> None:
    if not store.can_edit(role):
        raise HTTPException(status_code=403, detail='You can open this app but not change it')


@hosted_apps_api.get('')
async def list_apps(request: Request, ctx: AuthContext = Depends(jwt_only)):
    store.remember_engine_url(str(request.base_url))
    me = _me(ctx)
    people, agents = await store.names()
    runtime = default_runtime()
    apps = []
    for app, role in await store.visible(me):
        row = store.project(app, role, people=people, agents=agents)
        # The live picture on each card: a token that never wakes the app.
        row['thumb_url'] = store.app_path(app.slug, store.open_token(app.slug, me, thumb=True)) \
            if app.status == 'online' else ''
        apps.append(row)
    return {'apps': apps, 'runtime': runtime}


@hosted_apps_api.get('/{ref}')
async def get_app(ref: str, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    people, agents = await store.names()
    out = store.project(app, role, people=people, agents=agents)
    if store.can_edit(role):
        out['versions'] = [store.project_version(v, agents) for v in await store.versions(app)]
        out['task'] = await store.task_info(app.task_id)
    if role == store.ROLE_OWNER:
        out['people'] = [{'id': k, 'name': v} for k, v in people.items() if k != app.owner_id]
    return out


class AppPatch(BaseModel):
    title: str | None = Field(default=None, max_length=store.MAX_TITLE)
    pinned: bool | None = None
    sleep_after: int | None = Field(default=None, ge=0, le=24 * 60)
    description: str | None = Field(default=None, max_length=500)


@hosted_apps_api.patch('/{ref}')
async def patch_app(ref: str, body: AppPatch, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    _owner_only(role)
    changes: dict[str, Any] = {}
    if body.title is not None:
        try:
            changes['title'] = store._clean_title(body.title)
        except store.AppError as exc:
            raise _bad(exc) from None
    if body.pinned is not None:
        changes['pinned'] = body.pinned
    if body.sleep_after is not None:
        changes['sleep_after'] = int(body.sleep_after)
    if body.description is not None:
        changes['description'] = body.description.strip()
    await store._set(app, **changes)
    return await get_app(ref, ctx)


class Sharing(BaseModel):
    people: dict[str, str] | None = None
    everyone: str | None = None
    """'' (nobody else), 'view' or 'edit'."""


@hosted_apps_api.put('/{ref}/sharing')
async def share_app(ref: str, body: Sharing, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    _owner_only(role)
    try:
        await store.set_sharing(app, people=body.people, everyone=body.everyone)
    except store.AppError as exc:
        raise _bad(exc) from None
    return await get_app(ref, ctx)


@hosted_apps_api.post('/{ref}/open')
async def open_app(ref: str, request: Request, thumb: bool = False, ctx: AuthContext = Depends(jwt_only)):
    """A link to the running app for me (good for 12 hours). ``thumb``: for the
    small live picture on the Apps page, which never wakes it."""
    store.remember_engine_url(str(request.base_url))
    app, _ = await _app(ref, ctx)
    return {'url': store.app_path(app.slug, store.open_token(app.slug, _me(ctx), thumb=thumb)), 'status': app.status}


async def _runner(app: HostedApp):
    try:
        return await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
    except RunnerError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from None


@hosted_apps_api.post('/{ref}/start')
async def start_app(ref: str, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    _editor_only(role)
    ok = await store.wake(app)
    if not ok:
        raise HTTPException(status_code=502, detail=app.last_error or 'The app did not start')
    return await get_app(ref, ctx)


@hosted_apps_api.post('/{ref}/stop')
async def stop_app(ref: str, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    _editor_only(role)
    runner = await _runner(app)
    try:
        await asyncio.to_thread(runner.stop, app.slug)
    except RunnerError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    if app.status == 'online':
        await store._set(app, status='asleep')
    return await get_app(ref, ctx)


class Promote(BaseModel):
    version: int = Field(ge=1)


@hosted_apps_api.post('/{ref}/promote')
async def promote_app(ref: str, body: Promote, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    _editor_only(role)
    try:
        await store.promote(app, body.version)
    except store.AppError as exc:
        raise _bad(exc) from None
    return await get_app(ref, ctx)


class Redeploy(BaseModel):
    note: str = Field(default='', max_length=store.MAX_NOTE)


@hosted_apps_api.post('/{ref}/redeploy')
async def redeploy_app(ref: str, body: Redeploy | None = None, ctx: AuthContext = Depends(jwt_only)):
    """Publish the app's folder again, e.g. after its branch changed."""
    app, role = await _app(ref, ctx)
    _editor_only(role)
    try:
        await store.publish(user_key=_me(ctx), title=app.title, folder=app.folder, command=app.command,
                            install=app.install, note=(body.note if body else '') or 'Redeployed from its folder',
                            app_id=str(app.id), port=app.port, env=app.env, branch=app.branch)
    except store.AppError as exc:
        raise _bad(exc) from None
    return await get_app(ref, ctx)


@hosted_apps_api.get('/{ref}/logs')
async def app_logs(ref: str, lines: int = 200, version: int = 0, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    _editor_only(role)
    return {'log': await store.logs(app, max(1, min(lines, 2000)), version)}


@hosted_apps_api.delete('/{ref}')
async def delete_app(ref: str, ctx: AuthContext = Depends(jwt_only)):
    app, role = await _app(ref, ctx)
    _owner_only(role)
    await store.delete_app(app)
    return {'ok': True}


# ------------------------------------------------------------------ the app itself

_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers',
        'transfer-encoding', 'upgrade', 'host', 'content-length', 'cookie', 'x-cognitrix-sandbox-token'}
_DROP_OUT = {'connection', 'keep-alive', 'transfer-encoding', 'content-length', 'set-cookie',
             'content-security-policy', 'x-frame-options', 'access-control-allow-origin',
             'access-control-allow-credentials', 'content-encoding', 'strict-transport-security',
             'server', 'date'}
# Scripts and forms run; the page gets an opaque origin (no allow-same-origin),
# so it can't read Cognitrix's storage or call its API as the viewer.
APP_CSP = ("sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals "
           "allow-downloads; frame-ancestors 'self'")
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10), follow_redirects=False)
    return _client


def _page(title: str, body: str, status: int = 200, refresh: int = 0) -> HTMLResponse:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ''
    return HTMLResponse(
        f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
        f'initial-scale=1">{meta}<title>{html.escape(title)}</title><style>'
        ':root{color-scheme:light dark;--bg:#f7f7f4;--fg:#16181a;--dim:#5a5f5c;--accent:#c6f24e}'
        '@media (prefers-color-scheme:dark){:root{--bg:#0b0c0e;--fg:#f4f4f2;--dim:#8b8f8b}}'
        'body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);'
        'font:15px/1.5 system-ui,sans-serif}.b{max-width:380px;padding:24px;text-align:center}'
        'h1{font-size:18px;margin:0 0 8px}p{color:var(--dim);margin:0 0 14px}'
        '.bar{height:4px;border-radius:2px;background:rgba(127,127,127,.2);overflow:hidden}'
        '.bar i{display:block;height:100%;width:40%;background:var(--accent);animation:m 1.2s ease-in-out infinite}'
        '@keyframes m{0%{transform:translateX(-100%)}100%{transform:translateX(250%)}}</style></head>'
        f'<body><div class="b">{body}</div></body></html>',
        status_code=status, headers={'Cache-Control': 'no-store', 'Content-Security-Policy': APP_CSP,
                                     'Referrer-Policy': 'no-referrer'})


def _cors(headers: dict[str, str]) -> dict[str, str]:
    headers.update({'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': '*',
                    'Access-Control-Allow-Headers': '*', 'Access-Control-Max-Age': '600'})
    return headers


def _header_safe(value: str) -> str:
    """Viewer details as an ASCII header: plain names pass as they are, anything else
    (José, a % sign, line breaks) is percent-encoded UTF-8, so apps can always unquote it."""
    return urllib.parse.quote(str(value or ''), safe=" !#$&'()*+,/:;=?@[]~")


_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']


@app_proxy.api_route('/a/{slug}', methods=_METHODS, include_in_schema=False)
@app_proxy.api_route('/a/{slug}/', methods=_METHODS, include_in_schema=False)
async def app_no_token(slug: str):
    return _to_open_page(slug)


_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,47}$')
_TOKEN = re.compile(r'^[A-Za-z0-9_.-]{1,2048}$')


def _to_open_page(slug: str) -> Response:
    """Send a visit without a token to the sign-in page for that app (same site only)."""
    if not _SLUG.match(slug):
        return Response('No such app.', status_code=404, media_type='text/plain')
    return RedirectResponse('/apps/open/' + slug, status_code=302)


@app_proxy.api_route('/a/{slug}/{rest:path}', methods=_METHODS, include_in_schema=False)
async def app_request(slug: str, rest: str, request: Request):
    if not rest.startswith('~'):
        return _to_open_page(slug)
    token, _, path = rest[1:].partition('/')
    if not _SLUG.match(slug) or not _TOKEN.match(token):
        return Response('No such app.', status_code=404, media_type='text/plain')
    if not path and not request.url.path.endswith('/'):
        # /a/x/~tok -> /a/x/~tok/ so the app's relative links resolve inside the prefix.
        return RedirectResponse('/a/' + slug + '/~' + token + '/', status_code=302)
    if request.method == 'OPTIONS':
        return Response(status_code=204, headers=_cors({}))
    read = store.read_open_token(token, slug)
    viewer, thumb = read if read else ('', False)
    app = await store.by_slug(slug) if viewer else None
    role = store.role_of(app, viewer) if app is not None else None
    if not viewer or app is None or role is None:
        return _page('Link expired', '<h1>This link has expired</h1><p>Open the app again from Cognitrix.</p>'
                     f'<p><a href="/apps/open/{html.escape(slug)}" target="_top">Open it again</a></p>', status=403)

    navigation = request.method == 'GET' and 'text/html' in request.headers.get('accept', '') and not thumb
    if thumb and app.status != 'online':
        return Response(status_code=204, headers=_cors({'Content-Security-Policy': APP_CSP}))
    if app.status in ('asleep', 'stopped') or store.waking(app.slug):
        if navigation:
            if not store.waking(app.slug):
                asyncio.create_task(store.wake(app))
            return _page('Waking up', f'<h1>Waking up {html.escape(app.title)}</h1><p>This app sleeps when '
                         'nobody is using it. It usually takes 5 to 10 seconds to start.</p><div class="bar">'
                         '<i></i></div>', status=503, refresh=2)
        if not await store.wake(app):
            return Response('The app is not running.', status_code=503, headers=_cors({}))
    if app.status in ('building', 'failed') and not app.version:
        return _page('Not ready', f'<h1>{html.escape(app.title)} is not running yet</h1>'
                     f'<p>{html.escape(app.last_error or "It is still being built.")}</p>', status=503,
                     refresh=5 if app.status == 'building' else 0)

    try:
        runner = await asyncio.to_thread(runner_for, app.runtime, app.owner_id)
        base, extra = await asyncio.to_thread(runner.target, app.slug)
    except RunnerError as exc:
        return _page('Unavailable', f'<h1>{html.escape(app.title)} is unavailable</h1><p>{html.escape(exc.message)}</p>',
                     status=503)
    if not thumb:
        store.touch(app.slug)
    if navigation:
        try:
            await store.record_visit(app)
        except Exception:
            logger.debug('visit not counted', exc_info=True)

    user = await _viewer_user(viewer)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    headers.update(extra)
    prefix = f'/a/{slug}/~{token}'
    headers.update({
        'X-Forwarded-Prefix': prefix, 'X-Forwarded-Proto': request.url.scheme,
        'X-Cognitrix-User-Id': _header_safe(viewer), 'X-Cognitrix-User-Name': _header_safe(user.get('name', '')),
        'X-Cognitrix-User-Email': _header_safe(user.get('email', '')), 'X-Cognitrix-User-Role': role,
        'Accept-Encoding': 'identity',
    })
    query = request.url.query
    url = f'{base}/{path}' + (f'?{query}' if query else '')
    body = await request.body()
    try:
        upstream = await _http().send(
            _http().build_request(request.method, url, headers=headers, content=body or None), stream=True)
    except httpx.HTTPError as exc:
        logger.info('app %s: %s', slug, exc)
        return _page('Unavailable', f'<h1>{html.escape(app.title)} did not answer</h1><p>Try again in a '
                     'moment. If it keeps happening, ask the agent that built it to check its log.</p>', status=502)

    out_headers: dict[str, str] = {}
    for key, value in upstream.headers.multi_items():
        lk = key.lower()
        if lk in _DROP_OUT:
            continue
        if lk == 'location' and value.startswith('/') and not value.startswith('//'):
            value = prefix + value
        out_headers[key] = value
    out_headers.update(_cors({'Content-Security-Policy': APP_CSP, 'Referrer-Policy': 'no-referrer',
                              'X-Content-Type-Options': 'nosniff', 'X-Robots-Tag': 'noindex',
                              'Cache-Control': upstream.headers.get('cache-control', 'private, no-cache')}))

    async def stream():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=out_headers,
                             media_type=upstream.headers.get('content-type'))


_users: dict[str, tuple[float, dict[str, str]]] = {}


async def _viewer_user(key: str) -> dict[str, str]:
    import time

    hit = _users.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    info = {'name': '', 'email': ''}
    try:
        from cognitrix.models import User

        user = await User.get(key)
        if user is None:
            rows = await User.find({'email': key}) or []
            user = rows[0] if rows else None
        if user is not None:
            info = {'name': str(getattr(user, 'name', '') or ''), 'email': str(getattr(user, 'email', '') or '')}
    except Exception:
        pass
    _users[key] = (time.time(), info)
    return info
