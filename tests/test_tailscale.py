"""Tailscale: the two doors, the private and public addresses, and what
else changes with them (webhook addresses, passkeys). The Tailscale client is
a stand-in (``FakeTailscale``); nothing here needs Tailscale installed."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from cognitrix import app_settings, tailscale
from cognitrix.common import addresses
from cognitrix.tailscale import cli, daemon, doors, service

NAME = 'studio.tail1234.ts.net'


class FakeTailscale:
    """Answers the few commands Cognitrix runs, and keeps Serve's config."""

    def __init__(self):
        self.state = 'Running'
        self.name = NAME
        self.web: dict[str, dict] = {}       # 'name:port' -> {'Handlers': {...}}
        self.funnel: set[str] = set()
        self.calls: list[list[str]] = []
        self.serve_enabled = True
        self.funnel_enabled = True
        self.denied = False

    def status(self) -> dict:
        return {
            'Version': '1.94.1-t0', 'BackendState': self.state, 'AuthURL': '',
            'TailscaleIPs': ['100.64.0.7'], 'CertDomains': [self.name] if self.name else [],
            'CurrentTailnet': {'Name': 'ada@example.com'},
            'Self': {'DNSName': f'{self.name}.' if self.name else '', 'UserID': 7, 'TailscaleIPs': ['100.64.0.7'],
                     'KeyExpiry': '2027-05-01T00:00:00Z', 'CapMap': {'funnel': None, 'https': None}},
            'User': {'7': {'LoginName': 'ada@example.com'}},
        }

    def __call__(self, argv, timeout, input=None):
        args = [a for a in argv[1:] if not a.startswith('--socket=')]
        self.calls.append(args)
        if args[:2] == ['status', '--json']:
            return cli.Result(0, json.dumps(self.status()))
        if args[:3] == ['serve', 'status', '--json']:
            return cli.Result(0, json.dumps({
                'Web': self.web, 'AllowFunnel': {key: True for key in self.funnel},
                'TCP': {key.rsplit(':', 1)[1]: {'HTTPS': True} for key in self.web}}))
        if args[0] in ('serve', 'funnel'):
            funnel = args[0] == 'funnel'
            port = next(a.split('=', 1)[1] for a in args if a.startswith('--https='))
            key = f'{self.name}:{port}'
            if self.denied:
                return cli.Result(1, '', "Access denied: serve config denied\n\nUse 'sudo tailscale serve' or "
                                         "'tailscale up --operator=$USER' to not require root.")
            if args[-1] == 'off':
                self.web.pop(key, None)
                self.funnel.discard(key)
                return cli.Result(0)
            if not (self.funnel_enabled if funnel else self.serve_enabled):
                what = 'Funnel' if funnel else 'Serve'
                return cli.Result(-9, '', f'{what} is not enabled on your tailnet.\nTo enable, visit:\n\n'
                                          f'         https://login.tailscale.com/f/{what.lower()}?node=abc123.\n', True)
            self.web[key] = {'Handlers': {'/': {'Proxy': args[-1]}}}
            (self.funnel.add if funnel else self.funnel.discard)(key)
            return cli.Result(0, 'Available within your tailnet')
        return cli.Result(1, '', 'unknown command')

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


@pytest.fixture
def fake(monkeypatch):
    fake = FakeTailscale()
    monkeypatch.setattr(cli, '_run', fake)
    monkeypatch.setattr(cli, 'find_cli', lambda: '/fake/tailscale')
    monkeypatch.setattr(daemon, 'running', lambda: False)
    monkeypatch.setattr(daemon, 'available', lambda: False)
    monkeypatch.setattr(doors, 'ports', lambda: {doors.TAILNET: 41001, doors.PUBLIC: 41002})
    monkeypatch.setattr(doors, 'is_open', lambda: True)
    monkeypatch.setattr(service, '_last', None)
    monkeypatch.setattr(service, '_waiting', {})
    monkeypatch.setattr(app_settings, '_env_fallback', False)
    return fake


def _set(**values):
    app_settings.set_many(values)


