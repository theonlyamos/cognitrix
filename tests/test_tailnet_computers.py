"""Fly computers on the tailnet (cognitrix/tailscale/computers.py, api.py,
outbound.py and the Fly manager's use of them). Tailscale's web API, its
client and Fly are stand-ins; nothing here needs a tailnet."""
from __future__ import annotations

import json
import os
import shutil
import socket
import socketserver
import struct
import subprocess
import threading
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from cognitrix import app_settings, tailscale
from cognitrix.computer import DriverError, Unsupported
from cognitrix.computer.sandbox.client import user_key
from cognitrix.computer.sandbox.fly import META_TAILNET, FlyManager
from cognitrix.tailscale import api, cli, computers, daemon, outbound, service
from tests.test_sandbox_fly import FakeFly

TAG = 'tag:cognitrix-sandbox'


class FakeTailscaleApi:
    """Tailscale's web API: OAuth tokens, join keys, devices."""

    def __init__(self):
        self.secret = 'tskey-client-secret'
        self.allowed_tags = {TAG}
        self.keys: list[dict] = []
        self.devices_: list[dict] = []
        self.removed: list[str] = []
        self.tokens = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/oauth/token'):
            form = parse_qs(request.content.decode())
            if form.get('client_secret') != [self.secret]:
                return httpx.Response(401, json={'message': 'invalid client credentials'})
            self.tokens += 1
            return httpx.Response(200, json={'access_token': f'tok-{self.tokens}', 'expires_in': 3600})
        if request.headers.get('Authorization') != f'Bearer tok-{self.tokens}':
            return httpx.Response(401, json={'message': 'bad token'})
        if path.endswith('/tailnet/-/keys') and request.method == 'POST':
            body = json.loads(request.content)
            create = body['capabilities']['devices']['create']
            if not set(create['tags']) <= self.allowed_tags:
                return httpx.Response(403, json={'message': 'requested tags are invalid or not permitted'})
            self.keys.append(body)
            return httpx.Response(200, json={'id': f'k{len(self.keys)}', 'key': f'tskey-auth-{len(self.keys)}'})
        if path.endswith('/tailnet/-/devices'):
            return httpx.Response(200, json={'devices': self.devices_})
        if '/device/' in path and request.method == 'DELETE':
            self.removed.append(path.rsplit('/', 1)[1])
            return httpx.Response(200)
        return httpx.Response(404, json={'message': 'not found'})


class Tailnet:
    """What this engine's own Tailscale reports (``tailscale status --json``)."""

    def __init__(self):
        self.state = 'Running'
        self.peers: dict[str, dict] = {}
        self.self_tags: list[str] = []

    def add(self, name: str, ip: str, tags=(TAG,), online=True, created='2026-11-27T10:00:00Z'):
        self.peers[f'node-{len(self.peers)}'] = {
            'ID': f'n{len(self.peers)}', 'HostName': name, 'DNSName': f'{name}.tail1234.ts.net.',
            'TailscaleIPs': [ip, 'fd7a:115c:a1e0::1'], 'Tags': list(tags), 'Online': online, 'Created': created}

    def status(self):
        return {'BackendState': self.state, 'Peer': self.peers,
                'Self': {'DNSName': 'studio.tail1234.ts.net.', 'TailscaleIPs': ['100.64.0.7'],
                         'Tags': self.self_tags}}


@pytest.fixture
def ts_api(monkeypatch):
    fake = FakeTailscaleApi()
    monkeypatch.setattr(api, '_transport', httpx.MockTransport(fake))
    api.forget_token()
    return fake


@pytest.fixture
def tailnet(monkeypatch):
    net = Tailnet()
    monkeypatch.setattr(cli, 'status', net.status)
    monkeypatch.setattr(computers, '_status_cache', None)
    monkeypatch.setattr(computers, '_STATUS_FRESH_S', 0.0)
    monkeypatch.setattr(service, 'mode', lambda: service.SYSTEM)
    monkeypatch.setattr(daemon, 'running', lambda: False)
    monkeypatch.setattr(app_settings, '_env_fallback', False)
    for name in ('FLY_PRIVATE_IP', 'FLY_APP_NAME', 'COGNITRIX_SANDBOX_PROXY_BIND', 'COGNITRIX_SANDBOX_PROXY_HOST'):
        monkeypatch.delenv(name, raising=False)
    return net


