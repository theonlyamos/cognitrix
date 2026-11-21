"""AGENTS.md in every chat: a working folder's instruction files reach the
agent the way Codex reads them.

- Each folder's first of AGENTS.override.md / AGENTS.md / CLAUDE.md /
  .cursorrules counts.
- In a git repository, the files from the repo root down to the working
  folder all apply, root first, so the closest one comes last.
- Subfolders with their own file are listed, and the first time a chat reads
  or changes a file under one, its instructions come back with the result.
- A project's instructions read from the same file aren't repeated.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cognitrix.common import agents_md as A
from cognitrix.common import folders as F
from cognitrix.common.process_security import HostProcessMode
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'tools_root', (tmp_path / 'legacy').resolve())
    monkeypatch.setattr(settings, 'workdir', (tmp_path / 'data').resolve())
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    monkeypatch.delenv('COGNITRIX_ALLOWED_FOLDERS', raising=False)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    A._file_cache.clear()
    A._scan_cache.clear()
    A._shown.clear()
    root = (tmp_path / 'repo').resolve()
    (root / '.git').mkdir(parents=True)
    (root / 'AGENTS.md').write_text('Root rule: use tabs.\n', encoding='utf-8')
    (root / 'web').mkdir()
    (root / 'web' / 'AGENTS.md').write_text('Web rule: use pnpm.\n', encoding='utf-8')
    (root / 'web' / 'app').mkdir()
    (root / 'web' / 'app' / 'AGENTS.md').write_text('App rule: no default exports.\n', encoding='utf-8')
    (root / 'web' / 'app' / 'main.ts').write_text('export const x = 1;\n', encoding='utf-8')
    (root / 'web' / 'node_modules' / 'pkg').mkdir(parents=True)
    (root / 'web' / 'node_modules' / 'pkg' / 'AGENTS.md').write_text('ignore me', encoding='utf-8')
    return root


def _turn(folders, session='chat-1'):
    return set_execution_context(ToolExecutionContext(
        host_process_mode=HostProcessMode.TRUSTED_LOCAL, folders=tuple(folders), session_id=session,
    ))


def test_files_from_the_repo_root_down_to_the_folder_apply_root_first(repo):
    chain = A.chain_for(repo / 'web')
    assert [f.path for f in chain] == [repo / 'AGENTS.md', repo / 'web' / 'AGENTS.md']
    block = A.instructions_block([F.Folder(str(repo / 'web'), F.WRITE, 'web', True)])
    assert block.startswith('## AGENTS.md')
    assert block.index('Root rule') < block.index('Web rule')  # the closest comes last
    assert 'closest' in block
    # The deeper file is listed, not inlined; node_modules is skipped.
    assert str(repo / 'web' / 'app' / 'AGENTS.md') in block and 'App rule' not in block
    assert 'ignore me' not in block and 'node_modules' not in block


def test_override_beats_agents_md_and_claude_md_is_a_fallback(repo):
    (repo / 'web' / 'AGENTS.override.md').write_text('Override wins.', encoding='utf-8')
    assert A.file_in(repo / 'web').text == 'Override wins.'
    plain = repo.parent / 'plain'
    plain.mkdir()
    (plain / 'CLAUDE.md').write_text('Claude rules.', encoding='utf-8')
    (plain / '.cursorrules').write_text('Cursor rules.', encoding='utf-8')
    assert A.file_in(plain).text == 'Claude rules.'
    # Not in a repository: only the folder's own file counts.
    assert [f.text for f in A.chain_for(plain)] == ['Claude rules.']


def test_parents_outside_the_allowed_folders_are_not_read(repo, monkeypatch):
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    monkeypatch.setenv('COGNITRIX_ALLOWED_FOLDERS', str(repo / 'web'))
    assert [f.path for f in A.chain_for(repo / 'web')] == [repo / 'web' / 'AGENTS.md']


def test_the_prompt_is_capped(repo, monkeypatch):
    monkeypatch.setattr(A, 'MAX_BYTES', 40)
    (repo / 'AGENTS.md').write_text('x' * 100, encoding='utf-8')
    block = A.instructions_block([F.Folder(str(repo / 'web'), F.WRITE, 'web', True)])
    assert 'Cut short' in block and 'x' * 41 not in block
    assert 'Not included: too long' in block  # the web file had no room left


def test_a_project_s_copy_of_the_file_is_not_repeated(repo):
    block = A.instructions_block([F.Folder(str(repo), F.WRITE, 'repo', True)],
                                 skip_texts=['Root rule:   use tabs.'])
    assert 'Root rule' not in block


def test_no_files_means_no_block(tmp_path, repo):
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert A.instructions_block([F.Folder(str(empty), F.WRITE, 'empty', True)]) == ''


async def test_a_subfolder_file_comes_back_once_with_read_and_edit(repo):
    from cognitrix.tools.misc import Edit, Read

    token = _turn([F.Folder(str(repo / 'web'), F.WRITE, 'web', True)])
    try:
        first = await Read.run(file_path=str(repo / 'web' / 'app' / 'main.ts'))
        assert 'App rule: no default exports.' in first.content
        again = await Read.run(file_path=str(repo / 'web' / 'app' / 'main.ts'))
        assert 'App rule' not in again.content
        # The working folder's own file is in the prompt: never attached.
        top = await Read.run(file_path=str(repo / 'web' / 'AGENTS.md'))
        assert top.content.count('Web rule') == 1
    finally:
        reset_execution_context(token)
    token = _turn([F.Folder(str(repo / 'web'), F.WRITE, 'web', True)], session='chat-2')
    try:
        edit = await Edit.run(file_path=str(repo / 'web' / 'app' / 'main.ts'), old_string='1', new_string='2')
        assert 'Replaced 1' in edit.content and 'App rule' in edit.content
    finally:
        reset_execution_context(token)


def test_the_chat_system_message_gets_the_block(repo):
    from cognitrix.sessions.base import _with_folder_block

    prompt = [{'role': 'system', 'content': 'You are helpful.'}]
    out = _with_folder_block(prompt, (F.Folder(str(repo / 'web'), F.WRITE, 'web', True),))
    content = out[0]['content']
    assert content.index('## Working folders') < content.index('## AGENTS.md')
    assert 'Root rule' in content and 'Web rule' in content


def test_a_project_chat_does_not_repeat_the_project_instructions(repo):
    from cognitrix.projects import context
    from cognitrix.sessions.base import _with_folder_block

    token = context.set_project(context.TurnProject(id='p', user_id='u', name='P',
                                                    instructions='Root rule: use tabs.'))
    try:
        out = _with_folder_block([{'role': 'system', 'content': 'x'}],
                                 (F.Folder(str(repo / 'web'), F.WRITE, 'web', True),))
    finally:
        context.reset(token)
    content = out[0]['content']
    assert 'Root rule' not in content and 'Web rule' in content


def test_cli_turns_use_the_app_working_folder(repo, monkeypatch):
    from cognitrix.config import settings
    from cognitrix.sessions.base import _with_folder_block

    monkeypatch.setattr(settings, 'tools_root', repo / 'web')
    out = _with_folder_block([{'role': 'system', 'content': 'x'}], ())
    assert 'Web rule' in out[0]['content']


def test_the_pc_prompt_shows_its_agents_md():
    from cognitrix.computer.paired.turn import device_prompt

    device = {'name': 'Kofi desktop', 'online': True, 'powers': ['files'],
              'folders': [{'path': 'C:\\code\\app', 'writable': True}],
              'agents_md': ('C:\\code\\app\\AGENTS.md', 'PC rule: run make test.')}
    prompt = device_prompt(device)
    assert 'Instructions from C:\\code\\app\\AGENTS.md' in prompt and 'PC rule' in prompt
    assert 'own AGENTS.md' in prompt


def test_folder_summary_for_the_picker(repo):
    info = A.summary(str(repo / 'web'))
    assert info == {'files': [str(repo / 'AGENTS.md'), str(repo / 'web' / 'AGENTS.md')], 'nested': 1}


async def test_the_folder_picker_route_checks_the_folder_first(repo, tmp_path):
    from fastapi import HTTPException

    from cognitrix.api.routes.folders import folder_instructions

    assert (await folder_instructions(str(repo / 'web')))['nested'] == 1
    with pytest.raises(HTTPException):
        await folder_instructions(str(tmp_path / 'data'))  # the app's own data folder is protected
