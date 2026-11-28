"""The agents' computer hosted on Fly (cognitrix/computer/sandbox/fly.py) and
the supervisor's /activity check used for idle shutdown. Fly itself is faked:
nothing here talks to Fly."""
from __future__ import annotations

import json
import socket
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cognitrix.computer import Unsupported
from cognitrix.computer.sandbox import TOKEN_ENV
from cognitrix.computer.sandbox.fly import FlyMachines, FlyManager, fly_settings


class FakeFly:
    """An in-memory Fly app: machines and volumes."""

    app = 'cog-sandboxes'

    def __init__(self):
        self.machines_: dict[str, dict] = {}
        self.volumes_: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self._n = 0

    def _id(self, prefix):
        self._n += 1
        return f'{prefix}{self._n}'

    def machines(self):
        self.calls.append(('list',))
        return [dict(m) for m in self.machines_.values()]

    def machine(self, mid):
        m = self.machines_.get(mid)
        return dict(m) if m else None

    def create(self, body):
        self.calls.append(('create', body))
        mid = self._id('m')
        vol = body['config']['mounts'][0]['volume']
        self.volumes_[vol]['attached_machine_id'] = mid
        self.machines_[mid] = {'id': mid, 'name': body['name'], 'state': 'started', 'region': body['region'],
                               'private_ip': f'fdaa::{self._n}', 'config': body['config']}
        return dict(self.machines_[mid])

    def update(self, mid, body):
        self.calls.append(('update', mid, body['config']['image']))
        self.machines_[mid]['config'] = body['config']
        return dict(self.machines_[mid])

    def start(self, mid):
        self.calls.append(('start', mid))
        self.machines_[mid]['state'] = 'started'

    def stop(self, mid):
        self.calls.append(('stop', mid))
        self.machines_[mid]['state'] = 'stopped'

    def suspend(self, mid):
        self.calls.append(('suspend', mid))
        self.machines_[mid]['state'] = 'suspended'

    def wait(self, mid, state='started', seconds=60):
        self.calls.append(('wait', mid, state))

    def destroy(self, mid):
        self.calls.append(('destroy', mid))
        m = self.machines_.pop(mid)
        for vol in self.volumes_.values():
            if vol.get('attached_machine_id') == m['id']:
                vol['attached_machine_id'] = None

    def volumes(self):
        return [dict(v) for v in self.volumes_.values()]

    def create_volume(self, name, region, size_gb):
        self.calls.append(('create_volume', name, region, size_gb))
        vid = self._id('vol_')
        self.volumes_[vid] = {'id': vid, 'name': name, 'region': region, 'state': 'created',
                              'attached_machine_id': None}
        return dict(self.volumes_[vid])

    def delete_volume(self, vid):
        self.calls.append(('delete_volume', vid))
        self.volumes_.pop(vid, None)


@pytest.fixture
def fly(monkeypatch):
    monkeypatch.setenv('COGNITRIX_SANDBOX_FLY_REGION', 'lhr')
    monkeypatch.setenv('COGNITRIX_SANDBOX_IDLE_MINUTES', '15')
    monkeypatch.delenv('COGNITRIX_SANDBOX_FLY_IDLE', raising=False)
    api = FakeFly()
    mgr = FlyManager(image='ghcr.io/x/desktop:v1', api=api, start_reaper=False)
    monkeypatch.setattr(mgr, '_wait_healthy', lambda box, seconds=30: None)
    return mgr, api


def test_first_use_makes_a_private_machine_with_a_home_volume(fly):
    mgr, api = fly
    box = mgr.sandbox('u1')
    (create,) = (c for c in api.calls if c[0] == 'create')
    config = create[1]['config']
    assert create[1]['region'] == 'lhr' and create[1]['name'] == mgr.machine_name('u1')
    assert config['image'] == 'ghcr.io/x/desktop:v1'
    assert config['mounts'][0]['path'] == '/home/agent'
    assert 'services' not in config  # no public address
    assert config['env']['COGNITRIX_SANDBOX_LISTEN'] == '::'
    assert config['processes'][0]['user'] == 'root'  # to hand the volume to 'agent', then drop privileges
    assert config['processes'][0]['entrypoint'][-1].endswith('cognitrix-sandbox-root')
    assert box.token == config['env'][TOKEN_ENV] and len(box.token) > 20
    assert box.api == 'http://[fdaa::2]:8790' and box.viewer_url('a') == 'ws://[fdaa::2]:6080/websockify?token=a'
    assert ('create_volume', mgr.volume_name('u1'), 'lhr', 10) in api.calls
    # The same machine next time, from any manager.
    other = FlyManager(image='ghcr.io/x/desktop:v1', api=api, start_reaper=False)
    assert other.existing('u1').machine_id == box.machine_id
    assert mgr.existing('u2') is None