@pytest.fixture
def public_on():
    """The public address is on in Settings (its door lets nothing through otherwise)."""
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_FUNNEL=True)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------

def test_reads_serve_status_and_tolerates_odd_output(monkeypatch):
    out = ('Warning: client version differs\n' + json.dumps({
        'Web': {f'{NAME}:443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3000'}}},
                f'{NAME}:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:41002'}, '/x': {'Path': '/tmp'}}},
                'nonsense': {}},
        'AllowFunnel': {f'{NAME}:8443': True}, 'TCP': {'443': {}, '22': {}, 'x': {}}}))
    monkeypatch.setattr(cli, 'find_cli', lambda: '/fake/tailscale')
    monkeypatch.setattr(daemon, 'running', lambda: False)
    monkeypatch.setattr(cli, '_run', lambda argv, timeout, input=None: cli.Result(0, out))
    served = cli.serving()
    assert served[443] == cli.Served(443, 'http://127.0.0.1:3000', False, 1)
    assert served[8443].funnel is True and served[8443].handlers == 2
    assert served[22].target == ''      # something else's TCP forward still counts as taken

    monkeypatch.setattr(cli, '_run', lambda argv, timeout, input=None: cli.Result(1, 'not json'))
    assert cli.serving() == {} and cli.status() is None
    monkeypatch.setattr(cli, 'find_cli', lambda: None)
    assert cli.run('status').code == 127 and cli.status() is None


def test_finds_the_page_tailscale_wants_opened():
    text = 'To enable, visit:\n\n   https://login.tailscale.com/f/serve?node=abc123.\n'
    assert cli.find_url(text) == 'https://login.tailscale.com/f/serve?node=abc123'
    assert cli.find_url('see https://evil.example.com/tailscale.com/x') == ''
    assert cli.whois('--json') is None


# ---------------------------------------------------------------------------
# The public door's allow list
# ---------------------------------------------------------------------------

WEBHOOKS = frozenset({'webhooks'})


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/v1/hooks/abc/tok'),
    ('POST', '/api/v1/github/app/webhook'),
    ('POST', '/api/v1/github/hooks/123'),
    ('POST', '/api/v1/channels/sms/9/webhook'),
    ('GET', '/api/v1/channels/whatsapp/9/webhook'),
    ('POST', '/api/v1/channels/teams/9/messages'),
    ('GET', '/api/v1/tailscale/door'),
])
def test_public_door_passes_webhooks(method, path):
    assert doors.public_allowed(method, path, WEBHOOKS)


@pytest.mark.parametrize('method,path', [
    ('GET', '/'),
    ('GET', '/login'),
    ('POST', '/api/v1/auth/login'),
    ('GET', '/api/v1/agents'),
    ('GET', '/api/v1/hooks/abc/tok'),                 # wrong method
    ('POST', '/api/v1/hooks/abc/tok/extra'),
    ('POST', '/api/v1/hooks/abc/../../agents'),       # nothing read two ways further in
    ('POST', '/api/v1/hooks//tok'),
    ('POST', '/api/v1/hooks/abc/tok/..'),
    ('POST', '/api/v1/hooks\\abc\\tok'),
    ('POST', '/API/V1/hooks/abc/tok'),
    ('GET', '/chat/abc'),                             # public links are a separate choice
    ('GET', '/assets/index.js'),
    ('POST', '/api/v1/tailscale/door'),
    ('GET', '/api/v1/tailscale'),
    ('POST', '/api/v1/passkeys/login/start'),
    ('GET', '/docs'),
    ('GET', '/health'),
])
def test_public_door_refuses_everything_else(method, path):
    assert not doors.public_allowed(method, path, WEBHOOKS)


