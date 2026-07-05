"""Webhook signing, SSRF guard, delivery retries, and orchestrator notify hooks."""

import hashlib
import hmac as hmac_mod
import json
from types import SimpleNamespace

import pytest

from cognitrix.utils import webhooks


def _key(**overrides):
    from cognitrix.models.api_key import APIKey
    base = dict(name='k', user_id='u', key_hash='h', prefix='p',
                scopes=['run'], webhook_secret='topsecret')
    base.update(overrides)
    key = APIKey(**base)
    key.id = overrides.get('id', 'key1')
    return key


class _StubRun:
    """Quacks like TaskRun for notify_completion (type(run).get is awaited)."""

    def __init__(self, **kw):
        self.id = 'run1'
        self.status = 'completed'
        self.result = 'done'
        self.error = None
        self.completed_at = 'now'
        self.__dict__.update(kw)

    @classmethod
    async def get(cls, _id):
        return None  # fall back to the in-memory record


def _task(**overrides):
    base = dict(id='task1', callback_url='https://hooks.example.com/x', callback_key_id='key1')
    base.update(overrides)
    return SimpleNamespace(**base)


# --- signing -----------------------------------------------------------------

def test_sign_matches_reference_hmac():
    body = '{"a":1}'
    sig = webhooks.sign(body, 'topsecret', '1700000000')
    expected = 'sha256=' + hmac_mod.new(
        b'topsecret', b'1700000000.{"a":1}', hashlib.sha256).hexdigest()
    assert hmac_mod.compare_digest(sig, expected)


# --- SSRF guard --------------------------------------------------------------

def test_check_callback_url(monkeypatch):
    monkeypatch.delenv('COGNITRIX_WEBHOOK_ALLOW_PRIVATE', raising=False)
    assert webhooks.check_callback_url('ftp://x/y') is not None
    assert webhooks.check_callback_url('http://') is not None
    assert webhooks.check_callback_url('http://127.0.0.1:9/hook') is not None
    assert webhooks.check_callback_url('http://localhost:9/hook') is not None
    assert webhooks.check_callback_url('http://169.254.169.254/latest') is not None
    assert webhooks.check_callback_url('http://192.168.1.10/hook') is not None

    monkeypatch.setenv('COGNITRIX_WEBHOOK_ALLOW_PRIVATE', '1')
    assert webhooks.check_callback_url('http://127.0.0.1:9/hook') is None
    # Scheme check still applies even with private allowed.
    assert webhooks.check_callback_url('file:///etc/passwd') is not None


# --- delivery ----------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    """Stands in for httpx.AsyncClient; scripted status codes per call."""

    calls: list = []
    script: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, content=None, headers=None):
        _FakeClient.calls.append({'url': url, 'body': content, 'headers': headers})
        item = _FakeClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.script = []
    monkeypatch.setattr(webhooks.httpx, 'AsyncClient', _FakeClient)
    monkeypatch.setattr(webhooks, 'BACKOFFS', (0.0, 0.0, 0.0))
    monkeypatch.setenv('COGNITRIX_WEBHOOK_ALLOW_PRIVATE', '1')
    return _FakeClient


@pytest.fixture
def key_lookup(monkeypatch):
    from cognitrix.models.api_key import APIKey
    holder = {'key': _key()}

    async def fake_get(_id):
        return holder['key']

    monkeypatch.setattr(APIKey, 'get', staticmethod(fake_get))
    return holder


async def test_notify_noop_without_callback(fake_http, key_lookup):
    assert await webhooks.notify_completion(_task(callback_url=None), _StubRun()) is False
    assert await webhooks.notify_completion(_task(callback_key_id=None), _StubRun()) is False
    assert fake_http.calls == []


async def test_notify_skips_revoked_and_expired_keys(fake_http, key_lookup):
    key_lookup['key'] = _key(revoked=True)
    assert await webhooks.notify_completion(_task(), _StubRun()) is False
    key_lookup['key'] = _key(expires_at='2000-01-01 00:00:00')
    assert await webhooks.notify_completion(_task(), _StubRun()) is False
    assert fake_http.calls == []


async def test_notify_delivers_with_valid_signature(fake_http, key_lookup):
    fake_http.script = [200]
    assert await webhooks.notify_completion(_task(), _StubRun()) is True
    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    payload = json.loads(call['body'])
    assert payload['task_id'] == 'task1' and payload['status'] == 'completed'
    ts = call['headers']['X-Cognitrix-Timestamp']
    expected = webhooks.sign(call['body'], 'topsecret', ts)
    assert hmac_mod.compare_digest(call['headers']['X-Cognitrix-Signature'], expected)


