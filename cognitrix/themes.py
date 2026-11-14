"""Custom themes: each person's colour themes and which ones are in use.

The web and desktop apps draw everything from CSS variables (frontend
``src/app.css``). A theme names a background, text and accent colour for light
and/or dark; the frontend (``src/lib/theme/themes.ts``) derives the rest and
can override single tokens. This module keeps the account copy so every device
matches, validates theme files the same way the frontend does, and backs the
Set Theme agent tool (``cognitrix/tools/theme.py``).

Built-in ids must match ``frontend/src/lib/theme/presets.json``
(tests/test_themes.py checks).
"""
from __future__ import annotations

import json
import re
from typing import Any

from odbms import Model

THEME_FORMAT = 'cognitrix-theme'
DEFAULT_THEME_ID = 'signal'
MAX_CUSTOM_THEMES = 50
MAX_THEME_BYTES = 16 * 1024
MODES = ('system', 'light', 'dark')

BUILT_IN_THEMES: dict[str, dict[str, Any]] = {
    'signal': {'name': 'Signal', 'modes': ('light', 'dark')},
    'nord': {'name': 'Nord', 'modes': ('light', 'dark')},
    'solarized': {'name': 'Solarized', 'modes': ('light', 'dark')},
    'catppuccin': {'name': 'Catppuccin', 'modes': ('light', 'dark')},
    'tokyo-night': {'name': 'Tokyo Night', 'modes': ('light', 'dark')},
    'high-contrast': {'name': 'High contrast', 'modes': ('light', 'dark')},
}

TOKENS = (
    'bg', 'panel', 'panel-2', 'line', 'fg', 'fg-dim',
    'accent', 'on-accent', 'accent-ink', 'focus',
    'danger', 'danger-ink', 'ok',
    'warn', 'on-warn', 'warn-ink', 'info', 'info-ink', 'violet', 'violet-ink',
    'syn-num', 'syn-alt',
)

_HEX = re.compile(r'^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')
_HEX8 = re.compile(r'^#[0-9a-fA-F]{8}$')
_RGBA = re.compile(r'^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$', re.I)
_FONT = re.compile(r"^[\w ._'-]{1,64}$", re.UNICODE)
_ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,47}$')


class ThemeError(ValueError):
    pass


class ThemeSettings(Model):
    """One row per person. Missing row = Signal for both, mode left to each device."""
    user_id: str
    mode: str = ''
    """'' (each device keeps its own), 'system', 'light' or 'dark'."""
    light_theme: str = DEFAULT_THEME_ID
    dark_theme: str = DEFAULT_THEME_ID
    themes: str = '[]'
    """JSON list of the person's own theme files (see ``clean_theme``)."""


# ---------------------------------------------------------------- validation

def normalize_hex(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    m = _HEX.match(value.strip())
    if not m:
        return None
    body = m.group(1)
    if len(body) == 3:
        body = ''.join(c * 2 for c in body)
    return '#' + body.lower()


def _token_color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    hexed = normalize_hex(v)
    if hexed:
        return hexed
    if _HEX8.match(v) or _RGBA.match(v):
        return v
    return None


def slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40]
    return slug or 'theme'


def _clean_variant(raw: Any, where: str) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ThemeError(f'"{where}" must be an object.')
    out: dict[str, Any] = {}
    for key in ('background', 'text', 'accent'):
        hexed = normalize_hex(raw.get(key))
        if not hexed:
            raise ThemeError(f'"{where}.{key}" must be a hex colour such as #1a1b26.')
        out[key] = hexed
    colors = raw.get('colors')
    if colors is not None:
        if not isinstance(colors, dict):
            raise ThemeError(f'"{where}.colors" must be an object.')
        clean: dict[str, str] = {}
        for key, value in colors.items():
            if key not in TOKENS:
                continue
            color = _token_color(value)
            if not color:
                raise ThemeError(f'"{where}.colors.{key}" must be a colour.')
            clean[key] = color
        if clean:
            out['colors'] = clean
    return out


def _clean_font(value: Any, where: str) -> str | None:
    if value in (None, ''):
        return None
    if not isinstance(value, str) or not _FONT.match(value.strip()):
        raise ThemeError(f'"{where}" must be a font name (letters, numbers, spaces).')
    return value.strip()


def clean_theme(raw: Any) -> dict:
    """Check and tidy a theme file. Raises ThemeError with a readable reason."""
    if not isinstance(raw, dict):
        raise ThemeError('A theme must be a JSON object.')
    if raw.get('format', THEME_FORMAT) != THEME_FORMAT:
        raise ThemeError('This is not a Cognitrix theme.')
    if raw.get('version', 1) != 1:
        raise ThemeError('This theme was made by a newer version of Cognitrix.')
    name = str(raw.get('name') or '').strip()[:48]
    if not name:
        raise ThemeError('The theme needs a name.')
    light = _clean_variant(raw.get('light'), 'light')
    dark = _clean_variant(raw.get('dark'), 'dark')
    if not light and not dark:
        raise ThemeError('The theme needs a "light" or "dark" variant (or both).')
    theme_id = raw.get('id')
    if not isinstance(theme_id, str) or not _ID.match(theme_id):
        theme_id = slugify(name)
    out: dict[str, Any] = {'format': THEME_FORMAT, 'version': 1, 'id': theme_id, 'name': name}
    if light:
        out['light'] = light
    if dark:
        out['dark'] = dark
    if raw.get('contrast') is not None:
        try:
            contrast = float(raw['contrast'])
        except (TypeError, ValueError):
            raise ThemeError('"contrast" must be a number from 0 to 100.') from None
        out['contrast'] = round(min(100.0, max(0.0, contrast)))
    fonts = raw.get('fonts')
    if fonts is not None:
        if not isinstance(fonts, dict):
            raise ThemeError('"fonts" must be an object.')
        ui = _clean_font(fonts.get('ui'), 'fonts.ui')
        code = _clean_font(fonts.get('code'), 'fonts.code')
        if ui or code:
            out['fonts'] = {'ui': ui, 'code': code}
    if len(json.dumps(out)) > MAX_THEME_BYTES:
        raise ThemeError('This theme is too large.')
    return out