def _turn_on(scope='everyone', **more):
    app_settings.set_many({'COGNITRIX_TAILSCALE': True, 'COGNITRIX_TAILSCALE_SANDBOX': scope,
                           'COGNITRIX_TAILSCALE_OAUTH_ID': 'client-1',
                           'COGNITRIX_TAILSCALE_OAUTH_SECRET': 'tskey-client-secret',
                           'COGNITRIX_SANDBOX_FLY_REGION': 'lhr', **more})


@pytest.fixture
def fly(monkeypatch, tailnet, ts_api):
    app_settings.set_many({'COGNITRIX_SANDBOX_FLY_REGION': 'lhr'})
    fake = FakeFly()
    mgr = FlyManager(image='ghcr.io/x/desktop:v1', api=fake, start_reaper=False)
    monkeypatch.setattr(mgr, '_wait_healthy', lambda box, seconds=30: None)
    monkeypatch.setattr(computers.time, 'sleep', lambda s: None)
    monkeypatch.setattr(computers, 'JOIN_WAIT_S', 0.05)
    return mgr, fake


# ---------------------------------------------------------------------------
# Tailscale's web API
# ---------------------------------------------------------------------------

def test_a_join_key_works_once_for_ten_minutes_with_the_tag(ts_api, tailnet):
    _turn_on()
    assert api.configured()
    key = api.join_key(TAG, 'Cognitrix computer abc/123')
    assert key == 'tskey-auth-1'
    (body,) = ts_api.keys
    assert body['capabilities']['devices']['create'] == {
        'reusable': False, 'ephemeral': False, 'preauthorized': True, 'tags': [TAG]}
    assert body['expirySeconds'] == 600 and body['description'] == 'Cognitrix computer abc123'
    api.join_key(TAG)
    assert ts_api.tokens == 1  # the access token is kept for its hour


def test_api_says_what_is_wrong(ts_api, tailnet):
    with pytest.raises(api.ApiError) as none:
        api.join_key(TAG)
    assert none.value.code == 'not_configured'
    _turn_on(COGNITRIX_TAILSCALE_OAUTH_SECRET='wrong')
    with pytest.raises(api.ApiError) as refused:
        api.join_key(TAG)
    assert refused.value.code == 'refused' and 'ID and secret' in str(refused.value)
    _turn_on()
    ts_api.allowed_tags = set()
    with pytest.raises(api.ApiError) as tag:
        api.join_key(TAG)
    assert tag.value.code == 'not_allowed' and 'tag' in str(tag.value)


def test_the_secret_is_never_shown(ts_api, tailnet):
    _turn_on()
    shown = computers.view()
    assert shown['oauth_id'] == 'client-1' and shown['oauth_saved'] is True
    assert 'tskey-client-secret' not in json.dumps(shown)
    assert all(row.get('value') in ('', None) or 'tskey' not in str(row.get('value'))
               for row in app_settings.describe('tailscale'))


# ---------------------------------------------------------------------------
# Whose computers join, and finding them
# ---------------------------------------------------------------------------

def test_nobody_joins_until_turned_on_and_admins_only_when_asked(tailnet):
    assert computers.wanted_for(user_key('u1')) is False
    _turn_on('everyone')
    assert computers.wanted_for(user_key('u1')) is True
    _turn_on('admins')
    assert computers.wanted_for(user_key('u1')) is False
    computers._members_file().write_text(json.dumps({'admins': [user_key('u1')]}), encoding='utf-8')
    assert computers.wanted_for(user_key('u1')) is True and computers.wanted_for(user_key('u2')) is False
    app_settings.set_many({'COGNITRIX_TAILSCALE': False})
    assert computers.wanted_for(user_key('u1')) is False  # Tailscale off turns it off


