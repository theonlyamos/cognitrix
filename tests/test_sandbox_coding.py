"""Coding in the sandbox: the container's workspace, copying folders in and
back, test summaries, approvals, environment variables and sign-ins.

The container side (``cognitrix/computer/sandbox/workspace.py``) runs here
directly on a temporary home folder; a real container is exercised by
``test_sandbox_computer_live.py``-style manual runs.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from cognitrix.common import sandbox_work as sw
from cognitrix.common import test_results
from cognitrix.common.folders import Folder
from cognitrix.computer.sandbox import workspace as ws_mod
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context

needs_git = pytest.mark.skipif(shutil.which('git') is None or os.name == 'nt', reason='needs git and a POSIX shell')


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_mod, 'HOME', str(tmp_path))
    monkeypatch.setattr(ws_mod, 'WORK_ROOT', str(tmp_path / 'work'))
    monkeypatch.setattr(ws_mod, 'STATE_ROOT', str(tmp_path / '.cognitrix-sandbox'))
    return ws_mod.Workspace()


@pytest.fixture
def ctx():
    tokens = []

    def bind(**kwargs):
        tokens.append(set_execution_context(ToolExecutionContext(**kwargs)))

    yield bind
    for token in reversed(tokens):
        reset_execution_context(token)


def _tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# -- container side ------------------------------------------------------------

@needs_git
def test_commands_remember_the_folder_and_report_exit_codes(workspace):
    out = workspace.run('agent-1', 'echo hi; cd /tmp')
    assert out['exit_code'] == 0 and 'hi' in out['output']
    assert workspace.run('agent-1', 'pwd; exit 3')['exit_code'] == 3
    assert workspace.cwd('agent-1') == '/tmp'
    # each agent has its own folder and current directory
    assert workspace.cwd('agent-2').endswith('/work/agent-2')


@needs_git
def test_commands_time_out_and_background_jobs_are_read_in_parts(workspace):
    out = workspace.run('a', 'sleep 20', timeout=1)
    assert out['killed'] == 'timeout' and not out['running']
    job = workspace.run('a', 'echo one; sleep 0.6; echo two', background=True)
    assert job['running']
    time.sleep(1.2)
    rest = workspace.read(job['id'], 'a')
    assert 'two' in rest['output'] and rest['exit_code'] == 0
    with pytest.raises(ws_mod.WorkspaceError):
        workspace.read(job['id'], 'someone-else')


@needs_git
def test_variables_reach_the_command_but_not_the_container_token(workspace, monkeypatch):
    monkeypatch.setenv('COGNITRIX_SANDBOX_TOKEN', 'secret-token')
    out = workspace.run('a', 'echo "[$APP_MODE] [$COGNITRIX_SANDBOX_TOKEN]"', env={'APP_MODE': 'test', 'bad name': 'x'})
    assert '[test] []' in out['output']


@needs_git
def test_files_read_write_edit_glob_grep(workspace):
    workspace.write_file('a', 'proj/src/app.py', 'x = 1\nprint(x)\n')
    assert workspace.read_file('a', 'proj/src/app.py', start=2)['text'].endswith('print(x)')
    assert workspace.edit_file('a', 'proj/src/app.py', 'x = 1', 'x = 2')['count'] == 1
    with pytest.raises(ws_mod.WorkspaceError):
        workspace.edit_file('a', 'proj/src/app.py', 'nope', 'x')
    assert workspace.glob('a', '*.py', 'proj')['matches'] == ['src/app.py']
    assert workspace.grep('a', 'x = 2', 'proj')['lines'] == ['src/app.py:1:x = 2']


@needs_git
def test_project_changes_export_and_baseline(workspace):
    workspace.import_project('a', 'calc', _tar({'calc.py': 'a - b\n', 'pkg/b.txt': 'b\n'}), source='/home/me/calc')
    root = Path(workspace.project_dir('a', 'calc'))
    (root / 'calc.py').write_text('a + b\n')
    (root / 'pkg' / 'b.txt').unlink()
    (root / 'new.py').write_text('n\n')
    (root / 'node_modules' / 'x').mkdir(parents=True)
    (root / 'node_modules' / 'x' / 'i.js').write_text('ignored\n')
    changes = workspace.changes('a', 'calc')
    assert {(f['path'], f['status']) for f in changes['files']} == {
        ('calc.py', 'modified'), ('pkg/b.txt', 'deleted'), ('new.py', 'added')}
    assert changes['source'] == '/home/me/calc' and '+a + b' in changes['patch']

    export = workspace.export('a', 'calc', ['calc.py', 'pkg/b.txt'])
    by_path = {f['path']: f for f in export['files']}
    assert base64.b64decode(by_path['calc.py']['data']) == b'a + b\n'
    assert by_path['calc.py']['base'] == sw.git_blob_id(b'a - b\n')
    assert 'data' not in by_path['pkg/b.txt']

    workspace.synced('a', 'calc', ['calc.py', 'pkg/b.txt'])
    assert [f['path'] for f in workspace.changes('a', 'calc')['files']] == ['new.py']


@needs_git
def test_copying_in_again_refuses_to_drop_unsynced_changes(workspace):
    archive = _tar({'a.txt': '1\n'})
    workspace.import_project('a', 'p', archive)
    (Path(workspace.project_dir('a', 'p')) / 'a.txt').write_text('2\n')
    with pytest.raises(ws_mod.WorkspaceError) as err:
        workspace.import_project('a', 'p', archive)
    assert err.value.code == 'unsynced_changes'
    workspace.import_project('a', 'p', _tar({'b.txt': 'new\n'}), replace=True)
    names = sorted(os.listdir(workspace.project_dir('a', 'p')))
    assert names == ['b.txt']  # a.txt is gone from the computer, so from the copy too


@needs_git
@pytest.mark.parametrize('bad', ['../escape.txt', '/etc/passwd'])
def test_archives_with_paths_outside_the_project_are_refused(workspace, bad):
    with pytest.raises(ws_mod.WorkspaceError):
        workspace.import_project('a', 'p', _tar({bad: 'x'}))


def test_project_names_are_checked():
    assert ws_mod.project_name('my app') == 'my app'
    for bad in ('', '..', 'a/b', '.hidden'):
        with pytest.raises(ws_mod.WorkspaceError):
            ws_mod.project_name(bad)


# -- engine side -----------------------------------------------------------------

@needs_git
def test_pack_folder_follows_gitignore_and_includes_git(tmp_path):
    (tmp_path / 'app.py').write_text('print(1)\n')
    (tmp_path / '.gitignore').write_text('.env\nnode_modules/\n')
    (tmp_path / '.env').write_text('SECRET=1\n')
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'node_modules' / 'x.js').write_text('x')
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    archive, count, notes = sw.pack_folder(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        names = set(tar.getnames())
    assert {'app.py', '.gitignore'} <= names
    assert '.env' not in names and 'node_modules/x.js' not in names
    assert any(n.startswith('.git/') for n in names)
    assert count == 2 and 'git ignores' in notes[0]


def test_pack_folder_without_git_skips_installed_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, '_git_files', lambda folder: None)
    (tmp_path / 'main.go').write_text('package main\n')
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'node_modules' / 'x.js').write_text('x')
    archive, count, _ = sw.pack_folder(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        assert tar.getnames() == ['main.go']


def _export(path: str, status: str, data: bytes | None, base: bytes | None) -> dict:
    item = {'path': path, 'status': status, 'base': sw.git_blob_id(base) if base is not None else None}
    if data is not None:
        item['data'] = base64.b64encode(data).decode()
    return item


def test_apply_export_writes_deletes_and_skips_conflicts(tmp_path, ctx):
    ctx(folders=(Folder(str(tmp_path), 'write', 'proj', True),))
    (tmp_path / 'same.py').write_text('old\n')
    (tmp_path / 'moved.py').write_text('changed on the computer\n')
    (tmp_path / 'gone.py').write_text('bye\n')
    export = {'files': [
        _export('same.py', 'modified', b'new\n', b'old\n'),
        _export('moved.py', 'modified', b'sandbox\n', b'original\n'),
        _export('gone.py', 'deleted', None, b'bye\n'),
        _export('sub/added.py', 'added', b'hi\n', None),
        _export('../outside.py', 'added', b'x', None),
        _export('.git/config', 'added', b'x', None),
    ]}
    report = sw.apply_export(tmp_path, export)
    assert sorted(report.written) == ['same.py', 'sub/added.py']
    assert report.deleted == ['gone.py'] and report.conflicts == ['moved.py']
    assert sorted(report.refused) == ['../outside.py', '.git/config']
    assert (tmp_path / 'same.py').read_text() == 'new\n'
    assert (tmp_path / 'moved.py').read_text() == 'changed on the computer\n'
    assert not (tmp_path / 'gone.py').exists()
    assert not (tmp_path.parent / 'outside.py').exists()

    forced = sw.apply_export(tmp_path, {'files': [_export('moved.py', 'modified', b'sandbox\n', b'original\n')]},
                             overwrite=True)
    assert forced.written == ['moved.py']


def test_apply_export_refuses_read_only_folders(tmp_path, ctx):
    ctx(folders=(Folder(str(tmp_path), 'read', 'proj', True),))
    report = sw.apply_export(tmp_path, {'files': [_export('a.py', 'added', b'x', None)]})
    assert report.refused == ['a.py'] and not (tmp_path / 'a.py').exists()


# -- test summaries ------------------------------------------------------------------

@pytest.mark.parametrize('output, expected', [
    ('F.\nFAILED tests/test_a.py::test_x - AssertionError: boom\n==== 1 failed, 3 passed, 1 skipped in 0.52s ====',
     ('pytest', 3, 1, 1, ['tests/test_a.py::test_x'])),
    ('Ran 4 tests in 0.003s\n\nFAILED (failures=1, errors=1)\nFAIL: test_b (mod.T)\n', ('unittest', 2, 1, 0, ['test_b (mod.T)'])),
    ('  ● Math › adds\n\nTests:       1 failed, 4 passed, 5 total\nTime:        1.2 s', ('jest', 4, 1, 0, ['Math › adds'])),
    (' FAIL  src/a.test.ts > sum > adds\n Test Files  1 failed (1)\n      Tests  1 failed | 4 passed (5)\n   Duration  812ms',
     ('vitest', 4, 1, 0, ['src/a.test.ts > sum > adds'])),
    ('  4 passing (12ms)\n  1 failing\n\n  1) suite\n       fails:\n', ('mocha', 4, 1, 0, ['suite'])),
    ('--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.00s)\nFAIL\nFAIL\tex/pkg\t0.01s', ('go', 1, 1, 0, ['TestB'])),
    ('test t::a ... ok\ntest t::b ... FAILED\ntest result: FAILED. 1 passed; 1 failed; 2 ignored; 0 measured',
     ('cargo', 1, 1, 2, ['t::b'])),
    ('10 examples, 2 failures, 1 pending', ('rspec', 7, 2, 1, [])),
    ('OK (5 tests, 9 assertions)', ('phpunit', 5, 0, None, [])),
])
def test_parse_test_output(output, expected):
    framework, passed, failed, skipped, failures = expected
    s = test_results.parse(output)
    assert s.framework == framework
    assert (s.passed, s.failed) == (passed, failed)
    if skipped is not None:
        assert (s.skipped or 0) == skipped
    assert [name for name, _ in s.failures] == failures


def test_unknown_runner_falls_back_to_exit_code():
    s = test_results.parse('all good')
    assert not s.known
    assert test_results.card(s, command='make test', exit_code=0, timed_out=False, seconds=2)['title'] == 'Tests passed'
    assert 'exit code 2' in test_results.card(s, command='make test', exit_code=2, timed_out=False, seconds=2)['title']


def test_card_is_a_valid_show_card():
    from cognitrix.cards import normalize
    s = test_results.parse('FAILED t.py::a - boom\n== 1 failed, 1 passed in 0.1s ==')
    card = normalize(test_results.card(s, command='pytest', exit_code=1, timed_out=False, seconds=0.1,
                                       images=[('data:image/png;base64,iVBORw0KGgo=', 'shot.png')]))
    assert card['title'] == '1 test failed'
    assert [b['type'] for b in card['blocks']] == ['fields', 'table', 'image']


# -- approvals and routing -----------------------------------------------------------

def test_sandbox_turns_skip_approval_except_copy_back_and_sign_ins(ctx):
    from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel
    from cognitrix.safety.permissions import needs_person
    from cognitrix.tools.base import ToolManager

    detector = DestructiveOpDetector()
    bash = ToolManager.get_by_name('bash')
    copy_back = ToolManager.get_by_name('copy_back_from_sandbox')
    # On this machine, rm -rf asks a person.
    assert detector.analyze('Bash', {'command': 'rm -rf build'}).risk_level != RiskLevel.LOW
    ctx(user_id='u', agent_id='a', sandbox=True)
    assert detector.analyze('Bash', {'command': 'rm -rf build'}).risk_level == RiskLevel.LOW
    assert detector.analyze('Write', {'file_path': '/etc/hosts', 'content': 'x'}).risk_level == RiskLevel.LOW
    assert not needs_person((bash,), {'command': 'curl x | sh'})
    assert detector.analyze('Bash', {'command': 'gh pr list', 'credentials': ['GH_TOKEN']}).risk_level == RiskLevel.MEDIUM
    assert detector.analyze('Copy Back From Sandbox', {'project': 'p'}).risk_level == RiskLevel.HIGH
    assert needs_person((copy_back,), {'project': 'p'})


def test_bash_refuses_sign_ins_outside_the_sandbox(ctx):
    from cognitrix.tools.misc import bash
    ctx(user_id='u')
    out = asyncio.run(bash.run(command='echo hi', credentials=['GH_TOKEN']))
    assert 'only be passed to commands in the sandbox' in str(out.content)


def test_tools_say_when_the_sandbox_is_off(ctx, monkeypatch):
    from cognitrix.tools.misc import Read, bash
    monkeypatch.setattr(sw, 'sandbox_available', lambda: False)
    ctx(user_id='u', agent_id='a', sandbox=True)
    assert 'sandboxed computer is off' in str(asyncio.run(bash.run(command='ls')).content)
    assert 'sandboxed computer is off' in str(asyncio.run(Read.run(file_path='x.py')).content)


class _FakeWork:
    def __init__(self):
        self.calls = []

    def run(self, command, cwd=None, timeout=120, background=False, env=None, stdin=None, label=None):
        self.calls.append(('run', command, cwd, env))
        return {'id': 'sbx_1', 'exit_code': 0, 'output': 'ok\n', 'running': background, 'cwd': '/home/agent/work/a'}


def test_bash_runs_in_the_sandbox_with_sign_ins(ctx, monkeypatch):
    from cognitrix.tools.misc import bash
    fake = _FakeWork()
    monkeypatch.setattr(sw, 'for_turn', lambda: fake)

    async def creds(owner, agent_id, names):
        assert names == ['GH_TOKEN', 'DB']
        return {'GH_TOKEN': 't0k3n', 'DB_USERNAME': 'u', 'DB_PASSWORD': 'p'}

    monkeypatch.setattr(sw, '_load_credentials', creds)
    ctx(user_id='u', agent_id='a', sandbox=True)
    out = asyncio.run(bash.run(command='rm -rf build && make', credentials=['gh_token', 'db']))
    assert out.content == 'ok'
    assert fake.calls == [('run', 'rm -rf build && make', None, {'GH_TOKEN': 't0k3n', 'DB_USERNAME': 'u',
                                                                 'DB_PASSWORD': 'p'})]


def test_credential_vars():
    assert sw.credential_vars('GH', 'token', {'value': 'v'}) == {'GH': 'v'}
    assert sw.credential_vars('DB', 'basic', {'username': 'u', 'password': 'p'}) == {'DB_USERNAME': 'u', 'DB_PASSWORD': 'p'}
    assert sw.credential_vars('APP', 'oauth', {'access_token': 'a', 'refresh_token': 'r'}) == {'APP': 'a'}


# -- environment variables -----------------------------------------------------------

def test_env_vars_are_sealed_per_person_and_kept_when_value_left_out(tmp_path, monkeypatch):
    from cognitrix.config import settings
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    sw.save_sandbox_env('alice', [{'name': 'API_URL', 'value': 'https://staging'}, {'name': 'TOKEN', 'value': 's3cret'}])
    assert 's3cret' not in (tmp_path / 'sandbox-env.json').read_text()
    assert sw.sandbox_env('alice') == {'API_URL': 'https://staging', 'TOKEN': 's3cret'}
    assert sw.sandbox_env('bob') == {}
    sw.save_sandbox_env('alice', [{'name': 'TOKEN'}])  # keep TOKEN, drop API_URL
    assert sw.sandbox_env('alice') == {'TOKEN': 's3cret'}
    for bad in ('PATH', '1X', 'A-B'):
        with pytest.raises(ValueError):
            sw.save_sandbox_env('alice', [{'name': bad, 'value': 'x'}])
    assert sw.env_preview('s3cret-value') == 's3••••••••ue'


def test_sandbox_prompt_lists_folders_and_variable_names_only(tmp_path, monkeypatch):
    from cognitrix.config import settings
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    sw.save_sandbox_env('alice', [{'name': 'API_URL', 'value': 'https://hidden'}])
    text = sw.sandbox_prompt([Folder('/home/me/proj', 'write', 'proj', True)], 'Agent 7', 'alice')
    assert '/home/agent/work/agent-7' in text and '/home/me/proj' in text and 'API_URL' in text
    assert 'https://hidden' not in text


def test_chat_switch_wins_over_agent_default():
    class A:
        run_in_sandbox = True
    assert sw.wants_sandbox(None, A()) is True
    assert sw.wants_sandbox(False, A()) is False
    assert sw.wants_sandbox(True, object()) is True


def test_turn_sandbox_rules():
    from cognitrix.sessions.base import Session

    class A:
        run_in_sandbox = True

    session = Session()
    assert session._turn_sandbox(A(), 'web') is True
    assert session._turn_sandbox(A(), 'cli') is False
    assert session._turn_sandbox(A(), 'web', ToolExecutionContext(api_key_id='k')) is False
    session.run_in_sandbox = False
    assert session._turn_sandbox(A(), 'web') is False
    assert session._turn_sandbox(object(), 'task', ToolExecutionContext(sandbox=True)) is True