def test_public_door_choices():
    links = frozenset({'links'})
    assert doors.public_allowed('GET', '/chat/abc', links)
    assert doors.public_allowed('GET', '/chat/abc/widget.js', links)
    assert doors.public_allowed('POST', '/api/v1/public/agents/abc/messages', links)
    assert doors.public_allowed('GET', '/s/abc', links)
    assert doors.public_allowed('GET', '/assets/index-abc.js', links)
    assert not doors.public_allowed('POST', '/api/v1/hooks/abc/tok', links)
    assert not doors.public_allowed('GET', '/api/v1/agents', links)
    assert not doors.public_allowed('GET', '/login', links)
    everything = frozenset({'app'})
    assert doors.public_allowed('GET', '/login', everything)
    assert not doors.public_allowed('GET', '/api/v1/../x', everything)
    # An unknown or empty choice falls back to webhooks only.
    app_settings.set_many({'COGNITRIX_TAILSCALE_FUNNEL_ALLOW': 'everything\nplease'})
    assert tailscale.funnel_allow() == WEBHOOKS


# ---------------------------------------------------------------------------
# Requests through the doors
# ---------------------------------------------------------------------------

async def _echo(scope, receive, send):
    """A stand-in app that says what it was told about the request."""
    if scope['type'] == 'websocket':
        await send({'type': 'websocket.accept'})
        return
    body = json.dumps({
        'door': scope.get(doors.SCOPE_KEY, 'direct'), 'client': list(scope['client'] or ()),
        'scheme': scope['scheme'], 'headers': {k.decode(): v.decode() for k, v in scope['headers']},
    }).encode()
    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
    await send({'type': 'http.response.body', 'body': body})


async def _through(app, method, path, headers=None, client=('127.0.0.1', 5555)):
    transport = httpx.ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:41001') as http:
        return await http.request(method, path, headers=headers or {})


TAILSCALE_HEADERS = {
    'Tailscale-User-Login': 'ada@example.com', 'Tailscale-User-Name': 'Ada',
    'X-Forwarded-For': '100.64.0.9', 'X-Forwarded-Host': NAME, 'X-Forwarded-Proto': 'https',
}


async def test_tailnet_door_keeps_who_the_visitor_is():
    app = doors.DoorGuard(_echo)
    seen = (await _through(doors.door_app(app, doors.TAILNET), 'GET', '/api/v1/agents', TAILSCALE_HEADERS)).json()
    assert seen['door'] == 'tailnet' and seen['scheme'] == 'https'
    assert seen['headers']['tailscale-user-login'] == 'ada@example.com'
    assert seen['headers']['host'] == NAME                 # the name the visitor typed
    assert seen['client'][0] == '100.64.0.9'               # never this machine


async def test_identity_headers_count_nowhere_else(public_on):
    # Straight at the main listener (which the local network can reach): dropped.
    seen = (await _through(doors.DoorGuard(_echo), 'GET', '/api/v1/agents', TAILSCALE_HEADERS)).json()
    assert seen['door'] == 'direct'
    assert not [h for h in seen['headers'] if h.startswith('tailscale-')]
    # Through the public door: dropped as well, whatever the visitor sent.
    app = doors.door_app(doors.DoorGuard(_echo), doors.PUBLIC)
    seen = (await _through(app, 'POST', '/api/v1/hooks/a/b', TAILSCALE_HEADERS)).json()
    assert seen['door'] == 'public'
    assert not [h for h in seen['headers'] if h.startswith('tailscale-')]


async def test_door_requests_never_look_like_this_machine(public_on):
    for forwarded in ('127.0.0.1', '::1', '0.0.0.0', 'garbage', '', '[::1]', '127.0.0.1, 100.64.0.9'):
        headers = {'X-Forwarded-For': forwarded} if forwarded else {}
        headers['fly-client-ip'] = '127.0.0.1'
        for name, path, method in ((doors.TAILNET, '/x', 'GET'), (doors.PUBLIC, '/api/v1/hooks/a/b', 'POST')):
            seen = (await _through(doors.door_app(_echo, name), method, path, headers)).json()
            assert seen['client'][0] == '0.0.0.0', forwarded
            assert 'fly-client-ip' not in seen['headers']