def test_a_sleeping_computer_wakes_on_the_next_request(fly):
    mgr, api = fly
    box = mgr.sandbox('u1')
    mgr.stop('u1')
    assert mgr.existing('u1') is None and mgr.asleep('u1')
    api.calls.clear()
    again = mgr.sandbox('u1')
    assert ('start', box.machine_id) in api.calls and again.machine_id == box.machine_id
    assert not any(c[0] in ('create', 'create_volume') for c in api.calls)
    assert not mgr.asleep('u1')


def test_a_new_pinned_image_is_used_when_it_wakes(fly):
    mgr, api = fly
    box = mgr.sandbox('u1')
    mgr.stop('u1')
    mgr.image = 'ghcr.io/x/desktop:v2'
    assert mgr.update_available(box)
    mgr.sandbox('u1')
    assert ('update', box.machine_id, 'ghcr.io/x/desktop:v2') in api.calls
    assert not mgr.update_available(mgr.existing('u1'))


def test_digest_pinned_images_are_compared_whole(fly):
    mgr, api = fly
    mgr.image = 'ghcr.io/x/desktop@sha256:aaa'
    box = mgr.sandbox('u1')
    mgr.stop('u1')
    mgr.image = 'ghcr.io/x/desktop@sha256:bbb'
    assert mgr.update_available(box)
    mgr.sandbox('u1')
    assert ('update', box.machine_id, 'ghcr.io/x/desktop@sha256:bbb') in api.calls
    # A tag Fly stored with its resolved digest still counts as the same.
    mgr.image = 'ghcr.io/x/desktop:v1'
    assert not mgr._differs('ghcr.io/x/desktop:v1@sha256:ccc')


def test_idle_computers_are_stopped_but_not_busy_or_watched_ones(fly, monkeypatch):
    mgr, api = fly
    a = mgr.sandbox('u1')
    b = mgr.sandbox('u2')
    c = mgr.sandbox('u3')
    activity = {a.machine_id: {'ok': True, 'idle_s': 3600, 'busy': []},
                b.machine_id: {'ok': True, 'idle_s': 3600, 'busy': ['a command is running']},
                c.machine_id: {'ok': True, 'idle_s': 3600, 'busy': []}}
    monkeypatch.setattr(mgr, '_activity', lambda m: activity[m['id']])
    mgr.viewer_opened('u3')  # someone has u3's screen open
    later = mgr._last_used[a.machine_id] + 20 * 60
    assert mgr.stop_idle(now=later) == [a.machine_id]
    assert api.machines_[b.machine_id]['state'] == 'started'
    mgr.viewer_closed('u3')
    assert mgr.stop_idle(now=later) == [c.machine_id]


def test_recently_used_by_the_engine_is_not_idle(fly, monkeypatch):
    mgr, api = fly
    a = mgr.sandbox('u1')
    monkeypatch.setattr(mgr, '_activity', lambda m: {'ok': True, 'idle_s': 3600, 'busy': []})
    assert mgr.stop_idle(now=mgr._last_used[a.machine_id] + 60) == []
    # An older image without /activity: the engine's own view decides.
    monkeypatch.setattr(mgr, '_activity', lambda m: None)
    assert mgr.stop_idle(now=mgr._last_used[a.machine_id] + 16 * 60) == [a.machine_id]


def test_suspend_when_asked(fly, monkeypatch):
    mgr, api = fly
    monkeypatch.setenv('COGNITRIX_SANDBOX_FLY_IDLE', 'suspend')
    a = mgr.sandbox('u1')
    monkeypatch.setattr(mgr, '_activity', lambda m: {'ok': True, 'idle_s': 3600, 'busy': []})
    mgr.stop_idle(now=mgr._last_used[a.machine_id] + 3600)
    assert api.machines_[a.machine_id]['state'] == 'suspended' and mgr.asleep('u1')
    mgr.sandbox('u1')
    assert ('start', a.machine_id) in api.calls


