"""Build the desktop engine with PyInstaller and put it where Tauri bundles it.

    python desktop/scripts/build_engine.py [--skip-frontend]

Steps: build the React app (frontend/dist), run PyInstaller on
desktop/cognitrix-engine.spec, then move the result to
desktop/src-tauri/engine/. Run it from the Python environment that has
Cognitrix and PyInstaller installed (`pip install -e . pyinstaller`).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / 'desktop'
TARGET = DESKTOP / 'src-tauri' / 'engine'


def run(cmd: list[str], cwd: Path) -> None:
    print('+', ' '.join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, shell=sys.platform == 'win32' and cmd[0] in ('pnpm', 'npm'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--skip-frontend', action='store_true', help='Reuse the existing frontend/dist')
    args = parser.parse_args()

    if not args.skip_frontend:
        run(['pnpm', 'install', '--frozen-lockfile'], ROOT / 'frontend')
        run(['pnpm', 'run', 'build'], ROOT / 'frontend')
    if not (ROOT / 'frontend' / 'dist' / 'index.html').is_file():
        print('frontend/dist is missing; build the frontend first', file=sys.stderr)
        return 1

    work = DESKTOP / 'build'
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
         '--distpath', str(work / 'dist'), '--workpath', str(work / 'pyinstaller'),
         str(DESKTOP / 'cognitrix-engine.spec')], ROOT)

    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.move(str(work / 'dist' / 'engine'), str(TARGET))
    print(f'Engine ready at {TARGET}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
