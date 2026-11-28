"""Pairing a PC over the tailnet (Part 3 of the Tailscale work).

- A plain http address is accepted only for a machine Tailscale on the PC lists in its tailnet.
- "Find my server" asks the machines on the tailnet whether they are Cognitrix.
- A PC can ask to pair without a code: Tailscale says which login it is, the
  account that login is linked to approves it on the web, then the PC gets its token.
- A link keeps every address of its server and tries the tailnet one first.

Tailscale is never run: the server is reached through the tailnet door with the
headers Tailscale Serve adds, and the PC's ``tailscale status`` is a stand-in.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from cognitrix import app_settings
from cognitrix.computer.paired import store, tailnet
from cognitrix.tailscale import cli, doors, identity
from tests.test_paired_pc import _free_port, _wait, db  # noqa: F401 - db is a fixture

NAME = 'studio.tail1234.ts.net'
PC_IP = '100.64.0.9'
STATUS = {
    'BackendState': 'Running',
    'Self': {'HostName': 'work-laptop', 'DNSName': 'work-laptop.tail1234.ts.net.', 'TailscaleIPs': [PC_IP, 'fd7a:115c:a1e0::9']},
    'Peer': {
        'k1': {'HostName': 'studio', 'DNSName': f'{NAME}.', 'TailscaleIPs': ['100.64.0.7'], 'Online': True},
        'k2': {'HostName': 'nas', 'DNSName': 'nas.tail1234.ts.net.', 'TailscaleIPs': ['100.64.0.8'], 'Online': False},
    },
}


@pytest.fixture
def my_tailnet(monkeypatch):
    """Tailscale on the PC: three machines, one of them offline."""
    state = SimpleNamespace(status=STATUS, resolves={NAME: ['100.64.0.7'], 'studio': ['100.64.0.7']})
    monkeypatch.setattr(tailnet, '_status', lambda: state.status)
    monkeypatch.setattr(tailnet, '_cached', None)

    def getaddrinfo(host, *_args, **_kwargs):
        if host not in state.resolves:
            raise OSError('unknown name')
        return [(None, None, None, '', (ip, 0)) for ip in state.resolves[host]]

    monkeypatch.setattr(tailnet.socket, 'getaddrinfo', getaddrinfo)
    return state


# ---------------------------------------------------------------------------
# Plain http, only inside the tailnet
# ---------------------------------------------------------------------------

def test_plain_http_is_for_machines_on_my_tailnet_only(my_tailnet, monkeypatch):
    from cognitrix.computer.paired.connector import LinkError, normalize_server

    monkeypatch.delenv('COGNITRIX_PAIRED_ALLOW_HTTP', raising=False)
    assert normalize_server(f'http://{NAME}:8000') == f'http://{NAME}:8000'
    assert normalize_server('http://100.64.0.7:8000') == 'http://100.64.0.7:8000'
    assert normalize_server('http://studio:8000') == 'http://studio:8000'
    for address in ('http://cognitrix.example.com', 'http://100.64.0.99:8000', 'http://192.168.1.20:8000',
                    'http://unknown.tail1234.ts.net:8000'):
        with pytest.raises(LinkError) as refused:
            normalize_server(address)
        assert refused.value.code == 'insecure'

    # Some other name pointed at a tailnet address is not one of the tailnet's names.
    my_tailnet.resolves['cognitrix.example.com'] = ['100.64.0.7']
    with pytest.raises(LinkError):
        normalize_server('http://cognitrix.example.com:8000')
    # A name that also leads somewhere outside the tailnet is not a tailnet address.
    my_tailnet.resolves['studio'] = ['100.64.0.7', '192.168.1.20']
    with pytest.raises(LinkError):
        normalize_server('http://studio:8000')
    # Tailscale stopped or signed out on the PC: nothing is confirmed.
    my_tailnet.status = {**STATUS, 'BackendState': 'NeedsLogin'}
    monkeypatch.setattr(tailnet, '_cached', None)
    with pytest.raises(LinkError):
        normalize_server(f'http://{NAME}:8000')
    my_tailnet.status = None
    monkeypatch.setattr(tailnet, '_cached', None)
    assert tailnet.confirms('100.64.0.7') is False
    # https never needed Tailscale's word.
    assert normalize_server(f'https://{NAME}') == f'https://{NAME}'


def test_a_link_keeps_every_address_tailnet_first(my_tailnet):
    from cognitrix.computer.paired.connector import _servers_after_pairing, servers_of

    told = [f'https://{NAME}', 'https://cognitrix.example.com', 'http://evil.example.com', 'not an address', 7]
    assert _servers_after_pairing('https://cognitrix.example.com', told) == [f'https://{NAME}', 'https://cognitrix.example.com']
    assert _servers_after_pairing(f'http://{NAME}:8000', told) == [
        f'https://{NAME}', f'http://{NAME}:8000', 'https://cognitrix.example.com']
    assert _servers_after_pairing('https://a.example.com', None) == ['https://a.example.com']
    # A link from before there were several addresses.
    assert servers_of({'server': 'https://a.example.com'}) == ['https://a.example.com']
    assert servers_of({'server': 'https://a.example.com', 'servers': ['https://b.ts.net', 'https://a.example.com/']}) == [
        'https://b.ts.net', 'https://a.example.com']


# ---------------------------------------------------------------------------
# The server, reached the way Tailscale Serve reaches it
# ---------------------------------------------------------------------------

class _Serve:
    """Stands in for Tailscale Serve: adds who is asking, then goes through the tailnet door."""

    def __init__(self, app):
        self.app = app
        self.login = 'kofi@example.com'
        self.door = doors.TAILNET

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = [(k, v) for k, v in scope.get('headers', []) if not k.startswith(b'tailscale-')]
        if self.login:
            headers += [(b'tailscale-user-login', self.login.encode()), (b'tailscale-user-name', b'Kofi')]
        headers.append((b'x-forwarded-for', PC_IP.encode()))
        scope = {**scope, 'headers': headers}
        app = doors.DoorGuard(self.app)
        return await (doors.door_app(app, self.door) if self.door else app)(scope, receive, send)


@pytest.fixture
async def world(db, monkeypatch):  # noqa: F811
    """A server with the devices API behind the tailnet door, Kofi's account, and his Tailscale login linked."""
    import uvicorn
    from fastapi import FastAPI

    from cognitrix import signins
    from cognitrix.api.routes.computer_devices import devices_api
    from cognitrix.common import addresses
    from cognitrix.common.security import AuthContext, jwt_only
    from cognitrix.models import User

    store._requests.clear()
    signins._checks.clear()
    app_settings.set_many({'COGNITRIX_TAILSCALE': True, 'COGNITRIX_TAILSCALE_SERVE': True,
                           'COGNITRIX_TAILSCALE_SIGNIN': True})
    monkeypatch.setattr(addresses, 'private_url', lambda: f'https://{NAME}')
    monkeypatch.setattr(cli, 'whois', lambda address: {'Node': {'ComputedName': 'work-laptop', 'Name': 'work-laptop.tail1234.ts.net.'}}
                        if address == PC_IP else None)
    await User(name='Kofi Boateng', email='kofi@example.com', password='x' * 20).save()
    kofi = await User.find_one({'email': 'kofi@example.com'})
    tailnet_request = SimpleNamespace(headers={'tailscale-user-login': 'kofi@example.com', 'user-agent': 'test'},
                                      scope={doors.SCOPE_KEY: doors.TAILNET}, client=SimpleNamespace(host=PC_IP))
    await identity.link(tailnet_request, kofi)

    api = FastAPI()
    api.include_router(devices_api, prefix='/api/v1')
    here = SimpleNamespace(sid='')
    api.dependency_overrides[jwt_only] = lambda: AuthContext(user=kofi, sid=here.sid)
    serve = _Serve(api)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(serve, host='127.0.0.1', port=port, log_level='warning', lifespan='off'))
    task = asyncio.create_task(srv.serve())
    for _ in range(100):
        if srv.started:
            break
        await asyncio.sleep(0.05)
    url = f'http://127.0.0.1:{port}'
    async with httpx.AsyncClient(base_url=url + '/api/v1/computer/devices') as http:
        yield SimpleNamespace(url=url, http=http, serve=serve, kofi=kofi, here=here)
    srv.should_exit = True
    await task


