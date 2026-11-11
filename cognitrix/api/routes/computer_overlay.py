"""The desktop app's agent cursor: a feed of agent actions and its Stop button.

Only the desktop app that started this engine can use these: it sends the
token it gave the engine (X-Cognitrix-Desktop-Token). Without one they 404.
See cognitrix/computer/overlay.py.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from cognitrix.computer import overlay

overlay_api = APIRouter(prefix='/computer/overlay')


def _desktop_app(token: str | None = Header(default=None, alias='X-Cognitrix-Desktop-Token')) -> None:
    if not overlay.token_ok(token):
        raise HTTPException(status_code=404, detail='Not found')


@overlay_api.get('/events', dependencies=[Depends(_desktop_app)])
async def overlay_events():
    queue = overlay.hub.subscribe()

    async def feed():
        try:
            yield {'event': 'ready', 'data': '{}'}
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield {'event': 'ping', 'data': '{}'}
                    continue
                yield {'event': 'action', 'data': json.dumps(item)}
        finally:
            overlay.hub.unsubscribe(queue)

    return EventSourceResponse(feed())


class StopBody(BaseModel):
    agent_id: str | None = Field(default=None, max_length=200)


@overlay_api.post('/stop', dependencies=[Depends(_desktop_app)])
async def overlay_stop(body: StopBody):
    stopped = await asyncio.to_thread(overlay.stop, body.agent_id or None)
    return {'stopped': stopped}
