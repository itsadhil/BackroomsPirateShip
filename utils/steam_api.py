"""Improved Steam API client with rate limiting and shared HTTP session."""
import logging
from typing import Optional, Dict, List
from utils.http_client import get_http_session
from utils.rate_limiter import get_steam_limiter
from utils.retry import retry
from config.settings import settings

logger = logging.getLogger(__name__)

class SteamAPI:
    """Improved Steam Web API client with rate limiting and retry logic."""
    
    BASE_URL = "https://api.steampowered.com"
    
    @staticmethod
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def get_player_summaries(steam_id: str) -> Optional[Dict]:
        """Get player profile information with rate limiting."""
        if not settings.STEAM_API_KEY:
            logger.error("STEAM_API_KEY not configured")
            return None
        
        await get_steam_limiter().wait()
        
        url = f"{SteamAPI.BASE_URL}/ISteamUser/GetPlayerSummaries/v0002/"
        params = {
            'key': settings.STEAM_API_KEY,
            'steamids': steam_id
        }
        
        try:
            session = await get_http_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    players = data.get('response', {}).get('players', [])
                    return players[0] if players else None
                else:
                    logger.warning(f"Steam API returned status {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching Steam player summaries: {e}", exc_info=True)
            return None
    
    @staticmethod
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def get_owned_games(steam_id: str, include_appinfo: bool = True) -> Optional[List[Dict]]:
        """Get list of games owned by player."""
        if not settings.STEAM_API_KEY:
            logger.error("STEAM_API_KEY not configured")
            return None
        
        await get_steam_limiter().wait()
        
        url = f"{SteamAPI.BASE_URL}/IPlayerService/GetOwnedGames/v0001/"
        params = {
            'key': settings.STEAM_API_KEY,
            'steamid': steam_id,
            'include_appinfo': 1 if include_appinfo else 0,
            'include_played_free_games': 1
        }
        
        try:
            session = await get_http_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('response', {}).get('games', [])
                else:
                    logger.warning(f"Steam API returned status {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching Steam owned games: {e}", exc_info=True)
            return None
    
    @staticmethod
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def get_recently_played_games(steam_id: str) -> Optional[List[Dict]]:
        """Get recently played games."""
        if not settings.STEAM_API_KEY:
            return None
        
        await get_steam_limiter().wait()
        
        url = f"{SteamAPI.BASE_URL}/IPlayerService/GetRecentlyPlayedGames/v0001/"
        params = {
            'key': settings.STEAM_API_KEY,
            'steamid': steam_id,
            'count': 10
        }
        
        try:
            session = await get_http_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('response', {}).get('games', [])
                return None
        except Exception as e:
            logger.error(f"Error fetching recently played games: {e}", exc_info=True)
            return None
    
    @staticmethod
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def get_friend_list(steam_id: str) -> Optional[List[Dict]]:
        """Get player's friend list."""
        if not settings.STEAM_API_KEY:
            return None
        
        await get_steam_limiter().wait()
        
        url = f"{SteamAPI.BASE_URL}/ISteamUser/GetFriendList/v0001/"
        params = {
            'key': settings.STEAM_API_KEY,
            'steamid': steam_id,
            'relationship': 'friend'
        }
        
        try:
            session = await get_http_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('friendslist', {}).get('friends', [])
                return None
        except Exception as e:
            logger.error(f"Error fetching friend list: {e}", exc_info=True)
            return None
    
    @staticmethod
    @retry(max_attempts=3, base_delay=1.0, exceptions=(Exception,))
    async def resolve_vanity_url(vanity_url: str) -> Optional[str]:
        """Convert Steam vanity URL to Steam ID."""
        if not settings.STEAM_API_KEY:
            return None
        
        await get_steam_limiter().wait()
        
        url = f"{SteamAPI.BASE_URL}/ISteamUser/ResolveVanityURL/v0001/"
        params = {
            'key': settings.STEAM_API_KEY,
            'vanityurl': vanity_url
        }
        
        try:
            session = await get_http_session()
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('response', {}).get('success') == 1:
                        return data['response']['steamid']
                return None
        except Exception as e:
            logger.error(f"Error resolving vanity URL: {e}", exc_info=True)
            return None

def format_playtime(minutes: int) -> str:
    """Format playtime from minutes to human-readable format."""
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    if hours < 100:
        return f"{hours:.1f} hrs"
    return f"{int(hours)} hrs"

def get_personastate_string(state: int) -> str:
    """Convert Steam persona state to readable string."""
    states = {
        0: "Offline",
        1: "Online",
        2: "Busy",
        3: "Away",
        4: "Snooze",
        5: "Looking to trade",
        6: "Looking to play"
    }
    return states.get(state, "Unknown")