async def test_hello_says_cognitrix_and_whether_this_pc_can_ask(world):
    from cognitrix.config import VERSION

    assert (await world.http.get('/hello')).json() == {'app': 'cognitrix', 'version': VERSION, 'ask_to_pair': True}
    world.serve.login = 'eve@example.com'                   # on the tailnet, linked to nobody
    assert (await world.http.get('/hello')).json()['ask_to_pair'] is False
    world.serve.login, world.serve.door = 'kofi@example.com', None      # the local network
    assert (await world.http.get('/hello')).json() == {'app': 'cognitrix', 'version': VERSION, 'ask_to_pair': False}


async def test_a_pc_asks_and_its_owner_approves_on_the_web(world, tmp_path):
    from cognitrix import signins
    from cognitrix.models.paired_device import PairedDevice
    from cognitrix.models.sign_in import SignIn

    asked = await world.http.post('/pair-requests', json={'name': 'Work laptop', 'platform': 'Windows 11', 'client': 'desktop'})
    assert asked.status_code == 200, asked.text
    ask = asked.json()
    assert ask['account'] == 'Kofi Boateng · kofi@example.com' and ask['expires_in'] > 500
    mine = {'secret': ask['secret']}
    path = f"/pair-requests/{ask['request_id']}"

    # It waits under Settings, Computers, saying who Tailscale says it is. The secret stays with the PC.
    waiting = (await world.http.get('/pair-requests')).json()['requests']
    assert [(r['name'], r['login'], r['node'], r['address'], r['client']) for r in waiting] == [
        ('Work laptop', 'kofi@example.com', 'work-laptop', PC_IP, 'desktop')]
    assert 'secret' not in str(waiting)
    assert (await world.http.post(path + '/status', json=mine)).json()['status'] == 'pending'
    early = await world.http.post(path + '/pair', json={**mine, 'folders': [str(tmp_path)], 'powers': ['files']})
    assert early.status_code == 409 and early.json()['detail']['code'] == 'not_approved'

    # A device that itself came in by a code today can't approve a PC.
    world.here.sid = await signins.start(world.kofi, method='code', name='New phone')
    refused = await world.http.post(path + '/approve')
    assert refused.status_code == 403 and 'less than a day ago' in refused.json()['detail']
    world.here.sid = await signins.start(world.kofi, method='password', name='Chrome on Windows')
    assert (await world.http.post(path + '/approve')).json() == {'requests': []}
    assert (await world.http.post(path + '/status', json=mine)).json()['status'] == 'approved'

    # Only the PC that asked can use it: the secret, the same Tailscale login, the tailnet door.
    assert (await world.http.post(path + '/status', json={'secret': 'guess'})).status_code == 404
    world.serve.login = 'eve@example.com'
    assert (await world.http.post(path + '/pair', json={**mine, 'folders': [], 'powers': []})).status_code == 404
    world.serve.login, world.serve.door = 'kofi@example.com', None
    assert (await world.http.post(path + '/pair', json={**mine, 'folders': [], 'powers': []})).status_code == 404
    world.serve.door = doors.TAILNET

    done = await world.http.post(path + '/pair', json={**mine, 'name': 'Work laptop', 'platform': 'Windows 11',
                                                        'folders': [str(tmp_path)], 'powers': ['files'], 'client': 'desktop'})
    assert done.status_code == 200, done.text
    out = done.json()
    assert out['token'].startswith('cgxd_') and out['addresses'] == [f'https://{NAME}']
    device = await PairedDevice.find_one({'id': out['device_id']})
    assert str(device.user_id) == str(world.kofi.id) and device.tailnet_node == 'work-laptop'
    assert await store.device_for_token(out['token']) is not None
    row = await SignIn.find_one({'sid': device.sign_in_sid})
    assert row.method == 'pairing' and row.approved_by == 'Chrome on Windows' and row.kind == 'desktop'
    listed = (await world.http.get(world.url + '/api/v1/computer/devices')).json()
    assert [d['tailnet_node'] for d in listed['devices']] == ['work-laptop']
    # Used once.
    again = await world.http.post(path + '/pair', json={**mine, 'folders': [str(tmp_path)], 'powers': ['files']})
    assert again.status_code == 404 and 'already used' in again.json()['detail']['message']


