"""Write latest.json, the file installed apps read to find a newer version.

Run in a folder holding the release's ``.sig`` files (one per installer the
app can update from). Each signature is matched to its installer by name:

    *-setup.exe.sig     -> windows-x86_64
    *.AppImage.sig      -> linux-x86_64
    *.app.tar.gz.sig    -> darwin-aarch64

Usage: make_update_manifest.py <version> <download base URL> [folder] [notes]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PLATFORMS = (
    ('-setup.exe.sig', 'windows-x86_64'),
    ('.AppImage.sig', 'linux-x86_64'),
    ('.app.tar.gz.sig', 'darwin-aarch64'),
)


def build(version: str, base_url: str, folder: Path, notes: str = '') -> dict:
    platforms: dict[str, dict[str, str]] = {}
    for sig in sorted(folder.glob('*.sig')):
        for suffix, platform in PLATFORMS:
            if sig.name.endswith(suffix):
                installer = sig.name[:-len('.sig')]
                platforms[platform] = {
                    'signature': sig.read_text(encoding='utf-8').strip(),
                    # GitHub turns spaces in an uploaded file's name into dots.
                    'url': f"{base_url.rstrip('/')}/{installer.replace(' ', '.')}",
                }
    return {
        'version': version.lstrip('v'),
        'notes': notes,
        'pub_date': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'platforms': platforms,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    folder = Path(argv[3]) if len(argv) > 3 else Path('.')
    manifest = build(argv[1], argv[2], folder, argv[4] if len(argv) > 4 else '')
    if not manifest['platforms']:
        print('No signatures found, so there is nothing to publish.')
        return 1
    (folder / 'latest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f"latest.json: version {manifest['version']} for {', '.join(manifest['platforms'])}")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
