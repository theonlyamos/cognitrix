"""Test configuration and shared fixtures for Cognitrix tests."""

from unittest.mock import MagicMock

import pytest


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
def _secrets_key(monkeypatch):
    """Encrypt saved secrets with a test key, not a file in the real home folder."""
    from cognitrix.common import secret_box

    monkeypatch.setenv('COGNITRIX_SECRETS_KEY', 'test-secrets-key')
    monkeypatch.delenv('COGNITRIX_SECRETS_OLD_KEYS', raising=False)
    secret_box.reset_key_cache()
    yield
    secret_box.reset_key_cache()
