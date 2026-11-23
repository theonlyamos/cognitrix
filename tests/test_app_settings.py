"""App-wide settings set in Settings instead of environment variables."""
import json
import re
from pathlib import Path

import pytest

from cognitrix import app_settings


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A fresh settings file, and no reading from the environment (the app's
    real behaviour; conftest turns the test fallback on for older tests)."""
    monkeypatch.setattr(app_settings, 'settings_file', lambda: tmp_path / 'app_settings.json')
    monkeypatch.setattr(app_settings, '_env_fallback', False)
    monkeypatch.setattr(app_settings, '_cache', None)
    monkeypatch.setattr(app_settings, '_overrides', {})
    return tmp_path / 'app_settings.json'


def test_environment_is_never_read(store, monkeypatch):
    monkeypatch.setenv('COGNITRIX_SMTP_HOST', 'smtp.from-env.example')
    monkeypatch.setenv('COGNITRIX_PUBLIC_URL', 'https://from-env.example')
    assert app_settings.get('COGNITRIX_SMTP_HOST') == ''
    assert app_settings.get('COGNITRIX_PUBLIC_URL', 'fallback') == 'fallback'
    assert not store.exists()  # nothing imported from the environment either


def test_saved_values_are_read_back(store):
    app_settings.set_many({'COGNITRIX_SMTP_HOST': ' smtp.example.com ', 'COGNITRIX_SMTP_PORT': '465'})
    assert app_settings.get('COGNITRIX_SMTP_HOST') == 'smtp.example.com'
    assert app_settings.get('COGNITRIX_SMTP_PORT') == '465'
    # An empty value clears it, back to the default.
    app_settings.set_many({'COGNITRIX_SMTP_PORT': ''})
    assert app_settings.get('COGNITRIX_SMTP_PORT', '587') == '587'


def test_secrets_are_sealed_on_disk_and_hidden(store):
    app_settings.set_many({'COGNITRIX_SMTP_PASSWORD': 'hunter2-very-secret'})
    raw = json.loads(store.read_text())
    assert raw['values']['COGNITRIX_SMTP_PASSWORD'].startswith('enc:')
    assert 'hunter2' not in store.read_text()
    assert app_settings.get('COGNITRIX_SMTP_PASSWORD') == 'hunter2-very-secret'
    row = next(s for g in app_settings.describe('people') for s in g['settings'] if s['name'] == 'COGNITRIX_SMTP_PASSWORD')
    assert row['value'] == '' and row['is_set'] is True and 'hunter2' not in json.dumps(row)


def test_bad_values_are_refused(store):
    with pytest.raises(ValueError, match='Unknown setting'):
        app_settings.set_many({'NOT_A_SETTING': 'x'})
    with pytest.raises(ValueError, match='number'):
        app_settings.set_many({'COGNITRIX_SMTP_PORT': 'abc'})
    with pytest.raises(ValueError, match='whole number'):
        app_settings.set_many({'COGNITRIX_MAX_TOOL_ROUNDS': '1.5'})
    with pytest.raises(ValueError, match='choices'):
        app_settings.set_many({'COGNITRIX_SHELL_MODE': 'yolo'})
    app_settings.set_many({'COGNITRIX_GATE_THRESHOLD': '7.5', 'COGNITRIX_STEP_TIMEOUT': '120.0'})
    assert app_settings.get('COGNITRIX_GATE_THRESHOLD') == '7.5'
    assert app_settings.get('COGNITRIX_STEP_TIMEOUT') == '120'


def test_lists_are_one_per_line_in_settings(store):
    app_settings.set_many({'COGNITRIX_ADMIN_EMAILS': 'a@example.com\n\n b@example.com '})
    assert app_settings.get('COGNITRIX_ADMIN_EMAILS') == 'a@example.com,b@example.com'
    row = next(s for g in app_settings.describe('people') for s in g['settings'] if s['name'] == 'COGNITRIX_ADMIN_EMAILS')
    assert row['value'] == 'a@example.com\nb@example.com'


def test_toggle_and_override(store):
    assert app_settings.get_bool('COGNITRIX_AUTO_APPROVE', False) is False
    app_settings.override('COGNITRIX_AUTO_APPROVE', '1')
    assert app_settings.get_bool('COGNITRIX_AUTO_APPROVE', False) is True
    app_settings.override('COGNITRIX_AUTO_APPROVE', None)
    app_settings.set_many({'COGNITRIX_AUTO_APPROVE': True})
    assert app_settings.get('COGNITRIX_AUTO_APPROVE') == '1'


def test_every_page_has_settings():
    pages = {s.page for s in app_settings.registry().values()}
    assert pages == {'models', 'people', 'apps', 'computers', 'notifications', 'advanced'}


def test_no_code_reads_a_setting_from_the_environment():
    """A setting moved into Settings must not be read with os.getenv again."""
    names = set(app_settings.registry())
    root = Path(app_settings.__file__).resolve().parent
    pattern = re.compile(r"os\.(?:getenv|environ\.get)\(\s*['\"]([A-Z0-9_]+)['\"]|os\.environ\[['\"]([A-Z0-9_]+)['\"]\]")
    offenders = []
    for path in root.rglob('*.py'):
        for match in pattern.finditer(path.read_text(encoding='utf-8')):
            name = match.group(1) or match.group(2)
            if name in names:
                offenders.append(f'{path.relative_to(root)}: {name}')
    assert offenders == []


def test_provider_keys_ignore_the_environment(monkeypatch, tmp_path):
    from cognitrix.config import settings
    from cognitrix.providers import saved_keys

    monkeypatch.setattr(saved_keys, 'keys_file', lambda: tmp_path / 'provider_keys.json')
    monkeypatch.setattr(saved_keys, '_cache', None)
    monkeypatch.setattr(app_settings, '_env_fallback', False)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-from-env-1234')
    assert settings.get_api_key('anthropic') == ''
    saved_keys.set_saved_key('anthropic', 'sk-ant-saved-5678')
    assert settings.get_api_key('anthropic') == 'sk-ant-saved-5678'


def test_changing_fly_settings_drops_the_cached_fly_manager(monkeypatch):
    from cognitrix.computer.sandbox import client

    closed = []

    class Fly:
        kind = 'fly'

        def close(self):
            closed.append(True)

    monkeypatch.setattr(client, '_manager', Fly())
    client.forget_fly_manager()
    assert client._manager is None and closed == [True]
    docker = type('Docker', (), {'kind': 'docker'})()
    monkeypatch.setattr(client, '_manager', docker)
    client.forget_fly_manager()
    assert client._manager is docker