async def test_public_door_answers_404_before_the_app(public_on):
    reached = []

    async def app(scope, receive, send):
        reached.append(scope['path'])
        await _echo(scope, receive, send)

    public = doors.door_app(app, doors.PUBLIC)
    assert (await _through(public, 'GET', '/api/v1/agents')).status_code == 404
    assert (await _through(public, 'GET', '/login')).status_code == 404
    assert (await _through(public, 'POST', '/api/v1/hooks/a/b/../../agents')).status_code == 404
    assert reached == []
    assert (await _through(public, 'POST', '/api/v1/hooks/a/b')).status_code == 200
    assert reached == ['/api/v1/hooks/a/b']


async def test_public_door_limits_one_address(public_on, monkeypatch):
    monkeypatch.setattr(doors, '_hits', {})
    monkeypatch.setattr(doors, 'RATE_LIMIT', 3)
    public = doors.door_app(_echo, doors.PUBLIC)
    codes = [(await _through(public, 'POST', '/api/v1/hooks/a/b', {'X-Forwarded-For': '203.0.113.5'})).status_code
             for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    other = await _through(public, 'POST', '/api/v1/hooks/a/b', {'X-Forwarded-For': '203.0.113.6'})
    assert other.status_code == 200


async def test_public_door_closes_live_connections_unless_the_whole_app_is_public(public_on):
    async def connect(allow: str) -> list[dict]:
        app_settings.set_many({'COGNITRIX_TAILSCALE_FUNNEL_ALLOW': allow})
        sent: list[dict] = []

        async def receive():
            return {'type': 'websocket.connect'}

        async def send(message):
            sent.append(message)

        scope = {'type': 'websocket', 'path': '/ws', 'headers': [], 'client': ('127.0.0.1', 1), 'scheme': 'ws'}
        await doors.door_app(_echo, doors.PUBLIC)(scope, receive, send)
        return sent

    assert (await connect('webhooks'))[0] == {'type': 'websocket.close', 'code': 1008}
    assert (await connect('links'))[0] == {'type': 'websocket.close', 'code': 1008}
    assert (await connect('app'))[0] == {'type': 'websocket.accept'}


async def test_public_door_lets_nothing_through_while_the_public_address_is_off():
    """Tailscale may still forward to the door after Settings turned the address off
    (taking it down can fail): the door itself refuses then."""
    reached = []

    async def app(scope, receive, send):
        reached.append(scope['path'])
        await _echo(scope, receive, send)

    public = doors.door_app(app, doors.PUBLIC)
    for on, public_wanted, allow in ((False, False, 'webhooks'), (True, False, 'webhooks'), (False, True, 'app'),
                                     (True, False, 'app')):
        settings = {'COGNITRIX_TAILSCALE': on, 'COGNITRIX_TAILSCALE_FUNNEL': public_wanted,
                    'COGNITRIX_TAILSCALE_FUNNEL_ALLOW': allow}
        _set(**settings)
        assert (await _through(public, 'POST', '/api/v1/hooks/a/b')).status_code == 404, settings
        sent: list[dict] = []

        async def receive():
            return {'type': 'websocket.connect'}

        async def send(message, sent=sent):
            sent.append(message)

        await public({'type': 'websocket', 'path': '/ws', 'headers': [], 'client': ('127.0.0.1', 1), 'scheme': 'ws'},
                     receive, send)
        assert sent == [{'type': 'websocket.close', 'code': 1008}]
    assert reached == []
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_FUNNEL=True, COGNITRIX_TAILSCALE_FUNNEL_ALLOW='webhooks')
    assert (await _through(public, 'POST', '/api/v1/hooks/a/b')).status_code == 200 and reached == ['/api/v1/hooks/a/b']


async def test_the_real_app_says_which_door(public_on, monkeypatch):
    from cognitrix.api.main import app

    seen = await _through(doors.door_app(app, doors.PUBLIC), 'GET', '/api/v1/tailscale/door',
                          {'X-Forwarded-For': '203.0.113.5'})
    assert seen.status_code == 200 and seen.json() == {'door': 'public'}
    assert (await _through(app, 'GET', '/api/v1/tailscale/door')).json() == {'door': 'direct'}
    # Signed-in pages stay shut at the public door.
    assert (await _through(doors.door_app(app, doors.PUBLIC), 'GET', '/api/v1/tailscale')).status_code == 404


