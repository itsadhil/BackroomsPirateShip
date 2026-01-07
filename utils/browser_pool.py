"""Playwright browser pool manager to reuse browser instances."""
import asyncio
import logging
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)

class BrowserPool:
    """Manages a pool of Playwright browsers for reuse."""
    
    def __init__(self, max_browsers: int = 2):
        self.max_browsers = max_browsers
        self._playwright: Optional[Playwright] = None
        self._browsers: list[Browser] = []
        self._available_browsers: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._initialized = False
    
    async def initialize(self):
        """Initialize the browser pool."""
        if self._initialized:
            return
        
        async with self._lock:
            if self._initialized:
                return
            
            try:
                self._playwright = await async_playwright().start()
                logger.info("Playwright initialized")
                
                # Pre-create browsers
                for i in range(min(self.max_browsers, 2)):
                    browser = await self._create_browser()
                    if browser:
                        await self._available_browsers.put(browser)
                
                self._initialized = True
                logger.info(f"Browser pool initialized with {self._available_browsers.qsize()} browsers")
            except Exception as e:
                logger.error(f"Error initializing browser pool: {e}", exc_info=True)
                raise
    
    async def _create_browser(self) -> Optional[Browser]:
        """Create a new browser instance."""
        try:
            browser = await self._playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            self._browsers.append(browser)
            logger.debug("Created new browser instance")
            return browser
        except Exception as e:
            logger.error(f"Error creating browser: {e}", exc_info=True)
            return None
    
    async def get_browser(self, timeout: float = 30.0) -> Optional[Browser]:
        """Get an available browser from the pool."""
        if not self._initialized:
            await self.initialize()
        
        try:
            # Try to get from queue with timeout
            browser = await asyncio.wait_for(
                self._available_browsers.get(),
                timeout=timeout
            )
            return browser
        except asyncio.TimeoutError:
            # Create new browser if queue is empty and under limit
            async with self._lock:
                if len(self._browsers) < self.max_browsers:
                    browser = await self._create_browser()
                    if browser:
                        return browser
            
            logger.warning("No browsers available, waiting...")
            # Wait for a browser to become available
            browser = await self._available_browsers.get()
            return browser
    
    async def return_browser(self, browser: Browser):
        """Return a browser to the pool."""
        if browser and not browser.is_connected():
            # Browser is closed, create a new one
            logger.warning("Browser was closed, creating replacement")
            browser = await self._create_browser()
            if browser:
                await self._available_browsers.put(browser)
        elif browser:
            await self._available_browsers.put(browser)
    
    async def create_context(self, browser: Optional[Browser] = None) -> Optional[BrowserContext]:
        """Create a browser context."""
        if not browser:
            browser = await self.get_browser()
        
        if not browser:
            return None
        
        try:
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            return context
        except Exception as e:
            logger.error(f"Error creating context: {e}", exc_info=True)
            await self.return_browser(browser)
            return None
    
    async def close_all(self):
        """Close all browsers and cleanup."""
        async with self._lock:
            for browser in self._browsers:
                try:
                    if browser.is_connected():
                        await browser.close()
                except Exception as e:
                    logger.error(f"Error closing browser: {e}")
            
            self._browsers.clear()
            
            # Clear queue
            while not self._available_browsers.empty():
                try:
                    browser = self._available_browsers.get_nowait()
                    if browser.is_connected():
                        await browser.close()
                except:
                    pass
            
            if self._playwright:
                try:
                    await self._playwright.stop()
                except:
                    pass
            
            self._initialized = False
            logger.info("Browser pool closed")

# Global browser pool
_browser_pool: Optional[BrowserPool] = None

def get_browser_pool() -> BrowserPool:
    """Get the global browser pool instance."""
    global _browser_pool
    if _browser_pool is None:
        _browser_pool = BrowserPool(max_browsers=2)
    return _browser_pool

async def close_browser_pool():
    """Close the global browser pool."""
    global _browser_pool
    if _browser_pool:
        await _browser_pool.close_all()
        _browser_pool = None

