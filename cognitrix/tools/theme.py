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


# Fallback artwork for a theme an agent made without a drawing: the built-in
# drawing whose mood fits the theme's name, else its accent colour.
_ARTWORK_WORDS: dict[str, tuple[str, ...]] = {
    'skyline': ('city', 'tokyo', 'neon', 'cyber', 'synth', 'urban', 'metro', 'street', 'midnight', 'retro wave',
                'vapor', 'arcade', 'tower'),
    'aurora': ('aurora', 'nord', 'north', 'arctic', 'polar', 'ice', 'icy', 'frost', 'snow', 'winter', 'glacier',
               'ocean', 'sea', 'wave', 'lake', 'mountain', 'forest', 'pine', 'mint', 'teal'),
    'dunes': ('dune', 'desert', 'sand', 'sun', 'solar', 'sunset', 'sunrise', 'dawn', 'dusk', 'beach', 'summer',
              'autumn', 'fall', 'harvest', 'warm', 'amber', 'copper', 'terracotta', 'coffee', 'mocha', 'earth'),
    'blueprint': ('blueprint', 'grid', 'engineer', 'code', 'terminal', 'hacker', 'matrix', 'tech', 'circuit',
                  'graph', 'lab', 'science', 'space'),
    'halftone': ('contrast', 'mono', 'noir', 'comic', 'print', 'paper', 'ink', 'newspaper', 'grayscale', 'greyscale',
                 'black', 'minimal'),
    'moon-cat': ('cat', 'kitten', 'moon', 'cozy', 'cosy', 'cute', 'pastel', 'candy', 'latte', 'lavender',
                 'rose', 'sakura', 'blossom', 'dream', 'sleep', 'night', 'halloween', 'haunted', 'spooky'),
}


def _hue(color: str) -> float | None:
    """The hue (0-360) of a #rrggbb colour, or None for greys."""
    hex_ = themes.normalize_hex(color)
    if not hex_:
        return None
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hi, lo = max(r, g, b), min(r, g, b)
    if hi - lo < 0.08:
        return None
    if hi == r:
        h = ((g - b) / (hi - lo)) % 6
    elif hi == g:
        h = (b - r) / (hi - lo) + 2
    else:
        h = (r - g) / (hi - lo) + 4
    return h * 60


def fitting_artwork(name: str, accent: str = '') -> str:
    """A built-in drawing that suits a theme, from its name, else its accent colour."""
    words = f' {themes.slugify(name).replace("-", " ")} '
    for art, keys in _ARTWORK_WORDS.items():
        if any(f' {k}' in words for k in keys):
            return art
    hue = _hue(accent)
    if hue is None:
        return 'halftone'
    if hue < 60 or hue >= 330:
        return 'dunes'
    if hue < 200:
        return 'aurora'
    if hue < 260:
        return 'skyline'
    return 'moon-cat'


_DRAW_NEXT = ('Next, without waiting to be asked: draw artwork that fits this theme and its mood, and set it '
              'with set_theme action "artwork" (svg, name{theme}). Use the theme words (accent, fg, warn, ...) as '
              'colours. Skip this only if the person said they want no artwork.')


def _draw_note(theme_name: str, reason: str) -> str:
    return f'\n{reason} ' + _DRAW_NEXT.format(theme=f', theme "{theme_name}"')


def _describe(state: dict) -> str:
    custom = state['themes']
    mode = state['mode'] or 'each device chooses'
    return (f'light: {themes.theme_name(state["light"], custom)}, dark: {themes.theme_name(state["dark"], custom)}, '
            f'mode: {mode}')


