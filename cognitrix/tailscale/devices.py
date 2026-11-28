"""The tailnet devices agents may run commands on.

Two agent tools use this (cognitrix/tools/tailnet.py): one lists the
machines on the tailnet, the other runs one command on one of them. A
command only runs when all of these hold:

- an admin turned the feature on (``COGNITRIX_TAILSCALE_DEVICES``: admins'
  agents, or everyone's) and the person the agent works for is covered;
- the machine is on the admin's list (``TailnetDevice``) with the login name
  the agent asked for. Nothing is on the list until an admin puts it there;
- a person approved that exact command on a card (the tool refuses without
  it, and no permission level or automatic approval stands in).

A command travels by SSH over the tailnet, one of two ways per device:

- **Tailscale SSH**: the device's own Tailscale checks the tailnet policy and
  lets this machine in with no key. Tailscale's rules decide who may reach
  what (a tagged server only reaches tagged devices; Windows can't accept it).
- **This server's key**: ordinary SSH with a key pair Cognitrix makes for
  itself. The private half stays sealed on this machine; the public half is
  shown in Settings to add to the device's ``authorized_keys``.

When Tailscale asks the owner to confirm it is them (its "check" mode), the
link it prints is returned so the agent can hand it to the person.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cognitrix import tailscale
from cognitrix.tailscale import cli

logger = logging.getLogger('cognitrix.tailscale')

PATH_TAILSCALE = 'tailscale'
PATH_KEY = 'key'
PATHS = (PATH_TAILSCALE, PATH_KEY)
MAX_USERS = 10
MAX_DEVICES = 100
DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 900
_MAX_STREAM = 2_000_000
# After Tailscale prints a "confirm it is you" link, how long the command may
# still finish on its own before the link is handed back instead.
_CHECK_GRACE_S = 4.0
# What Tailscale SSH prints before it lets a connection in that needs the owner's check.
_CHECK_MARKS = (b'requires an additional check', b'To authenticate, visit')
_USER = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]{0,63}$')


class DeviceError(RuntimeError):
    """Words for the agent (and through it the person); ``code`` for callers."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Who may use it
# ---------------------------------------------------------------------------

async def allowed_for(user_id: Any) -> bool:
    """Whether agents working for this person may use tailnet devices at all."""
    scope = tailscale.devices_scope()
    if scope == tailscale.EVERYONE:
        return True
    if scope != tailscale.ADMINS:
        return False
    from cognitrix.agents.access import is_admin
    from cognitrix.models.user import User

    if not user_id:
        # The local command line: an admin's only where there are no accounts to compare with.
        try:
            return not await User.all()
        except Exception:  # noqa: BLE001
            return False
    try:
        user = await User.get(str(user_id))
    except Exception:  # noqa: BLE001
        user = None
    return user is not None and await is_admin(user)


def clean_users(raw: Any) -> list[str]:
    """Login names as typed in Settings: checked, de-duplicated, capped."""
    items = raw if isinstance(raw, list) else str(raw or '').replace(',', ' ').split()
    out: list[str] = []
    for item in items:
        name = str(item or '').strip()
        if not name:
            continue
        if not _USER.match(name):
            raise DeviceError('bad_user', f'"{name[:40]}" is not a login name. Use letters, digits, dots, dashes '
                                          'and underscores.')
        if name not in out:
            out.append(name)
    if len(out) > MAX_USERS:
        raise DeviceError('too_many', f'At most {MAX_USERS} login names per device.')
    return out


# ---------------------------------------------------------------------------
# The machines on the tailnet
# ---------------------------------------------------------------------------

def _ipv4(row: dict[str, Any]) -> str:
    for ip in row.get('TailscaleIPs') or []:
        try:
            if ipaddress.ip_address(str(ip)).version == 4:
                return str(ip)
        except ValueError:
            continue
    return ''