async def test_an_approval_ends_when_its_approver_is_signed_out(world, tmp_path):
    from cognitrix import signins
    from cognitrix.models import User
    from cognitrix.models.paired_device import PairedDevice

    async def approved_request():
        ask = (await world.http.post('/pair-requests', json={'name': 'Work laptop'})).json()
        world.here.sid = await signins.start(world.kofi, method='password', name='Chrome on Windows')
        assert (await world.http.post(f"/pair-requests/{ask['request_id']}/approve")).status_code == 200
        return f"/pair-requests/{ask['request_id']}", {'secret': ask['secret'], 'folders': [str(tmp_path)], 'powers': ['files']}

    # The approving device is signed out before the PC finishes.
    path, body = await approved_request()
    assert await signins.end(str(world.kofi.id), world.here.sid)
    gone = await world.http.post(path + '/pair', json=body)
    assert gone.status_code == 404 and 'signed out since' in gone.json()['detail']['message']
    assert (await world.http.post(path + '/status', json=body)).json()['status'] == 'denied'

    # Signed out everywhere (what a new password does too).
    path, body = await approved_request()
    await signins.end_all(str(world.kofi.id))
    assert (await world.http.post(path + '/pair', json=body)).status_code == 404

    # Every session ended some other way: the account's cut-off alone is enough.
    path, body = await approved_request()
    await User.update_one({'id': world.kofi.id}, {'tokens_valid_after': int(time.time()) + 5})
    assert (await world.http.post(path + '/pair', json=body)).status_code == 404
    assert not await PairedDevice.find({'user_id': str(world.kofi.id)})

    # A new yes after that works.
    await User.update_one({'id': world.kofi.id}, {'tokens_valid_after': int(time.time()) - 5})
    path, body = await approved_request()
    assert (await world.http.post(path + '/pair', json=body)).status_code == 200


