"""The agents' sandboxed computer: driver sandbox mode, the worker wire format,
RemoteDriver, approvals and the live screen API. Runs anywhere (fake backend,
no Docker); the live container test is tests/test_sandbox_computer_live.py."""
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import cognitrix.computer as computer
from cognitrix.computer import CONFIRMED, Driver, HumanControl, Stale
from cognitrix.computer.sandbox import desktop_name
from cognitrix.computer.sandbox.client import RemoteDriver
from cognitrix.computer.sandbox.worker import Worker
from cognitrix.safety.destructive_ops import DestructiveOpDetector, RiskLevel
from tests.test_background_computer_use import FakeBackend


@pytest.fixture(autouse=True)
def _no_saved_setting(tmp_path, monkeypatch):
    # Only the environment variable switches the sandbox on in these tests.
    monkeypatch.setattr('cognitrix.computer.sandbox.client.SETTINGS_FILE', tmp_path / 'computer-sandbox.json')


@pytest.fixture
def fake():
    return FakeBackend()


@pytest.fixture
def sandboxed(fake):
    d = Driver(fake, sandbox=True)
    d.settle_s = 0
    d.open_watch_s = 0
    return d


class _Box:
    """Stands in for a user's container: calls go straight to a Worker."""

    def __init__(self, worker: Worker):
        self.worker = worker
        self.calls = []

    def request(self, method, path, body=None, timeout=None):
        self.calls.append((method, path))
        return self.worker.call(body['method'], body.get('args') or {}, body.get('actor'))

    def desktop(self, name):
        return {'name': name}


@pytest.fixture
def remote(sandboxed):
    return RemoteDriver(_Box(Worker(sandboxed)), 'agent-a')


def test_desktop_names_are_safe():
    assert desktop_name('7C886528-E9CF') == '7c886528-e9cf'
    assert desktop_name(None) == 'main'
    assert desktop_name('../../etc') == 'etc'
    assert desktop_name('A b/c') == 'a-b-c'
    assert len(desktop_name('x' * 200)) <= 48


def test_sandbox_types_and_presses_keys_with_real_input(sandboxed, fake):
    fake.fg = 2
    fake.idle = 0.0  # in a sandbox nobody else is using the screen, so it doesn't wait
    sandboxed.look(3)
    result = sandboxed.press_key(3, 'ctrl+l')
    assert result.status == CONFIRMED
    assert ('screen-key', 'l', ('ctrl',)) in fake.posted
    t0 = time.monotonic()
    sandboxed.type_text(2, 'hi')
    assert time.monotonic() - t0 < 1
    assert ('screen-char', 'h') in fake.posted


def test_sandbox_fills_a_field_the_app_wont_set(sandboxed, fake):
    fake.accept_value = False
    fake.trees[2][0].rect = (10, 10, 300, 40)
    _, snap, _, _ = sandboxed.look(2)
    result = sandboxed.set_text(2, 'agent@example.com', index=0, snapshot_id=snap.id)
    assert ('screen-key', 'a', ('ctrl',)) in fake.posted  # select all, then type
    assert any(p[0] == 'screen-click' for p in fake.posted)
    assert result.status in (CONFIRMED, 'unverified')


def test_without_sandbox_take_over_refuses_at_once(fake):
    d = Driver(fake)
    d.hold('Amos')
    with pytest.raises(HumanControl, match='Amos has taken over'):
        d.look(1)
    d.release()
    d.look(1)


def test_sandbox_agent_waits_while_the_person_has_control(sandboxed, fake):
    sandboxed.hold_wait_s = 5
    sandboxed.look(1)
    sandboxed.hold('Amos')
    threading.Timer(0.6, sandboxed.release).start()
    t0 = time.monotonic()
    sandboxed.act_on_element(1, 2, 'toggle')
    assert time.monotonic() - t0 >= 0.5  # paused until handed back
    assert fake.trees[1][2].state['toggled'] == 'on'


def test_sandbox_gives_up_waiting(sandboxed):
    sandboxed.hold_wait_s = 0.2
    sandboxed.hold('Amos')
    with pytest.raises(HumanControl):
        sandboxed.look(1)