def test_only_a_machine_with_the_tag_is_the_computer(tailnet):
    _turn_on()
    key = user_key('u1')
    tailnet.add(computers.node_name(key), '100.64.0.66', tags=())            # took the name, no tag
    tailnet.add(computers.node_name(key), '100.64.0.67', tags=('tag:other',))
    assert computers.find(key) is None
    tailnet.add(computers.node_name(key), '100.64.0.20', online=False, created='2026-11-01T00:00:00Z')
    tailnet.add(computers.node_name(key), '100.64.0.21')
    assert computers.find(key)['ip'] == '100.64.0.21'  # the live one, not what a reset left behind
    assert computers.reach(key) == ('100.64.0.21', 8790, 6080)
    tailnet.state = 'NeedsLogin'
    with pytest.raises(computers.ComputersError, match="isn't connected to Tailscale"):
        computers.reach(key)


def test_the_policy_snippet_names_this_server_and_the_tag(tailnet):
    _turn_on(COGNITRIX_TAILSCALE_SANDBOX_TAG='Agents-Box')
    text = computers.policy_snippet()
    assert '"tag:agents-box": ["autogroup:admin"]' in text
    assert '{"src": ["100.64.0.7"], "dst": ["tag:agents-box"], "ip": ["tcp:8790", "tcp:6080"]}' in text
    tailnet.self_tags = ['tag:server']
    assert '{"src": ["tag:agents-box"], "dst": ["tag:server"], "ip": ["tcp:8791-8798"]}' in computers.policy_snippet()


def test_check_tries_the_oauth_client(ts_api, tailnet):
    _turn_on()
    assert computers.check() == {'ok': True, 'steps': [
        {'name': 'oauth', 'ok': True, 'detail': ''}, {'name': 'join_key', 'ok': True, 'detail': ''},
        {'name': 'engine', 'ok': True, 'detail': ''}]}
    ts_api.allowed_tags = set()
    out = computers.check()
    assert out['ok'] is False and [s['ok'] for s in out['steps']] == [True, False, True]


# ---------------------------------------------------------------------------
# The Fly manager
# ---------------------------------------------------------------------------

def test_a_new_computer_joins_and_is_dialled_at_its_tailnet_address(fly, tailnet, ts_api):
    mgr, fake = fly
    _turn_on()
    tailnet.add(computers.node_name(user_key('u1')), '100.64.0.21')
    box = mgr.sandbox('u1')
    (create,) = (c for c in fake.calls if c[0] == 'create')
    config = create[1]['config']
    assert config['env'][computers.ENV_ON] == '1'
    assert config['env'][computers.ENV_NAME] == computers.node_name(user_key('u1'))
    assert config['env'][computers.ENV_KEY] == 'tskey-auth-1'
    assert config['metadata'][META_TAILNET] == '1'
    assert box.api == 'http://100.64.0.21:8790' and box.viewer_url('a').startswith('ws://100.64.0.21:6080/')
    assert mgr.tailnet_info('u1') == {'name': computers.node_name(user_key('u1')),
                                      'dns': f"{computers.node_name(user_key('u1'))}.tail1234.ts.net", 'online': True}
    # A stop and a wake keep the machine's settings: no second key.
    mgr.stop('u1')
    assert mgr.sandbox('u1').api == box.api and len(ts_api.keys) == 1


def test_without_the_setting_nothing_changes(fly, tailnet, ts_api):
    mgr, fake = fly
    box = mgr.sandbox('u1')
    config = next(c for c in fake.calls if c[0] == 'create')[1]['config']
    assert computers.ENV_KEY not in config['env'] and META_TAILNET not in config['metadata']
    assert box.api.startswith('http://[fdaa::') and not ts_api.keys
    assert mgr.tailnet_info('u1') is None


def test_an_older_machine_joins_when_it_is_next_needed(fly, tailnet, ts_api, monkeypatch):
    mgr, fake = fly
    mgr.sandbox('u1')                      # made before the tailnet was turned on
    monkeypatch.setattr(fake, 'update', lambda mid, body: (fake.calls.append(('update', mid, body['config'])),
                                                           fake.machines_[mid].update(config=body['config']))[0])
    _turn_on()
    tailnet.add(computers.node_name(user_key('u1')), '100.64.0.21')
    box = mgr.sandbox('u1')
    (update,) = (c for c in fake.calls if c[0] == 'update')
    assert update[2]['env'][computers.ENV_KEY] == 'tskey-auth-1' and update[2]['metadata'][META_TAILNET] == '1'
    assert box.api == 'http://100.64.0.21:8790'


