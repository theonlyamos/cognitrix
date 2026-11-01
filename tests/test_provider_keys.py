"""Settings › Models and keys: provider keys saved from the UI."""
import json

import pytest
from fastapi import HTTPException

# Obviously fake, low-entropy keys so secret scanners don't flag them.
KEY_A = 'fake' + 'x' * 12 + '9876'
KEY_B = 'fake' + 'y' * 12 + '1234'
KEY_C = 'fake' + 'z' * 12 + '4444'


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite, settings

    db_file = str(tmp_path / 'keys.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()

    monkeypatch.setattr(settings, 'workdir', tmp_path)
    monkeypatch.setenv('COGNITRIX_SECRETS_KEY', 'test-secrets-key')
    from cognitrix.common import secret_box
    secret_box.reset_key_cache()
    monkeypatch.delenv('COGNITRIX_ADMIN_EMAILS', raising=False)
    for name in ('ANTHROPIC', 'OPENAI', 'GOOGLE', 'OPENROUTER', 'GROQ', 'CEREBRAS'):
        monkeypatch.delenv(f'{name}_API_KEY', raising=False)
    for attr in ('anthropic_api_key', 'openai_api_key', 'google_api_key', 'groq_api_key'):
        monkeypatch.setattr(settings, attr, '', raising=False)
    from cognitrix.providers import saved_keys
    monkeypatch.setattr(saved_keys, '_cache', None)
    yield tmp_path
    secret_box.reset_key_cache()


async def _user(name, email):
    from cognitrix.models import User

    user = User(name=name, email=email, password='x' * 20)
    await user.save()
    return await User.find_one({'email': email})


def _ctx(user):
    from cognitrix.common.security import AuthContext

    return AuthContext(user=user)


def _row(listing, provider):
    return next(p for p in listing['providers'] if p['id'] == provider)


async def test_admin_saves_key_sealed_and_engine_uses_it(db):
    from cognitrix.api.routes import providers
    from cognitrix.providers.base import _resolve_runtime_config

    admin = _ctx(await _user('Ada', 'ada@example.com'))
    listing = await providers.list_provider_keys(admin)
    assert listing['can_edit'] is True
    assert _row(listing, 'groq')['source'] == 'none'

    row = await providers.save_provider_key('groq', providers.KeyBody(api_key=f' {KEY_A} '), admin)
    assert row['source'] == 'saved'
    assert row['hint'] == '…9876'

    on_disk = (db / 'provider_keys.json').read_text()
    assert KEY_A not in on_disk
    assert json.loads(on_disk)['groq'].startswith('enc:v1:')

    assert _resolve_runtime_config('groq')['api_key'] == KEY_A
    assert KEY_A not in repr(await providers.list_provider_keys(admin))

    row = await providers.remove_provider_key('groq', admin)
    assert row['source'] == 'none'
    with pytest.raises(ValueError):
        _resolve_runtime_config('groq')


async def test_environment_key_wins_and_cannot_be_overwritten(db, monkeypatch):
    from cognitrix.api.routes import providers

    monkeypatch.setenv('OPENAI_API_KEY', 'fake' + 'e' * 12)
    admin = _ctx(await _user('Ada', 'ada@example.com'))
    assert _row(await providers.list_provider_keys(admin), 'openai')['source'] == 'env'
    with pytest.raises(HTTPException) as err:
        await providers.save_provider_key('openai', providers.KeyBody(api_key='fake' + 'o' * 12), admin)
    assert err.value.status_code == 409


async def test_only_admin_can_change_keys(db):
    from cognitrix.api.routes import providers

    await _user('Ada', 'ada@example.com')  # first account is the admin
    other = _ctx(await _user('Bob', 'bob@example.com'))
    assert (await providers.list_provider_keys(other))['can_edit'] is False
    with pytest.raises(HTTPException) as err:
        await providers.save_provider_key('groq', providers.KeyBody(api_key=KEY_B), other)
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        await providers.remove_provider_key('groq', other)
    assert err.value.status_code == 403


async def test_rejects_bad_keys_and_unknown_providers(db):
    from cognitrix.api.routes import providers

    admin = _ctx(await _user('Ada', 'ada@example.com'))
    for bad in ('', 'has space', 'x' * 600):
        with pytest.raises(HTTPException) as err:
            await providers.save_provider_key('groq', providers.KeyBody(api_key=bad), admin)
        assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        await providers.save_provider_key('ollama', providers.KeyBody(api_key='abc12345'), admin)
    assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        await providers.save_provider_key('nope', providers.KeyBody(api_key='abc12345'), admin)
    assert err.value.status_code == 404


async def test_saved_key_counts_for_onboarding_and_voice_and_trust_check(db):
    from cognitrix.api.routes import onboarding
    from cognitrix.providers.base import _server_held_keys
    from cognitrix.providers.saved_keys import set_saved_key
    from cognitrix.providers.transcription import server_key

    set_saved_key('groq', KEY_C)
    assert onboarding._has_server_key('groq') is True
    assert server_key('groq') == KEY_C
    assert 'groq' in _server_held_keys()[KEY_C]


async def test_test_endpoint_reports_missing_key(db):
    from cognitrix.api.routes import providers

    admin = _ctx(await _user('Ada', 'ada@example.com'))
    result = await providers.test_provider_key('cerebras', admin)
    assert result['ok'] is False
    assert 'Missing api_key' in result['message']
