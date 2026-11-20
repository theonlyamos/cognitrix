"""The agents' sandboxed computer, for the live screen panel.

    GET  /computer/sandbox                         is it on, is it running, which desktops
    POST /computer/sandbox/desktops/{name}/viewer  a one-time ticket + password for the live screen
    WS   /computer/sandbox/screen?ticket=...       the live screen (VNC over websocket), proxied
    POST /computer/sandbox/desktops/{name}/hold    take over: the agent pauses; returns the password that
                                                   lets the screen take mouse and keyboard
    POST /computer/sandbox/desktops/{name}/release hand back
    POST /computer/sandbox/desktops/{name}/teach/start  Teach a task: take over and start recording
    GET  /computer/sandbox/desktops/{name}/teach        the steps recorded so far
    POST /computer/sandbox/desktops/{name}/teach/stop   stop recording (and hand back); returns the steps
    POST /computer/sandbox/teach/draft             a model writes a skill from the steps, for review
    POST /computer/sandbox/update                  restart onto a newer image (files and logins stay)
    POST /computer/sandbox/reset                   throw the computer away (files and logins too)
    POST /computer/sandbox/wake                    start a computer that is asleep (hosted on Fly)
    GET  /computer/sandbox/env                     the person's sandbox environment variables (names only)
    PUT  /computer/sandbox/env                     replace them (a value left out keeps the saved one;
                                                   hosts: the only sites a hidden value is sent to)
    GET  /computer/sandbox/snapshots               saved snapshots (whole computer or one agent's setup)
    POST /computer/sandbox/snapshots               save one {name, agent?}
    POST /computer/sandbox/snapshots/{id}/restore  put it back {agent?: the agent an agent setup goes to}
    DELETE /computer/sandbox/snapshots/{id}
    POST /computer/sandbox/copy-agent              copy one agent's setup to another {from, to}
    GET  /computer/sandbox/chats/{session_id}      is Run in sandbox on for a chat
    PUT  /computer/sandbox/chats/{session_id}      turn it on or off (null: follow the agent)

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

    mode = sandbox_mode()
    if not mode:
        return {'enabled': False, 'running': False, 'desktops': [], 'from_env': mode_from_env()}
    try:
        box = await run_in_threadpool(get_manager().existing, _owner(ctx))
    except Exception:  # Docker missing or stopped, or Fly unreachable
        logger.warning("Couldn't reach the agents' computer", exc_info=True)
        return {'enabled': True, 'running': False, 'desktops': [], 'host': mode,
                'error': "Couldn't reach Fly. Check the server's Fly settings." if mode == 'fly'
                else "Couldn't reach Docker. Check that Docker is installed and running."}
    if box is None:
        try:
            asleep = bool(await run_in_threadpool(get_manager().asleep, _owner(ctx)))
        except Exception:
            asleep = False
        return {'enabled': True, 'running': False, 'desktops': [], 'host': mode, 'asleep': asleep}
    desktops = await run_in_threadpool(box.desktops)
    mgr = get_manager()
    try:
        update = bool(await run_in_threadpool(mgr.update_available, box))
    except Exception:
        update = False
    return {'enabled': True, 'running': True, 'desktops': desktops, 'update_available': update, 'host': mode}


class WakeBody(BaseModel):
    desktop: str | None = None


@sandbox_api.post('/wake')
async def wake(body: WakeBody | None = None, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Start the person's computer now (a hosted one sleeps when idle), with
    a desktop to show: the one asked for, else 'main' when it has none."""
    box = await _box(ctx, start=True)
    desktops = await run_in_threadpool(box.desktops)
    wanted = _desktop(body.desktop) if body and body.desktop else None
    if (wanted and not any(d.get('name') == wanted for d in desktops)) or not desktops:
        await run_in_threadpool(box.desktop, wanted or 'main')
        desktops = await run_in_threadpool(box.desktops)
    return {'ok': True, 'desktops': desktops}


