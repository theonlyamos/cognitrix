"""Snapshots of the agents' sandboxed computer, and copying one agent's setup
to another agent.

Two kinds of snapshot, both saved as .tar.gz files in the engine's data folder
(outside the container, so agents can't read or change them):

- **Whole computer**: everything in the home folder (installed tools, every
  agent's files and browser sign-ins, Apps). Restoring replaces the whole
  computer with it; open windows close.
- **One agent's setup**: that agent's folder (``~/work/<agent>``), its
  browser profiles (``~/.profiles/<agent>``) and its copied-in project
  baselines. It can be restored into any agent, which is how a set-up agent
  is copied for another one (or for many swarm workers).

Copying an agent's setup straight to another agent needs no snapshot.

Everything that reads or writes the home folder runs inside the container (or
a throwaway container on the same volume) as the unprivileged ``agent`` user,
so nothing in a snapshot ever lands on this machine except as one archive file.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from cognitrix.computer import DriverError, Unsupported
from cognitrix.computer.sandbox import check_name

logger = logging.getLogger('cognitrix.sandbox')

MAX_SNAPSHOTS = int(os.environ.get('COGNITRIX_SANDBOX_MAX_SNAPSHOTS', '20') or 20)
_TIMEOUT = 3600
_NAME = re.compile(r'^[^\x00-\x1f]{1,80}$')
_ID = re.compile(r'^[a-z0-9]{12}$')
# Never worth keeping: job logs, the key proxy's files (made again on use),
# browser caches and the locks a running browser holds.
_EXCLUDES = ('--exclude=./.cognitrix-sandbox/jobs', '--exclude=./.cognitrix-sandbox/proxy',
             '--exclude=Cache_Data', '--exclude=Code Cache', '--exclude=GPUCache', '--exclude=GrShaderCache',
             '--exclude=SingletonLock', '--exclude=SingletonSocket', '--exclude=SingletonCookie')
_AGENT_PARTS = (('work', 'agent/work'), ('.profiles', 'agent/profiles'),
                ('.cognitrix-sandbox/baselines', 'agent/baselines'))

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class SnapshotError(Exception):
    pass


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _folder(user_id: Any) -> Path:
    from cognitrix.computer.sandbox.client import user_key
    from cognitrix.config import settings
    path = Path(settings.workdir) / 'sandbox-snapshots' / user_key(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_file(user_id: Any) -> Path:
    return _folder(user_id) / 'index.json'


def _read_index(user_id: Any) -> list[dict]:
    try:
        data = json.loads(_index_file(user_id).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict) and _ID.match(str(d.get('id', '')))] if isinstance(data, list) else []


def _write_index(user_id: Any, items: list[dict]) -> None:
    path = _index_file(user_id)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def list_snapshots(user_id: Any) -> list[dict]:
    """Newest first. Entries whose file is gone are left out."""
    folder = _folder(user_id)
    items = [d for d in _read_index(user_id) if (folder / f"{d['id']}.tar.gz").exists()]
    return sorted(items, key=lambda d: d.get('created_at', 0), reverse=True)


def _find(user_id: Any, snapshot_id: str) -> dict:
    for item in _read_index(user_id):
        if item.get('id') == snapshot_id:
            if not (_folder(user_id) / f'{snapshot_id}.tar.gz').exists():
                break
            return item
    raise SnapshotError("That snapshot doesn't exist any more.")


def _clean_name(name: Any, fallback: str) -> str:
    text = ' '.join(str(name or '').split())[:80] or fallback
    if not _NAME.match(text):
        raise SnapshotError("That name can't be used.")
    return text


# -- running tar in the container ---------------------------------------------

def _exec_to_file(mgr: Any, container: str, script: str, target: Path) -> int:
    """Run ``script`` as the agent user in the container; its output is the
    archive. GNU tar exits 1 when files changed while it read them, which is
    fine for a computer that's in use."""
    tmp = target.with_suffix('.part')
    try:
        with open(tmp, 'wb') as fh:
            got = subprocess.run([mgr.docker, 'exec', '-u', 'agent', container, 'sh', '-c', script],  # noqa: S603
                                 stdout=fh, stderr=subprocess.PIPE, timeout=_TIMEOUT)
        if got.returncode not in (0, 1) or tmp.stat().st_size == 0:
            detail = (got.stderr or b'').decode(errors='replace').strip().splitlines()[-1:] or ['']
            if got.returncode == 3:
                raise SnapshotError('That agent has nothing on the computer yet.')
            raise SnapshotError(f"Couldn't save the snapshot: {detail[0][:300]}")
        os.replace(tmp, target)
        return target.stat().st_size
    except FileNotFoundError:
        raise Unsupported("The agents' computer needs Docker, and Docker isn't installed here.") from None
    except subprocess.TimeoutExpired:
        raise SnapshotError('Saving the snapshot took too long.') from None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _exec_from_file(args: list[str], source: Path) -> None:
    try:
        with open(source, 'rb') as fh:
            got = subprocess.run(args, stdin=fh, capture_output=True,  # noqa: S603
                                 timeout=_TIMEOUT)
    except FileNotFoundError:
        raise Unsupported("The agents' computer needs Docker, and Docker isn't installed here.") from None
    except subprocess.TimeoutExpired:
        raise SnapshotError('Restoring the snapshot took too long.') from None
    if got.returncode != 0:
        detail = (got.stderr or b'').decode(errors='replace').strip().splitlines()[-1:] or ['']
        raise SnapshotError(f"Couldn't restore the snapshot: {detail[0][:300]}")


