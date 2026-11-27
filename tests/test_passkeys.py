"""Passkeys (cognitrix/passkeys.py), checked with a software authenticator."""
import hashlib
import json
import os
import struct
from types import SimpleNamespace

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from tests.test_session_ownership_routes import _initialize_routes_db

CHROME_WIN = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
WINDOWS_HELLO = '08987058-cadc-4b81-b6e1-30de50dcbe96'


class Authenticator:
    """Makes and uses one passkey, like Windows Hello would."""

    def __init__(self, aaguid: str = WINDOWS_HELLO):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.aaguid = bytes.fromhex(aaguid.replace('-', ''))
        self.count = 0
        self.user_handle = b''

    def _cose(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, 'big'), -3: nums.y.to_bytes(32, 'big')})

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps({'type': kind, 'challenge': challenge, 'origin': origin}).encode()

    def create(self, options: dict, origin: str) -> dict:
        rp_id = options['rp']['id']
        self.user_handle = base64url_to_bytes(options['user']['id'])
        flags = 0x01 | 0x04 | 0x40 | 0x08 | 0x10  # UP, UV, AT, BE, BS
        auth_data = (hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack('>I', self.count)
                     + self.aaguid + struct.pack('>H', len(self.credential_id)) + self.credential_id + self._cose())
        attestation = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth_data})
        cid = bytes_to_base64url(self.credential_id)
        return {
            'id': cid, 'rawId': cid, 'type': 'public-key',
            'response': {
                'clientDataJSON': bytes_to_base64url(self._client_data('webauthn.create', options['challenge'], origin)),
                'attestationObject': bytes_to_base64url(attestation),
                'transports': ['internal', 'hybrid'],
            },
        }

    def get(self, options: dict, origin: str, *, rp_id: str | None = None) -> dict:
        self.count += 1
        flags = 0x01 | 0x04 | 0x08 | 0x10
        auth_data = (hashlib.sha256((rp_id or options['rpId']).encode()).digest() + bytes([flags])
                     + struct.pack('>I', self.count))
        client_data = self._client_data('webauthn.get', options['challenge'], origin)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        cid = bytes_to_base64url(self.credential_id)
        return {
            'id': cid, 'rawId': cid, 'type': 'public-key',
            'response': {
                'clientDataJSON': bytes_to_base64url(client_data),
                'authenticatorData': bytes_to_base64url(auth_data),
                'signature': bytes_to_base64url(signature),
                'userHandle': bytes_to_base64url(self.user_handle),
            },
        }


@pytest.fixture
async def world(tmp_path, monkeypatch):
    from cognitrix import passkeys, signins
    from cognitrix.common.utils import Utils
    from cognitrix.models import User
    from cognitrix.models.sign_in import MODELS

    monkeypatch.delenv('COGNITRIX_PASSKEYS', raising=False)
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    await _initialize_routes_db(tmp_path)
    for model in (User, *MODELS):
        await model._create_table_async()
    try:
        from cognitrix.tasks.automation import AgentNotification
        await AgentNotification._create_table_async()
    except Exception:  # noqa: BLE001
        pass
    signins._checks.clear()
    passkeys._starts.clear()
    await User(name='Ada', email='ada@example.com', password=Utils.hash_password('old-password')).save()
    ada = await User.find_one({'email': 'ada@example.com'})
    return SimpleNamespace(ada=ada)


def _request(origin='https://cog.example.com', host='cog.example.com', ip='41.66.10.20', ua=CHROME_WIN, auth=''):
    headers = {'user-agent': ua, 'origin': origin, 'host': host}
    if auth:
        headers['authorization'] = f'Bearer {auth}'
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=ip), query_params={},
                           base_url=f'https://{host}/')


async def _ctx(token):
    from cognitrix.common.security import _resolve_jwt
    return await _resolve_jwt(token)


async def _login():
    from cognitrix.api.routes import auth
    res = await auth.login(auth.LoginForm(username='ada@example.com', password='old-password'), _request())
    return await _ctx(json.loads(res.body)['access_token'])


async def _add_passkey(ctx, authenticator, request=None, name=''):
    from cognitrix.api.routes import passkeys as routes
    request = request or _request()
    start = await routes.register_options(request, ctx)
    assert start['options']['authenticatorSelection']['residentKey'] == 'required'
    credential = authenticator.create(start['options'], request.headers['origin'])
    return await routes.register(routes.Finish(ceremony=start['ceremony'], credential=credential, name=name),
                                 request, ctx)


