"""Shared HTTP client with connection pooling and proper resource management."""
import aiohttp
import logging
from typing import Optional
import asyncio

logger = logging.getLogger(__name__)

class HTTPClientManager:
    """Manages shared HTTP sessions for connection pooling."""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
    
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create the shared HTTP session."""
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=30, connect=10)
                    connector = aiohttp.TCPConnector(limit=100, limit_per_host=20)
                    self._session = aiohttp.ClientSession(
                        timeout=timeout,
                        connector=connector
                    )
                    logger.info("Created new HTTP session")
        return self._session
    
    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Closed HTTP session")
    
    async def __aenter__(self):
        return await self.get_session()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Don't close on exit, keep session alive
        pass

# Global HTTP client manager
_http_manager = HTTPClientManager()

async def get_http_session() -> aiohttp.ClientSession:
    """Get the shared HTTP session."""
    return await _http_manager.get_session()

async def close_http_sessions():
    """Close all HTTP sessions. Call on bot shutdown."""
    await _http_manager.close()