def test_a_running_machine_is_left_alone_when_the_engine_is_on_fly(fly, tailnet, ts_api, monkeypatch):
    mgr, fake = fly
    mgr.sandbox('u1')
    monkeypatch.setenv('FLY_PRIVATE_IP', 'fdaa:0:1::2')
    _turn_on()
    box = mgr.sandbox('u1')
    assert not [c for c in fake.calls if c[0] == 'update'] and not ts_api.keys  # it joins when it next wakes
    assert box.api.startswith('http://[fdaa::')  # and the engine keeps using Fly's own network


def test_a_computer_that_fell_off_the_tailnet_gets_one_new_key(fly, tailnet, ts_api, monkeypatch):
    mgr, fake = fly
    _turn_on()
    key = user_key('u1')

    def update(mid, body):
        fake.calls.append(('update', mid, body['config']))
        fake.machines_[mid]['config'] = body['config']
        tailnet.add(computers.node_name(key), '100.64.0.30')  # the new key lets it back on

    monkeypatch.setattr(fake, 'update', update)
    box = mgr.sandbox('u1')
    assert len(ts_api.keys) == 2 and box.api == 'http://100.64.0.30:8790'
    # Never a third: a computer the policy hides says so instead of restarting for ever.
    tailnet.peers.clear()
    mgr._boxes.clear()
    with pytest.raises(DriverError, match='tailnet policy'):
        mgr.sandbox('u1')
    assert len(ts_api.keys) == 2


def test_a_computer_that_no_longer_joins_is_taken_off_the_tailnet(fly, tailnet, ts_api, monkeypatch):
    """The choice went from everyone to nobody: a computer that joined before loses
    its join settings (its start script then deletes the saved identity) and its
    place on the tailnet, the next time it is needed."""
    mgr, fake = fly
    _turn_on()
    name = computers.node_name(user_key('u1'))
    tailnet.add(name, '100.64.0.21')
    mgr.sandbox('u1')
    monkeypatch.setattr(fake, 'update', lambda mid, body: (fake.calls.append(('update', mid, body['config'])),
                                                           fake.machines_[mid].update(config=body['config']))[0])
    ts_api.devices_ = [{'nodeId': 'nAAA', 'hostname': name, 'tags': [TAG]}]
    monkeypatch.setenv('FLY_PRIVATE_IP', 'fdaa:0:1::2')      # the engine still reaches it on Fly's own network
    _turn_on('nobody')
    box = mgr.sandbox('u1')
    (update,) = (c for c in fake.calls if c[0] == 'update')
    assert not {computers.ENV_ON, computers.ENV_NAME, computers.ENV_KEY} & set(update[2]['env'])
    assert update[2]['env']['COGNITRIX_SANDBOX_TOKEN'] and META_TAILNET not in update[2]['metadata']
    assert ts_api.removed == ['nAAA'] and len(ts_api.keys) == 1
    assert box.api.startswith('http://[fdaa::') and mgr.tailnet_info('u1') is None
    # Once is enough: the next use changes nothing more.
    mgr.sandbox('u1')
    assert len([c for c in fake.calls if c[0] == 'update']) == 1 and ts_api.removed == ['nAAA']


