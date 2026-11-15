"""Public agent links: visitors chat with an agent without an account.

- Only the agent's owner turns a link on; the server can turn them all off.
- A visitor's chat belongs to the visitor token, never shows in the owner's
  chat list and has none of the owner's memories.
- A visitor's turn is offered (and may run) only the public tools.
- Limits per visitor, per link per day, per network address.
- The chat page has its own CSP; listed websites are the only frame parents.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings
    from cognitrix.public_agents import service

    db_file = str(tmp_path / 'public.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setattr(settings, 'workdir', tmp_path)
    monkeypatch.delenv('COGNITRIX_PUBLIC_AGENT_LINKS', raising=False)
    service.reset_limits()
    yield tmp_path


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


async def _agent(owner, name='Helper'):
    from cognitrix.agents import Agent
    from cognitrix.providers import LLM

    agent = Agent(name=name, system_prompt='You help customers of Acme.', owner_id=str(owner.id),
                  llm=LLM(provider='openai', model='gpt-test', api_key='sk-test'))
    await agent.save()
    return await Agent.find_one({'id': agent.id})


def _request(host='1.2.3.4'):
    return SimpleNamespace(client=SimpleNamespace(host=host),
                           url=SimpleNamespace(scheme='https', netloc='cog.example.com'))


async def _events(response):
    out = []
    async for item in response.body_iterator:
        data = item.get('data') if isinstance(item, dict) else None
        if data:
            out.append(json.loads(data))
    return out


@pytest.fixture
def fake_turn(monkeypatch):
    """Stand in for the model: record what the turn could see, then answer."""
    from cognitrix.sessions.base import Session

    seen = {}

    async def call(self, message, agent, *args, **kwargs):
        from cognitrix.safety import permissions

        seen['prompt'] = agent.system_prompt
        seen['public_tools'] = permissions.public_tools.get()
        seen['read_only'] = permissions.read_only_run.get()
        seen['guest'] = permissions.guest_run.get()
        seen['user_id'] = getattr(kwargs.get('tool_context'), 'user_id', None)
        self.update_history({'role': 'user', 'type': 'text', 'content': message})
        await kwargs['output']({'type': 'generate', 'content': 'Hello '})
        await kwargs['output']({'type': 'generate', 'content': 'there<think>secret</think>'})
        self.update_history({'role': 'assistant', 'type': 'text', 'content': 'Hello there<think>secret</think>'})
        await self.save()

    monkeypatch.setattr(Session, '__call__', call)
    return seen


async def test_owner_turns_the_link_on_and_others_cannot(db):
    from cognitrix.api.routes import public_agents as routes

    amos = await _user('Amos', 'amos@example.com')
    kofi = await _user('Kofi', 'kofi@example.com')
    agent = await _agent(amos)

    view = await routes.get_link(agent.id, _ctx(amos))
    assert view['link'] is None and view['allowed']
    view = await routes.set_link(agent.id, routes.LinkUpdate(greeting='Hi! Ask me about Acme.',
                                                              sites=['shop.example.com/help']), _ctx(amos))
    link = view['link']
    assert link['enabled'] and link['url'].startswith('/chat/') and len(link['url']) > 30
    assert link['sites'] == ['https://shop.example.com']
    with pytest.raises(HTTPException) as err:
        await routes.get_link(agent.id, _ctx(kofi))
    assert err.value.status_code == 404
    with pytest.raises(HTTPException):
        await routes.set_link(agent.id, routes.LinkUpdate(sites=['javascript:alert(1)']), _ctx(amos))

    token = link['url'].rsplit('/', 1)[1]
    info = await routes.public_info(token)
    assert info == {'title': 'Helper', 'agent': 'Helper', 'greeting': 'Hi! Ask me about Acme.', 'emoji': '', 'hue': None}

    rotated = (await routes.rotate_link(agent.id, _ctx(amos)))['link']
    assert rotated['url'] != link['url']
    with pytest.raises(HTTPException):
        await routes.public_info(token)

    await routes.set_link(agent.id, routes.LinkUpdate(enabled=False), _ctx(amos))
    with pytest.raises(HTTPException):
        await routes.public_info(rotated['url'].rsplit('/', 1)[1])


async def test_server_switch_turns_links_off(db, monkeypatch):
    from cognitrix.api.routes import public_agents as routes

    amos = await _user('Amos', 'amos@example.com')
    agent = await _agent(amos)
    token = (await routes.set_link(agent.id, routes.LinkUpdate(), _ctx(amos)))['link']['url'].rsplit('/', 1)[1]
    monkeypatch.setenv('COGNITRIX_PUBLIC_AGENT_LINKS', 'off')
    with pytest.raises(HTTPException):
        await routes.public_info(token)
    page = await routes.chat_page(token)
    assert page.status_code == 404
    monkeypatch.delenv('COGNITRIX_PUBLIC_AGENT_LINKS')
    assert (await routes.public_info(token))['agent'] == 'Helper'


async def test_visitor_chat_is_theirs_and_restricted(db, fake_turn):
    from cognitrix.api.routes import public_agents as routes
    from cognitrix.sessions.base import Session
    from cognitrix.sessions.index import ChatIndex

    amos = await _user('Amos', 'amos@example.com')
    agent = await _agent(amos)
    token = (await routes.set_link(agent.id, routes.LinkUpdate(instructions='Never offer refunds.'),
                                   _ctx(amos)))['link']['url'].rsplit('/', 1)[1]

    started = await routes.public_new_chat(token, _request())
    chat, visitor = started['chat_id'], started['visitor_token']
    response = await routes.public_send(token, chat, routes.VisitorMessage(message='What do you sell?'),
                                        _request(), x_visitor_token=visitor)
    events = await _events(response)
    assert [e['type'] for e in events] == ['text', 'text', 'done']
    assert events[-1]['content'] == 'Hello there'  # reasoning stripped

    # The turn ran as a restricted visitor turn, counted for the owner.
    assert fake_turn['public_tools'] == frozenset({'search_knowledge'})
    assert fake_turn['read_only'] and fake_turn['guest']
    assert fake_turn['user_id'] == str(amos.id)
    assert 'answering a visitor' in fake_turn['prompt'] and 'Never offer refunds.' in fake_turn['prompt']

    # Only the visitor token reads it.
    read = await routes.public_read_chat(token, chat, x_visitor_token=visitor)
    assert read['messages'] == [{'role': 'user', 'content': 'What do you sell?'},
                                {'role': 'assistant', 'content': 'Hello there'}]
    with pytest.raises(HTTPException):
        await routes.public_read_chat(token, chat, x_visitor_token='someone-else')
    with pytest.raises(HTTPException):
        await routes.public_send(token, chat, routes.VisitorMessage(message='hi'), _request(),
                                 x_visitor_token='someone-else')

    # Not in the owner's chat list; the owner reads it on the link.
    session = await Session.get(chat)
    assert session.user_id == str(amos.id) and session.public_link_id and session.folders == []
    assert await ChatIndex.find_one({'session_id': chat}) is None
    listed = await routes.list_link_chats(agent.id, _ctx(amos))
    assert listed['total'] == 1 and listed['chats'][0]['preview'] == 'What do you sell?'
    assert (await routes.read_link_chat(agent.id, chat, _ctx(amos)))['messages'][1]['content'] == 'Hello there'

    # Removing the link removes visitors' chats.
    await routes.delete_link(agent.id, _ctx(amos))
    assert await Session.get(chat) is None


async def test_web_search_is_only_offered_when_allowed(db, fake_turn):
    from cognitrix.api.routes import public_agents as routes

    amos = await _user('Amos', 'amos@example.com')
    agent = await _agent(amos)
    token = (await routes.set_link(agent.id, routes.LinkUpdate(web_search=True), _ctx(amos)))['link']['url'].rsplit('/', 1)[1]
    started = await routes.public_new_chat(token, _request())
    await _events(await routes.public_send(token, started['chat_id'], routes.VisitorMessage(message='hi'),
                                           _request(), x_visitor_token=started['visitor_token']))
    assert 'tavily_search' in fake_turn['public_tools'] and 'web_fetch' not in fake_turn['public_tools']


def test_public_tools_filter_offers_and_blocks():
    from cognitrix.safety import permissions
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel

    agent = SimpleNamespace(tool_permissions={}, permission_level='allow')
    tools = [SimpleNamespace(name=n, category='') for n in ('Search Knowledge', 'Read', 'Bash', 'Memory', 'Web Fetch')]
    token = permissions.public_tools.set(frozenset({'search_knowledge'}))
    ro = permissions.read_only_run.set('visitor')
    try:
        assert [t.name for t in permissions.visible_tools(agent, tools)] == ['Search Knowledge']
        risk = RiskAssessment(RiskLevel.LOW, [], '')
        run = permissions.ToolDecision('run', risk)
        for tool in tools[1:]:
            assert permissions.read_only_block(run, tool, {}).action == 'block'
        assert permissions.read_only_block(run, tools[0], {'query': 'x'}).action == 'run'
    finally:
        permissions.public_tools.reset(token)
        permissions.read_only_run.reset(ro)
    assert len(permissions.visible_tools(agent, tools)) == 5


async def test_limits(db, fake_turn, monkeypatch):
    from cognitrix.api.routes import public_agents as routes
    from cognitrix.public_agents import service

    amos = await _user('Amos', 'amos@example.com')
    agent = await _agent(amos)
    token = (await routes.set_link(agent.id, routes.LinkUpdate(visitor_hourly_limit=2, daily_limit=3),
                                   _ctx(amos)))['link']['url'].rsplit('/', 1)[1]

    async def send(chat, visitor, host='1.2.3.4'):
        return await _events(await routes.public_send(token, chat, routes.VisitorMessage(message='hi'),
                                                      _request(host), x_visitor_token=visitor))

    a = await routes.public_new_chat(token, _request())
    await send(a['chat_id'], a['visitor_token'])
    await send(a['chat_id'], a['visitor_token'])
    with pytest.raises(HTTPException) as err:
        await send(a['chat_id'], a['visitor_token'])
    assert err.value.status_code == 429  # this visitor, this hour

    b = await routes.public_new_chat(token, _request('5.6.7.8'))
    await send(b['chat_id'], b['visitor_token'], '5.6.7.8')
    with pytest.raises(HTTPException) as err:
        await send(b['chat_id'], b['visitor_token'], '5.6.7.8')
    assert 'today' in err.value.detail  # the whole link, today
    assert (await routes.get_link(agent.id, _ctx(amos)))['link']['used_today'] == 3

    monkeypatch.setattr(service, 'IP_NEW_CHATS_PER_HOUR', 1)
    await routes.public_new_chat(token, _request('9.9.9.9'))
    with pytest.raises(HTTPException):
        await routes.public_new_chat(token, _request('9.9.9.9'))


async def test_visitor_chats_have_no_owner_memories(db):
    from cognitrix.memory.hybrid_context import HybridContextManager
    from cognitrix.sessions.base import Session

    amos = await _user('Amos', 'amos@example.com')
    agent = await _agent(amos)
    called = []

    async def block(*args, **kwargs):
        called.append(True)
        return 'MEMORY'

    import cognitrix.memory.saved as saved
    original = saved.prompt_block
    saved.prompt_block = block
    try:
        manager = HybridContextManager.__new__(HybridContextManager)
        manager.short_term = SimpleNamespace(build_prompt=lambda *a: _value([{}, ]))
        visitor = Session(agent_id=agent.id, user_id=str(amos.id), public_link_id='l1', chat=[])
        prompt = await manager.build_prompt(agent, visitor)
        assert 'MEMORY' not in prompt[0]['content'] and not called
        own = Session(agent_id=agent.id, user_id=str(amos.id), chat=[])
        prompt = await manager.build_prompt(agent, own)
        assert 'MEMORY' in prompt[0]['content']
    finally:
        saved.prompt_block = original


async def _value(v):
    return v


async def test_page_csp_and_widget(db):
    from cognitrix.api.routes import public_agents as routes

    amos = await _user('Amos', 'amos@example.com')
    agent = await _agent(amos, name='Acme <Help>')
    token = (await routes.set_link(agent.id, routes.LinkUpdate(sites=['https://shop.example.com']),
                                   _ctx(amos)))['link']['url'].rsplit('/', 1)[1]
    page = await routes.chat_page(token)
    csp = page.headers['content-security-policy']
    assert "frame-ancestors 'self' https://shop.example.com" in csp
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    body = page.body.decode()
    assert 'Acme &lt;Help&gt;' in body and '<Help>' not in body

    widget = await routes.chat_widget(token, _request())
    js = widget.body.decode()
    assert f'https://cog.example.com/chat/{token}?embed=1' in js

    # Any website, when none are listed.
    await routes.set_link(agent.id, routes.LinkUpdate(sites=[]), _ctx(amos))
    assert "frame-ancestors 'self' https: http:" in (await routes.chat_page(token)).headers['content-security-policy']


async def test_deleting_the_agent_removes_its_link(db, fake_turn):
    from cognitrix.api.routes import agents as agent_routes
    from cognitrix.api.routes import public_agents as routes
    from cognitrix.public_agents.models import PublicAgentLink

    amos = await _user('Amos', 'amos@example.com')
    agent = await _agent(amos)
    await routes.set_link(agent.id, routes.LinkUpdate(), _ctx(amos))
    await agent_routes.delete_agent(agent.id, _ctx(amos))
    assert await PublicAgentLink.find_one({'agent_id': agent.id}) is None
    await asyncio.sleep(0)
