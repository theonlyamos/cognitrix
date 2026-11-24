"""The server's own setup without environment variables: the first-start
Server setup screen, keys the server makes itself, the web server settings
and `cognitrix settings`."""
import socket
from argparse import Namespace

import pytest
from fastapi import HTTPException

from cognitrix import app_settings


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from odbms import DBMS

    from cognitrix.config import _ensure_schema, _patch_odbms_sqlite

    db_file = str(tmp_path / 'setup.db')
    if hasattr(DBMS, 'initialize_async'):
        await DBMS.initialize_async('sqlite', database=db_file)
    else:
        DBMS.initialize('sqlite', database=db_file)
    _patch_odbms_sqlite()
    await _ensure_schema()
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    monkeypatch.setattr(app_settings, '_env_fallback', False)
    yield


async def test_setup_is_offered_once_before_the_first_account(db, monkeypatch):
    from cognitrix.api.routes import server_setup
    from cognitrix.api.routes.auth import auth_options
    from cognitrix.models import User

    assert (await auth_options())['server_setup'] is True
    data = await server_setup.read_server_setup()
    assert data['database']['type'] == 'sqlite' and data['queue'] == {'redis': False}

    # Keeping the built-in database and queue needs no restart.
    result = await server_setup.save_server_setup(server_setup.ServerSetupBody())
    assert result == {'restart_needed': False}
    assert (await auth_options())['server_setup'] is False
    with pytest.raises(HTTPException) as exc:
        await server_setup.read_server_setup()
    assert exc.value.status_code == 403

    # Never on a server that already has people, nor in the desktop app.
    monkeypatch.setattr(app_settings, 'server_setup_done', lambda: False)
    assert await server_setup.setup_pending() is True
    await User(name='Ada', email='ada@example.com', password='x' * 20).save()
    assert await server_setup.setup_pending() is False


async def test_desktop_never_shows_server_setup(db, monkeypatch):
    from cognitrix.api.routes import server_setup

    monkeypatch.setenv('COGNITRIX_DESKTOP', '1')
    assert await server_setup.setup_pending() is False


async def test_pointing_at_a_database_and_redis(db, monkeypatch):
    from cognitrix.api.routes import server_setup

    monkeypatch.setattr(server_setup, '_reachable', lambda host, port, timeout=3.0: (host, port) != ('bad.example', 5432))
    with pytest.raises(HTTPException, match='') as exc:
        await server_setup.save_server_setup(server_setup.ServerSetupBody(db_type='postgresql', db_host='bad.example'))
    assert exc.value.status_code == 400 and "Couldn't reach" in exc.value.detail
    assert app_settings.get('DB_TYPE', 'sqlite') == 'sqlite'

    with pytest.raises(HTTPException) as exc:
        await server_setup.save_server_setup(server_setup.ServerSetupBody(queue='redis', broker_url='http://x'))
    assert 'redis://' in exc.value.detail

    result = await server_setup.save_server_setup(server_setup.ServerSetupBody(
        db_type='postgresql', db_host='db.example', db_user='cog', db_password='pw-secret',
        queue='redis', broker_url='redis://cache.example:6379/0'))
    assert result == {'restart_needed': True}
    assert app_settings.get('DB_TYPE') == 'postgresql' and app_settings.get('DB_HOST') == 'db.example'
    assert app_settings.get('DB_PASSWORD') == 'pw-secret'
    assert 'pw-secret' not in app_settings.settings_file().read_text()
    assert app_settings.get('CELERY_BROKER_URL') == 'redis://cache.example:6379/0'


def test_reachable_checks_a_real_port():
    from cognitrix.api.routes.server_setup import _reachable

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        assert _reachable('127.0.0.1', listener.getsockname()[1])


def test_database_and_web_server_come_from_settings(monkeypatch):
    from cognitrix.config import CognitrixSettings

    monkeypatch.setattr(app_settings, '_env_fallback', False)
    monkeypatch.setattr(CognitrixSettings, '_ensure_directories', lambda self: None)
    monkeypatch.setenv('DB_TYPE', 'mysql')  # ignored
    plain = CognitrixSettings()
    assert plain.db_type == 'sqlite' and plain.db_name.endswith('cognitrix.db')
    assert plain.cors_origins == ['http://localhost:8000', 'http://localhost:5173']
    assert plain.jwt_algorithm == 'HS256' and plain.jwt_secret_key

    app_settings.set_many({'DB_TYPE': 'postgresql', 'DB_HOST': 'db.example',
                           'COGNITRIX_CORS_ORIGINS': 'https://a.example\nhttps://b.example'})
    configured = CognitrixSettings()
    assert (configured.db_type, configured.db_host, configured.db_port, configured.db_name) == \
        ('postgresql', 'db.example', 5432, 'cognitrix')
    assert configured.cors_origins == ['https://a.example', 'https://b.example']
    # The sign-in key is the same on every start.
    assert configured.jwt_secret_key == plain.jwt_secret_key


def test_content_security_policy_from_settings(monkeypatch):
    from cognitrix.api.main import _DEFAULT_CSP, _content_security_policy

    monkeypatch.setattr(app_settings, '_env_fallback', False)
    monkeypatch.delenv('COGNITRIX_DESKTOP', raising=False)
    assert _content_security_policy() == _DEFAULT_CSP
    app_settings.set_many({'COGNITRIX_CSP': 'off'})
    assert _content_security_policy() == ''


def test_task_limits_are_strict_only_with_a_shared_redis(monkeypatch):
    from cognitrix.tasks import accounting

    monkeypatch.setattr(app_settings, '_env_fallback', False)
    seen = {}
    monkeypatch.setattr(accounting, 'build_concurrency_limiter', lambda **kw: seen.update(kw) or object())
    monkeypatch.setattr(accounting, '_DEFAULT_LIMITER', None)
    accounting._default_limiter()
    assert seen['environment'] == 'development' and seen['redis_url'] is None
    app_settings.set_many({'CELERY_BROKER_URL': 'redis://cache.example:6379/0'})
    monkeypatch.setattr(accounting, '_DEFAULT_LIMITER', None)
    accounting._default_limiter()
    assert seen['environment'] == 'production' and seen['redis_url'] == 'redis://cache.example:6379/0'


def test_settings_command(monkeypatch, capsys):
    from cognitrix.cli.handlers_settings import manage_settings

    monkeypatch.setattr(app_settings, '_env_fallback', False)
    manage_settings(Namespace(settings_action='set', items=['CELERY_BROKER_URL=redis://redis:6379/0'],
                              if_unset=True, page=''))
    manage_settings(Namespace(settings_action='set', items=['CELERY_BROKER_URL=redis://other:6379/0'],
                              if_unset=True, page=''))
    assert app_settings.get('CELERY_BROKER_URL') == 'redis://redis:6379/0'
    manage_settings(Namespace(settings_action='list', items=[], if_unset=False, page='advanced'))
    out = capsys.readouterr().out
    assert 'CELERY_BROKER_URL=(set)' in out and 'redis://' not in out
    manage_settings(Namespace(settings_action='unset', items=['CELERY_BROKER_URL'], if_unset=False, page=''))
    assert app_settings.get('CELERY_BROKER_URL') == ''
    with pytest.raises(SystemExit):
        manage_settings(Namespace(settings_action='set', items=['NOT_A_SETTING=1'], if_unset=False, page=''))
