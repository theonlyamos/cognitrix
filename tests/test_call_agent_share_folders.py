"""Call Agent can share folders with the agent it calls: the helper may then
change files and run commands there, never more than the caller itself."""
import os

import pytest

from cognitrix.common.folders import READ, WRITE, Folder
from cognitrix.common.process_security import HostProcessMode
from cognitrix.tools.utils import ToolExecutionContext
from tests.test_call_agent_handoff import (
    _background_done,
    _call,
    _capture_reports,
    _fake_turn,
    _patch_target,
)
from tests.test_session_ownership_routes import _initialize_routes_db, _owned_session


def _folders(tmp_path):
    project = tmp_path / 'project'
    (project / 'app').mkdir(parents=True)
    docs = tmp_path / 'docs'
    docs.mkdir()
    return project, docs, (
        Folder(str(project), WRITE, 'project', True),
        Folder(str(docs), READ, 'docs', False),
    )


def _ctx(chat_id, folders, **kwargs):
    return ToolExecutionContext(
        session_id=str(chat_id), user_id='user-1', agent_id='agent-1', folders=folders,
        host_process_mode=kwargs.pop('host_process_mode', HostProcessMode.TRUSTED_LOCAL), **kwargs)


def _by_path(folders):
    return {os.path.normcase(f.path): f for f in folders}


async def _setup(tmp_path, monkeypatch):
    await _initialize_routes_db(tmp_path / 'db')
    _patch_target(monkeypatch)
    seen = {}
    _fake_turn(monkeypatch, seen)
    parent = await _owned_session('user-1', 'agent-1')
    return seen, parent