def test_changing_who_joins_takes_the_others_off_at_once(tailnet, ts_api, tmp_path, monkeypatch):
    """Saving the choice removes the machines it leaves out from the tailnet there
    and then, whatever those computers still have saved."""
    monkeypatch.setattr(tailscale, 'folder', lambda: tmp_path)
    mine, theirs = computers.node_name(user_key('admin-1')), computers.node_name(user_key('u2'))
    ts_api.devices_ = [{'nodeId': 'nMINE', 'hostname': mine, 'tags': [TAG]},
                       {'nodeId': 'nTHEIRS', 'hostname': theirs, 'tags': [TAG]},
                       {'nodeId': 'nNAME', 'hostname': theirs, 'tags': []},                 # took the name, not a computer
                       {'nodeId': 'nOTHER', 'hostname': 'build-box', 'tags': [TAG]}]        # tagged, not a computer
    _turn_on()
    assert computers.remove_unwanted() == [] and ts_api.removed == []

    # Admins only, before anyone wrote down who the admins are: nobody is removed by mistake.
    _turn_on('admins')
    assert computers.remove_unwanted() == []
    (tmp_path / 'members.json').write_text(json.dumps({'admins': [user_key('admin-1')]}))
    assert computers.remove_unwanted() == [theirs] and ts_api.removed == ['nTHEIRS']

    _turn_on('nobody')
    assert computers.remove_unwanted() == [mine, theirs]
    # Tailscale itself turned off counts as nobody.
    ts_api.removed.clear()
    _turn_on('everyone', COGNITRIX_TAILSCALE=False)
    assert computers.remove_unwanted() == [mine, theirs] and ts_api.removed == ['nMINE', 'nTHEIRS']
    # No OAuth client: nothing to ask.
    _turn_on('nobody', COGNITRIX_TAILSCALE_OAUTH_ID='')
    assert computers.remove_unwanted() == []


ROOT_SCRIPT = Path(__file__).resolve().parents[1] / 'sandbox' / 'desktop' / 'bin' / 'cognitrix-sandbox-root'


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('sh'), reason='the start script is a POSIX shell script')
def test_the_start_script_uses_a_replacement_key_and_forgets_an_identity_it_no_longer_needs(tmp_path):
    """The computer's start script with stand-in tailscale programs: an ordinary wake
    leaves a working identity alone, a key it has not tried is used even then
    (the tag changed), and a computer that no longer joins deletes what it saved."""
    home, run, fake = tmp_path / 'home', tmp_path / 'run', tmp_path / 'fake'
    for folder in (home, run, fake):
        folder.mkdir()
    text = ROOT_SCRIPT.read_text()
    for old, new in (('/home/agent', str(home)), ('/run/tailscale', str(run / 'tailscale')),
                     ('/run/cognitrix-tailnet', str(run / 'keys')), ('/var/log/tailscaled.log', str(tmp_path / 'log'))):
        assert old in text
        text = text.replace(old, new)
    # Only the Tailscale part: what follows hands over to the sandbox itself.
    head, rest = text.split('if [ "$(id -u)" = "0" ]; then', 1)
    part = rest.split('    unset COGNITRIX_TAILNET_AUTHKEY', 1)[0]
    assert 'start_tailscale' in part and 'chown agent:agent' in part
    part = part.split('    fi\n', 1)[1]               # past handing the home folder to the agent user
    script = tmp_path / 'start'
    script.write_text(head + part)
    (fake / 'state').write_text('NeedsLogin')
    programs = {
        'tailscaled': 'for a in "$@"; do case "$a" in --socket=*) python3 -c "import socket,sys; '
                      's=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1])" "${a#--socket=}";; esac; done',
        'tailscale': f'''case " $* " in
  *" status "*) printf '{{"BackendState": "%s"}}' "$(cat {fake}/state)" ;;
  *" up "*) for a in "$@"; do case "$a" in --auth-key=file:*) key=$(cat "${{a#--auth-key=file:}}");; esac; done
            echo "$key $*" >> {fake}/ups; printf Running > {fake}/state ;;
esac''',
        'chown': 'exit 0',
        'stat': 'echo 0',        # root's own folder, as on the real machine
    }
    for name, body in programs.items():
        (fake / name).write_text('#!/bin/sh\n' + body + '\n')
        (fake / name).chmod(0o755)

    def start(**env):
        for sock in (run / 'tailscale').glob('*.sock'):
            sock.unlink()
        subprocess.run(['sh', str(script)], check=True, timeout=60,
                       env={'PATH': f"{fake}{os.pathsep}{os.environ['PATH']}", **env})
        return (fake / 'ups').read_text().splitlines() if (fake / 'ups').exists() else []

    joining = {'COGNITRIX_TAILNET': '1', 'COGNITRIX_TAILNET_NAME': 'cognitrix-sandbox-u1'}
    state = home / '.cognitrix-tailscale'
    ups = start(**joining, COGNITRIX_TAILNET_AUTHKEY='tskey-auth-1')
    assert len(ups) == 1 and ups[0].startswith('tskey-auth-1 ') and '--force-reauth' not in ups[0]
    assert '--hostname=cognitrix-sandbox-u1' in ups[0] and not (run / 'keys' / 'join-key').exists()
    assert 'tskey-auth-1' not in (state / 'cognitrix-join-key.sha256').read_text()
    # A stop and a wake: the same, used key and a working identity. Nothing to do.
    assert len(start(**joining, COGNITRIX_TAILNET_AUTHKEY='tskey-auth-1')) == 1
    # The engine sends another key (the tag changed): signed in again with it, though the old identity works.
    ups = start(**joining, COGNITRIX_TAILNET_AUTHKEY='tskey-auth-2')
    assert len(ups) == 2 and ups[1].startswith('tskey-auth-2 ') and '--force-reauth' in ups[1]
    assert len(start(**joining, COGNITRIX_TAILNET_AUTHKEY='tskey-auth-2')) == 2
    # Signed in with nothing remembered about its key: left alone, and the key is remembered from now on.
    (state / 'cognitrix-join-key.sha256').unlink()
    assert len(start(**joining, COGNITRIX_TAILNET_AUTHKEY='tskey-auth-2')) == 2
    assert (state / 'cognitrix-join-key.sha256').exists()
    # The identity stopped working (removed from the tailnet) and the key is the old one: tried, as before.
    (fake / 'state').write_text('NeedsLogin')
    ups = start(**joining, COGNITRIX_TAILNET_AUTHKEY='tskey-auth-2')
    assert len(ups) == 3 and '--force-reauth' not in ups[2]
    # No longer meant to join: the saved identity is deleted and Tailscale isn't started.
    assert state.is_dir()
    assert len(start()) == 3 and not state.exists()


