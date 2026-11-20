"""Projects (cognitrix/projects) and the approval card's "Always allow".

- A project is the person's own: CRUD, moving chats and tasks in and out.
- A chat in a project gets its instructions, folders, knowledge and memories.
- With the shared sandbox, every agent of a project uses one desktop, user
  and ~/work folder; on a paired PC only the project's folders are used.
- "Always allow" saves the tool for the agent, or the command's prefix for
  that computer, and only single plain commands match a saved prefix.
"""
from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import httpx
import pytest

from cognitrix.projects import context as pctx
from cognitrix.safety import command_rules


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'projects.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    yield


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
async def client(db):
    import uvicorn
    from fastapi import FastAPI

    from cognitrix.api.routes.projects import projects_api
    from cognitrix.common.security import AuthContext, jwt_only

    app = FastAPI()
    app.include_router(projects_api, prefix='/api/v1')
    who = {'id': 'u1'}
    app.dependency_overrides[jwt_only] = lambda: AuthContext(user=SimpleNamespace(id=who['id'], email=''))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    task = asyncio.create_task(srv.serve())
    for _ in range(100):
        if srv.started:
            break
        await asyncio.sleep(0.05)
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}/api/v1') as http:
        yield SimpleNamespace(http=http, who=who)
    srv.should_exit = True
    await task


async def test_crud_and_owner_only(client, tmp_path, monkeypatch):
    monkeypatch.setenv("COGNITRIX_ALLOWED_FOLDERS", str(tmp_path))
    (tmp_path / 'repo').mkdir()
    (tmp_path / 'repo' / 'AGENTS.md').write_text('Use tabs.', encoding='utf-8')
    r = await client.http.post('/projects', json={
        'name': 'Website', 'folders': [{'path': str(tmp_path / 'repo'), 'access': 'write'}],
        'agents': ['a1', 'a1', 'a2'], 'computer': 'sandbox'})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p['instructions'] == 'Use tabs.'  # read from AGENTS.md
    assert p['agents'] == ['a1', 'a2'] and p['computer'] == 'sandbox'

    r = await client.http.get('/projects')
    assert [x['name'] for x in r.json()['projects']] == ['Website']

    r = await client.http.patch(f"/projects/{p['id']}", json={'name': 'Site', 'archived': True})
    assert r.json()['name'] == 'Site' and r.json()['archived']
    assert (await client.http.get('/projects')).json()['projects'] == []
    assert len((await client.http.get('/projects?archived=true')).json()['projects']) == 1

    r = await client.http.post('/projects', json={'name': 'x', 'computer': 'not-a-pc'})
    assert r.status_code == 400

    client.who['id'] = 'u2'
    assert (await client.http.get(f"/projects/{p['id']}")).status_code == 404
    assert (await client.http.patch(f"/projects/{p['id']}", json={'name': 'mine'})).status_code == 404
    client.who['id'] = 'u1'
    assert (await client.http.delete(f"/projects/{p['id']}")).status_code == 200


async def test_move_chat_and_project_memory(client):
    from cognitrix.memory import saved
    from cognitrix.sessions.base import Session

    p = (await client.http.post('/projects', json={'name': 'P', 'computer': 'sandbox',
                                                   'use_account_memory': False})).json()
    session = Session(agent_id='a1', user_id='u1')
    await session.save()
    r = await client.http.put(f'/projects/chats/{session.id}', json={'project_id': p['id']})
    assert r.status_code == 200
    moved = await Session.get(session.id)
    assert moved.project_id == p['id'] and moved.run_in_sandbox is True  # a new chat takes the project computer
    chats = (await client.http.get(f"/projects/{p['id']}")).json()['chats']
    assert [c['id'] for c in chats] == [str(session.id)]

    client.who['id'] = 'u2'
    assert (await client.http.put(f'/projects/chats/{session.id}', json={'project_id': None})).status_code == 404
    client.who['id'] = 'u1'

    await saved.add('u1', 'Account fact about tea', scope='global', source='user')
    r = await client.http.post(f"/projects/{p['id']}/memories", json={'content': 'Deploys go to staging first'})
    assert r.status_code == 200 and r.json()['project_id'] == p['id']

    agent = SimpleNamespace(id='a1', memory={'read_global': True})
    outside = [m.content for m in await saved.visible_memories('u1', agent)]
    assert 'Deploys go to staging first' not in outside and 'Account fact about tea' in outside

    turn = await pctx.for_session(moved)
    token = pctx.set_project(turn)
    try:
        inside = [m.content for m in await saved.visible_memories('u1', agent)]
    finally:
        pctx.reset(token)
    # use_account_memory off: only the project's own memories.
    assert inside == ['Deploys go to staging first']


