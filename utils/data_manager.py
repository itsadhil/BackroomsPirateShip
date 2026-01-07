"""Centralized data management with safe storage operations."""
import logging
from typing import Any, Dict, Set, List
from utils.storage import load_json, save_json
from config.settings import settings

logger = logging.getLogger(__name__)

class DataManager:
    """Manages all bot data with safe file operations."""
    
    def __init__(self):
        self._seen_rss_posts: Set[str] = set()
        self._bot_state: Dict[str, Any] = {}
        self._user_data: Dict[str, Any] = {}
        self._reviews: Dict[str, Any] = {}
        self._tags: Dict[str, Any] = {}
        self._health: Dict[str, Any] = {}
        self._webhooks: Dict[str, Any] = {}
        self._collections: Dict[str, Any] = {}
        self._compatibility: Dict[str, Any] = {}
    
    def load_all(self):
        """Load all data files."""
        logger.info("Loading all data files...")
        
        # Load seen RSS posts
        seen_list = load_json(str(settings.RSS_SEEN_FILE), default=[])
        self._seen_rss_posts = set(seen_list) if isinstance(seen_list, list) else set()
        logger.info(f"Loaded {len(self._seen_rss_posts)} seen RSS posts")
        
        # Load bot state
        self._bot_state = load_json(str(settings.BOT_STATE_FILE), default={})
        logger.info("Loaded bot state")
        
        # Load user data
        self._user_data = load_json(str(settings.USER_DATA_FILE), default={})
        logger.info("Loaded user data")
        
        # Load reviews
        reviews_data = load_json(str(settings.REVIEWS_FILE), default={})
        self._reviews = {int(k): v for k, v in reviews_data.items() if str(k).isdigit()}
        logger.info(f"Loaded {len(self._reviews)} game reviews")
        
        # Load tags
        tags_data = load_json(str(settings.TAGS_FILE), default={})
        self._tags = {int(k): v for k, v in tags_data.items() if str(k).isdigit()}
        logger.info(f"Loaded tags for {len(self._tags)} games")
        
        # Load health
        health_data = load_json(str(settings.HEALTH_FILE), default={})
        self._health = {int(k): v for k, v in health_data.items() if str(k).isdigit()}
        logger.info(f"Loaded health data for {len(self._health)} games")
        
        # Load webhooks
        webhooks_data = load_json(str(settings.WEBHOOKS_FILE), default={})
        self._webhooks = {int(k): v for k, v in webhooks_data.items() if str(k).isdigit()}
        logger.info(f"Loaded {len(self._webhooks)} webhooks")
        
        # Load collections
        collections_data = load_json(str(settings.COLLECTIONS_FILE), default={})
        self._collections = {int(k): v for k, v in collections_data.items() if str(k).isdigit()}
        logger.info(f"Loaded collections for {len(self._collections)} users")
        
        # Load compatibility
        compat_data = load_json(str(settings.COMPATIBILITY_FILE), default={})
        self._compatibility = {int(k): v for k, v in compat_data.items() if str(k).isdigit()}
        logger.info(f"Loaded compatibility reports for {len(self._compatibility)} games")
    
    def save_all(self):
        """Save all data files."""
        logger.info("Saving all data files...")
        
        # Save seen RSS posts
        save_json(list(self._seen_rss_posts), str(settings.RSS_SEEN_FILE))
        
        # Save bot state
        save_json(self._bot_state, str(settings.BOT_STATE_FILE))
        
        # Save user data
        save_json(self._user_data, str(settings.USER_DATA_FILE))
        
        # Save reviews
        save_json({str(k): v for k, v in self._reviews.items()}, str(settings.REVIEWS_FILE))
        
        # Save tags
        save_json({str(k): v for k, v in self._tags.items()}, str(settings.TAGS_FILE))
        
        # Save health
        save_json({str(k): v for k, v in self._health.items()}, str(settings.HEALTH_FILE))
        
        # Save webhooks
        save_json({str(k): v for k, v in self._webhooks.items()}, str(settings.WEBHOOKS_FILE))
        
        # Save collections
        save_json({str(k): v for k, v in self._collections.items()}, str(settings.COLLECTIONS_FILE))
        
        # Save compatibility
        save_json({str(k): v for k, v in self._compatibility.items()}, str(settings.COMPATIBILITY_FILE))
        
        logger.info("All data files saved")
    
    # Property accessors
    @property
    def seen_rss_posts(self) -> Set[str]:
        return self._seen_rss_posts
    
    @property
    def bot_state(self) -> Dict[str, Any]:
        return self._bot_state
    
    @property
    def user_data(self) -> Dict[str, Any]:
        return self._user_data
    
    @property
    def reviews(self) -> Dict[int, Any]:
        return self._reviews
    
    @property
    def tags(self) -> Dict[int, Any]:
        return self._tags
    
    @property
    def health(self) -> Dict[int, Any]:
        return self._health
    
    @property
    def webhooks(self) -> Dict[int, Any]:
        return self._webhooks
    
    @property
    def collections(self) -> Dict[int, Any]:
        return self._collections
    
    @property
    def compatibility(self) -> Dict[int, Any]:
        return self._compatibility
    
    def save_seen_posts(self):
        """Save seen RSS posts immediately."""
        save_json(list(self._seen_rss_posts), str(settings.RSS_SEEN_FILE))
    
    def save_bot_state(self):
        """Save bot state immediately."""
        save_json(self._bot_state, str(settings.BOT_STATE_FILE))
    
    def save_user_data(self):
        """Save user data immediately."""
        save_json(self._user_data, str(settings.USER_DATA_FILE))
    
    def save_reviews(self):
        """Save reviews immediately."""
        save_json({str(k): v for k, v in self._reviews.items()}, str(settings.REVIEWS_FILE))
    
    def save_tags(self):
        """Save tags immediately."""
        save_json({str(k): v for k, v in self._tags.items()}, str(settings.TAGS_FILE))
    
    def save_health(self):
        """Save health data immediately."""
        save_json({str(k): v for k, v in self._health.items()}, str(settings.HEALTH_FILE))
    
    def save_webhooks(self):
        """Save webhooks immediately."""
        save_json({str(k): v for k, v in self._webhooks.items()}, str(settings.WEBHOOKS_FILE))
    
    def save_collections(self):
        """Save collections immediately."""
        save_json({str(k): v for k, v in self._collections.items()}, str(settings.COLLECTIONS_FILE))
    
    def save_compatibility(self):
        """Save compatibility data immediately."""
        save_json({str(k): v for k, v in self._compatibility.items()}, str(settings.COMPATIBILITY_FILE))

# Global data manager instance
data_manager = DataManager()