async def test_doors_listen_on_this_machine_only_and_remember_their_ports():
    try:
        ports = await doors.ensure_open(_echo)
        assert set(ports) == {'tailnet', 'public'} and doors.is_open()
        for server in doors._servers.values():
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.02)
            for sock in server.servers[0].sockets:
                assert sock.getsockname()[0] == '127.0.0.1'
        async with httpx.AsyncClient() as http:
            private = await http.get(f"http://127.0.0.1:{ports['tailnet']}/anything",
                                     headers={'X-Forwarded-For': '100.64.0.9'})
            public = await http.get(f"http://127.0.0.1:{ports['public']}/anything")
        assert private.json()['door'] == 'tailnet' and public.status_code == 404
        assert await doors.ensure_open(_echo) == ports       # safe to call again
        await doors.close()
        assert not doors.is_open()
        assert await doors.ensure_open(_echo) == ports       # the same ports next time
    finally:
        await doors.close()


# ---------------------------------------------------------------------------
# Serve and Funnel
# ---------------------------------------------------------------------------

def test_off_does_nothing_at_all(fake):
    snap = service.reconcile()
    assert snap['enabled'] is False and snap['state'] == 'running'
    assert not fake.ran('serve', '--bg') and not fake.ran('funnel')
    assert addresses.view()['private'] == '' and addresses.hooks_url() == ''


def test_private_address(fake):
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True)
    snap = service.reconcile()
    assert snap['mode'] == 'system' and snap['user'] == 'ada@example.com' and snap['name'] == NAME
    assert snap['private'] == {'wanted': True, 'port': 443, 'active': True, 'url': f'https://{NAME}',
                               'problem': '', 'detail': '', 'needs_url': ''}
    assert fake.ran('serve', '--bg') == [['serve', '--bg', '--yes', '--https=443', 'http://127.0.0.1:41001']]
    assert addresses.private_url() == f'https://{NAME}' and addresses.people_url() == f'https://{NAME}'
    assert addresses.funnel_url() == '' and addresses.hooks_url() == ''   # private is never given to outsiders
    # Nothing is set up twice.
    service.reconcile()
    assert len(fake.ran('serve', '--bg')) == 1
    # Off again: only what we put there is removed.
    fake.web[f'{NAME}:9443'] = {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3000'}}}
    _set(COGNITRIX_TAILSCALE_SERVE=False)
    service.reconcile()
    assert list(fake.web) == [f'{NAME}:9443'] and addresses.private_url() == ''


def test_public_address_for_webhooks(fake, monkeypatch):
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True, COGNITRIX_TAILSCALE_FUNNEL=True)
    snap = service.reconcile()
    assert snap['public']['active'] and snap['public']['url'] == f'https://{NAME}:8443'
    assert snap['public']['allow'] == ['webhooks']
    assert fake.ran('funnel') == [['funnel', '--bg', '--yes', '--https=8443', 'http://127.0.0.1:41002']]
    assert fake.funnel == {f'{NAME}:8443'}
    assert addresses.hooks_url() == f'https://{NAME}:8443'
    assert addresses.public_links_url() == ''              # links were not let through
    assert addresses.own_origins() == [f'https://{NAME}', f'https://{NAME}:8443']

    from cognitrix.tasks import pr_watch
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    assert pr_watch.webhooks_possible() and pr_watch.public_url() == f'https://{NAME}:8443'
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    assert pr_watch.webhooks_possible()                    # the desktop app has an address now
    _set(COGNITRIX_TAILSCALE_FUNNEL_ALLOW='links')
    assert not pr_watch.webhooks_possible() and addresses.public_links_url() == f'https://{NAME}:8443'
    # A public address typed in Settings wins.
    _set(COGNITRIX_PUBLIC_URL='https://cog.example.com/', COGNITRIX_TAILSCALE_FUNNEL_ALLOW='webhooks')
    assert addresses.hooks_url() == 'https://cog.example.com'
    # Moving the port takes the old one down.
    _set(COGNITRIX_TAILSCALE_FUNNEL_PORT='10000')
    service.reconcile()
    assert sorted(fake.web) == [f'{NAME}:10000', f'{NAME}:443'] and fake.funnel == {f'{NAME}:10000'}


