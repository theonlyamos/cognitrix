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
    'artwork': 'artwork', 'draw': 'artwork', 'picture': 'artwork', 'art': 'artwork', 'illustration': 'artwork',
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


def _changed(name: str, before: dict, after: dict, *, replaced: dict | None = None,
             created: str | None = None, artwork: dict | None = None) -> ToolOutcome:
    """The result line the chat reads (ThemeNotice): what Undo needs is after "Previous:".
    ``theme`` is a definition this call overwrote; ``created`` a theme it added."""
    previous: dict = {'mode': before['mode'], 'light': before['light'], 'dark': before['dark']}
    if replaced:
        previous['theme'] = replaced
    if created:
        previous['created'] = created
    if artwork is not None:
        previous['artwork'] = artwork
    return ToolOutcome.success(
        f'Theme changed: {name} ({_describe(after)}). Previous: {json.dumps(previous)}')


@tool(category=THEME_TOOL_CATEGORY, retryable=False, max_attempts=1, supported_interfaces=['web', 'ws'])
async def set_theme(action: str, theme: str = '', mode: str = '', name: str = '',
                    light_background: str = '', light_text: str = '', light_accent: str = '',
                    dark_background: str = '', dark_text: str = '', dark_accent: str = '',
                    contrast: int = 50, svg: str = '', artwork: str = '', strength: int = 0) -> ToolOutcome:
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
    - "artwork": the picture shown at the top of their sidebar and above new chats. Either pick a
      built-in one (artwork: signal, aurora, dunes, moon-cat, skyline, halftone, blueprint; "none" to
      hide it; "default" for the theme's own), or draw one: svg = a complete <svg viewBox="0 0 400 160">
      with name. Draw a calm, simple scene with the ground along the bottom (it is cropped to a strip
      and faded). Shapes only: path, circle, ellipse, rect, line, polyline, polygon, g, linearGradient,
      radialGradient, mask, feGaussianBlur. No text, images, scripts or styles. Colours are theme words
      so the drawing follows the theme: accent, fg (text), fg-dim, warn, info, violet, ok, danger,
      panel-2, and surface (what the drawing sits on; use it to hide shapes behind others). Use
      opacity for softer tones. Keep it under 24 KB. Applies to the themes in use, or to `theme`.
      strength: 20-100 (default 85). Every drawing is kept in their library ("list" shows them), so
      artwork can also be the name of a saved drawing to bring it back.
      "create" also takes svg (with name) to give the new theme its own drawing.
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
            row = await themes.get_settings(user_id)
            in_use = dict.fromkeys([before['light'], before['dark']])
            arts = '; '.join(f'{themes.theme_name(tid, custom)}: {themes.artwork_label(themes.theme_artwork(tid, row))}'
                             for tid in in_use)
            return ToolOutcome.success(
                f'In use: {_describe(before)}.\nArtwork: {arts}.\nBuilt-in themes (light and dark): {built_in}.\n'
                f'Their own themes: {own}.\nBuilt-in artwork: {", ".join(themes.ARTWORK_NAMES)}.\n'
                f'Saved drawings: {", ".join(d["name"] for d in before["drawings"]) or "none"}.')

        if action == 'artwork':
            if theme:
                theme_id = themes.find_theme(theme, custom)
                if not theme_id:
                    return ToolOutcome.failure('no_theme', f'There is no theme called "{theme}".')
                targets = [theme_id]
            else:
                targets = list(dict.fromkeys([before['light'], before['dark']]))
            choice = str(artwork or '').strip().lower().replace(' ', '-')
            if str(svg or '').strip():
                art_raw: dict | None = {'svg': svg, 'name': str(name or '').strip() or 'Drawing'}
            elif choice in ('default', 'reset', 'theme', 'own'):
                art_raw = None
            elif choice in ('none', 'off', 'hide', 'no'):
                art_raw = {'id': 'none'}
            elif choice:
                found = next((k for k, v in themes.ARTWORK_NAMES.items()
                              if choice in (k, v.lower().replace(' ', '-'))), None)
                saved = None if found else themes.find_drawing(str(artwork), await themes.get_settings(user_id))
                if saved:
                    art_raw = {'svg': saved['svg'], 'name': saved['name']}
                elif not found:
                    drawn = ', '.join(d['name'] for d in before['drawings'])
                    return ToolOutcome.failure(
                        'no_artwork', f'There is no artwork "{artwork}". Choose from: {", ".join(themes.ARTWORK_NAMES)}'
                        + (f', a saved drawing ({drawn})' if drawn else '') + ', or none.')
                else:
                    art_raw = {'id': found}
            else:
                return ToolOutcome.failure('no_artwork', 'Give svg (a drawing) or artwork (a built-in name, "none" or "default").')
            if art_raw is not None and strength:
                art_raw['strength'] = strength
            if art_raw is not None:
                art_raw = themes.clean_artwork(art_raw)  # one clear error before anything changes
            previous_art = {tid: before['artwork'].get(tid) for tid in targets}
            after = before
            for tid in targets:
                after = await themes.set_artwork(user_id, tid, art_raw)
            on = ' and '.join(themes.theme_name(tid, custom) for tid in targets)
            label = 'theme artwork' if art_raw is None else ('no artwork' if art_raw.get('id') == 'none'
                                                        else f'{themes.artwork_label(art_raw)} artwork')
            return _changed(f'{label} on {on}', before, after, artwork=previous_art)

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
            if str(svg or '').strip():
                raw['artwork'] = {'svg': svg, 'name': str(name or label).strip()[:48] or 'Drawing'}
                if strength:
                    raw['artwork']['strength'] = strength
            existing = themes.find_theme(label, custom)
            if existing in themes.BUILT_IN_THEMES:
                existing = None
                raw['name'] = f'{label} (custom)'
            replaced = next((t for t in custom if t['id'] == existing), None) if existing else None
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
            return _changed(saved['name'], before, after, replaced=replaced,
                            created=None if replaced else saved['id'])
    except themes.ThemeError as exc:
        return ToolOutcome.failure('bad_theme', str(exc))
    return ToolOutcome.failure('bad_action', 'action must be "list", "apply", "mode", "create" or "artwork".')
