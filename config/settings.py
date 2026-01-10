"""Configuration settings with environment variable support."""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class Settings:
    """Bot configuration settings."""
    
    # Discord
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    GUILD_ID: int = int(os.getenv("GUILD_ID", "1053630118360260650"))
    INPUT_CHANNEL_ID: int = int(os.getenv("INPUT_CHANNEL_ID", "1456371838325096733"))
    OUTPUT_CHANNEL_ID: int = int(os.getenv("OUTPUT_CHANNEL_ID", "1456386547879514317"))
    REQUEST_CHANNEL_ID: int = int(os.getenv("REQUEST_CHANNEL_ID", "1456387358952919132"))
    DASHBOARD_CHANNEL_ID: int = int(os.getenv("DASHBOARD_CHANNEL_ID", "1456480472128425985"))
    ALLOWED_CHANNEL_ID: int = int(os.getenv("ALLOWED_CHANNEL_ID", "1456371610473988388"))
    ADMIN_ROLE_ID: int = int(os.getenv("ADMIN_ROLE_ID", "1072117821397540954"))
    GITHUB_DISCORD_CHANNEL_ID: int = int(os.getenv("GITHUB_DISCORD_CHANNEL_ID", "1457161296750444595"))
    
    # Steam
    STEAM_API_KEY: str = os.getenv("STEAM_API_KEY", "")
    STEAM_OAUTH_PORT: int = int(os.getenv("STEAM_OAUTH_PORT", "5000"))
    STEAM_OAUTH_CALLBACK_URL: str = os.getenv("STEAM_OAUTH_CALLBACK_URL", "http://localhost:5000/auth/callback")
    STEAM_ACTIVITY_CHANNEL_ID: int = int(os.getenv("STEAM_ACTIVITY_CHANNEL_ID", "1431592234368766002"))
    
    # IGDB/Twitch
    TWITCH_CLIENT_ID: str = os.getenv("TWITCH_CLIENT_ID", "")
    TWITCH_CLIENT_SECRET: str = os.getenv("TWITCH_CLIENT_SECRET", "")
    
    # Features
    ENABLE_RSS_AUTO: bool = os.getenv("ENABLE_RSS_AUTO", "true").lower() == "true"
    
    # Minecraft
    MINECRAFT_SERVICE: str = os.getenv("MINECRAFT_SERVICE", "minecraft-bedrock")
    MINECRAFT_DIR: str = os.getenv("MINECRAFT_DIR", "/home/ubuntu/minecraft-bedrock")
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "bot.log")
    
    # AI Assistant
    AI_ENABLED: bool = os.getenv("AI_ENABLED", "true").lower() == "true"
    AI_API_KEY: str = os.getenv("GROQ_API_KEY", "") or os.getenv("OPENAI_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "groq").lower()  # openai, groq, anthropic (default to groq)
    AI_MODEL: str = os.getenv("AI_MODEL", "")  # Optional: override default model
    
    # Data files
    DATA_DIR: Path = Path("data")
    RSS_SEEN_FILE: Path = DATA_DIR / "fitgirl_seen_posts.json"
    BOT_STATE_FILE: Path = DATA_DIR / "bot_state.json"
    USER_DATA_FILE: Path = DATA_DIR / "user_data.json"
    REVIEWS_FILE: Path = DATA_DIR / "reviews_data.json"
    TAGS_FILE: Path = DATA_DIR / "tags_data.json"
    HEALTH_FILE: Path = DATA_DIR / "link_health_data.json"
    WEBHOOKS_FILE: Path = DATA_DIR / "webhooks_data.json"
    COLLECTIONS_FILE: Path = DATA_DIR / "collections_data.json"
    COMPATIBILITY_FILE: Path = DATA_DIR / "compatibility_data.json"
    STEAM_LINKS_FILE: Path = DATA_DIR / "steam_links.json"
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required settings are present."""
        required = [
            ("DISCORD_TOKEN", cls.DISCORD_TOKEN),
        ]
        
        missing = []
        for name, value in required:
            if not value:
                missing.append(name)
        
        if missing:
            logger.error(f"Missing required settings: {', '.join(missing)}")
            return False
        
        # Create data directory
        cls.DATA_DIR.mkdir(exist_ok=True)
        
        return True
    
    @classmethod
    def get_channel_config(cls, guild_id: Optional[int] = None) -> dict:
        """Get channel configuration (can be extended for per-guild config)."""
        return {
            "input": cls.INPUT_CHANNEL_ID,
            "output": cls.OUTPUT_CHANNEL_ID,
            "request": cls.REQUEST_CHANNEL_ID,
            "dashboard": cls.DASHBOARD_CHANNEL_ID,
            "allowed": cls.ALLOWED_CHANNEL_ID,
            "steam_activity": cls.STEAM_ACTIVITY_CHANNEL_ID,
            "github": cls.GITHUB_DISCORD_CHANNEL_ID,
        }

# Create settings instance
settings = Settings()

