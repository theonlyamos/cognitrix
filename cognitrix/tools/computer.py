"""Background computer use tools: work in app windows without taking over the
person's screen, keyboard or mouse. See cognitrix/computer for the driver."""
from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Any

from cognitrix.computer import (
    AGENT,
    PERSON,
    ActionResult,
    Actor,
    DriverError,
    Unsupported,
    current_actor,
    get_driver,
    which_computer,
)
from cognitrix.tools.misc import coords_normalized, queue_image
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome


def _actor(parent: Any) -> Actor:
    """The calling agent and chat, for the on-screen agent cursor and its Stop."""
    from cognitrix.tools.utils import current_execution_context

    ctx = current_execution_context()
    agent_id = getattr(parent, 'id', None) or ctx.agent_id
    return Actor(agent_id=str(agent_id) if agent_id else None, agent=getattr(parent, 'name', None),
                 session_id=ctx.session_id, user_id=ctx.user_id)


COMPUTER_NOTE = (
    '## Your computer\n'
    'You have your own computer: a Linux desktop with Chromium and Firefox that the person can watch. Do '
    'everything that needs a computer or a browser there, including visiting websites: open_app with the web '
    'address (for example open_app("https://x.com")), then look_at_window, click_element, type_in_window and '
    "the other window tools. The person's own computer is not yours to use: unless they explicitly ask you to "
    "use their computer or their browser, don't drive a browser or apps on it, so don't use skills or commands "
    'that do (such as agent-browser, Playwright or Puppeteer scripts run with Bash) or the full-screen tools. '
    "When they do ask, pass computer='person' to the window tools. Just reading a page's text with WebFetch "
    'is fine.')


def computer_note() -> str:
    """The system-prompt block about the agent's own computer, while it has one."""
    try:
        from cognitrix.computer.sandbox.client import sandbox_mode

        return COMPUTER_NOTE if sandbox_mode() else ''
    except Exception:
        return ''


class _Refused(Exception):
    def __init__(self, outcome):
        self.outcome = outcome


def _driver(actor: Actor, computer: str = AGENT):
    """The agent's desktop in the user's sandbox, or, only when asked for by
    name, the person's own computer (this machine)."""
    from cognitrix.computer.sandbox.client import sandbox_driver, sandbox_mode

    mode = sandbox_mode()
    if which_computer(computer) == PERSON:
        if mode == 'fly':
            raise _Refused(ToolOutcome.failure(
                'unsupported', "This server runs agents' computers in the cloud and can't reach the person's own "
                'computer. Tell them, and offer to do it on your computer instead.'))
        return get_driver()
    if not mode:
        raise _Refused(ToolOutcome.failure(
            'computer_off', "Your own computer (the sandboxed computer) is off, so there's nowhere to do this. Ask "
            'the person to turn it on in Settings, Agent access (it needs Docker Desktop). Only if they explicitly '
            "asked you to use their own computer, call again with computer='person'."))
    return sandbox_driver(actor)


def _run(fn, parent: Any = None, computer: str = AGENT):
    actor = _actor(parent)
    token = current_actor.set(actor)
    try:
        return fn(_driver(actor, computer))
    except _Refused as exc:
        return exc.outcome
    except Unsupported as exc:
        return ToolOutcome.failure('unsupported', str(exc))
    except DriverError as exc:
        return ToolOutcome.failure('computer_use', str(exc))
    finally:
        current_actor.reset(token)


def _done(result: ActionResult):
    return result.text()