def _changed(name: str, before: dict, after: dict, *, replaced: dict | None = None,
             created: str | None = None, artwork: dict | None = None, note: str = '') -> ToolOutcome:
    """The result line the chat reads (ThemeNotice): what Undo needs is after "Previous:".
    ``theme`` is a definition this call overwrote; ``created`` a theme it added. ``note``
    (for the agent, on the lines after) is ignored by the chat."""
    previous: dict = {'mode': before['mode'], 'light': before['light'], 'dark': before['dark']}
    if replaced:
        previous['theme'] = replaced
    if created:
        previous['created'] = created
    if artwork is not None:
        previous['artwork'] = artwork
    return ToolOutcome.success(
        f'Theme changed: {name} ({_describe(after)}). Previous: {json.dumps(previous)}{note}')


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
      Solarized, Catppuccin, Tokyo Night, High contrast (each has its own artwork). Optionally also
      set mode.
    - "mode": switch mode only (mode: "light", "dark" or "system" to follow the computer).
    - "create": make a new theme from colours and use it (name, plus hex colours for the light
      version, the dark version, or both: *_background, *_text, *_accent). The app derives panels,
      borders and secondary colours and keeps text readable. contrast: 0-100 (default 50).
      Saving under an existing name replaces that theme (keeping its artwork unless you give new).
      ALWAYS give svg too: a drawing that fits the theme's mood (see "artwork" for the rules). The
      person expects every theme an agent makes or changes to come with fitting artwork, without
      asking for it. Only leave it out if they said they want no artwork (then artwork: "none").
      Without svg the theme gets the closest built-in drawing and you are asked to draw one.
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
      "create" also takes svg (with name) to give the new theme its own drawing, or artwork (a
      built-in or saved drawing's name, or "none").
    Only change the theme when the person asks for it. When they do (create, change or set a theme),
    make sure it ends up with fitting artwork; no image key is needed, you draw it as svg."""
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
            saved_list = ', '.join(f'{d["name"]} ({d["key"]})' for d in before['drawings'])
            return ToolOutcome.success(
                f'In use: {_describe(before)}.\nArtwork: {arts}.\nBuilt-in themes (light and dark): {built_in}.\n'
                f'Their own themes: {own}.\nBuilt-in artwork: {", ".join(themes.ARTWORK_NAMES)}.\n'
                f'Saved drawings (name, key): {saved_list or "none"}.')

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
            note = ''
            if themes.theme_artwork(theme_id, await themes.get_settings(user_id)) is None:
                note = _draw_note(themes.theme_name(theme_id, custom), 'This theme has no artwork yet.')
            return _changed(themes.theme_name(theme_id, custom), before, after, note=note)

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
            replaced = next((t for t in custom if t['id'] == existing), None) if existing else None
            note = ''
            art_name = str(name or label).strip()[:48] or 'Drawing'
            choice = str(artwork or '').strip()
            art: dict | None = None
            if str(svg or '').strip():
                try:
                    art = themes.clean_artwork({'svg': svg, 'name': art_name})
                except themes.ThemeError as exc:
                    # Keep the colours; the agent fixes the drawing with action "artwork".
                    note = _draw_note(raw['name'], f'The drawing was not used ({exc}). Gave the theme a built-in one for now.')
            elif choice.lower() in ('none', 'off', 'hide', 'no'):
                art = {'id': 'none'}
            elif choice:
                slug = choice.lower().replace(' ', '-')
                found = next((k for k, v in themes.ARTWORK_NAMES.items() if slug in (k, v.lower().replace(' ', '-'))), None)
                saved_art = None if found else themes.find_drawing(choice, await themes.get_settings(user_id))
                if found:
                    art = {'id': found}
                elif saved_art:
                    art = {'svg': saved_art['svg'], 'name': saved_art['name']}
            if art is None and replaced and replaced.get('artwork') and not note:
                art = replaced['artwork']  # changing a theme's colours keeps its drawing
            if art is None:
                variant = raw.get('dark') or raw.get('light') or {}
                art = {'id': fitting_artwork(label, variant.get('accent', ''))}
                if not note:
                    note = _draw_note(raw['name'], f'The theme has the built-in {themes.ARTWORK_NAMES[art["id"]]} '
                                                   'artwork for now.')
            if strength and art.get('id') != 'none':
                art = {**art, 'strength': strength}
            raw['artwork'] = art
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
                            created=None if replaced else saved['id'], note=note)
    except themes.ThemeError as exc:
        return ToolOutcome.failure('bad_theme', str(exc))
    return ToolOutcome.failure('bad_action', 'action must be "list", "apply", "mode", "create" or "artwork".')
