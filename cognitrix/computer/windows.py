"""Windows backend: Win32 (ctypes) for windows, capture and posted input;
UI Automation (comtypes) for elements and patterns. Never activates windows
or moves the real pointer."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time
from _ctypes import COMError

from cognitrix.computer import Element, WindowInfo, blocked_reason, fingerprint_of

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32')
dwmapi = ctypes.WinDLL('dwmapi')
advapi32 = ctypes.WinDLL('advapi32')

WM_CHAR, WM_KEYDOWN, WM_KEYUP = 0x0102, 0x0100, 0x0101
WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0200, 0x020A, 0x020E
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
HWND_BOTTOM = 1
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
PW_RENDERFULLCONTENT = 2
CWP_SKIPINVISIBLE, CWP_SKIPTRANSPARENT = 0x0001, 0x0004
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DPI_PER_MONITOR_V2 = ctypes.c_void_p(-4)

for _fn, _args, _res in [
    ('GetForegroundWindow', [], wt.HWND),
    ('GetWindowTextW', [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
    ('GetClassNameW', [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
    ('GetWindowThreadProcessId', [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
    ('IsWindow', [wt.HWND], wt.BOOL),
    ('IsWindowVisible', [wt.HWND], wt.BOOL),
    ('IsIconic', [wt.HWND], wt.BOOL),
    ('GetWindowLongW', [wt.HWND, ctypes.c_int], ctypes.c_long),
    ('GetWindowRect', [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
    ('ShowWindow', [wt.HWND, ctypes.c_int], wt.BOOL),
    ('SetWindowPos', [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT],
     wt.BOOL),
    ('PostMessageW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
    ('PrintWindow', [wt.HWND, wt.HDC, wt.UINT], wt.BOOL),
    ('GetDC', [wt.HWND], wt.HDC),
    ('ReleaseDC', [wt.HWND, wt.HDC], ctypes.c_int),
    ('ScreenToClient', [wt.HWND, ctypes.POINTER(wt.POINT)], wt.BOOL),
    ('ChildWindowFromPointEx', [wt.HWND, wt.POINT, wt.UINT], wt.HWND),
    ('FindWindowExW', [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR], wt.HWND),
    ('MapVirtualKeyW', [wt.UINT, wt.UINT], wt.UINT),
    ('VkKeyScanW', [wt.WCHAR], ctypes.c_short),
    ('SetForegroundWindow', [wt.HWND], wt.BOOL),
    ('IsChild', [wt.HWND, wt.HWND], wt.BOOL),
    ('AttachThreadInput', [wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
    ('SwitchToThisWindow', [wt.HWND, wt.BOOL], None),
    ('GetKeyboardState', [ctypes.c_void_p], wt.BOOL),
    ('SendMessageTimeoutW', [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                             ctypes.POINTER(ctypes.c_size_t)], wt.LPARAM),
    ('SetKeyboardState', [ctypes.c_void_p], wt.BOOL),
]:
    getattr(user32, _fn).argtypes, getattr(user32, _fn).restype = _args, _res

_EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [_EnumProc, wt.LPARAM]
user32.EnumChildWindows.argtypes = [wt.HWND, _EnumProc, wt.LPARAM]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
kernel32.GetCurrentProcess.restype = wt.HANDLE
advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
                                         ctypes.POINTER(wt.DWORD)]
try:
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
except AttributeError:  # older than Windows 10 1607
    pass


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('flags', wt.DWORD), ('hwndActive', wt.HWND), ('hwndFocus', wt.HWND),
                ('hwndCapture', wt.HWND), ('hwndMenuOwner', wt.HWND), ('hwndMoveSize', wt.HWND),
                ('hwndCaret', wt.HWND), ('rcCaret', wt.RECT)]


user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(_GUITHREADINFO)]

# Shell surfaces that aren't app windows.
_SHELL_CLASSES = frozenset({'Shell_TrayWnd', 'Shell_SecondaryTrayWnd', 'Progman', 'WorkerW',
                            'Windows.UI.Core.CoreWindow', 'Xaml_WindowedPopupClass'})
_SHELL_APPS = frozenset({'textinputhost.exe', 'searchhost.exe', 'startmenuexperiencehost.exe',
                         'shellexperiencehost.exe', 'searchapp.exe', 'lockapp.exe'})
_CONSOLE_CLASSES = frozenset({'ConsoleWindowClass', 'CASCADIA_HOSTING_WINDOW_CLASS', 'PseudoConsoleWindow'})
_WEB_CLASSES = ('Chrome_WidgetWin_', 'MozillaWindowClass')

_APP_ALIASES = {
    'calculator': 'calc.exe', 'calc': 'calc.exe', 'notepad': 'notepad.exe',
    'file explorer': 'explorer.exe', 'explorer': 'explorer.exe', 'paint': 'mspaint.exe',
    'wordpad': 'write.exe', 'task manager': 'taskmgr.exe',
}

_VK = {
    'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'escape': 0x1B, 'esc': 0x1B, 'backspace': 0x08,
    'delete': 0x2E, 'del': 0x2E, 'insert': 0x2D, 'space': 0x20, 'home': 0x24, 'end': 0x23,
    'pageup': 0x21, 'pagedown': 0x22, 'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    **{f'f{i}': 0x6F + i for i in range(1, 13)},
}

_ROLES = {
    50000: 'Button', 50001: 'Calendar', 50002: 'CheckBox', 50003: 'ComboBox', 50004: 'Edit',
    50005: 'Link', 50006: 'Image', 50007: 'ListItem', 50008: 'List', 50009: 'Menu', 50010: 'MenuBar',
    50011: 'MenuItem', 50012: 'ProgressBar', 50013: 'RadioButton', 50014: 'ScrollBar', 50015: 'Slider',
    50016: 'Spinner', 50017: 'StatusBar', 50018: 'Tab', 50019: 'TabItem', 50020: 'Text', 50021: 'ToolBar',
    50022: 'ToolTip', 50023: 'Tree', 50024: 'TreeItem', 50025: 'Custom', 50026: 'Group', 50027: 'Thumb',
    50028: 'DataGrid', 50029: 'DataItem', 50030: 'Document', 50031: 'SplitButton', 50032: 'Window',
    50033: 'Pane', 50034: 'Header', 50035: 'HeaderItem', 50036: 'Table', 50037: 'TitleBar',
    50038: 'Separator', 50039: 'SemanticZoom', 50040: 'AppBar',
}
_SKIP_ROLES = frozenset({'Separator', 'Thumb', 'ScrollBar', 'ToolTip'})
_TEXT_ROLES = frozenset({'Text', 'Edit', 'Document', 'Link', 'StatusBar', 'Header', 'HeaderItem'})


def _dpi_aware() -> None:
    try:
        user32.SetThreadDpiAwarenessContext(DPI_PER_MONITOR_V2)
    except Exception:
        pass


def _text(hwnd: int, getter) -> str:
    buf = ctypes.create_unicode_buffer(512)
    getter(hwnd, buf, 512)
    return buf.value


def _pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _thread(hwnd: int) -> int:
    return user32.GetWindowThreadProcessId(hwnd, None)


def _frame(hwnd: int) -> tuple[int, int, int, int]:
    rect = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                    ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _cloaked(hwnd: int) -> bool:
    value = wt.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


class _ProcInfo:
    """Exe name and elevation per pid (cached; pids are reused rarely)."""

    def __init__(self):
        self._cache: dict[int, tuple[str, bool]] = {}

    def get(self, pid: int) -> tuple[str, bool]:
        if pid in self._cache:
            return self._cache[pid]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Access denied: a protected or elevated process we can't drive.
            info = ('', ctypes.get_last_error() == 5)
        else:
            try:
                size = wt.DWORD(1024)
                buf = ctypes.create_unicode_buffer(1024)
                name = ''
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(buf.value).lower()
                info = (name, _elevated(handle))
            finally:
                kernel32.CloseHandle(handle)
        self._cache[pid] = info
        return info


def _elevated(process_handle) -> bool:
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(process_handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        return False
    try:
        value, size = wt.DWORD(), wt.DWORD()
        ok = advapi32.GetTokenInformation(token, 20, ctypes.byref(value), ctypes.sizeof(value),
                                          ctypes.byref(size))  # TokenElevation
        return bool(ok and value.value)
    finally:
        kernel32.CloseHandle(token)


def _self_elevated() -> bool:
    return _elevated(kernel32.GetCurrentProcess())


# -- UI Automation ----------------------------------------------------------

_com_local = threading.local()
_uia_guard = threading.Lock()
_uia_mod = None
_uia_obj = None


def _uia():
    """The UIA client, created in the multithreaded apartment so any worker
    thread can use it. Each thread joins the MTA on first use."""
    global _uia_mod, _uia_obj
    if not getattr(_com_local, 'ready', False):
        if 'comtypes' not in sys.modules:
            sys.coinit_flags = 0  # COINIT_MULTITHREADED for comtypes' own init
        import comtypes
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialized on this thread
        _com_local.ready = True
    with _uia_guard:
        if _uia_obj is None:
            import comtypes.client
            comtypes.client.GetModule('UIAutomationCore.dll')
            from comtypes.gen import UIAutomationClient as mod
            _uia_mod = mod
            _uia_obj = comtypes.client.CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
    return _uia_mod, _uia_obj


_PROPS = (
    'ControlType', 'Name', 'IsEnabled', 'BoundingRectangle', 'IsOffscreen', 'AcceleratorKey',
    'ValueValue', 'ValueIsReadOnly', 'ToggleToggleState', 'ExpandCollapseExpandCollapseState',
    'SelectionItemIsSelected', 'ScrollVerticalScrollPercent', 'ScrollHorizontalScrollPercent',
    'IsInvokePatternAvailable', 'IsTogglePatternAvailable', 'IsExpandCollapsePatternAvailable',
    'IsSelectionItemPatternAvailable', 'IsValuePatternAvailable', 'IsScrollPatternAvailable',
)


def _cache_request(mod, uia):
    request = uia.CreateCacheRequest()
    for name in _PROPS:
        request.AddProperty(getattr(mod, f'UIA_{name}PropertyId'))
    return request


def _cached(mod, el, name):
    try:
        return el.GetCachedPropertyValue(getattr(mod, f'UIA_{name}PropertyId'))
    except Exception:
        return None


def _to_element(mod, el) -> Element | None:
    role = _ROLES.get(_cached(mod, el, 'ControlType') or 0, 'Custom')
    if role in _SKIP_ROLES:
        return None
    name = (_cached(mod, el, 'Name') or '').strip()
    actions, state = [], {}
    if _cached(mod, el, 'IsInvokePatternAvailable'):
        actions.append('invoke')
    if _cached(mod, el, 'IsTogglePatternAvailable'):
        actions.append('toggle')
        state['toggled'] = {0: 'off', 1: 'on', 2: 'mixed'}.get(_cached(mod, el, 'ToggleToggleState'), '?')
    if _cached(mod, el, 'IsSelectionItemPatternAvailable'):
        actions.append('select')
        state['selected'] = bool(_cached(mod, el, 'SelectionItemIsSelected'))
    if _cached(mod, el, 'IsExpandCollapsePatternAvailable'):
        ec = _cached(mod, el, 'ExpandCollapseExpandCollapseState')
        if ec != 3:  # 3 = leaf node
            actions.append('expand')
            state['expanded'] = ec in (1, 2)
    value = None
    if _cached(mod, el, 'IsValuePatternAvailable'):
        value = _cached(mod, el, 'ValueValue')
        if not _cached(mod, el, 'ValueIsReadOnly'):
            actions.append('set_value')
    if _cached(mod, el, 'IsScrollPatternAvailable'):
        v = _cached(mod, el, 'ScrollVerticalScrollPercent')
        h = _cached(mod, el, 'ScrollHorizontalScrollPercent')
        if (v is not None and v >= 0) or (h is not None and h >= 0):
            actions.append('scroll')
            if v is not None and v >= 0:
                state['scrolled'] = f'{round(v)}%'
    if not actions and not (name and role in _TEXT_ROLES) and not value:
        return None
    rect = _cached(mod, el, 'BoundingRectangle') or (0, 0, 0, 0)
    left, top, width, height = (int(v) for v in rect)
    return Element(role=role, name=name, value=value if isinstance(value, str) else None,
                   enabled=bool(_cached(mod, el, 'IsEnabled')), actions=actions,
                   rect=(left, top, left + width, top + height), state=state,
                   accelerator=(_cached(mod, el, 'AcceleratorKey') or ''), ref=el)


def _with_timeout(fn, seconds: float = 5.0) -> None:
    """Run a UIA pattern call that may block (e.g. a button that opens a modal
    dialog) without hanging the tool call."""
    errors: list[BaseException] = []

    def run():
        _uia()
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if errors:
        raise errors[0]


class WindowsBackend:
    def __init__(self):
        self._procs = _ProcInfo()
        self._own_pid = os.getpid()
        self._self_elevated = _self_elevated()

    # -- windows ----------------------------------------------------------
    def _info(self, hwnd: int) -> WindowInfo | None:
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd) or _cloaked(hwnd):
            return None
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            return None
        title = _text(hwnd, user32.GetWindowTextW)
        cls = _text(hwnd, user32.GetClassNameW)
        if not title or cls in _SHELL_CLASSES:
            return None
        pid = _pid(hwnd)
        app, elevated = self._procs.get(pid)
        kind = 'win32'
        if cls == 'ApplicationFrameWindow':
            kind = 'uwp'
            core = user32.FindWindowExW(hwnd, None, 'Windows.UI.Core.CoreWindow', None)
            if core:
                pid = _pid(core)
                app, elevated = self._procs.get(pid)
        elif cls in _CONSOLE_CLASSES:
            kind = 'console'
        elif cls.startswith(_WEB_CLASSES):
            kind = 'chromium'
        if app in _SHELL_APPS:
            return None
        own = pid == self._own_pid
        blocked = blocked_reason(app, kind, title, own=own, elevated=elevated and not self._self_elevated)
        return WindowInfo(id=int(hwnd), title=title, app=app or '?', pid=pid,
                          minimized=bool(user32.IsIconic(hwnd)), rect=_frame(hwnd), kind=kind, blocked=blocked)

    def list_windows(self) -> list[WindowInfo]:
        _dpi_aware()
        found: list[int] = []
        cb = _EnumProc(lambda h, _: found.append(h) or True)
        user32.EnumWindows(cb, 0)
        out = []
        for hwnd in found:
            info = self._info(hwnd)
            if info is not None and info.blocked != "it is Cognitrix's own window":
                out.append(info)
        return out

    def window(self, window_id: int) -> WindowInfo | None:
        _dpi_aware()
        return self._info(int(window_id))

    def foreground(self) -> int:
        return int(user32.GetForegroundWindow() or 0)

    def launch(self, target: str, args: str | None = None) -> None:
        import win32api  # pywin32, a Windows dependency
        target = target.strip().strip('"')
        if args:
            win32api.ShellExecute(0, 'open', target, args, None, SW_SHOWNOACTIVATE)
            return
        if os.path.isdir(target):
            win32api.ShellExecute(0, 'open', 'explorer.exe', f'"{target}"', None, SW_SHOWNOACTIVATE)
            return
        if os.path.isfile(target):
            # Opening through the shell association lets the app take focus,
            # so start the associated program directly with the file.
            command = _associated_command(target)
            if command:
                win32api.ShellExecute(0, 'open', command[0], command[1], None, SW_SHOWNOACTIVATE)
                return
        resolved = _APP_ALIASES.get(target.lower(), target)
        try:
            win32api.ShellExecute(0, 'open', resolved, None, None, SW_SHOWNOACTIVATE)
            return
        except Exception as exc:
            first_error = exc
        app_id = _start_app_id(target)
        if not app_id:
            raise RuntimeError(f"Couldn't open {target!r}: {first_error}")
        win32api.ShellExecute(0, 'open', f'shell:AppsFolder\\{app_id}', None, None, SW_SHOWNOACTIVATE)

    def prepare(self, window_id: int) -> str:
        hwnd = int(window_id)
        if not user32.IsIconic(hwnd):
            return ''
        # Restore without activating, then send it behind other windows so it
        # doesn't cover the person's work. PrintWindow captures it there.
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        time.sleep(0.4)
        if user32.IsIconic(hwnd):
            return 'The window is minimized and could not be restored in the background.'
        return 'The window was minimized; it was restored behind your other windows without focus.'

    def capture(self, window_id: int):
        _dpi_aware()
        from PIL import Image
        hwnd = int(window_id)
        outer = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(outer))
        frame = _frame(hwnd)
        width, height = outer.right - outer.left, outer.bottom - outer.top
        if width <= 0 or height <= 0 or user32.IsIconic(hwnd):
            return None, frame[:2], 'No screenshot: the window is minimized.'
        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        old = gdi32.SelectObject(mem_dc, bitmap)
        try:
            user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
            # BITMAPINFOHEADER: 32-bit, top-down (negative height).
            header = (ctypes.c_uint32 * 10)(40, width, (-height) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(width * height * 4)
            gdi32.GetDIBits(mem_dc, bitmap, 0, height, pixels, header, 0)
        finally:
            gdi32.SelectObject(mem_dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
        image = Image.frombuffer('RGB', (width, height), pixels, 'raw', 'BGRX', 0, 1)
        crop = (frame[0] - outer.left, frame[1] - outer.top, frame[2] - outer.left, frame[3] - outer.top)
        if crop[2] > crop[0] and crop[3] > crop[1]:
            image = image.crop(crop)
        if max(hi for _, hi in image.getextrema()) < 8:
            return None, frame[:2], ('The screenshot came back black (this app draws in a way that '
                                     'background capture cannot see); use the element list.')
        return image, frame[:2], ''

    # -- elements ---------------------------------------------------------
    def _root(self, hwnd: int):
        mod, uia = _uia()
        return mod, uia, uia.ElementFromHandle(hwnd)

    def elements(self, window_id: int, cap: int = 600) -> list[Element]:
        _dpi_aware()
        for attempt in range(3):
            try:
                mod, uia, root = self._root(int(window_id))
                found = root.FindAllBuildCache(4, uia.ControlViewCondition, _cache_request(mod, uia))  # descendants
                break
            except COMError:  # e.g. a page mid-navigation; try again
                if attempt == 2:
                    raise
                time.sleep(0.5)
        out: list[Element] = []
        for i in range(min(found.Length, cap * 3)):
            el = found.GetElement(i)
            if _cached(mod, el, 'IsOffscreen'):
                continue
            item = _to_element(mod, el)
            if item is not None:
                out.append(item)
                if len(out) >= cap:
                    break
        return out

    def element_state(self, element: Element) -> Element | None:
        mod, uia = _uia()
        try:
            fresh = element.ref.BuildUpdatedCache(_cache_request(mod, uia))
        except Exception:
            return None
        return _to_element(mod, fresh) or Element(role=element.role, name=element.name, ref=fresh)

    def perform(self, element: Element, action: str, value: str | None = None) -> None:
        mod, _ = _uia()
        el = element.ref

        def pattern(name):
            raw = el.GetCurrentPattern(getattr(mod, f'UIA_{name}PatternId'))
            return raw.QueryInterface(getattr(mod, f'IUIAutomation{name}Pattern'))

        if action == 'invoke':
            _with_timeout(lambda: pattern('Invoke').Invoke())
        elif action == 'toggle':
            _with_timeout(lambda: pattern('Toggle').Toggle())
        elif action == 'select':
            _with_timeout(lambda: pattern('SelectionItem').Select())
        elif action == 'expand':
            _with_timeout(lambda: pattern('ExpandCollapse').Expand())
        elif action == 'collapse':
            _with_timeout(lambda: pattern('ExpandCollapse').Collapse())
        elif action == 'set_value':
            _with_timeout(lambda: pattern('Value').SetValue(value or ''))
        elif action.startswith('scroll_'):
            direction = action.split('_', 1)[1]
            no, back, fwd = 2, 1, 4  # ScrollAmount: NoAmount, SmallDecrement, SmallIncrement
            step = {'up': (no, back), 'down': (no, fwd), 'left': (back, no), 'right': (fwd, no)}[direction]
            scroll = pattern('Scroll')
            for _ in range(max(1, int(value or 1))):
                scroll.Scroll(*step)
        else:
            raise ValueError(f'Unknown action {action}')

    def focused_element(self, window_id: int) -> Element | None:
        mod, uia = _uia()
        focus = self._focus_hwnd(int(window_id))
        candidates = []
        if focus:
            try:
                candidates.append(uia.ElementFromHandle(focus).BuildUpdatedCache(_cache_request(mod, uia)))
            except Exception:
                pass
        for el in candidates:
            item = _to_element(mod, el)
            if item is not None and item.value is not None:
                return item
        # Fall back to the window's only editable text area, if there is one.
        editable = [e for e in self.elements(window_id) if 'set_value' in e.actions
                    and e.role in ('Edit', 'Document')]
        return editable[0] if len(editable) == 1 else None

    # -- posted input -----------------------------------------------------
    def _focus_hwnd(self, hwnd: int) -> int:
        info = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
        if not (user32.GetGUIThreadInfo(_thread(hwnd), ctypes.byref(info)) and info.hwndFocus):
            return 0
        focus = int(info.hwndFocus)
        # Windows sharing a GUI thread share its focus, so the focused control
        # may sit in another window (maybe the one the person is typing in).
        if focus == int(hwnd) or user32.IsChild(hwnd, focus):
            return focus
        return 0

    def _deepest_child(self, hwnd: int, sx: int, sy: int) -> tuple[int, int, int]:
        """Deepest child window under a screen point, with client coordinates."""
        target = hwnd
        while True:
            pt = wt.POINT(sx, sy)
            user32.ScreenToClient(target, ctypes.byref(pt))
            child = user32.ChildWindowFromPointEx(target, pt, CWP_SKIPINVISIBLE | CWP_SKIPTRANSPARENT)
            if not child or int(child) == int(target):
                return int(target), pt.x, pt.y
            target = child

    def post_click(self, window_id: int, x: int, y: int, button: str, double: bool) -> None:
        _dpi_aware()
        target, cx, cy = self._deepest_child(int(window_id), x, y)
        lparam = (cy & 0xFFFF) << 16 | (cx & 0xFFFF)
        down, up, flag = ((WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON) if button == 'right'
                          else (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON))
        user32.PostMessageW(target, WM_MOUSEMOVE, 0, lparam)
        user32.PostMessageW(target, down, flag, lparam)
        user32.PostMessageW(target, up, 0, lparam)
        if double:
            user32.PostMessageW(target, WM_LBUTTONDBLCLK if button != 'right' else down, flag, lparam)
            user32.PostMessageW(target, up, 0, lparam)

    def _key_target(self, hwnd: int) -> int:
        return self._focus_hwnd(hwnd) or hwnd

    def post_text(self, window_id: int, text: str) -> None:
        target = self._key_target(int(window_id))
        units = text.replace('\r\n', '\n').replace('\n', '\r').encode('utf-16-le')
        for i in range(0, len(units), 2):
            user32.PostMessageW(target, WM_CHAR, int.from_bytes(units[i:i + 2], 'little'), 1)

    def post_key(self, window_id: int, key: str, mods: tuple[str, ...] = ()) -> None:
        vk = _VK.get(key)
        if vk is None and len(key) == 1:
            vk = user32.VkKeyScanW(key) & 0xFF
        if not vk:
            raise ValueError(f'Unknown key {key!r}')
        hwnd = int(window_id)
        target = self._focus_hwnd(hwnd)
        if not target:
            frame = _frame(hwnd)
            target = self._deepest_child(hwnd, (frame[0] + frame[2]) // 2, (frame[1] + frame[3]) // 2)[0]
        scan = user32.MapVirtualKeyW(vk, 0)
        down, up = 1 | (scan << 16), 1 | (scan << 16) | (0xC0 << 24)
        if mods:
            # Posted messages don't carry modifier state, so share the app
            # thread's input state and mark the modifiers down there only.
            # The person's keyboard and foreground window are not involved.
            with _modifiers(hwnd, mods):
                user32.PostMessageW(target, WM_KEYDOWN, vk, down)
                time.sleep(0.3)  # the app reads key state while handling the message
                user32.PostMessageW(target, WM_KEYUP, vk, up)
                time.sleep(0.1)
            return
        user32.PostMessageW(target, WM_KEYDOWN, vk, down)
        if key in ('enter', 'return', 'tab', 'backspace', 'escape', 'esc'):
            user32.PostMessageW(target, WM_CHAR, {0x0D: 13, 0x09: 9, 0x08: 8, 0x1B: 27}[vk], down)
        user32.PostMessageW(target, WM_KEYUP, vk, up)

    def post_scroll(self, window_id: int, x: int, y: int, amount: int, horizontal: bool = False) -> None:
        # Positive amount scrolls up, or right when horizontal.
        target, _, _ = self._deepest_child(int(window_id), x, y)
        wparam = ((amount * 120) & 0xFFFF) << 16
        message = WM_MOUSEHWHEEL if horizontal else WM_MOUSEWHEEL
        user32.PostMessageW(target, message, wparam, (y & 0xFFFF) << 16 | (x & 0xFFFF))

    def wake(self, window_id: int) -> None:
        """Ask Chromium/Electron for their page accessibility tree, which they
        only build once an assistive client requests it (WM_GETOBJECT)."""
        hwnd = int(window_id)
        targets = [hwnd]
        cb = _EnumProc(lambda h, _: (_text(h, user32.GetClassNameW) == 'Chrome_RenderWidgetHostHWND'
                                     and targets.append(h)) or True)
        user32.EnumChildWindows(hwnd, cb, 0)
        result = ctypes.c_size_t()
        for target in targets:
            user32.SendMessageTimeoutW(target, 0x003D, 0, -4, 0x0002, 500, ctypes.byref(result))  # OBJID_CLIENT

    def give_back_focus(self, window_id: int) -> bool:
        """Return the foreground to the person's window after an app grabbed it.
        Joining the current foreground thread's input lets SetForegroundWindow work."""
        hwnd = int(window_id)
        if not user32.IsWindow(hwnd):
            return False
        current = user32.GetForegroundWindow()
        ours = kernel32.GetCurrentThreadId()
        theirs = _thread(current) if current else 0
        attached = bool(theirs and theirs != ours and user32.AttachThreadInput(ours, theirs, True))
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(ours, theirs, False)
        time.sleep(0.1)
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            user32.SwitchToThisWindow(hwnd, True)
            time.sleep(0.15)
        return int(user32.GetForegroundWindow() or 0) == hwnd

    def fingerprint(self, window_id: int) -> str:
        title = _text(int(window_id), user32.GetWindowTextW)
        try:
            elements = self.elements(window_id, cap=400)
        except Exception:
            elements = []
        return fingerprint_of(title, elements)


