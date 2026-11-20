"""The server side of paired PCs: one live connection per online PC.

The PC dials in (``WS /computer/devices/connect``) and keeps the socket open.
A tool call for that PC is sent down the socket as ``{"type": "call", ...}``
and the PC answers with ``{"type": "result", ...}`` carrying the same id.

Connections live in the web process (chats run there). A call made from
another thread's event loop is handed to the loop that owns the socket.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from cognitrix.computer.paired import (
    APPS,
    COMMANDS,
    DEFAULT_CALL_TIMEOUT_S,
    FILES,
    MAX_RESULT_CHARS,
)

logger = logging.getLogger('cognitrix.paired')


class DeviceUnavailable(RuntimeError):
    """The PC is offline, paused, or doesn't allow this (message is model-facing)."""


@dataclass
class Connection:
    device_id: str
    user_id: str
    name: str
    socket: Any  # starlette WebSocket
    loop: asyncio.AbstractEventLoop
    platform: str = ''
    folders: list[dict[str, Any]] = field(default_factory=list)
    powers: list[str] = field(default_factory=list)
    paused: bool = False
    connected_at: float = field(default_factory=time.time)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send(self, message: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.socket.send_text(json.dumps(message))

    def fail_pending(self, why: str) -> None:
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.set_exception(DeviceUnavailable(why))
        self.pending.clear()


class Hub:
    def __init__(self) -> None:
        self._conns: dict[str, Connection] = {}
        self._ids = itertools.count(1)
        # True in the web process, where PCs connect. Elsewhere (the task
        # worker) calls go to the web process through relay.py.
        self.serving = False

    # -- connections -----------------------------------------------------------
    def register(self, conn: Connection) -> Connection | None:
        """Add a PC's connection; returns the one it replaces (the PC reconnected)."""
        old = self._conns.get(conn.device_id)
        self._conns[conn.device_id] = conn
        return old

    def unregister(self, conn: Connection) -> None:
        conn.closed = True
        if self._conns.get(conn.device_id) is conn:
            self._conns.pop(conn.device_id, None)
        conn.fail_pending(f'{conn.name} went offline while the call was running.')

    def get(self, device_id: Any) -> Connection | None:
        conn = self._conns.get(str(device_id or ''))
        return conn if conn is not None and not conn.closed else None

    def online(self, device_id: Any) -> bool:
        return self.get(device_id) is not None

    async def drop(self, device_id: Any, message: dict[str, Any] | None = None, code: int = 4000) -> None:
        """Close a PC's connection (unpaired from the web), telling it why first."""
        conn = self.get(device_id)
        if conn is None:
            return
        await _on_loop(conn.loop, self._drop(conn, message, code))

    async def _drop(self, conn: Connection, message: dict[str, Any] | None, code: int) -> None:
        try:
            if message:
                await conn.send(message)
            await conn.socket.close(code=code)
        except Exception:
            pass
        self.unregister(conn)

    def answer(self, conn: Connection, message: dict[str, Any]) -> None:
        fut = conn.pending.pop(str(message.get('id') or ''), None)
        if fut is not None and not fut.done():
            fut.set_result(message)

    # -- calls -----------------------------------------------------------------
    async def call(self, device_id: Any, tool: str, args: dict[str, Any], *, power: str,
                   ctx: dict[str, Any] | None = None, timeout: float = DEFAULT_CALL_TIMEOUT_S) -> str:
        """Run one tool on the PC and return its text. Raises DeviceUnavailable."""
        text, images = await self.call_full(device_id, tool, args, power=power, ctx=ctx, timeout=timeout)
        if images:
            # Screenshots the tool showed the model on the PC: show them here too.
            from cognitrix.tools.misc import queue_image
            for image in images:
                queue_image(image)
        return text

    async def call_full(self, device_id: Any, tool: str, args: dict[str, Any], *, power: str,
                        ctx: dict[str, Any] | None = None,
                        timeout: float = DEFAULT_CALL_TIMEOUT_S) -> tuple[str, list[str]]:
        """Like ``call``, returning (text, images) without showing the images."""
        conn = self.get(device_id)
        if conn is None and not self.serving:
            return await self._relay_call(device_id, tool, args, power=power, ctx=ctx or {}, timeout=timeout)
        if conn is None:
            raise DeviceUnavailable('offline')
        if conn.paused:
            raise DeviceUnavailable('paused')
        if power not in conn.powers:
            raise DeviceUnavailable(f'no_{power}')
        return await _on_loop(conn.loop, self._call(conn, tool, args, ctx or {}, timeout))

    async def _relay_call(self, device_id: Any, tool: str, args: dict[str, Any], *, power: str,
                          ctx: dict[str, Any], timeout: float) -> tuple[str, list[str]]:
        from cognitrix.computer.paired import relay
        reply = await relay.send('call', {'device_id': str(device_id), 'tool': tool, 'args': args, 'power': power,
                                          'ctx': ctx, 'timeout': timeout}, timeout=timeout + 15)
        if reply is None:
            raise DeviceUnavailable('offline')
        if reply.get('error'):
            raise DeviceUnavailable(str(reply['error']))
        return str(reply.get('text') or ''), list(reply.get('images') or [])

    async def _call(self, conn: Connection, tool: str, args: dict[str, Any], ctx: dict[str, Any],
                    timeout: float) -> tuple[str, list[str]]:
        call_id = f'c{next(self._ids)}'
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = fut
        try:
            await conn.send({'type': 'call', 'id': call_id, 'tool': tool, 'args': args, 'ctx': ctx})
            reply = await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            raise DeviceUnavailable(f'{conn.name} did not answer within {int(timeout)} seconds.') from None
        except DeviceUnavailable:
            raise
        except Exception as exc:
            raise DeviceUnavailable(f'{conn.name} went offline ({exc}).') from None
        finally:
            conn.pending.pop(call_id, None)
        if not reply.get('ok'):
            code = str(reply.get('error') or 'failed')
            if code in ('paused', f'no_{FILES}', f'no_{COMMANDS}', f'no_{APPS}'):
                raise DeviceUnavailable(code)
            return f"Error: {reply.get('message') or 'The call failed on the PC.'}", []
        text = str(reply.get('text') or '')
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + '\n\n[... cut: the answer from the PC was too long ...]'
        images = [i for i in (reply.get('images') or [])[:4]
                  if isinstance(i, str) and i.startswith('data:image/') and len(i) < 8_000_000]
        return text, images


async def status(device_id: Any) -> dict[str, Any] | None:
    """A PC's live state ({online, paused, powers, folders}), asking the web
    process when this one isn't it. None when that can't be told."""
    conn = hub.get(device_id)
    if conn is not None:
        return {'online': True, 'paused': conn.paused, 'powers': list(conn.powers), 'folders': list(conn.folders)}
    if hub.serving:
        return {'online': False}
    from cognitrix.computer.paired import relay
    return await relay.send('status', {'device_id': str(device_id)}, timeout=10)


async def _on_loop(loop: asyncio.AbstractEventLoop, coro):
    """Await ``coro`` on ``loop`` (the socket's), from whichever loop we're on."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        return await coro
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))


hub = Hub()
