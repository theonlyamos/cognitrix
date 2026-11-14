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

import hashlib
import json
import re
import pyexpat
import xml.etree.ElementTree as ET
from typing import Any

from odbms import Model

THEME_FORMAT = 'cognitrix-theme'
DEFAULT_THEME_ID = 'signal'
MAX_CUSTOM_THEMES = 50
MAX_THEME_BYTES = 40 * 1024
MODES = ('system', 'light', 'dark')

BUILT_IN_THEMES: dict[str, dict[str, Any]] = {
    'signal': {'name': 'Signal', 'modes': ('light', 'dark'), 'artwork': 'signal'},
    'nord': {'name': 'Nord', 'modes': ('light', 'dark'), 'artwork': 'aurora'},
    'solarized': {'name': 'Solarized', 'modes': ('light', 'dark'), 'artwork': 'dunes'},
    'catppuccin': {'name': 'Catppuccin', 'modes': ('light', 'dark'), 'artwork': 'moon-cat'},
    'tokyo-night': {'name': 'Tokyo Night', 'modes': ('light', 'dark'), 'artwork': 'skyline'},
    'high-contrast': {'name': 'High contrast', 'modes': ('light', 'dark'), 'artwork': 'halftone'},
}

# Built-in drawings (frontend src/lib/theme/artwork.ts ARTWORK_NAMES; a test checks).
ARTWORK_NAMES: dict[str, str] = {
    'signal': 'Signal', 'aurora': 'Aurora', 'dunes': 'Dunes', 'moon-cat': 'Moon cat',
    'skyline': 'Skyline', 'halftone': 'Halftone', 'blueprint': 'Blueprint',
}
MAX_ARTWORK_BYTES = 24 * 1024
MAX_ARTWORK_ELEMENTS = 1500
# Drawings kept in the person's library (newest first) so picking other artwork never loses one.
MAX_DRAWINGS = 40

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
    artwork: str = '{}'
    """JSON {theme id: artwork} the person or an agent chose; wins over the theme's own."""
    drawings: str = '[]'
    """JSON list of every drawing the person or an agent made ({key, name, svg}), newest first.
    Choosing other artwork only changes ``artwork``; the drawing stays here to pick again."""


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


# ---------------------------------------------------------------- artwork
# Same rules as frontend src/lib/theme/artwork.ts `cleanArtworkSvg`: plain
# shapes, gradients, masks and blur; colours are hex or theme words. The app
# shows drawings as <img>, so even a miss here can't run anything.

_SVG_NS = 'http://www.w3.org/2000/svg'
_ART_ELEMENTS = {
    'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon',
    'defs', 'linearGradient', 'radialGradient', 'stop', 'mask', 'clipPath', 'filter', 'feGaussianBlur',
}
_ART_ALIASES = {'text': 'fg', 'background': 'bg', 'foreground': 'fg'}
_ART_COLOR_WORDS = {
    'bg', 'panel', 'panel-2', 'fg', 'fg-dim', 'accent', 'accent-ink', 'on-accent',
    'danger', 'ok', 'warn', 'info', 'violet', 'surface',
}
_A_NUMBER = re.compile(r'^-?(\d+\.?\d*|\.\d+)(e-?\d+)?%?$', re.I)
_A_NUMBERS = re.compile(r'^[-\d.eE,\s]+$')
_A_PATH = re.compile(r'^[MmLlHhVvCcSsQqTtAaZz\d.eE,\s+-]+$')
_A_TRANSFORM_FN = r'(?:matrix|translate|scale|rotate|skewX|skewY)\s*\([-\d.eE,\s]*\)'
# Written so no two parts can match the same text (no catastrophic backtracking).
_A_TRANSFORM = re.compile(rf'^\s*{_A_TRANSFORM_FN}(?:[\s,]*{_A_TRANSFORM_FN})*\s*$')
_A_ID = re.compile(r'^[A-Za-z][\w-]{0,40}$')
_A_REF = re.compile(r'^url\(\s*#[A-Za-z][\w-]{0,40}\s*\)$')
_A_HEX = re.compile(r'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')
_A_UNITS = {'userSpaceOnUse', 'objectBoundingBox'}
_A_ENUMS = {
    'stroke-linecap': {'butt', 'round', 'square'}, 'stroke-linejoin': {'miter', 'round', 'bevel'},
    'fill-rule': {'nonzero', 'evenodd'}, 'clip-rule': {'nonzero', 'evenodd'},
    'gradientUnits': _A_UNITS, 'maskUnits': _A_UNITS, 'maskContentUnits': _A_UNITS, 'clipPathUnits': _A_UNITS,
    'spreadMethod': {'pad', 'reflect', 'repeat'},
}
_A_NUMERIC = {
    'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'width', 'height', 'fx', 'fy', 'offset',
    'opacity', 'fill-opacity', 'stroke-opacity', 'stop-opacity', 'stroke-width', 'stdDeviation', 'stroke-miterlimit',
}
_A_COLOR = {'fill', 'stroke', 'stop-color'}


