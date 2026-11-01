"""Working folders: chats and agents can use several folders, read only or read & write."""
import os

import pytest

from cognitrix.common import folders as F
from cognitrix.common.process_security import HostProcessMode
from cognitrix.common.safe_exec import PathEscapesRoot
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


@pytest.fixture
def two_folders(tmp_path, monkeypatch):
    from cognitrix.config import settings

    project = tmp_path / 'project'
    notes = tmp_path / 'notes'
    outside = tmp_path / 'outside'
    for d in (project, notes, outside):
        d.mkdir()
    (project / 'README.md').write_text('hello project\n')
    (notes / 'install.md').write_text('step one\n')
    (outside / 'secret.txt').write_text('nope\n')
    monkeypatch.setattr(settings, 'tools_root', (tmp_path / 'legacy').resolve())
    monkeypatch.setattr(settings, 'workdir', (tmp_path / 'data').resolve())
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    monkeypatch.delenv('COGNITRIX_ALLOWED_FOLDERS', raising=False)
    folders = (
        F.Folder(str(project.resolve()), F.WRITE, 'project', True),
        F.Folder(str(notes.resolve()), F.READ, 'notes', False),
    )
    token = set_execution_context(ToolExecutionContext(
        host_process_mode=HostProcessMode.TRUSTED_LOCAL, folders=folders,
    ))
    yield project, notes, outside
    reset_execution_context(token)


def test_relative_paths_start_in_the_primary_folder(two_folders):
    project, notes, _ = two_folders
    assert F.resolve_path('README.md') == (project / 'README.md').resolve()
    assert F.resolve_path(str(notes / 'install.md')) == (notes / 'install.md').resolve()
    assert F.primary_folder() == project.resolve()


def test_paths_outside_every_folder_are_refused(two_folders):
    _, _, outside = two_folders
    with pytest.raises(PathEscapesRoot, match='outside the working folders'):
        F.resolve_path(str(outside / 'secret.txt'))
    with pytest.raises(PathEscapesRoot):
        F.resolve_path('../outside/secret.txt')


def test_writes_need_a_read_and_write_folder(two_folders):
    project, notes, _ = two_folders
    assert F.resolve_path('new.txt', write=True) == (project / 'new.txt').resolve()
    with pytest.raises(F.FolderReadOnly, match="'notes' is read only"):
        F.resolve_path(str(notes / 'install.md'), write=True)


@pytest.mark.skipif(os.name == 'nt', reason='symlinks need admin rights on Windows')
def test_a_symlink_cannot_lead_out_of_the_folders(two_folders):
    project, notes, outside = two_folders
    (project / 'escape').symlink_to(outside)
    with pytest.raises(PathEscapesRoot):
        F.resolve_path('escape/secret.txt')
    # A link from a writable folder into a read only one can't be used to write.
    (project / 'into-notes').symlink_to(notes)
    assert F.resolve_path('into-notes/install.md').exists()
    with pytest.raises(F.FolderReadOnly):
        F.resolve_path('into-notes/install.md', write=True)


async def test_file_tools_follow_folder_access(two_folders):
    from cognitrix.tools.misc import Edit, Read, Write

    project, notes, outside = two_folders
    read_notes = await Read.run(file_path=str(notes / 'install.md'))
    assert 'step one' in read_notes.content
    refused = await Write.run(file_path=str(notes / 'new.md'), content='x')
    assert 'read only' in refused.content
    assert not (notes / 'new.md').exists()
    edit = await Edit.run(file_path=str(notes / 'install.md'), old_string='one', new_string='two')
    assert 'read only' in edit.content
    assert (notes / 'install.md').read_text() == 'step one\n'
    written = await Write.run(file_path='out.txt', content='done')
    assert (project / 'out.txt').read_text() == 'done', written.content
    outside_read = await Read.run(file_path=str(outside / 'secret.txt'))
    assert 'outside the working folders' in outside_read.content


async def test_bash_starts_in_the_primary_folder_and_can_use_other_folders(two_folders):
    from cognitrix.tools.misc import bash

    project, notes, outside = two_folders
    here = await bash.run(command='ls')
    assert 'README.md' in here.content
    there = await bash.run(command='ls', working_dir=str(notes))
    assert 'install.md' in there.content
    refused = await bash.run(command='ls', working_dir=str(outside))
    assert 'Command refused' in refused.content


