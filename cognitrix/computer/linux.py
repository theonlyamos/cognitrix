"""Linux backend for background computer use (X11 sessions).

Two system libraries, both loaded with ctypes so nothing extra is installed:

* ``libatspi`` (AT-SPI, the Linux accessibility bus) reads a window's controls
  and presses, toggles, selects and fills them without the window being in
  front. This is the reliable path and what GTK and Qt apps answer to.
* ``libX11`` lists windows, captures one window's pixels (through the
  Composite extension, so a covered window still reads correctly) and sends
  clicks and keys to one window with ``XSendEvent``. Modern toolkits (GTK 3+,
  Qt 5+, Chromium) ignore those sent events, so the driver's read-back reports
  them as unverified or failed; older toolkits accept them.

Wayland has no way to list, capture or send input to another app's window, so
it is not supported.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from ctypes import POINTER, byref, c_char_p, c_double, c_int, c_long, c_uint, c_ulong, c_void_p
from collections.abc import Callable
from typing import Any

from cognitrix.computer import Element, Unsupported, WindowInfo, blocked_reason, fingerprint_of

logger = logging.getLogger('cognitrix.computer')

TERMINAL_APPS = frozenset({
    'xterm', 'uxterm', 'gnome-terminal', 'gnome-terminal-server', 'konsole', 'xfce4-terminal', 'alacritty',
    'kitty', 'wezterm', 'wezterm-gui', 'tilix', 'terminator', 'urxvt', 'rxvt', 'st', 'foot', 'lxterminal',
    'mate-terminal', 'qterminal', 'kgx', 'ptyxis', 'ghostty', 'guake', 'yakuake', 'tabby', 'hyper', 'warp',
    'terminology', 'cool-retro-term', 'deepin-terminal', 'io.elementary.terminal',
})
PASSWORD_MANAGERS = frozenset({
    '1password', 'bitwarden', 'keepassxc', 'keepass2', 'seahorse', 'enpass', 'nordpass', 'dashlane',
    'lastpass', 'proton-pass', 'secrets', 'kwalletmanager5', 'kwalletmanager', 'buttercup',
})
SECURE_APPS = frozenset({
    'polkit-gnome-authentication-agent-1', 'polkit-kde-authentication-agent-1', 'polkit-mate-authentication-agent-1',
    'lxpolkit', 'gcr-prompter', 'pinentry', 'pinentry-gtk-2', 'pinentry-gnome3', 'pinentry-qt', 'ssh-askpass',
    'ksshaskpass', 'gnome-screensaver-dialog', 'xscreensaver', 'i3lock', 'pkexec', 'gksu', 'kdesu',
})

_MAX_NODES = 500
_MAX_DEPTH = 40
_TEXT_LIMIT = 2000

# AtspiStateType
_ST_CHECKED, _ST_DEFUNCT, _ST_EDITABLE, _ST_ENABLED, _ST_EXPANDABLE, _ST_EXPANDED = 4, 6, 7, 8, 9, 10
_ST_FOCUSABLE, _ST_FOCUSED, _ST_HORIZONTAL, _ST_PRESSED, _ST_SELECTABLE, _ST_SELECTED = 11, 12, 14, 20, 22, 23
_ST_SENSITIVE, _ST_SHOWING, _ST_VISIBLE = 24, 25, 30
# AtspiCache: parent, role, interfaces and states. Nothing here runs a GLib main
# loop, so cached names and children would never be refreshed and are left out.
# States must stay in: libatspi reads them while handling the app's own cache
# messages, and an uncached read at that point deadlocks. They are re-read by
# clearing the object's cache before each use (see _Atspi.states).
_CACHE_MASK = 1 | 16 | 32 | 64

_CLICK_ACTIONS = ('click', 'press', 'activate', 'jump', 'open', 'toggle', 'invoke', 'do default', 'dodefault')
_EXPAND_ACTIONS = ('expand or contract', 'expand', 'collapse', 'showmenu', 'menu')
_TOGGLE_ROLES = frozenset({
    'check box', 'toggle button', 'radio button', 'check menu item', 'radio menu item', 'switch',
})
_WINDOW_ROLES = frozenset({'frame', 'dialog', 'window', 'alert', 'file chooser', 'color chooser', 'font chooser'})
_MENU_ROLES = frozenset({'menu bar', 'menu', 'menu item', 'check menu item', 'radio menu item', 'tearoff menu item'})
_FILLER_ROLES = frozenset({
    'filler', 'panel', 'scroll pane', 'viewport', 'split pane', 'layered pane', 'root pane', 'glass pane',
    'section', 'redundant object', 'unknown', 'separator', 'page tab list', 'tool bar', 'menu bar',
    'application', 'frame', 'window', 'grouping', 'box', 'form',
})
_VALUE_ROLES = frozenset({'slider', 'spin button', 'progress bar', 'level bar', 'scroll bar'})

_KEYSYMS = {
    'enter': 'Return', 'tab': 'Tab', 'escape': 'Escape', 'backspace': 'BackSpace', 'delete': 'Delete',
    'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right', 'home': 'Home', 'end': 'End',
    'pageup': 'Prior', 'pagedown': 'Next', 'space': 'space', 'insert': 'Insert', 'menu': 'Menu',
}
_MOD_MASKS = {'shift': 1, 'ctrl': 4, 'alt': 8, 'win': 64, 'super': 64, 'meta': 64, 'cmd': 64}
_MOD_KEYSYMS = {'shift': b'Shift_L', 'ctrl': b'Control_L', 'alt': b'Alt_L', 'win': b'Super_L',
                'super': b'Super_L', 'meta': b'Super_L', 'cmd': b'Super_L'}


# ------------------------------------------------------------------ Xlib

class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ('x', c_int), ('y', c_int), ('width', c_int), ('height', c_int), ('border_width', c_int),
        ('depth', c_int), ('visual', c_void_p), ('root', c_ulong), ('c_class', c_int),
        ('bit_gravity', c_int), ('win_gravity', c_int), ('backing_store', c_int),
        ('backing_planes', c_ulong), ('backing_pixel', c_ulong), ('save_under', c_int),
        ('colormap', c_ulong), ('map_installed', c_int), ('map_state', c_int),
        ('all_event_masks', c_long), ('your_event_mask', c_long), ('do_not_propagate_mask', c_long),
        ('override_redirect', c_int), ('screen', c_void_p),
    ]


class _XImage(ctypes.Structure):
    _fields_ = [
        ('width', c_int), ('height', c_int), ('xoffset', c_int), ('format', c_int), ('data', c_void_p),
        ('byte_order', c_int), ('bitmap_unit', c_int), ('bitmap_bit_order', c_int), ('bitmap_pad', c_int),
        ('depth', c_int), ('bytes_per_line', c_int), ('bits_per_pixel', c_int),
    ]


class _XInputEvent(ctypes.Structure):
    """XKeyEvent and XButtonEvent share this layout (detail = keycode or button)."""
    _fields_ = [
        ('type', c_int), ('serial', c_ulong), ('send_event', c_int), ('display', c_void_p),
        ('window', c_ulong), ('root', c_ulong), ('subwindow', c_ulong), ('time', c_ulong),
        ('x', c_int), ('y', c_int), ('x_root', c_int), ('y_root', c_int),
        ('state', c_uint), ('detail', c_uint), ('same_screen', c_int),
    ]


class _XClientMessage(ctypes.Structure):
    _fields_ = [
        ('type', c_int), ('serial', c_ulong), ('send_event', c_int), ('display', c_void_p),
        ('window', c_ulong), ('message_type', c_ulong), ('format', c_int), ('data', c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [('input', _XInputEvent), ('client', _XClientMessage), ('pad', c_long * 24)]


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [('window', c_ulong), ('state', c_int), ('kind', c_int), ('til_or_since', c_ulong),
                ('idle', c_ulong), ('event_mask', c_ulong)]


_ERROR_HANDLER = ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)


def _load(name: str) -> ctypes.CDLL:
    path = ctypes.util.find_library(name)
    if not path:
        raise OSError(f'lib{name} is not installed')
    return ctypes.CDLL(path)


class _X:
    """The few Xlib calls the backend needs."""

    def __init__(self) -> None:
        self.lib = lib = _load('X11')
        sig = self._sig
        sig('XInitThreads', c_int)
        sig('XOpenDisplay', c_void_p, c_char_p)
        sig('XDefaultRootWindow', c_ulong, c_void_p)
        sig('XInternAtom', c_ulong, c_void_p, c_char_p, c_int)
        sig('XGetWindowProperty', c_int, c_void_p, c_ulong, c_ulong, c_long, c_long, c_int, c_ulong,
            POINTER(c_ulong), POINTER(c_int), POINTER(c_ulong), POINTER(c_ulong), POINTER(c_void_p))
        sig('XFree', c_int, c_void_p)
        sig('XGetWindowAttributes', c_int, c_void_p, c_ulong, POINTER(_XWindowAttributes))
        sig('XTranslateCoordinates', c_int, c_void_p, c_ulong, c_ulong, c_int, c_int,
            POINTER(c_int), POINTER(c_int), POINTER(c_ulong))
        sig('XQueryTree', c_int, c_void_p, c_ulong, POINTER(c_ulong), POINTER(c_ulong),
            POINTER(c_void_p), POINTER(c_uint))
        sig('XGetImage', POINTER(_XImage), c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_ulong, c_int)
        sig('XDestroyImage', c_int, POINTER(_XImage))
        sig('XFreePixmap', c_int, c_void_p, c_ulong)
        sig('XSendEvent', c_int, c_void_p, c_ulong, c_int, c_long, POINTER(_XEvent))
        sig('XSync', c_int, c_void_p, c_int)
        sig('XFlush', c_int, c_void_p)
        sig('XStringToKeysym', c_ulong, c_char_p)
        sig('XKeysymToKeycode', c_uint, c_void_p, c_ulong)
        sig('XkbKeycodeToKeysym', c_ulong, c_void_p, c_uint, c_int, c_int)
        sig('XSetErrorHandler', c_void_p, _ERROR_HANDLER)
        sig('XQueryPointer', c_int, c_void_p, c_ulong, POINTER(c_ulong), POINTER(c_ulong), POINTER(c_int),
            POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_uint))
        sig('XRaiseWindow', c_int, c_void_p, c_ulong)
        sig('XDisplayKeycodes', c_int, c_void_p, POINTER(c_int), POINTER(c_int))
        sig('XGetKeyboardMapping', POINTER(c_ulong), c_void_p, ctypes.c_ubyte, c_int, POINTER(c_int))
        sig('XChangeKeyboardMapping', c_int, c_void_p, c_int, c_int, POINTER(c_ulong), c_int)
        lib.XInitThreads()
        self.dpy = lib.XOpenDisplay(None)
        if not self.dpy:
            raise OSError('could not connect to the X display')
        self.root = lib.XDefaultRootWindow(self.dpy)
        self.errors = 0
        self._handler = _ERROR_HANDLER(self._on_error)  # keep a reference
        lib.XSetErrorHandler(self._handler)
        self._atoms: dict[str, int] = {}
        self.composite = None
        try:
            comp = _load('Xcomposite')
            comp.XCompositeQueryExtension.argtypes = [c_void_p, POINTER(c_int), POINTER(c_int)]
            comp.XCompositeRedirectWindow.argtypes = [c_void_p, c_ulong, c_int]
            comp.XCompositeNameWindowPixmap.argtypes = [c_void_p, c_ulong]
            comp.XCompositeNameWindowPixmap.restype = c_ulong
            a, b = c_int(), c_int()
            if comp.XCompositeQueryExtension(self.dpy, byref(a), byref(b)):
                self.composite = comp
        except (OSError, AttributeError):
            pass
        # Real input (use_screen). Optional: without it only background input works.
        self.xtest = None
        try:
            xtest = _load('Xtst')
            xtest.XTestFakeMotionEvent.argtypes = [c_void_p, c_int, c_int, c_int, c_ulong]
            xtest.XTestFakeButtonEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
            xtest.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
            a, b, c, d = c_int(), c_int(), c_int(), c_int()
            xtest.XTestQueryExtension.argtypes = [c_void_p] + [POINTER(c_int)] * 4
            if xtest.XTestQueryExtension(self.dpy, byref(a), byref(b), byref(c), byref(d)):
                self.xtest = xtest
        except (OSError, AttributeError):
            pass
        self.xss = None
        try:
            xss = _load('Xss')
            xss.XScreenSaverAllocInfo.restype = c_void_p
            xss.XScreenSaverQueryInfo.argtypes = [c_void_p, c_ulong, c_void_p]
            self.xss_info = xss.XScreenSaverAllocInfo()
            if self.xss_info:
                self.xss = xss
        except (OSError, AttributeError):
            pass

    def _sig(self, name: str, restype: Any, *argtypes: Any) -> None:
        fn = getattr(self.lib, name)
        fn.restype, fn.argtypes = restype, list(argtypes)

    def _on_error(self, _dpy: Any, _event: Any) -> int:
        # A window can close between listing it and asking about it.
        self.errors += 1
        return 0

    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.lib.XInternAtom(self.dpy, name.encode(), 0)
        return self._atoms[name]

    def prop(self, window: int, name: str) -> tuple[int, bytes | list[int]] | None:
        """(format, value): bytes for 8-bit properties, a list of ints for 32-bit."""
        kind, fmt, count, after, data = c_ulong(), c_int(), c_ulong(), c_ulong(), c_void_p()
        status = self.lib.XGetWindowProperty(self.dpy, window, self.atom(name), 0, 1 << 20, 0, 0,
                                             byref(kind), byref(fmt), byref(count), byref(after), byref(data))
        if status != 0 or not data.value:
            return None
        try:
            if fmt.value == 8:
                return 8, ctypes.string_at(data.value, count.value)
            if fmt.value == 32:  # delivered as C longs
                return 32, list(ctypes.cast(data.value, POINTER(c_ulong))[:count.value])
            return None
        finally:
            self.lib.XFree(data)

    def text_prop(self, window: int, *names: str) -> str:
        for name in names:
            got = self.prop(window, name)
            if got and got[0] == 8 and got[1]:
                return bytes(got[1]).split(b'\0')[0].decode('utf-8', 'replace')
        return ''

    def attrs(self, window: int) -> _XWindowAttributes | None:
        attrs = _XWindowAttributes()
        before = self.errors
        ok = self.lib.XGetWindowAttributes(self.dpy, window, byref(attrs))
        return attrs if ok and self.errors == before else None

    def to_root(self, window: int, x: int = 0, y: int = 0) -> tuple[int, int]:
        rx, ry, child = c_int(), c_int(), c_ulong()
        self.lib.XTranslateCoordinates(self.dpy, window, self.root, x, y, byref(rx), byref(ry), byref(child))
        return rx.value, ry.value

    def children(self, window: int) -> list[int]:
        root, parent, kids, count = c_ulong(), c_ulong(), c_void_p(), c_uint()
        if not self.lib.XQueryTree(self.dpy, window, byref(root), byref(parent), byref(kids), byref(count)):
            return []
        try:
            return list(ctypes.cast(kids.value, POINTER(c_ulong))[:count.value]) if kids.value else []
        finally:
            if kids.value:
                self.lib.XFree(kids)

    def deepest(self, window: int, root_x: int, root_y: int) -> tuple[int, int, int]:
        """The innermost sub-window under a screen point, and the point inside it."""
        current = window
        for _ in range(32):
            x, y, child = c_int(), c_int(), c_ulong()
            self.lib.XTranslateCoordinates(self.dpy, self.root, current, root_x, root_y,
                                           byref(x), byref(y), byref(child))
            if not child.value or child.value == current:
                return current, x.value, y.value
            current = child.value
        return current, 0, 0


# ---------------------------------------------------------------- AT-SPI

class _Acc:
    """One accessible object; drops its reference when Python is done with it."""
    __slots__ = ('ptr', '_unref')

    def __init__(self, ptr: int, unref: Any):
        self.ptr, self._unref = ptr, unref

    def __del__(self) -> None:
        try:
            self._unref(self.ptr)
        except Exception:
            pass


class _Ref:
    """What an Element keeps to find its control again."""
    __slots__ = ('acc', 'window_id', 'click', 'expand', 'in_menu', 'states')

    def __init__(self, acc: _Acc, window_id: int, in_menu: bool = False):
        self.acc, self.window_id, self.in_menu = acc, window_id, in_menu
        self.click: int | None = None
        self.expand: int | None = None
        self.states: set[int] = set()


class _Atspi:
    def __init__(self) -> None:
        self.lib = lib = _load('atspi')
        self.glib = _load('glib-2.0')
        self.gobject = _load('gobject-2.0')
        self.gobject.g_object_unref.argtypes = [c_void_p]
        self.glib.g_free.argtypes = [c_void_p]
        self.glib.g_main_context_iteration.argtypes = [c_void_p, c_int]
        P, I, E = c_void_p, c_int, c_void_p  # noqa: E741, N806

        def sig(name: str, restype: Any, *argtypes: Any) -> None:
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = restype, list(argtypes)

        sig('atspi_init', I)
        sig('atspi_get_desktop', P, I)
        sig('atspi_accessible_set_cache_mask', None, P, I)
        self._clear = 'atspi_accessible_clear_cache_single' if hasattr(lib, 'atspi_accessible_clear_cache_single') \
            else 'atspi_accessible_clear_cache'
        sig(self._clear, None, P)
        sig('atspi_accessible_get_child_count', I, P, E)
        sig('atspi_accessible_get_child_at_index', P, P, I, E)
        sig('atspi_accessible_get_parent', P, P, E)
        sig('atspi_accessible_get_index_in_parent', I, P, E)
        sig('atspi_accessible_get_name', P, P, E)
        sig('atspi_accessible_get_role_name', P, P, E)
        sig('atspi_accessible_get_process_id', c_uint, P, E)
        sig('atspi_accessible_get_state_set', P, P)
        sig('atspi_state_set_contains', I, P, I)
        for iface in ('component', 'action', 'text', 'editable_text', 'value', 'selection'):
            sig(f'atspi_accessible_get_{iface}_iface', P, P)
        sig('atspi_component_get_extents', P, P, I, E)
        sig('atspi_component_grab_focus', I, P, E)
        sig('atspi_action_get_n_actions', I, P, E)
        self._action_name = 'atspi_action_get_action_name' if hasattr(lib, 'atspi_action_get_action_name') \
            else 'atspi_action_get_name'
        sig(self._action_name, P, P, I, E)
        sig('atspi_action_get_key_binding', P, P, I, E)
        sig('atspi_action_do_action', I, P, I, E)
        sig('atspi_text_get_character_count', I, P, E)
        sig('atspi_text_get_caret_offset', I, P, E)
        sig('atspi_text_get_text', P, P, I, I, E)
        sig('atspi_editable_text_set_text_contents', I, P, c_char_p, E)
        sig('atspi_editable_text_insert_text', I, P, I, c_char_p, I, E)
        sig('atspi_value_get_current_value', c_double, P, E)
        sig('atspi_value_get_minimum_value', c_double, P, E)
        sig('atspi_value_get_maximum_value', c_double, P, E)
        sig('atspi_value_get_minimum_increment', c_double, P, E)
        sig('atspi_value_set_current_value', I, P, c_double, E)
        sig('atspi_selection_select_child', I, P, I, E)
        if lib.atspi_init() not in (0, 1):
            raise OSError('AT-SPI did not start')
        desktop = lib.atspi_get_desktop(0)
        if not desktop:
            raise OSError('no AT-SPI desktop')
        self.desktop = self.wrap(desktop)
        lib.atspi_accessible_set_cache_mask(desktop, _CACHE_MASK)

    def wrap(self, ptr: int | None) -> _Acc | None:
        return _Acc(ptr, self.gobject.g_object_unref) if ptr else None

    def pump(self) -> None:
        """Let queued accessibility-bus messages (apps coming and going) through."""
        for _ in range(200):
            if not self.glib.g_main_context_iteration(None, 0):
                break

    def string(self, ptr: int | None) -> str:
        if not ptr:
            return ''
        try:
            return ctypes.string_at(ptr).decode('utf-8', 'replace')
        finally:
            self.glib.g_free(ptr)

    def has(self, acc: _Acc, iface: str) -> bool:
        ptr = getattr(self.lib, f'atspi_accessible_get_{iface}_iface')(acc.ptr)
        if ptr:
            self.gobject.g_object_unref(ptr)
        return bool(ptr)

    def children(self, acc: _Acc, limit: int = 2000) -> list[_Acc]:
        count = self.lib.atspi_accessible_get_child_count(acc.ptr, None)
        out = []
        for i in range(max(0, min(count, limit))):
            child = self.wrap(self.lib.atspi_accessible_get_child_at_index(acc.ptr, i, None))
            if child is not None:
                out.append(child)
        return out

    def name(self, acc: _Acc) -> str:
        return self.string(self.lib.atspi_accessible_get_name(acc.ptr, None))

    def role(self, acc: _Acc) -> str:
        return self.string(self.lib.atspi_accessible_get_role_name(acc.ptr, None))

    def states(self, acc: _Acc) -> set[int]:
        """The object's states, read fresh from the app."""
        getattr(self.lib, self._clear)(acc.ptr)
        ptr = self.lib.atspi_accessible_get_state_set(acc.ptr)
        if not ptr:
            return {_ST_DEFUNCT}
        try:
            return {s for s in (_ST_CHECKED, _ST_DEFUNCT, _ST_EDITABLE, _ST_ENABLED, _ST_EXPANDABLE, _ST_EXPANDED,
                                _ST_FOCUSABLE, _ST_FOCUSED, _ST_HORIZONTAL, _ST_PRESSED, _ST_SELECTABLE,
                                _ST_SELECTED, _ST_SENSITIVE, _ST_SHOWING, _ST_VISIBLE)
                    if self.lib.atspi_state_set_contains(ptr, s)}
        finally:
            self.gobject.g_object_unref(ptr)

    def extents(self, acc: _Acc) -> tuple[int, int, int, int]:
        ptr = self.lib.atspi_component_get_extents(acc.ptr, 0, None)  # 0 = screen coordinates
        if not ptr:
            return (0, 0, 0, 0)
        try:
            x, y, w, h = ctypes.cast(ptr, POINTER(c_int))[:4]
        finally:
            self.glib.g_free(ptr)
        if w <= 0 or h <= 0 or min(x, y) < -100000:  # not laid out (e.g. inside a closed menu)
            return (0, 0, 0, 0)
        return (x, y, x + w, y + h)

    def actions(self, acc: _Acc) -> list[tuple[str, str]]:
        """(name, key binding) per action."""
        out = []
        for i in range(max(0, min(self.lib.atspi_action_get_n_actions(acc.ptr, None), 8))):
            name = self.string(getattr(self.lib, self._action_name)(acc.ptr, i, None)).strip().lower()
            out.append((name, self.string(self.lib.atspi_action_get_key_binding(acc.ptr, i, None))))
        return out

    def text(self, acc: _Acc) -> str:
        count = self.lib.atspi_text_get_character_count(acc.ptr, None)
        if count <= 0:
            return ''
        return self.string(self.lib.atspi_text_get_text(acc.ptr, 0, min(count, _TEXT_LIMIT), None))