def _squash(v: str) -> str:
    return re.sub(r'\s+', ' ', v)


def clean_artwork_attribute(name: str, raw: str) -> str | None:
    v = (raw or '').strip()
    if not v or len(v) > 4000:
        return None
    if name in _A_COLOR:
        if v == 'none' or _A_REF.match(v):
            return re.sub(r'\s+', '', v)
        if _A_HEX.match(v):
            return v.lower()
        word = _ART_ALIASES.get(v.lower(), v.lower())
        return word if word in _ART_COLOR_WORDS else None
    if name in _A_NUMERIC:
        return v if _A_NUMBER.match(v) else None
    if name in ('points', 'viewBox'):
        return _squash(v) if _A_NUMBERS.match(v) else None
    if name == 'stroke-dasharray':
        return _squash(v) if v == 'none' or _A_NUMBERS.match(v) else None
    if name == 'd':
        return _squash(v) if _A_PATH.match(v) else None
    if name in ('transform', 'gradientTransform'):
        return _squash(v) if _A_TRANSFORM.match(v) else None
    if name == 'id':
        return v if _A_ID.match(v) else None
    if name in ('mask', 'clip-path', 'filter'):
        return re.sub(r'\s+', '', v) if _A_REF.match(v) else None
    if name in _A_ENUMS:
        return v if v in _A_ENUMS[name] else None
    return None


def _local(tag: str) -> tuple[str | None, str]:
    if tag.startswith('{'):
        ns, _, name = tag[1:].partition('}')
        return ns, name
    return None, tag


def _esc(v: str) -> str:
    return v.replace('&', '&amp;').replace('<', '&lt;').replace('"', '&quot;')


def _clean_element(el: ET.Element, budget: list[int]) -> str:
    if not isinstance(el.tag, str):
        return ''
    ns, name = _local(el.tag)
    if (ns and ns != _SVG_NS) or name not in _ART_ELEMENTS or name == 'svg':
        return ''
    budget[0] -= 1
    if budget[0] < 0:
        raise ThemeError('This drawing has too many shapes.')
    attrs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(attr: str, value: str) -> None:
        if attr in seen:
            return
        clean = clean_artwork_attribute(attr, value)
        if clean is not None:
            attrs.append((attr, clean))
            seen.add(attr)

    for key, value in el.attrib.items():
        a_ns, a_name = _local(key)
        if a_ns is None and a_name != 'style':
            add(a_name, value)
    for decl in (el.attrib.get('style') or '').split(';'):
        parts = decl.split(':')
        if len(parts) == 2:
            add(parts[0].strip(), parts[1].strip())
    inner = ''.join(_clean_element(child, budget) for child in el)
    opening = f'<{name}' + ''.join(f' {k}="{_esc(v)}"' for k, v in attrs)
    return f'{opening}>{inner}</{name}>' if inner else f'{opening}/>'


def _refuse_doctype(*_args: Any) -> None:
    raise ThemeError('This drawing uses a DOCTYPE, which is not allowed.')


def _parse_xml_without_entities(text: str) -> ET.Element:
    """Parse XML with expat directly: DOCTYPEs and entity declarations are
    refused by the parser itself, so nothing is ever expanded."""
    builder = ET.TreeBuilder()
    expat = pyexpat.ParserCreate(namespace_separator='}')
    expat.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_NEVER)
    expat.StartDoctypeDeclHandler = _refuse_doctype
    expat.EntityDeclHandler = _refuse_doctype
    expat.ExternalEntityRefHandler = _refuse_doctype

    def qualify(name: str) -> str:
        return '{' + name if '}' in name else name

    expat.StartElementHandler = lambda name, attrs: builder.start(
        qualify(name), {qualify(k): v for k, v in attrs.items()})
    expat.EndElementHandler = lambda name: builder.end(qualify(name))
    try:
        expat.Parse(text, True)
    except pyexpat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from None
    return builder.close()


