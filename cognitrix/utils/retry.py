"""Retry utilities with exponential backoff and jitter."""

import asyncio
import random
import logging
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar, Any
from functools import wraps

logger = logging.getLogger('cognitrix.log')

T = TypeVar('T')


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.1
    retryable_exceptions: tuple = (Exception,)
    
    def should_retry(self, exception: Exception) -> bool:
        """Check if exception should trigger retry."""
        return isinstance(exception, self.retryable_exceptions)
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and jitter."""
        # Exponential backoff
        delay = min(
            self.base_delay * (self.exponential_base ** (attempt - 1)),
            self.max_delay
        )
        
        # Add jitter (randomness to prevent thundering herd)
        jitter = random.uniform(0, delay * self.jitter_factor)
        
        return delay + jitter


@dataclass
class RetryResult:
    """Result of a retry operation."""
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    attempts: int = 0


async def with_retry(
    func: Callable[..., T],
    config: Optional[RetryConfig] = None,
    on_retry: Optional[Callable[[Exception, int], None]] = None,
    *args,
    **kwargs
) -> RetryResult:
    """
    Execute function with retry logic.
    
    Args:
        func: Async function to execute
        config: Retry configuration
        on_retry: Callback on retry (exception, attempt_number)
        *args, **kwargs: Arguments to pass to func
        
    Returns:
        RetryResult with success status
    """
    config = config or RetryConfig()
    last_exception = None
    
    for attempt in range(1, config.max_attempts + 1):
        try:
            result = await func(*args, **kwargs)
            return RetryResult(success=True, result=result, attempts=attempt)
        
        except Exception as e:
            last_exception = e
            
            if not config.should_retry(e) or attempt == config.max_attempts:
                logger.error(f"Function failed after {attempt} attempts: {e}")
                break
            
            delay = config.calculate_delay(attempt)
            
            logger.warning(
                f"Attempt {attempt} failed: {e}. Retrying in {delay:.2f}s..."
            )
            
            if on_retry:
                try:
                    on_retry(e, attempt)
                except Exception as callback_error:
                    logger.error(f"Retry callback failed: {callback_error}")
            
            await asyncio.sleep(delay)
    
    return RetryResult(
        success=False,
        error=last_exception,
        attempts=config.max_attempts
    )


def retryable(config: Optional[RetryConfig] = None):
    """Decorator for adding retry logic to async functions."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await with_retry(func, config, None, *args, **kwargs)
        return wrapper
    return decorator


# Pre-configured retry configs for common scenarios
RETRY_CONFIGS = {
    'api_call': RetryConfig(
        max_attempts=3,
        base_delay=1.0,
        retryable_exceptions=(ConnectionError, TimeoutError)
    ),
    'llm_call': RetryConfig(
        max_attempts=3,
        base_delay=2.0,
        retryable_exceptions=(Exception,)  # LLM can fail for many reasons
    ),
    'tool_execution': RetryConfig(
        max_attempts=3,
        base_delay=1.0,
        exponential_base=2.0,
        retryable_exceptions=(Exception,)
    ),
    'persistent': RetryConfig(
        max_attempts=5,
        base_delay=2.0,
        max_delay=300.0,  # 5 minutes
        retryable_exceptions=(Exception,)
    )
}