def _viewer(parent: Any) -> Any:
    """Who is acting, so one agent's look can't be confused with another's."""
    if parent is None:
        return None
    return getattr(parent, 'id', None) or id(parent)


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
def list_windows(search: str | None = None, computer: str = AGENT):
    """List open app windows you can work in without taking over the person's screen.
    Each line starts with the window id to pass to the other window tools.
    Args:
        search (str, optional): Only windows whose title or app name contains this text.
        computer (str, optional): 'agent' (default) lists windows on your own sandboxed computer. 'person' lists
            the person's own computer, only when they explicitly asked you to work on their computer.
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
    return _run(go, computer=computer)


@tool(category='system')
def open_app(target: str, computer: str = AGENT):
    """Open an app, file or folder in the background, without taking focus, and return its window id.
    Args:
        target (str): An app name ("Notepad", "Calculator"), a web address ("https://x.com", opens in the
            browser), or the full path of a file or folder.
        computer (str, optional): 'agent' (default) opens it on your own sandboxed computer. 'person' opens it
            on the person's own computer, only when they explicitly asked you to work on their computer.
    """
    def go(driver):
        win = driver.open(target)
        if win is None:
            return (f'Opened {target!r}, but no new window appeared. Call list_windows to find it '
                    '(some apps reuse an existing window).')
        note = f' - can\'t control: {win.blocked}' if win.blocked else ''
        return f'Opened {target!r}: window {win.id} {win.title!r} ({win.app}){note}. Look at it with look_at_window.'
    return _run(go, computer=computer)


@tool(category='system')
def look_at_window(window_id: int, find: str | None = None, max_elements: int = 60, computer: str = AGENT, *, parent: Any | None = None):
    """Look at one app window: you get its screenshot (attached after this result, even if other windows
    cover it) and a numbered list of its controls. Use those numbers with click_element and set_text, which
    are more reliable than clicking pixels. Look again after acting, because numbers change.
    Args:
        window_id (int): Window id from list_windows or open_app.
        find (str, optional): Only list controls whose name or value contains this text.
        max_elements (int, optional): Most controls to list (default 60).
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    def go(driver):
        image, snap, shown, note = driver.look(window_id, find, max_elements, viewer=_viewer(parent))
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
    return _run(go, parent, computer)


@tool(category='system')
def click_element(window_id: int, element: int, action: str = 'click', use_screen: bool = False, computer: str = AGENT, *,
                  parent: Any | None = None):
    """Act on a control from your latest look_at_window of that window, in the background.
    action "click" presses buttons and links and opens list items; use "select" to only select an item,
    "toggle" for checkboxes, "expand" to open or close menus, dropdowns and tree nodes.
    Args:
        window_id (int): Window id.
        element (int): Control number from look_at_window.
        action (str, optional): click, select, toggle or expand. Default click.
        use_screen (bool, optional): Only after a result said this needs the screen: bring the window to the front
            for a moment and use the real mouse and keyboard, then put things back. The person is asked first.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    return _run(lambda d: _done(d.act_on_element(window_id, element, action, viewer=_viewer(parent),
                                                 use_screen=use_screen)), parent, computer)


@tool(category='system')
def set_text(window_id: int, text: str, element: int | None = None, computer: str = AGENT, *, parent: Any | None = None):
    """Put text into a text field in the background, replacing what it holds. Prefer this over typing.
    Args:
        window_id (int): Window id.
        text (str): The full text the field should contain.
        element (int, optional): Control number of the field from look_at_window; default is the focused field.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    return _run(lambda d: _done(d.set_text(window_id, text, element, viewer=_viewer(parent))), parent, computer)


@tool(category='system')
def click_in_window(window_id: int, x: int, y: int, button: str = 'left', double: bool = False,
                    use_screen: bool = False, computer: str = AGENT, *, parent: Any | None = None):
    """Click a point in a window's latest look_at_window screenshot, in the background. Use click_element
    when the target is in the control list.
    Args:
        window_id (int): Window id.
        x (int): X in the window screenshot.
        y (int): Y in the window screenshot.
        button (str, optional): left or right. Default left.
        double (bool, optional): Double-click. Default false.
        use_screen (bool, optional): Only after a result said this needs the screen: bring the window to the front
            for a moment and use the real mouse and keyboard, then put things back. The person is asked first.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    def go(driver):
        ix, iy = _image_point(driver, window_id, x, y)
        return _done(driver.click_point(window_id, ix, iy, button, double, viewer=_viewer(parent),
                                        use_screen=use_screen))
    return _run(go, parent, computer)


@tool(category='system')
def type_in_window(window_id: int, text: str, x: int | None = None, y: int | None = None,
                   use_screen: bool = False, computer: str = AGENT, *, parent: Any | None = None):
    """Type text into a window in the background, at its focused field or after clicking (x, y) in its
    screenshot. Use set_text instead when the field is in the control list. Browsers and Electron apps need
    use_screen=true to type; end the text with a new line (or press_key_in_window "enter") to submit.
    Args:
        window_id (int): Window id.
        text (str): Text to type.
        x (int, optional): Click here first (screenshot coordinates) to focus the field.
        y (int, optional): Click here first (screenshot coordinates) to focus the field.
        use_screen (bool, optional): Only after a result said this needs the screen: bring the window to the front
            for a moment and use the real mouse and keyboard, then put things back. The person is asked first.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    def go(driver):
        ix = iy = None
        if x is not None and y is not None:
            ix, iy = _image_point(driver, window_id, x, y)
        return _done(driver.type_text(window_id, text, ix, iy, viewer=_viewer(parent), use_screen=use_screen))
    return _run(go, parent, computer)


