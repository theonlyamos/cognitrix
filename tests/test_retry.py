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
        assert isinstance(result.error, ValueError)
        assert result.attempts == 1
        mock_func.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_retry_with_callback(self):
        """Test retry callback is called on failure."""
        mock_func = AsyncMock(side_effect=[Exception("fail"), "success"])
        mock_callback = MagicMock()
        config = RetryConfig(max_attempts=3, base_delay=0.01)
        
        result = await with_retry(mock_func, config, on_retry=mock_callback)
        
        assert result.success is True
        mock_callback.assert_called_once()
        # Callback should receive exception and attempt number
        call_args = mock_callback.call_args[0]
        assert isinstance(call_args[0], Exception)
        assert call_args[1] == 1
    
    @pytest.mark.asyncio
    async def test_retry_callback_error_handling(self):
        """Test that callback errors don't break retry."""
        mock_func = AsyncMock(side_effect=[Exception("fail"), "success"])
        mock_callback = MagicMock(side_effect=Exception("callback error"))
        config = RetryConfig(max_attempts=3, base_delay=0.01)
        
        result = await with_retry(mock_func, config, on_retry=mock_callback)
        
        assert result.success is True
        mock_callback.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_function_arguments_passed(self):
        """Test that function arguments are passed correctly."""
        mock_func = AsyncMock(return_value="success")
        config = RetryConfig()
        
        await with_retry(mock_func, config, None, "arg1", "arg2", key="value")
        
        mock_func.assert_called_once_with("arg1", "arg2", key="value")
    
    @pytest.mark.asyncio
    async def test_delay_between_retries(self):
        """Test that delays are respected between retries."""
        delays = []
        original_sleep = asyncio.sleep
        
        async def track_sleep(delay):
            delays.append(delay)
        
        mock_func = AsyncMock(side_effect=[Exception("fail"), Exception("fail"), "success"])
        config = RetryConfig(max_attempts=3, base_delay=0.1)
        
        with patch('asyncio.sleep', side_effect=track_sleep):
            await with_retry(mock_func, config)
        
        # Should have 2 delays (between 3 attempts)
        assert len(delays) == 2
        assert all(d > 0 for d in delays)


class TestRetryDecorator:
    """Test suite for retryable decorator."""
    
    @pytest.mark.asyncio
    async def test_decorator_success(self):
        """Test decorator with successful function."""
        config = RetryConfig(max_attempts=2, base_delay=0.01)
        
        @retryable(config)
        async def test_func():
            return "success"
        
        result = await test_func()
        
        assert result.success is True
        assert result.result == "success"
    
    @pytest.mark.asyncio
    async def test_decorator_with_retry(self):
        """Test decorator with retry."""
        config = RetryConfig(max_attempts=3, base_delay=0.01)
        call_count = 0
        
        @retryable(config)
        async def test_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception(f"fail {call_count}")
            return "success"
        
        result = await test_func()
        
        assert result.success is True
        assert result.attempts == 3
    
    @pytest.mark.asyncio
    async def test_decorator_with_default_config(self):
        """Test decorator with default configuration."""
        @retryable()
        async def test_func():
            return "success"
        
        result = await test_func()
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves function name/doc."""
        config = RetryConfig()
        
        @retryable(config)
        async def my_function():
            """My docstring."""
            return "success"
        
        assert my_function.__name__ == "my_function"
        # Note: __doc__ might not be preserved depending on wraps usage
    
    @pytest.mark.asyncio
    async def test_decorator_with_function_arguments(self):
        """Test decorator passes arguments correctly."""
        config = RetryConfig()
        received_args = None
        
        @retryable(config)
        async def test_func(a, b, c=None):
            nonlocal received_args
            received_args = (a, b, c)
            return "success"
        
        await test_func(1, 2, c=3)
        
        assert received_args == (1, 2, 3)


class TestRetryConfigs:
    """Test suite for pre-configured retry configs."""
    
    def test_api_call_config(self):
        """Test API call retry configuration."""
        config = RETRY_CONFIGS['api_call']
        
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.retryable_exceptions == (ConnectionError, TimeoutError)
    
    def test_llm_call_config(self):
        """Test LLM call retry configuration."""
        config = RETRY_CONFIGS['llm_call']
        
        assert config.max_attempts == 3
        assert config.base_delay == 2.0
        assert config.retryable_exceptions == (Exception,)
    
    def test_tool_execution_config(self):
        """Test tool execution retry configuration."""
        config = RETRY_CONFIGS['tool_execution']
        
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.exponential_base == 2.0
    
    def test_persistent_config(self):
        """Test persistent retry configuration."""
        config = RETRY_CONFIGS['persistent']
        
        assert config.max_attempts == 5
        assert config.base_delay == 2.0
        assert config.max_delay == 300.0
    
    @pytest.mark.asyncio
    async def test_api_call_config_usage(self):
        """Test API call config in practice."""
        config = RETRY_CONFIGS['api_call']
        mock_func = AsyncMock(side_effect=ConnectionError("timeout"))
        
        result = await with_retry(mock_func, config)
        
        assert result.success is False
        assert isinstance(result.error, ConnectionError)
        assert result.attempts == 3
    
    @pytest.mark.asyncio
    async def test_api_call_config_not_retrying_other_exceptions(self):
        """Test API call config doesn't retry non-network errors."""
        config = RETRY_CONFIGS['api_call']
        mock_func = AsyncMock(side_effect=ValueError("invalid"))
        
        result = await with_retry(mock_func, config)
        
        assert result.success is False
        assert result.attempts == 1  # No retries