@sandbox_api.get('/settings')
async def get_settings(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.client import IMAGE, docker_status, mode_from_env, sandbox_mode

    if sandbox_mode() == 'fly':
        from cognitrix.computer.sandbox.fly import fly_settings

        return {'enabled': True, 'from_env': True, 'image': IMAGE, 'host': 'fly', 'fly': fly_settings(),
                'docker': {'installed': True, 'running': True, 'image': True}}
    return {'enabled': bool(sandbox_mode()), 'from_env': mode_from_env(), 'image': IMAGE, 'host': 'docker',
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


class EnvVar(BaseModel):
    name: str
    value: str | None = None  # None keeps the saved value
    # Only sent to these sites (hidden from the agent); None keeps the saved
    # sites, [] makes it a plain variable.
    hosts: list[str] | None = None


class EnvBody(BaseModel):
    variables: list[EnvVar] = Field(default_factory=list)


def _env_view(values: dict[str, str]) -> dict[str, Any]:
    from cognitrix.common.key_proxy import HiddenValue, hiding_enabled
    from cognitrix.common.sandbox_work import env_preview

    return {'variables': [{'name': k, 'preview': env_preview(v),
                           'hosts': list(v.hosts) if isinstance(v, HiddenValue) else []}
                          for k, v in sorted(values.items())],
            'hiding': hiding_enabled()}


@sandbox_api.get('/env')
async def get_env(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Variables every command, test run and coding agent in this person's
    sandbox gets. Values never come back, only a masked preview."""
    from cognitrix.common.sandbox_work import sandbox_env

    return _env_view(await run_in_threadpool(sandbox_env, _owner(ctx)))


@sandbox_api.put('/env')
async def put_env(body: EnvBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.common.sandbox_work import save_sandbox_env

    try:
        saved = await run_in_threadpool(save_sandbox_env, _owner(ctx),
                                        [v.model_dump(exclude_none=True) for v in body.variables])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _env_view(saved)


class ChatSandboxBody(BaseModel):
    run_in_sandbox: bool | None = None


async def _chat_sandbox_view(session, ctx: AuthContext) -> dict[str, Any]:
    from cognitrix.common.sandbox_work import sandbox_available, wants_sandbox

    agent = None
    if session.agent_id:
        try:
            from cognitrix.agents import Agent
            agent = await Agent.find_one({'id': session.agent_id})
        except Exception:
            agent = None
    return {'run_in_sandbox': session.run_in_sandbox, 'agent_default': wants_sandbox(None, agent),
            'effective': wants_sandbox(session.run_in_sandbox, agent), 'available': sandbox_available()}


@sandbox_api.get('/chats/{session_id}')
async def get_chat_sandbox(session_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.api.routes.sessions import _load_authorized_session

    _, session = await _load_authorized_session(session_id, ctx)
    return await _chat_sandbox_view(session, ctx)


@sandbox_api.put('/chats/{session_id}')
async def put_chat_sandbox(session_id: str, body: ChatSandboxBody,
                           ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Run this chat's shell, files and tests in the sandbox (from its next message)."""
    from cognitrix.api.routes.sessions import _load_authorized_session

    _, session = await _load_authorized_session(session_id, ctx)
    if session.run_id:
        raise HTTPException(status_code=400, detail="A task run's setting can't be changed")
    session.run_in_sandbox = body.run_in_sandbox
    await session.save()
    return await _chat_sandbox_view(session, ctx)


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


def _call_out(out: dict) -> dict:
    if not out.get('ok'):
        status = 409 if out.get('error') in ('human_control', 'unsupported') else 502
        if out.get('error') == 'bad_method':
            out['message'] = "This computer can't record yet. Update it (the Update button), then try again."
            status = 409
        raise HTTPException(status_code=status, detail=out.get('message') or "The computer couldn't do that.")
    return out.get('result') or {}


@sandbox_api.post('/desktops/{name}/teach/start')
async def teach_start(name: str, body: HoldBody | None = None, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Teach a task: take over the desktop (the agent pauses) and record
    what the person does until they stop."""
    name = _desktop(name)
    box = await _box(ctx)
    if name not in box.passwords:
        await run_in_threadpool(box.desktop, name)
    by = (body.by if body and body.by else None) or _display_name(ctx)
    held = await run_in_threadpool(box.request, 'POST', f'/desktops/{name}/hold', {'by': by})
    if not held.get('ok'):
        raise HTTPException(status_code=409, detail=held.get('message') or "Couldn't take over.")
    out = await run_in_threadpool(box.call, 'POST', f'/desktops/{name}/call', {'method': 'record_start', 'args': {}})
    try:
        _call_out(out)
    except HTTPException:
        await run_in_threadpool(box.call, 'POST', f'/desktops/{name}/release')
        raise
    return {'ok': True, 'recording': True, 'held_by': by, 'password': held.get('vnc_password', '')}


@sandbox_api.get('/desktops/{name}/teach')
async def teach_status(name: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.recorder import steps_as_text

    box = await _box(ctx)
    out = await run_in_threadpool(box.call, 'POST', f'/desktops/{_desktop(name)}/call',
                                  {'method': 'record_status', 'args': {}})
    result = _call_out(out)
    steps = result.get('steps') or []
    return {'recording': bool(result.get('recording')), 'steps': steps, 'text': steps_as_text(steps),
            'error': result.get('error') or ''}


class TeachStopBody(BaseModel):
    hand_back: bool = True


@sandbox_api.post('/desktops/{name}/teach/stop')
async def teach_stop(name: str, body: TeachStopBody | None = None,
                     ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.recorder import steps_as_text

    name = _desktop(name)
    box = await _box(ctx)
    out = await run_in_threadpool(box.call, 'POST', f'/desktops/{name}/call', {'method': 'record_stop', 'args': {}})
    result = _call_out(out)
    if body is None or body.hand_back:
        await run_in_threadpool(box.call, 'POST', f'/desktops/{name}/release')
    steps = result.get('steps') or []
    return {'steps': steps, 'text': steps_as_text(steps), 'truncated': bool(result.get('truncated')),
            'seconds': result.get('seconds')}


class TeachDraftBody(BaseModel):
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=400)
    title: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=2000)
    agent_id: str | None = None
    desktop: str | None = None


async def _teaching_agent(ctx: AuthContext, agent_id: str | None, desktop: str | None):
    """The agent whose model writes the skill: the one named, else the one
    whose desktop was used, else the person's first agent."""
    from cognitrix.agents import access as agent_access
    from cognitrix.computer.sandbox import desktop_name

    me = agent_access.owner_key(ctx.user)
    if agent_id:
        agent = await agent_access.find_visible_agent(me, agent_id=agent_id)
        if agent is not None:
            return agent
    try:
        agents = list(await agent_access.visible_agents(me) or [])
    except Exception:
        agents = []
    if desktop:
        for agent in agents:
            if desktop_name(getattr(agent, 'id', None)) == desktop:
                return agent
    return agents[0] if agents else None


@sandbox_api.post('/teach/draft')
async def teach_draft(body: TeachDraftBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Write a skill from a recorded demonstration. Nothing is saved: the
    person edits it, then saves it with POST /skills/mine."""
    from cognitrix.agents import access as agent_access
    from cognitrix.skills import personal
    from cognitrix.skills.drafts import DraftError, slug
    from cognitrix.skills.teach import draft_from_steps

    agent = await _teaching_agent(ctx, body.agent_id, body.desktop)
    try:
        name, description, text = await draft_from_steps(getattr(agent, 'llm', None), body.steps, body.title,
                                                         body.note)
    except DraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    me = agent_access.owner_key(ctx.user)
    if await personal.name_taken(me, name):
        name = slug(f'{name}-{secrets.token_hex(2)}')
    return {'name': name, 'description': description, 'body': text,
            'agent': str(getattr(agent, 'name', '') or '')}


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


class SnapshotBody(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    agent: str | None = None  # a desktop name: save only that agent's setup


class RestoreBody(BaseModel):
    agent: str | None = None  # where an agent setup goes (default: the agent it came from)


class CopyBody(BaseModel):
    source: str = Field(alias='from')
    target: str = Field(alias='to')


def _snapshot_errors(exc: Exception) -> HTTPException:
    from cognitrix.computer import DriverError, Unsupported
    from cognitrix.computer.sandbox.snapshots import SnapshotError

    if isinstance(exc, SnapshotError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DriverError | Unsupported):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail='Bad agent name')
    raise exc


@sandbox_api.get('/snapshots')
async def get_snapshots(ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.snapshots import MAX_SNAPSHOTS, list_snapshots

    _manager()
    return {'snapshots': await run_in_threadpool(list_snapshots, _owner(ctx)), 'max': MAX_SNAPSHOTS}


@sandbox_api.post('/snapshots')
async def post_snapshot(body: SnapshotBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Save the whole computer, or one agent's setup (``agent``). Takes a
    while for a big computer; the computer keeps running meanwhile."""
    from cognitrix.computer.sandbox.snapshots import create_snapshot

    mgr = _manager()
    agent = _desktop(body.agent) if body.agent else None
    try:
        return await run_in_threadpool(create_snapshot, mgr, _owner(ctx), body.name, agent)
    except Exception as exc:
        raise _snapshot_errors(exc) from None


@sandbox_api.post('/snapshots/{snapshot_id}/restore')
async def restore_snapshot(snapshot_id: str, body: RestoreBody | None = None,
                           ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.snapshots import restore_snapshot as restore

    mgr = _manager()
    agent = _desktop(body.agent) if body and body.agent else None
    try:
        return await run_in_threadpool(restore, mgr, _owner(ctx), snapshot_id, agent)
    except Exception as exc:
        raise _snapshot_errors(exc) from None


@sandbox_api.delete('/snapshots/{snapshot_id}')
async def delete_snapshot(snapshot_id: str, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    from cognitrix.computer.sandbox.snapshots import delete_snapshot as delete

    _manager()
    try:
        await run_in_threadpool(delete, _owner(ctx), snapshot_id)
    except Exception as exc:
        raise _snapshot_errors(exc) from None
    return {'ok': True}


@sandbox_api.post('/copy-agent')
async def copy_agent(body: CopyBody, ctx: AuthContext = Depends(jwt_only)) -> dict[str, Any]:
    """Give one agent a copy of another's folder, browser sign-ins and
    projects on the computer (its own are replaced)."""
    from cognitrix.computer.sandbox.snapshots import copy_agent as copy

    mgr = _manager()
    try:
        return await run_in_threadpool(copy, mgr, _owner(ctx), _desktop(body.source), _desktop(body.target))
    except Exception as exc:
        raise _snapshot_errors(exc) from None


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
    url = box.viewer_url(name)
    mgr = get_manager()
    mgr.viewer_opened(owner)
    async with aiohttp.ClientSession() as session:
        try:
            upstream = await session.ws_connect(url, protocols=('binary',), max_msg_size=0)
        except aiohttp.ClientError:
            mgr.viewer_closed(owner)
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
            mgr.viewer_closed(owner)
            await upstream.close()
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass  # the viewer already left
