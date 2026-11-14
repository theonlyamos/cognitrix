"""The date line agents see, in the timezone of the person they work for.

The web and desktop apps send the browser's timezone with every request
(``X-Timezone``). Background work that has no request (task runs, chat apps)
falls back to the timezone of the machine the engine runs on.

Only the date goes into the system prompt, never the time of day: a prompt
that changed every minute could not be cached by the provider, which makes
every turn of a long chat cost full price.
"""
from __future__ import annotations

import contextvars
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

client_timezone: contextvars.ContextVar[str] = contextvars.ContextVar('client_timezone', default='')

_MAX_TZ_NAME = 64


def clean_timezone(name: object) -> str:
    """A valid IANA timezone name, or '' (the header is untrusted input)."""
    text = str(name or '').strip()
    if not text or len(text) > _MAX_TZ_NAME:
        return ''
    try:
        ZoneInfo(text)
    except Exception:
        return ''
    return text


def _zone() -> tuple[tzinfo | None, str]:
    name = clean_timezone(client_timezone.get())
    if name:
        return ZoneInfo(name), name
    return None, ''


def local_now() -> tuple[datetime, str]:
    """Now in the person's timezone, and that timezone's name ('' = this machine's)."""
    zone, name = _zone()
    if zone is not None:
        return datetime.now(zone), name
    return datetime.now().astimezone(), ''


def date_line() -> str:
    """First line of every system prompt. Changes once a day."""
    now, name = local_now()
    offset = now.strftime('%z') or '+0000'
    utc = f"UTC{offset[:3]}:{offset[3:5]}"
    where = f"{name}, {utc}" if name else utc
    return f"Today is {now.strftime('%A, %d %B %Y')} (the person's timezone: {where})."
