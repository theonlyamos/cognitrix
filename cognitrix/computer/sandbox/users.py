"""A Linux user per agent inside the sandboxed computer.

Each agent's desktop (its driver, browser and every app it opens) and its
shell commands run as a Linux user of its own, so one agent can't read
another agent's files, downloads or browser sign-ins. The image has a pool
of such users (``cgx01``, ``cgx02``, ...); an agent gets one the first time
it needs it and keeps it (``~/.cognitrix-sandbox/agent-users.json``).

The supervisor itself runs as ``agent``, which is in every pool user's group.
It holds four capabilities (SETUID, SETGID, CHOWN, FOWNER) so it can start
processes as an agent's user and hand that agent its folders; nothing it
starts gets them. A container started without them (an older engine) runs
everything as ``agent``, as before.

An agent's folders are its working folder (``~/work/<agent>``, also its home
folder), its browser profiles (``~/.profiles/<agent>``) and its command logs.
They are owned by the agent's user, group-shared with ``agent`` (setgid
folders, group read/write) and closed to everyone else.

Standard library only: the image ships just ``cognitrix.computer``.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import pwd
import stat
import threading
import time
from dataclasses import dataclass

from cognitrix.computer.sandbox import check_name

logger = logging.getLogger('cognitrix.sandbox')

HOME = os.path.expanduser('~')
WORK_ROOT = os.environ.get('COGNITRIX_SANDBOX_WORK', os.path.join(HOME, 'work'))
PROFILES_ROOT = os.path.join(HOME, '.profiles')
STATE_ROOT = os.path.join(HOME, '.cognitrix-sandbox')
MAP_FILE = os.path.join(STATE_ROOT, 'agent-users.json')
PREFIX = os.environ.get('COGNITRIX_SANDBOX_USER_PREFIX', 'cgx')
UMASK = 0o007  # what agents and the supervisor create: owner and group, nobody else

# Capability numbers (linux/capability.h).
CAP_CHOWN, CAP_FOWNER, CAP_SETGID, CAP_SETUID = 0, 3, 6, 7
_NEEDED = (CAP_CHOWN, CAP_FOWNER, CAP_SETGID, CAP_SETUID)


@dataclass(frozen=True)
class AgentUser:
    name: str
    uid: int
    gid: int

    def popen_kwargs(self) -> dict:
        """subprocess.Popen arguments that start a process as this user."""
        return {'user': self.uid, 'group': self.gid, 'extra_groups': [], 'umask': UMASK}

    def env(self, desktop: str, base: dict) -> dict:
        """The environment for this agent's processes: its own home folder."""
        home = agent_home(desktop)
        env = dict(base)
        # Its own installs (pip --user, npm -g, cargo, go) go in its own home,
        # ahead of what 'agent' installed for everyone.
        mine = [os.path.join(home, p) for p in ('.local/bin', '.npm-global/bin', '.cargo/bin', 'go/bin')]
        path = [p for p in str(env.get('PATH') or '/usr/local/bin:/usr/bin:/bin').split(':') if p]
        env.update({'HOME': home, 'USER': self.name, 'LOGNAME': self.name,
                    'COGNITRIX_PROFILES': PROFILES_ROOT, 'XDG_RUNTIME_DIR': runtime_dir(self),
                    'NPM_CONFIG_PREFIX': os.path.join(home, '.npm-global'),
                    'PATH': ':'.join(mine + [p for p in path if p not in mine])})
        for key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'DBUS_SESSION_BUS_ADDRESS',
                    'SESSION_MANAGER', 'MAIL'):
            env.pop(key, None)
        return env


def agent_home(desktop: str) -> str:
    return os.path.join(WORK_ROOT, check_name(desktop))


def runtime_dir(user: AgentUser) -> str:
    return f'/tmp/cognitrix-run/{user.name}'


def agent_folders(desktop: str) -> list[str]:
    desktop = check_name(desktop)
    return [os.path.join(WORK_ROOT, desktop), os.path.join(PROFILES_ROOT, desktop),
            os.path.join(STATE_ROOT, 'jobs', desktop)]


