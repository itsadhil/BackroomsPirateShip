"""Rate limiting utilities for API calls."""
import asyncio
import time
from typing import Dict, Optional
from collections import deque
from functools import wraps
import logging

logger = logging.getLogger(__name__)

class RateLimiter:
    """Rate limiter with sliding window algorithm."""
    
    def __init__(self, max_calls: int, period: float):
        """
        Args:
            max_calls: Maximum number of calls allowed
            period: Time period in seconds
        """
        self.max_calls = max_calls
        self.period = period
        self.calls: deque = deque()
        self.lock = asyncio.Lock()
    
    async def acquire(self) -> bool:
        """Acquire permission to make a call. Returns True if allowed."""
        async with self.lock:
            now = time.time()
            
            # Remove old calls outside the window
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            
            # Check if we can make a call
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            
            # Calculate wait time
            wait_time = self.period - (now - self.calls[0])
            logger.warning(f"Rate limit reached. Waiting {wait_time:.2f}s")
            return False
    
    async def wait(self):
        """Wait until a call can be made."""
        while not await self.acquire():
            if self.calls:
                wait_time = self.period - (time.time() - self.calls[0])
                if wait_time > 0:
                    await asyncio.sleep(min(wait_time, 1.0))
            else:
                await asyncio.sleep(0.1)
    
    def reset(self):
        """Reset the rate limiter."""
        self.calls.clear()

class RateLimitDecorator:
    """Decorator for rate limiting async functions."""
    
    def __init__(self, max_calls: int, period: float):
        self.limiter = RateLimiter(max_calls, period)
    
    def __call__(self, func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            await self.limiter.wait()
            return await func(*args, **kwargs)
        return wrapper

# Global rate limiters
_steam_limiter = RateLimiter(max_calls=100, period=60.0)  # 100 calls per minute
_igdb_limiter = RateLimiter(max_calls=4, period=1.0)  # 4 calls per second
_discord_limiter = RateLimiter(max_calls=50, period=1.0)  # 50 calls per second

def get_steam_limiter() -> RateLimiter:
    """Get Steam API rate limiter."""
    return _steam_limiter

def get_igdb_limiter() -> RateLimiter:
    """Get IGDB API rate limiter."""
    return _igdb_limiter

def get_discord_limiter() -> RateLimiter:
    """Get Discord API rate limiter."""
    return _discord_limiter

def rate_limit(max_calls: int, period: float):
    """Decorator for rate limiting."""
    return RateLimitDecorator(max_calls, period)

