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
                          reset=lambda owner: None)
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
    with pytest.raises(Exception):
        with client.websocket_connect('/computer/sandbox/screen?ticket=wrong'):
            pass