@tool(category='system')
def press_key_in_window(window_id: int, keys: str, use_screen: bool = False, computer: str = AGENT, *, parent: Any | None = None):
    """Press a key or shortcut in a window in the background, e.g. "enter", "tab", "down", "ctrl+s", "ctrl+w".
    Prefer a shortcut over opening a menu (menus in modern apps take the screen). Browsers and Electron apps
    need use_screen=true for keys; clicking their controls works in the background.
    Args:
        window_id (int): Window id.
        keys (str): Key name, or keys joined with + for a shortcut.
        use_screen (bool, optional): Only after a result said this needs the screen: bring the window to the front
            for a moment and use the real mouse and keyboard, then put things back. The person is asked first.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    return _run(lambda d: _done(d.press_key(window_id, keys, use_screen=use_screen)), parent, computer)


@tool(category='system')
def scroll_window(window_id: int, direction: str = 'down', amount: int = 3, element: int | None = None, computer: str = AGENT, *,
                  parent: Any | None = None):
    """Scroll a window, or one of its scrollable controls, in the background.
    Args:
        window_id (int): Window id.
        direction (str, optional): up, down, left or right. Default down.
        amount (int, optional): Steps to scroll (default 3).
        element (int, optional): Control number of the area to scroll; default is the main scrollable area.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    return _run(lambda d: _done(d.scroll(window_id, direction, amount, element, viewer=_viewer(parent))), parent, computer)


TAKEOVER_WAIT_S = 30 * 60  # how long the agent waits for the person to take over and hand back
_TAKEOVER_POLL_S = 1.0


def _desktop_held_by(driver: Any) -> str | None:
    """Who has control of the agent's desktop right now (None: the agent)."""
    for desk in driver.box.desktops():
        if desk.get('name') == driver.desktop:
            return desk.get('held_by') or None
    return None


async def wait_for_hand_back(held_by: Any, *, wait_s: float = TAKEOVER_WAIT_S,
                             poll_s: float = _TAKEOVER_POLL_S) -> str:
    """Wait until the person has taken over the desktop and handed it back.

    Returns 'handed_back', 'not_taken' (nobody took over in time) or
    'still_held' (taken over, not handed back in time). ``held_by`` is a
    blocking call returning who holds the desktop; a failed check counts as
    no change.
    """
    deadline = time.monotonic() + wait_s
    taken = False
    while True:
        try:
            holder = await asyncio.to_thread(held_by)
        except Exception:
            holder = 'unknown' if taken else None
        if holder:
            taken = True
        elif taken:
            return 'handed_back'
        if time.monotonic() >= deadline:
            return 'still_held' if taken else 'not_taken'
        await asyncio.sleep(poll_s)


@tool(category='system')
async def ask_to_take_over(reason: str, *, parent: Any | None = None):
    """Ask the person to take over your computer for a step only they should do: signing in, a 2FA code, a
    CAPTCHA, a payment or anything private. They see your reason with a Take over button, do it on your screen,
    and this returns once they hand the computer back (or say they'll skip it). Only for the sandboxed computer.
    Args:
        reason (str): What you need them to do, in one short sentence, e.g. "Sign in to your bank".
    """
    from cognitrix.computer.sandbox.client import sandbox_mode

    if not sandbox_mode():
        return ToolOutcome.failure('unsupported', 'This only works on the sandboxed computer. Ask the person in '
                                   'the chat instead.')
    # Runs only after the person pressed Take over on the request (see
    # destructive_ops, category 'person_takeover'; Skip refuses it). Now wait
    # for them to actually take control and hand it back.
    actor = _actor(parent)
    token = current_actor.set(actor)
    try:
        driver = await asyncio.to_thread(_driver, actor, AGENT)
    except _Refused as exc:
        return exc.outcome
    except DriverError as exc:
        return ToolOutcome.failure('computer_use', str(exc))
    finally:
        current_actor.reset(token)
    outcome = await wait_for_hand_back(lambda: _desktop_held_by(driver))
    minutes = int(TAKEOVER_WAIT_S // 60)
    if outcome == 'not_taken':
        return ToolOutcome.failure(
            'not_taken_over', f"The person didn't take over your computer within {minutes} minutes. Tell them in the "
            "chat what you need, and that they can press Take over on your computer when they're ready.")
    if outcome == 'still_held':
        return ToolOutcome.failure(
            'still_held', f"The person took over but hasn't handed the computer back after {minutes} minutes. "
            "Don't use the computer until they do; tell them in the chat you're waiting.")
    return ('The person handed the computer back. Look at the window again (look_at_window) before you '
            'continue, because things may have changed.')