async def test_declined_expired_and_repeated_requests(world):
    first = (await world.http.post('/pair-requests', json={'name': 'Laptop'})).json()
    # Asking again from the same machine replaces the request before it.
    second = (await world.http.post('/pair-requests', json={'name': 'Laptop'})).json()
    waiting = (await world.http.get('/pair-requests')).json()['requests']
    assert [r['id'] for r in waiting] == [second['request_id']]
    assert (await world.http.post(f"/pair-requests/{first['request_id']}/status", json={'secret': first['secret']})).status_code == 404

    assert (await world.http.post(f"/pair-requests/{second['request_id']}/deny")).json() == {'requests': []}
    status = (await world.http.post(f"/pair-requests/{second['request_id']}/status", json={'secret': second['secret']})).json()
    assert status['status'] == 'denied'
    assert (await world.http.post(f"/pair-requests/{second['request_id']}/approve")).status_code == 404

    third = (await world.http.post('/pair-requests', json={'name': 'Laptop'})).json()
    store._requests[third['request_id']].expires = 0
    status = await world.http.post(f"/pair-requests/{third['request_id']}/status", json={'secret': third['secret']})
    assert status.json()['status'] == 'expired'
    assert (await world.http.post(f"/pair-requests/{third['request_id']}/approve")).status_code == 404
    assert (await world.http.get('/pair-requests')).json() == {'requests': []}


async def test_asking_needs_the_tailnet_a_linked_login_and_the_setting(world):
    world.serve.login = 'eve@example.com'
    unlinked = await world.http.post('/pair-requests', json={'name': 'Laptop'})
    assert unlinked.status_code == 404 and unlinked.json()['detail']['code'] == 'no_link'
    world.serve.login = ''                                   # a tagged device: nobody
    assert (await world.http.post('/pair-requests', json={'name': 'Laptop'})).json()['detail']['code'] == 'no_tailnet'
    world.serve.login, world.serve.door = 'kofi@example.com', doors.PUBLIC
    assert (await world.http.post('/pair-requests', json={'name': 'Laptop'})).status_code == 404
    world.serve.door = None
    assert (await world.http.post('/pair-requests', json={'name': 'Laptop'})).json()['detail']['code'] == 'no_tailnet'
    world.serve.door = doors.TAILNET
    app_settings.set_many({'COGNITRIX_TAILSCALE_SIGNIN': False})
    assert (await world.http.post('/pair-requests', json={'name': 'Laptop'})).json()['detail']['code'] == 'no_tailnet'
    assert (await world.http.get('/pair-requests')).json() == {'requests': []}


async def test_wrong_guesses_are_limited_but_waiting_is_not(world):
    ask = (await world.http.post('/pair-requests', json={'name': 'Laptop'})).json()
    for _ in range(store.CLAIM_TRIES + 5):                   # ten minutes of asking how it stands
        ok = await world.http.post(f"/pair-requests/{ask['request_id']}/status", json={'secret': ask['secret']})
        assert ok.status_code == 200
    codes = [(await world.http.post(f"/pair-requests/{ask['request_id']}/status", json={'secret': f'guess{i}'})).status_code
             for i in range(store.CLAIM_TRIES + 2)]
    assert codes[0] == 404 and codes[-1] == 429


# ---------------------------------------------------------------------------
# The PC's side, end to end
# ---------------------------------------------------------------------------

