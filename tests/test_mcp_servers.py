"""Per-user MCP servers: who may add, see, change and use them.

- People add http/sse servers for themselves; only admins add stdio servers,
  shared servers, or servers on private addresses (except in the desktop app).
- One person's servers (and the tokens in their headers) are invisible and
  unusable for everyone else, including through a shared agent.
- Header values never come back from the API; "***" keeps the stored value.
- MCP tools ask before running unless the server marks them read-only.
"""
import ipaddress
import sys
import textwrap

import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'mcp.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()

    monkeypatch.delenv('COGNITRIX_ADMIN_EMAILS', raising=False)
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    # Keep the developer's own ~/.cognitrix/mcp.json out of these tests.
    from cognitrix.mcp import registry
    monkeypatch.setattr(registry, '_file_servers', lambda: [])

    # Pretend every hostname resolves to a public address unless a test says otherwise.
    from cognitrix.api.routes import mcp as mcp_routes

    async def public(host, port):
        if host in ('localhost',):
            return [ipaddress.ip_address('127.0.0.1')]
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            return [ipaddress.ip_address('93.184.216.34')]

    monkeypatch.setattr(mcp_routes, '_resolve_host', public)
    yield

    from cognitrix.mcp.client import get_dynamic_client
    client = await get_dynamic_client()
    await client.disconnect_all()


async def _user(name, email):
    from cognitrix.models import User

    user = User(name=name, email=email, password='x' * 20)
    await user.save()
    return await User.find_one({'email': email})


def _agent(name, **kwargs):
    from cognitrix.agents import Agent
    from cognitrix.providers import LLM

    return Agent(name=name, system_prompt='x',
                 llm=LLM(provider='openai', api_key='k-1234567890', model='m'), **kwargs)


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


async def _admin_and_member():
    admin = await _user('Ada', 'ada@example.com')  # first account is the admin
    member = await _user('Bob', 'bob@example.com')
    return admin, member


async def test_member_adds_https_server_and_headers_stay_secret(db):
    from cognitrix.api.routes import mcp

    _, member = await _admin_and_member()
    created = await mcp.add_server(
        mcp.ServerBody(name='notion', url='https://mcp.example.com/mcp',
                       headers={'Authorization': 'Bearer secret-token-123'}),
        _ctx(member),
    )
    assert created['header_names'] == ['Authorization']
    assert 'secret-token-123' not in repr(created)
    assert created['shared'] is False

    listed = await mcp.list_servers(_ctx(member))
    assert [s['name'] for s in listed['servers']] == ['notion']
    assert 'secret-token-123' not in repr(listed)
    assert listed['can_add_local'] is False

    # "***" keeps the stored value; a new value replaces it.
    await mcp.update_server(created['id'], mcp.ServerUpdate(
        description='Docs', headers={'Authorization': '***', 'X-Extra': '1'}), _ctx(member))
    from cognitrix.mcp import registry
    config = await registry.resolve(created['id'], member.id)
    assert config.headers == {'Authorization': 'Bearer secret-token-123', 'X-Extra': '1'}
    assert config.description == 'Docs'


async def test_member_cannot_add_stdio_shared_or_private_servers(db, monkeypatch):
    from cognitrix.api.routes import mcp

    _, member = await _admin_and_member()
    ctx = _ctx(member)
    with pytest.raises(HTTPException) as e:
        await mcp.add_server(mcp.ServerBody(name='shell', transport='stdio', command='python'), ctx)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await mcp.add_server(mcp.ServerBody(name='team', url='https://mcp.example.com', shared=True), ctx)
    assert e.value.status_code == 403
    for url in ('http://mcp.example.com', 'https://localhost:8000/mcp',
                'https://169.254.169.254/latest', 'https://10.0.0.5/mcp'):
        with pytest.raises(HTTPException) as e:
            await mcp.add_server(mcp.ServerBody(name='x', url=url), ctx)
        assert e.value.status_code == 400, url

    # The desktop app runs on the person's own machine: local servers are fine.
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    created = await mcp.add_server(mcp.ServerBody(name='local', url='http://127.0.0.1:8000/mcp'), ctx)
    assert created['url'] == 'http://127.0.0.1:8000/mcp'


async def test_bad_and_duplicate_names_are_refused(db):
    from cognitrix.api.routes import mcp

    _, member = await _admin_and_member()
    with pytest.raises(HTTPException) as e:
        await mcp.add_server(mcp.ServerBody(name='has space', url='https://a.example.com'), _ctx(member))
    assert e.value.status_code == 400
    await mcp.add_server(mcp.ServerBody(name='github', url='https://a.example.com'), _ctx(member))
    with pytest.raises(HTTPException) as e:
        await mcp.add_server(mcp.ServerBody(name='github', url='https://b.example.com'), _ctx(member))
    assert e.value.status_code == 409