def machines() -> list[dict[str, Any]]:
    """Every other machine this one's Tailscale lists:
    [{node, name, dns, ip, os, online, tags, tailscale_ssh}]. Empty when
    Tailscale isn't running or signed in here."""
    status = cli.status()
    if not isinstance(status, dict) or status.get('BackendState') != 'Running':
        return []
    peers = status.get('Peer')
    out = []
    for row in (peers.values() if isinstance(peers, dict) else []):
        if not isinstance(row, dict):
            continue
        node, ip = str(row.get('ID') or ''), _ipv4(row)
        if not node or not ip:
            continue
        dns = str(row.get('DNSName') or '').rstrip('.').lower()
        out.append({
            'node': node, 'name': str(row.get('HostName') or dns.split('.')[0] or ip), 'dns': dns, 'ip': ip,
            'os': str(row.get('OS') or ''), 'online': bool(row.get('Online')),
            'tags': [str(t) for t in (row.get('Tags') or [])],
            # A machine that accepts Tailscale SSH advertises its host keys.
            'tailscale_ssh': bool(row.get('sshHostKeys') or row.get('SSH_HostKeys')),
        })
    out.sort(key=lambda m: (not m['online'], m['name'].lower()))
    return out


def _is_computer(machine: dict[str, Any]) -> bool:
    """An agents' Fly computer (cognitrix/tailscale/computers.py), never a device to list."""
    from cognitrix.tailscale.computers import NAME_PREFIX

    return tailscale.sandbox_tag() in machine['tags'] and machine['name'].lower().startswith(NAME_PREFIX)


async def rows() -> dict[str, Any]:
    from cognitrix.models.tailnet_device import TailnetDevice

    return {str(r.node): r for r in (await TailnetDevice.all() or [])}


def _view(machine: dict[str, Any] | None, row: Any) -> dict[str, Any]:
    base = machine or {'node': str(row.node), 'name': str(row.name or row.node), 'dns': '', 'ip': '', 'os': '',
                       'online': False, 'tags': [], 'tailscale_ssh': False}
    return {**{k: v for k, v in base.items() if k != 'ip'}, 'present': machine is not None,
            'allowed': row is not None, 'users': list(row.users or []) if row is not None else [],
            'path': (row.path if row is not None and row.path in PATHS else PATH_TAILSCALE)}


async def listing() -> list[dict[str, Any]]:
    """Machines on the tailnet with what agents may do on each, and allowed
    machines that are gone from the tailnet (so they can be removed)."""
    import asyncio

    found = [m for m in await asyncio.to_thread(machines) if not _is_computer(m)]
    saved = await rows()
    out = [_view(m, saved.get(m['node'])) for m in found]
    seen = {m['node'] for m in found}
    out += [_view(None, row) for node, row in saved.items() if node not in seen]
    return out


async def allow(node: str, users: Any, path: str, by: str = '') -> dict[str, Any]:
    """Put a machine on the list, or change what agents may do on it."""
    import asyncio

    from cognitrix.models.tailnet_device import TailnetDevice

    node = str(node or '').strip()
    names = clean_users(users)
    if path not in PATHS:
        raise DeviceError('bad_path', 'Pick Tailscale SSH or this server\'s key.')
    machine = next((m for m in await asyncio.to_thread(machines) if m['node'] == node), None)
    saved = await rows()
    row = saved.get(node)
    if row is None:
        if machine is None or _is_computer(machine):
            raise DeviceError('unknown', "That machine isn't on this server's tailnet.")
        if len(saved) >= MAX_DEVICES:
            raise DeviceError('too_many', f'At most {MAX_DEVICES} devices.')
        row = TailnetDevice(node=node, name=machine['name'], users=names, path=path, added_by=str(by or ''),
                            created_at=datetime.now(UTC).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S'))
        await row.save()
    else:
        changes: dict[str, Any] = {'users': names, 'path': path}
        if machine is not None:
            changes['name'] = machine['name']
        await TailnetDevice.update_one({'id': row.id}, changes)
        row.users, row.path = names, path
    return _view(machine, row)


async def remove(node: str) -> bool:
    from cognitrix.models.tailnet_device import TailnetDevice

    row = (await rows()).get(str(node or '').strip())
    if row is None:
        return False
    await TailnetDevice.delete_many({'id': row.id})
    return True


# ---------------------------------------------------------------------------
# This server's own SSH key
# ---------------------------------------------------------------------------

def _key_file():
    return tailscale.folder() / 'device_key'


def _make_key() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                           serialization.PublicFormat.OpenSSH).decode()
    return private, f'{public} cognitrix'


_key_lock = threading.Lock()