async def test_the_connector_asks_waits_pairs_and_connects(world, tmp_path, my_tailnet):
    from cognitrix.computer.paired.connector import Connector, LinkError, ask, ask_status, link_approved, load_config

    shared = tmp_path / 'project'
    shared.mkdir()
    asked = await ask(world.url, name='Work laptop', client='desktop')
    assert asked['account'] == 'Kofi Boateng · kofi@example.com'
    assert (await ask_status(asked))['status'] == 'pending'
    with pytest.raises(LinkError):
        await link_approved(asked, name='Work laptop', folders=[str(shared)], powers=['files'])
    await world.http.post(f"/pair-requests/{asked['request_id']}/approve")
    assert (await ask_status(asked))['status'] == 'approved'
    view = await link_approved(asked, name='Work laptop', folders=[str(shared)], powers=['files'], client='desktop')
    assert view['linked'] and view['account'] == 'Kofi Boateng · kofi@example.com'
    # Both addresses are kept, the tailnet one first.
    assert load_config()['servers'] == [f'https://{NAME}', world.url]

    # The tailnet address is not reachable from here: the link falls back to the other one.
    connector = Connector()
    connector.start()
    try:
        assert await _wait(lambda: connector.status == 'connected', 30), connector.last_error
        assert connector.view()['connected_to'] == world.url
        assert (await world.http.get(world.url + '/api/v1/computer/devices')).json()['devices'][0]['online'] is True
    finally:
        await connector.stop()


async def test_find_my_server_asks_the_machines_that_are_on(world, my_tailnet, monkeypatch):
    port = int(world.url.rsplit(':', 1)[1])
    monkeypatch.setattr(tailnet, 'SERVER_PORT', port)
    asked: list[str] = []
    real = tailnet._ask

    async def ask(http, url):
        asked.append(url)
        # The names lead to this test's server; https there is not served.
        if not url.startswith('http://'):
            return None
        found = await real(http, url.replace(NAME, '127.0.0.1'))
        return {**found, 'server': url} if found else None

    monkeypatch.setattr(tailnet, '_ask', ask)
    found = await tailnet.find_servers()
    assert [(f['name'], f['server'], f['ask_to_pair']) for f in found] == [('studio', f'http://{NAME}:{port}', True)]
    # Not this PC, and not the machine that is off.
    assert asked == [f'https://{NAME}', f'http://{NAME}:{port}']

    my_tailnet.status = None
    with pytest.raises(LookupError):
        await tailnet.find_servers()


async def test_the_desktop_app_asks_and_finishes(world, tmp_path, my_tailnet, monkeypatch):
    from fastapi import HTTPException

    from cognitrix.api.routes import computer_devices as routes
    from cognitrix.common.security import AuthContext
    from cognitrix.computer.paired.connector import get_connector

    me = AuthContext(user=SimpleNamespace(id='local'))
    with pytest.raises(HTTPException) as web_only:
        await routes.ask_server(routes.AskServerBody(server=world.url), me)
    assert web_only.value.status_code == 404                 # only the desktop app has a PC to link
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    monkeypatch.setattr(routes, '_asked', {})
    assert (await routes.asked_status(me))['status'] == 'none'
    shared = tmp_path / 'project'
    shared.mkdir()
    view = await routes.ask_server(routes.AskServerBody(server=world.url, name='Work laptop'), me)
    assert view == {'status': 'pending', 'server': world.url, 'account': 'Kofi Boateng · kofi@example.com'}
    assert 'secret' not in str(view)
    request_id = routes._asked['request_id']
    await world.http.post(f'/pair-requests/{request_id}/approve')
    assert (await routes.asked_status(me))['status'] == 'approved'
    try:
        link = await routes.finish_asked(routes.AskFinishBody(name='Work laptop', folders=[str(shared)], powers=['files']), me)
        assert link['linked'] and link['servers'] == [f'https://{NAME}', world.url]
        assert routes._asked == {}
    finally:
        await get_connector().stop()

    # Declined on the web: the app is told once, then there is nothing to wait on.
    await routes.ask_server(routes.AskServerBody(server=world.url, name='Work laptop'), me)
    await world.http.post(f"/pair-requests/{routes._asked['request_id']}/deny")
    assert (await routes.asked_status(me))['status'] == 'denied'
    assert (await routes.asked_status(me))['status'] == 'none'


async def test_an_older_database_gets_the_tailnet_node_column(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'old.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await DBMS.Database.query('CREATE TABLE paireddevices (id TEXT PRIMARY KEY, user_id TEXT, name TEXT)')
    await _ensure_schema()
    cursor = await DBMS.Database.query('PRAGMA table_info(paireddevices)')
    rows = cursor.fetchall() if hasattr(cursor, 'fetchall') else cursor
    assert 'tailnet_node' in {r[1] for r in rows}