_MOD_VK = {'ctrl': (0x11, 0xA2), 'control': (0x11, 0xA2), 'shift': (0x10, 0xA0), 'alt': (0x12, 0xA4)}


class _modifiers:
    """Hold modifier keys down in a background app thread's key state."""

    def __init__(self, hwnd: int, mods):
        self.theirs = _thread(hwnd)
        self.ours = kernel32.GetCurrentThreadId()
        self.mods = [_MOD_VK[m] for m in mods if m in _MOD_VK]
        if len(self.mods) != len(mods):
            raise ValueError(f'Unsupported modifier in {mods}')

    def __enter__(self):
        if self.theirs == _thread(user32.GetForegroundWindow() or 0):
            # Never share input state with the thread the person is typing in.
            raise RuntimeError('the app is in the foreground')
        self.attached = bool(user32.AttachThreadInput(self.ours, self.theirs, True))
        self.old = (ctypes.c_ubyte * 256)()
        user32.GetKeyboardState(self.old)
        new = (ctypes.c_ubyte * 256)(*self.old)
        for generic, left in self.mods:
            new[generic] = new[left] = 0x80
        user32.SetKeyboardState(new)
        return self

    def __exit__(self, *exc):
        user32.SetKeyboardState(self.old)
        if self.attached:
            user32.AttachThreadInput(self.ours, self.theirs, False)
        return False


