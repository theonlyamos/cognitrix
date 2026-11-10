"""Chat approvals reach the person when they aren't on the chat: the Inbox,
web push with Approve/Deny buttons, and linked chat apps."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from cognitrix.safety.approval_gate import _WEB_PENDING, ApprovalGate, ToolCall, web_turn_ctx
from cognitrix.safety.destructive_ops import RiskAssessment, RiskLevel
from tests.test_chat_channels import _channel, _service
from tests.test_session_ownership_routes import _ctx


def _risk():
    return RiskAssessment(risk_level=RiskLevel.HIGH, categories=['delete'], details='Deletes a file')


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'alerts.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.setenv('COGNITRIX_APPROVAL_ALERT_DELAY', '0')
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    yield
    _WEB_PENDING.clear()


@pytest.fixture
def pushes(monkeypatch):
    from cognitrix.common import webpush

    sent = []

    async def send(user, title, body='', **kw):
        sent.append({'user': user, 'title': title, 'body': body, **kw})
        return 1

    monkeypatch.setattr(webpush, 'send_to_user', send)
    return sent


async def _ask(user='u1', **ctx):
    """Start a chat approval; returns (request_id, task awaiting its result)."""
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def run():
        token = web_turn_ctx.set({'emit': emit, 'session_id': 's1', 'bypass': False, 'user_key': user,
                                  'agent_name': 'Helper', **ctx})
        try:
            return await ApprovalGate().check_approval(
                ToolCall('Delete File', {'path': 'notes.txt'}), _risk(), interface='web', timeout=5, scope=user)
        finally:
            web_turn_ctx.reset(token)

    task = asyncio.create_task(run())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if emitted:
            break
    return emitted[0]['request_id'], task


async def test_a_waiting_chat_approval_is_listed_in_the_inbox(db):
    from cognitrix.api.routes.system import inbox

    rid, task = await _ask()
    items = (await inbox(_ctx('u1')))['items']
    assert items[0]['id'] == rid and items[0]['source'] == 'chat' and items[0]['kind'] == 'approval'
    assert items[0]['href'] == '/home?chat=s1' and items[0]['agent_name'] == 'Helper'
    assert items[0]['tool_name'] == 'Delete File' and items[0]['params'] == {'path': 'notes.txt'}
    assert (await inbox(_ctx('u2')))['items'] == []

    from cognitrix.safety.approval_gate import resolve_web_approval
    resolve_web_approval(rid, approved=False, user_key='u1')
    assert (await task).approved is False
    assert (await inbox(_ctx('u1')))['items'] == []


async def test_push_goes_out_with_buttons_and_the_button_answers(db, pushes):
    from cognitrix.api.routes import push

    rid, task = await _ask()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if pushes:
            break
    sent = pushes[0]
    assert sent['user'] == 'u1' and 'Helper wants to run Delete File' in sent['title']
    assert sent['url'] == '/home?chat=s1' and 'notes.txt' in sent['body']
    extra = sent['extra']
    assert [a['action'] for a in extra['actions']] == ['approve', 'deny'] and extra['require_interaction']

    # The token is not a sign-in token.
    import jwt

    from cognitrix.common.constants import JWT_ALGORITHM, JWT_SECRET_KEY
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(extra['action_token'], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])

    with pytest.raises(HTTPException) as bad:
        await push.approval_action(push.ApprovalActionBody(token='x' * 40, approved=True))
    assert bad.value.status_code == 403
    assert await push.approval_action(push.ApprovalActionBody(token=extra['action_token'], approved=True)) == {
        'status': 'approved'}
    assert (await task).approved is True
    with pytest.raises(HTTPException) as again:
        await push.approval_action(push.ApprovalActionBody(token=extra['action_token'], approved=False))
    assert again.value.status_code == 409
    assert rid not in _WEB_PENDING


async def test_nothing_is_pushed_when_the_chat_page_showed_it(db, pushes, monkeypatch):
    from cognitrix.api.routes.agents import approval_seen_endpoint

    monkeypatch.setenv('COGNITRIX_APPROVAL_ALERT_DELAY', '0.2')

    class Request:
        def __init__(self, body):
            self.body = body

        async def json(self):
            return self.body

    rid, task = await _ask()
    with pytest.raises(HTTPException):
        await approval_seen_endpoint(Request({'request_id': rid}), user=_ctx('u2').user)
    assert await approval_seen_endpoint(Request({'request_id': rid}), user=_ctx('u1').user) == {'status': 'seen'}
    await asyncio.sleep(0.3)
    assert pushes == []
    from cognitrix.safety.approval_gate import resolve_web_approval
    resolve_web_approval(rid, approved=True)
    await task


async def test_a_task_approval_push_can_be_answered_from_its_buttons(db, monkeypatch):
    from cognitrix.api.routes import push
    from cognitrix.common import webpush
    from cognitrix.tasks import approvals

    seen = []
    monkeypatch.setattr(webpush, 'send_soon', lambda user, title, body='', **kw: seen.append(kw))
    monkeypatch.setattr(approvals, 'approval_timeout_seconds', lambda: 0.5)
    monkeypatch.setattr(approvals, '_poll_interval', lambda: 0.02)
    ctx = {'run_id': 'r1', 'task_id': 't1', 'user_id': 'u1'}
    waiting = asyncio.create_task(approvals.request_task_approval(ctx, 'Delete File', {'path': 'x'}, _risk()))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if seen:
            break
    token = seen[0]['extra']['action_token']
    assert await push.approval_action(push.ApprovalActionBody(token=token, approved=True)) == {'status': 'approved'}
    assert (await waiting)[0] is True


async def test_chat_apps_get_the_approval_with_buttons(db, pushes):
    from cognitrix.channels.base import ButtonPress

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    rid, task = await _ask()
    assert await service.deliver_once() == 1
    chat, text, buttons = transport.sent[-1]
    assert 'Helper wants to run Delete File' in text and 'notes.txt' in text
    assert [b.label for b in buttons] == ['Approve', 'Deny']
    assert await service.deliver_once() == 0

    await service._on_button(channel.id, ButtonPress('c1', 'p1', buttons[1].data, '1'))
    assert (await task).approved is False
    assert transport.acks[-1] == 'Denied.'


async def test_chat_app_buttons_go_away_when_answered_elsewhere(db, pushes):
    from cognitrix.safety.approval_gate import resolve_web_approval

    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    rid, task = await _ask()
    await service.deliver_once()
    resolve_web_approval(rid, approved=True, user_key='u1')
    await task
    await service.deliver_once()
    assert transport.edits[-1][2].endswith('Answered.')


async def test_the_chat_app_that_started_the_turn_is_not_sent_it_twice(db, pushes):
    channel = await _channel(chat_id='c1', peer_id='p1', link_code='')
    service, transport = await _service(channel)
    rid, task = await _ask(origin=f'channel:{channel.id}')
    assert await service.deliver_once() == 0
    await asyncio.sleep(0.05)
    assert pushes == []  # the chat app is where the person is
    from cognitrix.safety.approval_gate import resolve_web_approval
    resolve_web_approval(rid, approved=True)
    await task


def test_push_payload_carries_buttons_and_stays_small(monkeypatch):
    from cognitrix.common import webpush

    captured = {}

    async def fake_post(row, payload, client):
        captured['payload'] = json.loads(payload)
        return 201

    class Row:
        endpoint = 'https://fcm.googleapis.com/fcm/send/x'

    async def run():
        from cognitrix.models.push_subscription import PushSubscription

        async def find(_q):
            return [Row()]

        monkeypatch.setattr(PushSubscription, 'find', find)
        monkeypatch.setattr(webpush, '_post', fake_post)
        monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
        return await webpush.send_to_user('u1', 'Approval needed', 'x' * 5000, url='/home?chat=s1', extra={
            'actions': [{'action': 'approve', 'title': 'Approve'}], 'action_token': 't' * 200,
            'require_interaction': True, 'ignored': 1})

    assert asyncio.run(run()) == 1
    payload = captured['payload']
    assert payload['action_token'] == 't' * 200 and payload['actions'][0]['action'] == 'approve'
    assert 'ignored' not in payload and len(json.dumps(payload)) <= webpush.MAX_PAYLOAD_BYTES


async def test_it_is_pushed_once_the_person_leaves_the_chat(db, pushes, monkeypatch):
    from cognitrix.safety import approval_alerts

    monkeypatch.setenv('COGNITRIX_APPROVAL_ALERT_DELAY', '0.05')
    monkeypatch.setenv('COGNITRIX_APPROVAL_AWAY_DELAY', '0.3')
    sleeps = []
    real_sleep = asyncio.sleep

    async def quick_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(min(seconds, 0.05))

    monkeypatch.setattr(approval_alerts.asyncio, 'sleep', quick_sleep)
    rid, task = await _ask()
    assert approval_alerts.mark_seen(rid, 'u1')
    await real_sleep(0.15)
    assert pushes == []  # still looking
    await real_sleep(0.4)
    assert len(pushes) == 1
    from cognitrix.safety.approval_gate import resolve_web_approval
    resolve_web_approval(rid, approved=True)
    await task
