"""Security regression tests for the bash and file tools.

These pin the hardening: the bash tool must reject shell injection and
non-whitelisted commands, and the file tools must not read outside the
configured tools root.
"""

import hashlib
import os
import uuid

import pytest

from cognitrix.common.process_security import HostProcessMode
from cognitrix.config import settings
from cognitrix.tools.misc import (
    Edit,
    Glob,
    Grep,
    Read,
    Write,
    bash,
    call_agent,
    open_file,
)
from cognitrix.tools.utils import (
    ToolExecutionContext,
    reset_execution_context,
    set_execution_context,
)


def _owned_upload_dir(root, *, session='session-1', user='user-1', agent='agent-1'):
    digest = hashlib.sha256(f'{session}\0{user}\0{agent}'.encode()).hexdigest()
    return root / 'uploads' / f'd_{digest}_{uuid.uuid4().hex}'


def _set_upload_context(
    *,
    session='session-1',
    user='user-1',
    agent='agent-1',
    host_process_mode=HostProcessMode.DENY,
):
    return set_execution_context(ToolExecutionContext(
        session_id=session,
        user_id=user,
        agent_id=agent,
        host_process_mode=host_process_mode,
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    "git status; rm -rf .",   # separator
    "git $(id)",              # substitution
    "echo hi | sh",           # pipe
    "rm -rf /",               # non-whitelisted
    "curl http://evil",       # non-whitelisted
])
async def test_bash_rejects_dangerous(payload):
    token = _set_upload_context(host_process_mode=HostProcessMode.TRUSTED_LOCAL)
    try:
        res = await bash.run(command=payload)
    finally:
        reset_execution_context(token)
    assert res.content.startswith("Error"), f"expected rejection for {payload!r}, got {res.content!r}"


@pytest.mark.asyncio
async def test_bash_allows_whitelisted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    token = _set_upload_context(host_process_mode=HostProcessMode.TRUSTED_LOCAL)
    try:
        res = await bash.run(command="echo hello")
    finally:
        reset_execution_context(token)
    assert "hello" in res.content


@pytest.mark.asyncio
async def test_bash_denies_without_authority_even_when_no_uploads_exist(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())

    result = await bash.run(command='echo should-not-run')

    assert result.content.startswith('Error')
    assert 'should-not-run' not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize('attack', ['absolute', 'recursive', 'foreign_cwd', 'python'])