async def test_add_a_passkey_then_sign_in_with_it(world):
    from cognitrix.api.routes import passkeys as routes
    from cognitrix.api.routes import sign_ins

    laptop = await _login()
    key = Authenticator()
    added = await _add_passkey(laptop, key)
    assert added['name'] == 'Windows Hello' and added['site'] == 'cog.example.com' and added['synced']
    assert [p['id'] for p in (await routes.list_passkeys(laptop))['passkeys']] == [added['id']]

    request = _request()
    start = await routes.sign_in_options(request)
    assert start['options']['allowCredentials'] == []  # no email asked: the passkey says whose it is
    body = await routes.passkey_sign_in(
        routes.Finish(ceremony=start['ceremony'], credential=key.get(start['options'], 'https://cog.example.com')),
        request)
    assert body['user']['email'] == 'ada@example.com' and 'password' not in body['user']
    phone = await _ctx(body['access_token'])
    assert phone.sid and phone.sid != laptop.sid

    listed = (await sign_ins.list_sign_ins(laptop))['sign_ins']
    assert ('passkey', 'Windows Hello') in [(s['method'], s['approved_by']) for s in listed]
    [saved] = (await routes.list_passkeys(laptop))['passkeys']
    assert saved['last_used_at']


async def test_a_challenge_works_once(world):
    from cognitrix.api.routes import passkeys as routes

    key = Authenticator()
    await _add_passkey(await _login(), key)
    request = _request()
    start = await routes.sign_in_options(request)
    credential = key.get(start['options'], 'https://cog.example.com')
    await routes.passkey_sign_in(routes.Finish(ceremony=start['ceremony'], credential=credential), request)
    with pytest.raises(HTTPException) as replay:
        await routes.passkey_sign_in(routes.Finish(ceremony=start['ceremony'], credential=credential), request)
    assert 'expired' in replay.value.detail


async def test_wrong_key_site_or_unknown_passkey_is_refused(world):
    from cognitrix.api.routes import passkeys as routes

    key = Authenticator()
    await _add_passkey(await _login(), key)

    stranger = Authenticator()
    stranger.user_handle = key.user_handle
    stranger.credential_id = key.credential_id  # same id, different private key
    request = _request()
    start = await routes.sign_in_options(request)
    with pytest.raises(HTTPException) as bad_sig:
        await routes.passkey_sign_in(routes.Finish(
            ceremony=start['ceremony'], credential=stranger.get(start['options'], 'https://cog.example.com')), request)
    assert bad_sig.value.status_code == 401

    start = await routes.sign_in_options(request)
    with pytest.raises(HTTPException) as unknown:
        await routes.passkey_sign_in(routes.Finish(
            ceremony=start['ceremony'], credential=Authenticator().get(start['options'], 'https://cog.example.com')),
            request)
    assert unknown.value.status_code == 401

    # Signed for another site (a phishing page) -> the rpIdHash doesn't match.
    start = await routes.sign_in_options(request)
    with pytest.raises(HTTPException):
        await routes.passkey_sign_in(routes.Finish(
            ceremony=start['ceremony'],
            credential=key.get(start['options'], 'https://cog.example.com', rp_id='evil.example')), request)


async def test_pages_that_cannot_use_passkeys(world):
    from cognitrix.api.routes import passkeys as routes

    for origin, host, words in (
        ('http://127.0.0.1:48210', '127.0.0.1:48210', 'localhost:48210'),
        ('http://cog.example.com', 'cog.example.com', 'https'),
        ('https://evil.example', 'cog.example.com', 'not part of this'),
    ):
        with pytest.raises(HTTPException) as refused:
            await routes.sign_in_options(_request(origin=origin, host=host))
        assert words in refused.value.detail
    # localhost is fine over http (browsers treat it as secure).
    out = await routes.sign_in_options(_request(origin='http://localhost:5173', host='localhost:8000'))
    assert out['options']['rpId'] == 'localhost'


async def test_rename_remove_and_turned_off(world, monkeypatch):
    from cognitrix.api.routes import passkeys as routes

    ctx = await _login()
    added = await _add_passkey(ctx, Authenticator(aaguid='00000000-0000-0000-0000-000000000000'))
    assert added['name'] == 'Chrome on Windows'  # unknown provider: named after the browser
    renamed = await routes.rename_passkey(added['id'], routes.Rename(name='  Work   laptop '), ctx)
    assert renamed['name'] == 'Work laptop'
    await routes.remove_passkey(added['id'], ctx)
    assert (await routes.list_passkeys(ctx))['passkeys'] == []
    with pytest.raises(HTTPException):
        await routes.remove_passkey(added['id'], ctx)

    monkeypatch.setenv('COGNITRIX_PASSKEYS', 'false')
    with pytest.raises(HTTPException) as off:
        await routes.sign_in_options(_request())
    assert off.value.status_code == 403


async def test_someone_elses_passkey_id_is_not_yours(world):
    from cognitrix.api.routes import passkeys as routes
    from cognitrix.common.utils import Utils
    from cognitrix.models import User

    added = await _add_passkey(await _login(), Authenticator())
    await User(name='Bo', email='bo@example.com', password=Utils.hash_password('bo-password')).save()
    from cognitrix.api.routes import auth
    res = await auth.login(auth.LoginForm(username='bo@example.com', password='bo-password'), _request())
    bo = await _ctx(json.loads(res.body)['access_token'])
    with pytest.raises(HTTPException) as nope:
        await routes.remove_passkey(added['id'], bo)
    assert nope.value.status_code == 404


