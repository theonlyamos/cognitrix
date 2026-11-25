"""Every app has an icon: on the Apps pages, and as the shortcut icon when it's
installed on a computer (the desktop app turns it into .ico / .icns / .png).

Agents draw one into the app's folder (``icon.svg`` or ``icon.png``; see the
build-app skill) and Publish App picks it up. When a folder has none, the app
shows a generated one (its initials on a coloured tile) so nothing is ever
blank, and the agent is told to make a real one.

Icons are kept apart from the app's code, in ``<workdir>/app-icons/<app id>.<ext>``:
the running copy is replaced on every publish, and an icon the owner uploads
from the app's page must survive that.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

MAX_ICON_BYTES = 512 * 1024

KINDS = {'png': 'image/png', 'svg': 'image/svg+xml', 'jpg': 'image/jpeg', 'webp': 'image/webp'}

# Looked for in this order in the app's folder when Publish App isn't told which file.
CANDIDATES = (
    'icon.svg', 'icon.png', 'app-icon.svg', 'app-icon.png', 'logo.svg', 'logo.png',
    'static/icon.svg', 'static/icon.png', 'public/icon.svg', 'public/icon.png',
    'assets/icon.svg', 'assets/icon.png', 'favicon.svg', 'favicon.png',
    'static/favicon.svg', 'static/favicon.png', 'public/favicon.svg', 'public/favicon.png',
    'icon.webp', 'icon.jpg', 'icon.jpeg',
)

# Kept in step with TILE_COLORS in frontend/src/lib/installedApps.ts.
TILE_COLORS = ('#c6f24e', '#7dd3fc', '#fca5a5', '#fcd34d', '#a7f3d0', '#c4b5fd', '#f9a8d4', '#fdba74')

# Icon states on HostedApp.icon. A generated icon is never stored: it is drawn
# from the current title whenever it's asked for, so a rename updates it.
CUSTOM = 'custom'        # a stored file: from the app's folder, or uploaded by a person
GENERATED = 'generated'  # initials on a tile, until someone makes a real one

_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
_SVG_DANGER = re.compile(
    r'<\s*(script|foreignObject|iframe|object|embed)\b|\son[a-z]+\s*=|javascript:', re.IGNORECASE)


class IconError(ValueError):
    pass


def icons_dir() -> Path:
    from cognitrix.config import settings

    path = Path(settings.workdir) / 'app-icons'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _base(app_id: str) -> Path | None:
    # Ids are database ids; anything else (a path) never reaches the disk.
    if not _ID.fullmatch(str(app_id or '')):
        return None
    return icons_dir() / str(app_id)


def icon_file(app_id: str) -> tuple[Path, str] | None:
    """The app's stored icon and its media type, if it has one."""
    base = _base(app_id)
    if base is None:
        return None
    for ext, media in KINDS.items():
        path = base.with_suffix(f'.{ext}')
        if path.is_file():
            return path, media
    return None


def remove(app_id: str) -> None:
    base = _base(app_id)
    if base is None:
        return
    for ext in KINDS:
        base.with_suffix(f'.{ext}').unlink(missing_ok=True)


def kind_of(data: bytes) -> str:
    """'png', 'jpg', 'webp' or 'svg' for an image we accept as an icon. Raises IconError."""
    if not data:
        raise IconError('The icon file is empty.')
    if len(data) > MAX_ICON_BYTES:
        raise IconError(f'The icon is {len(data) // 1024} KB; keep it under {MAX_ICON_BYTES // 1024} KB '
                        '(a 512 x 512 PNG or an SVG is plenty).')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    head = data[:2048].decode('utf-8', 'ignore').lstrip('﻿ \t\r\n').lower()
    if head.startswith('<svg') or (head.startswith(('<?xml', '<!--')) and '<svg' in head):
        text = data.decode('utf-8', 'ignore')
        if _SVG_DANGER.search(text):
            raise IconError('SVG icons cannot hold scripts, event handlers or embedded pages.')
        return 'svg'
    raise IconError('The icon must be a PNG, SVG, JPEG or WebP image.')