def clean_artwork_svg(source: Any) -> str:
    """Clean an SVG drawing down to plain shapes. Raises ThemeError."""
    text = str(source or '').strip()
    if not text:
        raise ThemeError('The drawing is empty.')
    if len(text) > MAX_ARTWORK_BYTES:
        raise ThemeError('This drawing is too large (keep it under 24 KB).')
    if re.search(r'<!(DOCTYPE|ENTITY)', text, re.I):
        raise ThemeError('This drawing uses a DOCTYPE, which is not allowed.')
    try:
        root = _parse_xml_without_entities(text)
    except ET.ParseError as exc:
        raise ThemeError(f'This is not valid SVG ({exc}).') from None
    ns, name = _local(root.tag)
    if name != 'svg' or (ns and ns != _SVG_NS):
        raise ThemeError('The drawing must be an <svg> element.')
    view_box = clean_artwork_attribute('viewBox', root.attrib.get('viewBox', ''))
    if not view_box:
        try:
            w = float(re.sub(r'[a-z%]+$', '', root.attrib.get('width', '')))
            h = float(re.sub(r'[a-z%]+$', '', root.attrib.get('height', '')))
        except ValueError:
            w = h = 0
        if not (w > 0 and h > 0):
            raise ThemeError('The drawing needs a viewBox, such as viewBox="0 0 400 160".')
        view_box = f'0 0 {w:g} {h:g}'
    if len([p for p in re.split(r'[\s,]+', view_box) if p]) != 4:
        raise ThemeError('The viewBox needs four numbers.')
    budget = [MAX_ARTWORK_ELEMENTS]
    inner = ''.join(_clean_element(child, budget) for child in root)
    if not inner:
        raise ThemeError('Nothing in this drawing could be kept.')
    return f'<svg xmlns="{_SVG_NS}" viewBox="{view_box}">{inner}</svg>'


def clean_artwork(raw: Any) -> dict:
    """Check and tidy an artwork setting: {id} for a built-in (or 'none'), or {svg, name}."""
    if not isinstance(raw, dict):
        raise ThemeError('"artwork" must be an object.')
    out: dict[str, Any] = {}
    svg = raw.get('svg')
    if isinstance(svg, str) and svg.strip():
        out['svg'] = clean_artwork_svg(svg)
        name = str(raw.get('name') or '').strip()[:48]
        out['name'] = name or 'Your drawing'
    elif raw.get('id') == 'none' or raw.get('id') in ARTWORK_NAMES:
        out['id'] = raw['id']
    else:
        choices = ', '.join(ARTWORK_NAMES)
        raise ThemeError(f'Unknown artwork "{raw.get("id") or ""}". Choose from: {choices}, or none.')
    if raw.get('strength') is not None:
        try:
            out['strength'] = round(min(100.0, max(0.0, float(raw['strength']))))
        except (TypeError, ValueError):
            pass
    return out


def artwork_label(art: dict | None) -> str:
    if not art or art.get('id') == 'none':
        return 'none'
    if art.get('svg'):
        return str(art.get('name') or 'a drawing')
    return ARTWORK_NAMES.get(str(art.get('id')), 'none')


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
    if raw.get('artwork') is not None:
        out['artwork'] = clean_artwork(raw['artwork'])
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


def _artwork_of(row: ThemeSettings) -> dict[str, dict]:
    try:
        items = json.loads(row.artwork or '{}')
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in items.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(items, dict) else {}


def drawing_key(svg: str) -> str:
    return hashlib.sha1(svg.encode('utf-8')).hexdigest()[:12]


def _stored_drawings(row: ThemeSettings) -> list[dict]:
    try:
        items = json.loads(getattr(row, 'drawings', None) or '[]')
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    out = []
    for d in items:
        if isinstance(d, dict) and isinstance(d.get('svg'), str) and d['svg']:
            out.append({'key': str(d.get('key') or drawing_key(d['svg'])),
                        'name': str(d.get('name') or 'Your drawing')[:48], 'svg': d['svg']})
    return out


def _add_drawing(drawings: list[dict], art: dict | None) -> list[dict]:
    """``drawings`` with ``art``'s drawing first (if it is one), without duplicates."""
    if not art or not art.get('svg'):
        return drawings
    key = drawing_key(art['svg'])
    rest = [d for d in drawings if d['key'] != key]
    return [{'key': key, 'name': str(art.get('name') or 'Your drawing')[:48], 'svg': art['svg']}, *rest]


def all_drawings(row: ThemeSettings) -> list[dict]:
    """The library, plus any drawing still in use that predates it (artwork chosen or a theme's own)."""
    drawings = _stored_drawings(row)
    known = {d['key'] for d in drawings}
    in_use = [*_artwork_of(row).values(), *(t.get('artwork') for t in _themes_of(row))]
    for art in in_use:
        if isinstance(art, dict) and art.get('svg') and drawing_key(art['svg']) not in known:
            drawings.append({'key': drawing_key(art['svg']), 'name': str(art.get('name') or 'Your drawing')[:48],
                             'svg': art['svg']})
            known.add(drawings[-1]['key'])
    return drawings


