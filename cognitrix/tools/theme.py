"""The Set Theme tool: agents change how the Cognitrix app looks for the
person they work for (colour theme and light/dark mode), across their web and
desktop apps. See ``cognitrix/themes.py``.

Added to each signed-in chat turn (not stored on the agent); see
``Session._run_turn``. The chat shows a note with Undo under each change
(frontend ``components/ThemeNotice.tsx``), which also applies it on screen.
"""
from __future__ import annotations

import json

from cognitrix import themes
from cognitrix.tools.tool import tool
from cognitrix.tools.utils import ToolOutcome, current_execution_context

THEME_TOOL_CATEGORY = 'system'

_ACTIONS = {
    'list': 'list', 'show': 'list', 'get': 'list', 'current': 'list',
    'apply': 'apply', 'use': 'apply', 'set': 'apply', 'switch': 'apply', 'change': 'apply', 'select': 'apply',
    'mode': 'mode', 'appearance': 'mode',
    'create': 'create', 'make': 'create', 'new': 'create', 'save': 'create', 'update': 'create', 'edit': 'create',
}


def _owner() -> str | None:
    ctx = current_execution_context()
    return str(ctx.user_id) if ctx.user_id else None


def _variant(background: str, text: str, accent: str, label: str) -> dict | None:
    given = [v for v in (background, text, accent) if str(v or '').strip()]
    if not given:
        return None
    if len(given) < 3:
        raise themes.ThemeError(f'For the {label} version give all three: {label}_background, {label}_text and {label}_accent.')
    return {'background': background, 'text': text, 'accent': accent}


def _describe(state: dict) -> str:
    custom = state['themes']
    mode = state['mode'] or 'each device chooses'
    return (f'light: {themes.theme_name(state["light"], custom)}, dark: {themes.theme_name(state["dark"], custom)}, '
            f'mode: {mode}')


def _changed(name: str, before: dict, after: dict) -> ToolOutcome:
    previous = {'mode': before['mode'], 'light': before['light'], 'dark': before['dark']}
    return ToolOutcome.success(
        f'Theme changed: {name} ({_describe(after)}). Previous: {json.dumps(previous)}')


@tool(category=THEME_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def set_theme(action: str, theme: str = '', mode: str = '', name: str = '',
                    light_background: str = '', light_text: str = '', light_accent: str = '',
                    dark_background: str = '', dark_text: str = '', dark_accent: str = '',
                    contrast: int = 50) -> ToolOutcome:
    """Change how the Cognitrix app looks for the person you work for: its colour theme and light or
    dark mode. Applies to all their devices (web and desktop). The person sees the change at once,
    with Undo.

    action:
    - "list": the themes they can use and which are in use now.
    - "apply": use a theme (theme: its name or id). Built-in themes: Signal (default), Nord,
      Solarized, Catppuccin, Tokyo Night, High contrast. Optionally also set mode.
    - "mode": switch mode only (mode: "light", "dark" or "system" to follow the computer).
    - "create": make a new theme from colours and use it (name, plus hex colours for the light
      version, the dark version, or both: *_background, *_text, *_accent). The app derives panels,
      borders and secondary colours and keeps text readable. contrast: 0-100 (default 50).
      Saving under an existing name replaces that theme.
    Only change the theme when the person asks for it."""
    user_id = _owner()
    if not user_id:
        return ToolOutcome.failure('no_owner', 'Themes can only be changed in a chat with a signed-in person.')
    action = _ACTIONS.get(str(action or '').strip().lower(), str(action or '').strip().lower())
    mode = str(mode or '').strip().lower()
    if mode in ('auto', 'os', 'computer', 'follow system'):
        mode = 'system'
    try:
        before = themes.project(await themes.get_settings(user_id))
        custom = before['themes']

        if action == 'list':
            built_in = ', '.join(f'{info["name"]} ({tid})' for tid, info in themes.BUILT_IN_THEMES.items())
            own = ', '.join(
                f'{t["name"]} ({t["id"]}; {"/".join(m for m in ("light", "dark") if t.get(m))})' for t in custom
            ) or 'none'
            return ToolOutcome.success(
                f'In use: {_describe(before)}.\nBuilt-in themes (light and dark): {built_in}.\nTheir own themes: {own}.')

        if action == 'mode':
            if mode not in themes.MODES:
                return ToolOutcome.failure('bad_mode', 'mode must be "light", "dark" or "system".')
            after = await themes.set_selection(user_id, mode=mode)
            return _changed(f'{mode} mode', before, after)

        if action == 'apply':
            theme_id = themes.find_theme(theme, custom)
            if not theme_id:
                names = ', '.join([i['name'] for i in themes.BUILT_IN_THEMES.values()] + [t['name'] for t in custom])
                return ToolOutcome.failure('no_theme', f'There is no theme called "{theme}". Choose from: {names}.')
            modes = themes.theme_modes(theme_id, custom)
            if mode and mode not in themes.MODES:
                return ToolOutcome.failure('bad_mode', 'mode must be "light", "dark" or "system".')
            # A theme with only one version also switches the mode to it, or it wouldn't show.
            if not mode and len(modes) == 1:
                mode = modes[0]
            after = await themes.set_selection(
                user_id, mode=mode or None,
                light=theme_id if 'light' in modes else None,
                dark=theme_id if 'dark' in modes else None,
            )
            return _changed(themes.theme_name(theme_id, custom), before, after)

        if action == 'create':
            label = str(name or theme or '').strip()
            if not label:
                return ToolOutcome.failure('no_name', 'Give the new theme a name.')
            raw: dict = {'name': label, 'contrast': contrast}
            light = _variant(light_background, light_text, light_accent, 'light')
            dark = _variant(dark_background, dark_text, dark_accent, 'dark')
            if light:
                raw['light'] = light
            if dark:
                raw['dark'] = dark
            existing = themes.find_theme(label, custom)
            if existing in themes.BUILT_IN_THEMES:
                existing = None
                raw['name'] = f'{label} (custom)'
            saved, _ = await themes.save_theme(user_id, raw, theme_id=existing)
            modes = tuple(m for m in ('light', 'dark') if saved.get(m))
            if mode and mode not in themes.MODES:
                return ToolOutcome.failure('bad_mode', 'mode must be "light", "dark" or "system".')
            if not mode and len(modes) == 1:
                mode = modes[0]
            after = await themes.set_selection(
                user_id, mode=mode or None,
                light=saved['id'] if 'light' in modes else None,
                dark=saved['id'] if 'dark' in modes else None,
            )
            return _changed(saved['name'], before, after)
    except themes.ThemeError as exc:
        return ToolOutcome.failure('bad_theme', str(exc))
    return ToolOutcome.failure('bad_action', 'action must be "list", "apply", "mode" or "create".')
