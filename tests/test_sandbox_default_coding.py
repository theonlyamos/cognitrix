"""Code runs in the sandbox by default, chat folders are copied in on first
use, sandbox turns end with a review card, and each agent gets a fair share
of the sandboxed computer."""
from __future__ import annotations

import asyncio
import os
import time
import types

import pytest

from cognitrix.common import sandbox_work as sw
from cognitrix.common.folders import Folder
from cognitrix.computer.sandbox import workspace as ws_mod
from cognitrix.tools.utils import ToolExecutionContext, reset_execution_context, set_execution_context


@pytest.fixture
def ctx():
    tokens = []

    def bind(**kwargs):
        tokens.append(set_execution_context(ToolExecutionContext(**kwargs)))

    yield bind
    for token in reversed(tokens):
        reset_execution_context(token)


@pytest.fixture(autouse=True)
def _clean_state():
    sw._copied.clear()
    sw._reviewed.clear()
    sw._chat_switch.clear()
    yield
    sw._copied.clear()
    sw._reviewed.clear()
    sw._chat_switch.clear()


def _agent(*tool_names, **fields):
    tools = [types.SimpleNamespace(name=n) for n in tool_names]
    return types.SimpleNamespace(tools=tools, **fields)


# -- on by default ----------------------------------------------------------------

def test_default_follows_the_sandbox_being_on(monkeypatch):
    monkeypatch.setattr(sw, 'sandbox_available', lambda: True)
    assert sw.sandbox_choice(None, _agent()) == (True, False)
    assert sw.sandbox_choice(None, _agent(code_on_my_computer=True)) == (False, True)
    assert sw.sandbox_choice(None, _agent(run_in_sandbox=True)) == (True, True)
    assert sw.sandbox_choice(False, _agent(run_in_sandbox=True)) == (False, True)
    monkeypatch.setattr(sw, 'sandbox_available', lambda: False)
    assert sw.sandbox_choice(None, _agent()) == (False, False)


def test_default_only_moves_tools_the_agent_has(monkeypatch):
    from cognitrix.sessions.base import Session
    monkeypatch.setattr(sw, 'sandbox_available', lambda: True)
    session = Session()
    assert session._turn_sandbox(_agent('Bash', 'Read'), 'web') is True
    assert session._turn_sandbox(_agent('Web Search'), 'web') is False
    # Chosen for the chat: on even without a shell (the turn brings one).
    session.run_in_sandbox = True
    assert session._turn_sandbox(_agent('Web Search'), 'web') is True
    session.run_in_sandbox = False
    assert session._turn_sandbox(_agent('Bash'), 'web') is False


def test_switching_to_the_persons_computer(ctx, monkeypatch):
    from cognitrix.sessions.base import Session
    from cognitrix.tools.sandbox_work import switch_computer_for_code
    monkeypatch.setattr(sw, 'sandbox_available', lambda: True)
    ctx(user_id='u', agent_id='a', session_id='chat1', sandbox=True)
    assert isinstance(sw.for_turn(), sw.SandboxWork)
    out = asyncio.run(switch_computer_for_code.run(computer='person'))
    assert "person's computer" in str(out.content)
    # At once in this turn, safety checks included...
    assert sw.for_turn() is None
    assert sw.turn_in_sandbox() is False
    # ...and the chat keeps it from the next turn.
    session = Session(id='chat1')
    assert session._turn_sandbox(_agent('Bash'), 'web') is False
    assert session.run_in_sandbox is False


# -- folders copied in on first use -------------------------------------------------

class _Box:
    """A SandboxWork without a container: remembers its projects."""

    def __init__(self):
        self.projects_list = []
        self.imports = []

    def projects(self):
        return list(self.projects_list)

    def import_folder(self, folder, name, replace=False):
        self.imports.append((str(folder), name))
        self.projects_list.append({'name': name, 'path': f'/home/agent/work/a/{name}', 'source': str(folder)})
        return {}


def _work(box):
    work = sw.SandboxWork('u', 'a')
    work.projects = box.projects
    work.import_folder = box.import_folder
    return work