def test_a_computer_left_off_the_tailnet_says_who_can_change_that(fly, tailnet, ts_api):
    mgr, _ = fly
    _turn_on('admins')
    with pytest.raises(Unsupported, match='An admin can change'):
        mgr.sandbox('u2')


def test_no_oauth_client_says_so(fly, tailnet, ts_api):
    mgr, _ = fly
    _turn_on(COGNITRIX_TAILSCALE_OAUTH_ID='')
    with pytest.raises(Unsupported, match='OAuth client'):
        mgr.sandbox('u1')


def test_reset_takes_the_computer_off_the_tailnet(fly, tailnet, ts_api):
    mgr, fake = fly
    _turn_on()
    name = computers.node_name(user_key('u1'))
    tailnet.add(name, '100.64.0.21')
    mgr.sandbox('u1')
    ts_api.devices_ = [{'nodeId': 'nAAA', 'hostname': name, 'tags': [TAG]},
                       {'nodeId': 'nBBB', 'hostname': name, 'tags': []},          # same name, someone's own
                       {'nodeId': 'nCCC', 'hostname': 'cognitrix-sandbox-other', 'tags': [TAG]}]
    mgr.reset('u1')
    assert ts_api.removed == ['nAAA'] and not fake.machines_


def test_hidden_keys_proxy_listens_on_the_tailnet_address(fly, tailnet, monkeypatch):
    mgr, _ = fly
    _turn_on()
    assert mgr._proxy_addresses() == ('100.64.0.7', '100.64.0.7')
    # The built-in Tailscale hands tailnet connections to this machine's loopback.
    mgr._proxy_where = None
    monkeypatch.setattr(outbound, 'proxy_port', lambda: 1055)
    assert mgr._proxy_addresses() == ('127.0.0.1', '100.64.0.7')


