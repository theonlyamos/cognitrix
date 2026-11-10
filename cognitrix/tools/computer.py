"""Background computer use tools: work in app windows without taking over the
person's screen, keyboard or mouse. See cognitrix/computer for the driver."""
from __future__ import annotations

import base64
import io

from cognitrix.computer import ActionResult, DriverError, Unsupported, get_driver
from cognitrix.tools.misc import coords_normalized, queue_image
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome


def _run(fn):
    try:
        return fn(get_driver())
    except Unsupported as exc:
        return ToolOutcome.failure('unsupported', str(exc))
    except DriverError as exc:
        return ToolOutcome.failure('computer_use', str(exc))


def _done(result: ActionResult):
    return result.text()


def _image_point(driver, window_id: int, x: float, y: float) -> tuple[float, float]:
    """Model coordinates -> pixels of the image it was shown."""
    if coords_normalized.get():
        snap = driver._snapshots.get(int(window_id))
        if snap is None:
            raise DriverError('Look at the window first (look_at_window).')
        w, h = snap.size[0] / snap.scale, snap.size[1] / snap.scale
        return float(x) / 1000 * w, float(y) / 1000 * h
    return float(x), float(y)


@tool(category='system')
def list_windows(search: str | None = None):
    """List open app windows you can work in without taking over the person's screen.
    Each line starts with the window id to pass to the other window tools.
    Args:
        search (str, optional): Only windows whose title or app name contains this text.
    """
    def go(driver):
        windows = driver.list_windows(search)
        if not windows:
            return 'No matching windows are open.'
        lines = []
        for w in windows:
            line = f'{w.id}: {w.title!r} ({w.app})'
            if w.minimized:
                line += ' minimized'
            if w.blocked:
                line += f' - can\'t control: {w.blocked}'
            lines.append(line)
        return '\n'.join(lines)
    return _run(go)


@tool(category='system')
def open_app(target: str):
    """Open an app, file or folder in the background, without taking focus, and return its window id.
    Args:
        target (str): An app name ("Notepad", "Calculator"), or the full path of a file or folder.
    """
    def go(driver):
        win = driver.open(target)
        if win is None:
            return (f'Opened {target!r}, but no new window appeared. Call list_windows to find it '
                    '(some apps reuse an existing window).')
        note = f' - can\'t control: {win.blocked}' if win.blocked else ''
        return f'Opened {target!r}: window {win.id} {win.title!r} ({win.app}){note}. Look at it with look_at_window.'
    return _run(go)