class TestRetryResult:
    """Test suite for RetryResult dataclass."""
    
    def test_success_result(self):
        """Test successful retry result."""
        result = RetryResult(success=True, result="data", attempts=1)
        
        assert result.success is True
        assert result.result == "data"
        assert result.error is None
        assert result.attempts == 1
    
    def test_failure_result(self):
        """Test failed retry result."""
        error = Exception("test error")
        result = RetryResult(success=False, error=error, attempts=3)
        
        assert result.success is False
        assert result.result is None
        assert result.error is error
        assert result.attempts == 3
    
    def test_default_values(self):
        """Test default RetryResult values."""
        result = RetryResult(success=True)
        
        assert result.result is None
        assert result.error is None
        assert result.attempts == 0


class TestExponentialBackoff:
    """Specific tests for exponential backoff behavior."""
    
    def test_backoff_calculation(self):
        """Test exponential backoff calculation."""
        config = RetryConfig(base_delay=1.0, exponential_base=2.0)
        
        # Calculate expected delays (without jitter)
        expected_delays = [
            1.0 * (2.0 ** 0),  # Attempt 1: 1.0
            1.0 * (2.0 ** 1),  # Attempt 2: 2.0
            1.0 * (2.0 ** 2),  # Attempt 3: 4.0
            1.0 * (2.0 ** 3),  # Attempt 4: 8.0
        ]
        
        for i, expected in enumerate(expected_delays, 1):
            # Calculate multiple times to account for jitter
            delays = [config.calculate_delay(i) for _ in range(10)]
            # Check that base delay is correct (delay - jitter ≈ expected)
            base_delays = [d / (1 + config.jitter_factor) for d in delays]
            assert all(abs(bd - expected) < 0.1 for bd in base_delays)
    
    def test_backoff_with_different_base(self):
        """Test backoff with different exponential base."""
        config = RetryConfig(base_delay=1.0, exponential_base=3.0)
        
        # With base 3: 1, 3, 9, 27...
        delays = [config.calculate_delay(i) for i in range(1, 5)]
        
        # Check exponential growth
        assert delays[1] > delays[0] * 2  # Should be ~3x
        assert delays[2] > delays[1] * 2  # Should be ~3x
    
    @pytest.mark.asyncio
    async def test_backoff_delays_between_retries(self):
        """Test that backoff delays are actually used."""
        config = RetryConfig(max_attempts=4, base_delay=0.05)
        mock_func = AsyncMock(side_effect=[Exception()] * 3 + ["success"])
        
        sleep_delays = []
        async def track_sleep(delay):
            sleep_delays.append(delay)
        
        with patch('asyncio.sleep', side_effect=track_sleep):
            await with_retry(mock_func, config)
        
        # Should have increasing delays
        assert len(sleep_delays) == 3
        assert sleep_delays[1] > sleep_delays[0]
        assert sleep_delays[2] > sleep_delays[1]


class TestMaxAttempts:
    """Tests specifically for max attempts behavior."""
    
    @pytest.mark.asyncio
    async def test_single_attempt(self):
        """Test with single attempt (no retry)."""
        mock_func = AsyncMock(return_value="success")
        config = RetryConfig(max_attempts=1)
        
        result = await with_retry(mock_func, config)
        
        assert result.success is True
        assert result.attempts == 1
        mock_func.assert_called_once()
    
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