def test_take_over_stops_typing_already_under_way(sandboxed, fake):
    fake.fg = 2
    fake.idle = 0.0
    sandboxed.look(2)
    real_tick = fake._tick

    def slow_tick(still_ok):
        time.sleep(0.05)
        return real_tick(still_ok)

    fake._tick = slow_tick
    typing = threading.Thread(target=sandboxed.type_text, args=(2, 'x' * 40))
    typing.start()
    time.sleep(0.2)
    sandboxed.hold('Amos')  # returns only once the typing has stopped
    sent = len([p for p in fake.posted if p[0] == 'screen-char'])
    time.sleep(0.2)
    assert len([p for p in fake.posted if p[0] == 'screen-char']) == sent < 40
    sandboxed.release()
    typing.join(2)


def test_sandbox_driver_keeps_its_looks_between_calls(monkeypatch):
    from cognitrix.computer.sandbox import client
    box = SimpleNamespace(passwords={'agent-a': 'x'}, drivers={})
    mgr = client.SandboxManager.__new__(client.SandboxManager)
    monkeypatch.setattr(mgr, 'sandbox', lambda owner: box, raising=False)
    assert mgr.driver('u1', 'agent-a') is mgr.driver('u1', 'agent-a')


def test_remote_driver_round_trip(remote, fake):
    windows = remote.list_windows('calc')
    assert [w.title for w in windows] == ['Calc']
    image, snap, shown, _ = remote.look(1, viewer='agent')
    assert image.size == (1280, 800) and snap.scale == 2
    assert remote._snapshots[1].id == snap.id
    assert shown[0][1].name == 'One'
    result = remote.act_on_element(1, 2, 'toggle', snapshot_id=snap.id, viewer='agent')
    assert result.status == CONFIRMED
    assert remote.backend.window(2).title == 'Notes'


def test_remote_driver_maps_errors(remote):
    with pytest.raises(Stale):
        remote.act_on_element(1, 0, viewer='someone')  # never looked


def test_remote_events_are_marked_as_sandbox(remote, monkeypatch):
    seen = []
    computer.add_global_listener(seen.append)
    try:
        remote.look(1, viewer='agent')
        remote.act_on_element(1, 2, 'toggle', viewer='agent')
    finally:
        computer.remove_global_listener(seen.append)
    # (Here the worker runs in this process, so its own events show up too.)
    marked = [e for e in seen if e.desktop == 'agent-a']
    assert marked and marked[0].label

    from cognitrix.computer import overlay
    before = dict(overlay.hub.last_seen)
    overlay.hub._on_event(marked[0])  # the cursor on the person's screen ignores sandbox actions
    assert overlay.hub.last_seen == before


def test_approvals_in_the_sandbox(monkeypatch):
    det = DestructiveOpDetector()
    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX', raising=False)
    assert det.analyze('type_in_window', {'window_id': 1, 'text': 'x'}).risk_level != RiskLevel.LOW
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    for name in ('type_in_window', 'click_element', 'open_app', 'press_key_in_window'):
        assert det.analyze(name, {'window_id': 1, 'use_screen': True}).risk_level == RiskLevel.LOW
    # The full-screen tools still act on this machine.
    assert det.analyze('mouse_click', {'x': 1, 'y': 2}).risk_level != RiskLevel.LOW
    ask = det.analyze('ask_to_take_over', {'reason': 'Sign in to the bank'})
    assert 'person_takeover' in ask.categories and 'Sign in to the bank' in ask.details


async def test_full_screen_tools_point_at_the_window_tools(monkeypatch):
    from cognitrix.tools import misc
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    out = (await misc.mouse_click.run(x=1, y=2)).content
    assert 'sandboxed computer' in out


async def test_ask_to_take_over_is_sandbox_only(monkeypatch):
    from cognitrix.tools import computer as tools
    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX', raising=False)
    assert 'only works on the sandboxed computer' in (await tools.ask_to_take_over.run(reason='x')).content


# -- live screen API ------------------------------------------------------

