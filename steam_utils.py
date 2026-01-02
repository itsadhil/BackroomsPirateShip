import aiohttp
import json
import os
from typing import Optional, Dict, List
from datetime import datetime

STEAM_API_KEY = os.getenv('STEAM_API_KEY')
STEAM_LINKS_FILE = 'steam_links.json'

class SteamAPI:
    """Utility class for Steam Web API interactions"""
    
    BASE_URL = "https://api.steampowered.com"
    
    @staticmethod
    async def get_player_summaries(steam_id: str) -> Optional[Dict]:
        """Get player profile information"""
        url = f"{SteamAPI.BASE_URL}/ISteamUser/GetPlayerSummaries/v0002/"
        params = {
            'key': STEAM_API_KEY,
            'steamids': steam_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    players = data.get('response', {}).get('players', [])
                    return players[0] if players else None
                return None
    
    @staticmethod
    async def get_owned_games(steam_id: str, include_appinfo: bool = True) -> Optional[List[Dict]]:
        """Get list of games owned by player"""
        url = f"{SteamAPI.BASE_URL}/IPlayerService/GetOwnedGames/v0001/"
        params = {
            'key': STEAM_API_KEY,
            'steamid': steam_id,
            'include_appinfo': 1 if include_appinfo else 0,
            'include_played_free_games': 1
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('response', {}).get('games', [])
                return None
    
    @staticmethod
    async def get_recently_played_games(steam_id: str) -> Optional[List[Dict]]:
        """Get recently played games"""
        url = f"{SteamAPI.BASE_URL}/IPlayerService/GetRecentlyPlayedGames/v0001/"
        params = {
            'key': STEAM_API_KEY,
            'steamid': steam_id,
            'count': 10
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('response', {}).get('games', [])
                return None
    
    @staticmethod
    async def get_friend_list(steam_id: str) -> Optional[List[Dict]]:
        """Get player's friend list"""
        url = f"{SteamAPI.BASE_URL}/ISteamUser/GetFriendList/v0001/"
        params = {
            'key': STEAM_API_KEY,
            'steamid': steam_id,
            'relationship': 'friend'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('friendslist', {}).get('friends', [])
                return None
    
    @staticmethod
    async def resolve_vanity_url(vanity_url: str) -> Optional[str]:
        """Convert Steam vanity URL to Steam ID"""
        url = f"{SteamAPI.BASE_URL}/ISteamUser/ResolveVanityURL/v0001/"
        params = {
            'key': STEAM_API_KEY,
            'vanityurl': vanity_url
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('response', {}).get('success') == 1:
                        return data['response']['steamid']
                return None
    
    @staticmethod
    async def get_player_achievements(steam_id: str, game_id: int) -> Optional[List[Dict]]:
        """Get player achievements for a specific game"""
        url = f"{SteamAPI.BASE_URL}/ISteamUserStats/GetPlayerAchievements/v0001/"
        params = {
            'key': STEAM_API_KEY,
            'steamid': steam_id,
            'appid': game_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('playerstats', {}).get('achievements', [])
                return None
    
    @staticmethod
    async def get_schema_for_game(game_id: int) -> Optional[Dict]:
        """Get game schema including achievement names"""
        url = f"{SteamAPI.BASE_URL}/ISteamUserStats/GetSchemaForGame/v2/"
        params = {
            'key': STEAM_API_KEY,
            'appid': game_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('game', {})
                return None

class SteamLinker:
    """Manage Discord-Steam account linking"""
    
    @staticmethod
    def load_links() -> Dict[str, str]:
        """Load Discord-Steam links from JSON file"""
        if os.path.exists(STEAM_LINKS_FILE):
            try:
                with open(STEAM_LINKS_FILE, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    @staticmethod
    def save_links(links: Dict[str, str]):
        """Save Discord-Steam links to JSON file"""
        with open(STEAM_LINKS_FILE, 'w') as f:
            json.dump(links, f, indent=2)
    
    @staticmethod
    def link_account(discord_id: str, steam_id: str):
        """Link a Discord account to a Steam ID"""
        links = SteamLinker.load_links()
        links[discord_id] = steam_id
        SteamLinker.save_links(links)
    
    @staticmethod
    def unlink_account(discord_id: str) -> bool:
        """Unlink a Discord account from Steam"""
        links = SteamLinker.load_links()
        if discord_id in links:
            del links[discord_id]
            SteamLinker.save_links(links)
            return True
        return False
    
    @staticmethod
    def get_steam_id(discord_id: str) -> Optional[str]:
        """Get Steam ID for a Discord user"""
        links = SteamLinker.load_links()
        return links.get(discord_id)

def format_playtime(minutes: int) -> str:
    """Format playtime from minutes to human-readable format"""
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    if hours < 100:
        return f"{hours:.1f} hrs"
    return f"{int(hours)} hrs"

def get_personastate_string(state: int) -> str:
    """Convert Steam persona state to readable string"""
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