def test_shell_approval_mentions_a_read_only_folder(two_folders):
    from cognitrix.safety.destructive_ops import DestructiveOpDetector

    _, notes, _ = two_folders
    risk = DestructiveOpDetector().analyze('Bash', {'command': 'npm run build', 'working_dir': str(notes)})
    assert risk.risk_level.value != 'low'
    assert 'read only' in risk.details
    plain = DestructiveOpDetector().analyze('Bash', {'command': 'npm run build'})
    assert 'read only' not in plain.details


def test_request_folder_always_asks(two_folders):
    from cognitrix.safety.destructive_ops import DestructiveOpDetector

    risk = DestructiveOpDetector().analyze('Request Folder', {'path': '/x', 'access': 'write'})
    assert risk.risk_level.value == 'medium'
    assert 'read & write' in risk.details


def test_validate_folder_refuses_protected_and_missing_places(two_folders, tmp_path, monkeypatch):
    from cognitrix.config import settings

    with pytest.raises(F.FolderError, match='not a folder'):
        F.validate_folder(str(tmp_path / 'missing'))
    with pytest.raises(F.FolderError, match='full path'):
        F.validate_folder('relative/path')
    (settings.workdir).mkdir(parents=True, exist_ok=True)
    with pytest.raises(F.FolderError, match='protected'):
        F.validate_folder(str(settings.workdir))
    ok = F.validate_folder(str(tmp_path / 'notes'), 'write')
    assert ok.access == F.WRITE and ok.name == 'notes'


def test_a_web_server_only_allows_folders_inside_its_working_folder(two_folders, tmp_path, monkeypatch):
    from cognitrix.config import settings

    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    monkeypatch.setattr(settings, 'tools_root', tmp_path / 'project')
    assert F.validate_folder(str(tmp_path / 'project')).path == str((tmp_path / 'project').resolve())
    with pytest.raises(F.FolderError, match='outside the folders this server allows'):
        F.validate_folder(str(tmp_path / 'notes'))
    monkeypatch.setenv('COGNITRIX_ALLOWED_FOLDERS', str(tmp_path / 'notes'))
    assert F.validate_folder(str(tmp_path / 'notes'))


def test_normalize_keeps_one_primary_and_drops_duplicates(two_folders):
    project, notes, _ = two_folders
    out = F.normalize_folders([
        {'path': str(notes), 'access': 'read'},
        {'path': str(project), 'access': 'write', 'primary': True},
        {'path': str(notes) + os.sep, 'access': 'write'},
    ])
    assert [f.name for f in out] == ['project', 'notes']
    assert [f.primary for f in out] == [True, False]
    with pytest.raises(F.FolderError):
        F.normalize_folders([{'path': '/does/not/exist'}])
    assert F.normalize_folders([{'path': '/does/not/exist'}], validate=False) == []


def test_sub_agents_get_read_only_folders(two_folders):
    from cognitrix.tools.utils import current_execution_context, delegated_execution_context

    child = delegated_execution_context(current_execution_context())
    assert child.folders and all(not f.writable for f in child.folders)


def test_merge_folder_adds_or_upgrades(two_folders):
    project, notes, outside = two_folders
    current = list(F.active_folders())
    upgraded = F.merge_folder(current, F.Folder(str(notes.resolve()), F.WRITE, 'notes'))
    assert [f.access for f in upgraded] == [F.WRITE, F.WRITE]
    added = F.merge_folder(current, F.Folder(str(outside.resolve()), F.READ, 'outside'))
    assert len(added) == 3 and not added[2].primary


async def test_request_folder_grants_through_the_running_chat(two_folders):
    from cognitrix.tools.folders import request_folder

    _, notes, outside = two_folders
    granted = []
    token = F.set_grant_sink(granted.append)
    try:
        result = await request_folder.run(path=str(outside), access='read', reason='need it')
        assert 'Added' in result.content
        assert granted and granted[0].path == str(outside.resolve())
        same = await request_folder.run(path=str(notes), access='read')
        assert 'already available' in same.content
    finally:
        F.reset_grant_sink(token)
    no_chat = await request_folder.run(path=str(outside))
    assert 'only be added' in no_chat.content


def test_folder_prompt_lists_access_and_primary(two_folders):
    text = F.folder_prompt(F.active_folders())
    assert 'project' in text and 'read & write, primary' in text
    assert 'notes' in text and 'read only' in text


