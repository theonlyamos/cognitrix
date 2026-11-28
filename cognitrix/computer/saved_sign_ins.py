"""Typing a saved sign-in into a window on the agent's own computer.

An agent signing in to a site in its sandbox browser writes
``{{secret:NAME.username}}`` / ``{{secret:NAME.password}}`` (or
``{{secret:NAME}}`` for a token) in Type In Window or Set Text, one field per
call. The value is filled in here, right before it is typed, so it isn't in
the model's tool call, and the result the model gets back doesn't show it:
the typing result is replaced, filled fields are masked in later looks
(``tools/computer.py``) and every tool result is scrubbed of saved values and
their cut-off copies (``agents/base._hide_saved_secrets``).

Where a value may go:

- a browser window: only into a page text field (never the address bar)
  whose kind fits (a password only into a password field, a username never
  into one), on a site the person allowed that sign-in to be typed on
  (``Credential.browser_hosts``: exact hosts, ``*.example.com`` for
  subdomains, a port only where one was saved). The site is the one the
  browser itself reports for that field's own page or frame (the driver's
  ``_page_url``, see ``computer/linux.py``), which must be https; a frame from
  another site counts as that site. When the browser doesn't say, nothing is
  typed. The field must still be the one the agent picked in its own last
  look.
- any other window on the agent's computer: only a sign-in saved just for
  commands in the sandbox (Ask For Sign-in without sites), which commands
  there can already read. One that also has sites is not typed there.
- never the person's own computer.

Limits: a typed value is in the sandbox's browser, so other code on the
agent's computer could read it; this guards against sending it to the wrong
place and against showing it to the model by accident, not against an agent
set on reading it there. The field is checked right before typing, not at the
instant each key lands.

All driver calls block: run these off the event loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from cognitrix.credentials import PLACEHOLDER_RE, Resolver, SecretUseError, browser_sites, host_allowed

# Hosts for a sign-in saved only for commands in the sandbox (sandbox_work.SANDBOX_ONLY_HOST).
SANDBOX_ONLY_HOST = 'sandbox.invalid'
BROWSER_APPS = ('chrom', 'firefox', 'brave', 'msedge', 'microsoft-edge', 'opera', 'vivaldi', 'epiphany',
                'web browser', 'qutebrowser', 'midori', 'falkon', 'librewolf', 'waterfox')
# The page address the driver reads for a field (computer/linux.py).
PAGE_URL = '_page_url'


class SignInRefused(Exception):
    """Nothing was typed; the message says what to do instead."""


def has_secrets(text: Any) -> bool:
    return isinstance(text, str) and bool(PLACEHOLDER_RE.search(text))


def is_browser(win: Any) -> bool:
    if win is None:
        return False
    return getattr(win, 'kind', '') == 'chromium' or any(b in (getattr(win, 'app', '') or '').lower()
                                                         for b in BROWSER_APPS)


# Fields a sign-in can go into. Not the page itself ('document web'): typing
# there lands wherever focus happens to be.
TEXT_ROLES = ('entry', 'text', 'password text', 'password', 'passwordbox', 'textbox', 'edit', 'combo box',
              'spin button')
PASSWORD_ROLES = ('password text', 'password', 'passwordbox')


def _norm(text: Any) -> str:
    return ' '.join(str(text or '').split()).lower()


def _same(a: Any, b: Any) -> bool:
    return (str(getattr(a, 'role', '')).lower(), _norm(getattr(a, 'name', ''))) == \
        (str(getattr(b, 'role', '')).lower(), _norm(getattr(b, 'name', '')))


def _is_address_bar(el: Any) -> bool:
    from cognitrix.payments.checkout import ADDRESS_BAR
    name = _norm(getattr(el, 'name', ''))
    return any(label in name for label in ADDRESS_BAR)


def _field_at(snap: Any, point: tuple[float, float]) -> Any:
    """The smallest text field under an image point of ``snap``."""
    sx = snap.origin[0] + point[0] * (snap.scale or 1.0)
    sy = snap.origin[1] + point[1] * (snap.scale or 1.0)
    hits = [e for e in snap.elements if e.rect[0] <= sx < e.rect[2] and e.rect[1] <= sy < e.rect[3]
            and str(e.role).lower() in TEXT_ROLES]
    return min(hits, key=lambda e: (e.rect[2] - e.rect[0]) * (e.rect[3] - e.rect[1])) if hits else None


# One saved value per call, optionally followed by a new line to submit. A
# tab or a second value could move focus to another field after the check.
_ONE_VALUE = re.compile(PLACEHOLDER_RE.pattern + r'\r?\n?')


def one_placeholder(text: str) -> bool:
    """Whether ``text`` is exactly one sign-in placeholder, maybe followed by
    a new line. In a browser nothing else may travel with a saved value, so
    it can't be built into an address or spill into another field."""
    return bool(_ONE_VALUE.fullmatch(text or ''))