async def test_bash_denies_commands_when_a_foreign_upload_is_visible(
    tmp_path, monkeypatch, attack
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    owned = _owned_upload_dir(tmp_path)
    foreign = _owned_upload_dir(tmp_path, session='session-2')
    owned.mkdir(parents=True)
    foreign.mkdir(parents=True)
    (owned / 'f_owned').write_text('owned content')
    foreign_file = foreign / 'f_foreign'
    foreign_file.write_text('foreign secret')
    script = tmp_path / 'read_foreign.py'
    script.write_text(
        f'from pathlib import Path\nprint(Path({str(foreign_file)!r}).read_text())\n'
    )
    commands = {
        'absolute': (f'cat "{foreign_file}"', os.fspath(tmp_path)),
        'recursive': ('rg foreign .', os.fspath(tmp_path)),
        'foreign_cwd': ('pwd', os.fspath(foreign)),
        'python': ('python read_foreign.py', os.fspath(tmp_path)),
    }
    command, working_dir = commands[attack]
    token = _set_upload_context()
    try:
        result = await bash.run(command=command, working_dir=working_dir)
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert 'foreign secret' not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize('context_kind', ['none', 'incomplete'])
async def test_bash_denies_managed_uploads_without_complete_turn_authority(
    tmp_path, monkeypatch, context_kind
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    owned = _owned_upload_dir(tmp_path)
    owned.mkdir(parents=True)
    target = owned / 'f_owned'
    target.write_text('private content')
    token = None
    if context_kind == 'incomplete':
        token = set_execution_context(ToolExecutionContext(session_id='session-1'))
    try:
        result = await bash.run(
            command=f'cat "{target}"', working_dir=os.fspath(tmp_path)
        )
    finally:
        if token is not None:
            reset_execution_context(token)

    assert result.content.startswith('Error')
    assert 'private content' not in result.content


@pytest.mark.asyncio
async def test_bash_sandbox_shell_flag_does_not_bypass_upload_isolation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    foreign = _owned_upload_dir(tmp_path, session='session-2')
    foreign.mkdir(parents=True)
    (foreign / 'f_foreign').write_text('foreign secret')
    monkeypatch.setenv('COGNITRIX_SANDBOX_SHELL', '1')
    token = _set_upload_context()
    try:
        result = await bash.run(command='rg foreign .', working_dir=os.fspath(tmp_path))
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert 'foreign secret' not in result.content


@pytest.mark.asyncio
async def test_open_file_denies_foreign_managed_upload(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    foreign = _owned_upload_dir(tmp_path, user='user-2')
    foreign.mkdir(parents=True)
    target = foreign / 'f_foreign'
    target.write_text('foreign secret')
    started = []
    monkeypatch.setattr(os, 'startfile', lambda value: started.append(value))
    token = _set_upload_context()
    try:
        result = await open_file.run(path=os.fspath(target))
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert started == []


@pytest.mark.asyncio
async def test_open_file_denies_managed_upload_even_for_trusted_local(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    owned = _owned_upload_dir(tmp_path)
    owned.mkdir(parents=True)
    target = owned / 'f_owned'
    target.write_text('owned content')
    started = []
    monkeypatch.setattr(os, 'startfile', lambda value: started.append(value))
    token = _set_upload_context(
        host_process_mode=HostProcessMode.TRUSTED_LOCAL
    )
    try:
        result = await open_file.run(
            path=os.fspath(target.parent), filename=target.name
        )
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert started == []


@pytest.mark.asyncio
async def test_delegated_agent_keeps_allowlists_but_not_document_or_host_authority(
    monkeypatch,
):
    from cognitrix.agents import Agent
    from cognitrix.sessions.base import Session

    target = type('Target', (), {'id': 'target-agent'})()
    captured = {}

    async def find_one(_query):
        return target

    class ChildSession:
        async def __call__(self, *args, **kwargs):
            captured.update(kwargs)

    async def get_session(_agent_id):
        return ChildSession()

    monkeypatch.setattr(Agent, 'find_one', find_one)
    monkeypatch.setattr(Session, 'get_by_agent_id', get_session)
    token = set_execution_context(ToolExecutionContext(
        session_id='parent-session',
        user_id='user-1',
        agent_id='parent-agent',
        allowed_agents=frozenset({'target-agent'}),
        host_process_mode=HostProcessMode.TRUSTED_LOCAL,
    ))
    try:
        result = await call_agent.run(name='child', task='inspect')
    finally:
        reset_execution_context(token)

    child = captured['tool_context']
    assert child.user_id == 'user-1'
    assert child.allowed_agents == frozenset({'target-agent'})
    assert child.session_id is None
    assert child.agent_id is None
    assert child.host_process_mode is HostProcessMode.DENY
    assert 'no output' in result.content


@pytest.mark.asyncio
async def test_read_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tools_root", tmp_path.resolve())
    res = await Read.run(file_path="../../../../etc/passwd")
    assert res.content.startswith("Error")


@pytest.mark.asyncio
async def test_read_allows_inside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tools_root", tmp_path.resolve())
    (tmp_path / "hello.txt").write_text("hi there")
    res = await Read.run(file_path="hello.txt")
    assert "hi there" in res.content


@pytest.mark.asyncio
async def test_write_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tools_root", tmp_path.resolve())
    res = await Write.run(file_path="../escape.txt", content="nope")
    assert res.content.startswith("Error")
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_read_denies_owner_prefix_without_an_exact_document_grant(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    owned = _owned_upload_dir(tmp_path)
    foreign = _owned_upload_dir(tmp_path, session='session-2')
    owned.mkdir(parents=True)
    foreign.mkdir(parents=True)
    (owned / 'f_owned').write_text('owned content')
    (foreign / 'f_foreign').write_text('foreign content')
    token = _set_upload_context()
    try:
        allowed = await Read.run(file_path=os.fspath((owned / 'f_owned').relative_to(tmp_path)))
        denied = await Read.run(file_path=os.fspath((foreign / 'f_foreign').relative_to(tmp_path)))
    finally:
        reset_execution_context(token)

    assert allowed.content.startswith('Error')
    assert denied.content.startswith('Error')
    assert 'owned content' not in allowed.content
    assert 'foreign content' not in denied.content


@pytest.mark.asyncio
async def test_read_denies_managed_uploads_without_a_bound_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    owned = _owned_upload_dir(tmp_path)
    owned.mkdir(parents=True)
    (owned / 'f_owned').write_text('private content')

    result = await Read.run(file_path=os.fspath((owned / 'f_owned').relative_to(tmp_path)))

    assert result.content.startswith('Error')
    assert 'private content' not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize('tool_name', ['write', 'edit'])
async def test_write_tools_never_mutate_managed_uploads(
    tmp_path, monkeypatch, tool_name
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    owned = _owned_upload_dir(tmp_path)
    owned.mkdir(parents=True)
    target = owned / 'f_owned'
    target.write_text('original')
    relative = os.fspath(target.relative_to(tmp_path))
    token = _set_upload_context()
    try:
        if tool_name == 'write':
            result = await Write.run(file_path=relative, content='changed')
        else:
            result = await Edit.run(
                file_path=relative, old_string='original', new_string='changed'
            )
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert target.read_text() == 'original'


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', [Grep, Glob])
async def test_search_tools_prune_the_entire_managed_upload_tree(
    tmp_path, monkeypatch, tool
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    owned = _owned_upload_dir(tmp_path)
    foreign = _owned_upload_dir(tmp_path, user='user-2')
    owned.mkdir(parents=True)
    foreign.mkdir(parents=True)
    (owned / 'f_owned.txt').write_text('needle-owned')
    (foreign / 'f_foreign.txt').write_text('needle-foreign')
    token = _set_upload_context()
    try:
        if tool is Grep:
            result = await tool.run(pattern='needle', path='.')
        else:
            result = await tool.run(pattern='f_*.txt', path='.')
    finally:
        reset_execution_context(token)

    assert 'f_owned.txt' not in result.content
    assert 'f_foreign.txt' not in result.content
    assert 'needle-foreign' not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', [Grep, Glob])
async def test_search_tools_are_confined_to_tools_root(tmp_path, monkeypatch, tool):
    root = tmp_path / 'root'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_text('needle-secret')
    monkeypatch.setattr(settings, 'tools_root', root.resolve())

    if tool is Grep:
        result = await tool.run(pattern='needle', path='../outside')
    else:
        result = await tool.run(pattern='*.txt', path='../outside')

    assert result.content.startswith('Error')
    assert 'secret.txt' not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize('tool', [Grep, Glob])
async def test_search_tools_deny_an_explicit_foreign_upload_path(
    tmp_path, monkeypatch, tool
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    foreign = _owned_upload_dir(tmp_path, session='session-2')
    foreign.mkdir(parents=True)
    (foreign / 'f_foreign.txt').write_text('needle-foreign')
    relative = os.fspath(foreign.relative_to(tmp_path))
    token = _set_upload_context()
    try:
        if tool is Grep:
            result = await tool.run(pattern='needle', path=relative)
        else:
            result = await tool.run(pattern='*.txt', path=relative)
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert 'f_foreign.txt' not in result.content


@pytest.mark.asyncio
async def test_upload_symlink_cannot_resolve_into_an_ordinary_tools_path(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, 'tools_root', tmp_path.resolve())
    ordinary = tmp_path / 'ordinary.txt'
    ordinary.write_text('ordinary secret')
    owned = _owned_upload_dir(tmp_path)
    owned.mkdir(parents=True)
    link = owned / 'f_link'
    try:
        link.symlink_to(ordinary)
    except OSError:
        pytest.skip('symlink creation is unavailable')
    token = _set_upload_context()
    try:
        result = await Read.run(file_path=os.fspath(link.relative_to(tmp_path)))
    finally:
        reset_execution_context(token)

    assert result.content.startswith('Error')
    assert 'ordinary secret' not in result.content