def test_reset_destroys_the_machine_and_its_files(fly):
    mgr, api = fly
    mgr.sandbox('u1')
    mgr.reset('u1')
    assert not api.machines_ and not api.volumes_
    assert mgr.existing('u1') is None


def test_without_fly_settings_it_says_what_is_missing(monkeypatch):
    for name in ('FLY_API_TOKEN', 'COGNITRIX_FLY_API_TOKEN', 'COGNITRIX_SANDBOX_FLY_APP', 'FLY_REGION',
                 'COGNITRIX_SANDBOX_FLY_REGION'):
        monkeypatch.delenv(name, raising=False)
    mgr = FlyManager(start_reaper=False)
    with pytest.raises(Unsupported, match='Fly API token'):
        mgr.sandbox('u1')
    settings = fly_settings()
    assert not settings['configured'] and 'Fly app name' in settings['missing']


def test_get_manager_picks_fly(monkeypatch, tmp_path):
    from cognitrix.computer.sandbox import client

    monkeypatch.setattr(client, 'SETTINGS_FILE', tmp_path / 'x.json')
    monkeypatch.setattr(client, '_manager', None)
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'fly')
    monkeypatch.setenv('COGNITRIX_SANDBOX_IDLE_MINUTES', '0')
    assert client.sandbox_mode() == 'fly' and client.get_manager().kind == 'fly'
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    assert client.get_manager().kind == 'docker'


# -- the Machines API client ----------------------------------------------------

def test_machines_api_paths_and_errors():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params),
                     request.headers.get('authorization')))
        if request.url.path.endswith('/machines/gone'):
            return httpx.Response(404, json={'error': 'not found'})
        if request.url.path.endswith('/start'):
            return httpx.Response(403, json={'error': 'unauthorized'})
        if request.method == 'POST' and request.url.path.endswith('/volumes'):
            body = json.loads(request.content)
            return httpx.Response(200, json={'id': 'vol_1', **body})
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler), headers={'Authorization': 'Bearer t'})
    api = FlyMachines('t', 'cog-sandboxes', base='https://fly.test/v1', client=client)
    assert api.machines() == []
    assert api.machine('gone') is None
    assert api.create_volume('home_x', 'lhr', 10)['encrypted'] is True
    api.wait('m1', 'started', 90)
    with pytest.raises(Unsupported, match='Fly API token'):
        api.start('m1')
    assert seen[0][:2] == ('GET', '/v1/apps/cog-sandboxes/machines') and seen[0][3] == 'Bearer t'
    assert seen[3][2] == {'state': 'started', 'timeout': '60'}


# -- supervisor /activity -------------------------------------------------------