async def test_without_sharing_the_helper_stays_read_only(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    _project, _docs, folders = _folders(tmp_path)

    out = await _call(_ctx(parent.id, folders), name='Researcher', task='Read the code')

    assert out == 'Research done.'
    ctx = seen['turns'][0][2]
    assert ctx.host_process_mode is HostProcessMode.DENY
    assert all(f.access == READ for f in ctx.folders)


async def test_sharing_the_primary_folder_lets_the_helper_work_there(tmp_path, monkeypatch):
    from cognitrix.sessions.base import Session

    seen, parent = await _setup(tmp_path, monkeypatch)
    project, docs, folders = _folders(tmp_path)

    out = await _call(_ctx(parent.id, folders), name='Researcher', task='Fix the bug',
                      share_folders=['primary'])

    assert out.startswith('(Researcher could change files and run commands in: project (')
    assert out.endswith('Research done.')
    helper_id, _message, ctx = seen['turns'][0]
    assert ctx.host_process_mode is HostProcessMode.TRUSTED_LOCAL
    assert ctx.session_id is None  # still a helper's own authority otherwise
    shared = _by_path(ctx.folders)
    assert shared[os.path.normcase(str(project))].access == WRITE
    assert shared[os.path.normcase(str(project))].primary
    assert shared[os.path.normcase(str(docs))].access == READ
    # The helper's chat shows what it was let work in.
    helper = await Session.get(helper_id)
    assert {f['path']: f['access'] for f in helper.folders} == {str(project): WRITE, str(docs): READ}


async def test_a_folder_inside_a_shared_one_becomes_the_helpers_primary(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    project, _docs, folders = _folders(tmp_path)

    await _call(_ctx(parent.id, folders), name='Researcher', task='Build it',
                share_folders=[str(project / 'app')])

    ctx = seen['turns'][0][2]
    primary = next(f for f in ctx.folders if f.primary)
    assert primary.path == str((project / 'app').resolve()) and primary.access == WRITE
    # The rest of the project stays read only for the helper.
    assert _by_path(ctx.folders)[os.path.normcase(str(project))].access == READ


async def test_relative_paths_and_names_resolve_against_the_callers_folders(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    project, _docs, folders = _folders(tmp_path)

    await _call(_ctx(parent.id, folders), name='Researcher', task='Build it', share_folders='app')
    assert next(f for f in seen['turns'][0][2].folders if f.primary).path == str((project / 'app').resolve())

    await _call(_ctx(parent.id, folders), name='Researcher', task='Build it', share_folders=['Project'])
    assert next(f for f in seen['turns'][1][2].folders if f.primary).path == str(project)


@pytest.mark.parametrize('share, why', [
    (['docs'], 'read only for you'),
    (['/definitely/not/mine'], 'not in your working folders'),
    (['app/missing'], 'is not a folder'),
])
async def test_only_folders_the_caller_can_change_can_be_shared(tmp_path, monkeypatch, share, why):
    seen, parent = await _setup(tmp_path, monkeypatch)
    _project, _docs, folders = _folders(tmp_path)

    out = await _call(_ctx(parent.id, folders), name='Researcher', task='Fix it', share_folders=share)

    assert out.startswith("Error calling agent: can't share those folders:")
    assert why in out
    assert 'turns' not in seen  # the helper never started


async def test_a_link_cannot_share_a_read_only_folder(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    project, docs, folders = _folders(tmp_path)
    try:
        os.symlink(docs, project / 'docs-link', target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not available')

    out = await _call(_ctx(parent.id, folders), name='Researcher', task='Fix it',
                      share_folders=['docs-link'])

    assert "can't share those folders" in out and 'read only' in out


async def test_a_read_only_helper_cannot_pass_on_write_access(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    _project, _docs, folders = _folders(tmp_path)
    helper_folders = tuple(Folder(f.path, READ, f.name, f.primary) for f in folders)

    out = await _call(_ctx(parent.id, helper_folders, host_process_mode=HostProcessMode.DENY),
                      name='Researcher', task='Fix it', share_folders=['all'])

    assert "can't share those folders" in out


async def test_sharing_never_grants_commands_the_caller_does_not_have(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    project, _docs, folders = _folders(tmp_path)

    await _call(_ctx(parent.id, folders, host_process_mode=HostProcessMode.DENY),
                name='Researcher', task='Fix it', share_folders=['all'])

    ctx = seen['turns'][0][2]
    assert ctx.host_process_mode is HostProcessMode.DENY
    assert _by_path(ctx.folders)[os.path.normcase(str(project))].access == WRITE


async def test_handed_off_helpers_keep_the_shared_folders(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    _capture_reports(monkeypatch)
    project, _docs, folders = _folders(tmp_path)

    out = await _call(_ctx(parent.id, folders), name='Researcher', task='Build it', mode='handoff',
                      share_folders=['all'])

    assert out.startswith('(Researcher could change files and run commands in: project (')
    assert 'Handed off to Researcher' in out
    await _background_done()
    ctx = seen['turns'][0][2]
    assert ctx.host_process_mode is HostProcessMode.TRUSTED_LOCAL
    assert _by_path(ctx.folders)[os.path.normcase(str(project))].access == WRITE


async def test_a_chat_on_a_paired_pc_can_share_the_pc(tmp_path, monkeypatch):
    seen, parent = await _setup(tmp_path, monkeypatch)
    _project, _docs, folders = _folders(tmp_path)

    await _call(_ctx(parent.id, folders, device='pc-1'), name='Researcher', task='Look')
    assert seen['turns'][0][2].device_read_only is True

    out = await _call(_ctx(parent.id, folders, device='pc-1'), name='Researcher', task='Fix it',
                      share_folders=['primary'])
    assert "the person's PC" in out
    ctx = seen['turns'][1][2]
    assert ctx.device == 'pc-1' and ctx.device_read_only is False

    # A helper that may only look at the PC can't hand on more.
    out = await _call(_ctx(parent.id, folders, device='pc-1', device_read_only=True),
                      name='Researcher', task='Fix it', share_folders=['primary'])
    assert "can't share those folders" in out
