"""Improved Steam account linker with safe storage."""
import logging
from typing import Optional, Dict
from utils.storage import load_json, save_json
from config.settings import settings

logger = logging.getLogger(__name__)

class SteamLinker:
    """Manage Discord-Steam account linking with safe file operations."""
    
    @staticmethod
    def load_links() -> Dict[str, str]:
        """Load Discord-Steam links from JSON file."""
        try:
            data = load_json(str(settings.STEAM_LINKS_FILE), default={})
            # Ensure all keys are strings
            return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"Error loading Steam links: {e}", exc_info=True)
            return {}
    
    @staticmethod
    def save_links(links: Dict[str, str]) -> bool:
        """Save Discord-Steam links to JSON file."""
        try:
            # Ensure all keys and values are strings
            clean_links = {str(k): str(v) for k, v in links.items()}
            return save_json(clean_links, str(settings.STEAM_LINKS_FILE))
        except Exception as e:
            logger.error(f"Error saving Steam links: {e}", exc_info=True)
            return False
    
    @staticmethod
    def link_account(discord_id: str, steam_id: str) -> bool:
        """Link a Discord account to a Steam ID."""
        links = SteamLinker.load_links()
        links[str(discord_id)] = str(steam_id)
        return SteamLinker.save_links(links)
    
    @staticmethod
    def unlink_account(discord_id: str) -> bool:
        """Unlink a Discord account from Steam."""
        links = SteamLinker.load_links()
        discord_id_str = str(discord_id)
        if discord_id_str in links:
            del links[discord_id_str]
            return SteamLinker.save_links(links)
        return False
    
    @staticmethod
    def get_steam_id(discord_id: str) -> Optional[str]:
        """Get Steam ID for a Discord user."""
        links = SteamLinker.load_links()
        return links.get(str(discord_id))