def save(app_id: str, data: bytes) -> str:
    """Store ``data`` as the app's icon (replacing any other). Returns its kind."""
    base = _base(app_id)
    if base is None:
        raise IconError('Unknown app.')
    kind = kind_of(data)
    remove(app_id)
    path = base.with_suffix(f'.{kind}')
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)
    return kind


def find_in_folder(folder: Path, explicit: str = '') -> Path | None:
    """The icon file in an app's folder: ``explicit`` (relative to the folder) or
    the first of :data:`CANDIDATES` that exists. Never a file outside the folder."""
    try:
        root = Path(folder).resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None
    names = [explicit] if str(explicit or '').strip() else list(CANDIDATES)
    for name in names:
        raw = Path(str(name).strip())
        path = (raw if raw.is_absolute() else root / raw).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def letters(title: str) -> str:
    """The first letter of the title's first two words (as the desktop app draws them)."""
    words = [w for w in str(title or '').split() if w]
    out = ''.join(w[0] for w in words[:2])
    return (out or '?').upper()


def color(slug: str) -> str:
    h = 0
    for ch in str(slug or ''):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return TILE_COLORS[h % len(TILE_COLORS)]


def stamp(title: str) -> str:
    """Changes when the generated icon would (for cache-busting its URL)."""
    return hashlib.sha256(letters(title).encode('utf-8')).hexdigest()[:8]


def generated_svg(title: str, slug: str) -> bytes:
    """A 256 x 256 tile with the app's initials: the icon an app has until it gets a real one."""
    text = html.escape(letters(title))
    size = 104 if len(letters(title)) > 1 else 132
    shade = hashlib.sha256(str(slug or '').encode('utf-8')).hexdigest()[:2]
    angle = int(shade, 16) % 4 * 45
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
        f'<defs><linearGradient id="g" gradientTransform="rotate({angle} .5 .5)">'
        f'<stop offset="0" stop-color="{color(slug)}"/><stop offset="1" stop-color="{color(slug)}" stop-opacity=".78"/>'
        '</linearGradient></defs>'
        '<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#g)"/>'
        f'<text x="128" y="136" text-anchor="middle" dominant-baseline="middle" fill="#16181a" '
        f'font-family="Space Grotesk, system-ui, sans-serif" font-weight="700" font-size="{size}">{text}</text>'
        '</svg>'
    )
    return svg.encode('utf-8')


def adopt(app_id: str, folder: Path | None, *, explicit: str = '', current: str = '') -> tuple[str, str]:
    """Pick the app's icon after a publish. Returns (state, note for the agent).

    - an icon file in the folder (or ``explicit``) becomes the icon;
    - otherwise a custom icon the app already has (uploaded on its page) stays;
    - otherwise the app shows its generated icon, and the note asks for a real one.
    """
    found = find_in_folder(folder, explicit) if folder is not None else None
    problem = f'No icon file "{explicit}" in the app\'s folder.' if str(explicit or '').strip() and found is None else ''
    if found is not None:
        try:
            save(app_id, found.read_bytes())
            return CUSTOM, ''
        except (IconError, OSError) as exc:
            problem = f'Its icon {found.name} was not used: {exc}'
    if current == CUSTOM and icon_file(app_id) is not None:
        return CUSTOM, problem
    hint = ('It has no icon yet, so it shows a placeholder with its initials. Make one now: save a square '
            'icon.svg (or a 512 x 512 icon.png) in the app\'s folder and publish again. It shows on the Apps '
            'page and as the shortcut icon when someone installs the app.')
    return GENERATED, f'{problem} {hint}'.strip()


def backfill(app_id: str, folder: str, current: str) -> str | None:
    """For apps made before icons: use an icon in their folder if there is one.
    Returns the new state, or None when nothing changed."""
    if current == CUSTOM and icon_file(app_id) is not None:
        return None
    state, _ = adopt(app_id, Path(folder) if folder else None, current=current)
    return state if state != current else None