def test_paths_in_chat_folders_point_at_the_copy(ctx, tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    box = _Box()
    work = _work(box)
    ctx(user_id='u', agent_id='a', folders=(Folder(str(proj), 'write', 'proj', True),))
    assert sw.to_sandbox_path(work, str(proj / 'src' / 'app.py')) == '/home/agent/work/a/proj/src/app.py'
    assert sw.to_sandbox_path(work, str(proj)) == '/home/agent/work/a/proj'
    assert box.imports == [(str(proj), 'proj')]  # copied once
    assert sw.to_sandbox_path(work, 'notes.txt') == 'notes.txt'
    assert sw.map_command(work, f'cd {proj} && pytest') == 'cd /home/agent/work/a/proj && pytest'
    assert len(box.imports) == 1


def test_the_sandbox_path_copies_the_folder_in(ctx, tmp_path):
    proj = tmp_path / 'site'
    proj.mkdir()
    box = _Box()
    work = _work(box)
    ctx(user_id='u', agent_id='a', folders=(Folder(str(proj), 'write', 'site', True),))
    assert sw.map_command(work, 'ls /home/agent/work/a/sitemap') == 'ls /home/agent/work/a/sitemap'
    assert box.imports == []
    sw.map_command(work, 'cd /home/agent/work/a/site && npm test')
    assert box.imports == [(str(proj), 'site')]


def test_the_sandbox_path_points_at_the_actual_copy(ctx, tmp_path):
    proj = tmp_path / 'site'
    proj.mkdir()
    box = _Box()
    box.projects_list.append({'name': 'site', 'source': '/elsewhere/site'})
    work = _work(box)
    ctx(user_id='u', agent_id='a', folders=(Folder(str(proj), 'write', 'site', True),))
    assert sw.to_sandbox_path(work, '/home/agent/work/a/site/x.py') == '/home/agent/work/a/site-2/x.py'
    assert sw.map_command(work, 'cd /home/agent/work/a/site') == 'cd /home/agent/work/a/site-2'
    assert box.imports == [(str(proj), 'site-2')]


def test_an_earlier_copy_is_reused(ctx, tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    box = _Box()
    box.projects_list.append({'name': 'mine', 'source': str(proj)})
    work = _work(box)
    ctx(user_id='u', agent_id='a', folders=(Folder(str(proj), 'write', 'proj', True),))
    assert sw.to_sandbox_path(work, str(proj / 'a.py')) == '/home/agent/work/a/mine/a.py'
    assert box.imports == []


def test_scratch_folder_is_never_copied(ctx, tmp_path):
    box = _Box()
    work = _work(box)
    ctx(user_id='u', agent_id='a', folders=(Folder(str(tmp_path), 'write', 'scratch', True),))
    assert sw.to_sandbox_path(work, str(tmp_path / 'x')) == str(tmp_path / 'x')
    assert box.imports == []


# -- review card --------------------------------------------------------------------

def test_changes_are_reviewed_once(tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    box = _Box()
    box.projects_list.append({'name': 'proj', 'source': str(proj)})
    work = _work(box)
    state = {'files': [{'path': 'a.py', 'status': 'modified', 'added': 1, 'removed': 1}], 'tree': 't1'}
    work.changes = lambda name, patch=True: dict(state)
    folders = [Folder(str(proj), 'write', 'proj', True)]
    assert sw.changes_to_review(work, 'chat', folders) == ['proj']
    sw.note_reviewed('chat', 'proj', dict(state))
    assert sw.changes_to_review(work, 'chat', folders) == []
    # The same line changed again: same counts, new content.
    state['tree'] = 't2'
    assert sw.changes_to_review(work, 'chat', folders) == ['proj']


def test_review_card_has_copy_back_buttons():
    from cognitrix.tools.sandbox_work import _changes_card
    out = {'files': [{'path': 'a.py', 'status': 'modified', 'added': 1, 'removed': 0}], 'source': '/home/me/proj'}
    card = _changes_card('proj', out, review=True)
    buttons = [b for block in card['blocks'] if block['type'] == 'actions' for b in block['buttons']]
    assert [b['action'] for b in buttons] == ['copy_back', 'keep_in_sandbox']
    assert all(block['type'] != 'actions' for block in _changes_card('proj', out)['blocks'])


def test_prompt_gives_each_folders_sandbox_path():
    text = sw.sandbox_prompt([Folder('/home/me/proj', 'write', 'proj', True)], 'Agent 7', 'alice')
    assert '/home/me/proj (read & write) -> /home/agent/work/agent-7/proj' in text
    assert 'Switch Computer For Code' in text


# -- fair share ---------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ws_mod, 'HOME', str(tmp_path))
    monkeypatch.setattr(ws_mod, 'WORK_ROOT', str(tmp_path / 'work'))
    monkeypatch.setattr(ws_mod, 'STATE_ROOT', str(tmp_path / '.cognitrix-sandbox'))
    return ws_mod.Workspace()


@pytest.mark.skipif(os.name == 'nt', reason='needs a POSIX shell')
def test_each_agent_gets_its_share_of_commands(workspace, monkeypatch):
    monkeypatch.setattr(ws_mod, 'MAX_JOBS_PER_AGENT', 2)
    jobs = [workspace.start('a', 'sleep 5'), workspace.start('a', 'sleep 5')]
    try:
        with pytest.raises(ws_mod.WorkspaceError) as err:
            workspace.start('a', 'sleep 5')
        assert err.value.code == 'limit'
        jobs.append(workspace.start('b', 'sleep 5'))  # another agent still can
    finally:
        for job in jobs:
            workspace._kill(job, 'test')


@pytest.mark.skipif(os.name == 'nt', reason='needs a POSIX shell')
def test_busy_when_memory_runs_out(workspace, monkeypatch):
    monkeypatch.setattr(ws_mod, 'free_memory_mb', lambda: 100)
    with pytest.raises(ws_mod.WorkspaceError) as err:
        workspace.start('a', 'echo hi')
    assert err.value.code == 'busy'
    assert 'almost out of memory (100 MB free)' in err.value.message
    assert 'tell the person' in err.value.message


@pytest.mark.skipif(os.name == 'nt', reason='needs a POSIX shell')
def test_later_commands_run_at_lower_priority(workspace):
    first = workspace.start('a', 'nice; sleep 3')
    second = workspace.start('a', 'nice')
    try:
        second.proc.wait(10)
        time.sleep(0.3)
        assert workspace.read(second.id, 'a')['output'].strip().splitlines()[-1] == '3'
    finally:
        workspace._kill(first, 'test')


def test_free_memory_is_read():
    free = ws_mod.free_memory_mb()
    assert free is None or free >= 0
