"""macOS backend for background computer use. EXPERIMENTAL: written from the
platform documentation and not yet run on a Mac. It is off unless
``COGNITRIX_MAC_BACKGROUND=1`` is set (see ``get_driver``).

* The Accessibility API (``AXUIElement``) reads a window's controls and
  presses, toggles, selects and fills them, and runs menu-bar items, without
  the app coming to the front.
* ``CGWindowListCreateImage`` (or the ``screencapture`` tool) captures one
  window, also when it is covered.
* ``CGEventPostToPid`` sends clicks, keys and scrolling to one app's event
  queue, so the real pointer and the person's keyboard focus stay put.

Needs pyobjc (Quartz and ApplicationServices) and two permissions for the app
that runs Cognitrix: Accessibility, and Screen Recording for pictures.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import Any

from cognitrix.computer import Element, Unsupported, WindowInfo, fingerprint_of

logger = logging.getLogger('cognitrix.computer')

TERMINAL_APPS = frozenset({
    'terminal', 'iterm2', 'iterm', 'alacritty', 'kitty', 'wezterm', 'wezterm-gui', 'warp', 'hyper', 'tabby',
    'ghostty', 'rio', 'console',
})
PASSWORD_MANAGERS = frozenset({
    '1password', '1password 7', 'bitwarden', 'keepassxc', 'dashlane', 'lastpass', 'enpass', 'nordpass',
    'keeper password manager', 'proton pass', 'passwords', 'keychain access', 'strongbox', 'secrets',
})
SECURE_APPS = frozenset({'securityagent', 'loginwindow', 'coreautha', 'screensaverengine', 'system settings'})

_MAX_NODES = 500
_MAX_DEPTH = 40
_TEXT_LIMIT = 2000
_TOGGLE_ROLES = frozenset({'AXCheckBox', 'AXRadioButton', 'AXSwitch'})
_TEXT_ROLES = frozenset({'AXTextField', 'AXTextArea', 'AXComboBox', 'AXSearchField'})
_FILLER_ROLES = frozenset({
    'AXGroup', 'AXScrollArea', 'AXSplitGroup', 'AXLayoutArea', 'AXUnknown', 'AXToolbar', 'AXTabGroup',
    'AXMenuBar', 'AXWindow', 'AXList', 'AXOutline', 'AXTable', 'AXRow', 'AXColumn', 'AXSplitter',
})

_KEYCODES = {
    'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7, 'c': 8, 'v': 9, 'b': 11, 'q': 12, 'w': 13,
    'e': 14, 'r': 15, 'y': 16, 't': 17, '1': 18, '2': 19, '3': 20, '4': 21, '6': 22, '5': 23, '=': 24, '9': 25,
    '7': 26, '-': 27, '8': 28, '0': 29, ']': 30, 'o': 31, 'u': 32, '[': 33, 'i': 34, 'p': 35, 'l': 37, 'j': 38,
    "'": 39, 'k': 40, ';': 41, '\\': 42, ',': 43, '/': 44, 'n': 45, 'm': 46, '.': 47, '`': 50,
    'enter': 36, 'tab': 48, 'space': 49, 'backspace': 51, 'escape': 53, 'delete': 117, 'home': 115, 'end': 119,
    'pageup': 116, 'pagedown': 121, 'left': 123, 'right': 124, 'down': 125, 'up': 126,
    'f1': 122, 'f2': 120, 'f3': 99, 'f4': 118, 'f5': 96, 'f6': 97, 'f7': 98, 'f8': 100, 'f9': 101, 'f10': 109,
    'f11': 103, 'f12': 111,
}


def _menu_shortcut(char: Any, modifiers: Any) -> str:
    """AXMenuItemCmdChar + AXMenuItemCmdModifiers -> 'cmd+shift+s'."""
    if not char or not isinstance(char, str):
        return ''
    try:
        bits = int(modifiers or 0)
    except (TypeError, ValueError):
        bits = 0
    mods = []
    if bits & 4:
        mods.append('ctrl')
    if bits & 2:
        mods.append('alt')
    if not bits & 8:  # 8 means "no Command key"
        mods.append('cmd')
    if bits & 1:
        mods.append('shift')
    return '+'.join([*mods, char.lower()]) if mods else ''


class _Ref:
    __slots__ = ('ax', 'window_id', 'pid', 'in_menu')

    def __init__(self, ax: Any, window_id: int, pid: int, in_menu: bool = False):
        self.ax, self.window_id, self.pid, self.in_menu = ax, window_id, pid, in_menu


class MacBackend:
    def __init__(self) -> None:
        try:
            import AppKit
            import ApplicationServices as AS  # noqa: N814
            import Quartz
        except ImportError:
            raise Unsupported('Background computer use on macOS needs pyobjc '
                              '(pip install pyobjc-framework-Quartz pyobjc-framework-ApplicationServices).') from None
        self.AS, self.AppKit, self.Q = AS, AppKit, Quartz
        if not AS.AXIsProcessTrusted():
            raise Unsupported(
                'Background computer use needs the Accessibility permission. Turn it on for Cognitrix (or the '
                'terminal running it) in System Settings > Privacy & Security > Accessibility, then try again.')
        self._guard = threading.RLock()
        self._windows: dict[int, tuple[Any, int]] = {}  # window id -> (AX window, pid)
        self._get_window = None
        try:  # the usual way to tie an accessibility window to its window number
            lib = ctypes.cdll.LoadLibrary(
                '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
            lib._AXUIElementGetWindow.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            lib._AXUIElementGetWindow.restype = ctypes.c_int32
            self._get_window = lib._AXUIElementGetWindow
        except (OSError, AttributeError):
            pass

    # -- accessibility helpers -----------------------------------------
    def _attr(self, ax: Any, name: str, default: Any = None) -> Any:
        try:
            err, value = self.AS.AXUIElementCopyAttributeValue(ax, name, None)
        except Exception:
            return default
        return value if err == 0 and value is not None else default

    def _settable(self, ax: Any, name: str) -> bool:
        try:
            err, ok = self.AS.AXUIElementIsAttributeSettable(ax, name, None)
        except Exception:
            return False
        return err == 0 and bool(ok)

    def _actions(self, ax: Any) -> list[str]:
        try:
            err, names = self.AS.AXUIElementCopyActionNames(ax, None)
        except Exception:
            return []
        return [str(n) for n in names] if err == 0 and names else []

    def _rect(self, ax: Any) -> tuple[int, int, int, int]:
        pos, size = self._attr(ax, 'AXPosition'), self._attr(ax, 'AXSize')
        if pos is None or size is None:
            return (0, 0, 0, 0)
        try:
            ok1, point = self.AS.AXValueGetValue(pos, self.AS.kAXValueCGPointType, None)
            ok2, extent = self.AS.AXValueGetValue(size, self.AS.kAXValueCGSizeType, None)
        except Exception:
            return (0, 0, 0, 0)
        if not (ok1 and ok2) or extent.width <= 0 or extent.height <= 0:
            return (0, 0, 0, 0)
        return (int(point.x), int(point.y), int(point.x + extent.width), int(point.y + extent.height))

    def _window_number(self, ax: Any) -> int:
        if self._get_window is None:
            return 0
        try:
            import objc

            number = ctypes.c_uint32(0)
            if self._get_window(objc.pyobjc_id(ax), ctypes.byref(number)) == 0:
                return int(number.value)
        except Exception:
            logger.debug('computer-use: window number lookup failed', exc_info=True)
        return 0

    # -- windows --------------------------------------------------------
    def _blocked(self, app: str, pid: int) -> str | None:
        if pid == os.getpid() or app.startswith('cognitrix'):
            return "it is Cognitrix's own window"
        if app in TERMINAL_APPS:
            return 'it is a terminal; use the shell tool instead'
        if app in PASSWORD_MANAGERS:
            return 'it is a password manager'
        if app in SECURE_APPS:
            return 'it is a system security prompt'
        return None

    def list_windows(self) -> list[WindowInfo]:
        with self._guard:
            out: list[WindowInfo] = []
            seen: dict[int, tuple[Any, int]] = {}
            workspace = self.AppKit.NSWorkspace.sharedWorkspace()
            for running in workspace.runningApplications():
                if running.activationPolicy() != 0:  # regular apps with a Dock icon only
                    continue
                pid = int(running.processIdentifier())
                app = str(running.localizedName() or '').lower()
                ax_app = self.AS.AXUIElementCreateApplication(pid)
                try:
                    self.AS.AXUIElementSetMessagingTimeout(ax_app, 1.0)
                except Exception:
                    pass
                for ax in self._attr(ax_app, 'AXWindows', []) or []:
                    number = self._window_number(ax)
                    if not number or self._attr(ax, 'AXSubrole') in ('AXSystemDialog', 'AXUnknown'):
                        continue
                    seen[number] = (ax, pid)
                    out.append(WindowInfo(
                        number, str(self._attr(ax, 'AXTitle', '') or ''), app, pid,
                        bool(self._attr(ax, 'AXMinimized', False)), self._rect(ax), 'cocoa',
                        self._blocked(app, pid)))
            self._windows = seen
            return out

    def window(self, window_id: int) -> WindowInfo | None:
        for info in self.list_windows():
            if info.id == int(window_id):
                return info
        return None

    def _ax_window(self, window_id: int) -> tuple[Any, int]:
        found = self._windows.get(int(window_id))
        if found is None:
            self.list_windows()
            found = self._windows.get(int(window_id))
        if found is None:
            raise RuntimeError('the window is gone')
        return found

    def foreground(self) -> int:
        with self._guard:
            front = self.AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is None:
                return 0
            ax_app = self.AS.AXUIElementCreateApplication(int(front.processIdentifier()))
            focused = self._attr(ax_app, 'AXFocusedWindow')
            return self._window_number(focused) if focused is not None else 0

    def give_back_focus(self, window_id: int) -> bool:
        with self._guard:
            try:
                ax, pid = self._ax_window(window_id)
            except RuntimeError:
                return False
            self.AS.AXUIElementPerformAction(ax, 'AXRaise')
            app = self.AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is not None:
                app.activateWithOptions_(1 << 1)  # ignoring other apps
        for _ in range(10):
            if self.foreground() == int(window_id):
                return True
            time.sleep(0.05)
        return False

    def launch(self, target: str, args: str | None = None) -> None:
        import shlex

        target = os.path.expanduser(target.strip())
        # -g opens without bringing the app to the front.
        is_document = '://' in target or (os.path.exists(target) and not target.endswith('.app'))
        command = ['open', '-g', target] if is_document else ['open', '-g', '-a', target]
        if args:
            command += ['--args', *shlex.split(args)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)  # noqa: S603
        if result.returncode != 0:
            raise FileNotFoundError((result.stderr or '').strip() or f"Could not open '{target}'")

    def prepare(self, window_id: int) -> str:
        info = self.window(window_id)
        if info is not None and info.minimized:
            return ('The window is minimized, so there is no picture of it; its controls can still be read '
                    'and used.')
        return ''

    # -- pixels ---------------------------------------------------------
    def capture(self, window_id: int) -> tuple[Any, tuple[int, int], str]:
        from PIL import Image

        info = self.window(window_id)
        if info is None:
            return None, (0, 0), 'The window is gone.'
        origin = (info.rect[0], info.rect[1])
        size = (info.rect[2] - info.rect[0], info.rect[3] - info.rect[1])
        if info.minimized or size[0] <= 0 or size[1] <= 0:
            return None, origin, ''
        Q = self.Q  # noqa: N806
        preflight = getattr(Q, 'CGPreflightScreenCaptureAccess', None)
        if preflight is not None and not preflight():
            return None, origin, ('No picture: turn on Screen Recording for Cognitrix in System Settings > '
                                  'Privacy & Security. The controls below still work.')
        picture = None
        try:
            image = Q.CGWindowListCreateImage(
                Q.CGRectNull, Q.kCGWindowListOptionIncludingWindow, int(window_id),
                Q.kCGWindowImageBoundsIgnoreFraming | Q.kCGWindowImageNominalResolution)
            if image is not None:
                width, height = Q.CGImageGetWidth(image), Q.CGImageGetHeight(image)
                data = Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(image))
                picture = Image.frombuffer('RGBA', (width, height), bytes(data), 'raw', 'BGRA',
                                           Q.CGImageGetBytesPerRow(image), 1).convert('RGB')
        except Exception:
            logger.debug('computer-use: CGWindowListCreateImage failed', exc_info=True)
        if picture is None:  # newer systems: the command-line tool still captures one window
            with tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, 'window.png')
                subprocess.run(['screencapture', '-x', '-o', '-l', str(int(window_id)), path],  # noqa: S603, S607
                               capture_output=True, timeout=15, check=False)
                if os.path.exists(path):
                    with Image.open(path) as shot:
                        picture = shot.convert('RGB')
        if picture is None:
            return None, origin, 'The window could not be captured.'
        if picture.size != size:
            picture = picture.resize(size)  # Retina pictures are 2x; keep one pixel per point
        return picture, origin, ''

    # -- controls -------------------------------------------------------
    def _describe(self, ref: _Ref) -> Element | None:
        ax = ref.ax
        role = self._attr(ax, 'AXRole')
        if not role:
            return None
        role = str(role)
        subrole = str(self._attr(ax, 'AXSubrole', '') or '')
        name = self._attr(ax, 'AXTitle') or self._attr(ax, 'AXDescription') or ''
        raw = self._attr(ax, 'AXValue')
        names = self._actions(ax)
        actions: list[str] = []
        state: dict[str, Any] = {}
        value: str | None = None
        accelerator = ''
        secret = subrole == 'AXSecureTextField'
        if role in _TOGGLE_ROLES:
            state['checked'] = bool(raw) and str(raw) not in ('0', 'False')
            if 'AXPress' in names:
                actions.append('toggle')
        elif 'AXPress' in names or 'AXPick' in names or 'AXOpen' in names:
            actions.append('invoke')
        if role in _TEXT_ROLES or (isinstance(raw, str) and self._settable(ax, 'AXValue')):
            value = None if secret else str(raw or '')[:_TEXT_LIMIT]
            if self._settable(ax, 'AXValue'):
                actions.append('set_value')
        elif role == 'AXScrollBar':
            if isinstance(raw, (int, float)):
                state['position'] = round(float(raw), 3)
                if self._settable(ax, 'AXValue'):
                    actions.append('scroll')
        elif role in ('AXSlider', 'AXIncrementor', 'AXValueIndicator', 'AXProgressIndicator', 'AXLevelIndicator'):
            if isinstance(raw, (int, float)):
                value = f'{float(raw):.6g}'
                if self._settable(ax, 'AXValue'):
                    actions.append('set_value')
        elif role == 'AXStaticText' and isinstance(raw, str):
            name = name or raw[:200]
        if not ref.in_menu and self._settable(ax, 'AXSelected'):
            state['selected'] = bool(self._attr(ax, 'AXSelected', False))
            actions.append('select')
        expanded = self._attr(ax, 'AXExpanded') if role != 'AXMenuItem' else None
        if expanded is None and role == 'AXDisclosureTriangle':
            expanded = bool(raw)
        if expanded is not None:
            state['expanded'] = bool(expanded)
            actions.append('expand')
        if ref.in_menu:
            accelerator = _menu_shortcut(self._attr(ax, 'AXMenuItemCmdChar'), self._attr(ax, 'AXMenuItemCmdModifiers'))
            mark = self._attr(ax, 'AXMenuItemMarkChar')
            if mark:
                state['checked'] = True
        rect = (0, 0, 0, 0) if ref.in_menu else self._rect(ax)
        enabled = bool(self._attr(ax, 'AXEnabled', True))
        return Element(role.removeprefix('AX'), str(name), value, enabled, actions, rect, state, accelerator, ref)

    def _walk(self, root: Any, window_id: int, pid: int, in_menu: bool, out: list[Element]) -> None:
        stack: list[tuple[Any, int]] = [(root, 0)]
        visited = 0
        while stack and len(out) < _MAX_NODES and visited < _MAX_NODES * 6:
            ax, depth = stack.pop()
            visited += 1
            element = self._describe(_Ref(ax, window_id, pid, in_menu))
            if element is None:
                continue
            named = bool(element.name.strip())
            if depth and (element.actions or element.value not in (None, '')
                          or (named and 'AX' + element.role not in _FILLER_ROLES)):
                if not (in_menu and not named):  # separators
                    out.append(element)
            if depth < _MAX_DEPTH:
                children = self._attr(ax, 'AXChildren', []) or []
                for child in reversed(list(children)[:300]):
                    stack.append((child, depth + 1))

    def elements(self, window_id: int) -> list[Element]:
        with self._guard:
            try:
                ax, pid = self._ax_window(window_id)
            except RuntimeError:
                return []
            out: list[Element] = []
            self._walk(ax, int(window_id), pid, False, out)
            # The app's menu bar: its items run in the background and carry the shortcuts.
            menu_bar = self._attr(self.AS.AXUIElementCreateApplication(pid), 'AXMenuBar')
            if menu_bar is not None:
                self._walk(menu_bar, int(window_id), pid, True, out)
            return out

    def fingerprint(self, window_id: int) -> str:
        with self._guard:
            try:
                ax, pid = self._ax_window(window_id)
            except RuntimeError:
                return ''
            out: list[Element] = []
            self._walk(ax, int(window_id), pid, False, out)
            return fingerprint_of(str(self._attr(ax, 'AXTitle', '') or ''), out)

    def element_state(self, element: Element) -> Element | None:
        ref = element.ref
        if not isinstance(ref, _Ref):
            return None
        with self._guard:
            fresh = self._describe(ref)
            if fresh is not None and fresh.rect == (0, 0, 0, 0):
                fresh.rect = element.rect
            return fresh

    def focused_element(self, window_id: int) -> Element | None:
        with self._guard:
            try:
                ax_window, pid = self._ax_window(window_id)
            except RuntimeError:
                return None
            focused = self._attr(self.AS.AXUIElementCreateApplication(pid), 'AXFocusedUIElement')
            if focused is None:
                return None
            owner = self._attr(focused, 'AXWindow')
            if owner is not None and self._window_number(owner) not in (0, int(window_id)):
                return None
            element = self._describe(_Ref(focused, int(window_id), pid))
            return element if element is not None and 'set_value' in element.actions else None

    def perform(self, element: Element, action: str, value: str | None = None) -> None:
        ref = element.ref
        if not isinstance(ref, _Ref):
            raise RuntimeError('no accessibility handle')
        AS, ax = self.AS, ref.ax  # noqa: N806
        with self._guard:
            if action in ('invoke', 'toggle'):
                names = self._actions(ax)
                chosen = next((n for n in ('AXPress', 'AXPick', 'AXOpen') if n in names), 'AXPress')
                err = AS.AXUIElementPerformAction(ax, chosen)
            elif action == 'select':
                err = AS.AXUIElementSetAttributeValue(ax, 'AXSelected', True)
            elif action in ('expand', 'collapse'):
                if self._settable(ax, 'AXExpanded'):
                    err = AS.AXUIElementSetAttributeValue(ax, 'AXExpanded', action == 'expand')
                else:
                    err = AS.AXUIElementPerformAction(ax, 'AXPress')
            elif action == 'set_value':
                current = self._attr(ax, 'AXValue')
                new: Any = float(value or 0) if isinstance(current, (int, float)) else (value or '')
                err = AS.AXUIElementSetAttributeValue(ax, 'AXValue', new)
            elif action.startswith('scroll_'):
                direction = action.split('_', 1)[1]
                vertical = 'Vertical' in str(self._attr(ax, 'AXOrientation', 'AXVerticalOrientation'))
                if vertical != (direction in ('up', 'down')):
                    raise RuntimeError('that scroll bar moves the other way')
                now = float(self._attr(ax, 'AXValue', 0.0) or 0.0)
                step = 0.1 * max(1, int(value or 1)) * (1 if direction in ('down', 'right') else -1)
                err = AS.AXUIElementSetAttributeValue(ax, 'AXValue', min(1.0, max(0.0, now + step)))
            else:
                raise ValueError(f'unknown action {action}')
            if err != 0:
                raise RuntimeError(f'the app refused {action} (error {err})')

    # -- sent input -----------------------------------------------------
    def _pid(self, window_id: int) -> int:
        return self._ax_window(window_id)[1]

    def post_click(self, window_id: int, x: int, y: int, button: str, double: bool) -> None:
        Q = self.Q  # noqa: N806
        down, up, which = {
            'right': (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight),
            'middle': (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp, Q.kCGMouseButtonCenter),
        }.get(button, (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft))
        with self._guard:
            pid = self._pid(window_id)
            for click in range(1, 3 if double else 2):
                for kind in (down, up):
                    event = Q.CGEventCreateMouseEvent(None, kind, (float(x), float(y)), which)
                    Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, click)
                    # Tell the app which of its windows the click is for.
                    for name in ('kCGMouseEventWindowUnderMousePointer',
                                 'kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent'):
                        field = getattr(Q, name, None)
                        if field is not None:
                            Q.CGEventSetIntegerValueField(event, field, int(window_id))
                    Q.CGEventPostToPid(pid, event)
                    time.sleep(0.02)

    def post_scroll(self, window_id: int, x: int, y: int, amount: int, horizontal: bool = False) -> None:
        Q = self.Q  # noqa: N806
        with self._guard:
            lines = int(amount)
            event = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 2,
                                                    0 if horizontal else lines, -lines if horizontal else 0)
            Q.CGEventSetLocation(event, (float(x), float(y)))
            Q.CGEventPostToPid(self._pid(window_id), event)

    def post_key(self, window_id: int, key: str, mods: tuple[str, ...] = ()) -> None:
        Q = self.Q  # noqa: N806
        code = _KEYCODES.get(key.lower())
        if code is None:
            raise ValueError(f"unknown key '{key}'")
        masks = {'shift': Q.kCGEventFlagMaskShift, 'ctrl': Q.kCGEventFlagMaskControl,
                 'alt': Q.kCGEventFlagMaskAlternate, 'option': Q.kCGEventFlagMaskAlternate,
                 'cmd': Q.kCGEventFlagMaskCommand, 'command': Q.kCGEventFlagMaskCommand,
                 'win': Q.kCGEventFlagMaskCommand, 'meta': Q.kCGEventFlagMaskCommand}
        flags = 0
        for mod in mods:
            if mod not in masks:
                raise ValueError(f"unknown modifier '{mod}'")
            flags |= masks[mod]
        with self._guard:
            pid = self._pid(window_id)
            for pressed in (True, False):
                event = Q.CGEventCreateKeyboardEvent(None, code, pressed)
                Q.CGEventSetFlags(event, flags)
                Q.CGEventPostToPid(pid, event)
                time.sleep(0.01)

    def post_text(self, window_id: int, text: str) -> None:
        Q = self.Q  # noqa: N806
        with self._guard:
            pid = self._pid(window_id)
            for start in range(0, len(text), 16):
                chunk = text[start:start + 16]
                for pressed in (True, False):
                    event = Q.CGEventCreateKeyboardEvent(None, 0, pressed)
                    Q.CGEventSetFlags(event, 0)
                    Q.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
                    Q.CGEventPostToPid(pid, event)
                time.sleep(0.01)
