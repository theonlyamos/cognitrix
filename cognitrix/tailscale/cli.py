"""Find and run the Tailscale client.

Every call has a time limit and never raises for a client that is missing,
signed out or slow: callers get a ``Result`` (or ``None``/``{}`` from the
JSON helpers) and decide what to tell the person. ``_run`` is the one place a
process is started, so tests swap it for a stand-in client.

Tailscale marks its ``--json`` output "subject to change", so every reader
here tolerates missing fields.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

# A page Tailscale wants the owner to open: sign in, or turn a feature on.
_URL = re.compile(r'https://[A-Za-z0-9.-]*tailscale\.com/[^\s"\'<>]+')


@dataclass
class Result:
    code: int
    out: str = ''
    err: str = ''
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out

    @property
    def text(self) -> str:
        return '\n'.join(p for p in (self.out.strip(), self.err.strip()) if p)


def find_url(text: str) -> str:
    match = _URL.search(text or '')
    return match.group(0).rstrip('.,)') if match else ''


def _candidates() -> list[str]:
    if sys.platform == 'win32':
        roots = [os.environ.get('ProgramFiles', r'C:\Program Files'),
                 os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')]
        return [os.path.join(root, 'Tailscale', 'tailscale.exe') for root in roots if root]
    if sys.platform == 'darwin':
        return ['/Applications/Tailscale.app/Contents/MacOS/Tailscale', '/usr/local/bin/tailscale',
                '/opt/homebrew/bin/tailscale']
    return ['/usr/bin/tailscale', '/usr/local/bin/tailscale', '/usr/sbin/tailscale']


def find_cli() -> str | None:
    """Path of the Tailscale client on this machine, or None."""
    override = os.environ.get('COGNITRIX_TAILSCALE_PATH', '').strip()
    if override:
        return override if os.path.isfile(override) else None
    found = shutil.which('tailscale')
    if found:
        return found
    for path in _candidates():
        if os.path.isfile(path):
            return path
    return None


def _run(argv: list[str], timeout: float, input: str | None = None) -> Result:
    kwargs: dict[str, Any] = {}
    if sys.platform == 'win32':
        kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL, **kwargs)
    except OSError as exc:
        return Result(127, '', str(exc))
    timed_out = False
    try:
        out, err = proc.communicate(input.encode() if input is not None else None, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            out, err = b'', b''
    return Result(proc.returncode if proc.returncode is not None else 1,
                  (out or b'').decode('utf-8', 'replace'), (err or b'').decode('utf-8', 'replace'), timed_out)


def base_argv() -> list[str] | None:
    """The client, pointed at the built-in Tailscale when that is the one in use."""
    cli = find_cli()
    if not cli:
        return None
    from cognitrix.tailscale import daemon

    if daemon.running():
        return [cli, f'--socket={daemon.socket_path()}']
    return [cli]


def run(*args: str, timeout: float = 10, input: str | None = None) -> Result:
    argv = base_argv()
    if argv is None:
        return Result(127, '', 'Tailscale is not installed on this machine.')
    return _run([*argv, *args], timeout, input)


def _json(result: Result) -> Any:
    text = result.out.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        # Some versions print a warning line before the JSON.
        start = text.find('{')
        if start > 0:
            try:
                return json.loads(text[start:])
            except ValueError:
                return None
        return None


def status() -> dict[str, Any] | None:
    """``tailscale status --json``, or None when no Tailscale answers."""
    data = _json(run('status', '--json', timeout=8))
    return data if isinstance(data, dict) else None


def version() -> str:
    result = run('version', timeout=5)
    first = result.out.strip().splitlines()[0].strip() if result.ok and result.out.strip() else ''
    return first[:40]


def whois(address: str) -> dict[str, Any] | None:
    """Who a tailnet address belongs to (``ip`` or ``ip:port``)."""
    if not address or address.startswith('-'):
        return None
    data = _json(run('whois', '--json', address, timeout=5))
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Serve (private) and Funnel (public)
# ---------------------------------------------------------------------------

@dataclass
class Served:
    port: int
    target: str      # what '/' is proxied to, '' when it is something else
    funnel: bool
    handlers: int


def serving() -> dict[int, Served]:
    """What this machine's Tailscale serves over https, by port."""
    data = _json(run('serve', 'status', '--json', timeout=8))
    if not isinstance(data, dict):
        return {}
    funnel = data.get('AllowFunnel') if isinstance(data.get('AllowFunnel'), dict) else {}
    out: dict[int, Served] = {}
    web = data.get('Web') if isinstance(data.get('Web'), dict) else {}
    for host_port, entry in web.items():
        try:
            port = int(str(host_port).rsplit(':', 1)[1])
        except (IndexError, ValueError):
            continue
        handlers = entry.get('Handlers') if isinstance(entry, dict) and isinstance(entry.get('Handlers'), dict) else {}
        root = handlers.get('/') if isinstance(handlers.get('/'), dict) else {}
        out[port] = Served(port, str(root.get('Proxy') or ''), bool(funnel.get(host_port)), len(handlers))
    tcp = data.get('TCP') if isinstance(data.get('TCP'), dict) else {}
    for port_text in tcp:
        try:
            port = int(port_text)
        except ValueError:
            continue
        out.setdefault(port, Served(port, '', False, 1))
    return out


def target_for(door_port: int) -> str:
    return f'http://127.0.0.1:{int(door_port)}'


def serve_on(port: int, door_port: int, *, funnel: bool = False) -> Result:
    """Point https port ``port`` at a door. Funnel makes that port public.
    A tailnet that hasn't turned the feature on makes the client wait and print
    a page to open: the time limit ends the wait and the page is in ``text``."""
    return run('funnel' if funnel else 'serve', '--bg', '--yes', f'--https={int(port)}',
               target_for(door_port), timeout=10)


def serve_off(port: int, *, funnel: bool = False) -> Result:
    return run('funnel' if funnel else 'serve', f'--https={int(port)}', 'off', timeout=10)