def _key() -> tuple[str, str]:
    """(private key, public line), made the first time it is asked for. The
    private half is sealed like the other secrets on this machine."""
    import json

    from cognitrix.common import secret_box

    with _key_lock:
        path = _key_file()
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return secret_box.unseal(data['private']), str(data['public'])
        except (OSError, ValueError, KeyError, secret_box.SecretBoxError):
            pass
        private, public = _make_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'private': secret_box.seal(private), 'public': public}), encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        return private, public


def public_key() -> str:
    """The line to add to a device's ``authorized_keys``."""
    return _key()[1]


# ---------------------------------------------------------------------------
# Running a command
# ---------------------------------------------------------------------------

@dataclass
class Ran:
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    check_url: str = ''


def find_ssh() -> str | None:
    found = shutil.which('ssh')
    if found:
        return found
    if os.name == 'nt':
        guess = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'OpenSSH', 'ssh.exe')
        if os.path.exists(guess):
            return guess
    return None


async def resolve(device: str) -> tuple[dict[str, Any], Any]:
    """The machine and its row for what the agent called it (name, tailnet
    name or address). Only machines on the list are ever matched."""
    import asyncio

    want = str(device or '').strip().lower().rstrip('.')
    if not want:
        raise DeviceError('unknown', 'Say which device (List Tailnet Devices shows them).')
    saved = await rows()
    found = [m for m in await asyncio.to_thread(machines) if not _is_computer(m)]
    matches = [m for m in found
               if want in (m['name'].lower(), m['dns'], m['dns'].split('.')[0], m['ip'], m['node'].lower())]
    listed = [m for m in matches if m['node'] in saved]
    if len(listed) > 1:
        raise DeviceError('ambiguous', f'More than one device is called "{want}". Use its full tailnet name.')
    if not listed:
        if matches:
            raise DeviceError('not_allowed', f"Agents may not use {matches[0]['name']}. Tell the person: an admin "
                                             'can allow it in Settings, Tailscale, under Devices agents may use.')
        raise DeviceError('unknown', f'No device called "{want}" is on this tailnet (List Tailnet Devices shows them).')
    return listed[0], saved[listed[0]['node']]


def _known_hosts() -> str:
    path = tailscale.folder() / 'known_hosts'
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def ssh_argv(machine: dict[str, Any], user: str, command: str, path: str, key_file: str = '') -> list[str]:
    """The ssh command line. Nothing here comes from the agent but ``user``
    (checked against the list) and ``command`` (one argument, for the remote shell)."""
    from cognitrix.tailscale import outbound

    program = find_ssh()
    if program is None:
        raise DeviceError('no_ssh', 'This machine has no ssh program, which running a command on a device needs.')
    argv = [program, '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
            '-o', f'UserKnownHostsFile={_known_hosts()}', '-o', 'GlobalKnownHostsFile=/dev/null' if os.name != 'nt'
            else 'GlobalKnownHostsFile=NUL',
            # One entry per machine, whatever address it has today.
            '-o', f"HostKeyAlias=tailnet-{machine['node']}", '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=30', '-o', 'IdentityAgent=none', '-o', 'ForwardAgent=no',
            '-o', 'ClearAllForwardings=yes', '-o', 'PasswordAuthentication=no',
            '-o', 'KbdInteractiveAuthentication=no']
    if path == PATH_KEY:
        argv += ['-o', 'IdentitiesOnly=yes', '-i', key_file]
    else:
        argv += ['-o', 'PubkeyAuthentication=no']
    if outbound.proxy_port():
        # The built-in Tailscale has no network device: go through its own client.
        base = cli.base_argv()
        if base:
            argv += ['-o', 'ProxyCommand=' + shlex.join([*base, 'nc', '%h', '%p'])]
    return [*argv, f"{user}@{machine['ip']}", '--', command]


def _drain(stream, into: list[bytes], seen: threading.Event | None = None) -> None:
    size = 0
    try:
        # read1: what has arrived, not a full block (the check link comes while ssh still waits).
        for chunk in iter(lambda: stream.read1(4096), b''):
            if size < _MAX_STREAM:
                into.append(chunk)
                size += len(chunk)
            if seen is not None and any(mark in b''.join(into[-3:]) for mark in _CHECK_MARKS):
                seen.set()
    except (OSError, ValueError):
        pass


