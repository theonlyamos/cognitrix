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
    assert 'list_windows' in out and "computer='person'" in out
    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX')
    # Off or on, they never act without the person's computer asked for by name.
    assert "computer='person'" in (await misc.mouse_click.run(x=1, y=2)).content


def test_window_tools_on_the_persons_computer_still_ask(monkeypatch):
    det = DestructiveOpDetector()
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    risk = det.analyze('type_in_window', {'window_id': 1, 'text': 'x', 'computer': 'person'})
    assert risk.risk_level != RiskLevel.LOW and 'desktop_control' in risk.categories


def test_sandbox_is_on_by_default_where_docker_is_installed(monkeypatch):
    from cognitrix.computer.sandbox import client as sandbox_client

    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX', raising=False)
    monkeypatch.setattr(sandbox_client, '_docker_installed', lambda: True)
    assert sandbox_client.sandbox_mode() == 'docker'
    sandbox_client.save_enabled(False)  # switched off in Settings stays off
    assert sandbox_client.sandbox_mode() is None
    sandbox_client.save_enabled(True)
    monkeypatch.setattr(sandbox_client, '_docker_installed', lambda: False)
    assert sandbox_client.sandbox_mode() == 'docker'


async def test_ask_to_take_over_is_sandbox_only(monkeypatch):
    from cognitrix.tools import computer as tools
    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX', raising=False)
    assert 'only works on the sandboxed computer' in (await tools.ask_to_take_over.run(reason='x')).content


async def test_ask_to_take_over_waits_for_take_over_and_hand_back(monkeypatch):
    from cognitrix.tools import computer as tools

    states = iter([None, None, 'the person', 'the person', None])
    assert await tools.wait_for_hand_back(lambda: next(states), wait_s=5, poll_s=0) == 'handed_back'
    # Nobody took over in time / never handed back.
    assert await tools.wait_for_hand_back(lambda: None, wait_s=0.05, poll_s=0.01) == 'not_taken'
    assert await tools.wait_for_hand_back(lambda: 'the person', wait_s=0.05, poll_s=0.01) == 'still_held'
    # A take over that ended before the first check (or between checks) still counts.
    assert await tools.wait_for_hand_back(lambda: None, handed_back=lambda: True, wait_s=5, poll_s=0) == 'handed_back'
    # It waits on a person, so it doesn't hold one of the shared execution slots.
    assert tools.ask_to_take_over.occupies_execution_slot is False

    # The tool itself: checks the agent's own desktop until it is handed back.
    class Box:
        def __init__(self):
            self.answers = iter([[{'name': 'a1', 'held_by': 'the person'}, {'name': 'b2', 'held_by': None}],
                                 [{'name': 'a1', 'held_by': None}]])

        def desktops(self):
            return next(self.answers)

    driver = type('D', (), {'box': Box(), 'desktop': 'a1'})()
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    monkeypatch.setattr(tools, '_driver', lambda actor, computer: driver)
    monkeypatch.setattr(tools, '_TAKEOVER_POLL_S', 0)
    out = (await tools.ask_to_take_over.run(reason='Sign in')).content
    assert 'handed the computer back' in out

    # Taken over and handed back after the card was shown but before the tool looked.
    from cognitrix.computer import sandbox as sandbox_pkg
    sandbox_pkg.note_takeover_asked('c3')
    sandbox_pkg.note_handed_back('c3')
    idle = type('D', (), {'box': type('B', (), {'desktops': lambda self: [{'name': 'c3', 'held_by': None}]})(),
                          'desktop': 'c3'})()
    monkeypatch.setattr(tools, '_driver', lambda actor, computer: idle)
    assert 'handed the computer back' in (await tools.ask_to_take_over.run(reason='Sign in')).content


def test_take_over_request_always_asks_a_person(monkeypatch):
    from cognitrix.safety import action_reviewer

    risk = DestructiveOpDetector().analyze('ask_to_take_over', {'reason': 'Sign in'})
    # No reviewer answers it for the person, even in a task run.
    monkeypatch.setattr(action_reviewer, 'reviewer_mode', lambda: 'always')
    assert not action_reviewer.should_review(risk, 'task')


async def test_take_over_card_waits_longer_and_names_the_desktop(monkeypatch):
    from cognitrix.safety import approval_gate as gate_mod
    from cognitrix.safety.approval_gate import ApprovalGate, ToolCall, web_turn_ctx

    sent = []
    seen = {}

    async def emit(event):
        sent.append(event)

    async def fake_wait_for(fut, timeout):
        seen['timeout'] = timeout
        fut.cancel()
        raise TimeoutError

    monkeypatch.setattr(gate_mod.asyncio, 'wait_for', fake_wait_for)
    risk = DestructiveOpDetector().analyze('ask_to_take_over', {'reason': 'Sign in to the bank'})
    token = web_turn_ctx.set({'emit': emit, 'session_id': 's1', 'user_key': 'u'})
    try:
        out = await ApprovalGate().check_approval(
            ToolCall(tool_name='ask_to_take_over', params={'reason': 'Sign in to the bank'}),
            risk, interface='web', scope='A1')
    finally:
        web_turn_ctx.reset(token)
    assert not out.approved and seen['timeout'] >= 30 * 60
    card = next(e for e in sent if e.get('type') == 'approval_request')
    assert card['takeover'] == {'desktop': 'a1', 'reason': 'Sign in to the bank'} and card['always_ask']


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
    from cognitrix.computer import sandbox as sandbox_pkg
    before = sandbox_pkg.handed_back_at('agent-a') or 0
    assert client.post('/computer/sandbox/desktops/agent-a/release').json() == {'ok': True}
    assert (sandbox_pkg.handed_back_at('agent-a') or 0) > before  # a waiting Ask To Take Over sees it


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