def _drawings_with(row: ThemeSettings, art: dict | None) -> str:
    """The library JSON after adding ``art``. Drops the oldest drawings not in use when full."""
    drawings = _add_drawing(all_drawings(row), art)
    if len(drawings) > MAX_DRAWINGS:
        used = {drawing_key(a['svg']) for a in _artwork_of(row).values() if a.get('svg')}
        used |= {drawing_key(t['artwork']['svg']) for t in _themes_of(row) if (t.get('artwork') or {}).get('svg')}
        if art and art.get('svg'):
            used.add(drawing_key(art['svg']))
        while len(drawings) > MAX_DRAWINGS:
            drop = next((i for i in range(len(drawings) - 1, -1, -1) if drawings[i]['key'] not in used), None)
            if drop is None:
                break
            drawings.pop(drop)
    return json.dumps(drawings)


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
        'artwork': _artwork_of(row),
        'drawings': all_drawings(row),
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
    # Keep drawings this replaces (and this theme's own) in the library.
    changes: dict[str, Any] = {'themes': json.dumps(custom), 'drawings': _drawings_with(row, theme.get('artwork'))}
    # A theme that lost the variant it was picked for falls back to Signal.
    if row.light_theme == theme['id'] and 'light' not in theme:
        changes['light_theme'] = DEFAULT_THEME_ID
    if row.dark_theme == theme['id'] and 'dark' not in theme:
        changes['dark_theme'] = DEFAULT_THEME_ID
    return theme, project(await _save(user_id, changes))


async def delete_theme(user_id: str, theme_id: str) -> dict:
    row = await get_settings(user_id)
    custom = [t for t in _themes_of(row) if t['id'] != theme_id]
    chosen = _artwork_of(row)
    # Its drawings stay in the library.
    changes: dict[str, Any] = {'themes': json.dumps(custom), 'drawings': _drawings_with(row, None)}
    if chosen.pop(theme_id, None) is not None:
        changes['artwork'] = json.dumps(chosen)
    if row.light_theme == theme_id:
        changes['light_theme'] = DEFAULT_THEME_ID
    if row.dark_theme == theme_id:
        changes['dark_theme'] = DEFAULT_THEME_ID
    return project(await _save(user_id, changes))


def theme_artwork(theme_id: str, row: ThemeSettings) -> dict | None:
    """The artwork in use for a theme: the person's choice, else the theme's own."""
    chosen = _artwork_of(row).get(theme_id)
    if chosen:
        return chosen
    if theme_id in BUILT_IN_THEMES:
        return {'id': BUILT_IN_THEMES[theme_id]['artwork']}
    for theme in _themes_of(row):
        if theme['id'] == theme_id:
            return theme.get('artwork')
    return None


async def set_artwork(user_id: str, theme_id: str, raw: Any) -> dict:
    """Choose a theme's artwork; ``raw`` None goes back to the theme's own."""
    row = await get_settings(user_id)
    if not theme_modes(theme_id, _themes_of(row)):
        raise ThemeError(f'There is no theme "{theme_id}".')
    chosen = _artwork_of(row)
    if raw is None:
        chosen.pop(theme_id, None)
    else:
        chosen[theme_id] = clean_artwork(raw)
    if len(json.dumps(chosen)) > MAX_CUSTOM_THEMES * MAX_ARTWORK_BYTES:
        raise ThemeError('Too much artwork saved. Remove some first.')
    # The library keeps the drawing this replaces (and the new one) so it can be picked again.
    return project(await _save(user_id, {
        'artwork': json.dumps(chosen), 'drawings': _drawings_with(row, chosen.get(theme_id)),
    }))


async def delete_drawing(user_id: str, key: str) -> dict:
    """Remove a drawing from the library. One still in use stays until it is replaced."""
    row = await get_settings(user_id)
    drawings = [d for d in all_drawings(row) if d['key'] != key]
    return project(await _save(user_id, {'drawings': json.dumps(drawings)}))


def find_drawing(query: str, row: ThemeSettings) -> dict | None:
    """A saved drawing by name (case-insensitive) or key."""
    q = (query or '').strip().lower()
    if not q:
        return None
    for d in all_drawings(row):
        if q in (d['key'], d['name'].lower(), slugify(d['name'])):
            return d
    return None


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
