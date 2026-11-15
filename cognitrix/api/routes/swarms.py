"""Swarms: an agent's group of parallel workers, as the chat page shows them.

GET  /api/swarms?session_id=…        the swarms of one chat, newest first
GET  /api/swarms/settings            my limits
PUT  /api/swarms/settings            change my limits
GET  /api/swarms/{id}                one swarm with its workers
GET  /api/swarms/{id}/workers/{name}/result   a worker's full result
POST /api/swarms/{id}/stop           stop the swarm
POST /api/swarms/{id}/workers/{name}/stop     stop one worker
POST /api/swarms/{id}/workers/{name}/merge    merge a worker's separate copy
POST /api/swarms/stop-all            stop every running swarm of mine

See ``cognitrix/swarms/service.py``. Agents use the Spawn Workers and Swarm tools.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.swarms import service
from cognitrix.swarms.models import SwarmRun

swarms_api = APIRouter(prefix='/swarms')


class Limits(BaseModel):
    enabled: bool | None = None
    concurrency: int | None = None
    max_workers: int | None = None
    token_budget: int | None = None
    auto_start: int | None = None


def _owner(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


def _fail(exc: service.SwarmError) -> HTTPException:
    missing = exc.code in ('swarm_not_found', 'worker_not_found')
    return HTTPException(status_code=404 if missing else 400, detail=exc.message)


async def _swarm(swarm_id: str, ctx: AuthContext) -> SwarmRun:
    swarm = await service.get_swarm(_owner(ctx), swarm_id)
    if swarm is None:
        raise HTTPException(status_code=404, detail='Swarm not found')
    return swarm


@swarms_api.get('')
async def list_swarms(session_id: str = '', ctx: AuthContext = Depends(jwt_only)):
    me = _owner(ctx)
    if session_id:
        rows = await service.swarms_for_chat(me, session_id)
    else:
        rows = await SwarmRun.find({'user_id': me, 'status': 'running'}) or []
    rows = sorted(rows, key=lambda row: str(row.started_at or ''), reverse=True)[:20]
    return [await service.public_swarm(row) for row in rows]


@swarms_api.get('/settings')
async def get_limits(ctx: AuthContext = Depends(jwt_only)):
    return service.public_settings(await service.get_settings(_owner(ctx)))


@swarms_api.put('/settings')
async def put_limits(body: Limits, ctx: AuthContext = Depends(jwt_only)):
    try:
        row = await service.save_settings(_owner(ctx), body.model_dump(exclude_none=True))
    except service.SwarmError as exc:
        raise _fail(exc) from None
    return service.public_settings(row)


@swarms_api.post('/stop-all')
async def stop_everything(ctx: AuthContext = Depends(jwt_only)):
    return {'stopped': await service.stop_all(_owner(ctx))}


@swarms_api.get('/{swarm_id}')
async def get_swarm(swarm_id: str, ctx: AuthContext = Depends(jwt_only)):
    return await service.public_swarm(await _swarm(swarm_id, ctx))


@swarms_api.get('/{swarm_id}/workers/{name}/result')
async def worker_result(swarm_id: str, name: str, offset: int = 0, ctx: AuthContext = Depends(jwt_only)):
    try:
        return {'text': await service.read_result(_owner(ctx), swarm_id, name, offset=max(0, offset))}
    except service.SwarmError as exc:
        raise _fail(exc) from None


@swarms_api.post('/{swarm_id}/stop')
async def stop_swarm(swarm_id: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        swarm = await service.stop_swarm(_owner(ctx), swarm_id, reason='Stopped by you.')
    except service.SwarmError as exc:
        raise _fail(exc) from None
    return await service.public_swarm(swarm)


@swarms_api.post('/{swarm_id}/workers/{name}/stop')
async def stop_worker(swarm_id: str, name: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        await service.stop_worker(_owner(ctx), swarm_id, name)
    except service.SwarmError as exc:
        raise _fail(exc) from None
    return await service.public_swarm(await _swarm(swarm_id, ctx))


@swarms_api.post('/{swarm_id}/workers/{name}/merge')
async def merge_worker(swarm_id: str, name: str, ctx: AuthContext = Depends(jwt_only)):
    try:
        merged, message = await service.merge_worker(_owner(ctx), swarm_id, name)
    except service.SwarmError as exc:
        raise _fail(exc) from None
    return {'merged': merged, 'message': message, 'swarm': await service.public_swarm(await _swarm(swarm_id, ctx))}