def _associated_command(path: str) -> tuple[str, str] | None:
    """(program, arguments) that opens ``path`` with its default app, or None.
    Packaged apps can't be started from WindowsApps; their execution alias can."""
    import shlex
    shlwapi = ctypes.WinDLL('shlwapi')
    size = wt.DWORD(2048)
    buf = ctypes.create_unicode_buffer(2048)
    ext = os.path.splitext(path)[1]
    if not ext or shlwapi.AssocQueryStringW(0, 1, ext, 'open', buf, ctypes.byref(size)) != 0:  # ASSOCSTR_COMMAND
        return None
    try:
        parts = shlex.split(buf.value, posix=False)
    except ValueError:
        return None
    if not parts:
        return None
    program = parts[0].strip('"')
    if '\\windowsapps\\' in program.lower():
        alias = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WindowsApps',
                             os.path.basename(program).lower())
        if not os.path.isfile(alias):
            return None
        program = alias
    args, used = [], False
    for part in parts[1:]:
        if part.strip('"') in ('%1', '%L', '%l'):
            args.append(f'"{path}"')
            used = True
        elif not part.startswith('%'):
            args.append(part)
    if not used:
        args.append(f'"{path}"')
    return program, ' '.join(args)


def _start_app_id(name: str) -> str | None:
    """Look an installed app up by its Start menu name (slow; last resort)."""
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-StartApps | ForEach-Object { $_.Name + "`t" + $_.AppID }'],
            capture_output=True, text=True, timeout=15, creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).stdout
    except Exception:
        return None
    rows = [line.split('\t', 1) for line in out.splitlines() if '\t' in line]
    want = name.lower()
    for app_name, app_id in rows:
        if app_name.lower() == want:
            return app_id
    for app_name, app_id in rows:
        if want in app_name.lower():
            return app_id
    return None