def test_an_address_tailscale_will_not_remove_is_tried_again(fake):
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True, COGNITRIX_TAILSCALE_FUNNEL=True)
    assert service.reconcile()['public']['active']
    # Turned off in Settings, but Tailscale refuses to take it down.
    fake.denied = True
    _set(COGNITRIX_TAILSCALE_FUNNEL=False)
    snap = service.reconcile()
    assert not snap['public']['active'] and not snap['public']['wanted']
    assert snap['public']['problem'] == 'not_removed' and 'Access denied' in snap['public']['detail']
    assert fake.funnel == {f'{NAME}:8443'}                 # still forwarded by Tailscale
    assert addresses.hooks_url() == ''                     # and no longer given out as an address
    # It stays written down as ours, so the next check tries again and succeeds.
    fake.denied = False
    snap = service.reconcile()
    assert snap['public']['problem'] == '' and fake.funnel == set() and sorted(fake.web) == [f'{NAME}:443']
    off = len(fake.ran('funnel', '--https=8443', 'off'))
    service.reconcile()
    assert len(fake.ran('funnel', '--https=8443', 'off')) == off     # nothing left to remove


def test_desktop_without_a_public_address_still_has_no_webhooks(fake, monkeypatch):
    from cognitrix.tasks import pr_watch

    _set(COGNITRIX_PUBLIC_URL='https://cog.example.com')
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    assert not pr_watch.webhooks_possible()


def test_a_port_someone_else_uses_is_left_alone(fake):
    fake.web[f'{NAME}:443'] = {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:3000'}}}
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True)
    snap = service.reconcile()
    assert snap['private']['problem'] == 'port_in_use' and not snap['private']['active']
    assert not fake.ran('serve', '--bg') and not fake.ran('serve', '--https=443', 'off')
    assert fake.web[f'{NAME}:443']['Handlers']['/']['Proxy'] == 'http://127.0.0.1:3000'
    _set(COGNITRIX_TAILSCALE=False)
    service.reconcile()
    assert f'{NAME}:443' in fake.web


def test_same_port_for_both_is_refused(fake):
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True, COGNITRIX_TAILSCALE_FUNNEL=True,
         COGNITRIX_TAILSCALE_FUNNEL_PORT='443')
    snap = service.reconcile()
    assert snap['private']['active'] and snap['public']['problem'] == 'same_port'
    assert not fake.ran('funnel')


def test_a_feature_the_tailnet_has_not_turned_on(fake, monkeypatch):
    fake.funnel_enabled = False
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_FUNNEL=True)
    snap = service.reconcile()
    assert snap['public']['problem'] == 'needs_turning_on'
    assert snap['public']['needs_url'] == 'https://login.tailscale.com/f/funnel?node=abc123'
    assert service.pending() and addresses.hooks_url() == ''
    # Asked again right away: the same answer without waiting on the client.
    again = service.reconcile()
    assert again['public']['needs_url'] == snap['public']['needs_url'] and len(fake.ran('funnel')) == 1
    # Once the owner turned it on, the next try goes through.
    fake.funnel_enabled = True
    monkeypatch.setattr(service, 'RETRY_AFTER_S', 0)
    assert service.reconcile()['public']['active'] and not service.pending()


def test_linux_without_permission_says_what_to_run(fake):
    fake.denied = True
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True)
    assert service.reconcile()['private']['problem'] == 'needs_operator'


