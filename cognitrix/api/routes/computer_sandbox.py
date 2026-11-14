"""The agents' sandboxed computer, for the live screen panel.

    GET  /computer/sandbox                         is it on, is it running, which desktops
    POST /computer/sandbox/desktops/{name}/viewer  a one-time ticket + password for the live screen
    WS   /computer/sandbox/screen?ticket=...       the live screen (VNC over websocket), proxied
    POST /computer/sandbox/desktops/{name}/hold    take over: the agent pauses; returns the password that
                                                   lets the screen take mouse and keyboard
    POST /computer/sandbox/desktops/{name}/release hand back
    POST /computer/sandbox/update                  restart onto a newer image (files and logins stay)
    POST /computer/sandbox/reset                   throw the computer away (files and logins too)

Browser sessions only. The container's own ports listen on 127.0.0.1 and need
a token the browser never sees; the screen stream goes through here so each
person only reaches their own sandbox.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.computer.sandbox import check_name

logger = logging.getLogger(__name__)

sandbox_api = APIRouter(prefix='/computer/sandbox')

_TICKET_TTL_S = 60
_tickets: dict[str, tuple[str, str, float]] = {}  # ticket -> (user key, desktop, expires)


def _owner(ctx: AuthContext) -> str:
    # Same key the computer tools use for this person (Actor.user_id).
    user = ctx.user
    return str(getattr(user, 'id', None) or getattr(user, 'email', 'anon'))


def _display_name(ctx: AuthContext) -> str:
    user = ctx.user
    return str(getattr(user, 'name', None) or getattr(user, 'email', None) or 'the person')[:80]


def _manager():
    from cognitrix.computer.sandbox.client import get_manager, sandbox_mode

    if not sandbox_mode():
        raise HTTPException(status_code=404, detail="The agents' sandboxed computer is off.")
    return get_manager()


def _desktop(name: str) -> str:
    try:
        return check_name(name)
    except ValueError:
        raise HTTPException(status_code=400, detail='Bad desktop name') from None


async def _box(ctx: AuthContext, start: bool = False):
    mgr = _manager()
    owner = _owner(ctx)
    box = await run_in_threadpool(mgr.sandbox if start else mgr.existing, owner)
    if box is None:
        raise HTTPException(status_code=404, detail="The agents' computer isn't running.")
    return box


@sandbox_api.get('')
async def status(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.client import get_manager, mode_from_env, sandbox_mode

    if not sandbox_mode():
        return {'enabled': False, 'running': False, 'desktops': [], 'from_env': mode_from_env()}
    try:
        box = await run_in_threadpool(get_manager().existing, _owner(ctx))
    except Exception:  # Docker missing or stopped
        logger.warning("Couldn't reach the agents' computer", exc_info=True)
        return {'enabled': True, 'running': False, 'desktops': [],
                'error': "Couldn't reach Docker. Check that Docker is installed and running."}
    if box is None:
        return {'enabled': True, 'running': False, 'desktops': []}
    desktops = await run_in_threadpool(box.desktops)
    mgr = get_manager()
    try:
        update = bool(await run_in_threadpool(mgr.update_available, box))
    except Exception:
        update = False
    return {'enabled': True, 'running': True, 'desktops': desktops, 'update_available': update}


@sandbox_api.get('/settings')
async def get_settings(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.client import IMAGE, docker_status, mode_from_env, sandbox_mode

    return {'enabled': bool(sandbox_mode()), 'from_env': mode_from_env(), 'image': IMAGE,
            'docker': await run_in_threadpool(docker_status)}


class SettingsBody(BaseModel):
    enabled: bool


@sandbox_api.put('/settings')
async def put_settings(body: SettingsBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Switch the sandboxed computer on or off for everyone on this server
    (admins only; the environment variable, when set, wins)."""
    from cognitrix.agents.access import is_admin
    from cognitrix.computer.sandbox.client import mode_from_env, save_enabled

    if not await is_admin(ctx.user):
        raise HTTPException(status_code=403, detail='Only an admin can change this.')
    if mode_from_env():
        raise HTTPException(status_code=409, detail='COGNITRIX_COMPUTER_SANDBOX is set on the server, so it decides.')
    save_enabled(body.enabled)
    return await get_settings(ctx)


