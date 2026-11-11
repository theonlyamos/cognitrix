"""Connected apps, second round:

- Slack signs in through an OAuth app an admin saved from the setup guide.
- "Send email" and "Create and change events" add built-in tools to Gmail and
  Calendar connections; they ask first and use the connection's own sign-in.
- Agents search their apps' tools instead of being handed every schema, and
  the per-tool Allow / Ask / Off settings apply to what Run Mcp Tool runs.
- Composio: more apps with the person's own API key.
- New mail in Gmail queues a trigger event for a task.
"""
import base64
import ipaddress
import json
import time
from datetime import datetime, timedelta
from email import message_from_bytes
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'apps.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()

    for name in ('COGNITRIX_ADMIN_EMAILS', 'COGNITRIX_DESKTOP', 'COGNITRIX_PUBLIC_URL', 'COMPOSIO_BASE_URL',
                 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GITHUB_CLIENT_ID', 'GITHUB_CLIENT_SECRET',
                 'SLACK_CLIENT_ID', 'SLACK_CLIENT_SECRET', 'COGNITRIX_MAIL_CHECK_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    from cognitrix.mcp import composio, netcheck, registry
    from cognitrix.providers import saved_keys

    monkeypatch.setattr(saved_keys, 'keys_file', lambda: tmp_path / 'provider_keys.json')
    monkeypatch.setattr(saved_keys, '_cache', None)
    monkeypatch.setattr(registry, '_file_servers', lambda: [])

    async def resolve(host, port):
        return [ipaddress.ip_address('93.184.216.34')]

    monkeypatch.setattr(netcheck, '_resolve_host', resolve)
    composio._tools_cache.clear()
    yield
    from cognitrix.mcp import oauth
    oauth._flows.clear()
    oauth._results.clear()
    composio._tools_cache.clear()


class Web:
    """A fake internet for httpx: route (method, url-without-query) to a handler."""

    def __init__(self, monkeypatch):
        self.routes = {}
        self.calls = []

        def client(*args, **kwargs):
            kwargs['transport'] = httpx.MockTransport(self._handle)
            return _REAL_CLIENT(*args, **kwargs)

        monkeypatch.setattr(httpx, 'AsyncClient', client)

    def on(self, method, url, handler):
        self.routes[(method, url)] = handler

    def _handle(self, request: httpx.Request):
        url = str(request.url).split('?')[0]
        self.calls.append((request.method, url, request))
        handler = self.routes.get((request.method, url))
        if handler is None:
            return httpx.Response(404)
        return handler(request)

    def made(self, method, url):
        return [r for m, u, r in self.calls if (m, u) == (method, url)]


def form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _request(base='https://cog.example.com'):
    parsed = urlparse(base)
    return Request({
        'type': 'http', 'method': 'GET', 'path': '/', 'headers': [(b'host', parsed.netloc.encode())],
        'scheme': parsed.scheme, 'server': (parsed.hostname, parsed.port or 443), 'query_string': b'',
        'root_path': '',
    })


async def _people():
    admin = await _user('Ada', 'ada@example.com')
    member = await _user('Bob', 'bob@example.com')
    return admin, member


class FakeClient:
    """Stands in for the MCP client: one server with the tools it was given."""

    def __init__(self, tools=None):
        self.live = {}
        self.tools = tools or {}
        self.tool_annotations = {}
        self.called = []

    def is_connected(self, key):
        return key in self.live

    async def connect_to_server(self, config):
        self.live[config.key] = dict(config.headers or {})
        return True

    async def disconnect_from_server(self, key):
        self.live.pop(key, None)
        return True

    async def list_tools(self, key):
        tools = self.tools.get(key, [])
        self.tool_annotations[key] = {t['name']: t.get('annotations') or {} for t in tools}
        return tools

    async def call_tool(self, key, name, arguments):
        self.called.append((key, name, arguments))
        return f'{name} ran'


def _use(monkeypatch, client):
    from cognitrix.mcp import client as client_module

    async def get():
        return client

    monkeypatch.setattr(client_module, 'get_dynamic_client', get)
    return client


async def _google_row(member, app_id, *, scope, options=()):
    from cognitrix.mcp import catalog, oauth
    from cognitrix.models.mcp_server import MCPServer

    app = catalog.get(app_id)
    await MCPServer(name=app_id.replace('-', '_'), owner_id=str(member.id), url=app.url, auth_type='oauth',
                    catalog_id=app_id, status='connected', account='bob@gmail.com', options=list(options)).save()
    row = await MCPServer.find_one({'catalog_id': app_id})
    await MCPServer.update_one({'id': row.id}, {'oauth': oauth._seal({
        'provider': 'google',
        'tokens': {'access_token': 'ya29.token', 'refresh_token': 'r', 'expires_at': time.time() + 3000,
                   'scope': scope},
    })})
    return await MCPServer.get(row.id)


def _agent(member, servers, **extra):
    return SimpleNamespace(name='helper', owner_id=str(member.id), mcp_servers=list(servers),
                           tool_permissions={}, permission_level='auto', **extra)


# --- setup guide and Slack -----------------------------------------------------------

async def test_setup_guide_is_for_admins_and_fills_in_this_server(db):
    from cognitrix.api.routes import connected_apps

    admin, member = await _people()
    with pytest.raises(HTTPException) as err:
        await connected_apps.app_setup_guide(_request(), _ctx(member))
    assert err.value.status_code == 403

    guide = await connected_apps.app_setup_guide(_request(), _ctx(admin))
    by_id = {p['id']: p for p in guide['providers']}
    assert set(by_id) == {'google', 'github', 'slack'}
    slack = by_id['slack']
    assert slack['source'] == 'none' and slack['can_edit'] is True
    assert slack['redirect_uri'] == 'https://cog.example.com/api/v1/auth/oauth/slack/callback'
    manifest = json.loads(next(s['copy']['text'] for s in slack['steps'] if s.get('copy')))
    assert manifest['oauth_config']['redirect_urls'] == [slack['redirect_uri']]
    assert 'chat:write' in manifest['oauth_config']['scopes']['user']
    copies = [s['copy']['text'] for s in by_id['google']['steps'] if s.get('copy')]
    assert any('gmailmcp.googleapis.com' in text.split() for text in copies)  # the gcloud command
    assert any('https://www.googleapis.com/auth/calendar.events' in text.splitlines() for text in copies)
    # Slack won't take a plain http address; Google only for localhost.
    local = await connected_apps.app_setup_guide(_request('http://127.0.0.1:48210'), _ctx(admin))
    problems = {p['id']: p['redirect_problem'] for p in local['providers']}
    assert problems['google'] is None and 'https' in problems['slack']


async def test_saved_client_is_sealed_and_the_environment_wins(db, monkeypatch, tmp_path):
    from cognitrix.api.routes import connected_apps
    from cognitrix.mcp import catalog

    admin, member = await _people()
    body = connected_apps.ProviderClientBody(client_id='slack-id', client_secret='slack-secret')
    with pytest.raises(HTTPException) as err:
        await connected_apps.save_provider_client('slack', body, _request(), _ctx(member))
    assert err.value.status_code == 403

    saved = await connected_apps.save_provider_client('slack', body, _request(), _ctx(admin))
    assert saved['source'] == 'saved' and saved['client_id'] == 'slack-id'
    assert 'slack-secret' not in json.dumps(saved)
    assert 'slack-secret' not in (tmp_path / 'provider_keys.json').read_text()
    assert catalog.provider_credentials('slack') == ('slack-id', 'slack-secret')
    listing = await connected_apps.list_apps(_request(), _ctx(member))
    assert next(a for a in listing['apps'] if a['id'] == 'slack')['available'] is True

    monkeypatch.setenv('SLACK_CLIENT_ID', 'env-id')
    monkeypatch.setenv('SLACK_CLIENT_SECRET', 'env-secret')
    assert catalog.provider_credentials('slack') == ('env-id', 'env-secret')
    with pytest.raises(HTTPException) as err:
        await connected_apps.save_provider_client('slack', body, _request(), _ctx(admin))
    assert err.value.status_code == 409
    monkeypatch.delenv('SLACK_CLIENT_ID')
    monkeypatch.delenv('SLACK_CLIENT_SECRET')

    removed = await connected_apps.remove_provider_client('slack', _request(), _ctx(admin))
    assert removed['source'] == 'none'
    assert catalog.provider_credentials('slack') is None


async def test_check_tells_a_wrong_client_from_a_right_one(db, monkeypatch):
    from cognitrix.api.routes import connected_apps

    admin, _ = await _people()
    web = Web(monkeypatch)
    answer = {'error': 'invalid_client'}
    web.on('POST', 'https://oauth2.googleapis.com/token', lambda r: httpx.Response(400, json=answer))

    result = await connected_apps.test_provider_client('google', _request(), _ctx(admin))
    assert result['ok'] is False and result['checks'][0]['ok'] is False  # nothing saved yet

    body = connected_apps.ProviderClientBody(client_id='g-id', client_secret='g-secret')
    await connected_apps.save_provider_client('google', body, _request(), _ctx(admin))
    result = await connected_apps.test_provider_client('google', _request(), _ctx(admin))
    assert result['ok'] is False
    assert "doesn't recognise" in result['checks'][-1]['detail']
    sent = form(web.made('POST', 'https://oauth2.googleapis.com/token')[-1])
    assert sent['client_id'] == 'g-id' and sent['client_secret'] == 'g-secret'

    answer['error'] = 'invalid_grant'
    result = await connected_apps.test_provider_client('google', _request(), _ctx(admin))
    assert result['ok'] is True and all(c['ok'] for c in result['checks'])

    answer['error'] = 'redirect_uri_mismatch'
    result = await connected_apps.test_provider_client('google', _request(), _ctx(admin))
    assert result['ok'] is False and 'redirect' in result['checks'][-1]['label']


async def test_connect_slack_end_to_end(db, monkeypatch):
    from cognitrix.api.routes import connected_apps
    from cognitrix.api.routes import oauth as signin_routes
    from cognitrix.mcp import catalog
    from cognitrix.models.mcp_server import MCPServer

    _, member = await _people()
    with pytest.raises(HTTPException):
        await connected_apps.connect_app('slack', _request(), _ctx(member))
    catalog.save_provider_credentials('slack', 'slack-id', 'slack-secret')

    web = Web(monkeypatch)

    def token(request):
        body = form(request)
        assert body['code'] == 'the-code' and body['client_secret'] == 'slack-secret' and body['code_verifier']
        return httpx.Response(200, json={
            'ok': True, 'access_token': 'xoxp-1', 'token_type': 'user',
            'authed_user': {'id': 'U1', 'scope': 'chat:write,search:read.public'}, 'team': {'id': 'T1'},
        })

    web.on('POST', 'https://slack.com/api/oauth.v2.user.access', token)
    web.on('POST', 'https://slack.com/api/auth.test',
           lambda r: httpx.Response(200, json={'ok': True, 'user': 'bob', 'team': 'Acme'}))

    started = await connected_apps.connect_app('slack', _request(), _ctx(member))
    url = urlparse(started['authorize_url'])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f'{url.netloc}{url.path}' == 'slack.com/oauth/v2_user/authorize'
    assert query['client_id'] == 'slack-id'
    assert query['redirect_uri'] == 'https://cog.example.com/api/v1/auth/oauth/slack/callback'
    assert {'chat:write', 'channels:history', 'search:read.public'} <= set(query['scope'].split())

    page = await signin_routes.callback('slack', _request(), code='the-code', state=query['state'])
    assert b'Slack is connected' in page.body
    row = await MCPServer.find_one({'catalog_id': 'slack'})
    assert row.status == 'connected' and row.account == 'bob in Acme'
    assert row.url == 'https://mcp.slack.com/mcp' and 'xoxp' not in row.oauth

    # A refused code comes back as 200 with ok: false.
    web.on('POST', 'https://slack.com/api/oauth.v2.user.access',
           lambda r: httpx.Response(200, json={'ok': False, 'error': 'invalid_code'}))
    again = await connected_apps.connect_app('slack', _request(), _ctx(member))
    state = parse_qs(urlparse(again['authorize_url']).query)['state'][0]
    page = await signin_routes.callback('slack', _request(), code='the-code', state=state)
    assert b'connect Slack' in page.body and b'is connected' not in page.body


# --- options: send email, change events ------------------------------------------------

async def test_send_email_is_off_until_turned_on_and_asks_first(db, monkeypatch):
    from cognitrix.api.routes import connected_apps
    from cognitrix.mcp import apps, registry
    from cognitrix.safety.destructive_ops import RiskLevel, mcp_tool_risk

    _, member = await _people()
    row = await _google_row(member, 'gmail', scope='openid email https://www.googleapis.com/auth/gmail.compose')
    client = _use(monkeypatch, FakeClient({str(row.id): [
        {'name': 'search_threads', 'description': 'Search mail', 'annotations': {'readOnlyHint': True}},
    ]}))
    config = registry.config_from_row(row)
    await registry.prepare(client, config)
    assert [t['name'] for t in await apps.list_tools(client, config)] == ['search_threads']

    conn = await connected_apps.set_app_options('gmail', connected_apps.OptionsBody(options=['send']), _ctx(member))
    assert conn['options'] == ['send'] and conn['pending_options'] == []  # gmail.compose already covers it
    with pytest.raises(HTTPException):
        await connected_apps.set_app_options('gmail', connected_apps.OptionsBody(options=['nope']), _ctx(member))

    config = registry.config_from_row(await _reload(row))
    names = [t['name'] for t in await apps.list_tools(client, config)]
    assert names == ['search_threads', 'send_email', 'send_draft']
    notes = await apps.annotations(client, config, 'send_email')
    assert mcp_tool_risk('send_email', notes).risk_level != RiskLevel.LOW
    assert mcp_tool_risk('search_threads', await apps.annotations(client, config, 'search_threads')).risk_level == RiskLevel.LOW

    web = Web(monkeypatch)
    web.on('POST', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
           lambda r: httpx.Response(200, json={'id': 'm1', 'threadId': 't9'}))
    result = await apps.call_tool(client, config, 'send_email', {
        'to': ['ann@example.com'], 'subject': 'Quote', 'body': 'Hello Ann', 'thread_id': 't9',
        'in_reply_to': '<abc@mail>',
    })
    assert json.loads(result) == {'sent': True, 'message_id': 'm1', 'thread_id': 't9'}
    request = web.made('POST', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')[0]
    assert request.headers['authorization'] == 'Bearer ya29.token'
    sent = json.loads(request.content)
    mail = message_from_bytes(base64.urlsafe_b64decode(sent['raw']))
    assert sent['threadId'] == 't9' and mail['To'] == 'ann@example.com' and mail['Subject'] == 'Quote'
    assert mail['In-Reply-To'] == '<abc@mail>' and 'Hello Ann' in mail.get_payload()
    # The server's own tools still go to the server.
    assert await apps.call_tool(client, config, 'search_threads', {}) == 'search_threads ran'

    missing = await apps.call_tool(client, config, 'send_email', {'subject': 'x', 'body': 'y'})
    assert missing.startswith('Error:') and len(web.made('POST', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')) == 1


async def _reload(row):
    from cognitrix.models.mcp_server import MCPServer

    return await MCPServer.get(row.id)


async def test_calendar_changes_wait_for_a_new_sign_in(db, monkeypatch):
    from cognitrix.api.routes import connected_apps
    from cognitrix.mcp import apps, oauth, registry
    from cognitrix.models.mcp_server import MCPServer

    catalog_scope = 'https://www.googleapis.com/auth/calendar.events'
    _, member = await _people()
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'g-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'g-secret')
    row = await _google_row(member, 'google-calendar', scope='openid email')
    client = _use(monkeypatch, FakeClient({str(row.id): [{'name': 'list_events', 'annotations': {'readOnlyHint': True}}]}))

    conn = await connected_apps.set_app_options(
        'google-calendar', connected_apps.OptionsBody(options=['edit']), _ctx(member))
    assert conn['pending_options'] == ['edit']
    config = registry.config_from_row(await _reload(row))
    await registry.prepare(client, config)
    assert [t['name'] for t in await apps.list_tools(client, config)] == ['list_events']

    # Reconnecting asks Google for the extra scope.
    started = await connected_apps.connect_app('google-calendar', _request(), _ctx(member))
    assert catalog_scope in parse_qs(urlparse(started['authorize_url']).query)['scope'][0].split()

    data = oauth.load(await _reload(row))
    data['tokens']['scope'] = f'openid email {catalog_scope}'
    await MCPServer.update_one({'id': row.id}, {'oauth': oauth._seal(data)})
    listing = await connected_apps.list_apps(_request(), _ctx(member))
    conn = next(a for a in listing['apps'] if a['id'] == 'google-calendar')['connection']
    assert conn['options'] == ['edit'] and conn['pending_options'] == []
    names = [t['name'] for t in await apps.list_tools(client, config)]
    assert names == ['list_events', 'create_event', 'update_event', 'delete_event']

    web = Web(monkeypatch)
    web.on('POST', 'https://www.googleapis.com/calendar/v3/calendars/primary/events',
           lambda r: httpx.Response(200, json={'id': 'e1', 'summary': 'Call', 'htmlLink': 'https://cal/e1'}))
    web.on('DELETE', 'https://www.googleapis.com/calendar/v3/calendars/primary/events/e1',
           lambda r: httpx.Response(204))
    made = await apps.call_tool(client, config, 'create_event', {
        'title': 'Call', 'start': '2030-01-02T15:00:00', 'end': '2030-01-02T15:30:00',
        'time_zone': 'Europe/Berlin', 'attendees': ['ann@example.com'],
    })
    assert json.loads(made)['event_id'] == 'e1'
    request = web.made('POST', 'https://www.googleapis.com/calendar/v3/calendars/primary/events')[0]
    body = json.loads(request.content)
    assert body['start'] == {'dateTime': '2030-01-02T15:00:00', 'timeZone': 'Europe/Berlin'}
    assert body['attendees'] == [{'email': 'ann@example.com'}]
    assert request.url.params['sendUpdates'] == 'none'  # nobody is emailed unless asked
    assert json.loads(await apps.call_tool(client, config, 'delete_event', {'event_id': 'e1'}))['deleted'] is True


# --- finding and running app tools -------------------------------------------------------

def _many_tools():
    tools = [{'name': f'list_thing_{i}', 'description': f'Lists thing number {i}', 'input_schema': {
        'type': 'object', 'properties': {'q': {'type': 'string'}}}} for i in range(30)]
    tools.append({'name': 'send_message', 'description': 'Send a message to a channel or person',
                  'input_schema': {'type': 'object', 'properties': {'channel': {'type': 'string'}}}})
    tools.append({'name': 'search_channels', 'description': 'Find channels by name',
                  'input_schema': {'type': 'object', 'properties': {}}, 'annotations': {'readOnlyHint': True}})
    return tools


async def _tracker(member, monkeypatch, tools):
    from cognitrix.models.mcp_server import MCPServer

    await MCPServer(name='chat', owner_id=str(member.id), url='https://mcp.example.com/mcp',
                    auth_type='none', enabled=True).save()
    row = await MCPServer.find_one({'name': 'chat'})
    client = _use(monkeypatch, FakeClient({str(row.id): tools}))
    return row, client


async def test_agents_search_their_tools_instead_of_reading_every_schema(db, monkeypatch):
    from cognitrix.tools.mcp import list_mcp_tools

    _, member = await _people()
    row, _client = await _tracker(member, monkeypatch, _many_tools())
    agent = _agent(member, [str(row.id)])

    index = json.loads((await list_mcp_tools.run(parent=agent)).content)
    assert len(index['servers']['chat']) == 32
    assert 'input_schema' not in index['servers']['chat'][0] and 'query' in index['note']

    found = json.loads((await list_mcp_tools.run(query='send a message to a channel', parent=agent)).content)
    first = found['matches']['chat'][0]
    assert first['name'] == 'send_message' and 'channel' in first['input_schema']['properties']
    assert sum(len(v) for v in found['matches'].values()) <= 8

    exact = json.loads((await list_mcp_tools.run(query='search_channels, list_thing_3', parent=agent)).content)
    assert [t['name'] for t in exact['matches']['chat']] == ['list_thing_3', 'search_channels'] or \
        {t['name'] for t in exact['matches']['chat']} == {'list_thing_3', 'search_channels'}
    assert 'No tool matches' in (await list_mcp_tools.run(query='zebra', parent=agent)).content

    # A short list still comes back whole, as before.
    row2, _ = await _tracker_named(member, monkeypatch, 'small', _many_tools()[-2:])
    small = json.loads((await list_mcp_tools.run(server='small', parent=_agent(member, [str(row2.id)]))).content)
    assert 'input_schema' in small['small'][0]


async def _tracker_named(member, monkeypatch, name, tools):
    from cognitrix.models.mcp_server import MCPServer

    await MCPServer(name=name, owner_id=str(member.id), url='https://mcp2.example.com/mcp',
                    auth_type='none', enabled=True).save()
    row = await MCPServer.find_one({'name': name})
    client = _use(monkeypatch, FakeClient({str(row.id): tools}))
    return row, client


async def test_per_tool_settings_apply_to_what_run_mcp_tool_runs(db, monkeypatch):
    from cognitrix.agents.base import AgentManager
    from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
    from cognitrix.safety.permissions import ToolDecision, apply_target_level
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.mcp import list_mcp_tools, run_mcp_tool

    _, member = await _people()
    row, client = await _tracker(member, monkeypatch, _many_tools())
    agent = _agent(member, [str(row.id)])
    agent.tool_permissions = {'chat_send_message': 'deny', 'chat_search_channels': 'ask', 'chat_list_thing_1': 'allow'}

    manager = AgentManager.__new__(AgentManager)
    manager.agent = agent
    run_tool = ToolManager.get_by_name('Run Mcp Tool')
    level = lambda tool: manager._mcp_target_level(run_tool, {'server': 'chat', 'tool_name': tool})  # noqa: E731
    assert await level('send_message') == ('deny', 'chat_send_message')
    assert await level('search_channels') == ('ask', 'chat_search_channels')
    assert await level('list_thing_2') == ('auto', 'chat_list_thing_2')
    assert await manager._mcp_target_level(ToolManager.get_by_name('List Mcp Tools'), {}) == ('auto', '')

    low = RiskAssessment(RiskLevel.LOW, [], 'reads')
    runs = ToolDecision('run', low, 'policy')
    asks = ToolDecision('ask', low, 'policy')
    assert apply_target_level(runs, 'deny', 'x', low).action == 'block'
    assert apply_target_level(runs, 'ask', 'x', low).action == 'ask'
    assert apply_target_level(asks, 'allow', 'x', low).action == 'run'
    assert apply_target_level(asks, 'auto', 'x', low) is asks

    # Switched-off tools aren't offered, and can't be run by name either.
    listed = (await list_mcp_tools.run(query='send_message', parent=agent)).content
    assert 'No tool matches' in listed
    refused = (await run_mcp_tool.run(server='chat', tool_name='send_message', arguments={}, parent=agent)).content
    assert 'switched off' in refused and client.called == []
    ok = (await run_mcp_tool.run(server='chat', tool_name='list_thing_1', arguments={'q': 'a'}, parent=agent)).content
    assert ok == 'list_thing_1 ran'


# --- Composio ---------------------------------------------------------------------------------

BASE = 'https://backend.composio.dev'


def _composio_web(monkeypatch):
    web = Web(monkeypatch)
    state = {'status': 'INITIATED', 'deleted': []}

    def keyed(handler):
        def wrapped(request):
            if request.headers.get('x-api-key') != 'ck_live_good':
                return httpx.Response(401, json={'error': {'message': 'bad key'}})
            return handler(request)
        return wrapped

    web.on('GET', f'{BASE}/api/v3.1/toolkits', keyed(lambda r: httpx.Response(200, json={'items': [
        {'slug': 'hubspot', 'name': 'HubSpot', 'composio_managed_auth_schemes': ['OAUTH2'],
         'meta': {'description': 'CRM', 'logo': 'https://logos.composio.dev/api/hubspot',
                  'categories': [{'id': 'crm', 'name': 'CRM'}], 'tools_count': 3}},
        {'slug': 'acme', 'name': 'Acme', 'composio_managed_auth_schemes': [], 'meta': {'description': 'Needs your own app'}},
        {'slug': 'Bad Slug!', 'name': 'x'},
    ], 'next_cursor': None})))
    web.on('POST', f'{BASE}/api/v3.1/tool_router/session',
           keyed(lambda r: httpx.Response(201, json={'session_id': 'sess_1'})))
    web.on('POST', f'{BASE}/api/v3.1/tool_router/session/sess_1/link', keyed(lambda r: httpx.Response(201, json={
        'redirect_url': 'https://connect.composio.dev/link/abc', 'connected_account_id': 'ca_1', 'link_token': 't'})))
    web.on('GET', f'{BASE}/api/v3.1/connected_accounts/ca_1', keyed(lambda r: httpx.Response(200, json={
        'id': 'ca_1', 'status': state['status'], 'data': {'email': 'bob@acme.com'}})))

    def delete(request):
        state['deleted'].append('ca_1')
        return httpx.Response(200, json={'success': True})

    web.on('DELETE', f'{BASE}/api/v3.1/connected_accounts/ca_1', keyed(delete))
    web.on('GET', f'{BASE}/api/v3.1/tools', keyed(lambda r: httpx.Response(200, json={'items': [
        {'slug': 'HUBSPOT_LIST_CONTACTS', 'name': 'List contacts', 'description': 'List CRM contacts',
         'toolkit': {'slug': 'hubspot'}, 'tags': ['readOnlyHint'],
         'input_parameters': {'type': 'object', 'properties': {'limit': {'type': 'integer'}}}},
        {'slug': 'HUBSPOT_DELETE_CONTACT', 'name': 'Delete contact', 'description': 'Delete a contact',
         'toolkit': {'slug': 'hubspot'}, 'tags': ['destructiveHint'], 'input_parameters': {}},
        {'slug': 'GMAIL_SEND_EMAIL', 'name': 'Wrong toolkit', 'toolkit': {'slug': 'gmail'}, 'tags': []},
    ], 'next_cursor': None})))

    def execute(request):
        body = json.loads(request.content)
        assert body['connected_account_id'] == 'ca_1' and body['user_id'].startswith('cognitrix-')
        return httpx.Response(200, json={'successful': True, 'error': None, 'data': {'contacts': [body['arguments']]}})

    web.on('POST', f'{BASE}/api/v3.1/tools/execute/HUBSPOT_LIST_CONTACTS', keyed(execute))
    web.on('POST', f'{BASE}/api/v3.1/tools/execute/HUBSPOT_DELETE_CONTACT', keyed(
        lambda r: httpx.Response(200, json={'successful': False, 'error': 'Contact not found', 'data': {}})))
    return web, state


async def test_composio_with_your_own_key(db, monkeypatch):
    from cognitrix.agents.base import AgentManager
    from cognitrix.api.routes import connected_apps
    from cognitrix.api.routes import mcp as mcp_routes
    from cognitrix.mcp import registry
    from cognitrix.models.mcp_server import MCPServer
    from cognitrix.tools.base import ToolManager
    from cognitrix.tools.mcp import list_mcp_tools, run_mcp_tool

    _, member = await _people()
    me = str(member.id)
    web, state = _composio_web(monkeypatch)
    _use(monkeypatch, FakeClient())

    with pytest.raises(HTTPException) as err:
        await connected_apps.search_composio_apps(ctx=_ctx(member))
    assert err.value.status_code == 400  # no key yet
    with pytest.raises(HTTPException) as err:
        await connected_apps.save_composio_key(connected_apps.ComposioKeyBody(api_key='ck_live_bad'), _ctx(member))
    assert err.value.status_code == 400 and "didn't accept" in err.value.detail

    saved = await connected_apps.save_composio_key(connected_apps.ComposioKeyBody(api_key='ck_live_good'), _ctx(member))
    assert saved == {'has_key': True, 'hint': '…good'}
    key_row = await MCPServer.find_one({'catalog_id': 'composio'})
    assert 'ck_live_good' not in key_row.oauth
    # The key's row is not a server anyone can pick or see.
    assert await registry.visible_servers(me) == []
    assert (await mcp_routes.list_servers(_ctx(member)))['servers'] == []

    found = await connected_apps.search_composio_apps(query='crm', ctx=_ctx(member))
    assert [(t['slug'], t['connectable']) for t in found['toolkits']] == [('hubspot', True), ('acme', False)]

    started = await connected_apps.connect_composio_app(
        'hubspot', _request(), connected_apps.ComposioConnectBody(name='HubSpot'), _ctx(member))
    assert started['authorize_url'] == 'https://connect.composio.dev/link/abc'
    link = json.loads(web.made('POST', f'{BASE}/api/v3.1/tool_router/session/sess_1/link')[0].content)
    assert link['toolkit'] == 'hubspot'
    assert link['callback_url'] == f"https://cog.example.com/api/v1/mcp/composio/callback/{started['flow_id']}"

    assert (await connected_apps.sign_in_status(started['flow_id'], _ctx(member)))['status'] == 'pending'
    row = await MCPServer.find_one({'catalog_id': 'composio:hubspot'})
    agent = _agent(member, [str(row.id)])
    assert (await list_mcp_tools.run(parent=agent)).content.startswith('App sign-in needed: HubSpot.')

    state['status'] = 'ACTIVE'
    page = await connected_apps.composio_callback(started['flow_id'])
    assert b'HubSpot is connected' in page.body
    assert (await connected_apps.sign_in_status(started['flow_id'], _ctx(member)))['status'] == 'done'
    row = await MCPServer.get(row.id)
    assert row.status == 'connected' and row.account == 'bob@acme.com' and row.name == 'hubspot'
    listing = await connected_apps.list_apps(_request(), _ctx(member))
    assert listing['composio']['has_key'] is True
    assert [c['slug'] for c in listing['composio']['connections']] == ['hubspot']
    assert all(a['id'] != 'composio' for a in listing['apps']) and listing['custom'] == []

    # Agents use it like any other connection.
    tools = json.loads((await list_mcp_tools.run(parent=agent)).content)
    assert [t['name'] for t in tools['hubspot']] == ['HUBSPOT_LIST_CONTACTS', 'HUBSPOT_DELETE_CONTACT']
    assert tools['hubspot'][0]['read_only'] is True
    result = (await run_mcp_tool.run(server='hubspot', tool_name='HUBSPOT_LIST_CONTACTS',
                                     arguments={'limit': 2}, parent=agent)).content
    assert json.loads(result) == {'contacts': [{'limit': 2}]}
    failed = (await run_mcp_tool.run(server='hubspot', tool_name='HUBSPOT_DELETE_CONTACT',
                                     arguments={}, parent=agent)).content
    assert 'Contact not found' in failed
    # Only this app's own tools, whatever name the model sends.
    other = (await run_mcp_tool.run(server='hubspot', tool_name='GMAIL_SEND_EMAIL', arguments={}, parent=agent)).content
    assert 'has no tool named' in other

    manager = AgentManager.__new__(AgentManager)
    manager.agent = agent
    run_tool = ToolManager.get_by_name('Run Mcp Tool')
    assert await manager._mcp_write_risk(run_tool, {'server': 'hubspot', 'tool_name': 'HUBSPOT_LIST_CONTACTS'}) is None
    assert await manager._mcp_write_risk(run_tool, {'server': 'hubspot', 'tool_name': 'HUBSPOT_DELETE_CONTACT'}) is not None

    shown = await mcp_routes.server_tools(str(row.id), _ctx(member))
    assert {t['name']: t['default'] for t in shown['tools']} == {
        'HUBSPOT_LIST_CONTACTS': 'allow', 'HUBSPOT_DELETE_CONTACT': 'ask'}

    # Nobody else can touch it.
    admin = await _user('Eve', 'eve@example.com')
    with pytest.raises(HTTPException):
        await connected_apps.disconnect_composio_app(str(row.id), _ctx(admin))

    await connected_apps.disconnect_composio_app(str(row.id), _ctx(member))
    assert state['deleted'] == ['ca_1'] and await MCPServer.get(row.id) is None
    await connected_apps.remove_composio_key(_ctx(member))
    assert await MCPServer.find_one({'catalog_id': 'composio'}) is None


# --- new mail starts a task ------------------------------------------------------------------

NOW = datetime(2030, 6, 1, 12, 0, 0)
GMAIL = 'https://gmail.googleapis.com/gmail/v1/users/me'


def _gmail_web(monkeypatch):
    web = Web(monkeypatch)
    box = {'history_id': '100', 'history': [], 'messages': {}, 'history_status': 200}
    web.on('GET', f'{GMAIL}/profile', lambda r: httpx.Response(200, json={'historyId': box['history_id']}))

    def history(request):
        if box['history_status'] != 200:
            return httpx.Response(box['history_status'], json={'error': {'message': 'gone'}})
        assert request.url.params['labelId'] == 'INBOX'
        return httpx.Response(200, json={'history': box['history'], 'historyId': box['history_id']})

    web.on('GET', f'{GMAIL}/history', history)
    return web, box


def _arrive(web, box, message_id, sender, subject, labels=('INBOX', 'UNREAD')):
    box['history'].append({'messagesAdded': [{'message': {'id': message_id, 'labelIds': list(labels)}}]})
    box['history_id'] = str(int(box['history_id']) + 1)
    web.on('GET', f'{GMAIL}/messages/{message_id}', lambda r: httpx.Response(200, json={
        'id': message_id, 'threadId': f't-{message_id}', 'snippet': f'First words of {subject}',
        'payload': {'headers': [{'name': 'From', 'value': sender}, {'name': 'Subject', 'value': subject},
                                {'name': 'Date', 'value': 'Sat, 1 Jun 2030 12:00:00 +0000'}]}}))


async def test_new_mail_queues_one_event_for_the_task(db, monkeypatch):
    from cognitrix.api.routes import automations
    from cognitrix.tasks.automation import TaskTrigger, TriggerEvent, automation_brief, dispatch_trigger_events
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.mail_triggers import poll_mail_triggers

    _, member = await _people()
    me = str(member.id)
    task = Task(title='inbox', description='answer mail', owner_id=me, assigned_agents=['a1'])
    await task.save()
    ctx = _ctx(member)

    with pytest.raises(HTTPException) as err:
        await automations.create_trigger(task.id, automations.TriggerCreate(kind='gmail'), ctx)
    assert err.value.status_code == 422 and 'Connect Gmail' in err.value.detail

    await _google_row(member, 'gmail', scope='https://www.googleapis.com/auth/gmail.readonly')
    made = await automations.create_trigger(task.id, automations.TriggerCreate(kind='gmail', match='invoice'), ctx)
    assert made['kind'] == 'gmail' and made['name'] == 'New mail in bob@gmail.com' and 'token' not in made
    with pytest.raises(HTTPException):
        await automations.create_trigger(task.id, automations.TriggerCreate(kind='gmail'), ctx)
    with pytest.raises(HTTPException):
        await automations.rotate_trigger(task.id, made['id'], ctx)

    web, box = _gmail_web(monkeypatch)
    # First look only notes where the inbox stands: old mail starts nothing.
    _arrive(web, box, 'old', 'old@example.com', 'Invoice from last week')
    assert await poll_mail_triggers(NOW) == 0
    trigger = await TaskTrigger.get(made['id'])
    assert trigger.cursor == '101' and await TriggerEvent.find({'task_id': task.id}) == []
    box['history'].clear()

    # Not due yet.
    _arrive(web, box, 'm1', 'Ann <ann@example.com>', 'Invoice 42')
    _arrive(web, box, 'm2', 'news@example.com', 'Weekly digest')
    _arrive(web, box, 'm3', 'me@gmail.com', 'Invoice draft', labels=('DRAFT',))
    assert await poll_mail_triggers(NOW + timedelta(seconds=10)) == 0
    assert await poll_mail_triggers(NOW + timedelta(minutes=3)) == 1

    events = await TriggerEvent.find({'task_id': task.id})
    payload = json.loads(events[0].payload)
    assert payload['account'] == 'bob@gmail.com'
    assert [m['id'] for m in payload['new_messages']] == ['m1']  # the digest doesn't match, the draft isn't inbox mail
    assert payload['new_messages'][0]['subject'] == 'Invoice 42'
    assert (await TaskTrigger.get(made['id'])).cursor == '104'

    # Nothing new: no second event.
    box['history'].clear()
    assert await poll_mail_triggers(NOW + timedelta(minutes=6)) == 0
    assert len(await TriggerEvent.find({'task_id': task.id})) == 1

    # The event starts a run that is told it is mail, as untrusted data.
    import cognitrix.api.routes.tasks as task_routes
    started = []

    async def fake_enqueue(task, resume=False, **kwargs):
        started.append(kwargs)
        return SimpleNamespace(id='run-1')

    monkeypatch.setattr(task_routes, '_enqueue_task_start', fake_enqueue)
    assert await dispatch_trigger_events(NOW + timedelta(minutes=6)) == 1
    assert started[0]['trigger_kind'] == 'email'
    run = SimpleNamespace(trigger_kind='email', trigger_note='New mail in bob@gmail.com', trigger_event_id=events[0].id)
    brief = await automation_brief(task, run)
    assert 'new mail arrived' in brief and 'Invoice 42' in brief and 'UNTRUSTED DATA' in brief

    # A webhook URL can't be guessed for a mail trigger.
    from fastapi import BackgroundTasks
    with pytest.raises(HTTPException) as err:
        await automations.receive_hook(made['id'], 'anything', _request(), BackgroundTasks())
    assert err.value.status_code == 404


async def test_mail_check_problems_are_shown_to_the_owner(db, monkeypatch):
    from cognitrix.api.routes import automations
    from cognitrix.models.mcp_server import MCPServer
    from cognitrix.tasks.automation import TaskTrigger
    from cognitrix.tasks.base import Task
    from cognitrix.tasks.mail_triggers import poll_mail_triggers

    _, member = await _people()
    task = Task(title='inbox', description='answer mail', owner_id=str(member.id), assigned_agents=['a1'])
    await task.save()
    row = await _google_row(member, 'gmail', scope='https://www.googleapis.com/auth/gmail.readonly')
    made = await automations.create_trigger(task.id, automations.TriggerCreate(kind='gmail'), _ctx(member))
    web, box = _gmail_web(monkeypatch)
    assert await poll_mail_triggers(NOW) == 0

    # Gmail forgot our place: start again from now instead of failing.
    box['history_status'] = 404
    box['history_id'] = '500'
    assert await poll_mail_triggers(NOW + timedelta(minutes=3)) == 0
    assert (await TaskTrigger.get(made['id'])).cursor == '500'
    box['history_status'] = 200

    await MCPServer.update_one({'id': row.id}, {'status': 'needs_reauth'})
    assert await poll_mail_triggers(NOW + timedelta(minutes=6)) == 0
    listed = await automations.list_triggers(task.id, _ctx(member))
    assert 'reconnecting' in listed[0]['error']

    # Turning it back on clears the error and starts from the current inbox.
    await MCPServer.update_one({'id': row.id}, {'status': 'connected'})
    await automations.update_trigger(task.id, made['id'], automations.TriggerPatch(enabled=True), _ctx(member))
    assert await poll_mail_triggers(NOW + timedelta(minutes=9)) == 0
    after = await TaskTrigger.get(made['id'])
    assert after.error is None and after.cursor == '500'
