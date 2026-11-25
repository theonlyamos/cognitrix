"""Check Artifact: look at an artifact the way people will see it.

The page is rendered exactly as the viewer serves it (``frame.render``) and
loaded in a headless browser once per view: desktop or phone size, light or
dark colour scheme. Each view gives a screenshot plus any script errors,
console errors and failed requests, so an agent can fix a draft before the
person opens it.

The browser is an installed Edge, Chrome or Chromium on the machine Cognitrix
runs on; when there is none (a web server), the agents' sandboxed computer
does the looking with its Chromium. See ``computer/sandbox/pagecheck.py``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from cognitrix.artifact_pages import frame
from cognitrix.artifact_pages.store import PageError
from cognitrix.computer.sandbox import pagecheck

logger = logging.getLogger('cognitrix.log')

VIEWS = pagecheck.VIEWS


def clean_views(views: Any) -> list[str]:
    """The views asked for, in order and without repeats; all four when empty."""
    if isinstance(views, str):
        views = [v for v in views.replace(',', ' ').split() if v]
    out: list[str] = []
    for view in views or VIEWS:
        clean = str(view or '').strip().lower()
        try:
            pagecheck.parse_view(clean)
        except ValueError as exc:
            raise PageError('bad_view', str(exc)) from None
        if clean not in out:
            out.append(clean)
    return out


def _running_as_root() -> bool:
    return hasattr(os, 'geteuid') and os.geteuid() == 0


def _check_here(html: str, csp: str, views: list[str], browser: str, full_page: bool, scroll_y: int) -> list[dict]:
    return pagecheck.check_html(html, csp, views, browser=browser, full_page=full_page, scroll_y=scroll_y,
                                no_sandbox=_running_as_root())


def _check_in_sandbox(owner: str, html: str, csp: str, views: list[str], full_page: bool, scroll_y: int) -> list[dict]:
    from cognitrix.computer import DriverError, Unsupported
    from cognitrix.computer.sandbox.client import get_manager

    try:
        box = get_manager().sandbox(owner)
        out = box.request('POST', '/pagecheck', {'html': html, 'csp': csp, 'views': views, 'full_page': full_page,
                                                  'scroll_y': scroll_y}, timeout=150)
    except (DriverError, Unsupported) as exc:
        message = str(exc)
        if 'not_found' in message:
            message = ("The agents' computer is running an older image that can't check pages. Restart it "
                       'from Settings > Computer to update it.')
        raise PageError('no_browser', message) from None
    if not out.get('ok'):
        raise PageError(str(out.get('error') or 'check_failed'), str(out.get('message') or 'The check failed.'))
    return list(out.get('views') or [])


async def look(page: Any, source: str, owner: str, *, views: list[str], full_page: bool = True,
               scroll_y: int = 0) -> list[dict]:
    """One result per view (see ``pagecheck.check_views``). Raises PageError."""
    html = frame.render(source, page.format, page.title)
    # Served with the viewer's policy, so the check blocks (and reports) what people's viewer blocks.
    csp = frame.content_security_policy()
    browser = pagecheck.find_browser()
    if browser is not None:
        try:
            return await asyncio.to_thread(_check_here, html, csp, views, browser, full_page, int(scroll_y or 0))
        except pagecheck.PageCheckError as exc:
            logger.info('Check Artifact on this machine failed: %s', exc)
            local_error = str(exc)
    else:
        local_error = 'No Edge, Chrome or Chromium is installed on the machine Cognitrix runs on.'
    from cognitrix.computer.sandbox.client import sandbox_mode

    if not sandbox_mode():
        raise PageError('no_browser', f'{local_error} Install one (or set COGNITRIX_CHECK_BROWSER), or turn on '
                                      "the agents' computer in Settings > Computer.")
    return await asyncio.to_thread(_check_in_sandbox, owner, html, csp, views, full_page, int(scroll_y or 0))