# -- capabilities -------------------------------------------------------------

def _cap_mask(field: str) -> int:
    try:
        with open('/proc/self/status', encoding='ascii') as fh:
            for line in fh:
                if line.startswith(field + ':'):
                    return int(line.split()[1], 16)
    except (OSError, ValueError):
        pass
    return 0


def have_caps() -> bool:
    eff = _cap_mask('CapEff')
    return all(eff & (1 << c) for c in _NEEDED)


def keep_caps_to_self() -> None:
    """Drop the ambient and inheritable capabilities this process was started
    with, so the programs it starts (apps, commands, the desktops) get none.
    The process keeps its own effective set."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL = 47, 4
        libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)

        class Header(ctypes.Structure):
            _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]

        class Data(ctypes.Structure):
            _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                        ('inheritable', ctypes.c_uint32)]

        header = Header(0x20080522, 0)  # _LINUX_CAPABILITY_VERSION_3
        data = (Data * 2)()
        if libc.capget(ctypes.byref(header), data) == 0:
            data[0].inheritable = data[1].inheritable = 0
            libc.capset(ctypes.byref(header), data)
    except Exception:
        logger.debug("Couldn't clear inherited capabilities", exc_info=True)


# -- the pool -------------------------------------------------------------------

def pool() -> list[AgentUser]:
    out = []
    for entry in pwd.getpwall():
        name = entry.pw_name
        if name.startswith(PREFIX) and name[len(PREFIX):].isdigit():
            out.append(AgentUser(name, entry.pw_uid, entry.pw_gid))
    return sorted(out, key=lambda u: u.name)


def _in_group(gid: int) -> bool:
    return gid in os.getgroups() or gid == os.getgid()


class Users:
    """Which pool user each agent has. Off (``enabled`` False) when the
    image has no pool or the supervisor lacks the capabilities."""

    def __init__(self, enabled: bool | None = None, map_file: str = MAP_FILE) -> None:
        self.map_file = map_file
        self._guard = threading.Lock()
        self._prepared: set[str] = set()
        users = pool()
        self.pool = [u for u in users if _in_group(u.gid)]
        if enabled is None:
            enabled = bool(self.pool) and have_caps()
            if users and not enabled:
                logger.info('Agents share one Linux user: the container was started without the capabilities '
                            'it needs to give each agent its own (an older engine started it).')
        self.enabled = enabled
        if enabled:
            # Agents' users pass through these to reach their own folders,
            # without seeing what else is in them.
            for folder in (HOME, WORK_ROOT, PROFILES_ROOT, STATE_ROOT, os.path.join(STATE_ROOT, 'jobs'),
                           '/tmp/cognitrix-run'):
                try:
                    os.makedirs(folder, exist_ok=True)
                    os.chmod(folder, 0o711)
                except OSError:
                    pass

    def _load(self) -> dict:
        try:
            with open(self.map_file, encoding='utf-8') as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.map_file), exist_ok=True)
        tmp = self.map_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, self.map_file)

    def get(self, desktop: str, running: set[str] | frozenset = frozenset()) -> AgentUser | None:
        """The agent's user, given one from the pool if it has none yet.
        ``running`` are agents whose user can't be taken back right now."""
        if not self.enabled:
            return None
        desktop = check_name(desktop)
        by_name = {u.name: u for u in self.pool}
        with self._guard:
            data = self._load()
            entry = data.get(desktop)
            if isinstance(entry, dict) and entry.get('user') in by_name:
                entry['used'] = time.time()
                self._save(data)
                return by_name[entry['user']]
            taken = {e.get('user'): name for name, e in data.items() if isinstance(e, dict)}
            free = [u for u in self.pool if u.name not in taken]
            if not free:
                # Every user is taken: take back the one used longest ago from
                # an agent that isn't working now. Its folders go back to
                # 'agent' first, so the next holder can't read them.
                idle = sorted(((e.get('used', 0), name) for name, e in data.items()
                               if isinstance(e, dict) and name not in running and e.get('user') in by_name))
                if not idle:
                    raise RuntimeError('every agent user on this computer is busy')
                _, old = idle[0]
                user = by_name[data.pop(old)['user']]
                self._prepared.discard(old)
                for folder in agent_folders(old):
                    fix_tree(folder, os.getuid(), os.getgid())
                free = [user]
            user = free[0]
            data[desktop] = {'user': user.name, 'used': time.time()}
            self._save(data)
            return user

    def prepare(self, desktop: str, user: AgentUser, force: bool = False) -> None:
        """Make the agent's folders and hand them to its user (once per run of
        the computer, or again when ``force``: after a snapshot was restored
        or a setup copied in, the files arrive owned by 'agent')."""
        key = f'{desktop}:{user.name}'
        if key in self._prepared and not force:
            return
        for folder in agent_folders(desktop):
            os.makedirs(folder, exist_ok=True)
            fix_tree(folder, user.uid, user.gid)
        os.makedirs(runtime_dir(user), exist_ok=True)
        os.chown(runtime_dir(user), user.uid, user.gid)
        os.chmod(runtime_dir(user), 0o700)
        self._prepared.add(key)

    def share(self, desktop: str) -> None:
        """Let 'agent' read everything in the agent's folders (browsers write
        their files for the owner only), for snapshots and copies."""
        user = self.get(desktop) if self.enabled else None
        if user is None:
            return
        for folder in agent_folders(desktop):
            if os.path.exists(folder):
                fix_tree(folder, user.uid, user.gid)

    def known(self) -> list[str]:
        return [name for name, e in self._load().items() if isinstance(e, dict)] if self.enabled else []