def _run(argv: list[str], timeout: float, watch_check: bool = False) -> Ran:
    out: list[bytes] = []
    err: list[bytes] = []
    asked = threading.Event()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise DeviceError('no_ssh', f"Couldn't start ssh on this machine: {exc}") from None
    readers = [threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, err, asked if watch_check else None), daemon=True)]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    asked_at = 0.0
    timed_out = check = False
    while proc.poll() is None:
        now = time.monotonic()
        if asked.is_set() and not asked_at:
            asked_at = now
        if asked_at and now - asked_at > _CHECK_GRACE_S:
            check = True
            break
        if now > deadline:
            timed_out = True
            break
        time.sleep(0.05)
    if proc.poll() is None:
        proc.kill()
    proc.wait()
    for reader in readers:
        reader.join(2)
    stdout = b''.join(out).decode('utf-8', 'replace')
    stderr = b''.join(err).decode('utf-8', 'replace')
    stderr = '\n'.join(line for line in stderr.splitlines() if not line.startswith('Warning: Permanently added'))
    return Ran(proc.returncode if proc.returncode is not None else -1, stdout, stderr, timed_out,
               cli.find_url(stderr) if check else '')


def explain(ran: Ran, machine: dict[str, Any], user: str, path: str) -> str:
    """Why ssh itself failed (exit 255), in words an agent can pass on."""
    text = ran.stderr.lower()
    name = machine['name']
    if 'permission denied' in text or 'too many authentication failures' in text:
        if path == PATH_KEY:
            return (f"{name} refused this server's key for {user}. Tell the person: the key shown in Settings, "
                    f"Tailscale has to be in that user's authorized_keys on {name}.")
        return (f"{name} refused: Tailscale SSH there doesn't let this server sign in as {user}. Tell the person: "
                "the SSH part of their tailnet policy has to allow it (a tagged server only reaches tagged "
                "devices), or the device can be switched to this server's own key in Settings, Tailscale.")
    if 'connection refused' in text or 'timed out' in text or 'no route' in text or 'connection closed' in text:
        how = "an SSH server running" if path == PATH_KEY else 'Tailscale SSH turned on (tailscale set --ssh)'
        return f"Couldn't connect to {name} over SSH. It has to be on, and have {how}."
    if 'host key verification failed' in text or 'remote host identification has changed' in text:
        return (f"{name} now shows a different SSH identity than the first time. Nothing was run. Tell the person: "
                'if the device was reinstalled, an admin can remove it in Settings, Tailscale and allow it again.')
    last = next((line for line in reversed(ran.stderr.splitlines()) if line.strip()), '')
    return f"Couldn't run it on {name}: {last[:300] or 'ssh failed'}"


def forget_host(node: str) -> None:
    """Drop the remembered SSH identity of a machine taken off the list."""
    path = tailscale.folder() / 'known_hosts'
    alias = f'tailnet-{node}'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
        kept = [line for line in lines if line.split(' ', 1)[0].split(',')[0] != alias]
        if kept != lines:
            path.write_text('\n'.join(kept) + ('\n' if kept else ''), encoding='utf-8')
    except OSError:
        pass


