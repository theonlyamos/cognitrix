"""Google / GitHub sign-in: provider setup, account matching, signup rule,
and the desktop app's poll hand-off."""
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'oauth.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    return db_file


@pytest.fixture
def google(monkeypatch):
    from cognitrix.api.routes import oauth

    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'cid')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'secret')
    monkeypatch.delenv('GITHUB_CLIENT_ID', raising=False)
    monkeypatch.delenv('COGNITRIX_ALLOW_SIGNUP', raising=False)
    monkeypatch.delenv('COGNITRIX_PUBLIC_URL', raising=False)
    monkeypatch.setattr(oauth, '_flows', {})
    monkeypatch.setattr(oauth, '_results', {})
    profile = {'email': 'amos@example.com', 'name': 'Amos'}

    async def exchange(provider, code, redirect_uri):
        assert code == 'good-code'
        return 'provider-token'

    async def fetch_profile(provider, token):
        return profile['email'], profile['name']

    monkeypatch.setattr(oauth, '_exchange_code', exchange)
    monkeypatch.setattr(oauth, '_profile', fetch_profile)
    return profile


def _client():
    from cognitrix.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url='http://127.0.0.1:48210')


async def _start(client, poll=None):
    params = {'poll': poll} if poll else {}
    response = await client.get('/api/v1/auth/oauth/google/start', params=params)
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers['location']).query)
    assert query['redirect_uri'] == ['http://127.0.0.1:48210/api/v1/auth/oauth/google/callback']
    return query['state'][0]


@pytest.mark.asyncio
async def test_only_configured_providers_are_offered(db, google):
    async with _client() as client:
        assert (await client.get('/api/v1/auth/oauth/providers')).json() == {'google': True, 'github': False}
        assert (await client.get('/api/v1/auth/oauth/github/start')).status_code == 404


@pytest.mark.asyncio
async def test_first_account_signs_up_and_lands_in_the_web_app(db, google):
    from cognitrix.models import User

    async with _client() as client:
        state = await _start(client)
        response = await client.get('/api/v1/auth/oauth/google/callback', params={'code': 'good-code', 'state': state})
        assert response.status_code == 302
        assert response.headers['location'].startswith('/oauth/complete#token=')
        token = response.headers['location'].split('token=')[1]
        me = await client.get('/api/v1/auth/user', headers={'Authorization': f'Bearer {token}'})
        assert me.json()['email'] == 'amos@example.com'
        # The state is single use.
        again = await client.get('/api/v1/auth/oauth/google/callback', params={'code': 'good-code', 'state': state})
        assert 'expired' in again.headers['location']

    user = await User.find_one({'email': 'amos@example.com'})
    assert user.password == ''
    # A provider-only account can't be signed into with an empty password.
    from cognitrix.common.security import authenticate
    assert not await authenticate('amos@example.com', '')


@pytest.mark.asyncio
async def test_existing_account_signs_in_but_closed_signup_blocks_new_ones(db, google):
    from cognitrix.common.utils import Utils
    from cognitrix.models import User

    await User(name='Owner', email='amos@example.com', password=Utils.hash_password('pw-123456')).save()
    async with _client() as client:
        state = await _start(client)
        ok = await client.get('/api/v1/auth/oauth/google/callback', params={'code': 'good-code', 'state': state})
        assert ok.headers['location'].startswith('/oauth/complete#token=')

        google['email'] = 'stranger@example.com'
        state = await _start(client)
        blocked = await client.get('/api/v1/auth/oauth/google/callback', params={'code': 'good-code', 'state': state})
        assert blocked.headers['location'].startswith('/?oauth_error=')
        assert 'signup+is+closed' in blocked.headers['location']
    assert await User.find_one({'email': 'stranger@example.com'}) is None


@pytest.mark.asyncio
async def test_desktop_app_collects_the_token_by_polling_once(db, google):
    poll = 'a' * 48
    async with _client() as client:
        assert (await client.post('/api/v1/auth/oauth/poll', json={'poll': poll})).json() == {'status': 'pending'}
        state = await _start(client, poll=poll)
        page = await client.get('/api/v1/auth/oauth/google/callback', params={'code': 'good-code', 'state': state})
        assert page.status_code == 200 and 'signed in' in page.text
        assert 'eyJ' not in page.text  # the token never reaches the browser tab

        done = (await client.post('/api/v1/auth/oauth/poll', json={'poll': poll})).json()
        assert done['status'] == 'done' and done['access_token']
        assert (await client.post('/api/v1/auth/oauth/poll', json={'poll': poll})).json() == {'status': 'pending'}

        cancelled_state = await _start(client, poll='b' * 48)
        await client.get('/api/v1/auth/oauth/google/callback', params={'error': 'access_denied', 'state': cancelled_state})
        failed = (await client.post('/api/v1/auth/oauth/poll', json={'poll': 'b' * 48})).json()
        assert failed == {'status': 'error', 'error': 'Sign-in was cancelled.'}
