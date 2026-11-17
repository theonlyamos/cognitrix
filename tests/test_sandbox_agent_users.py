"""A Linux user per agent in the sandboxed computer (cognitrix/computer/sandbox/users.py)."""
import os
import stat

import pytest

from cognitrix.computer.sandbox import users
from cognitrix.computer.sandbox.users import AgentUser, Users, fix_tree


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(users, 'WORK_ROOT', str(tmp_path / 'work'))
    monkeypatch.setattr(users, 'PROFILES_ROOT', str(tmp_path / '.profiles'))
    monkeypatch.setattr(users, 'STATE_ROOT', str(tmp_path / '.cognitrix-sandbox'))
    monkeypatch.setattr(users, 'HOME', str(tmp_path))
    monkeypatch.setattr(users, 'runtime_dir', lambda user: str(tmp_path / 'run' / user.name))
    return tmp_path


def _pool(n):
    me, group = os.getuid(), os.getgid()
    return [AgentUser(f'cgx{i:02d}', me, group) for i in range(1, n + 1)]


def _users(folders, n=3):
    u = Users(enabled=False, map_file=str(folders / 'map.json'))
    u.enabled = True
    u.pool = _pool(n)
    return u


def test_each_agent_keeps_its_own_user(folders):
    u = _users(folders)
    a, b = u.get('agent-a'), u.get('agent-b')
    assert a.name == 'cgx01' and b.name == 'cgx02'
    again = _users(folders)  # a restarted computer reads the saved map
    assert again.get('agent-a').name == 'cgx01' and again.get('agent-b').name == 'cgx02'


def test_a_full_pool_takes_back_the_longest_idle_user(folders):
    u = _users(folders, n=2)
    u.get('agent-a')
    u.get('agent-b')
    with pytest.raises(RuntimeError):
        u.get('agent-c', running={'agent-a', 'agent-b'})
    assert u.get('agent-c', running={'agent-b'}).name == 'cgx01'
    assert 'agent-a' not in u.known()


def test_off_without_capabilities(folders):
    assert Users(enabled=False, map_file=str(folders / 'map.json')).get('agent-a') is None


def test_prepare_makes_the_agents_folders_group_shared_and_private(folders):
    u = _users(folders)
    old = folders / 'work' / 'agent-a' / 'notes.txt'
    old.parent.mkdir(parents=True)
    old.write_text('hi')
    os.chmod(old, 0o644)
    cookies = folders / '.profiles' / 'agent-a' / 'chromium' / 'Cookies'
    cookies.parent.mkdir(parents=True)
    cookies.write_text('x')
    os.chmod(cookies, 0o600)
    u.prepare('agent-a', u.get('agent-a'))
    for folder in users.agent_folders('agent-a'):
        mode = stat.S_IMODE(os.stat(folder).st_mode)
        assert mode & 0o2770 == 0o2770 and not mode & 0o007
    assert stat.S_IMODE(os.stat(old).st_mode) == 0o660
    assert stat.S_IMODE(os.stat(cookies).st_mode) == 0o660


def test_fix_tree_never_leaves_setuid_files(tmp_path):
    f = tmp_path / 'tool'
    f.write_text('#!/bin/sh')
    os.chmod(f, 0o4755)
    fix_tree(str(tmp_path), os.getuid(), os.getgid())
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o770


def test_agent_env_has_its_own_home_and_installs(folders):
    user = _pool(1)[0]
    env = user.env('agent-a', {'PATH': '/usr/bin', 'HOME': '/home/agent', 'DBUS_SESSION_BUS_ADDRESS': 'x'})
    home = os.path.join(users.WORK_ROOT, 'agent-a')
    assert env['HOME'] == home and env['USER'] == 'cgx01'
    assert env['PATH'].startswith(os.path.join(home, '.local/bin')) and env['PATH'].endswith('/usr/bin')
    assert 'DBUS_SESSION_BUS_ADDRESS' not in env
    assert env['NPM_CONFIG_PREFIX'] == os.path.join(home, '.npm-global')
    assert user.popen_kwargs() == {'user': user.uid, 'group': user.gid, 'extra_groups': [], 'umask': 0o007}


def test_snapshots_hand_files_over_around_saving_and_restoring(tmp_path, monkeypatch):
    from cognitrix.computer.sandbox import snapshots
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'workdir', str(tmp_path))
    calls = []

    class Box:
        image_id = 'img'

        def __init__(self):
            self.passwords, self.drivers = {}, {}

        def request(self, method, path, body=None, timeout=None):
            calls.append((path, body))
            return {'ok': True}

    class Mgr:
        def sandbox(self, user_id):
            return Box()

        def exec_out(self, box, script, fh, timeout=None):
            fh.write(b'archive')
            return 0, ''

        def exec_in(self, box, script, fh, timeout=None):
            return 0, ''

    item = snapshots.create_snapshot(Mgr(), 'u1', agent='agent-a')
    assert ('/agents/agent-a/files', {'give_back': False}) in calls
    calls.clear()
    snapshots.restore_snapshot(Mgr(), 'u1', item['id'], agent='agent-b')
    assert calls[-1] == ('/agents/agent-b/files', {'give_back': True})
