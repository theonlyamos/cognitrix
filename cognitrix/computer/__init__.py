"""Background computer use: drive app windows without taking the screen.

The driver reads a window (screenshot + accessibility elements) and acts on it
through accessibility patterns or posted window messages, so the person keeps
their keyboard, mouse and foreground window. Every action reads state back and
reports one of:

- ``confirmed``: the effect was seen (value, element state, title or tree changed)
- ``unverified``: the action was delivered but no change could be seen
- ``needs_screen``: this target can't take the action in the background
- ``failed``: the action did not land (for example typed text is missing)

Only Windows has a backend today; elsewhere ``get_driver()`` raises
``Unsupported``. Platform code is imported lazily so servers never load it.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger('cognitrix.computer')

CONFIRMED = 'confirmed'
UNVERIFIED = 'unverified'
NEEDS_SCREEN = 'needs_screen'
FAILED = 'failed'

UNSUPPORTED_MESSAGE = (
    'Background computer use is only available on Windows for now. '
    'Use take_screenshot, mouse_click and text_input to control the screen instead.'
)


class Unsupported(RuntimeError):
    """No background driver on this platform."""


class DriverError(RuntimeError):
    """A request the driver refuses or can't serve (message is model-facing)."""


class Busy(DriverError):
    pass


class Stale(DriverError):
    pass


class Blocked(DriverError):
    pass


@dataclass
class WindowInfo:
    id: int
    title: str
    app: str  # executable name, e.g. 'notepad.exe' (the real app for UWP frames)
    pid: int
    minimized: bool = False
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # screen left, top, right, bottom
    kind: str = 'win32'  # 'win32' | 'uwp' | 'chromium' | 'console'
    blocked: str | None = None  # why the agent may not act on it


@dataclass
class Element:
    role: str
    name: str = ''
    value: str | None = None
    enabled: bool = True
    actions: list[str] = field(default_factory=list)
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # screen coordinates
    state: dict[str, Any] = field(default_factory=dict)  # toggle/expanded/selected
    accelerator: str = ''
    ref: Any = None  # backend handle

    def describe(self) -> str:
        bits = [self.role]
        if self.name:
            bits.append(repr(self.name[:80]))
        if self.value not in (None, ''):
            bits.append(f'value={self.value[:120]!r}')
        for key, val in self.state.items():
            bits.append(f'{key}={val}')
        if not self.enabled:
            bits.append('disabled')
        if self.actions:
            bits.append('actions=' + ','.join(self.actions))
        return ' '.join(bits)


@dataclass
class Snapshot:
    id: str
    window_id: int
    elements: list[Element]
    origin: tuple[int, int]  # screen position of the image's top-left pixel
    size: tuple[int, int]  # window image size in physical pixels
    scale: float  # physical pixels per pixel of the image the model saw


@dataclass
class ActionResult:
    status: str
    message: str
    focus_changed: bool = False

    def text(self) -> str:
        out = f'{self.status}: {self.message}'
        if self.focus_changed:
            out += ' Note: the foreground window changed during this action.'
        return out


@dataclass
class ActionEvent:
    """Emitted before each action, e.g. for an on-screen agent cursor."""
    window_id: int
    action: str
    x: int | None = None  # screen coordinates, when the action has a point
    y: int | None = None


class Backend(Protocol):
    def list_windows(self) -> list[WindowInfo]: ...
    def window(self, window_id: int) -> WindowInfo | None: ...
    def foreground(self) -> int: ...
    def launch(self, target: str, args: str | None = None) -> None: ...
    def prepare(self, window_id: int) -> str: ...  # e.g. restore a minimized window; returns a note
    def capture(self, window_id: int) -> tuple[Any, tuple[int, int], str]: ...  # image, origin, note
    def elements(self, window_id: int) -> list[Element]: ...
    def element_state(self, element: Element) -> Element | None: ...  # fresh read; None when gone
    def perform(self, element: Element, action: str, value: str | None = None) -> None: ...
    def focused_element(self, window_id: int) -> Element | None: ...
    def post_click(self, window_id: int, x: int, y: int, button: str, double: bool) -> None: ...
    def post_text(self, window_id: int, text: str) -> None: ...
    def post_key(self, window_id: int, key: str) -> None: ...
    def post_scroll(self, window_id: int, x: int, y: int, amount: int, horizontal: bool = False) -> None: ...
    def fingerprint(self, window_id: int) -> str: ...
    def give_back_focus(self, window_id: int) -> bool: ...
    def wake(self, window_id: int) -> None: ...  # optional
    # Real input for use_screen (takes the screen for a moment).
    def bring_to_front(self, window_id: int) -> bool: ...
    def idle_seconds(self) -> float: ...  # since the person's last keyboard/mouse input
    def cursor_pos(self) -> tuple[int, int]: ...
    def set_cursor_pos(self, x: int, y: int) -> None: ...
    # send_* stop and return False as soon as still_ok() is False.
    def send_click(self, x: int, y: int, button: str, double: bool, still_ok: Callable[[], bool]) -> bool: ...
    def send_text(self, text: str, still_ok: Callable[[], bool]) -> bool: ...
    def send_key(self, key: str, mods: tuple[str, ...], still_ok: Callable[[], bool]) -> bool: ...