def test_signed_out_or_missing(fake, monkeypatch):
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True)
    fake.state = 'NeedsLogin'
    snap = service.reconcile()
    assert snap['state'] == 'needs_login' and snap['name'] == '' and not fake.ran('serve', '--bg')
    assert snap['can_sign_in'] is False            # the machine's own Tailscale is signed in from its own app
    with pytest.raises(service.TailscaleError):
        service.disconnect()
    fake.state, fake.name = 'Running', ''
    assert service.reconcile()['private']['problem'] == 'needs_magicdns'
    monkeypatch.setattr(cli, 'find_cli', lambda: None)
    assert service.reconcile()['state'] == 'missing'
    with pytest.raises(service.TailscaleError):
        service.connect()


def test_built_in_tailscale_signs_in_with_a_key_that_is_not_kept(fake, monkeypatch):
    started = []
    seen = {}
    monkeypatch.setattr(daemon, 'available', lambda: True)
    monkeypatch.setattr(daemon, 'running', lambda: bool(started))
    monkeypatch.setattr(daemon, 'start', lambda wait=12.0: started.append(True) or True)
    monkeypatch.setattr(service, '_SYSTEM_MARKS', ())
    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_HOSTNAME='My Server!')
    real = fake.__call__

    def run(argv, timeout, input=None):
        args = [a for a in argv[1:] if not a.startswith('--socket=')]
        if args[0] == 'up':
            fake.calls.append(args)
            key_file = next(a for a in args if a.startswith('--auth-key=file:')).split(':', 1)[1]
            seen['key'] = open(key_file, encoding='utf-8').read()
            seen['socket'] = [a for a in argv if a.startswith('--socket=')]
            return cli.Result(0)
        return real(argv, timeout, input)

    monkeypatch.setattr(cli, '_run', run)
    assert service.mode() == 'builtin'
    snap = service.connect('tskey-auth-abc123')
    assert snap['mode'] == 'builtin' and snap['can_sign_in'] and started
    assert fake.ran('up')[0][1] == '--hostname=my-server'
    assert seen['key'] == 'tskey-auth-abc123' and seen['socket']       # asked of our own Tailscale
    assert not (tailscale.folder() / 'join-key').exists()
    assert 'tskey' not in json.dumps(app_settings._read_file())


def test_saved_ports_and_names_are_cleaned():
    assert tailscale.funnel_port() == 8443 and tailscale.serve_port() == 443
    app_settings.set_many({'COGNITRIX_TAILSCALE_SERVE_PORT': '9443', 'COGNITRIX_TAILSCALE_HOSTNAME': ' Büro PC_1 '})
    assert tailscale.serve_port() == 9443 and tailscale.hostname() == 'b-ro-pc-1'
    with pytest.raises(ValueError):
        app_settings.set_many({'COGNITRIX_TAILSCALE_FUNNEL_PORT': '8080'})   # Tailscale only offers three
    assert not tailscale.serve_wanted()            # nothing counts while Tailscale support is off


# ---------------------------------------------------------------------------
# The Settings page's routes
# ---------------------------------------------------------------------------

async def test_only_an_admin_changes_tailscale(fake, monkeypatch):
    from fastapi import HTTPException

    from cognitrix.api.routes import tailscale as routes
    from cognitrix.common.security import AuthContext

    admins = {'boss'}

    async def is_admin(user):
        return user.id in admins

    async def changed(app):
        return service.reconcile()

    monkeypatch.setattr(routes, 'is_admin', is_admin)
    monkeypatch.setattr(service, 'settings_changed', changed)
    request = SimpleNamespace(app=None, scope={})
    boss, member = (AuthContext(user=SimpleNamespace(id=name, email=f'{name}@example.com')) for name in ('boss', 'member'))

    with pytest.raises(HTTPException) as refused:
        await routes.save_tailscale(routes.TailscaleBody(enabled=True), request, member)
    assert refused.value.status_code == 403 and not tailscale.enabled()
    for call in (routes.connect_tailscale(routes.ConnectBody(), request, member),
                 routes.disconnect_tailscale(request, member), routes.check_tailscale(member)):
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 403

    saved = await routes.save_tailscale(
        routes.TailscaleBody(enabled=True, private=True, public=True, allow=['LINKS', 'nonsense']), request, boss)
    assert saved['can_edit'] and saved['private']['active'] and saved['public']['allow'] == ['links']
    assert saved['door'] == 'direct'
    with pytest.raises(HTTPException) as bad:
        await routes.save_tailscale(routes.TailscaleBody(public_port=8080), request, boss)
    assert bad.value.status_code == 400
    seen = await routes.read_tailscale(request, member)
    assert seen['can_edit'] is False and seen['private']['url'] == f'https://{NAME}'