def page_of(el: Any) -> str:
    """The address of the page (or frame) a field is on, as the browser
    reports it, without the query and fragment; '' when it doesn't say."""
    raw = str((getattr(el, 'state', None) or {}).get(PAGE_URL) or '').strip()
    if not raw:
        return ''
    parts = urlsplit(raw)
    return f'{parts.scheme}://{parts.netloc}{parts.path}'.lower() if parts.scheme else ''


def site_of_page(url: str) -> str:
    """``host`` or ``host:port`` of an https page address, else ''."""
    try:
        parts = urlsplit(str(url or '').strip())
        port = parts.port
    except ValueError:
        return ''
    if parts.scheme.lower() != 'https' or not parts.hostname:
        return ''
    host = parts.hostname.lower().rstrip('.')
    return f'{host}:{port}' if port and port != 443 else host


@dataclass
class Target:
    """The page field a saved value goes into, and the site of its page."""
    host: str
    role: str
    name: str
    page: str
    element: Any


def _own_snapshot(driver: Any, window_id: int, viewer: Any) -> Any:
    """The agent's own last look at the window, refused when someone else
    has looked since (their control numbers and positions differ)."""
    before = (getattr(driver, '_snapshots', None) or {}).get(int(window_id))
    if before is None:
        raise SignInRefused('Look at the window first (look_at_window), then type the saved sign-in.')
    seen = getattr(driver, '_seen', None)
    if viewer is not None and isinstance(seen, dict) and seen.get((viewer, int(window_id))) != before.id:
        raise SignInRefused('The window was looked at again since your last look, so nothing was typed. Look at it '
                            'again and use the new control number or position.')
    return before


def check_page(driver: Any, window_id: int, viewer: Any, *, element: int | None = None,
               point: tuple[float, float] | None = None) -> Target:
    """Look at the browser window again and return the field the agent meant
    and the site of its page, after checking it is still the same field.

    ``element`` is a control number from the agent's last look, ``point`` an
    image point of it. The fresh look renumbers controls, so a field whose
    number or position now means something else is refused, as is the
    address bar, anything that isn't a text field, and a field whose page the
    browser doesn't report or that isn't on https."""
    from cognitrix.payments.checkout import _look

    if element is None and point is None:
        raise SignInRefused('To type a saved sign-in in the browser, say which field: pass the field\'s control '
                            'number (set_text element) or click point (type_in_window x and y).')
    before = _own_snapshot(driver, window_id, viewer)
    if element is not None and not 0 <= int(element) < len(before.elements):
        raise SignInRefused(f'There is no control {element}. Look at the window again.')
    _look(driver, int(window_id), viewer)
    after = (getattr(driver, '_snapshots', None) or {}).get(int(window_id))
    if element is not None:
        old = before.elements[int(element)]
        new = after.elements[int(element)] if after and int(element) < len(after.elements) else None
        if new is None or not _same(old, new) or page_of(old) != page_of(new):
            raise SignInRefused('The window changed since you looked, so nothing was typed. Look at it again and '
                                'use the new control number.')
        target = new
    else:
        old = _field_at(before, point)
        target = _field_at(after, point) if after else None
        if target is None or (old is not None and (not _same(old, target) or page_of(old) != page_of(target))) or \
                tuple(before.origin) != tuple(after.origin) or before.scale != after.scale:
            raise SignInRefused('There is no text field at that point now, or the window moved. Look at the window '
                                'again and pass the field\'s position.')
    if _is_address_bar(target):
        raise SignInRefused("Saved sign-ins can't be typed into the browser's address bar.")
    if str(getattr(target, 'role', '')).lower() not in TEXT_ROLES:
        raise SignInRefused('That control is not a text field. Pick the sign-in field.')
    page = page_of(target)
    if not page:
        raise SignInRefused(
            "The browser didn't say which site this field is on, so the saved sign-in wasn't typed. Make sure the "
            'sign-in page has finished loading and try again; if it still fails, your computer may need an update: '
            'ask the person to type it (ask_to_take_over).')
    host = site_of_page(page)
    if not host:
        raise SignInRefused('This field is on a page that is not https, so a saved sign-in is not typed into it. '
                            'Open the https address of the site.')
    return Target(host=host, role=str(target.role).lower(), name=_norm(target.name), page=page, element=target)


