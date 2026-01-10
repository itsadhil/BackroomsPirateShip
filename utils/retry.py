"""Retry utilities with exponential backoff."""
import asyncio
import logging
from typing import TypeVar, Callable, Optional, Tuple
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')

async def retry_async(
    func: Callable[..., T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Exception, ...] = (Exception,),
    *func_args,
    **func_kwargs
) -> T:
    """
    Retry an async function with exponential backoff.
    
    Args:
        func: Async function to retry
        max_attempts: Maximum number of attempts
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        exceptions: Tuple of exceptions to catch
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Result of func
    
    Raises:
        Last exception if all attempts fail
    """
    last_exception = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*func_args, **func_kwargs)
        except exceptions as e:
            last_exception = e
            if attempt < max_attempts:
                delay = min(
                    base_delay * (exponential_base ** (attempt - 1)),
                    max_delay
                )
                logger.warning(
                    f"Attempt {attempt}/{max_attempts} failed: {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"All {max_attempts} attempts failed. Last error: {e}")
    
    raise last_exception

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Exception, ...] = (Exception,)
):
    """Decorator for retrying async functions."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Call retry_async with retry parameters as keyword args, then function args/kwargs
            return await retry_async(
                func,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                exponential_base=exponential_base,
                exceptions=exceptions,
                *args,
                **kwargs
            )
        return wrapper
    return decorator

