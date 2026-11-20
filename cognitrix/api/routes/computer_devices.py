"""Paired PCs: a person's own computers their agents can work on
(cognitrix/computer/paired).

On the web (browser sessions):

    GET    /computer/devices                       the person's paired PCs, online or not
    POST   /computer/devices/pair-codes            a one-time code to pair a PC (10 minutes)
    GET    /computer/devices/pair-codes/{code}     waiting | paired | expired
    PATCH  /computer/devices/{id}                  rename {name}
    DELETE /computer/devices/{id}                  unpair (the PC is told and forgets its token)
    GET    /computer/devices/chats/{session_id}    which PC a chat works on
    PUT    /computer/devices/chats/{session_id}    set it {computer: id | null}

From the PC (no session; the code or the PC's token is the proof):

    POST   /computer/devices/claim                 {code} -> whose account it is
    POST   /computer/devices/pair                  {code, name, platform, folders, powers} -> {device_id, token}
    WS     /computer/devices/connect               the PC's connection (first message: hello + token)

In the desktop app, linking this PC to a web account (local engine only):

    GET    /computer/link                          the saved link and its connection
    POST   /computer/link/preview                  {server, code} -> whose account
    POST   /computer/link                          {server, code, name, folders, powers}: pair and connect
    PATCH  /computer/link                          {name?, folders?, powers?, paused?}
    DELETE /computer/link                          unpair this PC
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.computer.paired import PROTOCOL, clean_folders, clean_powers

logger = logging.getLogger('cognitrix.paired')

devices_api = APIRouter(prefix='/computer/devices')
link_api = APIRouter(prefix='/computer/link')


def _user_id(ctx: AuthContext) -> str:
    return str(getattr(ctx.user, 'id', '') or '')


def _account(ctx: AuthContext) -> str:
    user = ctx.user
    name = str(getattr(user, 'name', '') or '').strip()
    email = str(getattr(user, 'email', '') or '').strip()
    return ' · '.join(p for p in (name, email) if p)[:160] or 'your account'


def _address(request: Request) -> str:
    # Behind Fly's proxy the client's own address is in Fly-Client-IP.
    if os.environ.get('FLY_APP_NAME') and request.headers.get('fly-client-ip'):
        return request.headers['fly-client-ip']
    return request.client.host if request.client else 'unknown'


def _pairing_error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={'code': exc.code, 'message': exc.message})


# ---------------------------------------------------------------------------
# The web
# ---------------------------------------------------------------------------

@devices_api.get('')
async def list_devices(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.paired.store import devices_of, view
    return {'devices': [view(d) for d in await devices_of(_user_id(ctx))]}


@devices_api.post('/pair-codes')
async def make_pair_code(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.paired.store import make_code
    item = make_code(_user_id(ctx), _account(ctx))
    server = str(request.base_url).rstrip('/')
    return {'code': item.code, 'expires_in': int(item.expires - time.time()), 'server': server}


@devices_api.get('/pair-codes/{code}')
async def pair_code_status(code: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.paired.store import code_status, device_of, view
    out = code_status(_user_id(ctx), code)
    if out.get('status') == 'paired':
        device = await device_of(_user_id(ctx), out.get('device_id'))
        if device is not None:
            out['device'] = view(device)
    return out


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@devices_api.patch('/{device_id}')
async def rename_device(device_id: str, body: RenameBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.paired.hub import hub
    from cognitrix.computer.paired.store import device_of, view
    from cognitrix.models.paired_device import PairedDevice
    device = await device_of(_user_id(ctx), device_id)
    if device is None:
        raise HTTPException(status_code=404, detail='No such PC')
    name = body.name.strip()[:80]
    await PairedDevice.update_one({'id': device.id}, {'name': name})
    device.name = name
    conn = hub.get(device.id)
    if conn is not None:
        conn.name = name
    return view(device)


@devices_api.delete('/{device_id}')
async def unpair_device(device_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.paired.hub import hub
    from cognitrix.computer.paired.store import device_of
    from cognitrix.models.paired_device import PairedDevice
    device = await device_of(_user_id(ctx), device_id)
    if device is None:
        raise HTTPException(status_code=404, detail='No such PC')
    await PairedDevice.delete_many({'id': device.id})
    await hub.drop(device.id, {'type': 'unpaired'}, code=4401)
    from cognitrix import audit
    audit.record('settings', f'Unpaired the PC {device.name}', user_id=_user_id(ctx),
                 actor=str(getattr(ctx.user, 'email', '') or ''), outcome='ok')
    return {'ok': True}


@devices_api.get('/{device_id}/screen')
async def device_screen(device_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """A fresh picture of the PC's screen, when its owner allowed apps there."""
    from cognitrix.computer.paired import APPS, SCREEN
    from cognitrix.computer.paired.hub import DeviceUnavailable, hub
    from cognitrix.computer.paired.store import device_of
    device = await device_of(_user_id(ctx), device_id)
    if device is None:
        raise HTTPException(status_code=404, detail='No such PC')
    try:
        text, images = await hub.call_full(device.id, SCREEN, {}, power=APPS, timeout=20)
    except DeviceUnavailable as exc:
        reason = str(exc)
        detail = {
            'offline': f'{device.name} is offline.',
            'paused': f'Agents are paused on {device.name}.',
            f'no_{APPS}': f"{device.name} doesn't allow apps, so its screen isn't shared.",
        }.get(reason, reason)
        raise HTTPException(status_code=409, detail=detail) from None
    if not images:
        raise HTTPException(status_code=502, detail=text.removeprefix('Error: ') or "Couldn't see the screen.")
    return {'image': images[0], 'at': time.time()}


