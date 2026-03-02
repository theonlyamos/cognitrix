"""Test configuration and shared fixtures for Cognitrix tests."""

import pytest
import asyncio
from unittest.mock import MagicMock


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


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
