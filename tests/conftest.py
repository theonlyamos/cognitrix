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