class _ApiBox:
    viewer_port = 1
    passwords = {'agent-a': 'viewpass'}

    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None, timeout=None):
        self.requests.append((method, path, body))
        if path.endswith('/hold'):
            return {'ok': True, 'vnc_password': 'fullpass'}
        return {'ok': True}

    def desktop(self, name):
        self.passwords[name] = 'viewpass'

    def desktops(self):
        return [{'name': 'agent-a', 'display': ':1', 'held_by': None, 'running': True}]


@pytest.fixture
def api(monkeypatch):
    from cognitrix.api.routes import computer_sandbox
    from cognitrix.common.security import AuthContext, jwt_only

    box = _ApiBox()
    mgr = SimpleNamespace(existing=lambda owner: box if owner == 'u1' else None, sandbox=lambda owner: box,
                          reset=lambda owner: None, update_available=lambda b: False,
                          update=lambda owner: mgr.updated.append(owner), updated=[])
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    monkeypatch.setattr('cognitrix.computer.sandbox.client.get_manager', lambda: mgr)
    app = FastAPI()
    app.include_router(computer_sandbox.sandbox_api)
    app.dependency_overrides[jwt_only] = lambda: AuthContext(user=SimpleNamespace(id='u1', name='Amos'))
    return TestClient(app), box


def test_status_lists_desktops(api):
    client, _ = api
    data = client.get('/computer/sandbox').json()
    assert data['enabled'] and data['running'] and data['desktops'][0]['name'] == 'agent-a'


def test_status_when_off(api, monkeypatch):
    client, _ = api
    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX')
    assert client.get('/computer/sandbox').json() == {'enabled': False, 'running': False, 'desktops': [],
                                                      'from_env': False}


def test_settings_switch_saves_for_admins(api, monkeypatch):
    from cognitrix.computer.sandbox import client as sandbox_client
    client, _ = api
    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX')
    monkeypatch.setattr('cognitrix.agents.access.is_admin', _async(False))
    assert client.put('/computer/sandbox/settings', json={'enabled': True}).status_code == 403
    monkeypatch.setattr('cognitrix.agents.access.is_admin', _async(True))
    monkeypatch.setattr(sandbox_client, 'docker_status', lambda: {'installed': True, 'running': True, 'image': False})
    data = client.put('/computer/sandbox/settings', json={'enabled': True}).json()
    assert data['enabled'] and not data['from_env'] and sandbox_client.sandbox_mode() == 'docker'
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'off')  # the environment wins
    assert sandbox_client.sandbox_mode() is None
    assert client.put('/computer/sandbox/settings', json={'enabled': True}).status_code == 409


def _async(value):
    async def fn(*_args, **_kwargs):
        return value
    return fn


def test_take_over_returns_the_full_password(api):
    client, box = api
    data = client.post('/computer/sandbox/desktops/agent-a/hold', json={}).json()
    assert data == {'ok': True, 'held_by': 'Amos', 'password': 'fullpass'}
    assert box.requests[-1] == ('POST', '/desktops/agent-a/hold', {'by': 'Amos'})
    assert client.post('/computer/sandbox/desktops/agent-a/release').json() == {'ok': True}


def test_bad_desktop_name_is_refused(api):
    client, _ = api
    assert client.post('/computer/sandbox/desktops/..%2Fx/hold', json={}).status_code in (400, 404)


def test_viewer_ticket_is_one_use(api):
    client, _ = api
    viewer = client.post('/computer/sandbox/desktops/agent-a/viewer').json()
    assert viewer['password'] == 'viewpass' and viewer['ticket'] in viewer['path']
    from cognitrix.api.routes import computer_sandbox
    assert viewer['ticket'] in computer_sandbox._tickets
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/computer/sandbox/screen?ticket=wrong'):
            pass


def test_status_says_when_an_update_is_ready_and_update_restarts(api, monkeypatch):
    from cognitrix.computer.sandbox import client as sandbox_client
    client, _ = api
    assert client.get('/computer/sandbox').json()['update_available'] is False
    mgr = sandbox_client.get_manager()
    monkeypatch.setattr(mgr, 'update_available', lambda b: True)
    assert client.get('/computer/sandbox').json()['update_available'] is True
    assert client.post('/computer/sandbox/update').json() == {'ok': True}
    assert mgr.updated == ['u1']