def test_hidden_keys_proxy_uses_the_ports_the_policy_names(fly, tailnet, monkeypatch):
    import socket

    from cognitrix.common import key_proxy

    mgr, _ = fly
    assert mgr._proxy_ports() == ()                      # on Fly's own network: any free port
    _turn_on()
    assert mgr._proxy_ports() == computers.PROXY_PORTS == tuple(range(8791, 8799))
    low, high = computers.PROXY_PORTS[0], computers.PROXY_PORTS[-1]
    assert f'"dst": ["100.64.0.7"], "ip": ["tcp:{low}-{high}"]' in computers.policy_snippet()

    # Each Cognitrix process on the machine runs its own proxy: the second takes the next port.
    taken = socket.socket()
    taken.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        taken.bind(('127.0.0.1', 0))
        first = taken.getsockname()[1]
        taken.listen(1)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            second = probe.getsockname()[1]
        proxy = key_proxy.KeyProxy()
        monkeypatch.setattr(key_proxy.KeyProxy, 'ca', property(lambda self: None))
        try:
            assert proxy.ensure_started('127.0.0.1', (first, second)) == ('127.0.0.1', second)
        finally:
            proxy.stop()
        only = key_proxy.KeyProxy()
        with pytest.raises(OSError):
            only.ensure_started('127.0.0.1', (first,))
    finally:
        taken.close()


def test_idle_check_asks_over_the_tailnet(fly, tailnet, ts_api, monkeypatch):
    mgr, fake = fly
    _turn_on()
    tailnet.add(computers.node_name(user_key('u1')), '100.64.0.21')
    mgr.sandbox('u1')
    asked = []
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: (asked.append(url), httpx.Response(
        200, json={'ok': True, 'busy': True}))[1])
    assert mgr._activity(next(iter(fake.machines_.values()))) == {'ok': True, 'busy': True}
    assert asked == ['http://100.64.0.21:8790/activity']


# ---------------------------------------------------------------------------
# Dialling through the built-in Tailscale
# ---------------------------------------------------------------------------

class _Socks(socketserver.BaseRequestHandler):
    """A SOCKS5 proxy that only knows one tailnet address."""

    def handle(self):
        self.request.recv(3)
        self.request.sendall(b'\x05\x00')
        head = self.request.recv(4)
        host = socket.inet_ntoa(self.request.recv(4)) if head[3] == 1 else ''
        (port,) = struct.unpack('>H', self.request.recv(2))
        target = self.server.tailnet.get((host, port))
        if target is None:
            self.request.sendall(b'\x05\x04\x00\x01' + bytes(6))
            return
        self.request.sendall(b'\x05\x00\x00\x01' + bytes(6))
        with socket.create_connection(target) as upstream:
            outbound._pump(self.request, upstream)


def test_tailnet_addresses_are_dialled_through_the_builtin_proxy(monkeypatch):
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request.recv(100)
            self.request.sendall(b'computer says ' + data)

    servers = []
    try:
        echo = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo)
        socks = socketserver.ThreadingTCPServer(('127.0.0.1', 0), _Socks)
        socks.tailnet = {('100.64.0.21', 8790): echo.server_address}
        for server in (echo, socks):
            server.daemon_threads = True
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()

        monkeypatch.setattr(outbound, 'proxy_port', lambda: 0)
        assert outbound.reach('100.64.0.21', 8790) == ('100.64.0.21', 8790)  # the machine's own Tailscale: straight

        monkeypatch.setattr(outbound, 'proxy_port', lambda: socks.server_address[1])
        host, port = outbound.reach('100.64.0.21', 8790)
        assert host == '127.0.0.1' and outbound.reach('100.64.0.21', 8790) == (host, port)  # one listener per address
        with socket.create_connection((host, port), timeout=5) as conn:
            conn.sendall(b'hello')
            assert conn.recv(100) == b'computer says hello'
        # An address the tailnet doesn't answer for closes without a word.
        host, port = outbound.reach('100.64.0.99', 8790)
        with socket.create_connection((host, port), timeout=5) as conn:
            assert conn.recv(100) == b''
    finally:
        outbound.close_all()
        for server in servers:
            server.shutdown()
            server.server_close()


def test_settings_keep_the_choices(tailnet):
    assert tailscale.sandbox_choice() == 'nobody' and tailscale.devices_choice() == 'off'
    app_settings.set_many({'COGNITRIX_TAILSCALE_SANDBOX': 'admins', 'COGNITRIX_TAILSCALE_DEVICES': 'everyone'})
    assert tailscale.sandbox_choice() == 'admins' and tailscale.sandbox_scope() == 'nobody'  # Tailscale is off
    assert tailscale.devices_choice() == 'everyone' and tailscale.devices_scope() == 'off'
    assert tailscale.sandbox_tag() == TAG