def _agent_paths(agent: str) -> list[tuple[str, str]]:
    return [(f'{base}/{agent}', inner) for base, inner in _AGENT_PARTS]


def agent_save_script(agent: str) -> str:
    agent = check_name(agent)
    pairs = _agent_paths(agent)
    exists = ' '.join(f'[ -e "{src}" ] && set -- "$@" "{src}";' for src, _ in pairs)
    transforms = ' '.join(f"--transform='s,^{_bre(src)},{inner},S'" for src, inner in pairs)
    return (f'cd "$HOME" || exit 2; set --; {exists} [ "$#" -gt 0 ] || exit 3; '
            f'exec tar --warning=no-file-changed {" ".join(_quote(e) for e in _EXCLUDES)} {transforms} -czf - "$@"')


def agent_restore_script(agent: str) -> str:
    agent = check_name(agent)
    pairs = _agent_paths(agent)
    remove = ' '.join(f'"{src}"' for src, _ in pairs)
    transforms = ' '.join(f"--transform='s,^{_bre(inner)},{src},S'" for src, inner in pairs)
    return (f'cd "$HOME" || exit 2; rm -rf {remove} && mkdir -p work .profiles .cognitrix-sandbox/baselines '
            f'&& tar -xzf - --no-same-owner {transforms}')


def copy_script(source: str, target: str) -> str:
    source, target = check_name(source), check_name(target)
    if source == target:
        raise SnapshotError('Pick two different agents.')
    steps = [f'rm -rf "{base}/{target}"' for base, _ in _AGENT_PARTS]
    copied = [f'if [ -e "{base}/{source}" ]; then mkdir -p "{base}" && cp -a "{base}/{source}" "{base}/{target}" '
              f'&& n=$((n+1)); fi' for base, _ in _AGENT_PARTS]
    return (f'cd "$HOME" || exit 2; n=0; {"; ".join(steps)}; {"; ".join(copied)}; '
            'find .profiles -maxdepth 3 -name "Singleton*" -path "*/' + target + '/*" -exec rm -f {} + 2>/dev/null; '
            '[ "$n" -gt 0 ] || exit 3')


def _bre(text: str) -> str:
    """A path as a literal in tar's --transform regex (names are [a-z0-9_-])."""
    return text.replace('.', r'\.')


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


# -- the operations ---------------------------------------------------------

def create_snapshot(mgr: Any, user_id: Any, name: str | None = None, agent: str | None = None) -> dict:
    """Save the whole computer, or one agent's setup when ``agent`` is a desktop name."""
    from cognitrix.computer.sandbox.client import user_key
    if agent is not None:
        agent = check_name(agent)
    with _lock(user_key(user_id)):
        items = _read_index(user_id)
        if len(items) >= MAX_SNAPSHOTS:
            raise SnapshotError(f'You have {len(items)} snapshots, the most allowed. Delete one first.')
        box = mgr.sandbox(user_id)
        snapshot_id = secrets.token_hex(6)
        target = _folder(user_id) / f'{snapshot_id}.tar.gz'
        if agent:
            script = agent_save_script(agent)
        else:
            script = f'cd "$HOME" || exit 2; exec tar --warning=no-file-changed {" ".join(_quote(e) for e in _EXCLUDES)} -czf - .'
        size = _exec_to_file(mgr, box.name, script, target)
        item = {'id': snapshot_id, 'name': _clean_name(name, 'Agent setup' if agent else 'Whole computer'),
                'kind': 'agent' if agent else 'computer', 'agent': agent, 'size': size,
                'created_at': time.time(), 'image_id': box.image_id}
        items.append(item)
        _write_index(user_id, items)
        return item