# Executables the agent never drives in the background.
TERMINAL_APPS = frozenset({
    'cmd.exe', 'powershell.exe', 'pwsh.exe', 'windowsterminal.exe', 'wt.exe', 'openconsole.exe',
    'conhost.exe', 'mintty.exe', 'alacritty.exe', 'wezterm-gui.exe', 'conemu.exe', 'conemu64.exe',
    'hyper.exe', 'tabby.exe', 'warp.exe', 'putty.exe', 'kitty.exe', 'cmder.exe', 'bash.exe', 'wsl.exe',
})
PASSWORD_MANAGERS = frozenset({
    '1password.exe', 'bitwarden.exe', 'keepass.exe', 'keepassxc.exe', 'dashlane.exe', 'lastpass.exe',
    'enpass.exe', 'nordpass.exe', 'roboform.exe', 'keeper.exe', 'keeperpasswordmanager.exe',
    'protonpass.exe', 'passwordsafe.exe', 'credentialuibroker.exe',
})
OWN_APPS = frozenset({'cognitrix-desktop.exe', 'cognitrix-engine.exe', 'cognitrix.exe'})
SECURE_APPS = frozenset({'consent.exe', 'logonui.exe', 'lockapp.exe'})


def blocked_reason(app: str, kind: str = 'win32', title: str = '', own: bool = False,
                   elevated: bool = False) -> str | None:
    """Why the agent may not act on a window, or None."""
    app = (app or '').lower()
    if own or app in OWN_APPS:
        return "it is Cognitrix's own window"
    if kind == 'console' or app in TERMINAL_APPS:
        return 'it is a terminal; use the shell tool instead'
    if app in PASSWORD_MANAGERS:
        return 'it is a password manager'
    if app in SECURE_APPS:
        return 'it is a Windows security prompt'
    if elevated:
        return 'it runs as administrator, and Windows blocks input from normal apps to it'
    return None


SCREEN_RETRY = (' To do it anyway, retry with use_screen=true: that brings the window to the front for a '
                'moment and uses the real mouse and keyboard, and the person is asked first.')
SCREEN_USED = ' The screen was used for a moment; the pointer and the foreground window were put back.'
SCREEN_ABORTED = ('Stopped: another window came to the front (the person may be using the computer), so the '
                  'rest was not sent. Look at the window to see what happened.')

_snapshot_ids = itertools.count(1)
_SHOT_MAX_W, _SHOT_MAX_H = 1280, 800