async def test_the_sign_in_page_is_for_admins_only(fake, monkeypatch):
    """Whoever opens Tailscale's sign-in page adds this machine to their own tailnet."""
    from cognitrix.api.routes import tailscale as routes
    from cognitrix.common.security import AuthContext

    async def is_admin(user):
        return user.id == 'boss'

    monkeypatch.setattr(routes, 'is_admin', is_admin)
    monkeypatch.setattr(service, '_login_url', 'https://login.tailscale.com/a/abc123')
    fake.state = 'NeedsLogin'
    _set(COGNITRIX_TAILSCALE=True)
    request = SimpleNamespace(app=None, scope={})
    boss, member = (AuthContext(user=SimpleNamespace(id=name)) for name in ('boss', 'member'))
    assert (await routes.read_tailscale(request, boss))['login_url'] == 'https://login.tailscale.com/a/abc123'
    assert (await routes.read_tailscale(request, member))['login_url'] == ''


async def test_desktop_pages_get_the_security_policy_over_tailscale(monkeypatch):
    from cognitrix.api import main

    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    assert main._content_security_policy() == ''                       # the app's own window
    assert main._content_security_policy(True) == main._DEFAULT_CSP    # a browser on the tailnet


# ---------------------------------------------------------------------------
# Passkeys on the Tailscale addresses
# ---------------------------------------------------------------------------

def _page(origin: str, host: str, ip: str = '100.64.0.9'):
    return SimpleNamespace(headers={'origin': origin, 'host': host}, client=SimpleNamespace(host=ip),
                           query_params={})


def test_passkeys_belong_to_the_tailscale_name_never_the_address(fake):
    from cognitrix import passkeys

    _set(COGNITRIX_TAILSCALE=True, COGNITRIX_TAILSCALE_SERVE=True, COGNITRIX_TAILSCALE_FUNNEL=True,
         COGNITRIX_TAILSCALE_FUNNEL_ALLOW='app')
    service.reconcile()
    # The private address: https and a real name, so passkeys work there.
    assert passkeys.site_for(_page(f'https://{NAME}', NAME)) == (NAME, f'https://{NAME}')
    # The public Tailscale address has the same name, so the same passkeys work on it.
    assert passkeys.site_for(_page(f'https://{NAME}:8443', 'internal:8000'))[0] == NAME
    # The tailnet IP address and the short name over http: refused with a reason.
    with pytest.raises(passkeys.PasskeyError) as ip:
        passkeys.site_for(_page('http://100.64.0.7:8000', '100.64.0.7:8000'))
    assert ip.value.code == 'ip_address'
    with pytest.raises(passkeys.PasskeyError) as plain:
        passkeys.site_for(_page('http://studio:8000', 'studio:8000'))
    assert plain.value.code == 'not_https'
    # Some other site is still not this server.
    with pytest.raises(passkeys.PasskeyError):
        passkeys.site_for(_page('https://evil.ts.net', 'internal:8000'))


async def test_passkey_browser_step_is_not_reachable_through_a_door(monkeypatch):
    """The desktop app trusts requests from its own machine for that step.
    A door request must never look like one."""
    from cognitrix import passkeys

    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    seen = {}

    async def app(scope, receive, send):
        from starlette.requests import Request
        seen['loopback'] = passkeys._loopback(Request(scope))
        await _echo(scope, receive, send)

    await _through(doors.door_app(app, doors.TAILNET), 'GET', '/x', {'X-Forwarded-For': '127.0.0.1'})
    assert seen['loopback'] is False
    await _through(app, 'GET', '/x')
    assert seen['loopback'] is True