def delete_snapshot(user_id: Any, snapshot_id: str) -> None:
    from cognitrix.computer.sandbox.client import user_key
    with _lock(user_key(user_id)):
        items = _read_index(user_id)
        keep = [d for d in items if d.get('id') != snapshot_id]
        if len(keep) == len(items):
            raise SnapshotError("That snapshot doesn't exist any more.")
        _write_index(user_id, keep)
        try:
            (_folder(user_id) / f'{snapshot_id}.tar.gz').unlink()
        except OSError:
            pass


def _stop_desktop(box: Any, agent: str) -> None:
    """Close an agent's desktop (its browser holds the profile open)."""
    try:
        box.request('DELETE', f'/desktops/{agent}', timeout=30)
    except DriverError:
        pass
    box.passwords.pop(agent, None)
    box.drivers.pop(agent, None)


def restore_snapshot(mgr: Any, user_id: Any, snapshot_id: str, agent: str | None = None) -> dict:
    """Put a snapshot back. A whole-computer snapshot replaces the computer
    (open windows close, then the same desktops open again). An agent setup
    replaces ``agent``'s setup (default: the agent it was saved from)."""
    from cognitrix.computer.sandbox.client import user_key
    with _lock(user_key(user_id)):
        item = _find(user_id, snapshot_id)
        source = _folder(user_id) / f'{snapshot_id}.tar.gz'
        if item.get('kind') == 'agent':
            target = check_name(agent or item.get('agent') or '')
            box = mgr.sandbox(user_id)
            _stop_desktop(box, target)
            _exec_from_file([mgr.docker, 'exec', '-i', '-u', 'agent', box.name, 'sh', '-c',
                             agent_restore_script(target)], source)
            return {'ok': True, 'kind': 'agent', 'agent': target}
        return _restore_computer(mgr, user_id, source)


def _restore_computer(mgr: Any, user_id: Any, source: Path) -> dict:
    from cognitrix.computer.sandbox.client import user_key
    name = mgr.container_name(user_id)
    try:
        old = mgr.existing(user_id)
        desktops = [d['name'] for d in (old.desktops() if old else []) if d.get('name')]
    except Exception:
        desktops = []
    with mgr._guard:
        mgr._docker('rm', '-f', name, check=False)
        mgr._boxes.pop(name, None)
    volume = f'cognitrix-sandbox-home-{user_key(user_id)}'
    # A throwaway container on the same home volume, without network, empties
    # it and unpacks the snapshot as the agent user.
    _exec_from_file([mgr.docker, 'run', '--rm', '-i', '--network', 'none', '--cap-drop', 'ALL',
                     '--security-opt', 'no-new-privileges', '-u', 'agent', '-v', f'{volume}:/home/agent',
                     '--entrypoint', 'sh', mgr.image, '-c',
                     'cd /home/agent && find . -mindepth 1 -delete && tar -xzf - --no-same-owner'], source)
    box = mgr.sandbox(user_id)
    for desktop in desktops:
        try:
            box.desktop(desktop)
        except Exception:
            logger.info("Couldn't reopen desktop %s after restoring a snapshot", desktop, exc_info=True)
    return {'ok': True, 'kind': 'computer'}


def copy_agent(mgr: Any, user_id: Any, source: str, target: str) -> dict:
    """Give ``target`` a copy of ``source``'s folder, browser profiles and
    project baselines (``target``'s own are replaced)."""
    from cognitrix.computer.sandbox.client import user_key
    script = copy_script(source, target)
    with _lock(user_key(user_id)):
        box = mgr.sandbox(user_id)
        _stop_desktop(box, target)
        got = mgr._docker('exec', '-u', 'agent', box.name, 'sh', '-c', script, check=False, timeout=_TIMEOUT)
        if got.returncode == 3:
            raise SnapshotError('That agent has nothing on the computer yet.')
        if got.returncode != 0:
            detail = (got.stderr or got.stdout or '').strip().splitlines()[-1:] or ['']
            raise SnapshotError(f"Couldn't copy the setup: {detail[0][:300]}")
    return {'ok': True, 'from': source, 'to': target}