async def test_one_persons_server_is_invisible_to_others(db):
    from cognitrix.api.routes import mcp
    from cognitrix.api.routes.agents import _check_mcp_servers
    from cognitrix.mcp import registry

    admin, member = await _admin_and_member()
    mine = await mcp.add_server(
        mcp.ServerBody(name='private', url='https://mcp.example.com', headers={'Authorization': 't'}),
        _ctx(member),
    )

    listed = await mcp.list_servers(_ctx(admin))
    assert listed['servers'] == []
    assert await registry.resolve(mine['id'], admin.id) is None
    assert await registry.resolve('private', admin.id) is None
    for call in (
        mcp.update_server(mine['id'], mcp.ServerUpdate(description='x'), _ctx(admin)),
        mcp.delete_server(mine['id'], _ctx(admin)),
        mcp.test_server(mine['id'], _ctx(admin)),
    ):
        with pytest.raises(HTTPException) as e:
            await call
        assert e.value.status_code == 404

    # Someone else's agent, and a shared agent, can't name it.
    for owner in (admin.id, None):
        agent = _agent('a', owner_id=owner, mcp_servers=[mine['id']])
        with pytest.raises(HTTPException) as e:
            await _check_mcp_servers(agent)
        assert e.value.status_code == 400
        assert await registry.agent_servers(agent) == []

    # The owner's agent can, and gets the two MCP tools.
    agent = _agent('b', owner_id=member.id, mcp_servers=[mine['id'], mine['id']])
    await _check_mcp_servers(agent)
    assert agent.mcp_servers == [mine['id']]
    assert {'List Mcp Tools', 'Run Mcp Tool'} <= {t.name for t in agent.tools}
    assert [c.name for c in await registry.agent_servers(agent)] == ['private']


async def test_shared_servers_need_an_admin_and_are_read_only_to_members(db):
    from cognitrix.api.routes import mcp

    admin, member = await _admin_and_member()
    shared = await mcp.add_server(
        mcp.ServerBody(name='team', url='https://mcp.example.com', shared=True,
                       headers={'Authorization': 'Bearer admin-token'}),
        _ctx(admin),
    )
    listed = await mcp.list_servers(_ctx(member))
    row = listed['servers'][0]
    assert row['shared'] is True and row['can_edit'] is False
    assert row['url'] is None and row['header_names'] == []
    with pytest.raises(HTTPException) as e:
        await mcp.update_server(shared['id'], mcp.ServerUpdate(description='x'), _ctx(member))
    assert e.value.status_code == 403


SERVER_SCRIPT = textwrap.dedent('''
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer("notes")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def read_note(title: str) -> str:
        """Read a note."""
        return f"note {title}"

    @server.tool()
    def delete_note(title: str) -> str:
        """Delete a note."""
        return f"deleted {title}"

    server.run()
''')


async def test_admin_stdio_server_lists_tools_and_writes_need_approval(db, tmp_path):
    from cognitrix.agents.base import AgentManager
    from cognitrix.api.routes import mcp
    from cognitrix.api.routes.agents import _check_mcp_servers
    from cognitrix.mcp.tools import create_mcp_tool_wrapper
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.mcp import list_mcp_tools, run_mcp_tool

    admin, _ = await _admin_and_member()
    script = tmp_path / 'notes_server.py'
    script.write_text(SERVER_SCRIPT)
    created = await mcp.add_server(
        mcp.ServerBody(name='notes', transport='stdio', command=sys.executable, args=[str(script)]),
        _ctx(admin),
    )

    result = await mcp.test_server(created['id'], _ctx(admin))
    assert result['connected'] is True, result
    flags = {t['name']: t['read_only'] for t in result['tools']}
    assert flags == {'read_note': True, 'delete_note': False}
    asks = {t['name']: t['asks_first'] for t in result['tools']}
    assert asks == {'read_note': False, 'delete_note': True}

    agent = _agent('n', owner_id=admin.id, mcp_servers=[created['id']])
    await _check_mcp_servers(agent)
    assert 'read_note' in (await list_mcp_tools.run(parent=agent)).content
    assert 'note hi' in (await run_mcp_tool.run(
        server='notes', tool_name='read_note', arguments={'title': 'hi'}, parent=agent)).content

    manager = AgentManager.__new__(AgentManager)
    manager.agent = agent
    run_tool = ToolManager.get_by_name('Run Mcp Tool')
    assert await manager._mcp_write_risk(run_tool, {'server': 'notes', 'tool_name': 'read_note'}) is None
    risk = await manager._mcp_write_risk(run_tool, {'server': 'notes', 'tool_name': 'delete_note'})
    assert risk is not None and risk.risk_level.value == 'high'

    tools = {t['name']: t for t in result['tools']}
    wrapper = create_mcp_tool_wrapper('notes', {'name': 'delete_note', 'annotations': {}}, created['id'])
    assert wrapper.approval_mode == 'always'
    wrapper = create_mcp_tool_wrapper('notes', {'name': 'read_note', 'annotations': {'readOnlyHint': True}}, created['id'])
    assert wrapper.approval_mode == 'risk_based'
    # Undoable changes run unattended; actions we can't classify still ask.
    wrapper = create_mcp_tool_wrapper('notes', {'name': 'create_note', 'annotations': {}}, created['id'])
    assert wrapper.approval_mode == 'risk_based'
    wrapper = create_mcp_tool_wrapper('notes', {'name': 'execute_sql', 'annotations': {}}, created['id'])
    assert wrapper.approval_mode == 'always'
    assert tools