@sandbox_api.post('/desktops/{name}/viewer')
async def viewer(name: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    name = _desktop(name)
    box = await _box(ctx)
    if name not in box.passwords:
        await run_in_threadpool(box.desktop, name)  # also returns its password
    now = time.monotonic()
    for key, (_, _, expires) in list(_tickets.items()):
        if expires < now:
            _tickets.pop(key, None)
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = (_owner(ctx), name, now + _TICKET_TTL_S)
    return {'ticket': ticket, 'password': box.passwords.get(name, ''), 'path': f'computer/sandbox/screen?ticket={ticket}'}


class HoldBody(BaseModel):
    by: str | None = Field(default=None, max_length=80)


@sandbox_api.post('/desktops/{name}/hold')
async def hold(name: str, body: HoldBody | None = None, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    box = await _box(ctx)
    by = (body.by if body and body.by else None) or _display_name(ctx)
    out = await run_in_threadpool(box.request, 'POST', f'/desktops/{_desktop(name)}/hold', {'by': by})
    # The screen password that accepts mouse and keyboard; reconnect with it.
    return {'ok': bool(out.get('ok')), 'held_by': by, 'password': out.get('vnc_password', '')}


@sandbox_api.post('/desktops/{name}/release')
async def release(name: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    box = await _box(ctx)
    out = await run_in_threadpool(box.request, 'POST', f'/desktops/{_desktop(name)}/release')
    return {'ok': bool(out.get('ok'))}


@sandbox_api.post('/update')
async def update(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Restart the person's computer on the newest downloaded image. Open
    windows close; files and logins are kept."""
    mgr = _manager()
    await run_in_threadpool(mgr.update, _owner(ctx))
    return {'ok': True}


@sandbox_api.post('/reset')
async def reset(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    mgr = _manager()
    await run_in_threadpool(mgr.reset, _owner(ctx))
    return {'ok': True}


@sandbox_api.websocket('/screen')
async def screen(websocket: WebSocket, ticket: str = '') -> None:
    # Browsers can't put a session header on a websocket, so a ticket from
    # POST .../viewer (one use, one minute) stands in for it.
    entry = _tickets.pop(ticket, None)
    if entry is None or entry[2] < time.monotonic():
        await websocket.close(code=4401)
        return
    owner, name, _ = entry
    from cognitrix.computer.sandbox.client import get_manager

    try:
        box = await run_in_threadpool(get_manager().existing, owner)
    except Exception:
        box = None
    if box is None:
        await websocket.close(code=4404)
        return
    await websocket.accept(subprotocol='binary' if 'binary' in websocket.scope.get('subprotocols', []) else None)
    url = f'ws://127.0.0.1:{box.viewer_port}/websockify?token={name}'
    async with aiohttp.ClientSession() as session:
        try:
            upstream = await session.ws_connect(url, protocols=('binary',), max_msg_size=0)
        except aiohttp.ClientError:
            await websocket.close(code=4502)
            return

        async def down() -> None:
            async for msg in upstream:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    await websocket.send_bytes(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    await websocket.send_text(msg.data)
                else:
                    break

        async def up() -> None:
            while True:
                msg = await websocket.receive()
                if msg.get('type') == 'websocket.disconnect':
                    break
                if msg.get('bytes') is not None:
                    await upstream.send_bytes(msg['bytes'])
                elif msg.get('text') is not None:
                    await upstream.send_str(msg['text'])

        tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for t in tasks:
                t.cancel()
            await upstream.close()
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass  # the viewer already left
