"""Input validation and sanitization utilities."""
import re
import urllib.parse
from typing import Optional
import logging

logger = logging.getLogger(__name__)

def sanitize_string(text: str, max_length: int = 2000) -> str:
    """Sanitize a string for safe display in Discord."""
    if not text:
        return ""
    
    # Remove control characters except newlines and tabs
    text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]', '', text)
    
    # Truncate if too long
    if len(text) > max_length:
        text = text[:max_length - 3] + "..."
    
    return text.strip()

def validate_url(url: str) -> bool:
    """Validate if a string is a valid URL."""
    if not url:
        return False
    
    try:
        result = urllib.parse.urlparse(url)
        return all([result.scheme in ['http', 'https'], result.netloc])
    except Exception:
        return False

def sanitize_url(url: str) -> Optional[str]:
    """Sanitize and validate a URL."""
    if not url:
        return None
    
    url = url.strip()
    if not validate_url(url):
        return None
    
    return url

def clean_game_name(name: str) -> str:
    """Clean game name for searching/storage."""
    if not name:
        return ""
    
    # Remove extra whitespace
    name = re.sub(r'\s+', ' ', name.strip())
    
    # Remove special characters that might cause issues
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    
    # Limit length
    if len(name) > 200:
        name = name[:200]
    
    return name

def validate_discord_id(id_str: str) -> bool:
    """Validate if a string is a valid Discord ID."""
    try:
        id_int = int(id_str)
        return 17 <= len(id_str) <= 19 and id_int > 0
    except (ValueError, TypeError):
        return False

def validate_steam_id(steam_id: str) -> bool:
    """Validate if a string is a valid Steam ID."""
    try:
        id_int = int(steam_id)
        return len(steam_id) >= 17 and id_int > 0
    except (ValueError, TypeError):
        return False

def sanitize_webhook_url(url: str) -> Optional[str]:
    """Validate and sanitize a Discord webhook URL."""
    if not url:
        return None
    
    url = url.strip()
    
    # Must be a Discord webhook URL
    if not url.startswith('https://discord.com/api/webhooks/'):
        if url.startswith('https://discordapp.com/api/webhooks/'):
            # Old domain, convert
            url = url.replace('discordapp.com', 'discord.com')
        else:
            return None
    
    if not validate_url(url):
        return None
    
    return url