class Driver:
    """Platform-neutral logic: snapshots, locks, safety, verification."""

    settle_s = 0.25  # wait before reading an action's effect back
    lock_timeout_s = 15.0
    open_watch_s = 2.0  # how long open() watches for a late focus grab
    screen_idle_s = 1.0  # use_screen waits for the person to pause this long...
    screen_wait_s = 6.0  # ...for at most this long

    def __init__(self, backend: Backend):
        self.backend = backend
        self._snapshots: dict[int, Snapshot] = {}
        # (viewer, window) -> the snapshot id that viewer last looked at
        self._seen: dict[tuple[Any, int], str] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._listeners: list[Callable[[ActionEvent], None]] = []

    # -- hooks ------------------------------------------------------------
    def add_listener(self, callback: Callable[[ActionEvent], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[ActionEvent], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, event: ActionEvent) -> None:
        for callback in list(self._listeners):
            try:
                callback(event)
            except Exception:
                logger.exception('computer-use listener failed')

    # -- windows ----------------------------------------------------------
    def list_windows(self, query: str | None = None) -> list[WindowInfo]:
        windows = self.backend.list_windows()
        if query:
            q = query.lower()
            windows = [w for w in windows if q in w.title.lower() or q in w.app.lower()]
        return windows

    def _window(self, window_id: int, acting: bool = True) -> WindowInfo:
        win = self.backend.window(int(window_id))
        if win is None:
            raise DriverError(f'No window with id {window_id}. Call list_windows to see open windows.')
        if acting and win.blocked:
            raise Blocked(f"Can't control '{win.title}' because {win.blocked}.")
        return win

    def open(self, target: str, timeout_s: float = 12.0, args: str | None = None) -> WindowInfo | None:
        """Start an app or open a file without activating it. Returns the window
        it opened, found by diffing the window list (launch pids are unreliable
        for Explorer and UWP apps)."""
        before = {w.id: w.title for w in self.backend.list_windows()}
        fg_before = self.backend.foreground()
        win = self._find_opened(target, before, timeout_s, args)
        if win is None or not fg_before:
            return win
        # Some apps (e.g. Notepad restoring its tabs) pull themselves to the
        # front a moment after their window appears: watch briefly and hand
        # focus back to the person's window.
        watch_until = time.monotonic() + self.open_watch_s
        while True:
            fg_after = self.backend.foreground()
            grabbed = self.backend.window(fg_after) if fg_after and fg_after != fg_before else None
            if grabbed is not None and grabbed.app == win.app:
                back = self.backend.give_back_focus(fg_before)
                logger.info('computer-use open: %s took focus; handed back=%s', win.app, back)
                break
            if time.monotonic() >= watch_until:
                break
            time.sleep(0.1)
        return win

    def _find_opened(self, target: str, before: dict[int, str], timeout_s: float = 12.0,
                     args: str | None = None) -> WindowInfo | None:
        if args:
            self.backend.launch(target, args)
        else:
            self.backend.launch(target)
        base = target.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1].lower()
        stem = base.rsplit('.', 1)[0]
        deadline = time.monotonic() + timeout_s
        gone: set[int] = set()
        while time.monotonic() < deadline:
            time.sleep(0.4)
            now = self.backend.list_windows()
            new = [w for w in now if w.id not in before and w.id not in gone and w.title]
            # Prefer a window of the program that was started (not a splash or helper).
            new.sort(key=lambda w: w.app != base)
            if new:
                win = self._settled_window(new[0], stem, deadline)
                if win is not None:
                    return win
                gone.add(new[0].id)  # a short-lived window (e.g. a splash screen); keep looking
                continue
            # Apps that reuse a window (e.g. a new tab) only change its title.
            renamed = [w for w in now if w.id in before and before[w.id] != w.title
                       and stem and stem in w.title.lower()]
            if renamed:
                return renamed[0]
        return None

    def _settled_window(self, win: WindowInfo, stem: str, deadline: float) -> WindowInfo | None:
        """Apps set their final title a moment after the window appears (e.g.
        'Notepad' then 'file.txt - Notepad'). Wait for the file name, or for the
        title to stop changing."""
        last, stable_since = win, time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(0.3)
            fresh = self.backend.window(win.id)
            if fresh is None:
                return None  # it closed again
            if stem and stem in fresh.title.lower():
                return fresh
            if fresh.title != last.title:
                last, stable_since = fresh, time.monotonic()
            elif time.monotonic() - stable_since > 1.5:
                return fresh
        return last

    # -- reading ----------------------------------------------------------
    def look(self, window_id: int, query: str | None = None, limit: int = 60, viewer: Any = None):
        """Screenshot + numbered elements. Returns (image, snapshot, shown, note).
        `viewer` names the caller (an agent): its later actions are refused when
        someone else's look has replaced the snapshot it saw."""
        win = self._window(window_id, acting=False)
        notes = [self.backend.prepare(win.id)]
        image, origin, cap_note = self.backend.capture(win.id)
        notes.append(cap_note)
        elements = self.backend.elements(win.id)
        if win.kind == 'chromium' and not any(e.role == 'Document' for e in elements):
            # Chromium/Electron build the page's tree only once asked for it.
            wake = getattr(self.backend, 'wake', None)
            for _ in range(6):  # a freshly loaded page can take a few seconds
                if wake:
                    wake(win.id)
                time.sleep(0.8)
                elements = self.backend.elements(win.id)
                if any(e.role == 'Document' for e in elements):
                    break
        size = image.size if image is not None else (win.rect[2] - win.rect[0], win.rect[3] - win.rect[1])
        scale = max(size[0] / _SHOT_MAX_W, size[1] / _SHOT_MAX_H, 1.0)
        snap = Snapshot(f's{next(_snapshot_ids)}', win.id, elements, origin, size, scale)
        self._snapshots[win.id] = snap
        if viewer is not None:
            self._seen[(viewer, win.id)] = snap.id
        if image is not None and scale > 1.0:
            image = image.resize((round(size[0] / scale), round(size[1] / scale)))
        shown = list(enumerate(elements))
        if query:
            q = query.lower()
            shown = [(i, e) for i, e in shown if q in e.name.lower() or q in (e.value or '').lower()]
        return image, snap, shown[:max(1, int(limit))], ' '.join(n for n in notes if n)

    def _snapshot(self, window_id: int, viewer: Any = None) -> Snapshot:
        """The window's latest snapshot, refused when this viewer hasn't seen it."""
        snap = self._snapshots.get(int(window_id))
        if snap is None:
            raise Stale('Look at the window first (look_at_window).')
        if viewer is not None and self._seen.get((viewer, int(window_id))) != snap.id:
            raise Stale('This window has been looked at again since your last look, so your element numbers '
                        'and coordinates are out of date. Look at the window again (look_at_window).')
        return snap

    @staticmethod
    def _moved(win: WindowInfo, snap: Snapshot) -> tuple[int, int]:
        """How far the window moved since the snapshot; a resized window is stale."""
        if (win.rect[2] - win.rect[0], win.rect[3] - win.rect[1]) != tuple(snap.size):
            raise Stale('The window was resized since your last look. Look at it again (look_at_window).')
        return win.rect[0] - snap.origin[0], win.rect[1] - snap.origin[1]

    def _element(self, window_id: int, index: int, snapshot_id: str | None,
                 viewer: Any = None) -> tuple[Snapshot, Element]:
        snap = self._snapshot(window_id, viewer)
        if snapshot_id and snapshot_id != snap.id:
            raise Stale(f'Element numbers from {snapshot_id} are out of date; use the latest look ({snap.id}).')
        if not 0 <= int(index) < len(snap.elements):
            raise Stale(f'There is no element {index} in {snap.id}. Look at the window again.')
        element = snap.elements[int(index)]
        fresh = self.backend.element_state(element)
        if fresh is None or fresh.role != element.role:
            raise Stale(f'Element {index} is gone or changed. Look at the window again.')
        return snap, fresh

    # -- locking + focus accounting -----------------------------------------
    def _lock(self, window_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(int(window_id), threading.Lock())

    def _act(self, window_id: int, action: str, body: Callable[[WindowInfo], ActionResult],
             point: tuple[int, int] | None = None) -> ActionResult:
        lock = self._lock(window_id)
        if not lock.acquire(timeout=self.lock_timeout_s):
            raise Busy('Another action is using this window right now. Try again in a moment.')
        try:
            win = self._window(window_id)
            self._emit(ActionEvent(win.id, action, *(point or (None, None))))
            fg_before = self.backend.foreground()
            try:
                result = body(win)
            except DriverError:
                raise
            except Exception as exc:  # the app or UI Automation rejected the call
                logger.info('computer-use %s on %s raised %r', action, win.app, exc)
                result = ActionResult(FAILED, f'The app rejected the action ({type(exc).__name__}). '
                                      'Look at the window and try another way.')
            fg_after = self.backend.foreground()
            result.focus_changed = fg_before != fg_after
            if result.focus_changed and fg_after == win.id and fg_before:
                # The app pulled itself to the front (e.g. modern menus do);
                # hand focus back to the window the person was using.
                back = self.backend.give_back_focus(fg_before)
                result.message += (' The app took focus; it was handed back to your previous window.'
                                    if back else ' The app took focus and it could not be handed back.')
            logger.info('computer-use %s on %s: %s (focus_changed=%s)',
                        action, win.app, result.status, result.focus_changed)
            return result
        finally:
            lock.release()

    def _settled_fingerprint(self, window_id: int, before: str) -> str:
        """Poll briefly for the window to change."""
        win = self.backend.window(window_id)
        # Web pages can take a few seconds to load after a click.
        polls = 12 if win is not None and win.kind == 'chromium' else 4
        after, deadline = before, time.monotonic() + polls * self.settle_s
        while True:
            time.sleep(self.settle_s)
            after = self.backend.fingerprint(window_id)
            if after != before or time.monotonic() >= deadline:
                return after

    def _with_screen(self, win: WindowInfo, send: Callable[[Callable[[], bool]], bool]) -> ActionResult | None:
        """Bring one window to the front, run `send` with real input, then put
        the pointer and the person's foreground window back. `send` gets a
        still_ok() check and stops when another window takes the front.
        Returns a result when nothing (or not everything) could be sent."""
        b = self.backend
        deadline = time.monotonic() + self.screen_wait_s
        while b.idle_seconds() < self.screen_idle_s:
            if time.monotonic() >= deadline:
                return ActionResult(FAILED, 'The person is using the keyboard or mouse right now, so the screen '
                                    'was not taken. Try again in a moment.')
            time.sleep(0.2)
        fg_before, cursor = b.foreground(), b.cursor_pos()
        if not b.bring_to_front(win.id):
            return ActionResult(FAILED, "Couldn't bring the window to the front, so nothing was sent.")

        def still_ok() -> bool:
            fg = b.foreground()
            if fg == win.id:
                return True
            other = b.window(fg) if fg else None
            return other is not None and other.pid == win.pid  # the app's own menus and dialogs

        try:
            done = send(still_ok)
        finally:
            b.set_cursor_pos(*cursor)
            if fg_before and fg_before != win.id:
                b.give_back_focus(fg_before)
        return None if done else ActionResult(FAILED, SCREEN_ABORTED)

    # -- actions ----------------------------------------------------------
    def act_on_element(self, window_id: int, index: int, action: str = 'click',
                       snapshot_id: str | None = None, viewer: Any = None,
                       use_screen: bool = False) -> ActionResult:
        def body(win: WindowInfo) -> ActionResult:
            snap, element = self._element(win.id, index, snapshot_id, viewer)
            if not element.enabled:
                return ActionResult(FAILED, f'Element {index} ({element.role} {element.name!r}) is disabled.')
            if use_screen:
                if element.rect[2] <= element.rect[0]:
                    return ActionResult(FAILED, f'Element {index} has no position on screen to click.')
                cx, cy = _center(element.rect)
                dx, dy = self._moved(win, snap)
                before_fp = self.backend.fingerprint(win.id)
                stopped = self._with_screen(
                    win, lambda ok: self.backend.send_click(cx + dx, cy + dy, 'left', False, ok))
                if stopped:
                    return stopped
                what = f'clicked {element.role} {element.name!r} with the screen'
                if self._settled_fingerprint(win.id, before_fp) != before_fp:
                    return ActionResult(CONFIRMED, f'{what}; the window changed.' + SCREEN_USED)
                return ActionResult(UNVERIFIED, f'{what}, but nothing visible changed.' + SCREEN_USED)
            chosen = _pick_action(element, action)
            if win.kind == 'chromium' and action == 'click' and element.rect[2] > element.rect[0]:
                # Chromium pulls its window to the front on accessibility
                # actions; a posted click at the control does not.
                chosen = 'pixel'
            if chosen is None:
                return ActionResult(
                    NEEDS_SCREEN if action == 'click' else FAILED,
                    f'Element {index} ({element.role} {element.name!r}) has no {action} action'
                    + ('; try click_in_window on it.' + SCREEN_RETRY if action == 'click' else '.'))
            before_fp = self.backend.fingerprint(win.id)
            if chosen == 'pixel':
                cx, cy = _center(element.rect)
                dx, dy = self._moved(win, snap)
                self.backend.post_click(win.id, cx + dx, cy + dy, 'left', False)
            else:
                self.backend.perform(element, chosen)
            after_fp = self._settled_fingerprint(win.id, before_fp)
            after = self.backend.element_state(element)
            what = f'{chosen} on {element.role} {element.name!r}'
            if after is not None and after.state != element.state:
                return ActionResult(CONFIRMED, f'{what}; it is now ' + _state_text(after.state) + '.')
            if after_fp != before_fp:
                return ActionResult(CONFIRMED, f'{what}; the window changed. Look again to see it.')
            return ActionResult(UNVERIFIED, f'{what} was sent but nothing visible changed. '
                                'Look at the window to check.')
        return self._act(window_id, f'{"screen" if use_screen else "element"}:{action}', body)

    def set_text(self, window_id: int, text: str, index: int | None = None,
                 snapshot_id: str | None = None, viewer: Any = None) -> ActionResult:
        def body(win: WindowInfo) -> ActionResult:
            if index is None:
                element = self.backend.focused_element(win.id)
                if element is None:
                    return ActionResult(FAILED, 'No text field has focus in that window; give an element number.')
            else:
                _, element = self._element(win.id, index, snapshot_id, viewer)
            if 'set_value' not in element.actions:
                return ActionResult(FAILED, f'{element.role} {element.name!r} does not accept text directly; '
                                    'use type_in_window instead.')
            self.backend.perform(element, 'set_value', text)
            time.sleep(self.settle_s)
            after = self.backend.element_state(element)
            if after is not None and (after.value or '') == text:
                return ActionResult(CONFIRMED, f'{element.role} {element.name!r} now holds the text.')
            if after is None:
                # Some fields (e.g. a rename box) disappear once the value is committed.
                return ActionResult(UNVERIFIED, 'The field closed after the change; look at the window to check.')
            if win.kind == 'chromium':
                return ActionResult(NEEDS_SCREEN, 'This app did not accept the text in the background. Use '
                                    'type_in_window on the field instead.' + SCREEN_RETRY)
            return ActionResult(FAILED, f'The field reads {(after.value or "")[:80]!r}, not the new text.')
        return self._act(window_id, 'set_text', body)

    def click_point(self, window_id: int, x: float, y: float, button: str = 'left',
                    double: bool = False, viewer: Any = None, use_screen: bool = False) -> ActionResult:
        """Click at (x, y) in the latest look's image coordinates."""
        snap = self._snapshot(window_id, viewer)
        px, py = round(x * snap.scale), round(y * snap.scale)
        if not (0 <= px < snap.size[0] and 0 <= py < snap.size[1]):
            raise DriverError(f'({x}, {y}) is outside the window image.')
        sx, sy = snap.origin[0] + px, snap.origin[1] + py

        def body(win: WindowInfo) -> ActionResult:
            before_fp = self.backend.fingerprint(win.id)
            if use_screen:
                dx, dy = self._moved(win, snap)
                stopped = self._with_screen(
                    win, lambda ok: self.backend.send_click(sx + dx, sy + dy, button, double, ok))
                if stopped:
                    return stopped
                how = f'{"double-" if double else ""}{button} click at ({x}, {y}) with the screen'
                if self._settled_fingerprint(win.id, before_fp) != before_fp:
                    return ActionResult(CONFIRMED, f'{how}; the window changed.' + SCREEN_USED)
                return ActionResult(UNVERIFIED, f'{how}, but nothing visible changed.' + SCREEN_USED)
            hit = None if (button != 'left' or double or win.kind == 'chromium') else _hit(snap.elements, sx, sy)
            if hit is not None:
                live = self.backend.element_state(hit)
                chosen = _pick_action(live, 'click') if live is not None else None
                if chosen and chosen != 'pixel':
                    self.backend.perform(live, chosen)
                    how = f'{chosen} on {live.role} {live.name!r}'
                else:
                    hit = None
            if hit is None:
                # The window may have been moved since the look.
                dx, dy = self._moved(win, snap)
                self.backend.post_click(win.id, sx + dx, sy + dy, button, double)
                how = f'{"double-" if double else ""}{button} click at ({x}, {y})'
            after_fp = self._settled_fingerprint(win.id, before_fp)
            if after_fp != before_fp:
                return ActionResult(CONFIRMED, f'{how}; the window changed. Look again to see it.')
            return ActionResult(UNVERIFIED, f'{how} was sent but nothing visible changed. Look at the window '
                                'to check; if it should have reacted, this app may need the screen.' + SCREEN_RETRY)
        return self._act(window_id, 'screen:click' if use_screen else 'click', body, (sx, sy))

    def type_text(self, window_id: int, text: str, x: float | None = None,
                  y: float | None = None, viewer: Any = None, use_screen: bool = False) -> ActionResult:
        if use_screen:
            return self._type_on_screen(window_id, text, x, y, viewer)
        if x is not None and y is not None:
            focus = self.click_point(window_id, x, y, viewer=viewer)
            if focus.status == NEEDS_SCREEN:
                return focus

        def body(win: WindowInfo) -> ActionResult:
            if win.kind == 'chromium':
                return ActionResult(NEEDS_SCREEN, 'Typing into this app (Chromium/Electron) only works with the '
                                    'screen. Try set_text on a field.' + SCREEN_RETRY)
            field_el = self.backend.focused_element(win.id)
            before_value = field_el.value if field_el is not None else None
            before_fp = self.backend.fingerprint(win.id)
            self.backend.post_text(win.id, text)
            time.sleep(self.settle_s)
            after = self.backend.element_state(field_el) if field_el is not None else None
            if after is not None and after.value is not None and before_value is not None:
                if text.strip() and text.strip() in after.value and text.strip() not in before_value:
                    return ActionResult(CONFIRMED, f'The text is in {after.role} {after.name!r}.')
                if after.value == before_value:
                    return ActionResult(FAILED, 'The text did not land: the focused field is unchanged. '
                                        'Click the field first (pass x and y) or use set_text.')
            if self._settled_fingerprint(win.id, before_fp) != before_fp:
                return ActionResult(UNVERIFIED, 'Typed, and the window changed, but the text could not be '
                                    'read back. Look at the window to check.')
            return ActionResult(UNVERIFIED, 'Typed, but nothing visible changed. Look at the window to check; '
                                'click the field first (pass x and y) if it was not focused.')
        return self._act(window_id, 'type', body)

    def _type_on_screen(self, window_id: int, text: str, x: float | None, y: float | None,
                        viewer: Any) -> ActionResult:
        point = None
        if x is not None and y is not None:
            snap = self._snapshot(window_id, viewer)
            point = (snap.origin[0] + round(x * snap.scale), snap.origin[1] + round(y * snap.scale))

        def body(win: WindowInfo) -> ActionResult:
            if point is None:
                field_el = self.backend.focused_element(win.id)
            else:
                # The field under the point, to read the text back afterwards.
                fields = [e for e in self._snapshots[win.id].elements if e.value is not None
                          and e.rect[0] <= point[0] < e.rect[2] and e.rect[1] <= point[1] < e.rect[3]]
                field_el = min(fields, key=_area) if fields else None
            before_value = field_el.value if field_el is not None else None
            before_fp = self.backend.fingerprint(win.id)

            def send(ok: Callable[[], bool]) -> bool:
                if point is not None:
                    dx, dy = self._moved(win, self._snapshots[win.id])
                    if not self.backend.send_click(point[0] + dx, point[1] + dy, 'left', False, ok):
                        return False
                    time.sleep(0.4)  # let the field take focus before typing
                return self.backend.send_text(text, ok)

            stopped = self._with_screen(win, send)
            if stopped:
                return stopped
            # Text ending in a new line submits it, which usually changes the field again.
            if field_el is not None and not text.endswith('\n'):
                after = self.backend.element_state(field_el)
                if after is not None and after.value is not None:
                    if text.strip() in after.value and text.strip() not in (before_value or ''):
                        return ActionResult(CONFIRMED, f'Typed with the screen; the text is in {after.role} '
                                            f'{after.name!r}.' + SCREEN_USED)
                    if after.value != (before_value or ''):
                        # It changed, but not to the text (e.g. characters were lost).
                        return ActionResult(FAILED, f'Typed with the screen, but {after.role} {after.name!r} '
                                            f'reads {after.value[:80]!r}. Look at the window and try again.'
                                            + SCREEN_USED)
                    # An unchanged value may just be a field that doesn't expose its text
                    # (e.g. VS Code's editor): judge by the window below.
            if self._settled_fingerprint(win.id, before_fp) != before_fp:
                return ActionResult(CONFIRMED, 'Typed with the screen; the window changed. Look again to check '
                                    'the text.' + SCREEN_USED)
            return ActionResult(UNVERIFIED, 'Typed with the screen, but nothing visible changed. Look at the '
                                'window to check.' + SCREEN_USED)
        return self._act(window_id, 'screen:type', body, point)

    def press_key(self, window_id: int, keys: str, use_screen: bool = False) -> ActionResult:
        combo = [k.strip().lower() for k in keys.replace(' ', '').split('+') if k.strip()]
        if not combo:
            raise DriverError('Give a key, e.g. "enter" or "ctrl+s".')
        mods, key = combo[:-1], combo[-1]

        def body(win: WindowInfo) -> ActionResult:
            if use_screen:
                before_fp = self.backend.fingerprint(win.id)
                stopped = self._with_screen(win, lambda ok: self.backend.send_key(
                    key, tuple(_KEY_ALIASES.get(m, m) for m in mods), ok))
                if stopped:
                    return stopped
                if self._settled_fingerprint(win.id, before_fp) != before_fp:
                    return ActionResult(CONFIRMED, f'Pressed {keys} with the screen; the window changed.'
                                        + SCREEN_USED)
                return ActionResult(UNVERIFIED, f'Pressed {keys} with the screen, but nothing visible changed.'
                                    + SCREEN_USED)
            if win.kind == 'chromium':
                return ActionResult(NEEDS_SCREEN, 'Keys can only be pressed in this app (Chromium/Electron) with '
                                    'the screen. Click a control with click_element instead.' + SCREEN_RETRY)
            before_fp = self.backend.fingerprint(win.id)
            target = _accelerator(self.backend.elements(win.id), mods, key) if mods else None
            if target is not None:
                self.backend.perform(target, _pick_action(target, 'click') or 'invoke')
                how = f'{keys} ({target.role} {target.name!r})'
            elif mods:
                try:
                    self.backend.post_key(win.id, key, tuple(_KEY_ALIASES.get(m, m) for m in mods))
                except (RuntimeError, ValueError) as exc:
                    return ActionResult(NEEDS_SCREEN, f"{keys} can't be sent in the background here ({exc}). "
                                        'Look for the command as an element instead.' + SCREEN_RETRY)
                how = f'pressed {keys}'
            else:
                self.backend.post_key(win.id, key)
                how = f'pressed {key}'
            if self._settled_fingerprint(win.id, before_fp) != before_fp:
                return ActionResult(CONFIRMED, f'{how}; the window changed.')
            return ActionResult(UNVERIFIED, f'{how}, but nothing visible changed. Look at the window to check.')
        return self._act(window_id, f'{"screen:" if use_screen else ""}key:{keys}', body)

    def scroll(self, window_id: int, direction: str = 'down', amount: int = 3,
               index: int | None = None, snapshot_id: str | None = None,
               viewer: Any = None) -> ActionResult:
        direction = direction.lower()
        if direction not in ('up', 'down', 'left', 'right'):
            raise DriverError('direction must be up, down, left or right.')

        def body(win: WindowInfo) -> ActionResult:
            before_fp = self.backend.fingerprint(win.id)
            target = None
            if index is not None:
                _, target = self._element(win.id, index, snapshot_id, viewer)
            else:
                snap = self._snapshots.get(win.id)
                scrollable = [e for e in (snap.elements if snap else []) if 'scroll' in e.actions]
                if scrollable:
                    target = self.backend.element_state(max(scrollable, key=_area))
            # Chromium ignores posted wheel messages, so its scroll pattern is
            # used even though it briefly pulls the window forward (focus is
            # handed back by _act).
            if target is not None and 'scroll' in target.actions:
                self.backend.perform(target, f'scroll_{direction}', str(int(amount)))
                after = self.backend.element_state(target)
                if after is not None and after.state != target.state:
                    return ActionResult(CONFIRMED, f'Scrolled {direction}; ' + _state_text(after.state) + '.')
            else:
                rect = target.rect if target is not None else win.rect
                cx, cy = _center(rect)
                latest = self._snapshots.get(win.id)
                if target is not None and latest is not None:
                    dx, dy = self._moved(win, latest)
                    cx, cy = cx + dx, cy + dy
                # Wheel deltas: positive is up, or right on the horizontal wheel.
                sign = 1 if direction in ('up', 'right') else -1
                self.backend.post_scroll(win.id, cx, cy, sign * int(amount), direction in ('left', 'right'))
            if self._settled_fingerprint(win.id, before_fp) != before_fp:
                return ActionResult(CONFIRMED, f'Scrolled {direction}; the window changed.')
            return ActionResult(UNVERIFIED, f'Scrolled {direction}, but nothing visible changed (maybe already at '
                                'the end). Look at the window to check.')
        return self._act(window_id, 'scroll', body)


def _area(e: Element) -> int:
    return max(0, e.rect[2] - e.rect[0]) * max(0, e.rect[3] - e.rect[1])


def _center(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


def _hit(elements: list[Element], x: int, y: int) -> Element | None:
    """Smallest element with an action under a screen point (works when the
    window is covered, unlike an on-screen hit test)."""
    hits = [e for e in elements if e.actions and e.rect[0] <= x < e.rect[2] and e.rect[1] <= y < e.rect[3]]
    return min(hits, key=_area) if hits else None


def _pick_action(element: Element, action: str) -> str | None:
    acts = element.actions
    if action == 'click':
        for candidate in ('invoke', 'toggle', 'select', 'expand'):
            if candidate in acts:
                return candidate
        return 'pixel' if element.rect[2] > element.rect[0] else None
    if action == 'expand' and 'expand' in acts and element.state.get('expanded') is True:
        return 'collapse'
    return action if action in acts else None


def _state_text(state: dict[str, Any]) -> str:
    return ', '.join(f'{k}={v}' for k, v in state.items()) or 'changed'


_KEY_ALIASES = {'control': 'ctrl', 'del': 'delete', 'esc': 'escape', 'return': 'enter'}


def _accelerator(elements: list[Element], mods: list[str], key: str) -> Element | None:
    want = '+'.join([_KEY_ALIASES.get(m, m) for m in mods] + [key]).lower()
    for e in elements:
        acc = e.accelerator.replace(' ', '').lower()
        if acc and acc == want and e.actions:
            return e
    return None


def fingerprint_of(title: str, elements: list[Element]) -> str:
    """Cheap change detector: title plus names, values and states."""
    h = hashlib.sha1(title.encode('utf-8', 'replace'))
    for e in elements:
        h.update(f'{e.role}|{e.name}|{e.value}|{sorted(e.state.items())}\n'.encode('utf-8', 'replace'))
    return h.hexdigest()


_driver: Driver | None = None
_driver_guard = threading.Lock()


def get_driver() -> Driver:
    global _driver
    if sys.platform != 'win32':
        raise Unsupported(UNSUPPORTED_MESSAGE)
    with _driver_guard:
        if _driver is None:
            from cognitrix.computer.windows import WindowsBackend
            _driver = Driver(WindowsBackend())
        return _driver
