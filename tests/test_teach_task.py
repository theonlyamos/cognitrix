"""Teach a task: recording a demonstration on a sandbox desktop and turning it
into a skill (cognitrix/computer/sandbox/recorder.py, cognitrix/skills/teach.py,
the /computer/sandbox/.../teach routes)."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cognitrix.computer import Driver
from cognitrix.computer.sandbox.recorder import SECRET, StepBuilder, keysym_char, steps_as_text
from cognitrix.computer.sandbox.worker import Worker
from cognitrix.skills.drafts import DraftError
from cognitrix.skills.teach import draft_from_steps, fallback_skill, prompt_for
from tests.test_background_computer_use import FakeBackend


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _builder(focused_role='entry', windows=None):
    clock = _Clock()
    where = {'app': 'chromium', 'window': 'Shop', 'rect': (0, 0, 1000, 500)}

    def describe(x, y):
        return {**where, 'element': {'role': 'push button', 'name': 'Buy'}} if x < 500 else dict(where)

    def focused():
        return {**where, 'element': {'role': focused_role, 'name': 'Search'}}

    return StepBuilder(describe, focused, windows, clock=clock), clock


def _type(b, text, state=0):
    for ch in text:
        b.key(ord(ch), state)


def test_clicks_name_the_control_and_fall_back_to_a_position():
    b, clock = _builder()
    b.click(100, 100)
    clock.now += 2
    b.click(750, 250, 3)
    steps = b.finish()
    assert steps[0] == {'kind': 'click', 'app': 'chromium', 'window': 'Shop', 'target': "push button 'Buy'",
                        'at': [100, 200], 't': 0.0}
    assert steps[1]['kind'] == 'right_click' and 'target' not in steps[1] and steps[1]['at'] == [750, 500]
    text = steps_as_text(steps)
    assert "1. Click push button 'Buy' in chromium ('Shop')" in text
    assert '2. Right-click the point 750/1000 across, 500/1000 down' in text


def test_double_click_and_scrolls_merge():
    b, clock = _builder()
    b.click(100, 100)
    clock.now += 0.2
    b.click(101, 100)
    b.click(10, 10, 5)
    b.click(10, 10, 5)
    b.click(10, 10, 4)
    kinds = [(s['kind'], s.get('direction'), s.get('amount')) for s in b.finish()]
    assert kinds == [('double_click', None, None), ('scroll', 'down', 2), ('scroll', 'up', 1)]


def test_typing_collects_text_with_backspace_and_ends_on_enter_or_shortcut():
    b, _ = _builder()
    _type(b, 'hello wrld')
    for _ in range(3):
        b.key(0xFF08)  # BackSpace
    _type(b, 'orld')
    b.key(ord('H'), 1)  # shift is fine inside text
    b.key(0xFF0D)  # Return
    b.key(ord('l'), 4)  # ctrl+l
    b.key(0xFFE3)  # Control_L on its own: nothing
    b.key(0xFF09, 1)  # shift+Tab
    steps = b.finish()
    assert steps[0]['kind'] == 'type' and steps[0]['text'] == 'hello worldH'
    assert steps[0]['target'] == "entry 'Search'"
    assert [s.get('keys') for s in steps[1:]] == ['enter', 'ctrl+l', 'shift+tab']


def test_passwords_are_never_recorded():
    b, _ = _builder(focused_role='password text')
    _type(b, 'hunter2')
    b.key(0xFF08)
    b.key(0xFF0D)
    steps = b.finish()
    assert steps[0]['text'] == SECRET and steps[0]['secret'] is True
    assert 'hunter' not in repr(steps) and 'hunter' not in steps_as_text(steps)


def test_new_windows_are_noted():
    shown = [[(1, 'chromium', 'Shop')], [(1, 'chromium', 'Shop'), (2, 'gedit', 'notes.txt')]]
    b, clock = _builder(windows=lambda: shown[0] if len(shown) > 1 else shown[-1])
    b.click(100, 100)
    shown.pop(0)  # a second window has opened since
    clock.now += 1
    b.click(100, 100)
    steps = b.finish()
    assert {'kind': 'window_opened', 'app': 'gedit', 'window': 'notes.txt'} in [
        {k: v for k, v in s.items() if k != 't'} for s in steps]


def test_keysym_chars():
    assert keysym_char(ord('a')) == 'a' and keysym_char(0xE9) == 'é'
    assert keysym_char(0x010020AC) == '€' and keysym_char(0xFFB5) == '5' and keysym_char(0xFF0D) == ''


# -- worker -------------------------------------------------------------------

def test_worker_records_only_while_the_person_has_control(monkeypatch):
    driver = Driver(FakeBackend(), sandbox=True)
    worker = Worker(driver)
    out = worker.call('record_start', {}, None)
    assert out['ok'] is False and out['error'] == 'human_control'

    started = []

    class FakeRecorder:
        recording = False

        def __init__(self, backend=None):
            self.backend = backend
            self.error = ''

        def start(self):
            started.append(1)
            self.recording = True

        def steps(self):
            return [{'kind': 'click', 'app': 'x'}]

        def stop(self):
            self.recording = False
            return {'steps': [{'kind': 'click', 'app': 'x'}], 'truncated': False}

    monkeypatch.setattr('cognitrix.computer.sandbox.recorder.Recorder', FakeRecorder)
    worker.call('hold', {'by': 'Amos'}, None)
    assert worker.call('record_start', {}, None)['ok'] and started
    assert worker.call('record_status', {}, None)['result']['recording'] is True
    assert worker.call('status', {}, None)['result']['recording'] is True
    # Handing back ends the recording; its steps are still there to collect.
    worker.call('release', {}, None)
    assert worker.call('record_status', {}, None)['result']['recording'] is False
    assert worker.call('record_stop', {}, None)['result']['steps'] == [{'kind': 'click', 'app': 'x'}]
    assert worker.call('record_stop', {}, None)['result']['steps'] == []


# -- writing the skill ----------------------------------------------------------

_STEPS = [
    {'kind': 'click', 'app': 'chromium', 'window': 'Shop', 'target': "link 'Invoices'"},
    {'kind': 'type', 'app': 'chromium', 'target': "entry 'Password'", 'text': SECRET, 'secret': True},
    {'kind': 'key', 'keys': 'enter', 'app': 'chromium'},
]


def test_prompt_carries_the_steps_as_material():
    prompt = prompt_for(_STEPS, 'Get the invoice', 'Pick last month')
    assert prompt[0]['role'] == 'system' and 'ask_to_take_over' in prompt[0]['content']
    assert "<task>Get the invoice</task>" in prompt[1]['content']
    assert "Click link 'Invoices'" in prompt[1]['content'] and SECRET in prompt[1]['content']


def test_fallback_skill_is_valid_without_a_model():
    name, description, body = fallback_skill(_STEPS, 'Get invoice')
    assert name == 'get-invoice' and 'Get invoice' in description
    assert "Click link 'Invoices'" in body and 'ask_to_take_over' in body


async def test_draft_uses_the_model_and_falls_back():
    async def good(prompt, stream=False):
        return SimpleNamespace(error=None, llm_response='{"name": "Fetch Invoice", "description": "Get an invoice.", '
                                                         '"body": "## Steps\\n1. Open Invoices in Chromium and look."}')

    async def broken(prompt, stream=False):
        raise RuntimeError('down')

    assert (await draft_from_steps(good, _STEPS, 'x'))[0] == 'fetch-invoice'
    assert (await draft_from_steps(broken, _STEPS, 'Get invoice'))[0] == 'get-invoice'
    assert (await draft_from_steps(None, _STEPS, 'Get invoice'))[0] == 'get-invoice'
    with pytest.raises(DraftError):
        await draft_from_steps(good, [{'kind': 'window_opened', 'app': 'x'}])


# -- routes -----------------------------------------------------------------------

class _TeachBox:
    passwords = {'agent-a': 'viewpass'}

    def __init__(self, record_error=None):
        self.sent = []
        self.record_error = record_error

    def request(self, method, path, body=None, timeout=None):
        self.sent.append((method, path, body))
        return {'ok': True, 'vnc_password': 'fullpass'} if path.endswith('/hold') else {'ok': True}

    def call(self, method, path, body=None, **_kw):
        self.sent.append((method, path, body))
        if not path.endswith('/call'):
            return {'ok': True}
        name = body['method']
        if name == 'record_start' and self.record_error:
            return {'ok': False, 'error': self.record_error, 'message': 'nope'}
        if name in ('record_status', 'record_stop'):
            return {'ok': True, 'result': {'recording': name == 'record_status', 'steps': _STEPS}}
        return {'ok': True, 'result': {}}

    def desktop(self, name):
        self.passwords[name] = 'viewpass'


@pytest.fixture
def teach_api(monkeypatch, tmp_path):
    from cognitrix.api.routes import computer_sandbox
    from cognitrix.common.security import AuthContext, jwt_only

    monkeypatch.setattr('cognitrix.computer.sandbox.client.SETTINGS_FILE', tmp_path / 'computer-sandbox.json')
    box = _TeachBox()
    mgr = SimpleNamespace(existing=lambda owner: box, sandbox=lambda owner: box)
    monkeypatch.setenv('COGNITRIX_COMPUTER_SANDBOX', 'docker')
    monkeypatch.setattr('cognitrix.computer.sandbox.client.get_manager', lambda: mgr)
    app = FastAPI()
    app.include_router(computer_sandbox.sandbox_api)
    app.dependency_overrides[jwt_only] = lambda: AuthContext(user=SimpleNamespace(id='u1', name='Amos'))
    return TestClient(app), box


def test_teach_start_takes_over_then_records(teach_api):
    client, box = teach_api
    data = client.post('/computer/sandbox/desktops/agent-a/teach/start', json={}).json()
    assert data == {'ok': True, 'recording': True, 'held_by': 'Amos', 'password': 'fullpass'}
    assert [s[1] for s in box.sent] == ['/desktops/agent-a/hold', '/desktops/agent-a/call']
    assert box.sent[1][2]['method'] == 'record_start'


def test_teach_start_hands_back_when_recording_cant_start(teach_api):
    client, box = teach_api
    box.record_error = 'bad_method'  # an older computer image
    resp = client.post('/computer/sandbox/desktops/agent-a/teach/start', json={})
    assert resp.status_code == 409 and 'Update' in resp.json()['detail']
    assert box.sent[-1][1] == '/desktops/agent-a/release'


def test_teach_status_and_stop(teach_api):
    client, box = teach_api
    status = client.get('/computer/sandbox/desktops/agent-a/teach').json()
    assert status['recording'] is True and "Click link 'Invoices'" in status['text']
    out = client.post('/computer/sandbox/desktops/agent-a/teach/stop', json={}).json()
    assert out['steps'] == _STEPS and box.sent[-1][1] == '/desktops/agent-a/release'


def test_teach_draft_returns_a_skill_to_review(teach_api, monkeypatch):
    client, _ = teach_api

    async def llm(prompt, stream=False):
        return SimpleNamespace(error=None, llm_response='{"name": "get-invoice", "description": "Get it.", '
                                                        '"body": "## Steps\\n1. Open Invoices in Chromium."}')

    agent = SimpleNamespace(id='agent-a', name='Researcher', llm=llm)

    async def visible(_me):
        return [agent]

    async def taken(_me, name):
        return name == 'get-invoice'

    monkeypatch.setattr('cognitrix.agents.access.visible_agents', visible)
    monkeypatch.setattr('cognitrix.skills.personal.name_taken', taken)
    data = client.post('/computer/sandbox/teach/draft',
                       json={'steps': _STEPS, 'title': 'Get invoice', 'desktop': 'agent-a'}).json()
    assert data['name'].startswith('get-invoice-') and data['agent'] == 'Researcher'
    assert client.post('/computer/sandbox/teach/draft', json={'steps': []}).status_code == 422


# -- live: a real X display -------------------------------------------------------

@pytest.mark.skipif(not (shutil.which('Xvfb') and shutil.which('xdotool')), reason='needs Xvfb and xdotool')
def test_recorder_hears_real_input_on_a_display():
    pytest.importorskip('Xlib')
    from cognitrix.computer.sandbox.recorder import Recorder

    display = ':93'
    xvfb = subprocess.Popen(['Xvfb', display, '-screen', '0', '800x600x24', '-nolisten', 'tcp'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if os.path.exists('/tmp/.X11-unix/X93'):
                break
            time.sleep(0.1)
        rec = Recorder(display=display)
        rec.start()
        time.sleep(0.3)
        env = dict(os.environ, DISPLAY=display)
        for args in (['mousemove', '50', '50', 'click', '1'], ['type', 'hi'], ['key', 'ctrl+s']):
            subprocess.run(['xdotool', *args], env=env, check=True)
        time.sleep(0.5)
        steps = rec.stop()['steps']
    finally:
        xvfb.terminate()
    assert [s['kind'] for s in steps] == ['click', 'type', 'key']
    assert steps[1]['text'] == 'hi' and steps[2]['keys'] == 'ctrl+s'
