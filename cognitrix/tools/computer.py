"""Background computer use tools: work in app windows without taking over the
person's screen, keyboard or mouse. See cognitrix/computer for the driver."""
from __future__ import annotations

import asyncio
import base64
import io
import re
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
    'is fine. Only when the person asks to see your screen, or a picture of it is the answer (how a page '
    'you made looks), show them one with show_screenshot.\n\n'
    'Work on your own and do as much of the task as you can before involving the person. When a site or app '
    'needs an email, username, password, API key or token, never ask the person to take over or to type it: '
    'call list_credentials and type a saved one with type_in_window or set_text, writing '
    '{{secret:NAME.username}}, {{secret:NAME.password}} or {{secret:NAME}} as the text. It is filled in only on '
    "the sites it was saved for, and you never see it. If none fits, ask for it with ask_for_sign_in "
    "(kind='basic' for a username and password, allowed_hosts=[the site, e.g. 'github.com']), then type it the "
    'same way. Use ask_to_take_over only for a step you truly cannot do yourself: a CAPTCHA you cannot pass, a '
    "code sent to the person's phone, a passkey or security key, or approving something in their bank or "
    'authenticator app. Say exactly what they need to do, and carry on with the rest of the task once they '
    'hand back.')


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


def _with_saved_sign_ins(driver, window_id: int, text: str, parent: Any):
    """``text`` with saved sign-ins filled in for this window, or a refusal
    (cognitrix/computer/saved_sign_ins.py)."""
    from cognitrix.computer import saved_sign_ins
    from cognitrix.credentials import agent_allowed_credentials
    from cognitrix.tools.utils import current_execution_context

    if not saved_sign_ins.has_secrets(text):
        return text
    owner = current_execution_context().user_id
    try:
        return saved_sign_ins.prepare(driver, window_id, text, owner=owner,
                                      allowed=agent_allowed_credentials(parent), viewer=_viewer(parent))
    except saved_sign_ins.SignInRefused as exc:
        return ToolOutcome.failure('sign_in', str(exc))


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
            elif w.own:
                line += ' - this is Cognitrix itself (the app you run in); the person approves each action in it'
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


SIGN_IN_HINT = (
    'This window asks for a sign-in. Do it yourself: call list_credentials and type the saved one with '
    '{{secret:NAME.username}} and {{secret:NAME.password}} (type_in_window or set_text). If none fits, ask for '
    "it with ask_for_sign_in (kind='basic', allowed_hosts=[this site]). Don't ask the person to take over for "
    'this.')


def _asks_for_password(elements) -> bool:
    """Whether a window's controls include a password field."""
    for el in elements or []:
        role = str(getattr(el, 'role', '') or '').lower()
        if role in ('password text', 'password', 'passwordbox'):
            return True
        if role in ('entry', 'text', 'textbox', 'edit') and 'password' in str(getattr(el, 'name', '') or '').lower():
            return True
    return False

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
        if getattr(driver, 'sandbox', False) and _asks_for_password(snap.elements):
            lines.append(SIGN_IN_HINT)
        return '\n'.join(lines)
    return _run(go, parent, computer)