def run_on(machine: dict[str, Any], user: str, command: str, path: str, timeout: float) -> Ran:
    """Run one command on one machine and wait for it. Blocking."""
    timeout = max(1.0, min(float(timeout or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S))
    if path != PATH_KEY:
        return _run(ssh_argv(machine, user, command, path), timeout, watch_check=True)
    private, _ = _key()
    folder = tempfile.mkdtemp(prefix='cognitrix-ssh-')
    key_file = os.path.join(folder, 'key')
    try:
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(private)
        return _run(ssh_argv(machine, user, command, path, key_file), timeout)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------------------
# What the tools call (here, or in the web process for a worker without Tailscale)
# ---------------------------------------------------------------------------

def usable_here() -> bool:
    """Whether this process can talk to Tailscale. A task worker in its own
    container can't: its calls go to the web process (``via_web``)."""
    status = cli.status()
    return isinstance(status, dict) and status.get('BackendState') == 'Running'


def _fail(code: str, text: str, denied: bool = False) -> dict[str, Any]:
    return {'ok': False, 'code': code, 'text': text, 'denied': denied}


async def perform(device: str, user: str, command: str, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Check the call against the list and run it: {ok, code, text, denied}.
    The caller has already made sure a person approved this exact command."""
    import asyncio

    from cognitrix.common.shell import truncate_output

    command = str(command or '').strip()
    user = str(user or '').strip()
    if not command:
        return _fail('no_command', 'Give the command to run.')
    try:
        machine, row = await resolve(device)
    except DeviceError as exc:
        return _fail(exc.code, str(exc), denied=exc.code == 'not_allowed')
    names = list(row.users or [])
    name = machine['name']
    if not names:
        return _fail('no_user', f'No login name is set for {name} yet. Tell the person: an admin adds one in '
                                'Settings, Tailscale.', denied=True)
    if user not in names:
        return _fail('user_not_allowed', f"You may not run commands as {user or '(no name)'} on {name}. "
                                         f"Allowed: {', '.join(names)}.", denied=True)
    if not machine['online']:
        return _fail('offline', f'{name} is offline on the tailnet right now.')
    path = row.path if row.path in PATHS else PATH_TAILSCALE
    try:
        ran = await asyncio.to_thread(run_on, machine, user, command, path, timeout)
    except DeviceError as exc:
        return _fail(exc.code, str(exc))
    if ran.check_url:
        return _fail('check_needed', "Tailscale wants the person to confirm it's them before it lets this in. Give "
                                     'them this link, and run the command again once they say they opened it: '
                                     f'{ran.check_url}')
    if ran.timed_out:
        text = truncate_output((ran.stdout + ('\n' + ran.stderr if ran.stderr else '')).strip())
        return _fail('timeout', f'It was still running after {int(timeout)} seconds and was stopped here (it may '
                                f'still be running on {name}).\n{text}'.strip())
    if ran.code == 255 and not ran.stdout.strip():
        return _fail('ssh_failed', explain(ran, machine, user, path))
    body = ran.stdout
    if ran.stderr.strip():
        body = f'{body.rstrip()}\n[stderr]\n{ran.stderr.strip()}' if body.strip() else f'[stderr]\n{ran.stderr.strip()}'
    text = f"{user}@{name} exit code {ran.code}\n{truncate_output(body.strip() or '(no output)')}"
    return {'ok': True, 'code': '', 'text': text, 'denied': False} if ran.code == 0 else _fail('command_failed', text)


def describe(found: list[dict[str, Any]]) -> str:
    """The machine list as the agent reads it."""
    lines = []
    for item in found:
        if not item['present']:
            continue
        state = 'online' if item['online'] else 'offline'
        tags = f" [{', '.join(item['tags'])}]" if item['tags'] else ''
        if item['allowed'] and item['users']:
            how = 'Tailscale SSH' if item['path'] == PATH_TAILSCALE else "this server's SSH key"
            use = f"you may run commands as {', '.join(item['users'])} ({how})"
        elif item['allowed']:
            use = 'allowed, but no login name is set for it yet (an admin adds one in Settings, Tailscale)'
        else:
            use = 'not allowed'
        lines.append(f"- {item['name']} ({item['os'] or 'unknown system'}, {state}){tags}: {use}")
    return '\n'.join(lines) or ("No other machines show on this server's tailnet (or Tailscale isn't "
                                'connected here).')


NO_TAILSCALE = _fail('no_tailscale', "Tailscale isn't connected where this is running, and the server's web process "
                                     "couldn't be reached to do it. Tell the person to check Settings, Tailscale.")


async def via_web(op: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    """Ask the web process to do it (the task worker's way, like paired PCs)."""
    from cognitrix.computer.paired import relay

    reply = await relay.send(op, payload, timeout=timeout)
    return reply if isinstance(reply, dict) and 'ok' in reply else dict(NO_TAILSCALE)


async def serve(op: str, data: dict[str, Any]) -> dict[str, Any]:
    """The web process answering a worker (the relay route checks the signature)."""
    if op == 'tailnet_list':
        return {'ok': True, 'code': '', 'text': describe(await listing()), 'denied': False}
    return await perform(str(data.get('device') or ''), str(data.get('user') or ''), str(data.get('command') or ''),
                         min(float(data.get('timeout') or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S))
