"""One desktop's driver, inside the sandbox container.

Started by the supervisor with this desktop's DISPLAY, it hosts
``Driver(LinuxBackend(), sandbox=True)`` and answers ``POST /call`` on
127.0.0.1 only (the supervisor is the only caller):

    {"method": "look", "args": {...}, "actor": {...}}
    -> {"ok": true, "result": ..., "events": [...]}
    -> {"ok": false, "error": "stale", "message": "..."}

    python -m cognitrix.computer.sandbox.worker --port 9001
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cognitrix.computer import (
    Actor, Blocked, Busy, Driver, DriverError, HumanControl, Stale, Unsupported, current_actor,
)
from cognitrix.computer.sandbox import (
    event_to_dict, image_to_b64, result_to_dict, snapshot_to_dict, window_to_dict,
)

logger = logging.getLogger('cognitrix.sandbox.worker')

_ERRORS = ((HumanControl, 'human_control'), (Stale, 'stale'), (Busy, 'busy'), (Blocked, 'blocked'),
           (Unsupported, 'unsupported'), (DriverError, 'driver'))


class Worker:
    def __init__(self, driver: Driver):
        self.driver = driver
        self._local = threading.local()
        driver.add_listener(self._on_event)

    def _on_event(self, event) -> None:
        bucket = getattr(self._local, 'events', None)
        if bucket is not None:
            bucket.append(event_to_dict(event))

    # Each method takes keyword arguments from JSON and returns JSON.
    def list_windows(self, query: str | None = None) -> list:
        return [window_to_dict(w) for w in self.driver.list_windows(query)]

    def window(self, window_id: int) -> dict | None:
        return window_to_dict(self.driver.backend.window(int(window_id)))

    def open(self, target: str, args: str | None = None, timeout_s: float = 12.0) -> dict | None:
        return window_to_dict(self.driver.open(target, timeout_s=float(timeout_s), args=args))

    def look(self, window_id: int, query: str | None = None, limit: int = 60, viewer: Any = None) -> dict:
        image, snap, shown, note = self.driver.look(int(window_id), query, int(limit), viewer=viewer)
        return {'image': image_to_b64(image), 'snapshot': snapshot_to_dict(snap),
                'shown': [i for i, _ in shown], 'note': note}

    def act_on_element(self, window_id: int, index: int, action: str = 'click', snapshot_id: str | None = None,
                       viewer: Any = None, use_screen: bool = False) -> dict:
        return result_to_dict(self.driver.act_on_element(int(window_id), int(index), action, snapshot_id,
                                                         viewer, bool(use_screen)))

    def set_text(self, window_id: int, text: str, index: int | None = None, snapshot_id: str | None = None,
                 viewer: Any = None) -> dict:
        return result_to_dict(self.driver.set_text(int(window_id), text, None if index is None else int(index),
                                                   snapshot_id, viewer))

    def click_point(self, window_id: int, x: float, y: float, button: str = 'left', double: bool = False,
                    viewer: Any = None, use_screen: bool = False) -> dict:
        return result_to_dict(self.driver.click_point(int(window_id), float(x), float(y), button, bool(double),
                                                      viewer, bool(use_screen)))

    def type_text(self, window_id: int, text: str, x: float | None = None, y: float | None = None,
                  viewer: Any = None, use_screen: bool = False) -> dict:
        return result_to_dict(self.driver.type_text(int(window_id), text, x, y, viewer, bool(use_screen)))

    def press_key(self, window_id: int, keys: str, use_screen: bool = False) -> dict:
        return result_to_dict(self.driver.press_key(int(window_id), keys, bool(use_screen)))

    def scroll(self, window_id: int, direction: str = 'down', amount: int = 3, index: int | None = None,
               viewer: Any = None) -> dict:
        return result_to_dict(self.driver.scroll(int(window_id), direction, int(amount),
                                                 None if index is None else int(index), viewer))

    def stop_agent(self, agent_id: str) -> None:
        self.driver.stop_agent(str(agent_id))

    def hold(self, by: str = 'the person') -> None:
        self.driver.hold(by)

    def release(self) -> None:
        self.driver.release()

    def status(self) -> dict:
        return {'held_by': self.driver.held_by, 'windows': len(self.driver.list_windows())}

    METHODS = frozenset({'list_windows', 'window', 'open', 'look', 'act_on_element', 'set_text', 'click_point',
                         'type_text', 'press_key', 'scroll', 'stop_agent', 'hold', 'release', 'status'})

    def call(self, method: str, args: dict, actor: dict | None) -> dict:
        if method not in self.METHODS:
            return {'ok': False, 'error': 'bad_method', 'message': f'unknown method {method!r}'}
        token = current_actor.set(Actor(**actor) if actor else None)
        self._local.events = []
        try:
            result = getattr(self, method)(**(args or {}))
            return {'ok': True, 'result': result, 'events': self._local.events}
        except TypeError as exc:
            return {'ok': False, 'error': 'bad_args', 'message': str(exc)}
        except DriverError as exc:
            kind = next(name for cls, name in _ERRORS if isinstance(exc, cls))
            return {'ok': False, 'error': kind, 'message': str(exc), 'events': self._local.events}
        except Exception as exc:  # keep the worker alive
            logger.exception('sandbox worker %s failed', method)
            return {'ok': False, 'error': 'internal', 'message': f'{type(exc).__name__}: {exc}'}
        finally:
            self._local.events = None
            current_actor.reset(token)


def serve(worker: Worker, port: int, host: str = '127.0.0.1') -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def do_POST(self):  # noqa: N802
            if self.path != '/call':
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                out = worker.call(str(body.get('method', '')), body.get('args') or {}, body.get('actor'))
            except (ValueError, json.JSONDecodeError):
                out = {'ok': False, 'error': 'bad_request', 'message': 'invalid JSON'}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description='Driver worker for one sandbox desktop')
    parser.add_argument('--port', type=int, required=True)
    opts = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    from cognitrix.computer.linux import LinuxBackend

    worker = Worker(Driver(LinuxBackend(), sandbox=True))
    serve(worker, opts.port).serve_forever()


if __name__ == '__main__':
    main()
