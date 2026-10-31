"""Start the packaged engine and check it serves the UI, on any OS.

    python desktop/scripts/smoke_engine.py [--port 48299] [--timeout 180]

Exits non-zero (and prints the engine's output) if /home doesn't answer 200.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1] / 'src-tauri' / 'engine'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=48299)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()

    exe = ENGINE_DIR / ('cognitrix-engine.exe' if sys.platform == 'win32' else 'cognitrix-engine')
    log = ENGINE_DIR.parent / 'smoke-engine.log'
    with log.open('wb') as out:
        proc = subprocess.Popen([str(exe), 'engine', '--port', str(args.port)], stdout=out, stderr=subprocess.STDOUT)
        ok = False
        deadline = time.time() + args.timeout
        try:
            while time.time() < deadline and proc.poll() is None:
                time.sleep(2)
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/home', timeout=5) as r:
                        if r.status == 200:
                            ok = True
                            break
                except OSError:
                    pass
        finally:
            proc.kill()
            proc.wait()
    if ok:
        print('Engine served the UI')
        return 0
    print(f'Engine did not serve the UI (exit code {proc.returncode}). Output:', file=sys.stderr)
    print(log.read_text(errors='replace')[-8000:], file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
