"""A built-in Tailscale for machines that have none: a server in Docker or
on Fly, where the image ships the ``tailscaled`` program.

It runs in Tailscale's userspace mode, which needs no special permissions:
incoming tailnet traffic reaches Serve and Funnel as usual, and outgoing
calls to tailnet addresses go through its local proxy (``proxy_url``). Its
identity is kept in the data folder, so the machine keeps its name and
address across restarts.

Never used where the machine has its own Tailscale: that one is the owner's,
and Cognitrix only asks it questions.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from cognitrix import tailscale

logger = logging.getLogger('cognitrix.tailscale')

_proc: subprocess.Popen | None = None


def binary() -> str | None:
    override = os.environ.get('COGNITRIX_TAILSCALED_PATH', '').strip()
    if override:
        return override if os.path.isfile(override) else None
    found = shutil.which('tailscaled')
    if found:
        return found
    for path in ('/usr/local/bin/tailscaled', '/usr/sbin/tailscaled', '/usr/bin/tailscaled'):
        if os.path.isfile(path):
            return path
    return None


def available() -> bool:
    return sys.platform.startswith('linux') and binary() is not None


def state_dir() -> Path:
    return tailscale.folder() / 'state'


def socket_path() -> Path:
    return tailscale.folder() / 'tailscaled.sock'


def _pid_file() -> Path:
    return tailscale.folder() / 'tailscaled.pid'


def _proxy_file() -> Path:
    return tailscale.folder() / 'proxy-port'


def log_file() -> Path:
    return tailscale.folder() / 'tailscaled.log'


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _saved_pid() -> int:
    try:
        return int(_pid_file().read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return 0


def running() -> bool:
    """True when the built-in Tailscale is up (started by any Cognitrix process)."""
    if sys.platform == 'win32':
        return False
    if _proc is not None and _proc.poll() is None:
        return True
    if not _pid_alive(_saved_pid()):
        return False
    # The pid file and socket outlive a container that was killed, and the
    # pid may now be another program: only a listening socket counts.
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.connect(str(socket_path()))
        return True
    except OSError:
        return False


def owned() -> bool:
    """True when this process started the built-in Tailscale."""
    return _proc is not None and _proc.poll() is None


def has_state() -> bool:
    """True when the built-in Tailscale was signed in before: it can come
    back on its own, without the owner."""
    try:
        return (state_dir() / 'tailscaled.state').stat().st_size > 0
    except OSError:
        return False


def proxy_port() -> int:
    try:
        return int(_proxy_file().read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return 0


def proxy_url(scheme: str = 'http') -> str:
    """The local proxy for reaching tailnet addresses, '' when not in use.
    ``http`` for web calls, ``socks5`` for anything else."""
    port = proxy_port() if running() else 0
    return f'{scheme}://127.0.0.1:{port}' if port else ''


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def start(wait: float = 12.0) -> bool:
    """Start the built-in Tailscale if it isn't running. False when it can't."""
    global _proc
    if running():
        return True
    program = binary()
    if not available() or program is None:
        return False
    folder = tailscale.folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        state_dir().mkdir(parents=True, exist_ok=True)
        socket_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Couldn't prepare %s for Tailscale: %s", folder, exc)
        return False
    port = _free_port()
    argv = [program, '--tun=userspace-networking', f'--statedir={state_dir()}', f'--socket={socket_path()}',
            f'--socks5-server=127.0.0.1:{port}', f'--outbound-http-proxy-listen=127.0.0.1:{port}']
    try:
        log = open(log_file(), 'ab')  # noqa: SIM115 - handed to the child
        _proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        log.close()
    except OSError as exc:
        logger.warning("Couldn't start the built-in Tailscale: %s", exc)
        return False
    try:
        _pid_file().write_text(str(_proc.pid), encoding='utf-8')
        _proxy_file().write_text(str(port), encoding='utf-8')
    except OSError:
        pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if _proc.poll() is not None:
            logger.warning('The built-in Tailscale stopped while starting (see %s)', log_file())
            return False
        if socket_path().exists():
            return True
        time.sleep(0.2)
    logger.warning('The built-in Tailscale did not come up in %.0f seconds', wait)
    return False


def stop() -> None:
    """Stop the built-in Tailscale (ours, or one an earlier engine left)."""
    global _proc
    pid = _proc.pid if _proc is not None and _proc.poll() is None else _saved_pid()
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, 15)
        except OSError:
            pass
        for _ in range(25):
            if not _pid_alive(pid) or (_proc is not None and _proc.poll() is not None):
                break
            time.sleep(0.2)
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    _proc = None
    for path in (_pid_file(), _proxy_file()):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
