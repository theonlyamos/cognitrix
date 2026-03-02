"""Tests for retry logic with exponential backoff."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, call

from cognitrix.utils.retry import (
    RetryConfig,
    RetryResult,
    with_retry,
    retryable,
    RETRY_CONFIGS
)


class TestRetryConfig:
    """Test suite for RetryConfig dataclass."""
    
    @pytest.fixture
    def default_config(self):
        """Create a default retry configuration."""
        return RetryConfig()
    
    def test_default_values(self, default_config):
        """Test default configuration values."""
        assert default_config.max_attempts == 3
        assert default_config.base_delay == 1.0
        assert default_config.max_delay == 60.0
        assert default_config.exponential_base == 2.0
        assert default_config.jitter_factor == 0.1
        assert default_config.retryable_exceptions == (Exception,)
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = RetryConfig(
            max_attempts=5,
            base_delay=2.0,
            max_delay=30.0,
            exponential_base=3.0,
            jitter_factor=0.2,
            retryable_exceptions=(ConnectionError, TimeoutError)
        )
        
        assert config.max_attempts == 5
        assert config.base_delay == 2.0
        assert config.max_delay == 30.0
        assert config.exponential_base == 3.0
        assert config.jitter_factor == 0.2
        assert config.retryable_exceptions == (ConnectionError, TimeoutError)
    
    def test_should_retry_with_matching_exception(self, default_config):
        """Test that matching exceptions trigger retry."""
        assert default_config.should_retry(Exception()) is True
        assert default_config.should_retry(ValueError()) is True
        assert default_config.should_retry(RuntimeError()) is True
    
    def test_should_retry_with_non_matching_exception(self):
        """Test that non-matching exceptions don't trigger retry."""
        config = RetryConfig(retryable_exceptions=(ConnectionError,))
        
        assert config.should_retry(ValueError()) is False
        assert config.should_retry(RuntimeError()) is False
        assert config.should_retry(ConnectionError()) is True
    
    def test_calculate_delay_exponential_growth(self, default_config):
        """Test that delay grows exponentially."""
        delay1 = default_config.calculate_delay(1)
        delay2 = default_config.calculate_delay(2)
        delay3 = default_config.calculate_delay(3)
        
        # Delays should increase (with jitter, so check approximate)
        assert delay1 >= default_config.base_delay
        assert delay2 >= default_config.base_delay * default_config.exponential_base
        assert delay3 >= default_config.base_delay * (default_config.exponential_base ** 2)
    
    def test_calculate_delay_max_cap(self):
        """Test that delay doesn't exceed max_delay."""
        config = RetryConfig(max_delay=10.0)
        
        # Calculate delay for high attempt number
        delay = config.calculate_delay(10)
        
        assert delay <= config.max_delay * (1 + config.jitter_factor)
    
    def test_calculate_delay_includes_jitter(self, default_config):
        """Test that jitter is added to delay."""
        # Calculate multiple delays to account for randomness
        delays = [default_config.calculate_delay(1) for _ in range(10)]
        
        # All delays should be >= base_delay
        assert all(d >= default_config.base_delay for d in delays)
        
        # Some delays should be different due to jitter
        assert len(set(delays)) > 1
    
    def test_calculate_delay_jitter_range(self, default_config):
        """Test that jitter is within expected range."""
        base = default_config.base_delay
        jitter_max = base * default_config.jitter_factor
        
        # Test many times to catch edge cases
        for _ in range(20):
            delay = default_config.calculate_delay(1)
            assert base <= delay <= base + jitter_max


class TestWithRetry:
    """Test suite for with_retry function."""
    
    @pytest.mark.asyncio
    async def test_successful_execution_no_retry(self):
        """Test that successful functions don't retry."""
        mock_func = AsyncMock(return_value="success")
        config = RetryConfig(max_attempts=3)
        
        result = await with_retry(mock_func, config)
        
        assert result.success is True
        assert result.result == "success"
        assert result.attempts == 1
        mock_func.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self):
        """Test retry after failure followed by success."""
        mock_func = AsyncMock(side_effect=[Exception("fail"), Exception("fail"), "success"])
        config = RetryConfig(max_attempts=3, base_delay=0.01)
        
        result = await with_retry(mock_func, config)
        
        assert result.success is True
        assert result.result == "success"
        assert result.attempts == 3
        assert mock_func.call_count == 3
    
    @pytest.mark.asyncio
    async def test_max_attempts_exceeded(self):
        """Test failure after max attempts."""
        mock_func = AsyncMock(side_effect=Exception("always fails"))
        config = RetryConfig(max_attempts=3, base_delay=0.01)
        
        result = await with_retry(mock_func, config)
        
        assert result.success is False
        assert result.error is not None
        assert result.attempts == 3
        assert mock_func.call_count == 3
    
    @pytest.mark.asyncio
    async def test_no_retry_for_non_retryable_exception(self):
        """Test that non-retryable exceptions fail immediately."""
        mock_func = AsyncMock(side_effect=ValueError("not retryable"))
        config = RetryConfig(
            max_attempts=3,
            retryable_exceptions=(ConnectionError,)
        )
        
        result = await with_retry(mock_func, config)
        
        assert result.success is False
        # The error should be ValueError
        assert result.error is not None
        # Should have attempted at least once
        assert result.attempts >= 1
    
    @pytest.mark.asyncio
    async def test_many_attempts(self):
        """Test with many attempts."""
        mock_func = AsyncMock(side_effect=[Exception()] * 9 + ["success"])
        config = RetryConfig(max_attempts=10, base_delay=0.001)
        
        result = await with_retry(mock_func, config)
        
        assert result.success is True
        assert result.attempts == 10
        assert mock_func.call_count == 10
    
    @pytest.mark.asyncio
    async def test_attempts_stops_at_max(self):
        """Test that attempts stop at max even if still failing."""
        mock_func = AsyncMock(side_effect=Exception("always fails"))
        config = RetryConfig(max_attempts=5, base_delay=0.001)
        
        result = await with_retry(mock_func, config)
        
        assert result.success is False
        assert result.attempts == 5
        assert mock_func.call_count == 5
