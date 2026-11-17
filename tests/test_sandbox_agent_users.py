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


# -- file tools stay inside what the agent's own user could reach --------------

@pytest.fixture
def fenced(folders, monkeypatch):
    import shutil

    from cognitrix.computer.sandbox import workspace as ws_mod

    if shutil.which('git') is None:
        pytest.skip('needs git')
    monkeypatch.setattr(ws_mod, 'HOME', str(folders))
    monkeypatch.setattr(ws_mod, 'WORK_ROOT', users.WORK_ROOT)
    monkeypatch.setattr(ws_mod, 'STATE_ROOT', users.STATE_ROOT)
    monkeypatch.setenv('COGNITRIX_SANDBOX_STATE', str(folders / 'state'))
    me = AgentUser('cgx01', os.getuid(), os.getgid())
    other = AgentUser('cgx02', 99998, 99998)
    monkeypatch.setattr(users, 'pool', lambda: [me, other])
    ws = ws_mod.Workspace(user_for=lambda agent: me)
    for agent in ('agent-a', 'agent-b'):
        for folder in users.agent_folders(agent):
            os.makedirs(folder, exist_ok=True)
    (folders / 'work' / 'agent-b' / 'secret.txt').write_text('token=abc')
    return ws


def test_file_tools_refuse_another_agents_folder(fenced, folders):
    from cognitrix.computer.sandbox.workspace import WorkspaceError

    other = str(folders / 'work' / 'agent-b' / 'secret.txt')
    for call in (lambda: fenced.read_file('agent-a', other),
                 lambda: fenced.write_file('agent-a', other, 'x'),
                 lambda: fenced.read_file('agent-a', str(folders / 'state' / 'agent-b.vncpass'))):
        with pytest.raises(WorkspaceError) as err:
            call()
        assert err.value.code == 'not_allowed'
    # A link in its own folder doesn't get around it.
    os.symlink(str(folders / 'work' / 'agent-b'), str(folders / 'work' / 'agent-a' / 'peek'))
    with pytest.raises(WorkspaceError):
        fenced.read_file('agent-a', 'peek/secret.txt')
    fenced.write_file('agent-a', 'mine.txt', 'hello')
    assert fenced.read_file('agent-a', 'mine.txt')['text'].endswith('hello')
    with pytest.raises(WorkspaceError):  # its user can't list the folder that holds everyone's
        fenced.grep('agent-a', 'token', path=str(folders / 'work'))
    # Searching elsewhere skips links into another agent's folder.
    import tempfile
    with tempfile.TemporaryDirectory() as shared:
        os.symlink(other, os.path.join(shared, 'leak.txt'))
        open(os.path.join(shared, 'fine.txt'), 'w').write('token=public')
        found = fenced.grep('agent-a', 'token', path=shared)
        assert found['hits'] == 1 and 'fine.txt' in found['lines'][0]
        assert fenced.glob('agent-a', '*.txt', path=shared)['matches'] == ['fine.txt']


def test_import_refuses_a_link_that_leads_out_of_the_project(fenced, folders):
    import io
    import tarfile

    from cognitrix.computer.sandbox.workspace import WorkspaceError

    def archive(files):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            for name, text in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(text)
                tar.addfile(info, io.BytesIO(text.encode()))
        return buf.getvalue()

    fenced.import_project('agent-a', 'proj', archive({'src/a.txt': 'one'}))
    project = folders / 'work' / 'agent-a' / 'proj'
    # The agent swaps a folder in its project for a link to another agent's folder.
    import shutil
    shutil.rmtree(project / 'src')
    os.symlink(str(folders / 'work' / 'agent-b'), str(project / 'src'))
    with pytest.raises(WorkspaceError) as err:
        fenced.import_project('agent-a', 'proj', archive({'src/secret.txt': 'overwritten'}), replace=True)
    assert err.value.code == 'bad_archive'
    assert (folders / 'work' / 'agent-b' / 'secret.txt').read_text() == 'token=abc'


def test_inside_follows_links():
    import tempfile

    from cognitrix.computer.sandbox.workspace import _inside

    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as away:
        os.makedirs(os.path.join(root, 'ok'))
        os.symlink(away, os.path.join(root, 'out'))
        assert _inside(root, 'ok/file') == os.path.join(os.path.realpath(root), 'ok', 'file')
        assert _inside(root, 'out/file') is None
        assert _inside(root, '../x') is None


# -- running computers move onto the image with agent users --------------------

def test_a_container_started_without_the_capabilities_is_outdated(monkeypatch):
    from cognitrix.computer.sandbox.client import SandboxManager

    mgr = SandboxManager(image='img')
    monkeypatch.setattr(mgr, '_local_image_id', lambda fresh=False: 'sha256:new')
    labels = {'Config': {'Labels': {'cognitrix.agent-users': '1'}}, 'Image': 'sha256:new'}
    assert mgr._outdated({**labels, 'HostConfig': {'CapAdd': None}})
    assert not mgr._outdated({**labels, 'HostConfig': {'CapAdd': ['CAP_CHOWN', 'CAP_FOWNER', 'CAP_SETGID',
                                                                  'CAP_SETUID']}})
    assert not mgr._outdated({'Config': {'Labels': {}}, 'Image': 'sha256:new', 'HostConfig': {}})
    assert mgr._outdated({'Config': {'Labels': {}}, 'Image': 'sha256:old', 'HostConfig': {}})


def test_idle_outdated_computers_are_restarted(monkeypatch):
    from cognitrix.computer.sandbox import client
    from cognitrix.computer.sandbox.client import Sandbox, SandboxManager

    mgr = SandboxManager(image='img')
    box = Sandbox('cognitrix-sandbox-x', 't', 1, 2)
    box.owner = 'u1'
    mgr._boxes[box.name] = box
    monkeypatch.setattr(mgr, '_inspect', lambda name: {'State': {'Running': True}})
    monkeypatch.setattr(mgr, '_outdated', lambda info: True)
    updated = []
    monkeypatch.setattr(mgr, 'update', lambda owner: updated.append(owner))
    answers = {'busy': ['a command is running'], 'idle_s': 9999}
    monkeypatch.setattr(box, 'request', lambda *a, **k: answers)
    assert mgr.update_idle() == [] and not updated
    answers.update(busy=[], idle_s=5)
    assert mgr.update_idle() == []
    answers.update(idle_s=client.AUTO_UPDATE_IDLE_S + 1)
    mgr.viewer_opened('u1')
    assert mgr.update_idle() == []  # someone is watching it
    mgr.viewer_closed('u1')
    assert mgr.update_idle() == [box.name] and updated == ['u1']
