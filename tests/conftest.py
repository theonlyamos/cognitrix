"""Test configuration and shared fixtures for Cognitrix tests."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cognitrix import app_settings as _app_settings

# Settings saved from the app live in a throwaway file during tests, and a
# value a test sets with monkeypatch.setenv still counts when nothing is
# saved (the app itself never reads these from the environment).
_APP_SETTINGS_DIR = Path(tempfile.mkdtemp(prefix='cognitrix-app-settings-'))
_app_settings.settings_file = lambda: _APP_SETTINGS_DIR / 'app_settings.json'
_app_settings._env_fallback = True


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM response."""
    response = MagicMock()
    response.llm_response = "Test response"
    return response


@pytest.fixture
def mock_embedding():
    """Create a mock embedding vector."""
    return [0.1, 0.2, 0.3, 0.4, 0.5]


@pytest.fixture(autouse=True)
def _app_settings_per_test(monkeypatch):
    """Process-only overrides (command line flags, the desktop's sign-in
    length) and saved values don't leak from one test into the next."""
    monkeypatch.setattr(_app_settings, '_overrides', {})
    path = _app_settings.settings_file()
    if path.exists():
        path.unlink()
    _app_settings._cache = None
    yield


@pytest.fixture(autouse=True)
def _secrets_key(monkeypatch):
    """Encrypt saved secrets with a test key, not a file in the real home folder."""
    from cognitrix.common import secret_box

    monkeypatch.setenv('COGNITRIX_SECRETS_KEY', 'test-secrets-key')
    monkeypatch.delenv('COGNITRIX_SECRETS_OLD_KEYS', raising=False)
    secret_box.reset_key_cache()
    yield
    secret_box.reset_key_cache()


@pytest.fixture(autouse=True)
def _no_default_sandbox(monkeypatch, tmp_path):
    """The agents' sandboxed computer is on by default wherever Docker is
    installed; tests start with it off and no saved setting, unless they
    switch it on themselves."""
    from cognitrix.computer.sandbox import client as sandbox_client

    monkeypatch.setattr(sandbox_client, '_docker_installed', lambda: False)
    monkeypatch.setattr(sandbox_client, 'SETTINGS_FILE', tmp_path / 'computer-sandbox.json')
    yield


@pytest.fixture(autouse=True)
def _no_memory_embeddings(monkeypatch):
    """Memory search by meaning loads an 80 MB model on first use; tests that
    want it install a fake embedder with cognitrix.memory.semantic.set_embedder."""
    monkeypatch.setenv('COGNITRIX_MEMORY_EMBEDDINGS', '0')
    yield
    from cognitrix.memory import semantic

    semantic.set_embedder(None)
