"""Code-sign the desktop build when signing secrets are set (CI helper).

Without secrets every command prints a notice and exits 0, so unsigned
builds keep working exactly as before.

    code_signing.py windows <out.json>
        Picks the Windows signing method from the environment, signs the
        frozen engine's .exe files with it, and writes a Tauri config file
        whose ``bundle.windows.signCommand`` signs the app and installers.
        Writes ``{}`` when nothing is set.

        Azure Artifact Signing (formerly Trusted Signing), preferred:
            AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
            AZURE_SIGNING_ENDPOINT, AZURE_SIGNING_ACCOUNT, AZURE_SIGNING_PROFILE
            (needs ``artifact-signing-cli`` on PATH)
        A certificate already imported into the user's certificate store:
            WINDOWS_CERTIFICATE_THUMBPRINT (+ SIGNTOOL, the signtool.exe path)

    code_signing.py macos-engine <identity> [engine dir]
        Signs every Mach-O file in the frozen engine with the Developer ID
        identity, hardened runtime and a secure timestamp. Notarization
        rejects an app with any unsigned binary inside, and Tauri only signs
        its own executable, so this must run before ``tauri build``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / 'src-tauri' / 'engine'
ENGINE_ENTITLEMENTS = HERE.parent / 'src-tauri' / 'engine.entitlements'
TIMESTAMP_URL = 'http://timestamp.digicert.com'
DESCRIPTION = 'Cognitrix'

AZURE_VARS = ('AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET',
              'AZURE_SIGNING_ENDPOINT', 'AZURE_SIGNING_ACCOUNT', 'AZURE_SIGNING_PROFILE')
MACH_O_MAGIC = {
    b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',  # 32-bit
    b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',  # 64-bit
    b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',  # universal (fat)
}


def notice(message: str) -> None:
    print(f'::notice::{message}')


def windows_sign_command(env: dict[str, str]) -> tuple[str, dict] | None:
    """The method name and Tauri ``signCommand`` (with ``%1`` for the file), or None."""
    if all(env.get(name) for name in AZURE_VARS):
        return 'Azure Artifact Signing', {
            'cmd': 'artifact-signing-cli',
            'args': ['-e', env['AZURE_SIGNING_ENDPOINT'], '-a', env['AZURE_SIGNING_ACCOUNT'],
                     '-c', env['AZURE_SIGNING_PROFILE'], '-d', DESCRIPTION, '%1'],
        }
    if env.get('WINDOWS_CERTIFICATE_THUMBPRINT'):
        return 'certificate', {
            'cmd': env.get('SIGNTOOL') or 'signtool',
            'args': ['sign', '/sha1', env['WINDOWS_CERTIFICATE_THUMBPRINT'], '/fd', 'sha256',
                     '/tr', TIMESTAMP_URL, '/td', 'sha256', '/d', DESCRIPTION, '%1'],
        }
    return None


def windows(out: Path, env: dict[str, str] | None = None, engine: Path = ENGINE) -> int:
    env = dict(os.environ) if env is None else env
    picked = windows_sign_command(env)
    if picked is None:
        out.write_text('{}\n', encoding='utf-8')
        notice('No Windows signing secrets are set, so this build is unsigned (see desktop/README.md).')
        return 0
    method, command = picked
    for exe in sorted(engine.rglob('*.exe')):
        args = [str(exe) if a == '%1' else a for a in command['args']]
        subprocess.run([command['cmd'], *args], check=True)
    out.write_text(json.dumps({'bundle': {'windows': {'signCommand': command}}}, indent=2) + '\n',
                   encoding='utf-8')
    print(f'Windows builds will be signed with {method}.')
    return 0


def is_mach_o(path: Path) -> bool:
    try:
        with path.open('rb') as f:
            return f.read(4) in MACH_O_MAGIC
    except OSError:
        return False


def mach_o_files(engine: Path) -> list[Path]:
    """Every Mach-O file, deepest first, the engine executable last."""
    files = [p for p in engine.rglob('*') if p.is_file() and not p.is_symlink() and is_mach_o(p)]
    main = engine / 'cognitrix-engine'
    files.sort(key=lambda p: (p == main, -len(p.parts), str(p)))
    return files


def macos_engine(identity: str, engine: Path = ENGINE) -> int:
    if not identity:
        notice('No Apple signing identity is set, so the macOS build is unsigned (see desktop/README.md).')
        return 0
    files = mach_o_files(engine)
    for path in files:
        cmd = ['codesign', '--force', '--timestamp', '--options', 'runtime', '--sign', identity]
        if os.access(path, os.X_OK) and path.suffix not in ('.so', '.dylib'):
            cmd += ['--entitlements', str(ENGINE_ENTITLEMENTS)]
        subprocess.run([*cmd, str(path)], check=True)
    print(f'Signed {len(files)} engine binaries.')
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == 'windows':
        return windows(Path(argv[2]))
    if len(argv) >= 3 and argv[1] == 'macos-engine':
        return macos_engine(argv[2], Path(argv[3]) if len(argv) > 3 else ENGINE)
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