class _FakeDocker:
    """Answers the docker commands SandboxManager runs."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.latest = 'sha256:new'
        self.container: dict | None = {'Image': 'sha256:old', 'State': {'Running': False},
                                       'Config': {'Labels': {'cognitrix.sandbox.token': 't'}},
                                       'NetworkSettings': {'Ports': {'8790/tcp': [{'HostPort': '5001'}],
                                                                     '6080/tcp': [{'HostPort': '5002'}]}}}

    def __call__(self, *args, check=True, timeout=120):
        import json as _json
        self.calls.append(args)
        out, code = '', 0
        if args[:2] == ('image', 'inspect'):
            out = self.latest
        elif args[0] == 'inspect':
            if self.container is None:
                code = 1
            else:
                out = _json.dumps([self.container])
        elif args[0] == 'rm':
            self.container = None
        elif args[0] == 'run':
            self.container = {**(self.container or {}), 'Image': self.latest, 'State': {'Running': True},
                              'Config': {'Labels': {'cognitrix.sandbox.token': 't2'}},
                              'NetworkSettings': {'Ports': {'8790/tcp': [{'HostPort': '5001'}],
                                                            '6080/tcp': [{'HostPort': '5002'}]}}}
        elif args[0] == 'start':
            self.container['State'] = {'Running': True}
        return SimpleNamespace(returncode=code, stdout=out, stderr='')


@pytest.fixture
def fake_manager(monkeypatch):
    from cognitrix.computer.sandbox import client as sandbox_client
    monkeypatch.setenv('COGNITRIX_SANDBOX_UPDATE_CHECK_HOURS', '0')
    mgr = sandbox_client.SandboxManager(image='img', docker='docker')
    fake = _FakeDocker()
    monkeypatch.setattr(mgr, '_docker', fake)
    monkeypatch.setattr(mgr, '_wait_healthy', lambda box: None)
    return mgr, fake


def test_a_stopped_computer_on_an_old_image_starts_on_the_new_one(fake_manager):
    mgr, fake = fake_manager
    box = mgr.sandbox('u1')
    assert ('start', mgr.container_name('u1')) not in fake.calls
    assert any(c[0] == 'rm' for c in fake.calls) and any(c[0] == 'run' for c in fake.calls)
    assert box.image_id == 'sha256:new' and not mgr.update_available(box)
    # The home volume (files, logins) is never removed by an update.
    assert not any(c[:2] == ('volume', 'rm') for c in fake.calls)


def test_a_running_computer_is_left_alone_until_asked(fake_manager):
    mgr, fake = fake_manager
    fake.container['State'] = {'Running': True}
    box = mgr.sandbox('u1')
    assert box.image_id == 'sha256:old' and mgr.update_available(box)
    assert not any(c[0] in ('rm', 'run') for c in fake.calls)
    box = mgr.update('u1')
    assert box.image_id == 'sha256:new' and not mgr.update_available(box)
    assert not any(c[:2] == ('volume', 'rm') for c in fake.calls)


def test_a_stopped_computer_on_the_newest_image_just_starts(fake_manager):
    mgr, fake = fake_manager
    fake.latest = 'sha256:old'
    mgr.sandbox('u1')
    assert any(c[0] == 'start' for c in fake.calls) and not any(c[0] in ('rm', 'run') for c in fake.calls)


def test_newer_image_check_is_throttled(fake_manager, monkeypatch):
    mgr, fake = fake_manager
    assert mgr.check_for_update() is False  # 0 hours = off
    monkeypatch.setenv('COGNITRIX_SANDBOX_UPDATE_CHECK_HOURS', '6')
    started = []
    monkeypatch.setattr(mgr, '_pull', lambda: started.append(1))
    assert mgr.check_for_update() is True
    mgr._pulling = False
    assert mgr.check_for_update() is False  # pulled moments ago
    assert mgr.check_for_update(force=True) is True
