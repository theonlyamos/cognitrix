"""What kind of file something is, and which files are noise."""
from __future__ import annotations

import os

# extension -> (mime type, kind)
_TYPES: dict[str, tuple[str, str]] = {
    # images
    '.png': ('image/png', 'image'), '.jpg': ('image/jpeg', 'image'), '.jpeg': ('image/jpeg', 'image'),
    '.gif': ('image/gif', 'image'), '.webp': ('image/webp', 'image'), '.bmp': ('image/bmp', 'image'),
    '.svg': ('image/svg+xml', 'image'), '.avif': ('image/avif', 'image'), '.ico': ('image/x-icon', 'image'),
    '.tif': ('image/tiff', 'image'), '.tiff': ('image/tiff', 'image'), '.heic': ('image/heic', 'image'),
    # video
    '.mp4': ('video/mp4', 'video'), '.m4v': ('video/mp4', 'video'), '.webm': ('video/webm', 'video'),
    '.mov': ('video/quicktime', 'video'), '.mkv': ('video/x-matroska', 'video'), '.avi': ('video/x-msvideo', 'video'),
    '.ogv': ('video/ogg', 'video'),
    # audio
    '.mp3': ('audio/mpeg', 'audio'), '.wav': ('audio/wav', 'audio'), '.ogg': ('audio/ogg', 'audio'),
    '.oga': ('audio/ogg', 'audio'), '.opus': ('audio/opus', 'audio'), '.m4a': ('audio/mp4', 'audio'),
    '.aac': ('audio/aac', 'audio'), '.flac': ('audio/flac', 'audio'), '.weba': ('audio/webm', 'audio'),
    # documents
    '.pdf': ('application/pdf', 'pdf'),
    '.docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'doc'),
    '.doc': ('application/msword', 'doc'), '.odt': ('application/vnd.oasis.opendocument.text', 'doc'),
    '.rtf': ('application/rtf', 'doc'), '.epub': ('application/epub+zip', 'doc'),
    '.pptx': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', 'slides'),
    '.ppt': ('application/vnd.ms-powerpoint', 'slides'),
    '.odp': ('application/vnd.oasis.opendocument.presentation', 'slides'),
    '.key': ('application/vnd.apple.keynote', 'slides'),
    '.xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'sheet'),
    '.xls': ('application/vnd.ms-excel', 'sheet'), '.ods': ('application/vnd.oasis.opendocument.spreadsheet', 'sheet'),
    '.csv': ('text/csv', 'sheet'), '.tsv': ('text/tab-separated-values', 'sheet'),
    # text
    '.md': ('text/markdown', 'text'), '.markdown': ('text/markdown', 'text'), '.txt': ('text/plain', 'text'),
    '.rst': ('text/x-rst', 'text'), '.tex': ('text/x-tex', 'text'), '.srt': ('text/plain', 'text'),
    '.vtt': ('text/vtt', 'text'),
    '.html': ('text/html', 'html'), '.htm': ('text/html', 'html'),
    # data
    '.json': ('application/json', 'data'), '.jsonl': ('application/x-ndjson', 'data'),
    '.xml': ('application/xml', 'data'), '.geojson': ('application/geo+json', 'data'),
    '.parquet': ('application/vnd.apache.parquet', 'data'), '.sqlite': ('application/vnd.sqlite3', 'data'),
    '.db': ('application/vnd.sqlite3', 'data'), '.ipynb': ('application/x-ipynb+json', 'data'),
    # archives
    '.zip': ('application/zip', 'archive'), '.tar': ('application/x-tar', 'archive'),
    '.gz': ('application/gzip', 'archive'), '.tgz': ('application/gzip', 'archive'),
    '.bz2': ('application/x-bzip2', 'archive'), '.xz': ('application/x-xz', 'archive'),
    '.7z': ('application/x-7z-compressed', 'archive'), '.rar': ('application/vnd.rar', 'archive'),
    # other things people open
    '.stl': ('model/stl', 'model'), '.obj': ('model/obj', 'model'), '.glb': ('model/gltf-binary', 'model'),
    '.gltf': ('model/gltf+json', 'model'), '.ttf': ('font/ttf', 'font'), '.otf': ('font/otf', 'font'),
    '.woff2': ('font/woff2', 'font'), '.apk': ('application/vnd.android.package-archive', 'other'),
    '.exe': ('application/vnd.microsoft.portable-executable', 'other'),
    '.dmg': ('application/x-apple-diskimage', 'other'), '.msi': ('application/x-msi', 'other'),
    '.deb': ('application/vnd.debian.binary-package', 'other'),
}