# ---------------------------------------------------------------- storage

def _themes_of(row: ThemeSettings) -> list[dict]:
    try:
        items = json.loads(row.themes or '[]')
    except (TypeError, ValueError):
        return []
    return [t for t in items if isinstance(t, dict) and isinstance(t.get('id'), str)]


async def get_settings(user_id: str) -> ThemeSettings:
    row = await ThemeSettings.find_one({'user_id': str(user_id)})
    return row or ThemeSettings(user_id=str(user_id))


async def _save(user_id: str, changes: dict[str, Any]) -> ThemeSettings:
    row = await ThemeSettings.find_one({'user_id': str(user_id)})
    if row is None:
        row = ThemeSettings(user_id=str(user_id), **changes)
        await row.save()
    elif changes:
        await ThemeSettings.update_one({'id': row.id}, changes)
    return await get_settings(user_id)


def theme_modes(theme_id: str, custom: list[dict]) -> tuple[str, ...]:
    """Appearances a theme has, or () when there is no such theme."""
    if theme_id in BUILT_IN_THEMES:
        return BUILT_IN_THEMES[theme_id]['modes']
    for theme in custom:
        if theme['id'] == theme_id:
            return tuple(m for m in ('light', 'dark') if theme.get(m))
    return ()


def theme_name(theme_id: str, custom: list[dict]) -> str:
    if theme_id in BUILT_IN_THEMES:
        return BUILT_IN_THEMES[theme_id]['name']
    for theme in custom:
        if theme['id'] == theme_id:
            return str(theme.get('name') or theme_id)
    return theme_id


def project(row: ThemeSettings) -> dict:
    """What the API returns. Ids that point nowhere fall back to Signal."""
    custom = _themes_of(row)
    light = row.light_theme if 'light' in theme_modes(row.light_theme, custom) else DEFAULT_THEME_ID
    dark = row.dark_theme if 'dark' in theme_modes(row.dark_theme, custom) else DEFAULT_THEME_ID
    return {
        'mode': row.mode if row.mode in MODES else '',
        'light': light,
        'dark': dark,
        'themes': custom,
    }


async def set_selection(user_id: str, *, mode: str | None = None, light: str | None = None,
                        dark: str | None = None) -> dict:
    row = await get_settings(user_id)
    custom = _themes_of(row)
    changes: dict[str, Any] = {}
    if mode is not None:
        if mode not in ('', *MODES):
            raise ThemeError('mode must be "system", "light" or "dark".')
        changes['mode'] = mode
    if light is not None:
        if 'light' not in theme_modes(light, custom):
            raise ThemeError(f'There is no theme "{light}" with a light version.')
        changes['light_theme'] = light
    if dark is not None:
        if 'dark' not in theme_modes(dark, custom):
            raise ThemeError(f'There is no theme "{dark}" with a dark version.')
        changes['dark_theme'] = dark
    return project(await _save(user_id, changes))


async def save_theme(user_id: str, raw: Any, *, theme_id: str | None = None) -> tuple[dict, dict]:
    """Add or replace one of the person's themes. Returns (theme, state)."""
    theme = clean_theme(raw)
    if theme_id:
        if not _ID.match(theme_id):
            raise ThemeError('Theme ids are lower-case letters, numbers and dashes.')
        theme['id'] = theme_id
    if theme['id'] in BUILT_IN_THEMES:
        raise ThemeError(f'"{theme["id"]}" is a built-in theme; save yours under another name.')
    row = await get_settings(user_id)
    custom = _themes_of(row)
    replaced = False
    for i, existing in enumerate(custom):
        if existing['id'] == theme['id']:
            custom[i] = theme
            replaced = True
    if not replaced:
        if len(custom) >= MAX_CUSTOM_THEMES:
            raise ThemeError(f'You can keep up to {MAX_CUSTOM_THEMES} themes. Delete one first.')
        custom.append(theme)
    changes: dict[str, Any] = {'themes': json.dumps(custom)}
    # A theme that lost the variant it was picked for falls back to Signal.
    if row.light_theme == theme['id'] and 'light' not in theme:
        changes['light_theme'] = DEFAULT_THEME_ID
    if row.dark_theme == theme['id'] and 'dark' not in theme:
        changes['dark_theme'] = DEFAULT_THEME_ID
    return theme, project(await _save(user_id, changes))


async def delete_theme(user_id: str, theme_id: str) -> dict:
    row = await get_settings(user_id)
    custom = [t for t in _themes_of(row) if t['id'] != theme_id]
    changes: dict[str, Any] = {'themes': json.dumps(custom)}
    if row.light_theme == theme_id:
        changes['light_theme'] = DEFAULT_THEME_ID
    if row.dark_theme == theme_id:
        changes['dark_theme'] = DEFAULT_THEME_ID
    return project(await _save(user_id, changes))


def find_theme(query: str, custom: list[dict]) -> str | None:
    """A theme id from an id or a name, case-insensitive."""
    q = (query or '').strip().lower()
    if not q:
        return None
    candidates = [(tid, info['name']) for tid, info in BUILT_IN_THEMES.items()]
    candidates += [(t['id'], str(t.get('name') or '')) for t in custom]
    for tid, name in candidates:
        if q == tid or q == name.lower():
            return tid
    slug = slugify(q)
    for tid, name in candidates:
        if slug == tid or slug == slugify(name):
            return tid
    return None
