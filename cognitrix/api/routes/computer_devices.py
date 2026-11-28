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
    GET    /computer/devices/chats/{session_id}/changes  files its agents changed on the PC
    POST   /computer/devices/chats/{session_id}/undo     {paths?}: put them back

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


@devices_api.get('/pair-requests')
async def pair_requests(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """PCs on my tailnet waiting for my yes."""
    from cognitrix.computer.paired.store import requests_of

    return {'requests': requests_of(_user_id(ctx))}


async def _decide(request_id: str, approve: bool, ctx: AuthContext) -> dict[str, Any]:
    from cognitrix import audit
    from cognitrix.computer.paired.store import PairingError, decide_request, requests_of

    try:
        item = await decide_request(_user_id(ctx), request_id, approve, ctx.sid)
    except PairingError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    audit.record('settings', f"{'Approved' if approve else 'Declined'} the PC {item['name']} asking to pair "
                             f"(Tailscale login {item['login']})",
                 user_id=_user_id(ctx), actor=str(getattr(ctx.user, 'email', '') or ''), outcome='ok')
    return {'requests': requests_of(_user_id(ctx))}


@devices_api.post('/pair-requests/{request_id}/approve')
async def approve_pair_request(request_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    return await _decide(request_id, True, ctx)


@devices_api.post('/pair-requests/{request_id}/deny')
async def deny_pair_request(request_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    return await _decide(request_id, False, ctx)


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
    if getattr(device, 'sign_in_sid', ''):
        from cognitrix import signins
        await signins.end(_user_id(ctx), device.sign_in_sid)
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


# ---------------------------------------------------------------------------
# The PC handing tasks to the web account (the 'tasks' power, its owner's choice
# on the PC). These use the PC's own token, not a web sign-in.
# ---------------------------------------------------------------------------

async def _calling_pc(request: Request):
    """The paired PC behind this request's token, with its owner's sign-in."""
    from cognitrix.computer.paired import TASKS
    from cognitrix.computer.paired.store import device_for_token, powers_of
    from cognitrix.models.user import User
    auth = request.headers.get('authorization', '')
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    device = await device_for_token(token)
    if device is None:
        raise HTTPException(status_code=401, detail='This PC is not paired with this server any more.')
    if TASKS not in powers_of(device):
        raise HTTPException(status_code=403, detail="This PC isn't allowed to send tasks to the web account. "
                            'Switch on "Send tasks" on the PC.')
    user = await User.get(device.user_id)
    if user is None or getattr(user, 'disabled', False):
        raise HTTPException(status_code=403, detail='The web account is not available.')
    return device, AuthContext(user=user)


@devices_api.get('/link/agents')
async def pc_agents(request: Request) -> dict[str, Any]:
    """The web account's agents, for picking who does a task sent from the PC."""
    from cognitrix.agents import access as agent_access
    _, ctx = await _calling_pc(request)
    agents = await agent_access.visible_agents(agent_access.owner_key(ctx.user))
    return {'agents': [{'id': str(a.id), 'name': a.name} for a in agents]}


class PcTaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=20000)
    steps: list[str] = Field(default_factory=list, max_length=50)
    agent_id: str | None = Field(default=None, max_length=80)


@devices_api.post('/link/tasks')
async def pc_send_task(body: PcTaskBody, request: Request) -> dict[str, Any]:
    """Make a task on the web account and start it."""
    from cognitrix.agents import access as agent_access
    from cognitrix.api.routes.tasks import _check_task_references, start_task_run
    from cognitrix.models.paired_device import PairedDevice
    from cognitrix.tasks.base import Task
    device, ctx = await _calling_pc(request)
    agents = await agent_access.visible_agents(agent_access.owner_key(ctx.user))
    ids = [str(a.id) for a in agents]
    agent_id = body.agent_id if body.agent_id in ids else (ids[0] if ids else None)
    if agent_id is None:
        raise HTTPException(status_code=422, detail='The web account has no agents yet.')
    steps = {str(i): {'step': s.strip()[:2000], 'done': False} for i, s in enumerate(x for x in body.steps if x.strip())}
    task = Task(title=body.title.strip(), description=(body.description.strip() or body.title.strip()),
                step_instructions=steps, assigned_agents=[agent_id], owner_id=str(ctx.user.id))
    await _check_task_references(ctx, task)
    await task.save()
    sent = [t for t in str(device.sent_tasks or '').split(',') if t][-49:] + [str(task.id)]
    await PairedDevice.update_one({'id': device.id}, {'sent_tasks': ','.join(sent)})
    from cognitrix import audit
    audit.record('settings', f'{device.name} sent a task to the web account', user_id=str(ctx.user.id),
                 actor=f'pc:{device.name}', outcome='ok')
    run = await start_task_run(str(task.id), None, ctx)
    return {'task_id': str(task.id), 'run_id': run.get('run_id'), 'status': str(run.get('status') or '')}


@devices_api.get('/link/tasks')
async def pc_sent_tasks(request: Request) -> dict[str, Any]:
    """The tasks this PC sent, newest first, with their latest result."""
    from cognitrix.tasks.base import Task
    device, _ = await _calling_pc(request)
    out = []
    for task_id in reversed([t for t in str(device.sent_tasks or '').split(',') if t][-20:]):
        task = await Task.get(task_id)
        if task is None or task.deleted_at:
            continue
        status = getattr(task.status, 'value', task.status)
        results = list(task.results or [])
        out.append({'id': str(task.id), 'title': task.title, 'status': str(status),
                    'result': str(results[-1])[:4000] if results else ''})
    return {'tasks': out}


@devices_api.post('/relay', include_in_schema=False)
async def relay_call(request: Request) -> dict[str, Any]:
    """The task worker reaching a PC through this process (see relay.py)."""
    from cognitrix.computer.paired import relay
    from cognitrix.computer.paired.hub import DeviceUnavailable, hub, status
    body = await request.body()
    if not relay.verify(body, request.headers.get(relay.HEADER, '')):
        raise HTTPException(status_code=403, detail='Not allowed')
    hub.serving = True
    data = json.loads(body or b'{}')
    device_id = str(data.get('device_id') or '')
    if data.get('op') == 'status':
        return await status(device_id) or {'online': False}
    if data.get('op') != 'call':
        raise HTTPException(status_code=400, detail='Unknown op')
    try:
        text, images = await hub.call_full(
            device_id, str(data.get('tool') or ''), data.get('args') or {}, power=str(data.get('power') or ''),
            ctx=data.get('ctx') or {}, timeout=min(float(data.get('timeout') or 150), 900))
    except DeviceUnavailable as exc:
        # Only the reason the hub gives (offline, paused, no_<power>, or a sentence for the person).
        return {'error': exc.args[0] if exc.args else 'offline'}
    return {'text': text, 'images': images}


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


class UndoBody(BaseModel):
    # None puts back every file the chat changed on the PC.
    paths: list[str] | None = Field(default=None, max_length=500)


async def _chat_pc_call(session_id: str, ctx: AuthContext, op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Ask the chat's PC about the files its agents changed there."""
    from cognitrix.api.routes.sessions import _load_authorized_session
    from cognitrix.computer.paired import FILES
    from cognitrix.computer.paired.hub import DeviceUnavailable, hub
    from cognitrix.computer.paired.store import device_of
    _, session = await _load_authorized_session(session_id, ctx)
    device = await device_of(_user_id(ctx), session.computer) if getattr(session, 'computer', None) else None
    if device is None:
        raise HTTPException(status_code=404, detail="This chat doesn't work on one of your PCs")
    try:
        text = await hub.call(device.id, op, args, power=FILES, ctx={'session_id': str(session.id)}, timeout=60)
    except DeviceUnavailable as exc:
        reason = str(exc)
        detail = {
            'offline': f'{device.name} is offline.',
            'paused': f'Agents are paused on {device.name}.',
            f'no_{FILES}': f"{device.name} doesn't allow files.",
        }.get(reason, reason)
        raise HTTPException(status_code=409, detail=detail) from None
    try:
        out = json.loads(text)
    except ValueError:
        raise HTTPException(status_code=502, detail=text.removeprefix('Error: ') or 'The PC did not answer.') from None
    return {**out, 'device': {'id': device.id, 'name': device.name}}


@devices_api.get('/chats/{session_id}/changes')
async def chat_pc_changes(session_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Files agents in this chat changed on its PC, which can be put back."""
    from cognitrix.computer.paired.undo import CHANGES
    return await _chat_pc_call(session_id, ctx, CHANGES, {})


@devices_api.post('/chats/{session_id}/undo')
async def chat_pc_undo(session_id: str, body: UndoBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Put files on the PC back as they were before this chat's agents changed them."""
    from cognitrix.computer.paired.undo import UNDO
    return await _chat_pc_call(session_id, ctx, UNDO, {'paths': body.paths})


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
    client: str = Field(default='', max_length=20)
    """'desktop' (the desktop app) or 'cli' (cognitrix connect): how the PC shows in Settings, Sign-ins."""


# A pairing code (Settings, Computers) or a sign-in code (Settings, Sign-ins)
# both pair a PC. A sign-in code also signs it in, so it shows in Sign-ins.

@devices_api.post('/claim')
async def claim_code(body: ClaimBody, request: Request) -> dict[str, Any]:
    from cognitrix import signins
    from cognitrix.computer.paired.store import PairingError, claim
    try:
        return claim(body.code, _address(request))
    except PairingError as exc:
        if exc.code != 'bad_code':
            raise _pairing_error(exc) from None
        try:
            return await signins.peek_code(body.code, _address(request))
        except signins.SignInError as sign_in_exc:
            if sign_in_exc.code == 'too_many':
                raise HTTPException(status_code=429, detail={'code': 'too_many', 'message': sign_in_exc.message}) from None
            raise _pairing_error(exc) from None


@devices_api.post('/pair')
async def pair_device(body: PairBody, request: Request) -> dict[str, Any]:
    from cognitrix.computer.paired.store import PairingError, pair, pair_with_sign_in_code
    args = dict(name=body.name, platform=body.platform, folders=body.folders, powers=body.powers,
                version=body.version, client=body.client, tailnet_node=await _tailnet_node(request))
    try:
        try:
            device, token = await pair(body.code, _address(request), **args)
        except PairingError as exc:
            if exc.code != 'bad_code':
                raise
            try:
                device, token = await pair_with_sign_in_code(body.code, _address(request), **args)
            except PairingError as sign_in_exc:
                raise (sign_in_exc if sign_in_exc.code in ('too_many', 'too_many_devices') else exc) from None
    except PairingError as exc:
        raise _pairing_error(exc) from None
    from cognitrix import audit
    audit.record('settings', f'Paired the PC {device.name}', user_id=str(device.user_id), outcome='ok')
    return {'device_id': str(device.id), 'token': token, 'name': device.name, 'addresses': _own_addresses()}


def _own_addresses() -> list[str]:
    """Where this server answers, best first: the PC keeps them all, so the link
    survives one going away. The Tailscale private address, then the public one."""
    from cognitrix.common import addresses

    out = []
    for url in (addresses.private_url(), addresses.configured()):
        if url and url not in out:
            out.append(url)
    return out


async def _tailnet_node(request: Request) -> str:
    """The calling machine's name on the tailnet, when it came through the Tailscale private address."""
    from cognitrix.tailscale import cli, doors

    if doors.door_of(request) != doors.TAILNET:
        return ''
    try:
        who = await asyncio.to_thread(cli.whois, _address(request))
        node = (who or {}).get('Node') or {}
        return str(node.get('ComputedName') or str(node.get('Name') or '').split('.')[0])[:80]
    except Exception:  # noqa: BLE001 - a name to show, nothing more
        return ''


# ---- Pairing without a code (a PC on the tailnet asks; its owner approves on the web) ----

@devices_api.get('/hello')
async def hello(request: Request) -> dict[str, Any]:
    """Asked by a PC looking for its server on the tailnet: is this Cognitrix?
    Open to all, and says nothing but the app's name and version, and whether
    the PC asking could pair without a code."""
    from cognitrix.config import VERSION
    from cognitrix.tailscale import identity

    seen = await identity.view(request)
    return {'app': 'cognitrix', 'version': VERSION, 'ask_to_pair': bool(seen['can_sign_in'])}


class AskBody(BaseModel):
    name: str = Field(default='', max_length=80)
    platform: str = Field(default='', max_length=80)
    client: str = Field(default='', max_length=20)


class AskSecret(BaseModel):
    secret: str = Field(max_length=100)


class AskPairBody(AskSecret):
    name: str = Field(default='', max_length=80)
    platform: str = Field(default='', max_length=80)
    folders: list[Any] = Field(default_factory=list, max_length=50)
    powers: list[str] = Field(default_factory=list, max_length=5)
    version: str = Field(default='', max_length=40)
    client: str = Field(default='', max_length=20)


async def _asking_login(request: Request) -> str:
    """The Tailscale login of the PC asking. Only the Tailscale private address knows it."""
    from cognitrix.tailscale import identity

    seen = identity.visitor(request, own_page=True)
    if seen is None or not identity.signin_on():
        raise HTTPException(status_code=404, detail={
            'code': 'no_tailnet', 'message': 'Pairing without a code only works at the server\'s Tailscale '
                                             'address, with "Sign in from the tailnet" turned on. Use a code.'})
    return seen.login


@devices_api.post('/pair-requests')
async def ask_to_pair(body: AskBody, request: Request) -> dict[str, Any]:
    from cognitrix.computer.paired.store import PairingError, make_request
    from cognitrix.models import User
    from cognitrix.tailscale import identity

    login = await _asking_login(request)
    row = await identity.link_of_login(login)
    user = await User.get(row.user_id) if row is not None else None
    if user is None or getattr(user, 'disabled', None):
        raise HTTPException(status_code=404, detail={
            'code': 'no_link', 'message': f'No account here is linked to the Tailscale login {login}. Link it on '
                                          'the web (Settings, Sign-ins), or pair with a code.'})
    account = ' · '.join(p for p in (str(user.name or '').strip(), str(user.email or '').strip()) if p)[:160]
    try:
        item, secret = make_request(str(user.id), account or 'your account', login=login,
                                    node=await _tailnet_node(request), address=_address(request),
                                    name=body.name, platform=body.platform, client=body.client)
    except PairingError as exc:
        raise _pairing_error(exc) from None
    try:
        from cognitrix.tasks.automation import create_notification

        await create_notification(
            str(user.id), f'{item.name} wants to pair',
            f'A PC on your tailnet ({item.node or item.address}, signed in to Tailscale as {login}) asked to be '
            'paired with your account. Approve it only if you just asked for this.',
            link='/settings/computers', urgent=True)
    except Exception:  # noqa: BLE001 - the request still shows under Settings, Computers
        logger.debug('could not notify about a pairing request', exc_info=True)
    return {'request_id': item.id, 'secret': secret, 'account': item.account,
            'expires_in': int(item.expires - time.time())}


@devices_api.post('/pair-requests/{request_id}/status')
async def ask_status(request_id: str, body: AskSecret, request: Request) -> dict[str, Any]:
    from cognitrix.computer.paired.store import PairingError, request_status

    login = await _asking_login(request)
    try:
        return request_status(request_id, body.secret, login, _address(request))
    except PairingError as exc:
        raise _pairing_error(exc) from None


@devices_api.post('/pair-requests/{request_id}/pair')
async def pair_approved_device(request_id: str, body: AskPairBody, request: Request) -> dict[str, Any]:
    from cognitrix.computer.paired.store import PairingError, pair_approved

    login = await _asking_login(request)
    try:
        device, token = await pair_approved(
            request_id, body.secret, login, _address(request), name=body.name, platform=body.platform,
            folders=body.folders, powers=body.powers, version=body.version, client=body.client)
    except PairingError as exc:
        raise _pairing_error(exc) from None
    from cognitrix import audit

    audit.record('settings', f'Paired the PC {device.name} (asked from the tailnet as {login})',
                 user_id=str(device.user_id), outcome='ok')
    return {'device_id': str(device.id), 'token': token, 'name': device.name, 'addresses': _own_addresses()}


class ProveBody(BaseModel):
    device_id: str = Field(max_length=80)
    nonce: str = Field(min_length=16, max_length=128)
    address: str = Field(max_length=300)


def _same_address(a: str, b: str) -> bool:
    from urllib.parse import urlsplit

    def key(value: str) -> tuple[str, str]:
        parts = urlsplit(str(value or '').strip())
        return parts.scheme.lower(), parts.netloc.lower()

    return bool(key(a)[1]) and key(a) == key(b)


@devices_api.post('/prove')
async def prove_same_server(body: ProveBody) -> dict[str, str]:
    """A paired PC asks this before it sends its token to an address the server
    named (not the one its owner paired at): show you are the server I paired
    with. Only for addresses this server answers at today, so an address that
    has gone to someone else can't pass the question on to us."""
    from cognitrix.computer.paired.store import PairingError, prove

    if not any(_same_address(body.address, own) for own in _own_addresses()):
        raise HTTPException(status_code=404, detail={'code': 'not_mine', 'message': 'This server does not answer at that address.'})
    try:
        return {'proof': await prove(body.device_id, body.nonce, body.address)}
    except PairingError as exc:
        raise _pairing_error(exc) from None


@devices_api.websocket('/connect')
async def connect(websocket: WebSocket) -> None:
    """A paired PC's connection. Its first message proves which PC it is."""
    from cognitrix.computer.paired.hub import Connection, hub
    from cognitrix.computer.paired.store import device_for_token, now_stamp
    from cognitrix.models.paired_device import PairedDevice

    await websocket.accept()
    hub.serving = True
    try:
        server = websocket.scope.get('server') or ()
        if len(server) > 1 and server[1]:
            from cognitrix.computer.paired.relay import remember_address
            remember_address(int(server[1]))
    except Exception:
        logger.debug('Could not note the relay address', exc_info=True)
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


def _at_this_pc(request: Request) -> None:
    """What this PC shares, and with which account, is decided at this PC: in the
    desktop app's own window. A session that reaches the engine from another
    machine (its Tailscale address, the local network) can look, not change."""
    import ipaddress

    from cognitrix import signins
    from cognitrix.tailscale import doors

    address = signins.client_address(request)
    try:
        local = ipaddress.ip_address(address).is_loopback
    except ValueError:
        local = address == 'localhost'
    if not local or doors.door_of(request) != doors.DIRECT:
        raise HTTPException(status_code=403, detail='Only the Cognitrix app on this PC itself can link it or change '
                                                    'what it shares. Open the app on that PC.')


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
async def preview_link(body: PreviewBody, request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    _at_this_pc(request)
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
async def make_link(body: LinkBody, request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    _at_this_pc(request)
    from cognitrix.computer.paired.connector import LinkError, get_connector, link
    try:
        await link(body.server, body.code, name=body.name, folders=body.folders, powers=body.powers,
                   client='desktop')
    except LinkError as exc:
        raise _link_error(exc) from None
    connector = get_connector()
    await connector.stop()
    connector.start()
    await asyncio.sleep(0.5)
    return _link_view()


# ---- This PC and a server on the same tailnet: find it, and pair without a code ----

@link_api.get('/find')
async def find_servers(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Cognitrix servers on this PC's tailnet (asks each machine a small question)."""
    _desktop_only()
    from cognitrix.computer.paired import tailnet
    try:
        return {'servers': await tailnet.find_servers()}
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


# The request this PC is waiting on. Its secret stays in the engine, and it
# belongs to the session that asked: no other session sees or finishes it.
_asked: dict[str, Any] = {}


def _asker(ctx: AuthContext) -> tuple[str, str]:
    return str(getattr(ctx.user, 'id', '') or ''), str(ctx.sid or '')


def _my_asked(ctx: AuthContext) -> bool:
    return bool(_asked) and _asked.get('by') == _asker(ctx)


class AskServerBody(BaseModel):
    server: str = Field(max_length=300)
    name: str = Field(default='', max_length=80)


def _asked_view(status: str = '', mine: bool = True) -> dict[str, Any]:
    if not _asked or not mine:
        return {'status': 'none', 'server': '', 'account': ''}
    return {'status': status or _asked.get('status') or 'pending', 'server': _asked['server'],
            'account': _asked.get('account') or ''}


@link_api.post('/ask')
async def ask_server(body: AskServerBody, request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Ask a server on the tailnet to pair this PC; its owner approves on the web."""
    _desktop_only()
    _at_this_pc(request)
    from cognitrix.computer.paired.connector import LinkError, ask
    try:
        asked = await ask(body.server, name=body.name, client='desktop')
    except LinkError as exc:
        raise _link_error(exc) from None
    _asked.clear()
    _asked.update(asked, status='pending', by=_asker(ctx))
    return _asked_view()


@link_api.get('/ask')
async def asked_status(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    from cognitrix.computer.paired.connector import LinkError, ask_status
    if not _my_asked(ctx):
        return _asked_view(mine=False)
    try:
        now = await ask_status(_asked)
    except LinkError as exc:
        if exc.code == 'gone':
            _asked.clear()
            return {'status': 'expired', 'server': '', 'account': ''}
        raise _link_error(exc) from None
    _asked.update(status=now['status'], account=now['account'])
    view = _asked_view()
    if now['status'] in ('denied', 'expired', 'used'):
        _asked.clear()
    return view


@link_api.delete('/ask')
async def forget_asked(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    _at_this_pc(request)
    if _my_asked(ctx):
        _asked.clear()
    return _asked_view(mine=False)


class AskFinishBody(BaseModel):
    name: str = Field(default='', max_length=80)
    folders: list[Any] = Field(default_factory=list, max_length=50)
    powers: list[str] = Field(default_factory=list, max_length=5)


@link_api.post('/ask/finish')
async def finish_asked(body: AskFinishBody, request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """The request was approved: share the chosen folders and save the link."""
    _desktop_only()
    _at_this_pc(request)
    from cognitrix.computer.paired.connector import LinkError, get_connector, link_approved
    if not _my_asked(ctx):
        raise HTTPException(status_code=400, detail='There is no pairing request to finish. Ask again.')
    try:
        await link_approved(_asked, name=body.name, folders=body.folders, powers=body.powers, client='desktop')
    except LinkError as exc:
        raise _link_error(exc) from None
    _asked.clear()
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
async def change_link(body: LinkChange, request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    _at_this_pc(request)
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
async def remove_link(request: Request, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    _at_this_pc(request)
    from cognitrix.computer.paired.connector import get_connector
    await get_connector().unpair()
    return _link_view()


@link_api.get('/remote/agents')
async def link_remote_agents(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """The linked web account's agents, for sending it a task."""
    _desktop_only()
    from cognitrix.computer.paired.connector import LinkError, remote
    try:
        return await remote('GET', '/link/agents')
    except LinkError as exc:
        raise _link_error(exc) from None


class SendTaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=20000)
    steps: list[str] = Field(default_factory=list, max_length=50)
    agent_id: str | None = Field(default=None, max_length=80)


@link_api.post('/remote/tasks')
async def link_send_task(body: SendTaskBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Hand a task to the linked web account; it runs there, on its agents."""
    _desktop_only()
    from cognitrix.computer.paired.connector import LinkError, load_config, remote
    try:
        out = await remote('POST', '/link/tasks', body.model_dump())
    except LinkError as exc:
        raise _link_error(exc) from None
    out['url'] = f"{load_config().get('server', '')}/tasks/{out.get('task_id', '')}"
    return out


@link_api.get('/remote/tasks')
async def link_sent_tasks(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    _desktop_only()
    from cognitrix.computer.paired.connector import LinkError, load_config, remote
    try:
        out = await remote('GET', '/link/tasks')
    except LinkError as exc:
        raise _link_error(exc) from None
    server = load_config().get('server', '')
    for task in out.get('tasks') or []:
        task['url'] = f"{server}/tasks/{task.get('id', '')}"
    return out


async def start_saved_link() -> None:
    """At startup in the desktop app: reconnect a PC linked earlier."""
    if os.environ.get('COGNITRIX_DESKTOP') != '1':
        return
    from cognitrix.computer.paired.connector import get_connector, load_config
    if load_config().get('token'):
        get_connector().start()