def fix_tree(path: str, uid: int, gid: int) -> None:
    """Own ``path`` and everything under it by uid:gid; folders setgid and
    group read/write, files group read/write, nothing for others."""
    if not os.path.lexists(path):
        return
    _fix(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            _fix(os.path.join(root, name), uid, gid)


def _fix(path: str, uid: int, gid: int) -> None:
    try:
        st = os.lstat(path)
        if st.st_uid != uid or st.st_gid != gid:
            os.lchown(path, uid, gid)
        if stat.S_ISLNK(st.st_mode):
            return
        mode = stat.S_IMODE(st.st_mode)
        if stat.S_ISDIR(st.st_mode):
            want = (mode | 0o2770) & ~0o007
        else:
            owner = (mode >> 6) & 0o7
            want = (mode & ~0o077) | (owner << 3)
            want = (want | 0o060) & ~0o007
            want &= ~0o6000  # never setuid/setgid files
        if want != mode:
            os.chmod(path, want)
    except OSError:
        pass


# -- what an agent's file tools may touch -----------------------------------------

def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip('/') + '/')


class Boundary:
    """What one agent may read and write through the computer's file tools,
    which run in the supervisor (as 'agent', a member of every agent's group):
    the same as the agent's own Linux user could, so the tools are no way
    around it. Paths are checked after following links."""

    def __init__(self, desktop: str, user: AgentUser, everyone: list[AgentUser]) -> None:
        self.desktop = check_name(desktop)
        self.own = [os.path.realpath(p) for p in agent_folders(desktop)] + [os.path.realpath(runtime_dir(user))]
        state = os.environ.get('COGNITRIX_SANDBOX_STATE', '/tmp/cognitrix-sandbox')
        self.closed = [os.path.realpath(p) for p in (HOME, state, '/tmp/cognitrix-run')]
        self.other_uids = {u.uid for u in everyone if u.uid != user.uid}
        self.other_gids = {u.gid for u in everyone if u.gid != user.gid}

    def allows(self, path: str) -> bool:
        real = os.path.realpath(path)
        if any(_within(real, root) for root in self.own):
            return True
        if any(_within(real, root) for root in self.closed):
            return False
        # Elsewhere (/tmp, /opt, ...): not what another agent's user owns, nor
        # inside a folder of theirs.
        probe = real
        while True:
            try:
                st = os.lstat(probe)
                if st.st_uid in self.other_uids or st.st_gid in self.other_gids:
                    return False
            except OSError:
                pass
            parent = os.path.dirname(probe)
            if parent == probe:
                return True
            probe = parent