async def test_shared_sandbox_identity_and_prompt():
    from cognitrix.common.sandbox_work import SandboxWork, sandbox_prompt

    project = pctx.TurnProject(id='p9', user_id='u1', name='Shop', instructions='Be brief.', computer='sandbox',
                               knowledge=('k2',))
    assert SandboxWork('u1', 'agent-a').agent == 'agent-a'
    token = pctx.set_project(project)
    try:
        assert SandboxWork('u1', 'agent-a').agent == SandboxWork('u1', 'agent-b').agent == 'project-p9'
        assert '/home/agent/work/project-p9' in sandbox_prompt([], 'agent-a', 'u1')
        note = pctx.prompt_note()
        assert 'Project: Shop' in note and 'Be brief.' in note and 'shares one sandbox' in note
        assert pctx.knowledge_for(SimpleNamespace(knowledge=['k1'])) == ['k1', 'k2']
    finally:
        pctx.reset(token)


def test_pc_folders_narrowed():
    from cognitrix.computer.paired.turn import _project_folders, device_prompt

    project = pctx.TurnProject(id='p1', user_id='u1', name='P', computer='dev1', pc_folders=('C:\\Code\\shop',))
    device = {'name': 'Desk', 'platform': 'windows', 'online': True, 'powers': ['files', 'commands'],
              'folders': [{'path': 'C:\\Code\\shop', 'writable': True}, {'path': 'C:\\Users\\me\\Docs', 'writable': True}]}
    token = pctx.set_project(project)
    try:
        text = device_prompt(device)
        assert _project_folders() == {'only_folders': ['C:\\Code\\shop']}
    finally:
        pctx.reset(token)
    assert 'C:\\Code\\shop' in text and 'Docs' not in text


def test_command_prefixes():
    assert command_rules.prefix_for('git status') == 'git status'
    assert command_rules.prefix_for('npm test -- --watch') == 'npm test'
    assert command_rules.prefix_for('ls -la src') == 'ls'
    assert command_rules.prefix_for('python build.py') == 'python build.py'
    for bad in ('python -c "x"', 'rm -rf build', 'git status && rm -rf ~', 'cat a > b', 'sudo ls',
                'echo $(whoami)', 'bash -c ls'):
        assert command_rules.prefix_for(bad) is None, bad
    assert command_rules.matches('git status', 'git status --short')
    assert not command_rules.matches('git status', 'git status; rm -rf /')
    assert not command_rules.matches('git status', 'git stash')


async def test_always_allow_command_rule(db):
    from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, ToolCall, web_turn_ctx
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

    gate = ApprovalGate(cache_dir=None)
    gate.permanent_cache.clear()
    asked: list[dict] = []

    async def fake_web(tool_call, risk, timeout, person_only=False, must_ask=False, scope=None):
        asked.append(gate._always_offer(tool_call, scope, person_only, must_ask))
        return ApprovalResult(approved=True, always=True)

    gate._web_approval = fake_web
    token = web_turn_ctx.set({'emit': None, 'session_id': 's1', 'user_key': 'u1'})
    try:
        risk = RiskAssessment(RiskLevel.MEDIUM, ['shell'], 'run')
        r = await gate.check_approval(ToolCall('Bash', {'command': 'git status'}), risk, interface='web',
                                      scope='a1', person_only=True)
        assert r.approved and asked == [{'kind': 'command', 'label': 'git status', 'computer': 'local'}]
        # A new chat (no session cache): the saved rule answers.
        gate.session_cache.clear()
        r = await gate.check_approval(ToolCall('Bash', {'command': 'git status -s'}), risk, interface='web',
                                      scope='a1', person_only=True)
        assert r.approved and r.cached and len(asked) == 1
        await gate.check_approval(ToolCall('Bash', {'command': 'git status; rm x'}), risk, interface='web',
                                  scope='a1', person_only=True)
        assert len(asked) == 2  # chained: asked again, and no "Always allow" offered
        assert asked[1] is None
    finally:
        web_turn_ctx.reset(token)
    rules = await command_rules.rules_of('u1')
    assert [(r.computer, r.prefix) for r in rules] == [('local', 'git status')]