def _site_hosts(row: Any) -> list[str]:
    return [h for h in (getattr(row, 'allowed_hosts', None) or []) if h and h != SANDBOX_ONLY_HOST]


def fill(resolver: Resolver, text: str, *, browser: bool, host: str = '', field_role: str = '') -> str:
    """``text`` with every placeholder replaced, or SignInRefused.
    ``field_role`` is the browser field's role, to match the part to it."""
    def one(match) -> str:
        name, part = match.group(1), match.group(2)
        row = resolver.rows.get(name) or resolver.rows.get(name.upper())
        if row is None:
            raise SignInRefused(
                f'There is no saved sign-in called {name} that you may use. Call List Credentials to see the '
                'names, or ask the person for it with ask_for_sign_in.')
        if browser:
            if not host:
                raise SignInRefused(
                    "Couldn't tell which site this field is on, so the saved sign-in wasn't typed. Make sure the "
                    'sign-in page is open in this window (not a blank tab or a pop-up) and try again.')
            ask = (f"ask_for_sign_in, name='{row.name}', kind='{row.kind}', allowed_hosts=['{host}'], "
                   'for_browser=True (the person types it once more)')
            typed_on = browser_sites(row)
            if not typed_on:
                raise SignInRefused(
                    f"{row.name} isn't allowed in the browser. Ask for it with {ask}, or ask the person to turn on "
                    '"Agents may type it into sign-in pages" for it in Settings, Saved sign-ins.')
            if field_role:
                is_password_field = field_role in PASSWORD_ROLES
                # A sign-in's plain value is its password.
                if part in (None, 'value') and getattr(row, 'kind', '') == 'basic':
                    part = 'password'
                if part == 'password' and not is_password_field:
                    raise SignInRefused('A password only goes into a password field. Pick the password field.')
                if part == 'username' and is_password_field:
                    raise SignInRefused('That is a password field. Type the username into the username or email '
                                        'field.')
            if not host_allowed(host, typed_on):
                raise SignInRefused(
                    f'{row.name} may be typed on {", ".join(typed_on)}, but this field is on {host}, so it wasn\'t '
                    'typed. If this really is the right site (a sign-in page on another subdomain, say), ask the '
                    f'person with {ask}; never type it on a site it is not allowed on.')
            target = host
        else:
            # Only a sign-in saved just for commands here: one that also has
            # sites is the person's sign-in for those sites, and a terminal or
            # an editor would show it in plain text.
            if SANDBOX_ONLY_HOST not in (getattr(row, 'allowed_hosts', None) or []) or _site_hosts(row):
                raise SignInRefused(
                    f'{row.name} is saved for websites ({", ".join(_site_hosts(row)) or "no sites"}), so it can '
                    'only be typed into the browser on one of those sites.')
            target = SANDBOX_ONLY_HOST
        try:
            return resolver.value(row.name, part, target)
        except SecretUseError as exc:
            raise SignInRefused(str(exc)) from None

    return PLACEHOLDER_RE.sub(one, text)


def prepare(driver: Any, window_id: int, text: str, *, owner: Any, allowed: list[str] | None,
            viewer: Any, element: int | None = None,
            point: tuple[float, float] | None = None) -> tuple[str, Target | None]:
    """The text to type, with saved sign-ins filled in for this window, and
    the browser field it goes into (None outside the browser).

    Runs in a tool's worker thread; loads the person's saved sign-ins on the
    calling event loop."""
    from cognitrix.tools.tool import run_on_calling_loop

    if not getattr(driver, 'sandbox', False):
        raise SignInRefused('Saved sign-ins can only be typed on your own computer, not the person\'s.')
    if not owner:
        raise SignInRefused('Saved sign-ins need a signed-in person.')
    win = driver.backend.window(int(window_id))
    if win is None:
        raise SignInRefused(f'Window {window_id} is gone. Call list_windows.')
    target = None
    if is_browser(win):
        if not one_placeholder(text):
            raise SignInRefused('In the browser, type one saved value per call, on its own: the text must be a '
                                'single {{secret:...}} placeholder, optionally followed by a new line to submit. '
                                'Type the username and the password into their fields with separate calls.')
        target = check_page(driver, window_id, viewer, element=element, point=point)
    resolver = run_on_calling_loop(Resolver.for_owner(str(owner), allowed))
    filled = fill(resolver, text, browser=target is not None, host=target.host if target else '',
                  field_role=target.role if target else '')
    try:
        run_on_calling_loop(resolver.mark_used())
    except Exception:
        pass
    return filled, target