@tool(category='system')
async def show_screenshot(window_id: int, caption: str = '', computer: str = AGENT, *, parent: Any | None = None):
    """Show the person a screenshot of one window on your computer, as an image in the chat. Use it only when
    they ask to see your screen (or a screenshot), or when the picture itself answers what they asked, such as
    how a page or an app you made looks. For your own checks use look_at_window, which only you see.
    Args:
        window_id (int): Window id from list_windows or open_app.
        caption (str, optional): One short line shown under the image.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    from cognitrix.produced_files import service
    from cognitrix.produced_files.sources import SourceError
    from cognitrix.tools.utils import current_execution_context

    on_pc = bool(getattr(current_execution_context(), 'device', None))
    title, controls = '', ''
    if on_pc:
        # The chat works on a paired PC: the window ids are that PC's. Its
        # look also refreshes the control numbers there, so the agent gets
        # the new list back with the picture.
        from cognitrix.computer.paired.turn import look_on_pc

        looked = await look_on_pc(window_id)
        if isinstance(looked, str):
            return ToolOutcome.failure('computer_use', looked)
        controls, png = looked
        if png is None:
            return ToolOutcome.failure('no_image', controls or f"Couldn't take a screenshot of window {window_id}.")
        from PIL import Image
        image = Image.open(io.BytesIO(png))
    else:
        actor = _actor(parent)

        def grab():
            token = current_actor.set(actor)
            try:
                driver = _driver(actor, computer)
                # Looked at under a viewer of its own, so the agent's own
                # look stays its latest: if this one replaced the window's
                # controls, the agent's next action is refused as out of date
                # (look again) instead of using an old number on a new control.
                image = driver.look(window_id, None, 1, viewer=('show_screenshot', _viewer(parent)))[0]
                win = driver.backend.window(int(window_id))
                return image, (win.title if win else '')
            finally:
                current_actor.reset(token)

        try:
            image, title = await asyncio.to_thread(grab)
        except _Refused as exc:
            return exc.outcome
        except Unsupported as exc:
            return ToolOutcome.failure('unsupported', str(exc))
        except DriverError as exc:
            return ToolOutcome.failure('computer_use', str(exc))
    if image is None:
        return ToolOutcome.failure('no_image', f"Couldn't take a screenshot of window {window_id}.")
    buf = io.BytesIO()
    image.convert('RGB').save(buf, format='PNG')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    try:
        row = await service.share_image(buf.getvalue(), f'screenshot-{stamp}.png', caption)
    except SourceError as exc:
        return ToolOutcome.failure('not_shared', str(exc))
    what = f' of {title!r}' if title else ''
    text = f'Showed the person a screenshot{what} ({image.size[0]}x{image.size[1]}).'
    if controls:
        text += f'\nThe window as it is now (use these numbers from here on):\n{controls}'
    else:
        text += ' Look at the window again (look_at_window) before you click or type in it.'
    return ToolOutcome.success(text, files=[service.ref(row)])


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
        text (str): The full text the field should contain. For a sign-in write {{secret:NAME.username}},
            {{secret:NAME.password}} or {{secret:NAME}} (see list_credentials); it is filled in only on the
            sites that sign-in is saved for, and you never see the value.
        element (int, optional): Control number of the field from look_at_window; default is the focused field.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    def go(driver):
        filled = _with_saved_sign_ins(driver, window_id, text, parent)
        if isinstance(filled, ToolOutcome):
            return filled
        return _done(driver.set_text(window_id, filled, element, viewer=_viewer(parent)))
    return _run(go, parent, computer)


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
        text (str): Text to type. For a sign-in write {{secret:NAME.username}}, {{secret:NAME.password}} or
            {{secret:NAME}} (see list_credentials); it is filled in only on the sites that sign-in is saved for,
            and you never see the value.
        x (int, optional): Click here first (screenshot coordinates) to focus the field.
        y (int, optional): Click here first (screenshot coordinates) to focus the field.
        use_screen (bool, optional): Only after a result said this needs the screen: bring the window to the front
            for a moment and use the real mouse and keyboard, then put things back. The person is asked first.
        computer (str, optional): Same as for list_windows: 'agent' (default) or 'person'.
    """
    def go(driver):
        # Saved sign-ins first: reading the page's address looks at the
        # window again, and the click point below uses the latest look.
        filled = _with_saved_sign_ins(driver, window_id, text, parent)
        if isinstance(filled, ToolOutcome):
            return filled
        ix = iy = None
        if x is not None and y is not None:
            ix, iy = _image_point(driver, window_id, x, y)
        return _done(driver.type_text(window_id, filled, ix, iy, viewer=_viewer(parent), use_screen=use_screen))
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