def _free_port(family=socket.AF_INET):
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.bind(('::1' if family == socket.AF_INET6 else '127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.mark.parametrize('host', ['127.0.0.1', '::'])
def test_supervisor_reports_activity_and_busy(monkeypatch, host):
    from cognitrix.computer.sandbox import supervisor

    if host == '::' and not socket.has_ipv6:
        pytest.skip('no IPv6 here')
    monkeypatch.setenv(TOKEN_ENV, 'secret')
    sup = SimpleNamespace(desktops={})
    work = SimpleNamespace(jobs={})
    port = _free_port()
    try:
        server = supervisor.make_server(sup, port=port, host=host, work=work)
    except OSError:
        pytest.skip('cannot bind here')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{port}'  # IPv4 works on the dual-stack socket too
        assert httpx.get(url + '/activity').status_code == 401
        auth = {'Authorization': 'Bearer secret'}
        first = httpx.get(url + '/activity', headers=auth).json()
        assert first['ok'] and first['busy'] == []
        server.activity.last -= 600  # ten minutes ago
        assert httpx.get(url + '/activity', headers=auth).json()['idle_s'] >= 599  # asking doesn't count
        httpx.get(url + '/desktops', headers=auth)
        assert httpx.get(url + '/activity', headers=auth).json()['idle_s'] < 5
        sup.desktops['a'] = SimpleNamespace(held_by='Amos', info=lambda: {})
        work.jobs['j'] = SimpleNamespace(running=True)
        assert httpx.get(url + '/activity', headers=auth).json()['busy'] == ['a person has control',
                                                                              'a command is running']
    finally:
        server.shutdown()
        server.server_close()


# -- routes ---------------------------------------------------------------------

@pytest.fixture
def fly_api(monkeypatch, tmp_path):
    from cognitrix.api.routes import computer_sandbox
    from cognitrix.common.security import AuthContext, jwt_only

    monkeypatch.setattr('cognitrix.computer.sandbox.client.SETTINGS_FILE', tmp_path / 'x.json')
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'fly')
    monkeypatch.setenv('COGNITRIX_SANDBOX_FLY_APP', 'cog-sandboxes')
    monkeypatch.setenv('COGNITRIX_FLY_API_TOKEN', 'secret-token')
    monkeypatch.setenv('COGNITRIX_SANDBOX_FLY_REGION', 'lhr')
    made = []
    box = SimpleNamespace(desktops=lambda: [{'name': n} for n in made], desktop=lambda n: made.append(n))
    state = {'asleep': True}
    mgr = SimpleNamespace(existing=lambda owner: None if state['asleep'] else box,
                          asleep=lambda owner: state['asleep'],
                          sandbox=lambda owner: (state.update(asleep=False), box)[1],
                          update_available=lambda b: False)
    monkeypatch.setattr('cognitrix.computer.sandbox.client.get_manager', lambda: mgr)
    app = FastAPI()
    app.include_router(computer_sandbox.sandbox_api)
    app.dependency_overrides[jwt_only] = lambda: AuthContext(user=SimpleNamespace(id='u1', name='Amos'))
    return TestClient(app)


def test_status_says_asleep_and_wake_starts_it(fly_api):
    data = fly_api.get('/computer/sandbox').json()
    assert data == {'enabled': True, 'running': False, 'desktops': [], 'host': 'fly', 'asleep': True}
    # A freshly started computer has no desktops: waking opens the one asked for.
    assert fly_api.post('/computer/sandbox/wake', json={'desktop': 'agent-a'}).json() == {
        'ok': True, 'desktops': [{'name': 'agent-a'}]}
    assert fly_api.post('/computer/sandbox/wake').json()['desktops'] == [{'name': 'agent-a'}]
    assert fly_api.get('/computer/sandbox').json()['running'] is True


def test_settings_show_fly_without_the_token(fly_api):
    data = fly_api.get('/computer/sandbox/settings').json()
    assert data['host'] == 'fly' and data['from_env'] and data['fly']['configured']
    assert data['fly']['app'] == 'cog-sandboxes' and 'secret-token' not in json.dumps(data)


def test_docker_only_calls_say_so_on_fly(fly):
    mgr, _ = fly
    with pytest.raises(Unsupported, match='Fly'):
        mgr._docker('exec', 'x')


# -- running scripts in the computer over /exec (snapshots, key proxy) ----------

@pytest.fixture
def exec_box(monkeypatch, tmp_path):
    """A real supervisor (exec routes only) on localhost, and a Sandbox for it."""
    from cognitrix.computer.sandbox import supervisor, workspace
    from cognitrix.computer.sandbox.client import Sandbox

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(workspace, 'HOME', str(home))
    monkeypatch.setattr(supervisor, 'STATE', str(tmp_path))
    monkeypatch.setenv(TOKEN_ENV, 'secret')
    port = _free_port()
    server = supervisor.make_server(SimpleNamespace(desktops={}), port=port, host='127.0.0.1',
                                    work=SimpleNamespace(jobs={}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    box = Sandbox('cognitrix-sandbox-x', 'secret', port, 6080)
    try:
        yield box, home
    finally:
        server.shutdown()
        server.server_close()


def test_exec_runs_scripts_as_the_computer_with_input(fly, exec_box):
    mgr, _ = fly
    box, home = exec_box
    code, out, err = mgr.exec_script(box, 'cat > got.txt && echo saved && echo warn >&2', input=b'pem')
    assert (code, out.strip(), err.strip()) == (0, 'saved', 'warn')
    assert (home / 'got.txt').read_bytes() == b'pem'
    assert mgr.exec_script(box, 'exit 3')[0] == 3


def test_exec_streams_big_output_and_input(fly, exec_box, tmp_path):
    import io

    mgr, _ = fly
    box, home = exec_box
    data = bytes(range(256)) * 20000  # ~5 MB
    (home / 'big.bin').write_bytes(data)
    out = io.BytesIO()
    code, err = mgr.exec_out(box, 'cat big.bin; echo done >&2', out)
    assert code == 0 and out.getvalue() == data and err.strip() == 'done'
    src = tmp_path / 'src.bin'
    src.write_bytes(data[::-1])
    with open(src, 'rb') as fh:
        code, err = mgr.exec_in(box, 'cat > copy.bin', fh)
    assert code == 0 and (home / 'copy.bin').read_bytes() == data[::-1]
    with open(src, 'rb') as fh:
        assert mgr.exec_in(box, 'exit 5', fh)[0] == 5  # stops reading early: still answers


def test_exec_on_an_older_image_says_update(fly, monkeypatch):
    from cognitrix.computer.sandbox.client import Sandbox

    mgr, _ = fly
    box = Sandbox('x', 't', 1, 2)
    box._client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(404, json={'ok': False, 'error': 'not_found'})))
    with pytest.raises(Unsupported, match='update it'):
        mgr.exec_script(box, 'true')


def test_restore_replaces_home_then_stops_the_machine(fly, monkeypatch):
    import io

    mgr, api = fly
    box = mgr.sandbox('u1')
    closed, ran = [], []
    monkeypatch.setattr(type(box), 'desktops', lambda self: [{'name': 'main'}, {'name': 'agent-a'}])
    monkeypatch.setattr(type(box), 'request', lambda self, m, path, body=None, timeout=None: closed.append((m, path)))
    monkeypatch.setattr(mgr, 'exec_in', lambda b, script, src, timeout=3600: (ran.append(script), (0, ''))[1])
    mgr.replace_home('u1', io.BytesIO(b'tgz'))
    assert closed == [('DELETE', '/desktops/main'), ('DELETE', '/desktops/agent-a')]
    assert 'find . -mindepth 1' in ran[0] and '! -name .cognitrix-tailscale' in ran[0] and 'tar -xzf -' in ran[0]
    mid = next(iter(api.machines_))
    assert api.machines_[mid]['state'] == 'stopped'
    assert mgr.sandbox('u1').container_id == mid  # wakes again for the desktops to reopen


def test_key_proxy_listens_on_the_private_network(fly, monkeypatch):
    mgr, _ = fly
    for name in ('COGNITRIX_SANDBOX_PROXY_BIND', 'COGNITRIX_SANDBOX_PROXY_HOST', 'FLY_PRIVATE_IP', 'FLY_APP_NAME'):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(Unsupported, match='PROXY_HOST'):
        mgr._proxy_addresses()
    monkeypatch.setenv('FLY_APP_NAME', 'cognitrix')
    assert mgr._proxy_addresses() == ('::', 'cognitrix.internal')
    mgr._proxy_where = None
    monkeypatch.setenv('FLY_PRIVATE_IP', 'fdaa:0:1::2')
    assert mgr._proxy_addresses() == ('::', '[fdaa:0:1::2]')


def test_hidden_keys_reach_a_fly_computer(fly, monkeypatch):
    from cognitrix.common import key_proxy

    mgr, _ = fly
    monkeypatch.setenv('FLY_PRIVATE_IP', 'fdaa::9')
    started, scripts = [], []
    fake = SimpleNamespace(ensure_started=lambda bind: (started.append(bind), (bind, 4242))[1],
                           ca=SimpleNamespace(pem=b'PEM', fingerprint='fp'), token_for=lambda key: 'tok')
    monkeypatch.setattr(key_proxy, 'get_proxy', lambda: fake)
    monkeypatch.setattr(mgr, 'exec_script', lambda box, script, input=None, timeout=120:
                        (scripts.append(input), (0, '', ''))[1])
    env = mgr.key_proxy_env('u1')
    assert started == ['::'] and scripts == [b'PEM']
    assert 'cognitrix:tok@[fdaa::9]:4242' in json.dumps(env)
    mgr.key_proxy_env('u1')
    assert scripts == [b'PEM']  # the certificate is installed once per machine
