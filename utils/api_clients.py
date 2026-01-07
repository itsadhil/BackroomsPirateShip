"""Improved API clients for IGDB and RAWG with rate limiting and retry logic."""
import logging
from typing import Optional, Dict, Any, List
from utils.http_client import get_http_session
from utils.rate_limiter import get_igdb_limiter
from utils.retry import retry
from config.settings import settings

logger = logging.getLogger(__name__)

class IGDBClient:
    """Improved IGDB API client with rate limiting and connection pooling."""
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None
    
    async def get_access_token(self) -> str:
        """Obtain OAuth access token from Twitch with retry logic."""
        if self.access_token:
            return self.access_token
        
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }
        
        try:
            session = await get_http_session()
            async with session.post(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data["access_token"]
                    logger.debug("IGDB access token obtained")
                    return self.access_token
                else:
                    error_text = await resp.text()
                    raise Exception(f"Failed to get access token: {resp.status} - {error_text}")
        except Exception as e:
            logger.error(f"Error getting IGDB access token: {e}", exc_info=True)
            raise
    
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def search_game_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Search IGDB for a game by name with rate limiting."""
        try:
            await get_igdb_limiter().wait()
            token = await self.get_access_token()
            
            url = "https://api.igdb.com/v4/games"
            headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {token}"
            }
            
            # IGDB uses Apicalypse query language
            body = f'search "{name}"; fields name,summary,genres.name,platforms.name,cover.image_id; limit 1;'
            
            session = await get_http_session()
            async with session.post(url, headers=headers, data=body) as resp:
                if resp.status == 200:
                    results = await resp.json()
                    return results[0] if results else None
                else:
                    logger.warning(f"IGDB search failed: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"IGDB search error: {e}", exc_info=True)
            return None
    
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def get_similar_games(self, game_id: int) -> List[Dict[str, Any]]:
        """Get similar games."""
        try:
            await get_igdb_limiter().wait()
            token = await self.get_access_token()
            
            url = "https://api.igdb.com/v4/games"
            headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {token}"
            }
            
            body = f'fields similar_games.name,similar_games.summary,similar_games.cover.image_id; where id = {game_id}; limit 1;'
            
            session = await get_http_session()
            async with session.post(url, headers=headers, data=body) as resp:
                if resp.status == 200:
                    results = await resp.json()
                    if results:
                        similar = results[0].get('similar_games', [])
                        return similar[:5]  # Return top 5
                    return []
                return []
        except Exception as e:
            logger.error(f"IGDB similar games error: {e}", exc_info=True)
            return []
    
    async def close(self):
        """Cleanup (no-op for this client)."""
        pass

class RAWGClient:
    """Improved RAWG API client with rate limiting."""
    
    BASE_URL = "https://api.rawg.io/api/games"
    
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def search_game_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Search RAWG for a game by name."""
        try:
            params = {
                "search": name,
                "page_size": 1
            }
            
            session = await get_http_session()
            async with session.get(self.BASE_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get('results', [])
                    if results:
                        game = results[0]
                        # Convert RAWG format to IGDB-like format
                        return {
                            'name': game.get('name'),
                            'summary': game.get('description_raw', ''),
                            'genres': [{'name': g.get('name')} for g in game.get('genres', [])],
                            'platforms': [{'name': p.get('platform', {}).get('name')} for p in game.get('platforms', [])],
                            'cover': {'image_id': game.get('background_image', '')}
                        }
                    return None
                else:
                    logger.warning(f"RAWG search failed: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"RAWG search error: {e}", exc_info=True)
            return None

