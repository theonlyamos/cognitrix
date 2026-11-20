"""The chat's changes view (cognitrix/file_changes).

- Write and Edit on this machine keep the file's text from before and after.
- The view lists each changed file as hunks; the person can accept a hunk or
  a file, and put a hunk, a file or everything back ("reject").
- Putting back goes through Edit with the hunk's own lines, so the rest of
  the file is left exactly as it is.
- Sandbox changes come from the sandbox copy's git diff.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cognitrix.file_changes import diff as d
from cognitrix.file_changes import service
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    monkeypatch.setenv('COGNITRIX_ALLOWED_FOLDERS', str(tmp_path))

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'changes.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield
    set_execution_context(ToolExecutionContext())


def _session(folder):
    return SimpleNamespace(id='s1', user_id='u1', agent_id='a1', task_id=None,
                           folders=[{'path': str(folder), 'access': 'write', 'primary': True}],
                           scratch_path=lambda: str(folder / '.scratch'))


async def _agent_call(folder, name, **params):
    """A tool call the way an agent's turn makes it."""
    from cognitrix.common.folders import normalize_folders
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.resilient_tool_wrapper import ResilientToolManager

    folders = tuple(normalize_folders([{'path': str(folder), 'access': 'write', 'primary': True}], validate=False))
    token = set_execution_context(ToolExecutionContext(user_id='u1', session_id='s1', agent_id='a1', folders=folders))
    try:
        result = await ResilientToolManager().run_tool(ToolManager.get_by_name(name), params, max_retries=1,
                                                       attempt_recovery=False)
    finally:
        reset_execution_context(token)
    assert result.success, result.error
    return result.data


SOURCE = """import os
from os.path import join

ROOT = "~/.cognitrix"


def to_posix(path):
    return path.replace("\\\\", "/")


def config_path(name):
    return join(ROOT, name)
"""


def test_hunks_and_revert_round_trip():
    after = SOURCE.replace('from os.path import join', 'from pathlib import Path').replace(
        'return join(ROOT, name)', 'return str(Path(ROOT) / name)')
    hunks = d.hunks_from_texts('p.py', SOURCE, after)
    assert len(hunks) == 2
    assert d.counts(hunks) == (2, 2)
    text = after
    for h in reversed(hunks):
        text = d.apply_revert(text, h)
    assert text == SOURCE


def test_hunk_ids_stay_when_another_hunk_goes():
    after = SOURCE.replace('import os', 'import os\nimport sys').replace('return join(ROOT, name)', 'return name')
    first = d.hunks_from_texts('p.py', SOURCE, after)
    reverted = d.apply_revert(after, first[0])
    second = d.hunks_from_texts('p.py', SOURCE, reverted)
    assert [h['id'] for h in second] == [first[1]['id']]


def test_parse_git_patch():
    patch = (
        'diff --git a/app.py b/app.py\n'
        'index 1..2 100644\n'
        '--- a/app.py\n'
        '+++ b/app.py\n'
        '@@ -1,3 +1,3 @@\n'
        ' a\n'
        '-b\n'
        '+B\n'
        ' c\n'
        '@@ -10 +10,2 @@ def f():\n'
        ' x\n'
        '+y\n'
        'diff --git a/new.txt b/new.txt\n'
        'new file mode 100644\n'
        '--- /dev/null\n'
        '+++ b/new.txt\n'
        '@@ -0,0 +1 @@\n'
        '+hello\n'
        '\\ No newline at end of file\n'
    )
    files = d.parse_git_patch(patch)
    assert list(files) == ['app.py', 'new.txt']
    assert [h['lines'] for h in files['app.py']] == [
        [[' ', 'a'], ['-', 'b'], ['+', 'B'], [' ', 'c']],
        [[' ', 'x'], ['+', 'y']],
    ]
    assert files['new.txt'][0]['lines'] == [['+', 'hello']]