class ChatComputerBody(BaseModel):
    computer: str | None = None


async def _chat_view(session, ctx: AuthContext) -> dict[str, Any]:
    from cognitrix.computer.paired.store import device_of, view
    device = await device_of(_user_id(ctx), session.computer) if session.computer else None
    return {'computer': str(device.id) if device is not None else None,
            'device': view(device) if device is not None else None}


@devices_api.get('/chats/{session_id}')
async def get_chat_computer(session_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.api.routes.sessions import _load_authorized_session
    _, session = await _load_authorized_session(session_id, ctx)
    return await _chat_view(session, ctx)


@devices_api.put('/chats/{session_id}')
async def put_chat_computer(session_id: str, body: ChatComputerBody,
                            ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Work on one of the person's PCs in this chat (from its next message), or null for the usual computer."""
    from cognitrix.api.routes.sessions import _load_authorized_session
    from cognitrix.computer.paired.store import device_of
    _, session = await _load_authorized_session(session_id, ctx)
    if session.run_id:
        raise HTTPException(status_code=400, detail="A task run's computer can't be changed")
    if body.computer and await device_of(_user_id(ctx), body.computer) is None:
        raise HTTPException(status_code=404, detail='No such PC')
    session.computer = body.computer or None
    await session.save()
    return await _chat_view(session, ctx)


# ---------------------------------------------------------------------------
# The PC
# ---------------------------------------------------------------------------

class ClaimBody(BaseModel):
    code: str = Field(max_length=20)


class PairBody(BaseModel):
    code: str = Field(max_length=20)
    name: str = Field(default='', max_length=80)
    platform: str = Field(default='', max_length=80)
    folders: list[Any] = Field(default_factory=list, max_length=50)
    powers: list[str] = Field(default_factory=list, max_length=5)
    version: str = Field(default='', max_length=40)


@devices_api.post('/claim')
async def claim_code(body: ClaimBody, request: Request) -> dict[str, Any]:
    from cognitrix.computer.paired.store import PairingError, claim
    try:
        return claim(body.code, _address(request))
    except PairingError as exc:
        raise _pairing_error(exc) from None


@devices_api.post('/pair')
async def pair_device(body: PairBody, request: Request) -> dict[str, Any]:
    from cognitrix.computer.paired.store import PairingError, pair
    try:
        device, token = await pair(body.code, _address(request), name=body.name, platform=body.platform,
                                   folders=body.folders, powers=body.powers, version=body.version)
    except PairingError as exc:
        raise _pairing_error(exc) from None
    from cognitrix import audit
    audit.record('settings', f'Paired the PC {device.name}', user_id=str(device.user_id), outcome='ok')
    return {'device_id': str(device.id), 'token': token, 'name': device.name}


@devices_api.websocket('/connect')
async def connect(websocket: WebSocket) -> None:
    """A paired PC's connection. Its first message proves which PC it is."""
    from cognitrix.computer.paired.hub import Connection, hub
    from cognitrix.computer.paired.store import device_for_token, now_stamp
    from cognitrix.models.paired_device import PairedDevice

    await websocket.accept()
    try:
        hello = json.loads(await asyncio.wait_for(websocket.receive_text(), 20))
    except Exception:
        await websocket.close(code=4400)
        return
    device = await device_for_token(str(hello.get('token') or '')) if isinstance(hello, dict) else None
    if device is None:
        try:
            await websocket.send_text(json.dumps({'type': 'error', 'code': 'revoked',
                                                  'message': 'This PC is not paired with this server any more.'}))
        finally:
            await websocket.close(code=4401)
        return
    folders = clean_folders(hello.get('folders'))
    powers = clean_powers(hello.get('powers'))
    paused = bool(hello.get('paused'))
    await PairedDevice.update_one({'id': device.id}, {
        'last_seen_at': now_stamp(), 'folders_json': json.dumps(folders), 'powers': ','.join(powers),
        'paused': paused, 'version': str(hello.get('version') or '')[:40],
        'platform': str(hello.get('platform') or device.platform or '')[:80],
    })
    conn = Connection(device_id=str(device.id), user_id=str(device.user_id), name=device.name, socket=websocket,
                      loop=asyncio.get_running_loop(), platform=str(hello.get('platform') or device.platform or ''),
                      folders=folders, powers=powers, paused=paused)
    old = hub.register(conn)
    if old is not None:
        try:
            await old.socket.close(code=4000)
        except Exception:
            pass
        hub.unregister(old)
    await conn.send({'type': 'welcome', 'protocol': PROTOCOL, 'device_id': str(device.id), 'name': device.name})
    last_touch = asyncio.get_running_loop().time()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get('type')
            if kind == 'result':
                hub.answer(conn, message)
            elif kind == 'state':
                conn.folders = clean_folders(message.get('folders'))
                conn.powers = clean_powers(message.get('powers'))
                conn.paused = bool(message.get('paused'))
                await PairedDevice.update_one({'id': device.id}, {
                    'folders_json': json.dumps(conn.folders), 'powers': ','.join(conn.powers), 'paused': conn.paused})
            elif kind == 'unpair':
                await PairedDevice.delete_many({'id': device.id})
                break
            now = asyncio.get_running_loop().time()
            if now - last_touch > 600:
                last_touch = now
                await PairedDevice.update_one({'id': device.id}, {'last_seen_at': now_stamp()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        logger.debug('Paired PC connection failed', exc_info=True)
    finally:
        hub.unregister(conn)
        try:
            await PairedDevice.update_one({'id': device.id}, {'last_seen_at': now_stamp()})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The desktop app: link this PC to a web account
# ---------------------------------------------------------------------------

def _desktop_only() -> None:
    if os.environ.get('COGNITRIX_DESKTOP') != '1':
        raise HTTPException(status_code=404, detail='Only the desktop app can link its PC to a web account.')


def _link_error(exc) -> HTTPException:
    # Shown as is on the desktop app's screen.
    return HTTPException(status_code=400, detail=exc.message)


def _link_view() -> dict[str, Any]:
    from cognitrix.computer.paired.connector import default_name, get_connector, platform_label, public_config
    return {**public_config(), **get_connector().view(), 'default_name': default_name(),
            'platform': platform_label()}


@link_api.get('')
async def get_link(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    return _link_view()


class PreviewBody(BaseModel):
    server: str = Field(max_length=300)
    code: str = Field(max_length=20)


@link_api.post('/preview')
async def preview_link(body: PreviewBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    from cognitrix.computer.paired.connector import LinkError, preview
    try:
        return await preview(body.server, body.code)
    except LinkError as exc:
        raise _link_error(exc) from None


class LinkBody(BaseModel):
    server: str = Field(max_length=300)
    code: str = Field(max_length=20)
    name: str = Field(default='', max_length=80)
    folders: list[Any] = Field(default_factory=list, max_length=50)
    powers: list[str] = Field(default_factory=list, max_length=5)


@link_api.post('')
async def make_link(body: LinkBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    from cognitrix.computer.paired.connector import LinkError, get_connector, link
    try:
        await link(body.server, body.code, name=body.name, folders=body.folders, powers=body.powers)
    except LinkError as exc:
        raise _link_error(exc) from None
    connector = get_connector()
    await connector.stop()
    connector.start()
    await asyncio.sleep(0.5)
    return _link_view()


class LinkChange(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    folders: list[Any] | None = Field(default=None, max_length=50)
    powers: list[str] | None = Field(default=None, max_length=5)
    paused: bool | None = None


@link_api.patch('')
async def change_link(body: LinkChange, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    from cognitrix.computer.paired.connector import LinkError, check_folders, get_connector, load_config, save_config
    cfg = load_config()
    if not cfg.get('token'):
        raise HTTPException(status_code=404, detail='This PC is not linked')
    try:
        if body.folders is not None:
            cfg['folders'] = check_folders(body.folders)
    except LinkError as exc:
        raise _link_error(exc) from None
    if body.powers is not None:
        cfg['powers'] = clean_powers(body.powers)
    if body.paused is not None:
        cfg['paused'] = bool(body.paused)
    if body.name is not None and body.name.strip():
        cfg['name'] = body.name.strip()[:80]
    save_config(cfg)
    await get_connector().push_state()
    return _link_view()


@link_api.delete('')
async def remove_link(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    from cognitrix.computer.paired.connector import get_connector
    await get_connector().unpair()
    return _link_view()


async def start_saved_link() -> None:
    """At startup in the desktop app: reconnect a PC linked earlier."""
    if os.environ.get('COGNITRIX_DESKTOP') != '1':
        return
    from cognitrix.computer.paired.connector import get_connector, load_config
    if load_config().get('token'):
        get_connector().start()