@tool(category='system')
def look_at_window(window_id: int, find: str | None = None, max_elements: int = 60):
    """Look at one app window: you get its screenshot (attached after this result, even if other windows
    cover it) and a numbered list of its controls. Use those numbers with click_element and set_text, which
    are more reliable than clicking pixels. Look again after acting, because numbers change.
    Args:
        window_id (int): Window id from list_windows or open_app.
        find (str, optional): Only list controls whose name or value contains this text.
        max_elements (int, optional): Most controls to list (default 60).
    """
    def go(driver):
        image, snap, shown, note = driver.look(window_id, find, max_elements)
        win = driver.backend.window(int(window_id))
        title = win.title if win else ''
        lines = [f'Window {window_id} {title!r}, snapshot {snap.id}.']
        if note:
            lines.append(note)
        if image is not None:
            buf = io.BytesIO()
            image.convert('RGB').save(buf, format='PNG')
            if queue_image('data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()):
                if coords_normalized.get():
                    lines.append('Screenshot attached. Give x and y for click_in_window on a 0-1000 grid of it.')
                else:
                    lines.append(f'Screenshot attached: {image.size[0]}x{image.size[1]} pixels. '
                                 'Give click_in_window coordinates in its pixels.')
            else:
                lines.append('(This chat cannot show images; use the control list.)')
        total = len(snap.elements)
        lines.append(f'Controls ({len(shown)} of {total}' + (f' matching {find!r}' if find else '') + '):')
        for index, el in shown:
            cx = (el.rect[0] + el.rect[2]) / 2 - snap.origin[0]
            cy = (el.rect[1] + el.rect[3]) / 2 - snap.origin[1]
            at = ''
            if 0 <= cx < snap.size[0] and 0 <= cy < snap.size[1]:
                px, py = cx / snap.scale, cy / snap.scale
                if coords_normalized.get():
                    px, py = px * 1000 / (snap.size[0] / snap.scale), py * 1000 / (snap.size[1] / snap.scale)
                at = f' at ({round(px)},{round(py)})'
            lines.append(f'[{index}] {el.describe()}{at}')
        if total and not shown:
            lines.append('(none match; try without find)')
        elif not total:
            lines.append('(no readable controls; use the screenshot and click_in_window)')
        return '\n'.join(lines)
    return _run(go)


@tool(category='system')
def click_element(window_id: int, element: int, action: str = 'click'):
    """Act on a control from your latest look_at_window of that window, in the background.
    action "click" presses buttons and links and opens list items; use "select" to only select an item,
    "toggle" for checkboxes, "expand" to open or close menus, dropdowns and tree nodes.
    Args:
        window_id (int): Window id.
        element (int): Control number from look_at_window.
        action (str, optional): click, select, toggle or expand. Default click.
    """
    return _run(lambda d: _done(d.act_on_element(window_id, element, action)))


@tool(category='system')
def set_text(window_id: int, text: str, element: int | None = None):
    """Put text into a text field in the background, replacing what it holds. Prefer this over typing.
    Args:
        window_id (int): Window id.
        text (str): The full text the field should contain.
        element (int, optional): Control number of the field from look_at_window; default is the focused field.
    """
    return _run(lambda d: _done(d.set_text(window_id, text, element)))


@tool(category='system')
def click_in_window(window_id: int, x: int, y: int, button: str = 'left', double: bool = False):
    """Click a point in a window's latest look_at_window screenshot, in the background. Use click_element
    when the target is in the control list.
    Args:
        window_id (int): Window id.
        x (int): X in the window screenshot.
        y (int): Y in the window screenshot.
        button (str, optional): left or right. Default left.
        double (bool, optional): Double-click. Default false.
    """
    def go(driver):
        ix, iy = _image_point(driver, window_id, x, y)
        return _done(driver.click_point(window_id, ix, iy, button, double))
    return _run(go)


@tool(category='system')
def type_in_window(window_id: int, text: str, x: int | None = None, y: int | None = None):
    """Type text into a window in the background, at its focused field or after clicking (x, y) in its
    screenshot. Use set_text instead when the field is in the control list. If the result says the app needs
    the screen, ask the person before using text_input, which takes over their keyboard.
    Args:
        window_id (int): Window id.
        text (str): Text to type.
        x (int, optional): Click here first (screenshot coordinates) to focus the field.
        y (int, optional): Click here first (screenshot coordinates) to focus the field.
    """
    def go(driver):
        ix = iy = None
        if x is not None and y is not None:
            ix, iy = _image_point(driver, window_id, x, y)
        return _done(driver.type_text(window_id, text, ix, iy))
    return _run(go)


@tool(category='system')
def press_key_in_window(window_id: int, keys: str):
    """Press a key or shortcut in a window in the background, e.g. "enter", "tab", "down", "ctrl+s", "ctrl+w".
    Prefer a shortcut over opening a menu (menus in modern apps take the screen). Browsers and Electron apps
    don't take keys in the background; click their controls instead.
    Args:
        window_id (int): Window id.
        keys (str): Key name, or keys joined with + for a shortcut.
    """
    return _run(lambda d: _done(d.press_key(window_id, keys)))


@tool(category='system')
def scroll_window(window_id: int, direction: str = 'down', amount: int = 3, element: int | None = None):
    """Scroll a window, or one of its scrollable controls, in the background.
    Args:
        window_id (int): Window id.
        direction (str, optional): up, down, left or right. Default down.
        amount (int, optional): Steps to scroll (default 3).
        element (int, optional): Control number of the area to scroll; default is the main scrollable area.
    """
    return _run(lambda d: _done(d.scroll(window_id, direction, amount, element)))