async def test_local_edits_are_recorded_and_put_back(db, tmp_path):
    folder = tmp_path / 'proj'
    folder.mkdir()
    target = folder / 'paths.py'
    target.write_text(SOURCE, encoding='utf-8')

    await _agent_call(folder, 'Edit', file_path=str(target), old_string='from os.path import join',
                      new_string='from pathlib import Path')
    await _agent_call(folder, 'Edit', file_path=str(target), old_string='return join(ROOT, name)',
                      new_string='return str(Path(ROOT) / name)')
    await _agent_call(folder, 'Write', file_path=str(folder / 'notes.md'), content='# Notes\nnew file\n')

    session = _session(folder)
    changes = await service.chat_changes(session)
    [src] = changes['sources']
    assert src['key'] == 'local'
    by_path = {f['path']: f for f in src['files']}
    assert set(by_path) == {'paths.py', 'notes.md'}
    assert by_path['paths.py']['status'] == 'modified'
    assert by_path['notes.md']['status'] == 'added'
    assert len(by_path['paths.py']['hunks']) == 2

    # Accept the first hunk, then put the second one back.
    first, second = by_path['paths.py']['hunks']
    await service.accept(session, 'local', str(target), first['id'])
    await service.reject(session, 'local', str(target), second['id'])
    text = target.read_text(encoding='utf-8')
    assert 'from pathlib import Path' in text
    assert 'return join(ROOT, name)' in text
    assert text.endswith('\n')

    changes = await service.chat_changes(session)
    paths_file = next(f for f in changes['sources'][0]['files'] if f['path'] == 'paths.py')
    assert [h['id'] for h in paths_file['hunks']] == [first['id']]
    assert paths_file['state'] == 'accepted'

    # Reject everything: the edit goes back and the new file is deleted.
    await service.reject(session, 'local', None, None)
    assert target.read_text(encoding='utf-8') == SOURCE
    assert not (folder / 'notes.md').exists()
    assert (await service.chat_changes(session))['sources'] == []


async def test_put_back_refuses_when_the_file_moved_on(db, tmp_path):
    folder = tmp_path / 'proj'
    folder.mkdir()
    target = folder / 'a.txt'
    target.write_text('one\ntwo\nthree\n', encoding='utf-8')
    await _agent_call(folder, 'Edit', file_path=str(target), old_string='two', new_string='TWO')
    target.write_text('one\nTWO\nthree\nfour\n', encoding='utf-8')  # someone else changed it

    session = _session(folder)
    [src] = (await service.chat_changes(session))['sources']
    with pytest.raises(service.ChangeError) as err:
        await service.reject(session, 'local', str(target), src['files'][0]['hunks'][0]['id'])
    assert err.value.code == 'changed'
    assert target.read_text(encoding='utf-8') == 'one\nTWO\nthree\nfour\n'


async def test_sandbox_changes_listed_and_put_back(db, tmp_path, monkeypatch):
    calls = []

    class FakeWork:
        def project_for(self, folder, copy=True):
            return 'proj'

        def changes(self, name, patch=True):
            return {'path': '/home/agent/work/proj', 'source': '/me/proj',
                    'files': [{'path': 'app.py', 'status': 'modified', 'added': 1, 'removed': 1}],
                    'patch': 'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n'}

        def files(self, op, **body):
            calls.append((op, body))
            if op == 'read':
                return {'text': 'a\nB\nc', 'resume': None}
            return {'ok': True}

    folder = tmp_path / 'proj'
    folder.mkdir()
    monkeypatch.setattr(service, '_sandbox_work', lambda session: FakeWork())
    session = _session(folder)
    [src] = (await service.chat_changes(session))['sources']
    assert src['key'] == 'sandbox:proj' and src['source'] == '/me/proj'
    hunk = src['files'][0]['hunks'][0]
    await service.reject(session, 'sandbox:proj', 'app.py', hunk['id'])
    assert ('edit', {'path': '/home/agent/work/proj/app.py', 'old': 'a\nB\nc', 'new': 'a\nb\nc'}) in calls