def test_status_says_when_the_computer_is_on_the_tailnet(api, monkeypatch):
    from cognitrix.computer.sandbox import client as sandbox_client
    client, _ = api
    assert 'tailnet' not in client.get('/computer/sandbox').json()      # a Docker computer never is
    mgr = sandbox_client.get_manager()
    asked = []
    place = {'name': 'cognitrix-sandbox-u1', 'dns': 'cognitrix-sandbox-u1.tail1234.ts.net', 'online': True}
    monkeypatch.setattr(mgr, 'tailnet_info', lambda owner: asked.append(owner) or place, raising=False)
    assert client.get('/computer/sandbox').json()['tailnet'] == place and asked == ['u1']
    monkeypatch.setattr(mgr, 'tailnet_info', lambda owner: None, raising=False)
    assert 'tailnet' not in client.get('/computer/sandbox').json()


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


def test_a_running_computer_is_left_alone_until_asked(fake_manager, monkeypatch):
    from cognitrix.computer.sandbox import client as sandbox_client
    mgr, fake = fake_manager
    fake.container['State'] = {'Running': True}
    box = mgr.sandbox('u1')
    assert box.image_id == 'sha256:old' and mgr.update_available(box)
    assert not any(c[0] in ('rm', 'run') for c in fake.calls)
    opened = []
    monkeypatch.setattr(sandbox_client.Sandbox, 'desktops', lambda self: [{'name': 'agent-a'}, {'name': 'main'}])
    monkeypatch.setattr(sandbox_client.Sandbox, 'desktop', lambda self, n: opened.append(n) or {'name': n})
    box = mgr.update('u1')
    assert box.image_id == 'sha256:new' and not mgr.update_available(box)
    assert opened == ['agent-a', 'main']  # the same desktops are back for the panel
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


def test_a_failed_image_check_is_tried_again_soon(fake_manager, monkeypatch):
    """A check that fails (offline, a download cut off) doesn't wait six
    hours for the next try, and the failure reaches the log."""
    import subprocess as _sp

    from cognitrix.computer.sandbox import client as sandbox_client
    mgr, fake = fake_manager
    monkeypatch.setenv('COGNITRIX_SANDBOX_UPDATE_CHECK_HOURS', '6')

    def failing(*args, **kwargs):
        raise _sp.TimeoutExpired(args, 10)
    monkeypatch.setattr(mgr, '_docker', failing)
    warned = []
    monkeypatch.setattr(sandbox_client.logger, 'warning', lambda *a, **k: warned.append(a[0]))
    mgr._pulling = True
    mgr._pull()
    assert not mgr._pulling and not mgr._last_pull_ok and warned
    monkeypatch.setattr(mgr, '_pull', lambda: None)
    assert mgr.check_for_update() is False  # just failed
    mgr._last_pull -= sandbox_client._PULL_RETRY_S + 1
    assert mgr.check_for_update() is True  # 20 minutes later, not 6 hours
    mgr._pulling, mgr._last_pull_ok = False, True
    mgr._last_pull -= sandbox_client._PULL_RETRY_S + 1
    assert mgr.check_for_update() is False  # after a good check, the full interval


def test_a_stopped_computer_still_looks_for_a_newer_image(fake_manager, monkeypatch):
    mgr, fake = fake_manager
    checked = []
    monkeypatch.setattr(mgr, 'check_for_update', lambda force=False: checked.append(1) or True)
    monkeypatch.setattr(mgr, '_start_auto_update', lambda: None)
    assert mgr.existing('u1') is None
    assert checked == [1]


def test_agents_are_told_to_browse_on_their_own_computer(monkeypatch):
    from cognitrix.sessions.base import _with_folder_prompt

    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX', raising=False)
    assert _with_folder_prompt([{'role': 'system', 'content': 'base'}], ())[0]['content'] == 'base'
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    content = _with_folder_prompt([{'role': 'system', 'content': 'base'}], ())[0]['content']
    assert '## Your computer' in content and 'agent-browser' in content and "computer='person'" in content


def test_browser_skills_get_a_reminder_while_the_agent_has_a_computer(monkeypatch):
    from types import SimpleNamespace

    from cognitrix.tools.skill_tool import _host_browser_note

    skill = SimpleNamespace(name='agent-browser', description='Automate the browser from the command line')
    other = SimpleNamespace(name='summarize-document', description='Summarize a document')
    monkeypatch.delenv('COGNITRIX_COMPUTER_SANDBOX', raising=False)
    assert _host_browser_note(skill) == ''
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    assert "person's own computer" in _host_browser_note(skill)
    assert _host_browser_note(other) == ''
