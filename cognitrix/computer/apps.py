"""Per-app answers for background computer use: "always allow agents to work
in Notepad", "never in Outlook". No answer means the person is asked.

The answer belongs to a person, not an agent, and only covers actions that
stay in the background. An action that takes over the screen always asks.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger('cognitrix.log')

LEVELS = ('allow', 'ask', 'deny')
LOCAL_USER = 'local'  # the CLI, where nobody is signed in

# Window tools that press, type or change something in an app.
ACTION_TOOLS = frozenset({
    'click_element', 'set_text', 'click_in_window', 'type_in_window', 'press_key_in_window',
})


def _tool_key(name: Any) -> str:
    return re.sub(r'[\s\-_]+', '_', str(name or '').strip()).lower()


def app_key(app: Any) -> str:
    return str(app or '').strip().lower()


def app_label(app: Any) -> str:
    """'notepad.exe' -> 'Notepad'."""
    name = re.sub(r'\.(exe|app)$', '', app_key(app))
    return name[:1].upper() + name[1:] if name else 'this app'


def app_of_call(tool_name: Any, params: dict | None) -> str | None:
    """The app a background window action would act in, or None when the call
    isn't one, takes over the screen, or the window can't be found."""
    if _tool_key(tool_name) not in ACTION_TOOLS:
        return None
    params = params if isinstance(params, dict) else {}
    if params.get('use_screen'):
        return None
    try:
        from cognitrix.computer import get_driver

        window = get_driver().backend.window(int(params.get('window_id')))
    except Exception:
        return None
    return app_key(getattr(window, 'app', '')) or None


async def _rows(user_key: str, app: str | None = None) -> list:
    from cognitrix.models.computer_app import ComputerAppPermission

    query = {'user_id': str(user_key or LOCAL_USER)}
    if app is not None:
        query['app'] = app_key(app)
    try:
        return list(await ComputerAppPermission.find(query) or [])
    except Exception:
        logger.exception('Could not read computer-use app permissions')
        return []


async def level(user_key: str | None, app: str) -> str:
    """'allow', 'deny' or 'ask' for this person and app."""
    rows = await _rows(user_key or LOCAL_USER, app)
    value = str(getattr(rows[0], 'level', '') or '').lower() if rows else 'ask'
    return value if value in ('allow', 'deny') else 'ask'


async def set_level(user_key: str | None, app: str, value: str) -> None:
    """Save a person's answer for an app; 'ask' forgets it."""
    from cognitrix.models.computer_app import ComputerAppPermission

    value = str(value or '').lower()
    if value not in LEVELS:
        raise ValueError(f"Unknown level '{value}'. Use one of: {', '.join(LEVELS)}")
    app = app_key(app)
    if not app:
        raise ValueError('An app name is required')
    user = str(user_key or LOCAL_USER)
    await ComputerAppPermission.delete_many({'user_id': user, 'app': app})
    if value != 'ask':
        await ComputerAppPermission(user_id=user, app=app, level=value).save()


async def list_levels(user_key: str | None) -> list[dict[str, str]]:
    rows = await _rows(user_key or LOCAL_USER)
    out = [
        {'app': app_key(r.app), 'label': app_label(r.app), 'level': str(r.level).lower()}
        for r in rows if str(getattr(r, 'level', '')).lower() in ('allow', 'deny')
    ]
    return sorted(out, key=lambda r: r['label'].lower())
