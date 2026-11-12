"""Feed of agent actions for the desktop app's on-screen agent cursor.

The desktop app starts the engine with a random COGNITRIX_DESKTOP_TOKEN and
reads ``/computer/overlay/events`` with it; the feed is off (404) without one.
Each event says where an agent is about to act and what it is doing, so the
app can draw a pointer there. ``/computer/overlay/stop`` is its Stop button.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import threading
from typing import Any

from cognitrix.computer import ActionEvent, add_global_listener

TOKEN_ENV = 'COGNITRIX_DESKTOP_TOKEN'


def token_ok(given: str | None) -> bool:
    expected = os.environ.get(TOKEN_ENV)
    return bool(expected and given and hmac.compare_digest(expected.encode(), given.encode()))


def _payload(event: ActionEvent) -> dict[str, Any]:
    return {'window_id': event.window_id, 'action': event.action, 'x': event.x, 'y': event.y,
            'label': event.label, 'agent': event.agent or 'Agent', 'agent_id': event.agent_id or ''}


class _Hub:
    """Fans driver events (from tool threads) out to the open feeds."""

    def __init__(self) -> None:
        self._feeds: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._guard = threading.Lock()
        self._listening = False
        # agent id -> (user id, chat session id) of its latest action, for Stop.
        self.last_seen: dict[str, tuple[str | None, str | None]] = {}

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._guard:
            self._feeds.append((asyncio.get_running_loop(), queue))
            if not self._listening:
                add_global_listener(self._on_event)
                self._listening = True
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._guard:
            self._feeds = [(loop, q) for loop, q in self._feeds if q is not queue]

    def _on_event(self, event: ActionEvent) -> None:
        if event.desktop:
            return  # it happened in the agents' sandbox, not on this screen
        if event.agent_id:
            self.last_seen[event.agent_id] = (event.user_id, event.session_id)
        payload = _payload(event)
        with self._guard:
            feeds = list(self._feeds)
        for loop, queue in feeds:
            try:
                loop.call_soon_threadsafe(_offer, queue, payload)
            except RuntimeError:  # that feed's loop has closed
                self.unsubscribe(queue)


def _offer(queue: asyncio.Queue, payload: dict[str, Any]) -> None:
    if queue.full():  # a slow reader only misses old pointer moves
        queue.get_nowait()
    queue.put_nowait(payload)


hub = _Hub()


def stop(agent_id: str | None = None) -> list[str]:
    """Keep the agent (or every agent seen acting) off the computer and stop
    its chat turn when it has one. Returns the agents stopped."""
    from cognitrix.computer import get_driver

    targets = [agent_id] if agent_id else list(hub.last_seen)
    try:
        driver = get_driver()
    except Exception:
        driver = None
    for target in targets:
        if driver is not None:
            driver.stop_agent(target)
        user_id, session_id = hub.last_seen.get(target, (None, None))
        if session_id:
            try:
                from cognitrix.sessions import runs

                runs.stop_run(str(user_id or 'local'), session_id)
            except Exception:
                pass
    return targets