def _accelerator(binding: str) -> str:
    """'s;<Alt>f:s;<Control>s' -> 'ctrl+s' (the last part is the shortcut)."""
    parts = [p for p in binding.split(';') if p.strip()]
    for part in reversed(parts):
        mods = re.findall(r'<([^>]+)>', part)
        key = re.sub(r'<[^>]+>', '', part).strip()
        if not mods or not key or ':' in part:
            continue
        names = {'control': 'ctrl', 'primary': 'ctrl', 'ctrl': 'ctrl', 'shift': 'shift', 'alt': 'alt',
                 'mod1': 'alt', 'super': 'win', 'meta': 'win'}
        out = [names.get(m.lower(), m.lower()) for m in mods]
        key = {'return': 'enter', 'page_up': 'pageup', 'page_down': 'pagedown'}.get(key.lower(), key.lower())
        return '+'.join([*out, key])
    return ''


def _number(value: float) -> str:
    return f'{value:.6g}'


# --------------------------------------------------------------- backend

class LinuxBackend:
    """See the module docstring. One lock guards both libraries."""

    def __init__(self) -> None:
        session = os.environ.get('XDG_SESSION_TYPE', '').lower()
        if session == 'wayland' or (os.environ.get('WAYLAND_DISPLAY') and session != 'x11'):
            raise Unsupported(
                "Background computer use needs an X11 desktop session; Wayland doesn't let one app read or "
                "control another app's window. Use take_screenshot, mouse_click and text_input instead.")
        if not os.environ.get('DISPLAY'):
            raise Unsupported('Background computer use needs a desktop session (no display found).')
        try:
            self.x = _X()
        except OSError as exc:
            raise Unsupported(f'Background computer use is unavailable here: {exc}.') from None
        self.atspi: _Atspi | None = None
        self.atspi_error = ''
        try:
            self.atspi = _Atspi()
        except (OSError, AttributeError) as exc:
            self.atspi_error = str(exc)
            logger.info('computer-use: AT-SPI unavailable: %s', exc)
        self._guard = threading.RLock()
        self._redirected: set[int] = set()
        self._focus: dict[int, Element] = {}  # the control we last gave focus to, per window
        self._last: dict[int, list[Element]] = {}
        self._uid = os.getuid()

    # -- windows --------------------------------------------------------
    def _client_windows(self) -> list[int]:
        got = self.x.prop(self.x.root, '_NET_CLIENT_LIST')
        if got and got[0] == 32:
            return [int(w) for w in got[1]]
        # No window manager list: top-level windows that carry a WM_STATE.
        found = []
        for top in self.x.children(self.x.root):
            for candidate in (top, *self.x.children(top)):
                if self.x.prop(candidate, 'WM_STATE'):
                    found.append(candidate)
                    break
        return found

    def _info(self, window: int) -> WindowInfo | None:
        attrs = self.x.attrs(window)
        if attrs is None or attrs.override_redirect:
            return None
        kinds = self.x.prop(window, '_NET_WM_WINDOW_TYPE')
        if kinds and kinds[0] == 32 and {self.x.atom('_NET_WM_WINDOW_TYPE_DESKTOP'),
                                         self.x.atom('_NET_WM_WINDOW_TYPE_DOCK')} & set(kinds[1]):
            return None
        title = self.x.text_prop(window, '_NET_WM_NAME', 'WM_NAME')
        pid_prop = self.x.prop(window, '_NET_WM_PID')
        pid = int(pid_prop[1][0]) if pid_prop and pid_prop[0] == 32 and pid_prop[1] else 0
        app, other_user = '', False
        if pid:
            try:
                app = os.path.basename(os.readlink(f'/proc/{pid}/exe')).replace(' (deleted)', '')
            except OSError:
                other_user = True  # another user's process (e.g. root) can't be inspected
            try:
                other_user = other_user or os.stat(f'/proc/{pid}').st_uid != self._uid
            except OSError:
                pass
        if not app:
            wm_class = self.x.prop(window, 'WM_CLASS')
            names = bytes(wm_class[1]).split(b'\0') if wm_class and wm_class[0] == 8 else []
            app = (names[1] if len(names) > 1 and names[1] else names[0] if names else b'').decode('utf-8', 'replace')
        app = app.lower()
        state = self.x.prop(window, '_NET_WM_STATE')
        hidden = bool(state and state[0] == 32 and self.x.atom('_NET_WM_STATE_HIDDEN') in state[1])
        minimized = hidden or attrs.map_state != 2  # 2 = IsViewable
        left, top = self.x.to_root(window)
        rect = (left, top, left + attrs.width, top + attrs.height)
        blocked = self._blocked(app, title, pid, other_user)
        return WindowInfo(int(window), title, app, pid, minimized, rect, 'x11', blocked)

    def _blocked(self, app: str, title: str, pid: int, other_user: bool) -> str | None:
        if pid == os.getpid() or app.startswith('cognitrix'):
            return blocked_reason(app, own=True)
        if app in TERMINAL_APPS or app.endswith('-terminal'):
            return 'it is a terminal; use the shell tool instead'
        if app in PASSWORD_MANAGERS:
            return 'it is a password manager'
        if app in SECURE_APPS or app.startswith('pinentry'):
            return 'it is a system security prompt'
        if other_user:
            return 'it runs as another user (for example as administrator)'
        return None

    def list_windows(self) -> list[WindowInfo]:
        with self._guard:
            out = [info for w in self._client_windows() if (info := self._info(w)) is not None]
            self.x.lib.XSync(self.x.dpy, 0)
            return [w for w in out if w.title or w.app]

    def window(self, window_id: int) -> WindowInfo | None:
        with self._guard:
            if int(window_id) not in self._client_windows():
                return None
            return self._info(int(window_id))

    def foreground(self) -> int:
        with self._guard:
            got = self.x.prop(self.x.root, '_NET_ACTIVE_WINDOW')
            return int(got[1][0]) if got and got[0] == 32 and got[1] else 0

    def launch(self, target: str, args: str | None = None) -> None:
        target = os.path.expanduser(target.strip())
        if re.match(r'^[a-z][a-z0-9+.\-]*://', target, re.I) or (
                os.path.exists(target) and not (os.path.isfile(target) and os.access(target, os.X_OK))):
            opener = shutil.which('xdg-open') or shutil.which('gio')
            if not opener:
                raise RuntimeError('xdg-open is not installed, so files and links cannot be opened')
            command = [opener, target] if opener.endswith('xdg-open') else [opener, 'open', target]
        else:
            command = [target, *shlex.split(args or '')]
            if not (os.path.sep in target or shutil.which(target)):
                launcher = shutil.which('gtk-launch')
                if not launcher:
                    raise FileNotFoundError(f"No program called '{target}' was found")
                command = [launcher, target]
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,  # noqa: S603
                         stderr=subprocess.DEVNULL, start_new_session=True)

    def prepare(self, window_id: int) -> str:
        info = self.window(window_id)
        if info is not None and info.minimized:
            # Restoring a window on X11 brings it to the front, so it is left alone.
            return ('The window is minimized, so there is no picture of it; its controls can still be read '
                    'and used.')
        return ''

    def give_back_focus(self, window_id: int) -> bool:
        with self._guard:
            event = _XEvent()
            event.client.type = 33  # ClientMessage
            event.client.send_event = 1
            event.client.display = self.x.dpy
            event.client.window = int(window_id)
            event.client.message_type = self.x.atom('_NET_ACTIVE_WINDOW')
            event.client.format = 32
            event.client.data[0] = 2  # sent by a pager, so the window manager honours it
            self.x.lib.XSendEvent(self.x.dpy, self.x.root, 0, (1 << 20) | (1 << 19), byref(event))
            self.x.lib.XSync(self.x.dpy, 0)
        for _ in range(10):
            if self.foreground() == int(window_id):
                return True
            time.sleep(0.05)
        return False

    # -- pixels ---------------------------------------------------------
    def capture(self, window_id: int) -> tuple[Any, tuple[int, int], str]:
        from PIL import Image

        with self._guard:
            info = self._info(int(window_id))
            if info is None:
                return None, (0, 0), 'The window is gone.'
            origin = (info.rect[0], info.rect[1])
            width, height = info.rect[2] - info.rect[0], info.rect[3] - info.rect[1]
            if info.minimized or width <= 0 or height <= 0:
                return None, origin, ''
            x, note, pixmap = self.x, '', 0
            drawable = int(window_id)
            if x.composite is not None:
                if drawable not in self._redirected:
                    x.composite.XCompositeRedirectWindow(x.dpy, drawable, 0)  # keeps its pixels when covered
                    self._redirected.add(drawable)
                    x.lib.XSync(x.dpy, 0)
                    time.sleep(0.35)  # the app repaints into its new off-screen copy
                before = x.errors
                pixmap = x.composite.XCompositeNameWindowPixmap(x.dpy, drawable)
                x.lib.XSync(x.dpy, 0)
                if x.errors != before:
                    pixmap = 0
            if not pixmap:
                note = 'This picture is taken from the screen, so windows covering it may show.'
            before = x.errors
            image = x.lib.XGetImage(x.dpy, pixmap or drawable, 0, 0, width, height, c_ulong(-1).value, 2)
            x.lib.XSync(x.dpy, 0)
            try:
                if not image or x.errors != before:
                    return None, origin, 'The window could not be captured.'
                raw = image.contents
                if raw.bits_per_pixel != 32:
                    return None, origin, 'The window could not be captured (unusual colour depth).'
                data = ctypes.string_at(raw.data, raw.bytes_per_line * raw.height)
                picture = Image.frombuffer('RGB', (raw.width, raw.height), data, 'raw', 'BGRX',
                                           raw.bytes_per_line, 1).copy()
                return picture, origin, note
            finally:
                if image:
                    x.lib.XDestroyImage(image)
                if pixmap:
                    x.lib.XFreePixmap(x.dpy, pixmap)

    # -- controls -------------------------------------------------------
    def _frame(self, info: WindowInfo) -> _Acc | None:
        """The accessibility object of an X window: same process, same title."""
        a = self.atspi
        if a is None or not info.pid:
            return None
        a.pump()
        frames: list[tuple[_Acc, str]] = []
        for app in a.children(a.desktop, 400):
            if a.lib.atspi_accessible_get_process_id(app.ptr, None) != info.pid:
                continue
            for top in a.children(app, 200):
                if a.role(top) in _WINDOW_ROLES:
                    frames.append((top, a.name(top)))
        if not frames:
            return None
        for frame, name in frames:
            if name == info.title:
                return frame
        if len(frames) == 1:
            return frames[0][0]
        showing = [f for f, _ in frames if _ST_SHOWING in a.states(f)]
        return showing[0] if len(showing) == 1 else None

    def _describe(self, ref: _Ref, rects: bool = True) -> Element | None:
        a = self.atspi
        if a is None:
            return None
        acc = ref.acc
        states = ref.states = a.states(acc)
        role = a.role(acc)
        if _ST_DEFUNCT in states or not role:
            return None
        name = a.name(acc)
        actions: list[str] = []
        state: dict[str, Any] = {}
        accelerator = ''
        value: str | None = None
        ref.click = ref.expand = None
        if a.has(acc, 'action'):
            for i, (action, binding) in enumerate(a.actions(acc)):
                if ref.click is None and action in _CLICK_ACTIONS:
                    ref.click = i
                    accelerator = _accelerator(binding)
                elif ref.expand is None and action in _EXPAND_ACTIONS:
                    ref.expand = i
        if role in _TOGGLE_ROLES:
            state['checked'] = _ST_CHECKED in states or _ST_PRESSED in states
            if ref.click is not None:
                actions.append('toggle')
        elif ref.click is not None:
            actions.append('invoke')
        if _ST_SELECTABLE in states and not ref.in_menu:
            state['selected'] = _ST_SELECTED in states
            actions.append('select')
        if _ST_EXPANDABLE in states:
            state['expanded'] = _ST_EXPANDED in states
            if ref.expand is not None:
                actions.append('expand')
        if role == 'password text':
            value = None  # never read back
            if _ST_EDITABLE in states and a.has(acc, 'editable_text'):
                actions.append('set_value')
        elif a.has(acc, 'editable_text') and _ST_EDITABLE in states:
            value = a.text(acc)
            actions.append('set_value')
        elif role in _VALUE_ROLES and a.has(acc, 'value'):
            current = a.lib.atspi_value_get_current_value(acc.ptr, None)
            if role == 'scroll bar':
                state['position'] = round(current, 2)
                actions.append('scroll')
            else:
                value = _number(current)
                if role != 'progress bar' and role != 'level bar':
                    actions.append('set_value')
        elif role in ('text', 'terminal', 'paragraph', 'document text') and a.has(acc, 'text'):
            value = a.text(acc)
        if 'set_value' in actions and 'invoke' in actions:
            # A field's own action is "activate" (like pressing Enter), not a click.
            actions.remove('invoke')
        rect = a.extents(acc) if rects and not ref.in_menu and a.has(acc, 'component') else (0, 0, 0, 0)
        enabled = _ST_ENABLED in states or _ST_SENSITIVE in states
        return Element(role, name, value, enabled, actions, rect, state, accelerator, ref)

    def _walk(self, info: WindowInfo, rects: bool = True) -> list[Element]:
        a = self.atspi
        frame = self._frame(info)
        if a is None or frame is None:
            return []
        out: list[Element] = []
        visited = 0
        stack: list[tuple[_Acc, int, bool]] = [(frame, 0, False)]
        while stack and len(out) < _MAX_NODES and visited < _MAX_NODES * 6:
            acc, depth, in_menu = stack.pop()
            visited += 1
            ref = _Ref(acc, info.id, in_menu)
            element = self._describe(ref, rects)
            if element is None:
                continue
            states = ref.states
            # Closed menus are not showing, but their items can still be run
            # (and carry the shortcuts), so menus are read all the way down.
            menu = in_menu or element.role in _MENU_ROLES
            if element.role == 'separator':
                continue
            if depth and not menu and not info.minimized and _ST_SHOWING not in states:
                continue
            named = bool(element.name.strip())
            if depth and (named or element.role != 'table cell') and (
                    element.actions or element.value not in (None, '')
                    or (named and element.role not in _FILLER_ROLES)):
                out.append(element)
            if depth < _MAX_DEPTH:
                for child in reversed(a.children(acc, 300)):
                    stack.append((child, depth + 1, menu))
        return out

    def elements(self, window_id: int) -> list[Element]:
        with self._guard:
            info = self._info(int(window_id))
            if info is None:
                return []
            found = self._walk(info)
            self._last[info.id] = found
            return found

    def fingerprint(self, window_id: int) -> str:
        with self._guard:
            info = self._info(int(window_id))
            if info is None:
                return ''
            return fingerprint_of(info.title, self._walk(info, rects=False))

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
            a = self.atspi
            if a is None:
                return None
            for element in self.elements(window_id):
                if _ST_FOCUSED in element.ref.states and 'set_value' in element.actions:
                    return element
            # A window in the background reports no focused control; use the
            # one this driver last put the caret in.
            remembered = self._focus.get(int(window_id))
            fresh = self.element_state(remembered) if remembered is not None else None
            if fresh is None:
                self._focus.pop(int(window_id), None)
            return fresh

    def perform(self, element: Element, action: str, value: str | None = None) -> None:
        a, ref = self.atspi, element.ref
        if a is None or not isinstance(ref, _Ref):
            raise RuntimeError('no accessibility connection')
        acc = ref.acc
        with self._guard:
            if action in ('invoke', 'toggle'):
                ok = ref.click is not None and a.lib.atspi_action_do_action(acc.ptr, ref.click, None)
            elif action in ('expand', 'collapse'):
                ok = ref.expand is not None and a.lib.atspi_action_do_action(acc.ptr, ref.expand, None)
            elif action == 'select':
                parent = a.wrap(a.lib.atspi_accessible_get_parent(acc.ptr, None))
                index = a.lib.atspi_accessible_get_index_in_parent(acc.ptr, None)
                ok = bool(parent is not None and index >= 0 and a.has(parent, 'selection')
                          and a.lib.atspi_selection_select_child(parent.ptr, index, None))
                if not ok and ref.click is not None:
                    ok = a.lib.atspi_action_do_action(acc.ptr, ref.click, None)
            elif action == 'set_value':
                if a.has(acc, 'editable_text'):
                    ok = a.lib.atspi_editable_text_set_text_contents(acc.ptr, (value or '').encode(), None)
                    if ok:
                        self._focus[ref.window_id] = element
                else:
                    ok = a.lib.atspi_value_set_current_value(acc.ptr, float(value or 0), None)
            elif action.startswith('scroll_'):
                direction = action.split('_', 1)[1]
                horizontal = _ST_HORIZONTAL in a.states(acc)
                if horizontal != (direction in ('left', 'right')):
                    raise RuntimeError('that scroll bar moves the other way')
                low = a.lib.atspi_value_get_minimum_value(acc.ptr, None)
                high = a.lib.atspi_value_get_maximum_value(acc.ptr, None)
                step = max(a.lib.atspi_value_get_minimum_increment(acc.ptr, None), (high - low) / 20.0, 1.0)
                now = a.lib.atspi_value_get_current_value(acc.ptr, None)
                sign = 1 if direction in ('down', 'right') else -1
                target = min(high, max(low, now + sign * step * max(1, int(value or 1))))
                ok = a.lib.atspi_value_set_current_value(acc.ptr, target, None)
            else:
                raise ValueError(f'unknown action {action}')
            if not ok:
                raise RuntimeError(f'the app refused {action}')

    # -- sent input -----------------------------------------------------
    def _send(self, window: int, kind: int, detail: int, state: int, root_x: int, root_y: int,
              x: int, y: int, mask: int) -> None:
        event = _XEvent()
        e = event.input
        e.type, e.send_event, e.display = kind, 1, self.x.dpy
        e.window, e.root, e.subwindow, e.time = window, self.x.root, 0, 0
        e.x, e.y, e.x_root, e.y_root = x, y, root_x, root_y
        e.state, e.detail, e.same_screen = state, detail, 1
        self.x.lib.XSendEvent(self.x.dpy, window, 1, mask, byref(event))

    def _focus_at(self, window_id: int, x: int, y: int) -> None:
        """Put the caret in the control under a point, without raising the window."""
        a = self.atspi
        if a is None:
            return
        hits = [e for e in self._last.get(window_id, [])
                if e.rect[0] <= x < e.rect[2] and e.rect[1] <= y < e.rect[3] and isinstance(e.ref, _Ref)
                and 'set_value' in e.actions]
        if not hits:
            return
        target = min(hits, key=lambda e: (e.rect[2] - e.rect[0]) * (e.rect[3] - e.rect[1]))
        if a.has(target.ref.acc, 'component'):
            a.lib.atspi_component_grab_focus(target.ref.acc.ptr, None)
        self._focus[window_id] = target

    def post_click(self, window_id: int, x: int, y: int, button: str, double: bool) -> None:
        number = {'left': 1, 'middle': 2, 'right': 3}.get(button, 1)
        with self._guard:
            self._focus.pop(int(window_id), None)
            self._focus_at(int(window_id), x, y)
            target, wx, wy = self.x.deepest(int(window_id), x, y)
            for _ in range(2 if double else 1):
                self._send(target, 4, number, 0, x, y, wx, wy, 1 << 2)  # ButtonPress
                self._send(target, 5, number, 1 << (7 + number), x, y, wx, wy, 1 << 3)  # ButtonRelease
            self.x.lib.XSync(self.x.dpy, 0)

    def post_scroll(self, window_id: int, x: int, y: int, amount: int, horizontal: bool = False) -> None:
        # Wheel "buttons": 4 up, 5 down, 6 left, 7 right. Positive is up or right.
        number = (7 if amount > 0 else 6) if horizontal else (4 if amount > 0 else 5)
        with self._guard:
            target, wx, wy = self.x.deepest(int(window_id), x, y)
            for _ in range(max(1, abs(int(amount)))):
                self._send(target, 4, number, 0, x, y, wx, wy, 1 << 2)
                self._send(target, 5, number, 0, x, y, wx, wy, 1 << 3)
            self.x.lib.XSync(self.x.dpy, 0)

    def _key_target(self, window_id: int) -> tuple[int, int, int, int, int]:
        info = self._info(int(window_id))
        if info is None:
            raise RuntimeError('the window is gone')
        cx, cy = (info.rect[0] + info.rect[2]) // 2, (info.rect[1] + info.rect[3]) // 2
        return (int(window_id), cx, cy, cx - info.rect[0], cy - info.rect[1])

    def _press(self, target: tuple[int, int, int, int, int], keysym: int, state: int) -> None:
        lib, dpy = self.x.lib, self.x.dpy
        code = lib.XKeysymToKeycode(dpy, keysym)
        if not code:
            raise ValueError('this keyboard layout has no key for it')
        if lib.XkbKeycodeToKeysym(dpy, code, 0, 0) != keysym and lib.XkbKeycodeToKeysym(dpy, code, 0, 1) == keysym:
            state |= 1  # needs Shift
        window, rx, ry, wx, wy = target
        self._send(window, 2, code, state, rx, ry, wx, wy, 1 << 0)  # KeyPress
        self._send(window, 3, code, state, rx, ry, wx, wy, 1 << 1)  # KeyRelease

    def post_key(self, window_id: int, key: str, mods: tuple[str, ...] = ()) -> None:
        key = key.lower()
        name = _KEYSYMS.get(key) or (key.upper() if re.fullmatch(r'f\d{1,2}', key) else key)
        with self._guard:
            keysym = self.x.lib.XStringToKeysym(name.encode())
            if not keysym and len(key) == 1:
                keysym = ord(key)
            if not keysym:
                raise ValueError(f"unknown key '{key}'")
            state = 0
            for mod in mods:
                if mod not in _MOD_MASKS:
                    raise ValueError(f"unknown modifier '{mod}'")
                state |= _MOD_MASKS[mod]
            self._press(self._key_target(window_id), keysym, state)
            self.x.lib.XSync(self.x.dpy, 0)

    def post_text(self, window_id: int, text: str) -> None:
        with self._guard:
            a = self.atspi
            field = self.focused_element(window_id)
            if a is not None and field is not None and '\n' not in text.rstrip('\n'):
                # Put the text at the caret through the accessibility bus:
                # this works in a background window, sent keys mostly do not.
                acc = field.ref.acc
                caret = a.lib.atspi_text_get_caret_offset(acc.ptr, None) if a.has(acc, 'text') else -1
                if caret < 0:
                    caret = max(0, a.lib.atspi_text_get_character_count(acc.ptr, None))
                body = text.rstrip('\n').encode()
                if body and a.lib.atspi_editable_text_insert_text(acc.ptr, caret, body, len(body), None):
                    if text.endswith('\n'):
                        self._press(self._key_target(window_id), self.x.lib.XStringToKeysym(b'Return'), 0)
                        self.x.lib.XSync(self.x.dpy, 0)
                    return
            target = self._key_target(window_id)
            for char in text:
                if char == '\n':
                    keysym = self.x.lib.XStringToKeysym(b'Return')
                elif char == '\t':
                    keysym = self.x.lib.XStringToKeysym(b'Tab')
                else:
                    keysym = ord(char) if ord(char) < 0x100 else 0x01000000 | ord(char)
                try:
                    self._press(target, keysym, 0)
                except ValueError:
                    continue  # no key for this character on the layout
            self.x.lib.XSync(self.x.dpy, 0)

    # -- real input (use_screen) ------------------------------------------
    # The X Test extension sends input as if from the real mouse and keyboard,
    # which every toolkit accepts (sent events are ignored by GTK3, Qt and
    # Chromium). On a person's desktop the driver only uses it after asking;
    # in a sandbox the screen belongs to the agent.
    def _xtest(self):
        if self.x.xtest is None:
            raise RuntimeError('real input needs the X Test extension (libXtst), which is not available here')
        return self.x.xtest

    def idle_seconds(self) -> float:
        """Seconds since the last keyboard or mouse input on this display."""
        with self._guard:
            xss = self.x.xss
            if xss is None or not xss.XScreenSaverQueryInfo(self.x.dpy, self.x.root, self.x.xss_info):
                return float('inf')  # can't tell: assume nobody is typing
            # XScreenSaverInfo: window, state, kind, til_or_since, idle, event_mask
            idle_ms = ctypes.cast(self.x.xss_info, POINTER(_XScreenSaverInfo)).contents.idle
            return idle_ms / 1000.0

    def cursor_pos(self) -> tuple[int, int]:
        with self._guard:
            root, child = c_ulong(), c_ulong()
            rx, ry, wx, wy, mask = c_int(), c_int(), c_int(), c_int(), c_uint()
            self.x.lib.XQueryPointer(self.x.dpy, self.x.root, byref(root), byref(child), byref(rx), byref(ry),
                                     byref(wx), byref(wy), byref(mask))
            return rx.value, ry.value

    def set_cursor_pos(self, x: int, y: int) -> None:
        with self._guard:
            self._xtest().XTestFakeMotionEvent(self.x.dpy, -1, int(x), int(y), 0)
            self.x.lib.XSync(self.x.dpy, 0)

    def bring_to_front(self, window_id: int) -> bool:
        with self._guard:
            self.x.lib.XRaiseWindow(self.x.dpy, int(window_id))
            self.x.lib.XSync(self.x.dpy, 0)
        return self.give_back_focus(int(window_id))  # _NET_ACTIVE_WINDOW as a pager

    def send_click(self, x: int, y: int, button: str, double: bool, still_ok: Callable[[], bool]) -> bool:
        number = {'left': 1, 'middle': 2, 'right': 3}.get(button, 1)
        xt = self._xtest()
        with self._guard:
            xt.XTestFakeMotionEvent(self.x.dpy, -1, int(x), int(y), 0)
            self.x.lib.XSync(self.x.dpy, 0)
        for _ in range(2 if double else 1):
            if not still_ok():
                return False
            with self._guard:
                xt.XTestFakeButtonEvent(self.x.dpy, number, 1, 0)
                xt.XTestFakeButtonEvent(self.x.dpy, number, 0, 0)
                self.x.lib.XSync(self.x.dpy, 0)
            time.sleep(0.03)
        return True

    def _keycode_for(self, keysym: int) -> tuple[int, bool, bool]:
        """(keycode, needs shift, was remapped). A character with no key on
        the layout is put on a spare keycode for the moment."""
        lib, dpy = self.x.lib, self.x.dpy
        code = lib.XKeysymToKeycode(dpy, keysym)
        if code:
            shift = (lib.XkbKeycodeToKeysym(dpy, code, 0, 0) != keysym
                     and lib.XkbKeycodeToKeysym(dpy, code, 0, 1) == keysym)
            return code, shift, False
        spare = self._spare_keycode()
        if not spare:
            raise ValueError('this keyboard layout has no key for it')
        syms = (c_ulong * 2)(keysym, keysym)
        lib.XChangeKeyboardMapping(dpy, spare, 2, syms, 1)
        lib.XSync(dpy, 0)
        time.sleep(0.02)  # let clients see the new mapping
        return spare, False, True

    def _spare_keycode(self) -> int:
        cached = getattr(self, '_spare', None)
        if cached is not None:
            return cached
        lib, dpy = self.x.lib, self.x.dpy
        low, high = c_int(), c_int()
        lib.XDisplayKeycodes(dpy, byref(low), byref(high))
        per = c_int()
        table = lib.XGetKeyboardMapping(dpy, low.value, high.value - low.value + 1, byref(per))
        spare = 0
        if table:
            for code in range(high.value, low.value - 1, -1):
                row = (code - low.value) * per.value
                if not any(table[row + i] for i in range(per.value)):
                    spare = code
                    break
            lib.XFree(table)
        self._spare = spare
        return spare

    def _tap(self, keysym: int, mods: tuple[int, ...] = ()) -> None:
        xt, dpy = self._xtest(), self.x.dpy
        code, shift, remapped = self._keycode_for(keysym)
        held = list(mods)
        if shift:
            held.append(self.x.lib.XKeysymToKeycode(dpy, self.x.lib.XStringToKeysym(b'Shift_L')))
        for m in held:
            xt.XTestFakeKeyEvent(dpy, m, 1, 0)
        xt.XTestFakeKeyEvent(dpy, code, 1, 0)
        xt.XTestFakeKeyEvent(dpy, code, 0, 0)
        for m in reversed(held):
            xt.XTestFakeKeyEvent(dpy, m, 0, 0)
        self.x.lib.XSync(dpy, 0)
        if remapped:
            time.sleep(0.02)  # the app reads the mapping when it handles the key

    def send_text(self, text: str, still_ok: Callable[[], bool]) -> bool:
        for char in text:
            if not still_ok():
                return False
            if char == '\n':
                keysym = self.x.lib.XStringToKeysym(b'Return')
            elif char == '\t':
                keysym = self.x.lib.XStringToKeysym(b'Tab')
            else:
                keysym = ord(char) if ord(char) < 0x100 else 0x01000000 | ord(char)
            with self._guard:
                self._tap(keysym)
            time.sleep(0.008)
        return True

    def send_key(self, key: str, mods: tuple[str, ...], still_ok: Callable[[], bool]) -> bool:
        if not still_ok():
            return False
        key = key.lower()
        name = _KEYSYMS.get(key) or (key.upper() if re.fullmatch(r'f\d{1,2}', key) else key)
        with self._guard:
            lib, dpy = self.x.lib, self.x.dpy
            keysym = lib.XStringToKeysym(name.encode())
            if not keysym and len(key) == 1:
                keysym = ord(key)
            if not keysym:
                raise ValueError(f"unknown key '{key}'")
            codes = []
            for mod in mods:
                if mod not in _MOD_KEYSYMS:
                    raise ValueError(f"unknown modifier '{mod}'")
                codes.append(lib.XKeysymToKeycode(dpy, lib.XStringToKeysym(_MOD_KEYSYMS[mod])))
            self._tap(keysym, tuple(codes))
        return True

