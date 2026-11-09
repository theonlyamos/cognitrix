"""Web push: message encryption, where the server will post, and the routes."""
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from tests.test_session_ownership_routes import _ctx, _initialize_routes_db

ENDPOINT = 'https://fcm.googleapis.com/fcm/send/abc'


def _browser():
    from cognitrix.common import webpush

    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, webpush.b64url(raw), webpush.b64url(b'0123456789abcdef')


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from cognitrix.common import webpush
    from cognitrix.models.push_subscription import PushSubscription

    await _initialize_routes_db(tmp_path)
    await PushSubscription._create_table_async()
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    monkeypatch.delenv('COGNITRIX_VAPID_PRIVATE_KEY', raising=False)
    monkeypatch.setattr(webpush, '_key_file', lambda: tmp_path / 'vapid.pem')
    monkeypatch.setattr(webpush, '_private_key', None)
    yield


def test_a_browser_can_read_what_the_server_encrypts():
    from cognitrix.common import webpush

    key, p256dh, auth = _browser()
    message = webpush.encrypt(b'{"title":"Hi"}', p256dh, auth)
    assert message[16:20] == (4096).to_bytes(4, 'big') and message[20] == 65
    assert webpush.decrypt(message, key, auth) == b'{"title":"Hi"}'
    # Another browser's keys can't.
    other, _, _ = _browser()
    with pytest.raises(Exception):  # noqa: B017 - any failure to decrypt will do
        webpush.decrypt(message, other, auth)


def test_only_real_push_services_are_posted_to():
    from cognitrix.common.webpush import endpoint_allowed

    assert endpoint_allowed(ENDPOINT)
    assert endpoint_allowed('https://updates.push.services.mozilla.com/wpush/v2/x')
    assert endpoint_allowed('https://web.push.apple.com/abc')
    assert endpoint_allowed('https://wns2-by3p.notify.windows.com/w/?token=x')
    for bad in (
        'http://fcm.googleapis.com/fcm/send/abc',
        'https://169.254.169.254/latest/meta-data',
        'https://localhost:8000/api',
        'https://fcm.googleapis.com.evil.example/x',
        'https://evil.example/fcm.googleapis.com',
        'https://user@fcm.googleapis.com/x',
        'https://fcm.googleapis.com:8443/x',
    ):
        assert not endpoint_allowed(bad), bad


async def test_vapid_header_is_signed_for_the_push_service(db):
    import jwt

    from cognitrix.common import webpush

    header = webpush.vapid_header(ENDPOINT)
    token = header.split('t=')[1].split(',')[0]
    claims = jwt.decode(token, webpush.private_key().public_key(), algorithms=['ES256'],
                        audience='https://fcm.googleapis.com')
    assert claims['sub'].startswith('https://')
    assert header.endswith(f'k={webpush.public_key()}')
    assert len(webpush.b64url_decode(webpush.public_key())) == 65


async def test_subscribe_send_and_forget_gone_browsers(db, monkeypatch):
    from cognitrix.api.routes import push
    from cognitrix.common import webpush
    from cognitrix.models.push_subscription import PushSubscription

    ctx = _ctx('u1')
    key, p256dh, auth = _browser()
    with pytest.raises(HTTPException):
        await push.subscribe(push.SubscriptionBody(
            endpoint='https://internal.example/hook', keys={'p256dh': p256dh, 'auth': auth}), ctx)
    saved = await push.subscribe(push.SubscriptionBody(
        endpoint=ENDPOINT, keys={'p256dh': p256dh, 'auth': auth}, label='Chrome on Android'), ctx)
    gone = await push.subscribe(push.SubscriptionBody(
        endpoint=ENDPOINT + '-gone', keys={'p256dh': p256dh, 'auth': auth}), ctx)
    state = await push.push_state(ctx)
    assert state['available'] and len(state['subscriptions']) == 2
    assert 'p256dh' not in json.dumps(state)
    assert (await push.push_state(_ctx('u2')))['subscriptions'] == []

    posted = []

    class Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, content, headers):
            posted.append((url, content, headers))

            class Response:
                status_code = 410 if url.endswith('-gone') else 201
            return Response()

    monkeypatch.setattr(webpush.httpx, 'AsyncClient', Client)
    assert await webpush.send_to_user(str(ctx.user.id), 'Approval needed', 'Run shell?', url='/tasks/1') == 1
    assert len(posted) == 2
    url, content, headers = posted[0]
    assert headers['Content-Encoding'] == 'aes128gcm' and headers['Authorization'].startswith('vapid t=')
    assert json.loads(webpush.decrypt(content, key, auth)) == {
        'title': 'Approval needed', 'body': 'Run shell?', 'url': '/tasks/1', 'tag': ''}
    left = await PushSubscription.find({'user_id': str(ctx.user.id)})
    assert [str(r.id) for r in left] == [saved['id']] and gone['id'] != saved['id']

    # Nobody else can remove it; the owner can.
    with pytest.raises(HTTPException):
        await push.remove(saved['id'], _ctx('u2'))
    await push.remove(saved['id'], ctx)
    assert await webpush.send_to_user(str(ctx.user.id), 'x') == 0


async def test_an_agent_alert_is_pushed(db, monkeypatch):
    from cognitrix.common import webpush
    from cognitrix.tasks.automation import AgentNotification, create_notification

    await AgentNotification._create_table_async()
    seen = []
    monkeypatch.setattr(webpush, 'send_soon', lambda user, title, body='', **kw: seen.append((user, title, body, kw['url'])))
    await create_notification('u1', 'Stock is low', 'Only 3 left')
    assert seen == [('u1', 'Stock is low', 'Only 3 left', '/inbox')]