def test_no_folders_means_the_app_working_folder(tmp_path, monkeypatch):
    from cognitrix.config import settings

    monkeypatch.setattr(settings, 'tools_root', tmp_path)
    token = set_execution_context(ToolExecutionContext())
    try:
        assert F.primary_folder() == tmp_path.resolve()
        assert F.resolve_path('x.txt', write=True) == (tmp_path / 'x.txt').resolve()
    finally:
        reset_execution_context(token)


def test_chats_copy_agent_defaults_or_get_a_scratch_folder(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from cognitrix.config import settings
    from cognitrix.sessions.base import Session

    monkeypatch.setattr(settings, 'tools_root', (tmp_path / 'legacy').resolve())
    monkeypatch.setattr(settings, 'workdir', (tmp_path / 'data').resolve())
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    (tmp_path / 'legacy').mkdir()
    (tmp_path / 'repo').mkdir()

    # Agent never configured: the app's working folder, as before.
    s1 = Session(agent_id='a')
    got = s1._turn_folders(SimpleNamespace(folders=None), 'web')
    assert [f.path for f in got] == [str((tmp_path / 'legacy').resolve())]
    assert s1.folders[0]['access'] == 'write'

    # Agent defaults are copied once; later agent changes don't move the chat.
    agent = SimpleNamespace(folders=[{'path': str(tmp_path / 'repo'), 'access': 'read'}])
    s2 = Session(agent_id='a')
    assert s2._turn_folders(agent, 'web')[0].access == 'read'
    agent.folders = []
    assert s2._turn_folders(agent, 'web')[0].name == 'repo'

    # An agent with no folders: each chat gets a private scratch folder.
    s3 = Session(agent_id='a')
    scratch = s3._turn_folders(SimpleNamespace(folders=[]), 'web')
    assert scratch[0].name == 'scratch' and os.path.isdir(scratch[0].path)
    assert str(F.workspaces_root()) in scratch[0].path

    # CLI turns keep the old single working folder.
    assert Session(agent_id='a')._turn_folders(agent, 'cli') == []


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'folders.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


async def test_folders_survive_a_save_and_none_stays_different_from_empty(db, tmp_path):
    from cognitrix.agents import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.sessions.base import Session

    stored = [{'path': str(tmp_path), 'access': 'write', 'name': 'x', 'primary': True}]
    for value in (None, [], stored):
        agent = Agent(name=f'A{value!r}', llm=LLM(provider='openai', model='gpt-x'), system_prompt='hi',
                      folders=value)
        await agent.save()
        again = await Agent.find_one({'id': agent.id})
        assert again.folders == value
        session = Session(agent_id=str(agent.id), folders=value)
        await session.save()
        assert (await Session.find_one({'id': session.id})).folders == value


async def test_chat_folders_api_checks_every_path(db, tmp_path, monkeypatch):
    from fastapi import HTTPException

    from cognitrix.api.routes import folders as routes
    from cognitrix.common.security import AuthContext
    from cognitrix.config import settings
    from cognitrix.models import User
    from cognitrix.session_ownership import claim_new, principal_key
    from cognitrix.sessions.base import Session

    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    monkeypatch.setattr(settings, 'workdir', (tmp_path / 'data').resolve())
    (tmp_path / 'repo').mkdir()
    user = User(name='Ada', email='ada@example.com', password='x' * 20)
    await user.save()
    user = await User.find_one({'email': 'ada@example.com'})
    ctx = AuthContext(user=user)
    session = Session(agent_id='agent-1', user_id=principal_key(user))
    await session.save()
    await claim_new(str(session.id), principal_key(user), 'agent-1')

    body = routes.FolderList(folders=[{'path': str(tmp_path / 'repo'), 'access': 'write'}])
    saved = await routes.set_session_folders(str(session.id), body, ctx)
    assert saved['folders'][0]['primary'] is True
    assert (await Session.find_one({'id': session.id})).folders[0]['access'] == 'write'

    bad = routes.FolderList(folders=[{'path': str(settings.workdir)}])
    (settings.workdir).mkdir(parents=True, exist_ok=True)
    with pytest.raises(HTTPException) as err:
        await routes.set_session_folders(str(session.id), bad, ctx)
    assert err.value.status_code == 400

    other = User(name='Bo', email='bo@example.com', password='x' * 20)
    await other.save()
    other = await User.find_one({'email': 'bo@example.com'})
    with pytest.raises(HTTPException) as err:
        await routes.set_session_folders(str(session.id), body, AuthContext(user=other))
    assert err.value.status_code == 404