async def test_desktop_app_signs_in_through_the_browser(world, monkeypatch):
    from cognitrix.api.routes import passkeys as routes

    key = Authenticator()
    browser = _request(origin='http://localhost:48210', host='localhost:48210', ip='127.0.0.1')
    app = _request(origin='http://127.0.0.1:48210', host='127.0.0.1:48210', ip='127.0.0.1')

    # Not on a web server, and not from another computer.
    with pytest.raises(HTTPException):
        await routes.start_sign_in_handoff(routes.StartHandoff(), app)
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    with pytest.raises(HTTPException):
        await routes.start_sign_in_handoff(routes.StartHandoff(), _request(ip='41.66.10.20'))

    # Adding a passkey from the desktop app also goes through the browser.
    laptop = await _login()
    handoff = await routes.start_register_handoff(routes.StartHandoff(), app, laptop)
    assert (await routes.handoff_info(handoff['handoff'], browser)) == {
        'kind': 'register', 'account': 'ada@example.com', 'expires_in': pytest.approx(300, abs=5)}
    start = await routes.handoff_options(handoff['handoff'], browser)
    await routes.handoff_finish(handoff['handoff'], routes.Finish(
        ceremony=start['ceremony'], credential=key.create(start['options'], 'http://localhost:48210')), browser)
    done = await routes.collect_handoff(routes.Secret(secret=handoff['secret']), app)
    assert done['status'] == 'done' and done['passkey']['site'] == 'localhost'

    # Signing in.
    handoff = await routes.start_sign_in_handoff(routes.StartHandoff(device_name='Cognitrix desktop app'), app)
    assert handoff['path'] == f"/passkey?handoff={handoff['handoff']}"
    assert (await routes.collect_handoff(routes.Secret(secret=handoff['secret']), app))['status'] == 'waiting'
    start = await routes.handoff_options(handoff['handoff'], browser)
    # A ceremony made for the handoff can't sign the browser itself in.
    with pytest.raises(HTTPException):
        await routes.passkey_sign_in(routes.Finish(
            ceremony=start['ceremony'], credential=key.get(start['options'], 'http://localhost:48210')), browser)
    start = await routes.handoff_options(handoff['handoff'], browser)
    out = await routes.handoff_finish(handoff['handoff'], routes.Finish(
        ceremony=start['ceremony'], credential=key.get(start['options'], 'http://localhost:48210')), browser)
    assert out == {'kind': 'sign_in', 'account': 'ada@example.com'}
    with pytest.raises(HTTPException):  # the link works once
        await routes.handoff_info(handoff['handoff'], browser)
    got = await routes.collect_handoff(routes.Secret(secret=handoff['secret']), app)
    assert got['status'] == 'done' and got['user']['email'] == 'ada@example.com'
    ctx = await _ctx(got['access_token'])
    from cognitrix.models.sign_in import SignIn
    row = await SignIn.find_one({'sid': ctx.sid})
    assert (row.kind, row.method, row.name) == ('desktop', 'passkey', 'Cognitrix desktop app')
    # The token is handed over once.
    assert (await routes.collect_handoff(routes.Secret(secret=handoff['secret']), app))['status'] == 'expired'
    assert (await routes.collect_handoff(routes.Secret(secret='wrong'), app))['status'] == 'expired'


async def test_canceled_handoff(world, monkeypatch):
    from cognitrix.api.routes import passkeys as routes

    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    app = _request(origin='http://127.0.0.1:48210', host='127.0.0.1:48210', ip='127.0.0.1')
    handoff = await routes.start_sign_in_handoff(routes.StartHandoff(), app)
    await routes.cancel_handoff(routes.Secret(secret=handoff['secret']), app)
    with pytest.raises(HTTPException) as gone:
        await routes.handoff_options(handoff['handoff'], app)
    assert gone.value.status_code == 410


async def test_options_say_what_the_sign_in_page_offers(world, monkeypatch):
    from cognitrix.api.routes import auth

    opts = await auth.auth_options()
    assert opts['passkeys'] is True and opts['passkey_handoff'] is False
    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    assert (await auth.auth_options())['passkey_handoff'] is True


async def test_register_link_stops_when_its_device_is_signed_out(world, monkeypatch):
    from cognitrix import signins
    from cognitrix.api.routes import passkeys as routes

    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    app = _request(origin='http://127.0.0.1:48210', host='127.0.0.1:48210', ip='127.0.0.1')
    browser = _request(origin='http://localhost:48210', host='localhost:48210', ip='127.0.0.1')
    laptop = await _login()
    handoff = await routes.start_register_handoff(routes.StartHandoff(), app, laptop)
    await signins.end(str(world.ada.id), laptop.sid)
    for call in (routes.handoff_info, routes.handoff_options):
        with pytest.raises(HTTPException) as gone:
            await call(handoff['handoff'], browser)
        assert gone.value.status_code == 410

    # Signed out everywhere (password change) after the link was made: also dead.
    other = await _login()
    handoff = await routes.start_register_handoff(routes.StartHandoff(), app, other)
    from cognitrix.models import User
    await User.update_one({'id': world.ada.id}, {'tokens_valid_after': int(__import__('time').time()) + 5})
    with pytest.raises(HTTPException):
        await routes.handoff_options(handoff['handoff'], browser)
