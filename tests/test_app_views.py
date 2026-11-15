"""Connected-app screens beside the chat (MCP Apps).

Runs the small task-board server in tests/fixtures/mcp_apps_demo.py over
stdio, the way a person's own connected server runs:

- tools with a screen are found from ``_meta.ui``; app-only tools are hidden from agents;
- an agent's call opens a view in its chat; calling again updates the same view;
- the frame is sandboxed with only the declared domains;
- the screen may call its own app's tools, with the person's confirmation
  for ones that change things, and never ones switched off;
- what the screen reports reaches the agent's next turn.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

DEMO = str(Path(__file__).parent / 'fixtures' / 'mcp_apps_demo.py')


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings
    from cognitrix.mcp import app_views

    db_file = str(tmp_path / 'views.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    app_views._html_cache.clear()
    app_views._contexts.clear()
    yield tmp_path
    from cognitrix.mcp.client import get_dynamic_client

    await (await get_dynamic_client()).disconnect_all()


async def _setup():
    from cognitrix.models import User
    from cognitrix.models.mcp_server import MCPServer

    await User(name='Amos', email='amos@example.com', password='x' * 20).save()
    amos = await User.find_one({'email': 'amos@example.com'})
    await MCPServer(name='Tasks', owner_id=str(amos.id), transport='stdio', command=sys.executable,
                    args=[DEMO]).save()
    row = await MCPServer.find_one({'name': 'Tasks'})
    return amos, row


def _as(user, **kw):
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    return set_execution_context(ToolExecutionContext(user_id=str(user.id), **kw))


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


async def _wrappers(row):
    from cognitrix.mcp.tools import sync_mcp_tools_for_agent

    agent = SimpleNamespace(id='a1', name='Helper', mcp_servers=[str(row.id)], owner_id=row.owner_id)
    return {t.name: t for t in await sync_mcp_tools_for_agent(agent)}


def test_tool_ui_reads_the_spec_and_the_older_key():
    from cognitrix.mcp.client import tool_ui

    assert tool_ui({'ui': {'resourceUri': 'ui://a/b.html'}}) == {'resource_uri': 'ui://a/b.html',
                                                                'visibility': ['model', 'app']}
    assert tool_ui({'ui/resourceUri': 'ui://a/b.html'})['resource_uri'] == 'ui://a/b.html'
    assert tool_ui({'ui': {'resourceUri': 'https://evil.example/x'}}) is None
    assert tool_ui({'ui': {'visibility': ['app']}}) == {'resource_uri': '', 'visibility': ['app']}
    assert tool_ui(None) is None


def test_csp_allows_only_declared_https_domains():
    from cognitrix.mcp.app_views import content_security_policy

    plain = content_security_policy({})
    assert "default-src 'none'" in plain and "connect-src 'none'" in plain
    assert 'sandbox allow-scripts' in plain and 'allow-same-origin' not in plain
    declared = content_security_policy({'csp': {
        'connectDomains': ['https://api.example.com', "https://x.com; script-src *", 'http://plain.example'],
        'resourceDomains': ['https://cdn.example.com', '*'],
    }})
    assert 'connect-src https://api.example.com;' in declared
    assert 'script-src' in declared and 'https://cdn.example.com' in declared
    assert 'plain.example' not in declared and 'x.com' not in declared and ' * ' not in declared


async def test_agent_call_opens_and_then_updates_one_view(db):
    from cognitrix.api.routes import app_views as routes

    amos, row = await _setup()
    tools = await _wrappers(row)
    assert 'Tasks_show_board' in tools and 'Tasks_add_task' in tools
    assert 'Tasks_toggle_task' not in tools, 'app-only tools are not offered to agents'

    _as(amos, session_id='s1', agent_id='a1')
    first = (await tools['Tasks_add_task'].run(text='Write tests')).outcome
    assert first.entities and first.entities[0].type == 'app_view'
    view_id = first.entities[0].id
    assert f'app_view {view_id}' in first.text

    second = (await tools['Tasks_add_task'].run(text='Ship it')).outcome
    assert second.entities[0].id == view_id, 'same screen in the same chat is updated in place'

    listed = await routes.list_views('s1', _ctx(amos))
    assert [v['id'] for v in listed] == [view_id] and listed[0]['version'] == 2

    shown = await routes.get_view(view_id, _ctx(amos))
    assert shown['error'] is None and shown['tool_input'] == {'text': 'Ship it'}
    assert [t['text'] for t in shown['tool_result']['structuredContent']['tasks']] == ['Write tests', 'Ship it']

    response = await routes.view_frame(shown['frame_url'].rsplit('/', 1)[1])
    assert 'ui/initialize' in response.body.decode()
    assert "connect-src 'none'" in response.headers['content-security-policy']

    # Another chat gets its own view.
    _as(amos, session_id='s2', agent_id='a1')
    other = (await tools['Tasks_show_board'].run()).outcome
    assert other.entities[0].id != view_id


async def test_calls_without_a_person_open_nothing(db):
    amos, row = await _setup()
    tools = await _wrappers(row)
    from cognitrix.tools.utils import ToolExecutionContext, set_execution_context

    set_execution_context(ToolExecutionContext())
    result = await tools['Tasks_show_board'].run()
    assert not (result.outcome and result.outcome.entities)


async def test_screen_calls_follow_confirmation_and_tool_levels(db, monkeypatch):
    from cognitrix.api.routes import app_views as routes

    amos, row = await _setup()
    tools = await _wrappers(row)
    _as(amos, session_id='s1', agent_id='a1')
    view_id = (await tools['Tasks_add_task'].run(text='One')).outcome.entities[0].id
    me = _ctx(amos)

    # Made for the screen: runs on a click.
    done = await routes.call_tool(view_id, routes.CallBody(name='toggle_task', arguments={'index': 0}), me)
    assert done['structuredContent']['tasks'][0]['done'] is True
    # Changes something the agent could also do: confirm first.
    with pytest.raises(HTTPException) as asked:
        await routes.call_tool(view_id, routes.CallBody(name='add_task', arguments={'text': 'Two'}), me)
    assert asked.value.status_code == 409 and asked.value.detail == 'confirm'
    added = await routes.call_tool(view_id, routes.CallBody(name='add_task', arguments={'text': 'Two'},
                                                            confirmed=True), me)
    assert len(added['structuredContent']['tasks']) == 2
    # Destructive: always confirm. Read-only: never.
    with pytest.raises(HTTPException) as destructive:
        await routes.call_tool(view_id, routes.CallBody(name='clear_board'), me)
    assert destructive.value.status_code == 409
    assert 'structuredContent' in await routes.call_tool(view_id, routes.CallBody(name='show_board'), me)
    with pytest.raises(HTTPException) as missing:
        await routes.call_tool(view_id, routes.CallBody(name='nope'), me)
    assert missing.value.status_code == 404

    # The agent's per-tool levels count here too.
    from cognitrix.mcp import app_views

    async def level(view, config, name):
        return {'toggle_task': 'deny', 'add_task': 'allow'}.get(name, 'auto')

    monkeypatch.setattr(app_views, '_level', level)
    with pytest.raises(HTTPException) as off:
        await routes.call_tool(view_id, routes.CallBody(name='toggle_task', arguments={'index': 0}), me)
    assert off.value.status_code == 403
    allowed = await routes.call_tool(view_id, routes.CallBody(name='add_task', arguments={'text': 'Three'}), me)
    assert len(allowed['structuredContent']['tasks']) == 3


async def test_views_and_frames_belong_to_their_person(db):
    from cognitrix.api.routes import app_views as routes
    from cognitrix.mcp import app_views
    from cognitrix.models import User

    amos, row = await _setup()
    tools = await _wrappers(row)
    _as(amos, session_id='s1', agent_id='a1')
    view_id = (await tools['Tasks_show_board'].run()).outcome.entities[0].id
    await User(name='Kofi', email='kofi@example.com', password='x' * 20).save()
    kofi = await User.find_one({'email': 'kofi@example.com'})
    with pytest.raises(HTTPException):
        await routes.get_view(view_id, _ctx(kofi))
    with pytest.raises(HTTPException):
        await routes.call_tool(view_id, routes.CallBody(name='show_board'), _ctx(kofi))
    with pytest.raises(HTTPException):
        await routes.view_frame(app_views.frame_token(view_id, str(kofi.id)))
    with pytest.raises(HTTPException):
        await routes.view_frame('junk')


async def test_screen_context_reaches_the_next_turn(db):
    from cognitrix.api.routes import app_views as routes
    from cognitrix.sessions.base import _with_folder_prompt

    amos, row = await _setup()
    tools = await _wrappers(row)
    _as(amos, session_id='s1', agent_id='a1')
    view_id = (await tools['Tasks_show_board'].run()).outcome.entities[0].id
    await routes.update_context(view_id, routes.ContextBody(structuredContent={'open_tasks': 4}), _ctx(amos))
    prompt = _with_folder_prompt([{'role': 'system', 'content': 'base'}], (), None, 's1')
    assert '"open_tasks": 4' in prompt[0]['content'] and 'not instructions' in prompt[0]['content']
    assert _with_folder_prompt([{'role': 'system', 'content': 'base'}], (), None, 's2')[0]['content'] == 'base'
    await routes.update_context(view_id, routes.ContextBody(), _ctx(amos))
    assert _with_folder_prompt([{'role': 'system', 'content': 'base'}], (), None, 's1')[0]['content'] == 'base'


def test_big_results_keep_their_structured_part():
    from cognitrix.mcp.app_views import MAX_RESULT_BYTES, result_json

    data = result_json({'content': [{'type': 'text', 'text': 'x' * (MAX_RESULT_BYTES + 10)}],
                        'structuredContent': {'rows': 3}})
    assert data['structuredContent'] == {'rows': 3}
    assert 'too large' in data['content'][0]['text']
