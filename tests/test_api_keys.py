"""API key model, unified auth dependency, and rate limiting."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cognitrix.common import security
from cognitrix.common.rate_limit import _windows, check_rate_limit
from cognitrix.common.security import AuthContext, crud_scope, get_auth_context, jwt_only, require
from cognitrix.models.api_key import (
    KEY_PREFIX,
    VALID_SCOPES,
    APIKey,
    generate_secret,
    hash_secret,
    normalize_expiry,
)


def _key(**overrides) -> APIKey:
    base = dict(
        name='test', user_id='u1', key_hash='h', prefix='ctx_abc',
        scopes=['read'], webhook_secret='s',
    )
    base.update(overrides)
    return APIKey(**base)


def _request(headers: dict) -> SimpleNamespace:
    return SimpleNamespace(headers=headers, method='GET')


# --- model helpers ----------------------------------------------------------

def test_generate_secret_format_and_hash():
    secret = generate_secret()
    assert secret.startswith(KEY_PREFIX)
    assert len(secret) > 30
    assert hash_secret(secret) != hash_secret(generate_secret())
    assert len(hash_secret(secret)) == 64


def test_normalize_expiry_variants():
    assert normalize_expiry(None) is None
    assert normalize_expiry('') is None
    assert normalize_expiry('2030-01-02T03:04:05') == '2030-01-02 03:04:05'
    # Aware datetimes convert to UTC then drop the offset.
    assert normalize_expiry('2030-01-02T03:04:05+02:00') == '2030-01-02 01:04:05'
    with pytest.raises(ValueError):
        normalize_expiry('not-a-date')


def test_is_expired():
    assert not _key(expires_at=None).is_expired()
    assert _key(expires_at='2000-01-01 00:00:00').is_expired()
    assert not _key(expires_at='2999-01-01 00:00:00').is_expired()
    assert not _key(expires_at='garbage').is_expired()  # unparseable = no lockout


def test_scope_and_allowlist_helpers():
    key = _key(scopes=['chat', 'read'], allowed_agents=['a1'], allowed_teams=[])
    assert key.has_scope('chat') and not key.has_scope('write')
    assert key.agent_allowed('a1') and not key.agent_allowed('a2')
    assert key.team_allowed('anything')  # empty allowlist = all


def test_null_list_coercion():
    key = _key(scopes=None, allowed_agents=None, allowed_teams=None, revoked=None)
    assert key.scopes == [] and key.allowed_agents == [] and key.allowed_teams == []
    assert key.revoked is False


# --- AuthContext ------------------------------------------------------------

def test_jwt_context_passes_everything():
    ctx = AuthContext(user=SimpleNamespace(id='u1'), api_key=None)
    assert ctx.has_scope('write') and ctx.agent_allowed('x') and ctx.team_allowed('y')


def test_key_context_narrows():
    ctx = AuthContext(user=SimpleNamespace(id='u1'), api_key=_key(scopes=['read'], allowed_agents=['a1']))
    assert ctx.has_scope('read') and not ctx.has_scope('run')
    assert ctx.agent_allowed('a1') and not ctx.agent_allowed('a2')


# --- dependencies -----------------------------------------------------------

async def test_require_blocks_missing_scope():
    ctx = AuthContext(user=SimpleNamespace(id='u1'), api_key=_key(scopes=['read']))
    dep = require('run')
    with pytest.raises(HTTPException) as e:
        await dep(ctx=ctx)
    assert e.value.status_code == 403
    assert 'run' in e.value.detail
    assert await dep(ctx=AuthContext(user=SimpleNamespace(id='u1'))) is not None  # JWT passes


async def test_crud_scope_infers_method():
    read_key = AuthContext(user=SimpleNamespace(id='u1'), api_key=_key(scopes=['read']))
    assert await crud_scope(_request({}), read_key) is read_key
    post = SimpleNamespace(headers={}, method='POST')
    with pytest.raises(HTTPException) as e:
        await crud_scope(post, read_key)
    assert e.value.status_code == 403


async def test_jwt_only_rejects_keys():
    with pytest.raises(HTTPException) as e:
        await jwt_only(AuthContext(user=SimpleNamespace(id='u1'), api_key=_key()))
    assert e.value.status_code == 403
    jwt_ctx = AuthContext(user=SimpleNamespace(id='u1'))
    assert await jwt_only(jwt_ctx) is jwt_ctx


async def test_get_auth_context_no_credential_401():
    with pytest.raises(HTTPException) as e:
        await get_auth_context(_request({}))
    assert e.value.status_code == 401
    assert 'WWW-Authenticate' in (e.value.headers or {})


async def test_get_auth_context_key_paths(monkeypatch):
    secret = generate_secret()
    stored = _key(key_hash=hash_secret(secret), scopes=['read'])
    stored.id = 'k1'
    user = SimpleNamespace(id='u1', password='hash')

    async def fake_find_one(query):
        return stored if query.get('key_hash') == stored.key_hash else None

    async def fake_get_user(user_id):
        return user

    async def fake_stamp(key):
        return None

    monkeypatch.setattr(security.APIKey, 'find_one', staticmethod(fake_find_one))
    monkeypatch.setattr(security.User, 'get', staticmethod(fake_get_user))
    monkeypatch.setattr(security, '_stamp_last_used', fake_stamp)

    ctx = await get_auth_context(_request({'Authorization': f'Bearer {secret}'}))
    assert ctx.api_key is stored
    assert not hasattr(ctx.user, 'password') or not ctx.user.password

    # X-API-Key header works too
    user.password = 'hash'
    ctx = await get_auth_context(_request({'X-API-Key': secret}))
    assert ctx.api_key is stored

    # Unknown key → uniform 401
    with pytest.raises(HTTPException) as e:
        await get_auth_context(_request({'Authorization': f'Bearer {KEY_PREFIX}nope'}))
    assert e.value.status_code == 401

    # Revoked and expired → same 401
    stored.revoked = True
    with pytest.raises(HTTPException):
        await get_auth_context(_request({'X-API-Key': secret}))
    stored.revoked = False
    stored.expires_at = '2000-01-01 00:00:00'
    with pytest.raises(HTTPException):
        await get_auth_context(_request({'X-API-Key': secret}))


# --- invoke-path allowlist enforcement --------------------------------------

def test_check_task_allowlists():
    from cognitrix.api.routes.tasks import _check_task_allowlists

    task = SimpleNamespace(team_id='t1', assigned_agents=['a1', 'a2'])
    jwt_ctx = AuthContext(user=SimpleNamespace(id='u1'))
    _check_task_allowlists(jwt_ctx, task)  # JWT passes

    ok = AuthContext(user=SimpleNamespace(id='u1'),
                     api_key=_key(scopes=['run'], allowed_teams=['t1'], allowed_agents=[]))
    _check_task_allowlists(ok, task)  # allowlisted team, all agents

    wrong_team = AuthContext(user=SimpleNamespace(id='u1'),
                             api_key=_key(scopes=['run'], allowed_teams=['other']))
    with pytest.raises(HTTPException) as e:
        _check_task_allowlists(wrong_team, task)
    assert e.value.status_code == 403

    wrong_agent = AuthContext(user=SimpleNamespace(id='u1'),
                              api_key=_key(scopes=['run'], allowed_agents=['a1']))
    with pytest.raises(HTTPException) as e:
        _check_task_allowlists(wrong_agent, task)  # a2 not allowlisted
    assert e.value.status_code == 403


# --- rate limiting ----------------------------------------------------------

def test_rate_limit_window():
    _windows.clear()
    key = _key(rate_limit=3)
    key.id = 'rl1'
    for _ in range(3):
        check_rate_limit(key)
    with pytest.raises(HTTPException) as e:
        check_rate_limit(key)
    assert e.value.status_code == 429
    assert 'Retry-After' in (e.value.headers or {})


def test_rate_limit_default_from_env(monkeypatch):
    _windows.clear()
    monkeypatch.setenv('COGNITRIX_API_RATE_LIMIT', '2')
    key = _key(rate_limit=None)
    key.id = 'rl2'
    check_rate_limit(key)
    check_rate_limit(key)
    with pytest.raises(HTTPException):
        check_rate_limit(key)


# --- real sqlite round-trip -------------------------------------------------

async def test_apikey_sqlite_roundtrip(tmp_path):
    """expires_at munging + list/bool round-trips through the actual adapter.

    Uses a temp file DB — odbms opens a new connection per operation, so a
    shared :memory: database evaporates between calls.
    """
    from odbms import DBMS

    from cognitrix.config import _patch_odbms_sqlite

    db_file = str(tmp_path / 'apikeys-test.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()

    create = getattr(APIKey, '_create_table_async', None) or APIKey.create_table
    await create()

    secret = generate_secret()
    key = APIKey(
        name='rt', user_id='u1', key_hash=hash_secret(secret), prefix=secret[:12],
        scopes=['chat', 'run'], allowed_agents=['a1'], allowed_teams=[],
        webhook_secret='whs', rate_limit=5,
        expires_at=normalize_expiry('2030-06-01T12:00:00+00:00'),
    )
    await key.save()

    fetched = await APIKey.find_one({'key_hash': hash_secret(secret)})
    assert fetched is not None
    assert fetched.scopes == ['chat', 'run']
    assert fetched.allowed_agents == ['a1'] and fetched.allowed_teams == []
    assert fetched.revoked is False
    assert fetched.rate_limit == 5
    # The pre-normalized value survives the adapter's *_at munging unchanged.
    assert fetched.expires_at == '2030-06-01 12:00:00'
    assert not fetched.is_expired()

    await APIKey.update_one({'id': fetched.id}, {'revoked': True})
    again = await APIKey.find_one({'key_hash': hash_secret(secret)})
    assert again.revoked is True