async def wait_for_hand_back(held_by: Any, *, handed_back: Any = None, wait_s: float = TAKEOVER_WAIT_S,
                             poll_s: float = _TAKEOVER_POLL_S) -> str:
    """Wait until the person has taken over the desktop and handed it back.

    Returns 'handed_back', 'not_taken' (nobody took over in time) or
    'still_held' (taken over, not handed back in time). ``held_by`` is a
    blocking call returning who holds the desktop; a failed check counts as
    no change. ``handed_back`` says whether a hand back was recorded since the
    request, which catches a take over that ended before (or between) checks.
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
        elif taken or (handed_back is not None and handed_back()):
            return 'handed_back'
        if time.monotonic() >= deadline:
            return 'still_held' if taken else 'not_taken'
        await asyncio.sleep(poll_s)


# Ask To Take Over is a last resort. A request that is only about typing a
# sign-in (an email, a password, a key) is sent back once per chat with how
# to do it with saved sign-ins; asked again, it goes to the person.
_SIGN_IN_WORDS = re.compile(
    r'\b(sign(ed|ing)?[ -]?in|log(ged|ging)?[ -]?in|password|passcode|credentials?|e-?mail|username|user name|'
    r'api[ -]?key|access key|token|secret key)\b', re.I)
_ONLY_THE_PERSON = re.compile(
    r'(captcha|recaptcha|hcaptcha|i.?m not a robot|robot|2fa|two[- ]factor|2[- ]step|mfa|multi[- ]factor|'
    r'verif|one[- ]time|otp|\bcode\b|passkey|security key|yubikey|authenticator|phone|sms|text message|'
    r'push notification|approve|face ?id|fingerprint|biometric|payment|pay\b|card|bank|identity|selfie|'
    r'\bid\b|consent|legal|terms|agree)', re.I)
_TAKEOVER_SENT_BACK: dict[str, float] = {}
_SENT_BACK_KEEP_S = 6 * 3600


def takeover_precheck(args: dict[str, Any], session_id: Any) -> str | None:
    """A reply instead of a Take over card, for a request that is only about
    typing a sign-in the agent can type itself. None: ask the person."""
    reason = str((args or {}).get('reason') or '')
    if not _SIGN_IN_WORDS.search(reason) or _ONLY_THE_PERSON.search(reason):
        return None
    now = time.monotonic()
    for key, when in list(_TAKEOVER_SENT_BACK.items()):
        if now - when > _SENT_BACK_KEEP_S:
            _TAKEOVER_SENT_BACK.pop(key, None)
    key = str(session_id or '')
    if not key or key in _TAKEOVER_SENT_BACK:
        return None  # it tried already (or there's no chat to remember it by): the person's call
    _TAKEOVER_SENT_BACK[key] = now
    return ('The person was not asked. Sign in yourself instead: call list_credentials, and if a saved sign-in '
            'fits this site, type it with type_in_window or set_text using {{secret:NAME.username}} and '
            "{{secret:NAME.password}} (or {{secret:NAME}} for a key). If none fits, call ask_for_sign_in "
            "(kind='basic' for a username and password, allowed_hosts=[the site's host]) so the person saves it "
            'without taking over, then type it. Only if the site then needs something only the person can do (a '
            "CAPTCHA, a code sent to their phone, a passkey), call ask_to_take_over again and say exactly that.")

# Waits on a person, so it holds no execution slot (like Ask User).
@tool(category='system', occupies_execution_slot=False)
async def ask_to_take_over(reason: str, *, parent: Any | None = None):
    """Last resort: ask the person to take over your computer for a step only they can do, such as a CAPTCHA
    you can't pass, a code sent to their phone, a passkey or security key, or approving something in their bank
    or authenticator app. Don't use it for typing an email, username, password, API key or token: type a saved
    sign-in with {{secret:NAME...}} (list_credentials), or ask for one with ask_for_sign_in. Do every step you
    can first. They see your reason with a Take over button, do it on your screen, and this returns once they
    hand the computer back (or say they'll skip it); then carry on. Only for the sandboxed computer.
    Args:
        reason (str): Exactly what you need them to do, in one short sentence, e.g. "Solve the CAPTCHA on the
            GitHub sign-in page".
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
    from cognitrix.computer.sandbox import handed_back_at, takeover_asked_at

    asked = takeover_asked_at(driver.desktop) or time.monotonic()

    def handed_back() -> bool:
        when = handed_back_at(driver.desktop)
        return when is not None and when >= asked

    outcome = await wait_for_hand_back(lambda: _desktop_held_by(driver), handed_back=handed_back,
                                       poll_s=_TAKEOVER_POLL_S)
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
