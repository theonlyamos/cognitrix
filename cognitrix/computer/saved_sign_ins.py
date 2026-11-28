"""Typing a saved sign-in into a window on the agent's own computer.

An agent signing in to a site in its sandbox browser writes
``{{secret:NAME.username}}`` / ``{{secret:NAME.password}}`` (or
``{{secret:NAME}}`` for a token) in Type In Window or Set Text. The value is
filled in here, right before it is typed, so the model never sees it, and
every tool result is scrubbed of saved values afterwards
(``agents/base._hide_saved_secrets``).

Where a value may go:

- a browser window: only when the page's address (read from the address bar
  just before typing) is a site the sign-in was saved for. ``github.com``
  also covers its subdomains here (``accounts.google.com`` for
  ``google.com``), the way a browser shares a sign-in across a site. A page
  that moved to another site after the agent looked gets nothing.
- any other window on the agent's computer: only a sign-in saved for
  commands in the sandbox (Ask For Sign-in without sites), which commands
  there can already read.
- never the person's own computer.

All driver calls block: run these off the event loop.
"""
from __future__ import annotations

from typing import Any

from cognitrix.credentials import PLACEHOLDER_RE, Resolver, SecretUseError, host_allowed

# Hosts for a sign-in saved only for commands in the sandbox (sandbox_work.SANDBOX_ONLY_HOST).
SANDBOX_ONLY_HOST = 'sandbox.invalid'
BROWSER_APPS = ('chrom', 'firefox')


class SignInRefused(Exception):
    """Nothing was typed; the message says what to do instead."""


def has_secrets(text: Any) -> bool:
    return isinstance(text, str) and bool(PLACEHOLDER_RE.search(text))


def is_browser(win: Any) -> bool:
    if win is None:
        return False
    return getattr(win, 'kind', '') == 'chromium' or any(b in (getattr(win, 'app', '') or '').lower()
                                                         for b in BROWSER_APPS)


def page_host(driver: Any, window_id: int, viewer: Any) -> str:
    """The site the browser window shows right now, from a fresh look."""
    from cognitrix.payments.checkout import _look
    from cognitrix.payments.service import site_of

    return site_of(_look(driver, int(window_id), viewer).address())


def _site_hosts(row: Any) -> list[str]:
    return [h for h in (getattr(row, 'allowed_hosts', None) or []) if h and h != SANDBOX_ONLY_HOST]


def _host_for(row: Any, host: str) -> str | None:
    """The allowed host pattern ``host`` counts as for ``row``, or None."""
    patterns = _site_hosts(row)
    if host_allowed(host, patterns):
        return host
    for pattern in patterns:
        if not pattern.startswith('*.') and ':' not in pattern and host.endswith('.' + pattern):
            return pattern
    return None


def fill(resolver: Resolver, text: str, *, browser: bool, host: str = '') -> str:
    """``text`` with every placeholder replaced, or SignInRefused."""
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
                    "Couldn't read this browser window's address, so the saved sign-in wasn't typed. Make sure the "
                    'sign-in page is open in this window (not a blank tab or a pop-up) and try again.')
            target = _host_for(row, host)
            if target is None:
                sites = ', '.join(_site_hosts(row))
                if not sites:
                    raise SignInRefused(
                        f'{row.name} was saved for commands on your computer, not for a website. Ask for it again '
                        f"with ask_for_sign_in, allowed_hosts=['{host}'], then type it.")
                raise SignInRefused(
                    f'{row.name} is saved for {sites}, but this page is on {host}, so it wasn\'t typed. If this '
                    'really is the right site, ask the person with ask_for_sign_in for a sign-in saved for '
                    f'{host}; never type it on a site it is not saved for.')
        else:
            if SANDBOX_ONLY_HOST not in (getattr(row, 'allowed_hosts', None) or []):
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
            viewer: Any) -> str:
    """The text to type, with saved sign-ins filled in for this window.

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
    browser = is_browser(win)
    host = page_host(driver, window_id, viewer) if browser else ''
    resolver = run_on_calling_loop(Resolver.for_owner(str(owner), allowed))
    filled = fill(resolver, text, browser=browser, host=host)
    try:
        run_on_calling_loop(resolver.mark_used())
    except Exception:
        pass
    return filled
