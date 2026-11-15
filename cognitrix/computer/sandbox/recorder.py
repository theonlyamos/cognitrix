"""Teach a task: record what a person does on a sandbox desktop.

While a person has control of a desktop (Take over), the worker can record
their clicks, typing, key presses and scrolls, and turn them into readable
steps such as ``Click button 'Sign in' in Chromium``. The engine then asks a
model to write a skill from those steps, which the person reviews and saves,
so the agent can repeat the task later with the computer tools.

Two parts:

- ``StepBuilder`` turns raw input (a click at x, y; a key with its keysym and
  modifiers) into steps. It knows nothing about X, so it is tested on its own.
- ``Recorder`` listens to the desktop's X server with the RECORD extension
  (python3-xlib, installed in the image) and feeds the builder. It sees the
  person's input whether it comes from the live screen (x11vnc) or anything
  else on that display.

Passwords are never kept: text typed into a password field is recorded as
``{password}``.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger('cognitrix.sandbox.recorder')

MAX_STEPS = 400
DOUBLE_CLICK_S = 0.45
SECRET = '{password}'

# X keysyms for keys that end typing and become their own step.
_NAMED_KEYS = {
    0xFF0D: 'enter', 0xFF8D: 'enter', 0xFF09: 'tab', 0xFE20: 'shift+tab', 0xFF1B: 'escape',
    0xFF51: 'left', 0xFF52: 'up', 0xFF53: 'right', 0xFF54: 'down', 0xFF55: 'page_up', 0xFF56: 'page_down',
    0xFF50: 'home', 0xFF57: 'end', 0xFFFF: 'delete', 0xFF63: 'insert',
    **{0xFFBE + i: f'f{i + 1}' for i in range(12)},
}
_BACKSPACE = 0xFF08
# Modifier keys on their own are not steps.
_MODIFIER_KEYSYMS = set(range(0xFFE1, 0xFFEF)) | {0xFE03, 0xFF7E}
# X modifier state bits.
_SHIFT, _LOCK, _CTRL, _ALT, _SUPER = 1, 2, 4, 8, 64


def keysym_char(keysym: int) -> str:
    """The character a keysym types ('' for keys that don't type one)."""
    if 0x20 <= keysym <= 0x7E or 0xA0 <= keysym <= 0xFF:
        return chr(keysym)
    if 0x01000100 <= keysym <= 0x0110FFFF:  # Unicode keysyms
        return chr(keysym - 0x01000000)
    if keysym in (0xFF80,):  # keypad space
        return ' '
    if 0xFFB0 <= keysym <= 0xFFB9:  # keypad digits
        return chr(ord('0') + keysym - 0xFFB0)
    return {0xFFAA: '*', 0xFFAB: '+', 0xFFAD: '-', 0xFFAE: '.', 0xFFAF: '/'}.get(keysym, '')


def _mods(state: int) -> list[str]:
    out = []
    if state & _CTRL:
        out.append('ctrl')
    if state & _ALT:
        out.append('alt')
    if state & _SUPER:
        out.append('super')
    if state & _SHIFT:
        out.append('shift')
    return out


def _target_text(element: dict | None) -> str:
    if not element:
        return ''
    role, name = element.get('role') or '', (element.get('name') or '').strip()
    if name:
        return f"{role} '{name[:80]}'" if role else f"'{name[:80]}'"
    return role


class StepBuilder:
    """Builds steps from input events.

    ``describe(x, y)`` returns where a screen point is: ``{'app', 'window',
    'rect', 'element': {'role', 'name'}}`` (any part may be missing).
    ``focused()`` returns the same shape for where typing goes now.
    ``windows()`` returns the open windows as ``[(id, app, title)]``, to
    notice windows the person opened.
    """

    def __init__(self, describe: Callable[[int, int], dict] | None = None,
                 focused: Callable[[], dict] | None = None,
                 windows: Callable[[], list[tuple[int, str, str]]] | None = None,
                 clock: Callable[[], float] = time.time):
        self._describe = describe or (lambda x, y: {})
        self._focused = focused or (lambda: {})
        self._windows = windows
        self._clock = clock
        self.steps: list[dict] = []
        self._typing: dict | None = None
        self._last_click: tuple[float, int, int, int] | None = None
        self._known: set[int] | None = None
        self.started = clock()
        self.truncated = False

    # -- helpers ------------------------------------------------------------
    def _add(self, step: dict) -> None:
        if len(self.steps) >= MAX_STEPS:
            self.truncated = True
            return
        step.setdefault('t', round(self._clock() - self.started, 2))
        self.steps.append(step)

    def _end_typing(self) -> None:
        typing, self._typing = self._typing, None
        if typing and (typing['text'] or typing.get('secret')):
            if typing.get('secret'):
                typing['text'] = SECRET
            self._add(typing)

    def _note_new_windows(self) -> None:
        if self._windows is None:
            return
        try:
            now = self._windows()
        except Exception:
            return
        ids = {w[0] for w in now}
        if self._known is not None:
            for wid, app, title in now:
                if wid not in self._known:
                    self._add({'kind': 'window_opened', 'app': app, 'window': title})
        self._known = ids

    @staticmethod
    def _where(info: dict) -> dict:
        out = {}
        if info.get('app'):
            out['app'] = info['app']
        if info.get('window'):
            out['window'] = info['window'][:120]
        return out

    # -- events -------------------------------------------------------------
    def click(self, x: int, y: int, button: int = 1) -> None:
        if button in (4, 5, 6, 7):
            self.scroll({4: 'up', 5: 'down', 6: 'left', 7: 'right'}[button], x, y)
            return
        self._end_typing()
        now = self._clock()
        last = self._last_click
        prev = self.steps[-1] if self.steps else None
        if (last and button == 1 and last[3] == 1 and now - last[0] <= DOUBLE_CLICK_S
                and abs(last[1] - x) <= 4 and abs(last[2] - y) <= 4 and prev and prev.get('kind') == 'click'):
            prev['kind'] = 'double_click'
            self._last_click = None
            return
        self._last_click = (now, x, y, button)
        self._note_new_windows()
        info = self._describe(x, y) or {}
        step = {'kind': {1: 'click', 2: 'middle_click', 3: 'right_click'}.get(button, 'click'), **self._where(info)}
        target = _target_text(info.get('element'))
        if target:
            step['target'] = target
        rect = info.get('rect')
        if rect and rect[2] > rect[0] and rect[3] > rect[1]:
            # Where in the window, 0-1000 each way: a fallback when the
            # element has no name.
            step['at'] = [round((x - rect[0]) * 1000 / (rect[2] - rect[0])),
                          round((y - rect[1]) * 1000 / (rect[3] - rect[1]))]
        self._add(step)

    def scroll(self, direction: str, x: int = 0, y: int = 0) -> None:
        self._end_typing()
        prev = self.steps[-1] if self.steps else None
        if prev and prev.get('kind') == 'scroll' and prev.get('direction') == direction:
            prev['amount'] = prev.get('amount', 1) + 1
            return
        info = self._describe(x, y) or {}
        self._add({'kind': 'scroll', 'direction': direction, 'amount': 1, **self._where(info)})

    def key(self, keysym: int, state: int = 0) -> None:
        """One key press. ``state`` is the X modifier mask at the time."""
        if keysym in _MODIFIER_KEYSYMS:
            return
        mods = _mods(state)
        char = keysym_char(keysym)
        shortcut = [m for m in mods if m != 'shift']
        if char and not shortcut:
            if self._typing is None:
                self._note_new_windows()
                info = self._focused() or {}
                element = info.get('element') or {}
                self._typing = {'kind': 'type', 'text': '', **self._where(info)}
                target = _target_text(element)
                if target:
                    self._typing['target'] = target
                if 'password' in (element.get('role') or ''):
                    self._typing['secret'] = True
            if not self._typing.get('secret'):
                self._typing['text'] += char
            return
        if keysym == _BACKSPACE and self._typing is not None and not shortcut:
            if not self._typing.get('secret'):
                self._typing['text'] = self._typing['text'][:-1]
            return
        self._end_typing()
        name = _NAMED_KEYS.get(keysym)
        if name is None:
            if char:
                name = char.lower()
            elif keysym == _BACKSPACE:
                name = 'backspace'
            else:
                return  # a key we can't name; leave it out rather than guess
        if name == 'shift+tab':
            keys = name
        else:
            keys = '+'.join([*mods, name]) if (shortcut or name not in ('enter', 'tab', 'escape') or 'shift' in mods) \
                else name
        self._note_new_windows()
        info = self._focused() or {}
        self._add({'kind': 'key', 'keys': keys, **self._where(info)})

    def finish(self) -> list[dict]:
        self._end_typing()
        return self.steps


def steps_as_text(steps: list[dict]) -> str:
    """Numbered, readable steps (also what the model reads)."""
    lines = []
    for i, s in enumerate(steps, 1):
        where = ''
        if s.get('app') or s.get('window'):
            app = s.get('app') or ''
            win = s.get('window') or ''
            where = f' in {app}' + (f" ('{win}')" if win and win != app else '')
        kind = s.get('kind')
        if kind in ('click', 'double_click', 'right_click', 'middle_click'):
            verb = {'click': 'Click', 'double_click': 'Double-click', 'right_click': 'Right-click',
                    'middle_click': 'Middle-click'}[kind]
            target = s.get('target') or (f"the point {s['at'][0]}/1000 across, {s['at'][1]}/1000 down"
                                         if s.get('at') else 'the screen')
            lines.append(f'{i}. {verb} {target}{where}')
        elif kind == 'type':
            into = f" into {s['target']}" if s.get('target') else ''
            lines.append(f"{i}. Type {s.get('text', '')!r}{into}{where}")
        elif kind == 'key':
            lines.append(f"{i}. Press {s.get('keys')}{where}")
        elif kind == 'scroll':
            lines.append(f"{i}. Scroll {s.get('direction')} {s.get('amount', 1)}x{where}")
        elif kind == 'window_opened':
            lines.append(f"{i}. (A window opened: {s.get('app') or ''} '{s.get('window') or ''}')")
    return '\n'.join(lines)


class Recorder:
    """Records one desktop's input with the X RECORD extension."""

    def __init__(self, display: str | None = None, backend: Any = None):
        self.display_name = display
        self.backend = backend
        self.builder: StepBuilder | None = None
        self._queue: queue.Queue = queue.Queue()
        self._ctl = None
        self._rec = None
        self._ctx = None
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self.error = ''

    @property
    def recording(self) -> bool:
        return self.builder is not None and self._ctx is not None

    # -- what is under a point / has focus --------------------------------
    def _windows(self) -> list[tuple[int, str, str]]:
        if self.backend is None:
            return []
        return [(w.id, w.app, w.title) for w in self.backend.list_windows()]

    def _window_at(self, x: int, y: int):
        if self.backend is None:
            return None
        wins = [w for w in self.backend.list_windows()
                if not w.minimized and w.rect[0] <= x < w.rect[2] and w.rect[1] <= y < w.rect[3]]
        if not wins:
            return None
        try:
            front = self.backend.foreground()
        except Exception:
            front = 0
        return next((w for w in wins if w.id == front), None) or min(
            wins, key=lambda w: (w.rect[2] - w.rect[0]) * (w.rect[3] - w.rect[1]))

    def describe(self, x: int, y: int) -> dict:
        from cognitrix.computer import _hit

        try:
            win = self._window_at(x, y)
            if win is None:
                return {'app': 'desktop'}
            out: dict[str, Any] = {'app': win.app, 'window': win.title, 'rect': win.rect}
            hit = _hit(self.backend.elements(win.id), x, y)
            if hit is not None:
                out['element'] = {'role': hit.role, 'name': hit.name}
            return out
        except Exception:
            logger.debug('describe failed', exc_info=True)
            return {}

    def focused(self) -> dict:
        try:
            wid = self.backend.foreground() if self.backend is not None else 0
            win = self.backend.window(wid) if wid else None
            if win is None:
                return {}
            out: dict[str, Any] = {'app': win.app, 'window': win.title, 'rect': win.rect}
            el = self.backend.focused_element(wid)
            if el is not None:
                out['element'] = {'role': el.role, 'name': el.name}
            return out
        except Exception:
            logger.debug('focused failed', exc_info=True)
            return {}

    # -- start / stop -------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self.recording:
                return
            from Xlib import X, display
            from Xlib.ext import record

            self.builder = StepBuilder(self.describe, self.focused, self._windows)
            self.error = ''
            self._ctl = display.Display(self.display_name)
            self._rec = display.Display(self.display_name)
            if not self._rec.has_extension('RECORD'):
                self.builder = None
                raise RuntimeError("This desktop can't record (the X RECORD extension is missing).")
            self._ctx = self._rec.record_create_context(0, [record.AllClients], [{
                'core_requests': (0, 0), 'core_replies': (0, 0),
                'ext_requests': (0, 0, 0, 0), 'ext_replies': (0, 0, 0, 0),
                'delivered_events': (0, 0), 'device_events': (X.KeyPress, X.ButtonPress),
                'errors': (0, 0), 'client_started': False, 'client_died': False,
            }])
            self._queue = queue.Queue()
            listen = threading.Thread(target=self._listen, name='teach-record', daemon=True)
            work = threading.Thread(target=self._work, name='teach-steps', daemon=True)
            self._threads = [listen, work]
            listen.start()
            work.start()

    def _listen(self) -> None:
        from Xlib import X
        from Xlib.ext import record
        from Xlib.protocol import rq

        rec, ctl = self._rec, self._ctl

        def on_reply(reply) -> None:
            if reply.category != record.FromServer or reply.client_swapped or not reply.data:
                return
            data = reply.data
            while len(data):
                event, data = rq.EventField(None).parse_binary_value(data, rec.display, None, None)
                if event.type == X.ButtonPress:
                    self._queue.put(('click', event.root_x, event.root_y, event.detail))
                elif event.type == X.KeyPress:
                    shifted = 1 if event.state & _SHIFT else 0
                    keysym = ctl.keycode_to_keysym(event.detail, shifted) or ctl.keycode_to_keysym(event.detail, 0)
                    if event.state & _LOCK and 0x61 <= keysym <= 0x7A and not shifted:
                        keysym -= 0x20  # caps lock
                    self._queue.put(('key', keysym, event.state))

        try:
            rec.record_enable_context(self._ctx, on_reply)
        except Exception as exc:  # the display went away
            self.error = f'{type(exc).__name__}: {exc}'
            logger.info('recording stopped: %s', self.error)
        finally:
            self._queue.put(None)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            builder = self.builder
            if builder is None:
                continue
            try:
                if item[0] == 'click':
                    builder.click(item[1], item[2], item[3])
                else:
                    builder.key(item[1], item[2])
            except Exception:
                logger.exception('could not record a step')

    def steps(self) -> list[dict]:
        builder = self.builder
        if builder is None:
            return []
        out = [dict(s) for s in builder.steps]
        typing = builder._typing
        if typing and (typing.get('text') or typing.get('secret')):
            out.append({**typing, 'text': SECRET if typing.get('secret') else typing['text'], 'typing': True})
        return out

    def stop(self) -> dict:
        with self._lock:
            builder = self.builder
            if builder is None:
                return {'steps': [], 'truncated': False}
            try:
                if self._ctl is not None and self._ctx is not None:
                    self._ctl.record_disable_context(self._ctx)
                    self._ctl.flush()
            except Exception:
                logger.debug('record_disable_context failed', exc_info=True)
            for thread in self._threads:
                thread.join(timeout=5)
            try:
                if self._rec is not None and self._ctx is not None:
                    self._rec.record_free_context(self._ctx)
            except Exception:
                pass
            for dpy in (self._rec, self._ctl):
                try:
                    if dpy is not None:
                        dpy.close()
                except Exception:
                    pass
            self._ctl = self._rec = self._ctx = None
            self.builder = None
            self._threads = []
            steps = builder.finish()
            return {'steps': steps, 'truncated': builder.truncated, 'seconds': round(time.time() - builder.started)}