async def test_always_allow_tool_saves_on_owned_agent(db):
    from cognitrix.models.agent import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, ToolCall, web_turn_ctx
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

    agent = Agent(name='Helper', system_prompt='hi', owner_id='u1', llm=LLM(provider='ollama', model='llama3'))
    await agent.save()
    gate = ApprovalGate(cache_dir=None)

    async def fake_web(*a, **k):
        return ApprovalResult(approved=True, always=True)

    gate._web_approval = fake_web
    token = web_turn_ctx.set({'emit': None, 'session_id': 's1', 'user_key': 'u1'})
    try:
        risk = RiskAssessment(RiskLevel.MEDIUM, ['network'], 'post')
        await gate.check_approval(ToolCall('Send Email', {'to': 'a'}), risk, interface='web', scope=str(agent.id))
        again = await gate.check_approval(ToolCall('Send Email', {'to': 'b'}), risk, interface='web',
                                          scope=str(agent.id))
        assert again.cached
    finally:
        web_turn_ctx.reset(token)
    stored = await Agent.get(agent.id)
    assert stored.tool_permissions.get('Send Email') == 'allow'


async def test_always_allow_on_someone_elses_agent_holds_for_this_chat(db):
    from cognitrix.models.agent import Agent
    from cognitrix.providers.base import LLM
    from cognitrix.safety.approval_gate import ApprovalGate, ApprovalResult, ToolCall, web_turn_ctx
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

    agent = Agent(name='Shared', system_prompt='hi', owner_id='owner', llm=LLM(provider='ollama', model='llama3'))
    await agent.save()
    gate = ApprovalGate(cache_dir=None)
    asked = []

    async def fake_web(*a, **k):
        asked.append(1)
        return ApprovalResult(approved=True, always=True)

    gate._web_approval = fake_web
    risk = RiskAssessment(RiskLevel.MEDIUM, ['network'], 'post')
    call = lambda: gate.check_approval(ToolCall('Send Email', {'to': 'a'}), risk,  # noqa: E731
                                       interface='web', scope=str(agent.id))
    token = web_turn_ctx.set({'emit': None, 'session_id': 's1', 'user_key': 'u1'})
    try:
        await call()
        assert (await call()).cached and len(asked) == 1
    finally:
        web_turn_ctx.reset(token)
    # Another person on the same agent is still asked.
    token = web_turn_ctx.set({'emit': None, 'session_id': 's2', 'user_key': 'u2'})
    try:
        await call()
        assert len(asked) == 2
    finally:
        web_turn_ctx.reset(token)
    stored = await Agent.get(agent.id)
    assert not (stored.tool_permissions or {}).get('Send Email')


async def test_dream_keeps_same_fact_in_two_projects(monkeypatch):
    from cognitrix.memory import dream, saved

    rows = [SimpleNamespace(id=f'm{i}', scope='project', agent_id=None, project_id=f'p{i}', content='Use pnpm',
                            pinned=False, created=f'2026-11-2{i}', source='agent', by='agent', valid_until=None)
            for i in (1, 2)]
    deleted = []

    async def fake_delete(row, **k):
        deleted.append(row.id)

    monkeypatch.setattr(saved, 'delete', fake_delete)
    counts = await dream.free_pass('u1', rows, 'd1', auto=True, proposals=[])
    assert counts['duplicates'] == 0 and not deleted
