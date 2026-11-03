"""Staying signed in: token refresh and the desktop app's session length."""
import time

import jwt
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def db(tmp_path):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'refresh.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    return db_file


def _client():
    from cognitrix.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url='http://127.0.0.1:48210')


async def _user(email='amos@example.com'):
    from cognitrix.common.utils import Utils
    from cognitrix.models import User

    user = User(name='Amos', email=email, password=Utils.hash_password('longpassword'))
    await user.save()
    return user


@pytest.mark.asyncio
async def test_refresh_returns_a_new_working_token(db):
    from cognitrix.common.constants import JWT_ALGORITHM, JWT_SECRET_KEY
    from cognitrix.common.security import create_access_token

    await _user()
    old = create_access_token({'sub': 'amos@example.com'})
    async with _client() as client:
        response = await client.post('/api/v1/auth/refresh', headers={'Authorization': f'Bearer {old}'})
        assert response.status_code == 200
        token = response.json()['access_token']
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        assert payload['sub'] == 'amos@example.com'
        me = await client.get('/api/v1/auth/user', headers={'Authorization': f'Bearer {token}'})
        assert me.status_code == 200


@pytest.mark.asyncio
async def test_refresh_refuses_bad_and_signed_out_tokens(db):
    from cognitrix.common.security import create_access_token
    from cognitrix.models import User

    user = await _user()
    async with _client() as client:
        bad = await client.post('/api/v1/auth/refresh', headers={'Authorization': 'Bearer nope'})
        assert bad.status_code == 401
        missing = await client.post('/api/v1/auth/refresh')
        assert missing.status_code == 401

        token = create_access_token({'sub': 'amos@example.com'})
        await User.update_one({'id': user.id}, {'tokens_valid_after': int(time.time()) + 5})
        revoked = await client.post('/api/v1/auth/refresh', headers={'Authorization': f'Bearer {token}'})
        assert revoked.status_code == 401


def test_desktop_session_is_never_shorter_than_30_days(monkeypatch):
    from cognitrix import desktop

    monkeypatch.setenv('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', '180')
    assert desktop.desktop_session_minutes() == 60 * 24 * 30
    monkeypatch.setenv('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', str(60 * 24 * 90))
    assert desktop.desktop_session_minutes() == 60 * 24 * 90
    monkeypatch.setenv('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', 'junk')
    assert desktop.desktop_session_minutes() == 60 * 24 * 30
