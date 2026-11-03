"""Connected apps: signing in to hosted MCP servers (GitHub, Gmail, ...).

- Catalog apps that use Google / GitHub sign in through the server's existing
  OAuth app and its sign-in callback, with PKCE and offline access.
- Other servers are discovered (protected-resource + auth-server metadata)
  and registered with dynamic client registration.
- Tokens are stored encrypted, refreshed before they expire, and a refused
  refresh marks the app "needs_reauth" with one Inbox alert.
- Everything is per person: nobody else sees or uses your connection.
"""
import ipaddress
import json
import time
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

    for name in ('COGNITRIX_ADMIN_EMAILS', 'COGNITRIX_DESKTOP', 'COGNITRIX_PUBLIC_URL',
                 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GITHUB_CLIENT_ID', 'GITHUB_CLIENT_SECRET'):
        monkeypatch.delenv(name, raising=False)
    from cognitrix.mcp import netcheck, registry
    monkeypatch.setattr(registry, '_file_servers', lambda: [])

    async def resolve(host, port):
        if host.startswith('internal') or host == 'localhost':
            return [ipaddress.ip_address('10.0.0.5')]
        return [ipaddress.ip_address('93.184.216.34')]

    monkeypatch.setattr(netcheck, '_resolve_host', resolve)
    from cognitrix.api.routes import mcp as mcp_routes
    monkeypatch.setattr(mcp_routes, '_resolve_host', resolve)
    yield
    from cognitrix.mcp import oauth
    oauth._flows.clear()
    oauth._results.clear()


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


def form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


async def _user(name, email):
    from cognitrix.models import User

    await User(name=name, email=email, password='x' * 20).save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _request(base='http://127.0.0.1:48210'):
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


def _google_web(monkeypatch, *, refresh_ok=True):
    web = Web(monkeypatch)
    issued = {'n': 0}

    def token(request):
        body = form(request)
        if body['grant_type'] == 'authorization_code':
            assert body['code'] == 'the-code'
            assert body['code_verifier']
            assert body['client_secret'] == 'g-secret'
            return httpx.Response(200, json={'access_token': 'ya29.first', 'refresh_token': '1//refresh',
                                             'expires_in': 3599, 'token_type': 'Bearer'})
        assert body['grant_type'] == 'refresh_token'
        if not refresh_ok:
            return httpx.Response(400, json={'error': 'invalid_grant'})
        issued['n'] += 1
        return httpx.Response(200, json={'access_token': f'ya29.renewed{issued["n"]}', 'expires_in': 3599})

    web.on('POST', 'https://oauth2.googleapis.com/token', token)
    web.on('GET', 'https://openidconnect.googleapis.com/v1/userinfo',
           lambda r: httpx.Response(200, json={'email': 'bob@gmail.com'}))
    return web


async def test_google_apps_need_the_servers_google_client(db, monkeypatch):
    from cognitrix.api.routes import connected_apps

    admin, member = await _people()
    listing = await connected_apps.list_apps(_request(), _ctx(member))
    gmail = next(a for a in listing['apps'] if a['id'] == 'gmail')
    assert gmail['available'] is False
    assert 'GOOGLE_CLIENT_ID' in gmail['setup']
    assert listing['callback_urls'] == {}

    with pytest.raises(HTTPException) as err:
        await connected_apps.connect_app('gmail', _request(), _ctx(member))
    assert err.value.status_code == 400

    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'g-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'g-secret')
    listing = await connected_apps.list_apps(_request(), _ctx(admin))
    assert next(a for a in listing['apps'] if a['id'] == 'gmail')['available'] is True
    assert listing['callback_urls']['google'] == 'http://127.0.0.1:48210/api/v1/auth/oauth/google/callback'


async def test_connect_gmail_end_to_end(db, monkeypatch):
    from cognitrix.api.routes import connected_apps
    from cognitrix.api.routes import oauth as signin_routes
    from cognitrix.models.mcp_server import MCPServer

    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'g-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'g-secret')
    _, member = await _people()
    _google_web(monkeypatch)

    started = await connected_apps.connect_app('gmail', _request(), _ctx(member))
    url = urlparse(started['authorize_url'])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == 'accounts.google.com'
    assert query['client_id'] == 'g-id'
    assert query['redirect_uri'] == 'http://127.0.0.1:48210/api/v1/auth/oauth/google/callback'
    assert query['code_challenge_method'] == 'S256' and query['code_challenge']
    assert 'https://www.googleapis.com/auth/gmail.readonly' in query['scope'].split()
    assert query['access_type'] == 'offline'

    status = await connected_apps.sign_in_status(started['flow_id'], _ctx(member))
    assert status['status'] == 'pending'

    # Google sends the browser back to the regular sign-in callback.
    page = await signin_routes.callback('google', _request(), code='the-code', state=query['state'])
    assert b'Gmail is connected' in page.body

    status = await connected_apps.sign_in_status(started['flow_id'], _ctx(member))
    assert status['status'] == 'done'
    row = await MCPServer.get(status['server_id'])
    assert row.status == 'connected' and row.account == 'bob@gmail.com'
    assert row.catalog_id == 'gmail' and row.auth_type == 'oauth'
    assert row.oauth.startswith('enc:v1:') and 'ya29' not in row.oauth

    listing = await connected_apps.list_apps(_request(), _ctx(member))
    conn = next(a for a in listing['apps'] if a['id'] == 'gmail')['connection']
    assert conn['status'] == 'connected' and conn['account'] == 'bob@gmail.com'

    # Connecting again reuses the same row, so agents keep their pick.
    again = await connected_apps.connect_app('gmail', _request(), _ctx(member))
    assert len(await MCPServer.find({'catalog_id': 'gmail'})) == 1
    assert 'login_hint=bob%40gmail.com' in again['authorize_url']


async def test_someone_elses_flow_and_server_are_hidden(db, monkeypatch):
    from cognitrix.api.routes import connected_apps

    monkeypatch.setenv('GITHUB_CLIENT_ID', 'gh-id')
    monkeypatch.setenv('GITHUB_CLIENT_SECRET', 'gh-secret')
    admin, member = await _people()
    started = await connected_apps.connect_app('github', _request(), _ctx(member))

    with pytest.raises(HTTPException):
        await connected_apps.sign_in_status(started['flow_id'], _ctx(admin))
    listing = await connected_apps.list_apps(_request(), _ctx(admin))
    assert next(a for a in listing['apps'] if a['id'] == 'github')['connection'] is None
    status = await connected_apps.sign_in_status(started['flow_id'], _ctx(member))
    with pytest.raises(HTTPException) as err:
        await connected_apps.sign_in_server(status['server_id'], _request(), None, _ctx(admin))
    assert err.value.status_code == 404


async def test_expired_token_refreshes_then_reconnect_alert(db, monkeypatch):
    from cognitrix.api.routes import connected_apps
    from cognitrix.api.routes import oauth as signin_routes
    from cognitrix.mcp import oauth, registry
    from cognitrix.models.mcp_server import MCPServer
    from cognitrix.tasks.automation import AgentNotification

    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'g-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'g-secret')
    _, member = await _people()
    _google_web(monkeypatch)
    started = await connected_apps.connect_app('google-calendar', _request(), _ctx(member))
    state = parse_qs(urlparse(started['authorize_url']).query)['state'][0]
    await signin_routes.callback('google', _request(), code='the-code', state=state)
    server_id = oauth.flow_status(started['flow_id'], str(member.id))['server_id']

    async def expire():
        row = await MCPServer.get(server_id)
        data = oauth.load(row)
        data['tokens']['expires_at'] = time.time() - 5
        await MCPServer.update_one({'id': server_id}, {'oauth': oauth._seal(data)})
        return await MCPServer.get(server_id)

    row = await expire()
    assert await oauth.access_token(row) == 'ya29.renewed1'
    # A fresh token is used as is.
    assert await oauth.access_token(await MCPServer.get(server_id)) == 'ya29.renewed1'

    # Now Google refuses the refresh token (revoked).
    _google_web(monkeypatch, refresh_ok=False)
    row = await expire()
    with pytest.raises(oauth.ReconnectNeeded):
        await oauth.access_token(row)
    row = await MCPServer.get(server_id)
    assert row.status == 'needs_reauth'
    alerts = await AgentNotification.find({'user_id': str(member.id)})
    assert len(alerts) == 1
    assert alerts[0].title == 'Reconnect Google Calendar' and alerts[0].link == '/settings/apps'

    # Agents get a message that asks the person to reconnect, and no second alert.
    from cognitrix.mcp.client import get_dynamic_client
    problem = await registry.prepare(await get_dynamic_client(), registry.config_from_row(row))
    assert problem.startswith(registry.SIGN_IN_NEEDED) and 'Google Calendar' in problem
    await oauth.mark_needs_reauth(row)
    assert len(await AgentNotification.find({'user_id': str(member.id)})) == 1


def _mcp_server_web(monkeypatch, *, token_endpoint='https://auth.example.com/token'):
    web = Web(monkeypatch)
    web.on('POST', 'https://mcp.example.com/mcp', lambda r: httpx.Response(
        401, headers={'WWW-Authenticate': 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'}))
    web.on('GET', 'https://mcp.example.com/.well-known/oauth-protected-resource/mcp', lambda r: httpx.Response(200, json={
        'resource': 'https://mcp.example.com/mcp',
        'authorization_servers': ['https://auth.example.com'],
        'scopes_supported': ['read', 'write'],
    }))
    web.on('GET', 'https://auth.example.com/.well-known/oauth-authorization-server', lambda r: httpx.Response(200, json={
        'issuer': 'https://auth.example.com',
        'authorization_endpoint': 'https://auth.example.com/authorize',
        'token_endpoint': token_endpoint,
        'registration_endpoint': 'https://auth.example.com/register',
        'response_types_supported': ['code'],
    }))

    def register(request):
        body = json.loads(request.content)
        assert body['redirect_uris'] == ['http://127.0.0.1:48210/api/v1/mcp/oauth/callback']
        assert body['token_endpoint_auth_method'] == 'none'
        return httpx.Response(201, json={'client_id': 'dyn-123', 'token_endpoint_auth_method': 'none'})

    web.on('POST', 'https://auth.example.com/register', register)

    def token(request):
        body = form(request)
        assert body['client_id'] == 'dyn-123' and body['resource'] == 'https://mcp.example.com/mcp'
        return httpx.Response(200, json={'access_token': 'at-1', 'refresh_token': 'rt-1', 'expires_in': 600})

    web.on('POST', 'https://auth.example.com/token', token)
    return web


async def test_add_by_url_discovers_and_registers(db, monkeypatch):
    from cognitrix.api.routes import connected_apps
    from cognitrix.models.mcp_server import MCPServer

    _, member = await _people()
    _mcp_server_web(monkeypatch)
    added = await connected_apps.add_custom_app(
        connected_apps.CustomAppBody(name='tracker', url='https://mcp.example.com/mcp'), _request(), _ctx(member))
    assert added['connection']['auth_type'] == 'oauth'
    query = {k: v[0] for k, v in parse_qs(urlparse(added['flow']['authorize_url']).query).items()}
    assert added['flow']['authorize_url'].startswith('https://auth.example.com/authorize?')
    assert query['client_id'] == 'dyn-123'
    assert query['resource'] == 'https://mcp.example.com/mcp'
    assert query['scope'] == 'read write'

    page = await connected_apps.sign_in_callback(code='c', state=query['state'])
    assert b'is connected' in page.body
    row = await MCPServer.find_one({'name': 'tracker'})
    assert row.status == 'connected' and row.owner_id == str(member.id)

    listing = await connected_apps.list_apps(_request(), _ctx(member))
    assert [c['name'] for c in listing['custom']] == ['tracker']


async def test_sign_in_metadata_cant_point_at_private_addresses(db, monkeypatch):
    from cognitrix.api.routes import connected_apps

    _, member = await _people()
    _mcp_server_web(monkeypatch, token_endpoint='https://internal.corp/token')
    added = await connected_apps.add_custom_app(
        connected_apps.CustomAppBody(name='sneaky', url='https://mcp.example.com/mcp'), _request(), _ctx(member))
    assert added['flow'] is None
    assert 'private' in added['error']


async def test_github_with_a_personal_access_token(db, monkeypatch):
    from cognitrix.api.routes import connected_apps
    from cognitrix.mcp import registry
    from cognitrix.models.mcp_server import MCPServer

    _, member = await _people()
    web = Web(monkeypatch)
    web.on('GET', 'https://api.github.com/user', lambda r: httpx.Response(
        200 if r.headers['Authorization'] == 'Bearer ghp_good' else 401, json={'login': 'bob'}))

    with pytest.raises(HTTPException):
        await connected_apps.connect_with_token('github', connected_apps.TokenBody(token='ghp_bad'), _ctx(member))
    conn = await connected_apps.connect_with_token('github', connected_apps.TokenBody(token='ghp_good'), _ctx(member))
    assert conn['status'] == 'connected' and conn['account'] == 'bob' and conn['auth_type'] == 'header'
    row = await MCPServer.get(conn['server_id'])
    assert 'ghp_good' not in row.headers
    assert registry.config_from_row(row).headers == {'Authorization': 'Bearer ghp_good'}

    await connected_apps.disconnect_app('github', _ctx(member))
    row = await MCPServer.get(conn['server_id'])
    assert not row.headers and not row.status
    listing = await connected_apps.list_apps(_request(), _ctx(member))
    assert next(a for a in listing['apps'] if a['id'] == 'github')['connection']['status'] == 'signed_out'

    with pytest.raises(HTTPException):
        await connected_apps.connect_with_token('gmail', connected_apps.TokenBody(token='x'), _ctx(member))


def test_tool_defaults_follow_annotations():
    from cognitrix.api.routes.mcp import _tool_row
    from cognitrix.mcp.server_manager import MCPServerConfig, MCPTransportType

    config = MCPServerConfig(name='gmail', transport=MCPTransportType.HTTP)
    read = _tool_row(config, {'name': 'search_threads', 'annotations': {'readOnlyHint': True}})
    assert read['tool'] == 'gmail_search_threads' and read['default'] == 'allow'
    send = _tool_row(config, {'name': 'send_message', 'annotations': {}})
    assert send['default'] == 'ask'
    wipe = _tool_row(config, {'name': 'update_label', 'annotations': {'destructiveHint': True}})
    assert wipe['default'] == 'ask'


def test_sign_in_tool_explains_and_never_asks():
    from cognitrix.mcp.tools import _sign_in_tool

    tool = _sign_in_tool('gmail', 'App sign-in needed: Gmail.')
    assert tool.name == 'gmail_sign_in_needed'
    assert tool.approval_mode == 'assigned_only'


class FakeClient:
    def __init__(self):
        self.live = {}

    def is_connected(self, key):
        return key in self.live

    async def connect_to_server(self, config):
        self.live[config.key] = dict(config.headers or {})
        return True

    async def disconnect_from_server(self, key):
        self.live.pop(key, None)
        return True


async def test_oauth_connection_sends_the_token_and_renews_it(db, monkeypatch):
    from cognitrix.mcp import oauth, registry
    from cognitrix.models.mcp_server import MCPServer

    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'g-id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'g-secret')
    _, member = await _people()
    _google_web(monkeypatch)
    await MCPServer(name='gmail', owner_id=str(member.id), url='https://gmailmcp.googleapis.com/mcp/v1',
                    auth_type='oauth', catalog_id='gmail', status='connected').save()
    row = await MCPServer.find_one({'name': 'gmail'})
    await MCPServer.update_one({'id': row.id}, {'oauth': oauth._seal({
        'provider': 'google',
        'tokens': {'access_token': 'ya29.first', 'refresh_token': 'r', 'expires_at': time.time() + 3000},
    })})
    row = await MCPServer.get(row.id)
    config = registry.config_from_row(row)
    client = FakeClient()

    assert await registry.prepare(client, config) is None
    assert client.live[config.key]['Authorization'] == 'Bearer ya29.first'

    # Close to expiry, the live connection is reopened with a renewed token.
    data = oauth.load(row)
    data['tokens']['expires_at'] = time.time() + 30
    await MCPServer.update_one({'id': row.id}, {'oauth': oauth._seal(data)})
    registry._live_expiry[config.key] = time.time() + 30
    assert await registry.prepare(client, config) is None
    assert client.live[config.key]['Authorization'] == 'Bearer ya29.renewed1'


async def test_tool_call_on_an_app_that_needs_reconnecting(db, monkeypatch):
    from cognitrix.mcp.tools import create_mcp_tool_wrapper
    from cognitrix.models.mcp_server import MCPServer

    _, member = await _people()
    await MCPServer(name='gmail', owner_id=str(member.id), url='https://gmailmcp.googleapis.com/mcp/v1',
                    auth_type='oauth', catalog_id='gmail', status='needs_reauth').save()
    row = await MCPServer.find_one({'name': 'gmail'})
    wrapper = create_mcp_tool_wrapper('gmail', {'name': 'search_threads', 'input_schema': {}}, str(row.id))
    result = (await wrapper.run()).content
    assert result.startswith('App sign-in needed: Gmail.')


async def test_disabled_server_is_not_reconnected_for_old_tool_wrappers(db):
    from cognitrix.mcp import registry
    from cognitrix.models.mcp_server import MCPServer

    _, member = await _people()
    await MCPServer(name='tracker', owner_id=str(member.id), url='https://mcp.example.com/mcp',
                    auth_type='none', enabled=False).save()
    row = await MCPServer.find_one({'name': 'tracker'})
    assert 'turned off' in await registry.prepare_key(str(row.id))