# Source code and configuration: the Changes card already shows these, so
# they never go on the files shelf (the agent can still share one).
CODE_EXTENSIONS = frozenset({
    '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.kt', '.kts', '.c', '.h', '.cc',
    '.cpp', '.hpp', '.cs', '.rb', '.php', '.swift', '.m', '.mm', '.scala', '.lua', '.r', '.pl', '.sh', '.bash',
    '.zsh', '.fish', '.ps1', '.bat', '.cmd', '.sql', '.css', '.scss', '.sass', '.less', '.vue', '.svelte',
    '.astro', '.dart', '.ex', '.exs', '.erl', '.hs', '.ml', '.clj', '.elm', '.zig', '.nim', '.toml', '.yaml',
    '.yml', '.ini', '.cfg', '.conf', '.env', '.properties', '.gradle', '.mk', '.cmake', '.proto', '.graphql',
    '.tf', '.dockerfile', '.gitignore', '.editorconfig', '.prettierrc', '.eslintrc',
})
CODE_NAMES = frozenset({'Dockerfile', 'Makefile', 'Procfile', 'Gemfile', 'Rakefile', 'Justfile', 'Vagrantfile'})

SKIP_DIRS = frozenset({
    'node_modules', 'venv', 'env', '__pycache__', 'site-packages', 'dist', 'build', 'target', 'coverage',
    'Pods', 'DerivedData', 'bower_components', 'vendor', 'tmp', 'temp', 'logs', 'profiles', 'snap',
    'AppData', 'Library', '$RECYCLE.BIN', 'System Volume Information',
})
SKIP_NAMES = frozenset({
    '.DS_Store', 'Thumbs.db', 'desktop.ini', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'poetry.lock',
    'uv.lock', 'Cargo.lock', 'composer.lock', 'Gemfile.lock', 'go.sum', 'AGENTS.md', 'CLAUDE.md', 'README.md',
})
SKIP_SUFFIXES = (
    '.pyc', '.pyo', '.log', '.lock', '.tmp', '.temp', '.part', '.crdownload', '.download', '.swp', '.swo', '.o',
    '.class', '.map', '.tsbuildinfo', '.pid', '.sock', '~', '.bak', '.orig', '.rej', '.d.ts',
)

# What the agent's results usually are: shown as cards when the agent shared nothing.
RESULT_KINDS = frozenset({'image', 'video', 'audio', 'pdf', 'doc', 'slides', 'sheet'})
# Shown in the reply with a preview (the rest show as a chip).
PREVIEW_KINDS = frozenset({'image', 'video', 'audio', 'pdf', 'sheet', 'doc', 'slides', 'text', 'html', 'data',
                           'archive'})

MAX_DEPTH = 8
MAX_VISITED = 20_000


def kind_of(name: str) -> tuple[str, str]:
    """(mime type, kind) for a file name. Kind is one of image, video, audio,
    pdf, doc, slides, sheet, text, html, data, archive, model, font, code, other."""
    base = os.path.basename(name or '')
    lower = base.lower()
    if lower.endswith(('.tar.gz', '.tar.bz2', '.tar.xz')):
        return 'application/gzip', 'archive'
    ext = os.path.splitext(lower)[1]
    if ext in _TYPES:
        return _TYPES[ext]
    if ext in CODE_EXTENSIONS or base in CODE_NAMES:
        return 'text/plain', 'code'
    return 'application/octet-stream', 'other'


def skip_dir(name: str) -> bool:
    return name.startswith('.') or name in SKIP_DIRS


def noise(name: str, size: int) -> bool:
    """A file that is never a result: hidden, temporary, logs, lock files, empty."""
    base = os.path.basename(name or '')
    if not base or base.startswith('.') or base in SKIP_NAMES or size <= 0:
        return True
    lower = base.lower()
    if lower.endswith(SKIP_SUFFIXES):
        return True
    return kind_of(base)[1] == 'code'


def looks_like_result(name: str, size: int) -> bool:
    """Worth a card of its own when the agent didn't share anything."""
    kind = kind_of(name)[1]
    if kind not in RESULT_KINDS:
        return False
    if kind == 'image' and size < 4096:
        return False  # icons and favicons
    return True