async def test_notify_retries_then_succeeds(fake_http, key_lookup):
    import httpx
    fake_http.script = [500, httpx.ConnectError('boom'), 200]
    assert await webhooks.notify_completion(_task(), _StubRun()) is True
    assert len(fake_http.calls) == 3


async def test_notify_gives_up_after_attempts(fake_http, key_lookup):
    fake_http.script = [500, 500, 500]
    assert await webhooks.notify_completion(_task(), _StubRun()) is False
    assert len(fake_http.calls) == 3


async def test_notify_never_raises(fake_http, key_lookup):
    fake_http.script = [RuntimeError('unexpected')]  # not an httpx error
    assert await webhooks.notify_completion(_task(), _StubRun()) is False


async def test_notify_reports_failure_status(fake_http, key_lookup):
    fake_http.script = [200]
    run = _StubRun(status='failed', result=None, error='step 2 exploded')
    assert await webhooks.notify_completion(_task(), run) is True
    payload = json.loads(fake_http.calls[0]['body'])
    assert payload['status'] == 'failed' and payload['error'] == 'step 2 exploded'


# --- orchestrator hook -------------------------------------------------------

async def test_orchestrator_notifies_on_failure_path(monkeypatch):
    """The finally-hook must fire even when run() raises (failed plan)."""
    import cognitrix.tasks.orchestrator as orch
    from cognitrix.tasks.run import TaskRun

    notified = []

    async def fake_notify(task, run):
        notified.append((task, run))
        return True

    monkeypatch.setattr(orch, 'notify_completion', fake_notify)

    class _FakeTaskCls:
        @staticmethod
        async def get(_id):
            return None

        @staticmethod
        async def update_one(*a, **kw):
            return 1

    class FakeTask(SimpleNamespace):
        get = _FakeTaskCls.get
        update_one = _FakeTaskCls.update_one

    agent = SimpleNamespace(id='a1', name='Agent A')
    task = FakeTask(
        id='t1', title='t', description='d', status='pending',
        step_instructions={'0': {'step_title': 's1', 'description': 'do'}},
        assigned_agents=['a1'], results=[], team_id=None,
        callback_url='https://hooks.example.com/x', callback_key_id='key1',
    )

    async def team():
        return [agent]
    task.team = team

    async def no_find(_q):
        return []
    monkeypatch.setattr(TaskRun, 'find', staticmethod(no_find))

    async def fake_save(self):
        self.id = 'r1'
        return self
    monkeypatch.setattr(TaskRun, 'save', fake_save)

    async def no_op(*a, **kw):
        return True
    monkeypatch.setattr(orch, '_set_task_status', no_op)
    monkeypatch.setattr(orch, '_set_run_status', no_op)
    monkeypatch.setattr(orch, '_save_plan', no_op)

    async def resolve_leader(_task, roster):
        return roster[0]
    monkeypatch.setattr(orch, '_resolve_leader', resolve_leader)

    def boom(_task):
        raise RuntimeError('planning exploded')
    monkeypatch.setattr(orch, '_template_plan', boom)

    with pytest.raises(RuntimeError, match='planning exploded'):
        await orch.run(task)  # type: ignore[arg-type]

    assert len(notified) == 1
    assert notified[0][0] is task and notified[0][1].id == 'r1'


async def test_orchestrator_notifies_on_no_roster(monkeypatch):
    import cognitrix.tasks.orchestrator as orch
    from cognitrix.tasks.run import TaskRun

    notified = []

    async def fake_notify(task, run):
        notified.append(run)
        return True
    monkeypatch.setattr(orch, 'notify_completion', fake_notify)

    async def fake_get(_id):
        return None

    class FakeTask2(SimpleNamespace):
        get = staticmethod(fake_get)

        @staticmethod
        async def update_one(*a, **kw):
            return 1

    task = FakeTask2(
        id='t1', title='t', description='d', status='pending',
        step_instructions={}, assigned_agents=[], results=[], team_id=None,
        callback_url='https://hooks.example.com/x', callback_key_id='key1',
    )

    async def team():
        return []
    task.team = team

    async def no_find(_q):
        return []
    monkeypatch.setattr(TaskRun, 'find', staticmethod(no_find))

    async def fake_save(self):
        self.id = 'r-no-roster'
        return self
    monkeypatch.setattr(TaskRun, 'save', fake_save)

    async def no_op(*a, **kw):
        return True
    monkeypatch.setattr(orch, '_set_task_status', no_op)

    with pytest.raises(RuntimeError, match='no agents'):
        await orch.run(task)  # type: ignore[arg-type]

    assert [r.id for r in notified] == ['r-no-roster']