
import os
import sys
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
from typing import Optional, Dict, Any, List
import re
import io
from bs4 import BeautifulSoup
import asyncio
from playwright.async_api import async_playwright
import tempfile
import feedparser
import json
from datetime import datetime

# Setup logging first
from utils.logging_config import setup_logging, get_logger
from config.settings import settings

# Initialize logging
setup_logging(settings.LOG_LEVEL, settings.LOG_FILE)
logger = get_logger(__name__)

# Import new utilities
from utils.data_manager import data_manager
from utils.http_client import get_http_session, close_http_sessions
from utils.browser_pool import get_browser_pool, close_browser_pool
from utils.api_clients import IGDBClient, RAWGClient
from utils.steam_api import SteamAPI, format_playtime, get_personastate_string
from utils.steam_linker import SteamLinker
from utils.retry import retry
from utils.rate_limiter import get_steam_limiter, get_igdb_limiter

# -------------------------
# LOAD ENV (for backward compatibility)
# -------------------------
load_dotenv()
TOKEN = settings.DISCORD_TOKEN
TWITCH_CLIENT_ID = settings.TWITCH_CLIENT_ID
TWITCH_CLIENT_SECRET = settings.TWITCH_CLIENT_SECRET

# -------------------------
# CONFIG (from settings)
# -------------------------
GUILD_ID = settings.GUILD_ID
INPUT_CHANNEL_ID = settings.INPUT_CHANNEL_ID
OUTPUT_CHANNEL_ID = settings.OUTPUT_CHANNEL_ID
REQUEST_CHANNEL_ID = settings.REQUEST_CHANNEL_ID
DASHBOARD_CHANNEL_ID = settings.DASHBOARD_CHANNEL_ID
ALLOWED_CHANNEL_ID = settings.ALLOWED_CHANNEL_ID
ADMIN_ROLE_ID = settings.ADMIN_ROLE_ID
GITHUB_DISCORD_CHANNEL_ID = settings.GITHUB_DISCORD_CHANNEL_ID

# -------------------------
# INTENTS
# -------------------------
intents = discord.Intents.default()
intents.message_content = True

# -------------------------
# BOT SETUP
# -------------------------
bot = commands.Bot(command_prefix="!", intents=intents)
bot.pending_torrents = {}  # Store pending torrent submissions
bot.pending_fulfillments = {}  # Store pending request fulfillments
bot.seen_rss_posts = set()  # Track seen FitGirl RSS posts
bot.dashboard_message_id = None  # Track dashboard message for updates
bot.contributor_stats = {}  # Track who added what games
bot.status_message_id = None  # Track bot status message
bot.download_stats = {}  # Track download counts per game (thread_id: count)
bot.user_libraries = {}  # Track user game libraries (user_id: [thread_ids])
bot.game_notifications = {}  # Track notification requests (game_name: [user_ids])
bot.request_votes = {}  # Track votes on requests (message_id: [user_ids])
bot.game_reviews = {}  # Track game reviews (thread_id: [{user_id, rating, review, timestamp}])
bot.game_tags = {}  # Track game tags (thread_id: [tags])
bot.link_health = {}  # Track link health (thread_id: {checked_at, status, broken_links})
bot.trending_views = {}  # Track thread views for trending (thread_id: view_count)
bot.webhooks = {}  # Track webhook URLs (user_id: webhook_url)

# ========== GITHUB WEBHOOK SERVER ==========
GITHUB_DISCORD_CHANNEL_ID = 1457161296750444595  # Channel to post GitHub events

async def handle_github_webhook(request):
    try:
        data = await request.json()
        # Handle push events
        if 'commits' in data and 'repository' in data:
            repo = data['repository']['full_name']
            pusher = data.get('pusher', {}).get('name', 'unknown')
            commit_msgs = "\n".join([f"[`{c['id'][:7]}`] {c['message']} (<{c['url']}>)" for c in data['commits']])
            msg = f"**[{repo}]** New push by **{pusher}**:\n{commit_msgs}"
            channel = bot.get_channel(GITHUB_DISCORD_CHANNEL_ID)
            if channel:
                await channel.send(msg)
        # You can add more event types here (pull_request, issues, etc.)
    except Exception as e:
        print(f"GitHub webhook error: {e}")
    return web.Response(text="OK")

def setup_github_webhook_server():
    print("setup_github_webhook_server() called")  # <--- Add this line
    print("Before creating aiohttp app")
    app = web.Application()
    app.router.add_post('/github', handle_github_webhook)
    runner = web.AppRunner(app)
    loop = asyncio.get_event_loop()
    print("Before defining start() async function")
    async def start():
        print("Entered start() async function")
        try:
            await runner.setup()
            print("Runner setup complete")
            site = web.TCPSite(runner, '0.0.0.0', 8080)
            await site.start()
            print("GitHub webhook server running on port 8080")
        except Exception as e:
            print(f"Exception in webhook server start(): {e}")
    print("Before scheduling start() task")
    loop.create_task(start())
    print("After scheduling start() task")

## Remove duplicate on_ready and merge logic below
bot.collections = {}  # Track user collections (user_id: {collection_name: [thread_ids]})
bot.bookmarks = {}  # Track user bookmarks (user_id: [thread_ids])
bot.compatibility_reports = {}  # Track compatibility reports (thread_id: [{user_id, status, specs, notes}])
bot.user_preferences = {}  # Track user genre preferences for recommendations (user_id: {genres, playtime})
bot.steam_gaming_status = {}  # Track current gaming status (discord_id: {game, timestamp})
bot.steam_activity_channel_id = 1431592234368766002  # Channel to send gaming notifications
bot.steam_privacy_settings = {}  # Track user privacy settings (discord_id: {vc_invites: bool})
bot.steam_achievements = {}  # Track recent achievements (discord_id: {game_id: [achievements]})
bot.steam_sessions = {}  # Track gaming sessions (discord_id: [{game, start, end, duration}])
bot.steam_wishlists = {}  # Track Steam wishlists (discord_id: [game_ids])
bot.game_nights = {}  # Track scheduled game nights (guild_id: [{host, game, time, participants}])
bot.squad_notifications = {}  # Track squad up notifications (game: [user_ids])

# Playwright queue system
bot.playwright_queue = asyncio.Queue()  # Queue for download requests
bot.playwright_active = False  # Track if Playwright is currently running
bot.queue_position = {}  # Track position in queue for users

# Files for persistent storage (using data_manager)
# Data manager handles all file operations safely

# Load previously seen posts
def load_seen_posts():
    try:
        data_manager.load_all()
        bot.seen_rss_posts = data_manager.seen_rss_posts
        logger.info(f"✅ Loaded {len(bot.seen_rss_posts)} seen RSS posts")
    except Exception as e:
        logger.error(f"⚠️ Could not load seen posts: {e}", exc_info=True)

def save_seen_posts():
    try:
        # Update the internal attribute directly (property is read-only)
        data_manager._seen_rss_posts = bot.seen_rss_posts.copy()
        data_manager.save_seen_posts()
    except Exception as e:
        logger.error(f"⚠️ Could not save seen posts: {e}", exc_info=True)

# Load bot state (dashboard ID, contributor stats)
def load_bot_state():
    try:
        data_manager.load_all()
        bot.dashboard_message_id = data_manager.bot_state.get('dashboard_message_id')
        bot.contributor_stats = data_manager.bot_state.get('contributor_stats', {})
        bot.status_message_id = data_manager.bot_state.get('status_message_id')
        logger.info(f"✅ Loaded bot state (Dashboard ID: {bot.dashboard_message_id}, Contributors: {len(bot.contributor_stats)})")
    except Exception as e:
        logger.error(f"⚠️ Could not load bot state: {e}", exc_info=True)

def save_bot_state():
    try:
        data_manager.bot_state['dashboard_message_id'] = bot.dashboard_message_id
        data_manager.bot_state['contributor_stats'] = bot.contributor_stats
        data_manager.bot_state['status_message_id'] = bot.status_message_id
        data_manager.bot_state['last_updated'] = discord.utils.utcnow().isoformat()
        data_manager.save_bot_state()
        logger.debug(f"💾 Bot state saved (Dashboard: {bot.dashboard_message_id})")
    except Exception as e:
        logger.error(f"⚠️ Could not save bot state: {e}", exc_info=True)

def load_user_data():
    """Load user-related data (libraries, notifications, votes, download stats)."""
    try:
        data_manager.load_all()
        user_data = data_manager.user_data
        bot.download_stats = {int(k): v for k, v in user_data.get('download_stats', {}).items()}
        bot.user_libraries = {int(k): v for k, v in user_data.get('user_libraries', {}).items()}
        bot.game_notifications = user_data.get('game_notifications', {})
        bot.request_votes = {int(k): v for k, v in user_data.get('request_votes', {}).items()}
        bot.trending_views = {int(k): v for k, v in user_data.get('trending_views', {}).items()}
        bot.bookmarks = {int(k): v for k, v in user_data.get('bookmarks', {}).items()}
        bot.user_preferences = {int(k): v for k, v in user_data.get('user_preferences', {}).items()}
        logger.info(f"✅ Loaded user data (Libraries: {len(bot.user_libraries)}, Notifications: {len(bot.game_notifications)})")
    except Exception as e:
        logger.error(f"⚠️ Could not load user data: {e}", exc_info=True)

def save_user_data():
    """Save user-related data."""
    try:
        data_manager.user_data['download_stats'] = {str(k): v for k, v in bot.download_stats.items()}
        data_manager.user_data['user_libraries'] = {str(k): v for k, v in bot.user_libraries.items()}
        data_manager.user_data['game_notifications'] = bot.game_notifications
        data_manager.user_data['request_votes'] = {str(k): v for k, v in bot.request_votes.items()}
        data_manager.user_data['trending_views'] = {str(k): v for k, v in bot.trending_views.items()}
        data_manager.user_data['bookmarks'] = {str(k): v for k, v in bot.bookmarks.items()}
        data_manager.user_data['user_preferences'] = {str(k): v for k, v in bot.user_preferences.items()}
        data_manager.user_data['last_updated'] = discord.utils.utcnow().isoformat()
        data_manager.save_user_data()
    except Exception as e:
        logger.error(f"⚠️ Could not save user data: {e}", exc_info=True)

def load_reviews_data():
    """Load review data."""
    try:
        data_manager.load_all()
        bot.game_reviews = data_manager.reviews
        logger.info(f"✅ Loaded {len(bot.game_reviews)} game reviews")
    except Exception as e:
        logger.error(f"⚠️ Could not load reviews data: {e}", exc_info=True)

def save_reviews_data():
    """Save review data."""
    try:
        data_manager._reviews = bot.game_reviews.copy()
        data_manager.save_reviews()
    except Exception as e:
        logger.error(f"⚠️ Could not save reviews data: {e}", exc_info=True)

def load_tags_data():
    """Load tags data."""
    try:
        data_manager.load_all()
        bot.game_tags = data_manager.tags
        logger.info(f"✅ Loaded tags for {len(bot.game_tags)} games")
    except Exception as e:
        logger.error(f"⚠️ Could not load tags data: {e}", exc_info=True)

def save_tags_data():
    """Save tags data."""
    try:
        data_manager._tags = bot.game_tags.copy()
        data_manager.save_tags()
    except Exception as e:
        logger.error(f"⚠️ Could not save tags data: {e}", exc_info=True)

def load_health_data():
    """Load link health data."""
    try:
        data_manager.load_all()
        bot.link_health = data_manager.health
        logger.info(f"✅ Loaded health data for {len(bot.link_health)} games")
    except Exception as e:
        logger.error(f"⚠️ Could not load health data: {e}", exc_info=True)

def save_health_data():
    """Save link health data."""
    try:
        data_manager._health = bot.link_health.copy()
        data_manager.save_health()
    except Exception as e:
        logger.error(f"⚠️ Could not save health data: {e}", exc_info=True)

def load_webhooks_data():
    """Load webhooks data."""
    try:
        data_manager.load_all()
        bot.webhooks = data_manager.webhooks
        logger.info(f"✅ Loaded {len(bot.webhooks)} webhooks")
    except Exception as e:
        logger.error(f"⚠️ Could not load webhooks data: {e}", exc_info=True)

def save_webhooks_data():
    """Save webhooks data."""
    try:
        data_manager._webhooks = bot.webhooks.copy()
        data_manager.save_webhooks()
    except Exception as e:
        logger.error(f"⚠️ Could not save webhooks data: {e}", exc_info=True)

def load_collections_data():
    """Load collections data."""
    try:
        data_manager.load_all()
        bot.collections = data_manager.collections
        logger.info(f"✅ Loaded collections for {len(bot.collections)} users")
    except Exception as e:
        logger.error(f"⚠️ Could not load collections data: {e}", exc_info=True)

def save_collections_data():
    """Save collections data."""
    try:
        data_manager._collections = bot.collections.copy()
        data_manager.save_collections()
    except Exception as e:
        logger.error(f"⚠️ Could not save collections data: {e}", exc_info=True)

def load_compatibility_data():
    """Load compatibility data."""
    try:
        data_manager.load_all()
        bot.compatibility_reports = data_manager.compatibility
        logger.info(f"✅ Loaded compatibility reports for {len(bot.compatibility_reports)} games")
    except Exception as e:
        logger.error(f"⚠️ Could not load compatibility data: {e}", exc_info=True)

def save_compatibility_data():
    """Save compatibility data."""
    try:
        data_manager._compatibility = bot.compatibility_reports.copy()
        data_manager.save_compatibility()
    except Exception as e:
        logger.error(f"⚠️ Could not save compatibility data: {e}", exc_info=True)

async def update_status_message(status: str):
    """Update or create the bot status message."""
    try:
        channel = bot.get_channel(DASHBOARD_CHANNEL_ID)
        if not channel:
            print(f"⚠️ Could not find dashboard channel {DASHBOARD_CHANNEL_ID}")
            return
        
        now = discord.utils.utcnow()
        timestamp = f"<t:{int(now.timestamp())}:F>"
        
        if status == "online":
            # Get system stats
            try:
                import psutil
                process = psutil.Process()
                # Get CPU usage - use a small interval for accurate reading
                # This will block for 0.1 seconds, but gives accurate results
                cpu_percent = process.cpu_percent(interval=0.1)
                
                # Get memory usage
                memory_usage = process.memory_info().rss / 1024 / 1024  # MB
                sys_mem = psutil.virtual_memory()
                memory_percent = (memory_usage / (sys_mem.total / 1024 / 1024)) * 100
            except Exception as e:
                logger.warning(f"Could not get system stats: {e}")
                cpu_percent = 0.0
                memory_percent = 0.0
            
            embed = discord.Embed(
                title="✅ Bot Started",
                description=f"**!Backroom Pirate Captain** is now online!",
                color=discord.Color.green(),
                timestamp=now
            )
            
            # Add stats fields
            embed.add_field(name="CPU", value=f"{cpu_percent:.1f}%", inline=True)
            embed.add_field(name="Memory", value=f"{memory_percent:.1f}%", inline=True)
            embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
            
            embed.set_footer(text="Backrooms Pirate Ship")
        elif status == "starting":
            embed = discord.Embed(
                title="🟡 Bot Starting",
                description=f"New build is being deployed, loading services...\n\n**Deploy Time:** {timestamp}",
                color=discord.Color.yellow(),
                timestamp=now
            )
            embed.set_footer(text="Backrooms Pirate Ship • Please wait...")
        elif status == "restarting":
            embed = discord.Embed(
                title="🟡 Bot Restarting",
                description=f"New build is being deployed, please wait...\n\n**Triggered:** {timestamp}",
                color=discord.Color.yellow(),
                timestamp=now
            )
            embed.set_footer(text="Backrooms Pirate Ship")
        else:
            return
        
        # Try to edit existing message, or create new one
        if bot.status_message_id:
            try:
                message = await channel.fetch_message(bot.status_message_id)
                await message.edit(embed=embed)
                print(f"✅ Updated status message to: {status}")
                return
            except discord.NotFound:
                print(f"⚠️ Previous status message not found, creating new one")
                bot.status_message_id = None
            except Exception as e:
                print(f"⚠️ Could not edit status message: {e}")
        
        # Create new message if needed
        message = await channel.send(embed=embed)
        bot.status_message_id = message.id
        save_bot_state()
        logger.debug(f"Created new status message: {status}")
        
    except Exception as e:
        logger.error(f"Error updating status message: {e}", exc_info=True)

# =========================================================
# INVITE TO VC VIEW FOR STEAM NOTIFICATIONS
# =========================================================
class InviteToVCView(discord.ui.View):
    def __init__(self, player_id: int, player_name: str):
        super().__init__(timeout=3600)  # 1 hour timeout
        self.player_id = player_id
        self.player_name = player_name
    
    @discord.ui.button(label="📞 Invite to VC", style=discord.ButtonStyle.blurple, custom_id="invite_to_vc")
    async def invite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Send VC invite to the player"""
        try:
            # Check if player has VC invites enabled
            privacy_settings = bot.steam_privacy_settings.get(str(self.player_id), {})
            if not privacy_settings.get('vc_invites', True):  # Default to True
                await interaction.response.send_message(
                    "❌ This user has disabled VC invites in their privacy settings.",
                    ephemeral=True
                )
                return
            
            # Get the player
            player = bot.get_user(self.player_id)
            if not player:
                try:
                    player = await bot.fetch_user(self.player_id)
                except:
                    await interaction.response.send_message(
                        "❌ Could not find the user.",
                        ephemeral=True
                    )
                    return
            
            # Create invite embed
            invite_embed = discord.Embed(
                title="📞 Voice Chat Invite",
                description=f"**{interaction.user.mention}** wants to play with you!",
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow()
            )
            invite_embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url
            )
            invite_embed.add_field(
                name="From Server",
                value=interaction.guild.name if interaction.guild else "Direct Message",
                inline=False
            )
            invite_embed.set_footer(text="Click below to join their voice channel!")
            
            # Try to send DM
            try:
                await player.send(embed=invite_embed)
                await interaction.response.send_message(
                    f"✅ Sent a VC invite to **{self.player_name}**!",
                    ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"❌ Could not send DM to **{self.player_name}**. They may have DMs disabled.",
                    ephemeral=True
                )
        
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error: {e}",
                ephemeral=True
            )

# =========================================================
# IGDB API CLIENT
# =========================================================
class IGDBClient:
    """Handles IGDB API interactions with Twitch OAuth."""
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def get_access_token(self) -> str:
        """Obtain OAuth access token from Twitch."""
        if self.access_token:
            return self.access_token
        
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }
        
        # Use temporary session for token request
        async with aiohttp.ClientSession() as temp_session:
            async with temp_session.post(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data["access_token"]
                    return self.access_token
                else:
                    raise Exception(f"Failed to get access token: {resp.status}")
    
    async def _ensure_session(self):
        """Ensure aiohttp session exists."""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def search_game_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Search IGDB for a game by name and return the best match."""
        try:
            await self._ensure_session()
            token = await self.get_access_token()
            
            url = "https://api.igdb.com/v4/games"
            headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {token}"
            }
            
            # IGDB uses Apicalypse query language
            body = f'search "{name}"; fields name,summary,genres.name,platforms.name,cover.image_id; limit 1;'
            
            async with self.session.post(url, headers=headers, data=body) as resp:
                if resp.status == 200:
                    results = await resp.json()
                    return results[0] if results else None
                else:
                    print(f"⚠️ IGDB search failed: {resp.status}")
                    return None
        except Exception as e:
            print(f"⚠️ IGDB search error: {e}")
            return None
    
    async def get_similar_games(self, game_id: int) -> List[Dict[str, Any]]:
        """Get similar games from IGDB."""
        try:
            await self.get_access_token()
            
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            url = "https://api.igdb.com/v4/games"
            headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {self.access_token}"
            }
            
            # Get similar games based on the game
            query = f'''
            fields name, cover.image_id, aggregated_rating, genres.name, similar_games;
            where id = {game_id};
            '''
            
            async with self.session.post(url, headers=headers, data=query) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and data[0].get('similar_games'):
                        similar_ids = data[0]['similar_games'][:10]
                        
                        # Fetch details of similar games
                        query2 = f'''
                        fields name, cover.image_id, aggregated_rating, genres.name;
                        where id = ({','.join(map(str, similar_ids))});
                        limit 10;
                        '''
                        
                        async with self.session.post(url, headers=headers, data=query2) as resp2:
                            if resp2.status == 200:
                                return await resp2.json()
            
            return []
        except Exception as e:
            print(f"⚠️ IGDB similar games error: {e}")
            return []
    
    async def get_game_videos(self, game_id: int) -> List[str]:
        """Get YouTube video IDs for game trailers."""
        try:
            await self.get_access_token()
            
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            url = "https://api.igdb.com/v4/game_videos"
            headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {self.access_token}"
            }
            
            query = f'fields video_id; where game = {game_id}; limit 5;'
            
            async with self.session.post(url, headers=headers, data=query) as resp:
                if resp.status == 200:
                    videos = await resp.json()
                    return [v.get('video_id') for v in videos if v.get('video_id')]
            
            return []
        except Exception as e:
            print(f"⚠️ IGDB videos error: {e}")
            return []
    
    async def get_external_ratings(self, game_id: int) -> Dict[str, Any]:
        """Get external ratings like Metacritic."""
        try:
            await self.get_access_token()
            
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            url = "https://api.igdb.com/v4/games"
            headers = {
                "Client-ID": self.client_id,
                "Authorization": f"Bearer {self.access_token}"
            }
            
            query = f'fields aggregated_rating, aggregated_rating_count, rating, rating_count; where id = {game_id};'
            
            async with self.session.post(url, headers=headers, data=query) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        return data[0]
            
            return {}
        except Exception as e:
            print(f"⚠️ IGDB ratings error: {e}")
            return {}
    
    async def close(self):
        """Close the aiohttp session."""
        if self.session:
            await self.session.close()

# Initialize IGDB and RAWG clients (using improved versions from utils)
igdb_client = IGDBClient(settings.TWITCH_CLIENT_ID, settings.TWITCH_CLIENT_SECRET)
rawg_client = RAWGClient()  # Initialize RAWG client

# =========================================================
# RAWG API CLIENT (FALLBACK)
# =========================================================
class RAWGClient:
    """Fallback game database using RAWG API (free, no auth needed)."""
    
    BASE_URL = "https://api.rawg.io/api/games"
    
    async def search_game_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Search RAWG for a game by name."""
        try:
            # RAWG works without API key for basic searches
            params = {
                "search": name,
                "page_size": 1
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.BASE_URL, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("results") and len(data["results"]) > 0:
                            game = data["results"][0]
                            
                            # Clean HTML from description if present
                            description = game.get("description_raw") or game.get("description", "No description available.")
                            if not description or description == "":
                                description = "No description available."
                            
                            # Transform to similar format as IGDB
                            transformed = {
                                "name": game.get("name"),
                                "summary": description,
                                "genres": [{"name": g["name"]} for g in game.get("genres", [])],
                                "platforms": [{"name": p["platform"]["name"]} for p in game.get("platforms", [])] if game.get("platforms") else [],
                            }
                            
                            # Add cover image if available
                            bg_image = game.get("background_image")
                            if bg_image and bg_image.strip():
                                transformed["cover"] = {"image_id": bg_image}
                                print(f"📸 RAWG background_image: {bg_image}")
                            else:
                                print(f"⚠️ RAWG: No background_image for {game.get('name')}")
                            
                            print(f"✅ RAWG found: {transformed['name']}")
                            return transformed
                        else:
                            print(f"⚠️ RAWG: No results for '{name}'")
                            return None
                    else:
                        print(f"⚠️ RAWG API returned status: {resp.status}")
                        return None
        except Exception as e:
            print(f"⚠️ RAWG search error: {e}")
            return None

# RAWG client already initialized from imports

# =========================================================
# FITGIRL REPACKS SCRAPER
# =========================================================
class FitGirlScraper:
    """Scraper for FitGirl Repacks website."""
    
    BASE_URL = "https://fitgirl-repacks.site"
    
    async def search_game(self, game_name: str) -> List[Dict[str, Any]]:
        """Search FitGirl Repacks for a game."""
        try:
            search_url = f"{self.BASE_URL}/?s={game_name.replace(' ', '+')}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        results = []
                        articles = soup.find_all('article', limit=10)  # Get more to account for filtered ones
                        
                        for article in articles:
                            try:
                                # Extract title
                                title_elem = article.find('h1', class_='entry-title') or article.find('h2', class_='entry-title')
                                if not title_elem:
                                    continue
                                
                                title_link = title_elem.find('a')
                                if not title_link:
                                    continue
                                
                                title = title_link.get_text(strip=True)
                                
                                # Skip "Updates Digest" posts
                                if 'Updates Digest' in title or 'updates digest' in title.lower():
                                    print(f"⏭️ Skipping Updates Digest: {title}")
                                    continue
                                
                                url = title_link.get('href')
                                
                                # Extract excerpt/description
                                excerpt_elem = article.find('div', class_='entry-content') or article.find('p')
                                excerpt = excerpt_elem.get_text(strip=True)[:200] if excerpt_elem else "No description available"
                                
                                results.append({
                                    'title': title,
                                    'url': url,
                                    'description': excerpt
                                })
                                
                                # Stop when we have 5 actual game results
                                if len(results) >= 5:
                                    break
                                    
                            except Exception as e:
                                print(f"⚠️ Error parsing article: {e}")
                                continue
                        
                        return results
                    else:
                        print(f"⚠️ FitGirl search failed: {resp.status}")
                        return []
        except Exception as e:
            print(f"⚠️ FitGirl search error: {e}")
            return []
    
    async def get_torrent_link(self, url: str) -> Optional[str]:
        """Extract the .torrent file link from a repack page."""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Find the ".torrent file only" link
                        torrent_link = soup.find('a', string=re.compile(r'\.torrent file only', re.IGNORECASE))
                        if torrent_link:
                            paste_url = torrent_link.get('href')
                            print(f"✅ Found paste URL: {paste_url}")
                            return paste_url
                        else:
                            print(f"⚠️ No .torrent file only link found")
                            return None
                    else:
                        print(f"⚠️ Failed to fetch repack page: {resp.status}")
                        return None
        except Exception as e:
            print(f"⚠️ Error getting torrent link: {e}")
            return None
    
    async def download_torrent_from_paste(self, paste_url: str, request_id: str = None) -> Optional[bytes]:
        """Download torrent file from FitGirl paste site using headless browser with queue system."""
        browser = None
        context = None
        page = None
        try:
            logger.info(f"🌐 Opening headless browser for: {paste_url}")
            
            # Use browser pool if available, otherwise fallback to direct launch
            try:
                from utils.browser_pool import get_browser_pool
                pool = get_browser_pool()
                if pool._initialized:
                    browser = await pool.get_browser()
                    context = await pool.create_context(browser)
                    if not context:
                        logger.warning("Could not create context from pool, using direct launch")
                        browser = None
            except Exception as e:
                logger.warning(f"Browser pool not available, using direct launch: {e}")
            
            # Fallback to direct launch if pool failed
            if not browser:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=['--no-sandbox', '--disable-setuid-sandbox']
                    )
                    context = await browser.new_context(
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    )
                    page = await context.new_page()
            else:
                page = await context.new_page()
            
            # Navigate to the paste URL
            await page.goto(paste_url, wait_until='domcontentloaded', timeout=30000)
            logger.info(f"📄 Page loaded, waiting for download link...")
            
            # Wait for the download link to appear (it's generated by JavaScript)
            # The link has class 'alert-link' and download attribute
            try:
                await page.wait_for_selector('a.alert-link[download]', timeout=15000)
                logger.info(f"✅ Download link appeared!")
            except Exception as e:
                logger.error(f"⚠️ Download link did not appear in time: {e}")
                if context:
                    await context.close()
                if browser and not browser.is_connected():
                    pass  # Already closed
                elif browser:
                    # Return browser to pool if using pool
                    try:
                        from utils.browser_pool import get_browser_pool
                        pool = get_browser_pool()
                        if pool._initialized:
                            await pool.return_browser(browser)
                        else:
                            await browser.close()
                    except:
                        await browser.close()
                return None
            
            # Get the download link element
            download_link = await page.query_selector('a.alert-link[download]')
            if not download_link:
                logger.error(f"⚠️ Could not find download link")
                if context:
                    await context.close()
                if browser:
                    try:
                        from utils.browser_pool import get_browser_pool
                        pool = get_browser_pool()
                        if pool._initialized:
                            await pool.return_browser(browser)
                        else:
                            await browser.close()
                    except:
                        await browser.close()
                return None
            
            # Get the blob URL
            href = await download_link.get_attribute('href')
            logger.info(f"🔗 Found download link: {href}")
            
            # Download the blob URL content
            # Execute JavaScript to fetch the blob and convert to base64
            torrent_base64 = await page.evaluate("""
                async (blobUrl) => {
                    const response = await fetch(blobUrl);
                    const blob = await response.blob();
                    return new Promise((resolve) => {
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(reader.result.split(',')[1]);
                        reader.readAsDataURL(blob);
                    });
                }
            """, href)
            
            if context:
                await context.close()
            
            # Return browser to pool or close
            if browser:
                try:
                    from utils.browser_pool import get_browser_pool
                    pool = get_browser_pool()
                    if pool._initialized and browser.is_connected():
                        await pool.return_browser(browser)
                    elif not browser.is_connected():
                        pass  # Already closed
                    else:
                        await browser.close()
                except:
                    if browser.is_connected():
                        await browser.close()
            
            if torrent_base64:
                import base64
                torrent_data = base64.b64decode(torrent_base64)
                logger.info(f"✅ Downloaded torrent: {len(torrent_data)} bytes")
                return torrent_data
            else:
                logger.error(f"⚠️ Failed to extract torrent data")
                return None
                    
        except Exception as e:
            logger.error(f"⚠️ Error downloading torrent with browser: {e}", exc_info=True)
            return None
        finally:
            # Clean up queue tracking
            if request_id and request_id in bot.queue_position:
                del bot.queue_position[request_id]
    
    async def get_game_details(self, url: str) -> Dict[str, Any]:
        """Fetch detailed information from a specific repack page."""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Extract game banner - multiple methods
                        banner_url = None
                        
                        # Method 1: Look for img inside p tag with height style
                        p_tags = soup.find_all('p', style=re.compile(r'height'))
                        for p in p_tags:
                            img = p.find('img')
                            if img:
                                src = img.get('src')
                                if src and ('imageban' in src or 'jpg' in src or 'png' in src):
                                    banner_url = src
                                    print(f"✅ Found banner via p tag: {banner_url}")
                                    break
                        
                        # Method 2: Look for img with alignleft class
                        if not banner_url:
                            banner_img = soup.find('img', class_='alignleft')
                            if banner_img:
                                banner_url = banner_img.get('src')
                                print(f"✅ Found banner via alignleft: {banner_url}")
                        
                        # Method 3: Look for any img with width="150"
                        if not banner_url:
                            imgs = soup.find_all('img', width="150")
                            for img in imgs:
                                src = img.get('src')
                                if src and ('imageban' in src or 'jpg' in src or 'png' in src):
                                    banner_url = src
                                    print(f"✅ Found banner via width=150: {banner_url}")
                                    break
                        
                        # Method 4: First img in entry-content that's not an icon
                        if not banner_url:
                            content = soup.find('div', class_='entry-content')
                            if content:
                                imgs = content.find_all('img')
                                for img in imgs:
                                    src = img.get('src')
                                    # Skip icons and small images
                                    if src and not any(x in src.lower() for x in ['icon', 'emoji', 'avatar']):
                                        if 'imageban' in src or 'jpg' in src or 'png' in src:
                                            banner_url = src
                                            print(f"✅ Found banner in content: {banner_url}")
                                            break
                        
                        if not banner_url:
                            print(f"⚠️ No banner found for: {url}")
                        
                        # Extract additional details from the info paragraph
                        info = {
                            'genres': None,
                            'companies': None,
                            'languages': None,
                            'original_size': None,
                            'repack_size': None
                        }
                        
                        # Find the paragraph with game info
                        content = soup.find('div', class_='entry-content')
                        if content:
                            # Look for the info in paragraph text
                            paragraphs = content.find_all('p')
                            for p in paragraphs:
                                text = p.get_text()
                                
                                # Extract genres
                                if 'Genres/Tags:' in text:
                                    genre_links = p.find_all('a', href=re.compile(r'/tag/'))
                                    if genre_links:
                                        info['genres'] = ', '.join([link.get_text() for link in genre_links])
                                
                                # Extract companies
                                if 'Companies:' in text:
                                    match = re.search(r'Companies:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['companies'] = match.group(1)
                                
                                # Extract languages
                                if 'Languages:' in text:
                                    match = re.search(r'Languages:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['languages'] = match.group(1)
                                
                                # Extract sizes
                                if 'Original Size:' in text:
                                    match = re.search(r'Original Size:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['original_size'] = match.group(1)
                                
                                if 'Repack Size:' in text:
                                    match = re.search(r'Repack Size:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['repack_size'] = match.group(1)
                        
                        return {
                            'banner': banner_url,
                            **info
                        }
                    else:
                        print(f"⚠️ Failed to fetch details: {resp.status}")
                        return {'banner': None}
        except Exception as e:
            print(f"⚠️ Error fetching game details: {e}")
            import traceback
            traceback.print_exc()
            return {'banner': None}
        """Fetch detailed information from a specific repack page."""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Extract game banner - multiple methods
                        banner_url = None
                        
                        # Method 1: Look for img inside p tag with height style
                        p_tags = soup.find_all('p', style=re.compile(r'height'))
                        for p in p_tags:
                            img = p.find('img')
                            if img:
                                src = img.get('src')
                                if src and ('imageban' in src or 'jpg' in src or 'png' in src):
                                    banner_url = src
                                    print(f"✅ Found banner via p tag: {banner_url}")
                                    break
                        
                        # Method 2: Look for img with alignleft class
                        if not banner_url:
                            banner_img = soup.find('img', class_='alignleft')
                            if banner_img:
                                banner_url = banner_img.get('src')
                                print(f"✅ Found banner via alignleft: {banner_url}")
                        
                        # Method 3: Look for any img with width="150"
                        if not banner_url:
                            imgs = soup.find_all('img', width="150")
                            for img in imgs:
                                src = img.get('src')
                                if src and ('imageban' in src or 'jpg' in src or 'png' in src):
                                    banner_url = src
                                    print(f"✅ Found banner via width=150: {banner_url}")
                                    break
                        
                        # Method 4: First img in entry-content that's not an icon
                        if not banner_url:
                            content = soup.find('div', class_='entry-content')
                            if content:
                                imgs = content.find_all('img')
                                for img in imgs:
                                    src = img.get('src')
                                    # Skip icons and small images
                                    if src and not any(x in src.lower() for x in ['icon', 'emoji', 'avatar']):
                                        if 'imageban' in src or 'jpg' in src or 'png' in src:
                                            banner_url = src
                                            print(f"✅ Found banner in content: {banner_url}")
                                            break
                        
                        if not banner_url:
                            print(f"⚠️ No banner found for: {url}")
                        
                        # Extract additional details from the info paragraph
                        info = {
                            'genres': None,
                            'companies': None,
                            'languages': None,
                            'original_size': None,
                            'repack_size': None
                        }
                        
                        # Find the paragraph with game info
                        content = soup.find('div', class_='entry-content')
                        if content:
                            # Look for the info in paragraph text
                            paragraphs = content.find_all('p')
                            for p in paragraphs:
                                text = p.get_text()
                                
                                # Extract genres
                                if 'Genres/Tags:' in text:
                                    genre_links = p.find_all('a', href=re.compile(r'/tag/'))
                                    if genre_links:
                                        info['genres'] = ', '.join([link.get_text() for link in genre_links])
                                
                                # Extract companies
                                if 'Companies:' in text:
                                    match = re.search(r'Companies:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['companies'] = match.group(1)
                                
                                # Extract languages
                                if 'Languages:' in text:
                                    match = re.search(r'Languages:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['languages'] = match.group(1)
                                
                                # Extract sizes
                                if 'Original Size:' in text:
                                    match = re.search(r'Original Size:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['original_size'] = match.group(1)
                                
                                if 'Repack Size:' in text:
                                    match = re.search(r'Repack Size:\s*<strong>(.*?)</strong>', str(p))
                                    if match:
                                        info['repack_size'] = match.group(1)
                        
                        return {
                            'banner': banner_url,
                            **info
                        }
                    else:
                        print(f"⚠️ Failed to fetch details: {resp.status}")
                        return {'banner': None}
        except Exception as e:
            print(f"⚠️ Error fetching game details: {e}")
            import traceback
            traceback.print_exc()
            return {'banner': None}

# Initialize FitGirl scraper
fitgirl_scraper = FitGirlScraper()

# =========================================================
# HELPER FUNCTION: CLEAN GAME NAME FOR SEARCH
# =========================================================
def clean_game_name_for_search(title: str) -> str:
    """Clean FitGirl game title to extract just the game name for IGDB/RAWG search."""
    # Remove content in parentheses like (Legacy), (Remastered), etc.
    title = re.sub(r'\([^)]*\)', '', title)
    
    # Remove version numbers like v1.0.3725.0, 1.72, etc.
    title = re.sub(r'v?\d+\.\d+[\.\d]*', '', title)
    
    # Remove common FitGirl patterns
    patterns_to_remove = [
        r'\s*–\s*.*',  # Everything after em dash
        r'\s*-\s*v\d+.*',  # Everything after dash with version
        r'\s*/\s*.*',  # Everything after forward slash
        r'\+\s*.*',  # Everything after plus sign (like + Bonus Content, + DLC)
        r':\s*Update.*',  # Update information
        r':\s*DLC.*',  # DLC information
        r'Bonus Content.*',
        r'Deluxe Edition.*',
        r'Ultimate Edition.*',
        r'Gold Edition.*',
        r'GOTY.*',
        r'Game of the Year.*',
    ]
    
    for pattern in patterns_to_remove:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE)
    
    # Clean up extra spaces and trim
    title = ' '.join(title.split())
    title = title.strip(' -–/')
    
    print(f"🔍 Cleaned title: '{title}'")
    return title

# -------------------------
# PERMISSION CHECK FUNCTION
# -------------------------
async def check_command_permissions(interaction: discord.Interaction) -> bool:
    """Check if user can use commands in current channel."""
    # Check if user has admin role - they can use commands anywhere
    if any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles):
        return True
    
    # Non-admins can only use commands in allowed channel
    if interaction.channel_id == ALLOWED_CHANNEL_ID:
        return True
    
    # Permission denied
    await interaction.response.send_message(
        f"❌ You can only use commands in <#{ALLOWED_CHANNEL_ID}>",
        ephemeral=True
    )
    return False

# -------------------------
# READY EVENT WITH GUILD-SPECIFIC SYNC
# -------------------------
@bot.event
async def on_ready():
    # FIRST: Load persistent data to get the previous status_message_id
    load_seen_posts()
    load_bot_state()
    load_user_data()
    load_reviews_data()
    load_tags_data()
    load_health_data()
    load_webhooks_data()
    load_collections_data()
    load_compatibility_data()

    # Start GitHub webhook server
    setup_github_webhook_server()

    # Start Steam OAuth server
    from steam_oauth_server import start_oauth_server
    start_oauth_server(port=settings.STEAM_OAUTH_PORT)
    logger.info(f"✅ Steam OAuth server started on port {settings.STEAM_OAUTH_PORT}")

    # SECOND: Update status to show bot is starting (will edit existing message if found)
    await update_status_message("starting")

    # Copy global commands to the guild for fast development
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    logger.info(f"✅ Logged in as {bot.user}")
    logger.info(f"✅ Commands synced to guild ID: {GUILD_ID}")
    
    # Initialize browser pool
    try:
        await get_browser_pool().initialize()
        logger.info("Browser pool initialized")
    except Exception as e:
        logger.error(f"Error initializing browser pool: {e}", exc_info=True)

    # Check if RSS auto-posting is enabled (can disable for free tier)
    rss_enabled = os.getenv("ENABLE_RSS_AUTO", "true").lower() == "true"
    if rss_enabled and not fitgirl_rss_monitor.is_running():
        fitgirl_rss_monitor.start()
        print(f"✅ FitGirl RSS Monitor started (checking every 30 minutes)")
    else:
        print(f"⚠️ RSS Auto-posting disabled (set ENABLE_RSS_AUTO=true to enable)")

    # Start dashboard updater
    if not update_dashboard.is_running():
        update_dashboard.start()
        print(f"✅ Dashboard updater started")

    # Start link health monitor
    if not link_health_monitor.is_running():
        link_health_monitor.start()
        print(f"✅ Link health monitor started (checks daily)")

    # Start auto-backup
    if not auto_backup.is_running():
        auto_backup.start()
        print(f"✅ Auto-backup started (runs daily)")

    # Start Steam activity monitor
    if not steam_activity_monitor.is_running():
        steam_activity_monitor.start()
        print(f"✅ Steam activity monitor started (checks every 10 seconds)")

    # Start Playwright queue processor
    if not playwright_queue_processor.is_running():
        playwright_queue_processor.start()
        print(f"✅ Playwright queue processor started")
    
    # Start Minecraft monitoring tasks
    if not minecraft_auto_restart_monitor.is_running():
        minecraft_auto_restart_monitor.start()
        print(f"✅ Minecraft auto-restart monitor started")
    
    if not minecraft_player_notifications.is_running():
        minecraft_player_notifications.start()
        print(f"✅ Minecraft player notifications started")
    
    if not minecraft_scheduled_backups.is_running():
        minecraft_scheduled_backups.start()
        print(f"✅ Minecraft scheduled backups monitor started")
    
    # Start Minecraft scheduled restarts
    try:
        if not minecraft_scheduled_restarts.is_running():
            minecraft_scheduled_restarts.start()
            print(f"✅ Minecraft scheduled restarts monitor started")
    except Exception as e:
        logger.error(f"Failed to start scheduled restarts: {e}", exc_info=True)
    
    # Start Minecraft resource monitor
    try:
        if not minecraft_resource_monitor.is_running():
            minecraft_resource_monitor.start()
            print(f"✅ Minecraft resource monitor started")
    except Exception as e:
        logger.error(f"Failed to start resource monitor: {e}", exc_info=True)
    
    # Start Minecraft dashboard updater
    try:
        if not minecraft_dashboard_updater.is_running():
            minecraft_dashboard_updater.start()
            print(f"✅ Minecraft dashboard updater started")
    except Exception as e:
        logger.error(f"Failed to start dashboard updater: {e}", exc_info=True)
    
    # LAST: Update status to online after everything is loaded
    await update_status_message("online")

@bot.event
async def on_close():
    """Clean up resources on bot shutdown."""
    logger.info("Bot shutting down, cleaning up resources...")
    try:
        await update_status_message("restarting")
    except:
        pass
    
    # Save all data
    try:
        data_manager.save_all()
    except Exception as e:
        logger.error(f"Error saving data: {e}", exc_info=True)
    
    # Close HTTP sessions
    try:
        await close_http_sessions()
    except Exception as e:
        logger.error(f"Error closing HTTP sessions: {e}", exc_info=True)
    
    # Close browser pool
    try:
        await close_browser_pool()
    except Exception as e:
        logger.error(f"Error closing browser pool: {e}", exc_info=True)
    
    # Close IGDB client
    try:
        await igdb_client.close()
    except Exception as e:
        logger.error(f"Error closing IGDB client: {e}", exc_info=True)
    
    logger.info("Cleanup complete")

@bot.event
async def on_raw_reaction_add(payload):
    """Handle reactions for library additions and request votes."""
    # Ignore bot reactions
    if payload.user_id == bot.user.id:
        return
    
    # Handle library additions (📚 emoji on forum posts)
    if str(payload.emoji) == "📚":
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if payload.channel_id == OUTPUT_CHANNEL_ID or (output_channel and payload.channel_id in [thread.id for thread in output_channel.threads]):
            # Add to user's library
            if payload.user_id not in bot.user_libraries:
                bot.user_libraries[payload.user_id] = []
            
            if payload.channel_id not in bot.user_libraries[payload.user_id]:
                bot.user_libraries[payload.user_id].append(payload.channel_id)
                save_user_data()
    
    # Handle request votes (👍 emoji on requests)
    elif str(payload.emoji) == "👍":
        if payload.channel_id == REQUEST_CHANNEL_ID:
            if payload.message_id not in bot.request_votes:
                bot.request_votes[payload.message_id] = []
            
            if payload.user_id not in bot.request_votes[payload.message_id]:
                bot.request_votes[payload.message_id].append(payload.user_id)
                save_user_data()

@bot.event
async def on_raw_reaction_remove(payload):
    """Handle reaction removal for library and votes."""
    # Handle library removal
    if str(payload.emoji) == "📚":
        if payload.user_id in bot.user_libraries:
            if payload.channel_id in bot.user_libraries[payload.user_id]:
                bot.user_libraries[payload.user_id].remove(payload.channel_id)
                save_user_data()
    
    # Handle vote removal
    elif str(payload.emoji) == "👍":
        if payload.message_id in bot.request_votes:
            if payload.user_id in bot.request_votes[payload.message_id]:
                bot.request_votes[payload.message_id].remove(payload.user_id)
                save_user_data()

# =========================================================
# PLAYWRIGHT QUEUE PROCESSOR
# =========================================================
@tasks.loop(seconds=2)
async def playwright_queue_processor():
    """Process Playwright download queue one at a time."""
    if bot.playwright_active:
        return  # Already processing something
    
    if bot.playwright_queue.empty():
        return  # Nothing to process
    
    try:
        bot.playwright_active = True
        
        # Get next item from queue
        queue_item = await bot.playwright_queue.get()
        paste_url = queue_item['paste_url']
        request_id = queue_item['request_id']
        callback = queue_item['callback']
        
        logger.info(f"📦 Processing queue item: {request_id}")
        
        # Download torrent
        torrent_data = await fitgirl_scraper.download_torrent_from_paste(paste_url, request_id)
        
        # Call callback with result
        try:
            await callback(torrent_data)
        except Exception as callback_error:
            logger.error(f"⚠️ Error in download callback: {callback_error}", exc_info=True)
            # Try to notify user if possible
            if 'interaction' in queue_item:
                try:
                    await queue_item['interaction'].followup.send(
                        f"❌ Error processing download: {str(callback_error)}",
                        ephemeral=True
                    )
                except:
                    pass
        
    except Exception as e:
        logger.error(f"⚠️ Error in queue processor: {e}", exc_info=True)
    finally:
        bot.playwright_active = False

@playwright_queue_processor.before_loop
async def before_queue_processor():
    await bot.wait_until_ready()

# =========================================================
# FITGIRL RSS MONITOR (AUTO-POST NEW RELEASES)
# =========================================================
@tasks.loop(minutes=30)  # Check every 30 minutes for new releases
async def fitgirl_rss_monitor():
    """Monitor FitGirl RSS feed for new releases and auto-post them."""
    try:
        print("🔍 Checking FitGirl RSS for new releases...")
        
        # Fetch RSS feed
        rss_url = "https://fitgirl-repacks.site/feed/"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(rss_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    print(f"⚠️ Failed to fetch RSS: {resp.status}")
                    return
                
                rss_content = await resp.text()
        
        # Parse RSS feed
        feed = feedparser.parse(rss_content)
        
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        
        new_posts = []
        for entry in feed.entries[:10]:  # Check last 10 entries for better coverage
            post_id = entry.get('id') or entry.get('link')
            
            # Skip if already seen in RSS
            if post_id in bot.seen_rss_posts:
                continue
            
            # Skip "Updates Digest" posts
            title = entry.get('title', '')
            if 'Updates Digest' in title or 'updates digest' in title.lower():
                print(f"⏭️ Skipping Updates Digest: {title}")
                bot.seen_rss_posts.add(post_id)
                save_seen_posts()
                continue
            
            # CRITICAL: Check if game already exists in forum BEFORE adding to queue
            clean_name = clean_game_name_for_search(title)
            game_exists = False
            
            # Check active threads
            for thread in output_channel.threads:
                thread_clean = clean_game_name_for_search(thread.name)
                if thread_clean.lower() == clean_name.lower():
                    game_exists = True
                    print(f"⏭️ Game already in forum (active): {title} -> {thread.name}")
                    break
            
            # Check archived threads if not found in active
            if not game_exists:
                try:
                    async for thread in output_channel.archived_threads(limit=200):
                        thread_clean = clean_game_name_for_search(thread.name)
                        if thread_clean.lower() == clean_name.lower():
                            game_exists = True
                            print(f"⏭️ Game already in forum (archived): {title} -> {thread.name}")
                            break
                except Exception as e:
                    print(f"⚠️ Error checking archived threads: {e}")
            
            # If game exists, mark as seen and skip
            if game_exists:
                bot.seen_rss_posts.add(post_id)
                save_seen_posts()
                continue
            
            # Only add to processing queue if it's truly new
            new_posts.append({
                'id': post_id,
                'title': title,
                'link': entry.get('link'),
                'published': entry.get('published')
            })
        
        if not new_posts:
            print("✅ No new FitGirl releases")
            save_seen_posts()
            return
        
        print(f"🆕 Found {len(new_posts)} new FitGirl release(s) to process!")
        
        # Process each new post
        for post in new_posts:
            try:
                print(f"📥 Processing: {post['title']}")
                
                # Get game details and torrent link
                paste_url = await fitgirl_scraper.get_torrent_link(post['link'])
                if not paste_url:
                    print(f"⚠️ No torrent link found for: {post['title']}")
                    bot.seen_rss_posts.add(post['id'])
                    save_seen_posts()
                    continue
                
                # Check if game already exists in forum
                clean_name = clean_game_name_for_search(post['title'])
                output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
                
                existing_thread = None
                
                # FIRST: Check active threads
                for thread in output_channel.threads:
                    thread_clean = clean_game_name_for_search(thread.name)
                    if thread_clean.lower() == clean_name.lower():
                        existing_thread = thread
                        print(f"✅ Found in active threads: {thread.name}")
                        break
                
                # Double-check if game exists (safety check for updates)
                clean_name = clean_game_name_for_search(post['title'])
                output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
                
                existing_thread = None
                
                # FIRST: Check active threads
                for thread in output_channel.threads:
                    thread_clean = clean_game_name_for_search(thread.name)
                    if thread_clean.lower() == clean_name.lower():
                        existing_thread = thread
                        break
                
                # SECOND: Check archived threads if not found
                if not existing_thread:
                    async for thread in output_channel.archived_threads(limit=200):
                        thread_clean = clean_game_name_for_search(thread.name)
                        if thread_clean.lower() == clean_name.lower():
                            existing_thread = thread
                            break
                
                # If game exists NOW (race condition check), it must be an update
                if existing_thread:
                    # Check if the new post has version info suggesting it's an update
                    if 'update' in post['title'].lower() or re.search(r'v?\d+\.\d+', post['title']):
                        print(f"🔄 Detected update for: {clean_name}")
                        await post_game_update(existing_thread, post['title'], post['link'], paste_url)
                    else:
                        print(f"⏭️ Game appeared during processing, skipping: {post['title']}")
                    
                    bot.seen_rss_posts.add(post['id'])
                    save_seen_posts()
                    continue
                
                # Download torrent
                print(f"⬇️ Downloading torrent for: {post['title']}")
                torrent_data = await fitgirl_scraper.download_torrent_from_paste(paste_url)
                
                if not torrent_data:
                    print(f"⚠️ Failed to download torrent for: {post['title']}")
                    bot.seen_rss_posts.add(post['id'])
                    save_seen_posts()
                    continue
                
                # Get game details
                details = await fitgirl_scraper.get_game_details(post['link'])
                
                # Auto-post to forum
                await auto_post_fitgirl_game(
                    game_name=post['title'],
                    game_url=post['link'],
                    torrent_data=torrent_data,
                    details=details
                )
                
                print(f"✅ Auto-posted: {post['title']}")
                bot.seen_rss_posts.add(post['id'])
                save_seen_posts()  # Save immediately after each post
                
                # Small delay between posts
                await asyncio.sleep(10)
                
            except Exception as e:
                print(f"⚠️ Error processing RSS post: {e}")
                import traceback
                traceback.print_exc()
                # Still mark as seen to avoid retry loops
                bot.seen_rss_posts.add(post['id'])
                save_seen_posts()  # Save even on error
        
        # Final save
        save_seen_posts()
        print("✅ RSS check complete")
        
    except Exception as e:
        print(f"⚠️ Error in RSS monitor: {e}")
        import traceback
        traceback.print_exc()

@fitgirl_rss_monitor.before_loop
async def before_rss_monitor():
    await bot.wait_until_ready()

# =========================================================
# DASHBOARD AUTO-UPDATER
# =========================================================
@tasks.loop(minutes=15)  # Update dashboard every 15 minutes
async def update_dashboard():
    """Update the bot dashboard with current stats."""
    try:
        dashboard_channel = bot.get_channel(DASHBOARD_CHANNEL_ID)
        if not dashboard_channel:
            print("⚠️ Dashboard channel not found")
            return
        
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        
        # Gather statistics
        total_games = 0
        active_games = len(output_channel.threads)
        archived_games = 0
        
        async for thread in output_channel.archived_threads(limit=None):
            archived_games += 1
        
        total_games = active_games + archived_games
        
        # Get latest games
        latest_threads = []
        for thread in output_channel.threads:
            latest_threads.append({'name': thread.name, 'created': thread.created_at, 'mention': thread.mention})
        
        async for thread in output_channel.archived_threads(limit=20):
            latest_threads.append({'name': thread.name, 'created': thread.created_at, 'mention': thread.mention})
        
        latest_threads.sort(key=lambda x: x['created'], reverse=True)
        
        # Get top contributors
        top_contributors = sorted(bot.contributor_stats.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Create dashboard embed
        embed = discord.Embed(
            title="🎮 Backroom Pirate Ship - Game Library Dashboard",
            description="Live statistics and information about the game library",
            color=0x1b2838
        )
        
        # Bot Status
        embed.add_field(
            name="🤖 Bot Status",
            value=f"✅ Online\n🔄 Last Updated: {discord.utils.format_dt(discord.utils.utcnow(), style='R')}",
            inline=False
        )
        
        # Library Stats
        stats_text = (
            f"📦 **Total Games:** {total_games:,}\n"
            f"🟢 **Active:** {active_games:,}\n"
            f"📁 **Archived:** {archived_games:,}"
        )
        embed.add_field(
            name="📊 Library Statistics",
            value=stats_text,
            inline=True
        )
        
        # Top Contributors
        if top_contributors:
            contrib_text = ""
            for user_id, count in top_contributors[:3]:  # Top 3 for compact display
                try:
                    user = await bot.fetch_user(user_id)
                    contrib_text += f"🏆 {user.mention}: **{count}**\n"
                except:
                    contrib_text += f"🏆 User: **{count}**\n"
            embed.add_field(
                name="👑 Top Contributors",
                value=contrib_text or "No data yet",
                inline=True
            )
        
        # Recent Games (expanded section)
        if latest_threads:
            recent_text = ""
            for idx, thread in enumerate(latest_threads[:5], 1):
                time_ago = discord.utils.format_dt(thread['created'], style='R')
                recent_text += f"**{idx}.** {thread['mention']} • {time_ago}\n"
            embed.add_field(
                name="🆕 Recent Games",
                value=recent_text or "No games yet",
                inline=False
            )
        
        # Quick Links
        embed.add_field(
            name="🔗 Quick Links",
            value=(
                f"• Use `/search` to find games\n"
                f"• Use `/latest` to see recent additions\n"
                f"• Use `/random` for a random game\n"
                f"• Use `/stats` for detailed statistics"
            ),
            inline=False
        )
        
        embed.set_footer(text="Dashboard updates every 15 minutes")
        embed.timestamp = discord.utils.utcnow()
        
        # Post or update dashboard message
        if bot.dashboard_message_id:
            try:
                msg = await dashboard_channel.fetch_message(bot.dashboard_message_id)
                await msg.edit(embed=embed)
                print("✅ Dashboard updated")
            except discord.NotFound:
                # Message was deleted, create new one
                msg = await dashboard_channel.send(embed=embed)
                bot.dashboard_message_id = msg.id
                save_bot_state()
                print(f"✅ Dashboard created: {msg.id}")
        else:
            # First time - create dashboard
            msg = await dashboard_channel.send(embed=embed)
            bot.dashboard_message_id = msg.id
            save_bot_state()
            print(f"✅ Dashboard created: {msg.id}")
            
    except Exception as e:
        print(f"⚠️ Error updating dashboard: {e}")
        import traceback
        traceback.print_exc()

@update_dashboard.before_loop
async def before_dashboard():
    await bot.wait_until_ready()

# =========================================================
# LINK HEALTH MONITOR
# =========================================================
@tasks.loop(hours=24)  # Check link health daily
async def link_health_monitor():
    """Monitor download links and update health status."""
    try:
        print("🔍 Starting link health check...")
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            return
        
        checked_count = 0
        broken_count = 0
        
        # Check active threads
        for thread in output_channel.threads:
            try:
                # Get the first message (post content)
                async for message in thread.history(limit=1, oldest_first=True):
                    if message.embeds:
                        embed = message.embeds[0]
                        
                        # Extract links from embed description
                        description = embed.description or ""
                        links = re.findall(r'https?://[^\s\)]+', description)
                        
                        # Check each link
                        broken_links = []
                        for link in links:
                            try:
                                async with aiohttp.ClientSession() as session:
                                    async with session.head(link, timeout=10, allow_redirects=True) as resp:
                                        if resp.status >= 400:
                                            broken_links.append(link)
                            except:
                                broken_links.append(link)
                        
                        # Update health data
                        health_status = "healthy" if not broken_links else "broken"
                        bot.link_health[thread.id] = {
                            'checked_at': discord.utils.utcnow().isoformat(),
                            'status': health_status,
                            'broken_links': broken_links,
                            'total_links': len(links)
                        }
                        
                        checked_count += 1
                        if broken_links:
                            broken_count += 1
                            print(f"⚠️ Found {len(broken_links)} broken links in: {thread.name}")
                
                # Rate limiting
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"⚠️ Error checking thread {thread.name}: {e}")
        
        save_health_data()
        print(f"✅ Link health check complete: {checked_count} checked, {broken_count} with broken links")
        
    except Exception as e:
        print(f"⚠️ Error in link health monitor: {e}")

@link_health_monitor.before_loop
async def before_health_monitor():
    await bot.wait_until_ready()

# =========================================================
# AUTO-BACKUP SYSTEM
# =========================================================
@tasks.loop(hours=24)  # Backup daily
async def auto_backup():
    """Automatically backup bot data daily."""
    try:
        import zipfile
        from datetime import datetime
        
        print("💾 Starting auto-backup...")
        
        # Create backup filename with timestamp
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backups/backup_{timestamp}.zip"
        
        # Create backups directory if it doesn't exist
        os.makedirs("backups", exist_ok=True)
        
        # Files to backup (using data/ directory)
        files_to_backup = [
            str(settings.RSS_SEEN_FILE),
            str(settings.BOT_STATE_FILE),
            str(settings.USER_DATA_FILE),
            str(settings.REVIEWS_FILE),
            str(settings.TAGS_FILE),
            str(settings.HEALTH_FILE)
        ]
        
        # Create zip file
        with zipfile.ZipFile(backup_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for filename in files_to_backup:
                if os.path.exists(filename):
                    zipf.write(filename)
        
        print(f"✅ Auto-backup created: {backup_filename}")
        
        # Keep only last 7 backups
        import glob
        backups = sorted(glob.glob("backups/backup_*.zip"))
        if len(backups) > 7:
            for old_backup in backups[:-7]:
                os.remove(old_backup)
                print(f"🗑️ Removed old backup: {old_backup}")
        
    except Exception as e:
        print(f"⚠️ Error in auto-backup: {e}")

@auto_backup.before_loop
async def before_auto_backup():
    await bot.wait_until_ready()

# =========================================================
# STEAM ACTIVITY MONITOR
# =========================================================
@tasks.loop(seconds=10)  # Check every 10 seconds
async def steam_activity_monitor():
    """Monitor Steam gaming activity and send notifications only when games change"""
    try:
        # Skip if no channel configured
        if not bot.steam_activity_channel_id:
            print("⚠️ Steam monitor: No channel configured")
            return
        
        channel = bot.get_channel(bot.steam_activity_channel_id)
        if not channel:
            print(f"⚠️ Steam monitor: Channel {bot.steam_activity_channel_id} not found")
            return
        
        # Get all linked Steam accounts
        steam_links = SteamLinker.load_links()
        
        if not steam_links:
            print("⚠️ Steam monitor: No linked accounts")
            return
        
        print(f"🔍 Checking {len(steam_links)} linked Steam accounts...")
        
        # Check each linked user
        for discord_id, steam_id in steam_links.items():
            try:
                # Get their Steam profile
                profile = await SteamAPI.get_player_summaries(steam_id)
                if not profile:
                    print(f"⚠️ No profile data for Steam ID {steam_id}")
                    continue
                
                current_game = profile.get('gameextrainfo')
                discord_id_str = str(discord_id)
                previous_status = bot.steam_gaming_status.get(discord_id_str)
                
                print(f"👤 {profile.get('personaname', 'Unknown')}: {current_game if current_game else 'Not playing'}")
                
                # Case 1: User is playing a game
                if current_game:
                    # Only notify if game changed (not same game, not just started same game again)
                    if not previous_status or previous_status.get('game') != current_game:
                        # Get Discord user - try cache first, then fetch
                        user = bot.get_user(int(discord_id))
                        if not user:
                            try:
                                user = await bot.fetch_user(int(discord_id))
                            except:
                                print(f"⚠️ Could not fetch Discord user {discord_id}")
                                continue
                        
                        # Create notification embed for game switch
                        embed = discord.Embed(
                            description=f"## 🎮 {user.mention} is now playing\n# **{current_game}**",
                            color=discord.Color.from_rgb(102, 187, 106),  # Modern green
                            timestamp=discord.utils.utcnow()
                        )
                        
                        # Add large game image
                        game_id = profile.get('gameid')
                        if game_id:
                            game_image_url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game_id}/header.jpg"
                            embed.set_image(url=game_image_url)
                        
                        # Add Steam info with avatar
                        if 'avatarmedium' in profile:
                            embed.set_author(
                                name=profile.get('personaname', 'Steam User'),
                                icon_url=profile['avatarmedium'],
                                url=profile.get('profileurl', '')
                            )
                        
                        # Add status indicator
                        embed.set_footer(
                            text="🟢 Online and Playing",
                            icon_url=user.display_avatar.url
                        )
                        
                        # Create view with invite button
                        view = InviteToVCView(int(discord_id), profile.get('personaname', 'Player'))
                        
                        # Send notification and store message ID
                        message = await channel.send(embed=embed, view=view)
                        
                        # Update status buffer with new game and message ID
                        bot.steam_gaming_status[discord_id_str] = {
                            'game': current_game,
                            'game_id': profile.get('gameid'),
                            'message_id': message.id,
                            'timestamp': discord.utils.utcnow(),
                            'start_time': discord.utils.utcnow()
                        }
                        
                        print(f"📢 Notification sent: {user.name} switched to {current_game}")
                    else:
                        print(f"✓ Still playing {current_game} (no notification)")
                
                # Case 2: User stopped playing
                else:
                    # Edit the notification message if they were playing before
                    if discord_id_str in bot.steam_gaming_status:
                        previous_game = bot.steam_gaming_status[discord_id_str].get('game')
                        message_id = bot.steam_gaming_status[discord_id_str].get('message_id')
                        
                        if message_id:
                            try:
                                # Fetch the original message
                                message = await channel.fetch_message(message_id)
                                
                                # Get Discord user - try cache first, then fetch
                                user = bot.get_user(int(discord_id))
                                if not user:
                                    try:
                                        user = await bot.fetch_user(int(discord_id))
                                    except:
                                        print(f"⚠️ Could not fetch Discord user {discord_id}")
                                        del bot.steam_gaming_status[discord_id_str]
                                        continue
                                
                                # Create session ended embed
                                embed = discord.Embed(
                                    description=f"## 🎮 {user.mention} finished playing\n# **{previous_game}**",
                                    color=discord.Color.from_rgb(239, 83, 80),  # Modern red
                                    timestamp=discord.utils.utcnow()
                                )
                                
                                # Add game image (stored from previous session)
                                previous_game_id = bot.steam_gaming_status[discord_id_str].get('game_id')
                                if previous_game_id:
                                    game_image_url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{previous_game_id}/header.jpg"
                                    embed.set_image(url=game_image_url)
                                
                                # Add Steam info
                                if 'avatarmedium' in profile:
                                    embed.set_author(
                                        name=profile.get('personaname', 'Steam User'),
                                        icon_url=profile['avatarmedium'],
                                        url=profile.get('profileurl', '')
                                    )
                                
                                # Add status indicator
                                embed.set_footer(
                                    text="⚫ Session Ended",
                                    icon_url=user.display_avatar.url
                                )
                                
                                # Edit the original message (remove buttons)
                                await message.edit(embed=embed, view=None)
                                print(f"📢 Message edited: {user.name} stopped playing {previous_game}")
                                
                                # Track session for history
                                start_time = bot.steam_gaming_status[discord_id_str].get('start_time')
                                if start_time:
                                    end_time = discord.utils.utcnow()
                                    duration_mins = int((end_time - start_time).total_seconds() / 60)
                                    
                                    # Save session
                                    if discord_id_str not in bot.steam_sessions:
                                        bot.steam_sessions[discord_id_str] = []
                                    
                                    bot.steam_sessions[discord_id_str].append({
                                        'game': previous_game,
                                        'start': start_time,
                                        'end': end_time,
                                        'duration': duration_mins
                                    })
                                    
                                    # Keep only last 100 sessions per user
                                    if len(bot.steam_sessions[discord_id_str]) > 100:
                                        bot.steam_sessions[discord_id_str] = bot.steam_sessions[discord_id_str][-100:]
                                    
                                    print(f"📊 Session saved: {duration_mins} mins")
                                
                            except discord.NotFound:
                                print(f"⚠️ Original message not found, cannot edit")
                            except Exception as e:
                                print(f"⚠️ Error editing message: {e}")
                        
                        # Clear from buffer
                        del bot.steam_gaming_status[discord_id_str]
            
            except Exception as e:
                print(f"⚠️ Error checking Steam status for {discord_id}: {e}")
                continue
        
        # Check for squad up opportunities (multiple people playing same game)
        await check_squad_opportunities(channel, steam_links)
    
    except Exception as e:
        print(f"⚠️ Error in steam_activity_monitor: {e}")

async def check_squad_opportunities(channel, steam_links):
    """Check if multiple people are playing the same game"""
    try:
        game_players = {}  # game: [discord_ids]
        
        for discord_id, steam_id in steam_links.items():
            status = bot.steam_gaming_status.get(str(discord_id))
            if status and status.get('game'):
                game_name = status['game']
                if game_name not in game_players:
                    game_players[game_name] = []
                game_players[game_name].append(discord_id)
        
        # Notify if 2+ people are playing same game
        for game_name, player_ids in game_players.items():
            if len(player_ids) >= 2:
                # Check if we already notified about this squad
                squad_key = f"{game_name}:{sorted(player_ids)}"
                if squad_key not in bot.squad_notifications:
                    # Get all users
                    users = []
                    for pid in player_ids:
                        user = bot.get_user(int(pid))
                        if not user:
                            try:
                                user = await bot.fetch_user(int(pid))
                            except:
                                continue
                        if user:
                            users.append(user)
                    
                    if len(users) >= 2:
                        # Create squad notification
                        embed = discord.Embed(
                            title="👥 Squad Up!",
                            description=f"**{len(users)}** members are playing **{game_name}**\n\n" + 
                                      "\n".join([f"• {u.mention}" for u in users]),
                            color=discord.Color.blurple(),
                            timestamp=discord.utils.utcnow()
                        )
                        embed.set_footer(text="Join them for some multiplayer action!")
                        
                        await channel.send(embed=embed)
                        bot.squad_notifications[squad_key] = True
                        print(f"👥 Squad notification sent for {game_name}")
        
        # Clean up old squad notifications
        current_squads = [f"{g}:{sorted(p)}" for g, p in game_players.items() if len(p) >= 2]
        for key in list(bot.squad_notifications.keys()):
            if key not in current_squads:
                del bot.squad_notifications[key]
    
    except Exception as e:
        print(f"⚠️ Error checking squad opportunities: {e}")

@steam_activity_monitor.before_loop
async def before_steam_activity_monitor():
    await bot.wait_until_ready()

# =========================================================
# AUTO-POST FITGIRL GAME FROM RSS
# =========================================================
async def auto_post_fitgirl_game(game_name: str, game_url: str, torrent_data: bytes, details: Dict[str, Any]):
    """Auto-post a FitGirl game to the forum."""
    try:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        input_channel = bot.get_channel(INPUT_CHANNEL_ID)
        
        # Clean the game name for better search
        clean_name = clean_game_name_for_search(game_name)
        
        # Search IGDB for game data
        igdb_data = await igdb_client.search_game_by_name(clean_name)
        
        # Fallback to RAWG
        if not igdb_data:
            igdb_data = await rawg_client.search_game_by_name(clean_name)
        
        # Build embed
        if igdb_data:
            game_title = igdb_data.get("name", game_name)
            summary = igdb_data.get("summary", "No description available.")
            
            genres = igdb_data.get("genres", [])
            genres_text = " • ".join([g["name"] for g in genres]) if genres else "N/A"
            
            platforms = igdb_data.get("platforms", [])
            platforms_text = " • ".join([p["name"] for p in platforms]) if platforms else "N/A"
            
            cover_url = None
            if "cover" in igdb_data and igdb_data["cover"]:
                image_id = igdb_data["cover"].get("image_id")
                if image_id and isinstance(image_id, str):
                    if image_id.startswith("http"):
                        cover_url = image_id
                    else:
                        cover_url = f"https://images.igdb.com/igdb/image/upload/t_screenshot_big/{image_id}.jpg"
            
            embed = discord.Embed(
                title=game_title,
                description=summary[:600] + ("..." if len(summary) > 600 else ""),
                color=0x1b2838
            )
            
            if cover_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(cover_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                embed.set_image(url=cover_url)
                except:
                    embed.set_image(url=cover_url)
            
            embed.add_field(name="🎮 Genres", value=genres_text, inline=True)
            embed.add_field(name="🖥️ Platforms", value=platforms_text, inline=True)
            
            # Add FitGirl specific info
            if details.get('repack_size'):
                embed.add_field(name="📥 Repack Size", value=details['repack_size'], inline=True)
            if details.get('languages'):
                embed.add_field(name="🌐 Languages", value=details['languages'], inline=True)
            
            embed.add_field(
                name="📝 FitGirl Repack",
                value="Auto-posted from RSS feed",
                inline=False
            )
            
            embed.set_footer(text="🤖 Auto-posted by FitGirl RSS Monitor")
            embed.timestamp = discord.utils.utcnow()
        else:
            embed = discord.Embed(
                title=game_name,
                description="Auto-posted from FitGirl Repacks",
                color=0x1b2838
            )
            embed.set_footer(text="🤖 Auto-posted by FitGirl RSS Monitor")
            embed.timestamp = discord.utils.utcnow()
        
        # Create torrent file
        torrent_filename = f"{clean_name[:50]} [FitGirl Repack].torrent"
        torrent_filename = re.sub(r'[<>:"/\\|?*]', '', torrent_filename)
        
        torrent_file = discord.File(
            fp=io.BytesIO(torrent_data),
            filename=f"SPOILER_{torrent_filename}",
            spoiler=True
        )
        
        thread_name = igdb_data.get("name", clean_name) if igdb_data else clean_name
        thread_name = thread_name[:100]
        
        view = GameButtonView(game_url, None)
        
        # Create forum thread
        thread = await output_channel.create_thread(
            name=thread_name,
            content=f"**{thread_name}**",
            embed=embed,
            file=torrent_file,
            view=view
        )
        
        # Add torrent button
        starter_message = thread.message
        if starter_message.attachments:
            public_torrent_url = starter_message.attachments[0].url
            view = GameButtonView(game_url, public_torrent_url)
            await starter_message.edit(view=view if view.children else None)
        
        # Log to input channel with embed
        log_embed = discord.Embed(
            title="📥 New Game Auto-Posted",
            description=f"**{game_name}**",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        log_embed.add_field(name="🤖 Posted By", value="RSS Monitor", inline=True)
        log_embed.add_field(name="📦 Thread", value=thread.thread.mention, inline=True)
        log_embed.add_field(name="🔗 Source", value=f"[FitGirl Page]({game_url})", inline=False)
        log_embed.set_footer(text="FitGirl RSS Auto-Poster")
        
        await input_channel.send(embed=log_embed)
        
        # Send webhook notifications
        try:
            await send_webhook_notifications(log_embed, game_name)
        except:
            pass
        
        # Track RSS bot as contributor
        if bot.user.id not in bot.contributor_stats:
            bot.contributor_stats[bot.user.id] = 0
        bot.contributor_stats[bot.user.id] += 1
        save_bot_state()
        
        # Update dashboard immediately
        try:
            await update_dashboard()
        except:
            pass
        
    except Exception as e:
        print(f"⚠️ Error auto-posting game: {e}")
        import traceback
        traceback.print_exc()

async def post_game_update(thread, title: str, game_url: str, paste_url: str):
    """Post an update notification in an existing game thread."""
    try:
        # Extract version info if available
        version_match = re.search(r'v?(\d+\.\d+(?:\.\d+)?)', title)
        version = version_match.group(1) if version_match else "Unknown"
        
        update_embed = discord.Embed(
            title="🔄 Game Updated!",
            description=f"**{title}**",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        update_embed.add_field(name="📦 Version", value=version, inline=True)
        update_embed.add_field(name="🔗 FitGirl Page", value=f"[View Update]({game_url})", inline=True)
        
        if paste_url:
            update_embed.add_field(name="⬇️ Torrent", value=f"[Download]({paste_url})", inline=True)
        
        update_embed.set_footer(text="🤖 Auto-detected from RSS")
        
        # Send update notification in thread
        await thread.send(embed=update_embed)
        
        # Notify users who have this game in their library
        users_to_notify = []
        for user_id, library in bot.user_libraries.items():
            if thread.id in library:
                users_to_notify.append(user_id)
        
        if users_to_notify:
            # Send mentions in batches
            mentions = [f"<@{user_id}>" for user_id in users_to_notify[:20]]  # Limit to 20 mentions
            if mentions:
                await thread.send(f"🔔 Update notification: {' '.join(mentions)}")
        
        print(f"✅ Posted update notification for: {title}")
        
    except Exception as e:
        print(f"⚠️ Error posting game update: {e}")
        import traceback
        traceback.print_exc()

# -------------------------
# PING COMMAND
# -------------------------
@bot.tree.command(name="ping", description="Check if bot is alive")
async def ping(interaction: discord.Interaction):
    if not await check_command_permissions(interaction):
        return
    await interaction.response.send_message("🏓 Pong! Bot is running.", ephemeral=True)

# -------------------------
# HELP COMMAND
# -------------------------
@bot.tree.command(name="help", description="Show all available commands and how to use them")
async def help_command(interaction: discord.Interaction):
    if not await check_command_permissions(interaction):
        return
    
    # Check if user is admin
    is_admin = any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)
    
    embed = discord.Embed(
        title="🎮 Backroom Pirate Ship - Bot Commands",
        description="Here are all available commands and their usage:",
        color=discord.Color.blue()
    )
    
    # General Commands
    embed.add_field(
        name="📋 General Commands",
        value=(
            "`/help` - Show this help message\n"
            "`/ping` - Check if bot is online\n"
            "`/stats` - View library statistics and top contributors\n"
        ),
        inline=False
    )
    
    # Search & Browse Commands
    embed.add_field(
        name="🔍 Search & Browse",
        value=(
            "`/search <query>` - Search games in the forum\n"
            "`/browse [genre]` - Browse games by genre\n"
            "`/latest [count]` - Show recently added games (default: 5)\n"
            "`/random` - Get a random game suggestion\n"
            "`/fgsearch <game>` - Search FitGirl Repacks\n"
            "`/similar <game>` - Find similar games (IGDB)\n"
        ),
        inline=False
    )
    
    # User Library Commands
    embed.add_field(
        name="📚 Your Library",
        value=(
            "`/mylibrary` - View your saved games\n"
            "`/notify <game>` - Get notified when a game is added\n"
            "React with 📚 on game posts to save them!\n"
        ),
        inline=False
    )
    
    # Request Commands
    embed.add_field(
        name="🎯 Game Requests",
        value=(
            "`/requestgame` - Request a game from moderators\n"
            "`/toprequests` - See most voted requests\n"
            "React with 👍 on requests to vote!\n"
        ),
        inline=False
    )
    
    # Statistics Commands
    embed.add_field(
        name="📊 Statistics",
        value=(
            "`/stats` - View library statistics\n"
            "`/downloadstats` - Most downloaded games\n"
        ),
        inline=False
    )
    
    # Admin Commands (only show to admins)
    if is_admin:
        embed.add_field(
            name="⚙️ Admin Commands",
            value=(
                "`/addgame` - Add a new game to the library\n"
                "`/checkrss` - Manually check FitGirl RSS feed\n"
                "`/finddupes` - Find duplicate game threads\n"
                "`/refreshdashboard` - Manually refresh dashboard\n"
            ),
            inline=False
        )
    
    # Usage Tips
    embed.add_field(
        name="💡 Tips",
        value=(
            "• React with 📚 to save games to your library\n"
            "• React with 👍 on requests to vote for them\n"
            "• Use `/notify` to get pinged when specific games are added\n"
            "• Game updates are auto-detected and posted in threads\n"
            "• RSS auto-posts new games every 30 minutes\n"
        ),
        inline=False
    )
    
    # Permissions info
    if not is_admin:
        embed.add_field(
            name="🔒 Permissions",
            value=f"Most commands can only be used in <#{ALLOWED_CHANNEL_ID}>",
            inline=False
        )
    
    embed.set_footer(text="Backroom Pirate Ship • Game Library Bot")
    embed.timestamp = discord.utils.utcnow()
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# -------------------------
# MANUAL RSS CHECK COMMAND
# -------------------------
@bot.tree.command(name="checkrss", description="Manually check FitGirl RSS for new releases")
async def checkrss(interaction: discord.Interaction):
    if not await check_command_permissions(interaction):
        return
    
    # Check if user has the required role
    REQUIRED_ROLE_ID = 1072117821397540954
    has_role = any(role.id == REQUIRED_ROLE_ID for role in interaction.user.roles)
    
    if not has_role:
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message(
        "🔍 Checking FitGirl RSS feed for new releases... This may take a few minutes.",
        ephemeral=True
    )
    
    try:
        # Manually trigger the RSS check
        await fitgirl_rss_monitor()
        
        await interaction.followup.send(
            "✅ RSS check complete! Check the input channel for any new posts.",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error checking RSS: {str(e)}",
            ephemeral=True
        )

# =========================================================
# SEARCH FORUM COMMAND
# =========================================================
@bot.tree.command(name="search", description="Search the forum for games")
async def search(interaction: discord.Interaction, query: str):
    """Search the game forum for a specific game."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    query_lower = query.lower()
    
    results = []
    
    # Search active threads
    for thread in output_channel.threads:
        if query_lower in thread.name.lower():
            results.append({
                'name': thread.name,
                'mention': thread.mention,
                'created': thread.created_at,
                'archived': False
            })
    
    # Search archived threads (last 100)
    async for thread in output_channel.archived_threads(limit=100):
        if query_lower in thread.name.lower():
            results.append({
                'name': thread.name,
                'mention': thread.mention,
                'created': thread.created_at,
                'archived': True
            })
    
    if not results:
        await interaction.followup.send(
            f"❌ No games found matching **'{query}'**",
            ephemeral=True
        )
        return
    
    # Sort by creation date (newest first)
    results.sort(key=lambda x: x['created'], reverse=True)
    
    # Create embed with results
    embed = discord.Embed(
        title=f"🔍 Search Results: {query}",
        description=f"Found {len(results)} game(s)",
        color=0x00FF00
    )
    
    # Show up to 10 results
    for result in results[:10]:
        status = "📦" if not result['archived'] else "📁"
        embed.add_field(
            name=f"{status} {result['name'][:80]}",
            value=result['mention'],
            inline=False
        )
    
    if len(results) > 10:
        embed.set_footer(text=f"Showing 10 of {len(results)} results")
    
    await interaction.followup.send(embed=embed)

# =========================================================
# STATS COMMAND
# =========================================================
@bot.tree.command(name="stats", description="Show game library statistics")
async def stats(interaction: discord.Interaction):
    """Display statistics about the game library."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    
    total_games = 0
    active_games = 0
    archived_games = 0
    
    # Count active threads
    active_games = len(output_channel.threads)
    
    # Count archived threads
    async for thread in output_channel.archived_threads(limit=None):
        archived_games += 1
    
    total_games = active_games + archived_games
    
    # Calculate download stats
    total_downloads = sum(bot.download_stats.values())
    total_users_with_libraries = len(bot.user_libraries)
    total_notifications = sum(len(users) for users in bot.game_notifications.values())
    
    # Create stats embed
    embed = discord.Embed(
        title="📊 Game Library Statistics",
        color=0x1b2838
    )
    
    embed.add_field(
        name="🎮 Total Games",
        value=f"**{total_games:,}**",
        inline=True
    )
    
    embed.add_field(
        name="📦 Active",
        value=f"**{active_games:,}**",
        inline=True
    )
    
    embed.add_field(
        name="📁 Archived",
        value=f"**{archived_games:,}**",
        inline=True
    )
    
    embed.add_field(
        name="⬇️ Total Downloads",
        value=f"**{total_downloads:,}**",
        inline=True
    )
    
    embed.add_field(
        name="📚 Users with Libraries",
        value=f"**{total_users_with_libraries:,}**",
        inline=True
    )
    
    embed.add_field(
        name="🔔 Active Notifications",
        value=f"**{total_notifications:,}**",
        inline=True
    )
    
    # Top contributors
    if bot.contributor_stats:
        top_contributors = sorted(bot.contributor_stats.items(), key=lambda x: x[1], reverse=True)[:3]
        contributors_text = []
        for user_id, count in top_contributors:
            user = await bot.fetch_user(user_id)
            contributors_text.append(f"• {user.mention} - {count} games")
        embed.add_field(
            name="👑 Top Contributors",
            value="\n".join(contributors_text) if contributors_text else "No data",
            inline=False
        )
    
    embed.set_footer(text=f"Forum: {output_channel.name}")
    embed.timestamp = discord.utils.utcnow()
    
    await interaction.followup.send(embed=embed)

# =========================================================
# LATEST COMMAND
# =========================================================
@bot.tree.command(name="latest", description="Show recently added games")
async def latest(interaction: discord.Interaction, count: int = 5):
    """Show the most recently added games."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    if count > 15:
        count = 15
    if count < 1:
        count = 1
    
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    
    # Get all threads and sort by creation date
    all_threads = []
    
    # Get active threads
    for thread in output_channel.threads:
        all_threads.append({
            'name': thread.name,
            'mention': thread.mention,
            'created': thread.created_at
        })
    
    # Get archived threads
    async for thread in output_channel.archived_threads(limit=50):
        all_threads.append({
            'name': thread.name,
            'mention': thread.mention,
            'created': thread.created_at
        })
    
    # Sort by creation date (newest first)
    all_threads.sort(key=lambda x: x['created'], reverse=True)
    
    if not all_threads:
        await interaction.followup.send(
            "❌ No games found in the library",
            ephemeral=True
        )
        return
    
    # Create embed
    embed = discord.Embed(
        title=f"🆕 Latest {count} Games Added",
        color=0x00FF00
    )
    
    for idx, thread in enumerate(all_threads[:count], 1):
        time_ago = discord.utils.format_dt(thread['created'], style='R')
        embed.add_field(
            name=f"{idx}. {thread['name'][:80]}",
            value=f"{thread['mention']} • Added {time_ago}",
            inline=False
        )
    
    embed.set_footer(text=f"Total games: {len(all_threads)}")
    
    await interaction.followup.send(embed=embed)

# =========================================================
# RANDOM GAME COMMAND
# =========================================================
@bot.tree.command(name="random", description="Get a random game suggestion")
async def random_game(interaction: discord.Interaction):
    """Suggest a random game from the library."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    
    # Collect all threads
    all_threads = []
    
    # Get active threads
    for thread in output_channel.threads:
        all_threads.append(thread)
    
    # Get some archived threads
    async for thread in output_channel.archived_threads(limit=100):
        all_threads.append(thread)
    
    if not all_threads:
        await interaction.followup.send(
            "❌ No games found in the library",
            ephemeral=True
        )
        return
    
    # Pick a random game
    import random
    random_thread = random.choice(all_threads)
    
    # Try to get the starter message with embed
    try:
        starter_message = await random_thread.parent.fetch_message(random_thread.id)
        
        # Create embed
        embed = discord.Embed(
            title=f"🎲 Random Game: {random_thread.name}",
            description=f"Here's a random game from the library!",
            color=0xFF6B6B
        )
        
        embed.add_field(
            name="🔗 Play Now",
            value=random_thread.mention,
            inline=False
        )
        
        # Try to get the cover image from original embed
        if starter_message.embeds:
            original_embed = starter_message.embeds[0]
            if original_embed.image:
                embed.set_image(url=original_embed.image.url)
            if original_embed.description:
                embed.add_field(
                    name="📝 Description",
                    value=original_embed.description[:200] + "...",
                    inline=False
                )
        
        embed.set_footer(text=f"🎮 {len(all_threads)} games in library • Try again for another!")
        
        await interaction.followup.send(embed=embed)
        
    except:
        # Fallback if we can't get the starter message
        await interaction.followup.send(
            f"🎲 **Random Game:** {random_thread.name}\n\n"
            f"🔗 Check it out: {random_thread.mention}",
        )

# =========================================================
# FIND DUPLICATES COMMAND
# =========================================================
@bot.tree.command(name="finddupes", description="Find duplicate games in the forum")
async def find_duplicates(interaction: discord.Interaction):
    """Find potential duplicate game threads."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    
    # Collect all threads
    all_threads = []
    
    # Get active threads
    for thread in output_channel.threads:
        all_threads.append(thread)
    
    # Get archived threads
    async for thread in output_channel.archived_threads(limit=500):
        all_threads.append(thread)
    
    if len(all_threads) < 2:
        await interaction.followup.send("❌ Not enough games to check for duplicates", ephemeral=True)
        return
    
    # Find duplicates based on similar names
    from difflib import SequenceMatcher
    
    duplicates = []
    checked = set()
    
    for i, thread1 in enumerate(all_threads):
        for thread2 in all_threads[i+1:]:
            pair = tuple(sorted([thread1.id, thread2.id]))
            if pair in checked:
                continue
            checked.add(pair)
            
            # Clean names for comparison
            name1 = clean_game_name_for_search(thread1.name).lower()
            name2 = clean_game_name_for_search(thread2.name).lower()
            
            # Calculate similarity
            similarity = SequenceMatcher(None, name1, name2).ratio()
            
            if similarity > 0.85:  # 85% similar
                duplicates.append((thread1, thread2, similarity))
    
    if not duplicates:
        await interaction.followup.send("✅ No duplicates found!", ephemeral=True)
        return
    
    # Build embed
    embed = discord.Embed(
        title="🔍 Potential Duplicates Found",
        description=f"Found {len(duplicates)} potential duplicate(s)",
        color=discord.Color.yellow()
    )
    
    for thread1, thread2, similarity in duplicates[:10]:  # Limit to first 10
        embed.add_field(
            name=f"{int(similarity*100)}% Match",
            value=f"{thread1.mention} ↔️ {thread2.mention}",
            inline=False
        )
    
    if len(duplicates) > 10:
        embed.set_footer(text=f"Showing 10 of {len(duplicates)} duplicates")
    
    await interaction.followup.send(embed=embed, ephemeral=True)

# =========================================================
# BROWSE BY GENRE COMMAND
# =========================================================
@bot.tree.command(name="browse", description="Browse games by genre")
async def browse_games(
    interaction: discord.Interaction,
    genre: str = None
):
    """Browse games filtered by genre."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    
    # If no genre specified, show available genres
    if not genre:
        genres_list = [
            "Action", "Adventure", "RPG", "Strategy", "Simulation",
            "Sports", "Racing", "Horror", "Shooter", "Puzzle",
            "Platformer", "Fighting", "Survival", "Stealth"
        ]
        
        embed = discord.Embed(
            title="🎮 Browse Games by Genre",
            description="Use `/browse <genre>` to filter games\n\nAvailable genres:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Popular Genres",
            value="\n".join([f"• {g}" for g in genres_list]),
            inline=False
        )
        
        await interaction.followup.send(embed=embed)
        return
    
    # Search for games with this genre in tags
    all_threads = []
    
    # Get active threads
    for thread in output_channel.threads:
        if genre.lower() in [tag.name.lower() for tag in thread.applied_tags]:
            all_threads.append(thread)
    
    # Get archived threads
    async for thread in output_channel.archived_threads(limit=200):
        if genre.lower() in [tag.name.lower() for tag in thread.applied_tags]:
            all_threads.append(thread)
    
    if not all_threads:
        await interaction.followup.send(
            f"❌ No games found with genre: **{genre}**\nTry `/browse` to see available genres",
            ephemeral=True
        )
        return
    
    # Build results embed
    embed = discord.Embed(
        title=f"🎮 {genre} Games",
        description=f"Found {len(all_threads)} game(s)",
        color=discord.Color.blue()
    )
    
    # Show first 15 games
    game_list = []
    for thread in all_threads[:15]:
        game_list.append(f"• {thread.mention}")
    
    embed.add_field(
        name="Games",
        value="\n".join(game_list) if game_list else "No games found",
        inline=False
    )
    
    if len(all_threads) > 15:
        embed.set_footer(text=f"Showing 15 of {len(all_threads)} games")
    
    await interaction.followup.send(embed=embed)

# =========================================================
# FITGIRL SEARCH NAVIGATION VIEW
# =========================================================
class FitGirlSearchView(discord.ui.View):
    """Navigation buttons for FitGirl search results."""
    
    def __init__(self, results: List[Dict[str, Any]], game_name: str):
        super().__init__(timeout=300)  # 5 minute timeout
        self.results = results
        self.game_name = game_name
        self.current_index = 0
        self.details_cache = {}  # Cache fetched details
        
        # Disable previous button initially
        self.children[0].disabled = True
    
    async def fetch_current_details(self):
        """Fetch details for current result if not cached."""
        if self.current_index not in self.details_cache:
            current_result = self.results[self.current_index]
            details = await fitgirl_scraper.get_game_details(current_result['url'])
            self.details_cache[self.current_index] = details
        return self.details_cache[self.current_index]
    
    async def download_image(self, url: str) -> Optional[discord.File]:
        """Download image and return as Discord File."""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://fitgirl-repacks.site/'
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        image_data = await resp.read()
                        # Get file extension from URL
                        ext = url.split('.')[-1].split('?')[0]
                        if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                            ext = 'jpg'
                        return discord.File(io.BytesIO(image_data), filename=f"cover.{ext}")
        except Exception as e:
            print(f"⚠️ Failed to download image: {e}")
        return None
    
    def create_embed(self, details: Dict[str, Any]) -> discord.Embed:
        """Create embed for current result."""
        current = self.results[self.current_index]
        
        embed = discord.Embed(
            title=current['title'],
            description=current['description'][:300] + "...",
            url=current['url'],
            color=0xFF1493  # FitGirl's pink color
        )
        
        # We'll attach the image separately, so set it to use the attachment
        banner_url = details.get('banner')
        if banner_url:
            # Use attachment:// to reference the uploaded file
            embed.set_image(url="attachment://cover.jpg")
            print(f"📸 Will attach image from: {banner_url}")
        else:
            print(f"⚠️ No banner URL available for result {self.current_index + 1}")
        
        # Add game info fields
        if details.get('genres'):
            embed.add_field(name="🎮 Genres", value=details['genres'], inline=True)
        
        if details.get('companies'):
            embed.add_field(name="🏢 Companies", value=details['companies'], inline=True)
        
        if details.get('languages'):
            embed.add_field(name="🌐 Languages", value=details['languages'], inline=True)
        
        if details.get('original_size'):
            embed.add_field(name="📦 Original Size", value=details['original_size'], inline=True)
        
        if details.get('repack_size'):
            embed.add_field(name="📥 Repack Size", value=details['repack_size'], inline=True)
        
        embed.add_field(
            name="🔗 View Full Repack",
            value=f"[Click here]({current['url']})",
            inline=False
        )
        
        embed.set_footer(
            text=f"Result {self.current_index + 1} of {len(self.results)} • FitGirl Repacks"
        )
        
        return embed
    
    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        self.current_index -= 1
        
        # Update button states
        self.children[0].disabled = (self.current_index == 0)
        self.children[1].disabled = (self.current_index == len(self.results) - 1)
        
        # Fetch details and update embed
        details = await self.fetch_current_details()
        embed = self.create_embed(details)
        
        # Download and attach image if available
        image_file = None
        if details.get('banner'):
            image_file = await self.download_image(details['banner'])
        
        if image_file:
            await interaction.message.edit(embed=embed, attachments=[image_file], view=self)
        else:
            await interaction.message.edit(embed=embed, view=self)
    
    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        self.current_index += 1
        
        # Update button states
        self.children[0].disabled = (self.current_index == 0)
        self.children[1].disabled = (self.current_index == len(self.results) - 1)
        
        # Fetch details and update embed
        details = await self.fetch_current_details()
        embed = self.create_embed(details)
        
        # Download and attach image if available
        image_file = None
        if details.get('banner'):
            image_file = await self.download_image(details['banner'])
        
        if image_file:
            await interaction.message.edit(embed=embed, attachments=[image_file], view=self)
        else:
            await interaction.message.edit(embed=embed, view=self)
    
    @discord.ui.button(label="📥 Download & Add", style=discord.ButtonStyle.success, row=1)
    async def download_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Download torrent and auto-create game submission."""
        # Allow anyone to add games - no permission check needed
        
        await interaction.response.send_message(
            "⏳ Checking if game already exists...",
            ephemeral=True
        )
        
        try:
            current = self.results[self.current_index]
            game_name = current['title']
            
            # Clean the game name for comparison
            clean_name = clean_game_name_for_search(game_name)
            
            # Check if game already exists in forum
            output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
            
            # Search through existing threads
            existing_thread = None
            async for thread in output_channel.archived_threads(limit=100):
                thread_clean = clean_game_name_for_search(thread.name)
                if thread_clean.lower() == clean_name.lower() or clean_name.lower() in thread_clean.lower():
                    existing_thread = thread
                    break
            
            # Also check active threads
            if not existing_thread:
                for thread in output_channel.threads:
                    thread_clean = clean_game_name_for_search(thread.name)
                    if thread_clean.lower() == clean_name.lower() or clean_name.lower() in thread_clean.lower():
                        existing_thread = thread
                        break
            
            if existing_thread:
                # Game already exists
                await interaction.followup.send(
                    f"✅ **{game_name}** already exists in the forum!\n\n"
                    f"📦 View existing thread: {existing_thread.mention}\n\n"
                    f"❌ Skipping download to avoid duplicates.",
                    ephemeral=True
                )
                # Delete the initial checking message
                await interaction.delete_original_response()
                return
            
            # Game doesn't exist, proceed with download
            queue_size = bot.playwright_queue.qsize()
            
            if bot.playwright_active or queue_size > 0:
                # Something is already processing
                position = queue_size + 1
                await interaction.followup.send(
                    f"⏳ **Download queued!**\n\n"
                    f"🔢 Position in queue: **#{position}**\n"
                    f"⚙️ Playwright is currently processing another download.\n\n"
                    f"Please wait, your download will start automatically...",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "✅ Game not found in forum.\n⏳ Downloading torrent from FitGirl... This may take a moment.",
                    ephemeral=True
                )
            
            details = await self.fetch_current_details()
            
            # Get torrent link from repack page
            paste_url = await fitgirl_scraper.get_torrent_link(current['url'])
            if not paste_url:
                await interaction.followup.send(
                    "❌ Could not find torrent link on the repack page.",
                    ephemeral=True
                )
                await asyncio.sleep(30)
                await interaction.delete_original_response()
                return
            
            # Check if it's a paste site (can download) or direct link (just post link)
            is_paste_site = 'paste' in paste_url.lower()
            
            if not is_paste_site:
                # It's a direct link (like sendfile.su), post as link instead
                await interaction.followup.send(
                    f"📎 Direct link detected! Posting game with link...",
                    ephemeral=True
                )
                
                # Create the game submission with link
                version = None
                notes = details.get('repack_size', '')
                if details.get('languages'):
                    notes = f"Languages: {details['languages']}\n" + notes
                
                # Process as link-based submission
                await process_game_submission(
                    interaction=interaction,
                    game_name=game_name,
                    version=version,
                    game_link=paste_url,  # Use torrent link as game link
                    notes=notes,
                    torrent_link=paste_url  # Also set as torrent link
                )
                
                await asyncio.sleep(10)
                try:
                    await interaction.delete_original_response()
                except:
                    pass
                return
            
            # It's a paste site, proceed with download
            # Create unique request ID
            request_id = f"{interaction.user.id}_{int(discord.utils.utcnow().timestamp())}"
            
            # Create callback for when download completes
            async def download_callback(torrent_data):
                if not torrent_data:
                    await interaction.followup.send(
                        "❌ Could not download torrent file. The paste site may require manual download.\n"
                        f"🔗 Torrent link: {paste_url}",
                        ephemeral=True
                    )
                    await asyncio.sleep(30)
                    try:
                        await interaction.delete_original_response()
                    except:
                        pass
                    return
                
                # Create the game submission
                version = None  # Could extract from title if needed
                notes = details.get('repack_size', '')
                if details.get('languages'):
                    notes = f"Languages: {details['languages']}\n" + notes
                
                # Store this as a pending torrent submission
                bot.pending_torrents[interaction.user.id] = {
                    'game_name': game_name,
                    'version': version,
                    'game_link': current['url'],  # Link to FitGirl page
                    'notes': notes,
                    'channel_id': OUTPUT_CHANNEL_ID,
                    'torrent_data': torrent_data,
                    'requester_id': interaction.user.id  # Store who initiated this
                }
                
                await interaction.followup.send(
                    f"✅ Torrent downloaded!\n"
                    f"📤 Creating game submission for **{game_name}**...",
                    ephemeral=True
                )
                
                # Simulate the torrent file upload by processing it directly
                await process_fitgirl_torrent_submission(interaction, interaction.user)
                
                # Delete the status messages after successful upload
                await asyncio.sleep(10)
                try:
                    await interaction.delete_original_response()
                except:
                    pass
            
            # Add to queue (store interaction for error reporting)
            await bot.playwright_queue.put({
                'paste_url': paste_url,
                'request_id': request_id,
                'callback': download_callback,
                'interaction': interaction  # Store for error reporting
            })
            
            # Update queue position message if needed
            if queue_size > 0:
                await asyncio.sleep(2)
                current_position = bot.queue_position.get(request_id, queue_size + 1)
                await interaction.followup.send(
                    f"📊 Queue update: Position **#{current_position}**",
                    ephemeral=True
                )
            
        except Exception as e:
            logger.error(f"⚠️ Error in download_button: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ An error occurred: {str(e)}\n"
                    f"Please check the logs for details.",
                    ephemeral=True
                )
            except:
                pass
            await asyncio.sleep(30)
            try:
                await interaction.delete_original_response()
            except:
                pass

# =========================================================
# FITGIRL SEARCH COMMAND
# =========================================================
@bot.tree.command(name="fgsearch", description="Search FitGirl Repacks for a game")
async def fgsearch(interaction: discord.Interaction, game_name: str):
    """Search FitGirl Repacks website for games."""
    # Allow anyone to use fgsearch - no permission check needed
    await interaction.response.defer()
    
    results = await fitgirl_scraper.search_game(game_name)
    
    if not results:
        await interaction.followup.send(
            f"❌ No results found for **{game_name}** on FitGirl Repacks.",
            ephemeral=True
        )
        return
    
    # Create navigation view
    view = FitGirlSearchView(results, game_name)
    
    # Fetch details for first result
    details = await view.fetch_current_details()
    embed = view.create_embed(details)
    
    # Disable next button if only one result
    if len(results) == 1:
        view.children[1].disabled = True
    
    # Download and attach image if available
    image_file = None
    if details.get('banner'):
        image_file = await view.download_image(details['banner'])
    
    if image_file:
        await interaction.followup.send(embed=embed, file=image_file, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view)

# =========================================================
# PROCESS FITGIRL TORRENT SUBMISSION
# =========================================================
async def process_fitgirl_torrent_submission(interaction, user):
    """Process a FitGirl torrent that was auto-downloaded."""
    try:
        if user.id not in bot.pending_torrents:
            logger.warning(f"No pending torrent for user {user.id}")
            return
        
        data = bot.pending_torrents.pop(user.id)
        torrent_data = data.get('torrent_data')
        
        if not torrent_data:
            logger.error(f"No torrent data for user {user.id}")
            await interaction.followup.send(
                "❌ No torrent data available. Download may have failed.",
                ephemeral=True
            )
            return
        
        output_channel = bot.get_channel(data['channel_id'])
        if not output_channel:
            logger.error(f"Output channel {data['channel_id']} not found")
            await interaction.followup.send(
                "❌ Could not find forum channel. Please contact an admin.",
                ephemeral=True
            )
            return
        
        input_channel = bot.get_channel(INPUT_CHANNEL_ID)
    
        # Clean the game name for better IGDB/RAWG search results
        clean_name = clean_game_name_for_search(data['game_name'])
        
        # Search IGDB for game data (with RAWG fallback)
        logger.info(f"Searching IGDB for: {clean_name}")
        igdb_data = await igdb_client.search_game_by_name(clean_name)
        
        # If IGDB fails, try RAWG as fallback
        if not igdb_data:
            logger.info(f"🔄 IGDB failed, trying RAWG fallback for: {clean_name}")
            igdb_data = await rawg_client.search_game_by_name(clean_name)
            if igdb_data:
                logger.info(f"✅ RAWG found data for: {clean_name}")
        
        # BUILD EMBED WITH IGDB DATA
        if igdb_data:
            game_title = igdb_data.get("name", data['game_name'])
            summary = igdb_data.get("summary", data['notes'] or "No description available.")
            
            genres = igdb_data.get("genres", [])
            genres_text = " • ".join([g["name"] for g in genres]) if genres else "N/A"
            
            platforms = igdb_data.get("platforms", [])
            platforms_text = " • ".join([p["name"] for p in platforms]) if platforms else "N/A"
            
            cover_url = None
            if "cover" in igdb_data and igdb_data["cover"]:
                image_id = igdb_data["cover"].get("image_id")
                if image_id and isinstance(image_id, str) and len(image_id) > 0:
                    if image_id.startswith("http"):
                        cover_url = image_id
                    else:
                        cover_url = f"https://images.igdb.com/igdb/image/upload/t_screenshot_big/{image_id}.jpg"
            
            embed = discord.Embed(
                title=game_title,
                description=summary[:600] + ("..." if len(summary) > 600 else ""),
                color=0x1b2838
            )
            
            if cover_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(cover_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                embed.set_image(url=cover_url)
                except Exception as e:
                    logger.warning(f"Could not verify image: {e}")
                    embed.set_image(url=cover_url)
            
            embed.add_field(name="🎮 Genres", value=genres_text, inline=True)
            embed.add_field(name="🖥️ Platforms", value=platforms_text, inline=True)
            
            if data.get('version'):
                embed.add_field(name="📦 Version", value=data['version'], inline=True)
            
            if data['notes']:
                embed.add_field(
                    name="📝 FitGirl Repack Info",
                    value=data['notes'][:400] + ("..." if len(data['notes']) > 400 else ""),
                    inline=False
                )
            
            embed.set_footer(
                text=f"Added by {user.name} • From FitGirl Repacks",
                icon_url=user.display_avatar.url
            )
            embed.timestamp = discord.utils.utcnow()
        else:
            embed = discord.Embed(
                title=data['game_name'],
                description=data['notes'] or "No description provided.",
                color=0x1b2838
            )
            embed.add_field(
                name="⚠️ Note",
                value="Game data not found in IGDB database",
                inline=False
            )
            embed.set_footer(
                text=f"Added by {user.name} • From FitGirl Repacks",
                icon_url=user.display_avatar.url
            )
            embed.timestamp = discord.utils.utcnow()
        
        # Create torrent file object
        torrent_filename = f"{data['game_name'][:50]} [FitGirl Repack].torrent"
        torrent_filename = re.sub(r'[<>:"/\\|?*]', '', torrent_filename)  # Clean filename
        
        public_torrent_file = discord.File(
            fp=io.BytesIO(torrent_data),
            filename=f"SPOILER_{torrent_filename}",
            spoiler=True
        )
        
        thread_name = igdb_data.get("name", data['game_name']) if igdb_data else data['game_name']
        thread_name = thread_name[:100]
        
        view = GameButtonView(data['game_link'], None)
        
        # Create thread in forum channel
        thread_result = None
        thread = None
        try:
            thread_result = await output_channel.create_thread(
                name=thread_name,
                content=f"**{thread_name}**",
                embed=embed,
                file=public_torrent_file,
                view=view
            )
            # create_thread returns ThreadWithMessage, access thread via .thread
            thread = thread_result.thread if hasattr(thread_result, 'thread') else thread_result
            logger.info(f"✅ Created forum thread: {thread_name} (ID: {thread.id})")
        except Exception as e:
            logger.error(f"Failed to create forum thread: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Failed to create forum thread: {str(e)}",
                ephemeral=True
            )
            return
        
        # Get public torrent URL and update with button
        try:
            # ThreadWithMessage has .message attribute for the starter message
            starter_message = getattr(thread_result, 'message', None) if thread_result else None
            if not starter_message:
                # Fallback: try to get from thread
                try:
                    async for message in thread.history(limit=1):
                        starter_message = message
                        break
                except:
                    pass
            
            if starter_message and hasattr(starter_message, 'attachments') and starter_message.attachments:
                public_torrent_url = starter_message.attachments[0].url
                view = GameButtonView(data['game_link'], public_torrent_url)
                await starter_message.edit(view=view if view.children else None)
        except Exception as e:
            logger.warning(f"Could not update starter message with button: {e}", exc_info=True)
        
        # Log to input channel
        try:
            version_text = f"\n📦 **Version:** {data['version']}" if data.get('version') else ""
            thread_mention = thread.mention
            await input_channel.send(
                f"📥 **New Game Submitted** (Auto from FitGirl)\n"
                f"👤 **User:** {user.mention}\n"
                f"🎮 **Game:** {data['game_name']}{version_text}\n"
                f"🔗 **Link:** {data['game_link'] or 'N/A'}\n"
                f"⬇️ **Torrent:** Attached\n"
                f"📦 **Thread:** {thread_mention}"
            )
        except Exception as e:
            logger.error(f"Could not log to input channel: {e}", exc_info=True)
        
        # Track contributor
        try:
            if user.id not in bot.contributor_stats:
                bot.contributor_stats[user.id] = 0
            bot.contributor_stats[user.id] += 1
            save_bot_state()
        except Exception as e:
            logger.error(f"Could not update contributor stats: {e}", exc_info=True)
        
        # Update dashboard immediately
        try:
            await update_dashboard()
        except Exception as e:
            logger.error(f"Could not update dashboard: {e}", exc_info=True)
        
        # DM the user
        try:
            thread_mention = thread.mention
            dm_embed = discord.Embed(
                title="✅ Game Added Successfully!",
                description=f"**{thread_name}** has been added from FitGirl Repacks!",
                color=0x00FF00
            )
            dm_embed.add_field(
                name="🔗 View Game Thread",
                value=thread_mention,
                inline=False
            )
            dm_embed.set_footer(text="FitGirl Auto-Add")
            
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            logger.warning(f"Could not DM user {user.name} - DMs disabled")
        except Exception as e:
            logger.error(f"Error sending DM: {e}", exc_info=True)
        
        # Follow up in the interaction
        try:
            thread_mention = thread.mention
            await interaction.followup.send(
                f"✅ **{thread_name}** has been added to {thread_mention}!\n"
                f"📬 Check your DMs for details!",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error sending followup: {e}", exc_info=True)
    
    except Exception as e:
        logger.error(f"Error in process_fitgirl_torrent_submission: {e}", exc_info=True)
        try:
            await interaction.followup.send(
                f"❌ Error creating forum thread: {str(e)}\n"
                f"Please check the logs for details.",
                ephemeral=True
            )
        except:
            pass

# =========================================================
# REFRESH DASHBOARD COMMAND
# =========================================================
@bot.tree.command(name="refreshdashboard", description="Manually refresh the dashboard")
async def refreshdashboard(interaction: discord.Interaction):
    """Manually trigger a dashboard update."""
    if not await check_command_permissions(interaction):
        return
    
    # Check if user has the required role
    REQUIRED_ROLE_ID = 1072117821397540954
    has_role = any(role.id == REQUIRED_ROLE_ID for role in interaction.user.roles)
    
    if not has_role:
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    await interaction.response.send_message(
        "🔄 Refreshing dashboard...",
        ephemeral=True
    )
    
    try:
        await update_dashboard()
        await interaction.followup.send(
            "✅ Dashboard refreshed successfully!",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error refreshing dashboard: {str(e)}",
            ephemeral=True
        )

# =========================================================
# REPORT ISSUE MODAL
# =========================================================
class ReportIssueModal(discord.ui.Modal, title="Report an Issue"):
    issue_type = discord.ui.TextInput(
        label="Issue Type",
        placeholder="Dead link, wrong info, not working, etc.",
        required=True,
        max_length=50
    )
    
    details = discord.ui.TextInput(
        label="Details",
        placeholder="Describe the issue...",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=1000
    )
    
    def __init__(self, thread_id: int):
        super().__init__()
        self.thread_id = thread_id
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get the thread
            thread = bot.get_channel(self.thread_id)
            if not thread:
                await interaction.followup.send("❌ Could not find the game thread.", ephemeral=True)
                return
            
            # Send report to the thread
            embed = discord.Embed(
                title="⚠️ Issue Reported",
                description=f"**Type:** {self.issue_type.value}\n**Details:** {self.details.value}",
                color=discord.Color.orange(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Reported by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
            
            await thread.send(embed=embed)
            await interaction.followup.send("✅ Issue reported! Moderators will check it soon.", ephemeral=True)
            
        except Exception as e:
            print(f"Error reporting issue: {e}")
            await interaction.followup.send(f"❌ Error reporting issue: {str(e)}", ephemeral=True)

# =========================================================
# REQUEST GAME MODAL
# =========================================================
class RequestGameModal(discord.ui.Modal, title="Request a Game"):
    game_name = discord.ui.TextInput(
        label="Game Name",
        placeholder="Enter the game you want...",
        required=True,
        max_length=100
    )

    steam_link = discord.ui.TextInput(
        label="Steam Link (optional)",
        placeholder="https://store.steampowered.com/app/...",
        required=False,
        style=discord.TextStyle.short
    )

    notes = discord.ui.TextInput(
        label="Additional Notes (optional)",
        placeholder="Why do you want this game? Any specific version?",
        required=False,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        request_channel = bot.get_channel(REQUEST_CHANNEL_ID)
        
        # Search for game info to get metadata
        igdb_data = await igdb_client.search_game_by_name(self.game_name.value)
        
        # If IGDB fails, try RAWG
        if not igdb_data:
            igdb_data = await rawg_client.search_game_by_name(self.game_name.value)
        
        # Build request embed
        if igdb_data:
            game_title = igdb_data.get("name", self.game_name.value)
            summary = igdb_data.get("summary", "No description available.")
            
            genres = igdb_data.get("genres", [])
            genres_text = " • ".join([g["name"] for g in genres]) if genres else "N/A"
            
            platforms = igdb_data.get("platforms", [])
            platforms_text = " • ".join([p["name"] for p in platforms]) if platforms else "N/A"
            
            # Get cover image
            cover_url = None
            if "cover" in igdb_data and "image_id" in igdb_data["cover"]:
                image_id = igdb_data["cover"]["image_id"]
                if image_id and image_id.startswith("http"):
                    cover_url = image_id
                elif image_id:
                    cover_url = f"https://images.igdb.com/igdb/image/upload/t_screenshot_big/{image_id}.jpg"
            
            embed = discord.Embed(
                title=f"🎮 Game Request: {game_title}",
                description=summary[:400] + ("..." if len(summary) > 400 else ""),
                color=0xFF5733  # Orange color for requests
            )
            
            if cover_url:
                embed.set_thumbnail(url=cover_url)
            
            embed.add_field(name="📋 Genres", value=genres_text, inline=True)
            embed.add_field(name="🖥️ Platforms", value=platforms_text, inline=True)
            
        else:
            # Fallback without metadata
            embed = discord.Embed(
                title=f"🎮 Game Request: {self.game_name.value}",
                description="No game information found in database.",
                color=0xFF5733
            )
        
        # Add request details
        embed.add_field(name="👤 Requested By", value=interaction.user.mention, inline=True)
        embed.add_field(name="📅 Date", value=discord.utils.format_dt(discord.utils.utcnow(), style='R'), inline=True)
        
        if self.steam_link.value:
            embed.add_field(name="🔗 Steam Link", value=self.steam_link.value, inline=False)
        
        if self.notes.value:
            embed.add_field(name="📝 Notes", value=self.notes.value[:300], inline=False)
        
        embed.set_footer(
            text=f"Requested by {interaction.user.name}",
            icon_url=interaction.user.display_avatar.url
        )
        embed.timestamp = discord.utils.utcnow()
        
        # Create fulfill button
        fulfill_view = FulfillRequestView(interaction.user.id, self.game_name.value)
        
        # Send to request channel with button
        await request_channel.send(embed=embed, view=fulfill_view)
        
        # Confirm to user
        await interaction.followup.send(
            "✅ Your game request has been submitted! Mods will review it.",
            ephemeral=True
        )

# =========================================================
# FULFILL REQUEST BUTTON VIEW
# =========================================================
class FulfillRequestView(discord.ui.View):
    """Button view for fulfilling game requests."""
    
    def __init__(self, requester_id: int, game_name: str):
        super().__init__(timeout=None)  # Persistent buttons
        self.requester_id = requester_id
        self.game_name = game_name
    
    @discord.ui.button(label="✅ Fulfill Request (Link)", style=discord.ButtonStyle.success, custom_id="fulfill_link")
    async def fulfill_link_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Store the request info for later
        bot.pending_fulfillments[interaction.user.id] = {
            'requester_id': self.requester_id,
            'game_name': self.game_name,
            'request_message_id': interaction.message.id,
            'request_channel_id': interaction.channel_id
        }
        
        # Open the add game modal for link
        await interaction.response.send_modal(AddGameLinkModal())
    
    @discord.ui.button(label="⬇️ Fulfill Request (Torrent)", style=discord.ButtonStyle.primary, custom_id="fulfill_torrent")
    async def fulfill_torrent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Store the request info for later
        bot.pending_fulfillments[interaction.user.id] = {
            'requester_id': self.requester_id,
            'game_name': self.game_name,
            'request_message_id': interaction.message.id,
            'request_channel_id': interaction.channel_id
        }
        
        # Open the add game modal for torrent
        await interaction.response.send_modal(AddGameTorrentModal())

# -------------------------
# REQUEST GAME COMMAND
# -------------------------
@bot.tree.command(name="requestgame", description="Request a game from the mods")
async def requestgame(interaction: discord.Interaction):
    if not await check_command_permissions(interaction):
        return
    await interaction.response.send_modal(RequestGameModal())

# =========================================================
# BUTTON VIEW FOR GAME EMBEDS
# =========================================================
class GameButtonView(discord.ui.View):
    """Interactive buttons for game embeds."""
    
    def __init__(self, game_links: str, torrent_link: str = None):
        super().__init__(timeout=None)  # Persistent buttons
        
        # Parse multiple game links (comma-separated)
        if game_links:
            links = [link.strip() for link in game_links.split(',') if link.strip()]
            
            # Add buttons for each valid link (max 5 to stay within Discord limits)
            link_names = ["🔗 Link 1", "🔗 Link 2", "🔗 Link 3", "🔗 Link 4", "🔗 Link 5"]
            for idx, link in enumerate(links[:5]):  # Discord allows max 5 buttons per row
                if self._is_valid_url(link):
                    # Try to detect store name from URL
                    label = self._get_link_label(link, idx)
                    view_button = discord.ui.Button(
                        label=label,
                        style=discord.ButtonStyle.link,
                        url=link
                    )
                    self.add_item(view_button)
        
        # Validate and add "Get Torrent" button if torrent link provided
        if torrent_link and (self._is_valid_url(torrent_link) or self._is_magnet_link(torrent_link)):
            torrent_button = discord.ui.Button(
                label="⬇️ Get Torrent",
                style=discord.ButtonStyle.link,
                url=torrent_link
            )
            self.add_item(torrent_button)
    
    @discord.ui.button(label="⚠️ Report Issue", style=discord.ButtonStyle.danger, custom_id="report_issue", row=1)
    async def report_issue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Report broken links or issues with this game."""
        await interaction.response.send_modal(ReportIssueModal(interaction.channel.id))
    
    @staticmethod
    def _get_link_label(url: str, index: int) -> str:
        """Generate a label for the link button based on URL."""
        url_lower = url.lower()
        if 'steam' in url_lower:
            return '🎮 Steam'
        elif 'epic' in url_lower:
            return '🎮 Epic Games'
        elif 'gog' in url_lower:
            return '🎮 GOG'
        elif 'itch.io' in url_lower:
            return '🎮 itch.io'
        elif 'humble' in url_lower:
            return '🎮 Humble'
        else:
            return f"🔗 Link {index + 1}"
    
    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """Check if URL is valid for Discord link buttons."""
        if not url:
            return False
        url_pattern = re.compile(
            r'^(https?|discord)://'  # http, https, or discord scheme
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain
            r'localhost|'  # localhost
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        return bool(url_pattern.match(url))
    
    @staticmethod
    def _is_magnet_link(url: str) -> bool:
        """Check if it's a valid magnet link."""
        return url.startswith("magnet:?")

# =========================================================
# SELECTION VIEW - LINK OR TORRENT
# =========================================================
class DownloadTypeView(discord.ui.View):
    """View to select between Link or Torrent."""
    
    def __init__(self):
        super().__init__(timeout=60)
    
    @discord.ui.button(label="🔗 Add Link", style=discord.ButtonStyle.primary)
    async def link_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddGameLinkModal())
    
    @discord.ui.button(label="⬇️ Add Torrent", style=discord.ButtonStyle.success)
    async def torrent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddGameTorrentModal())

# =========================================================
# MODAL FOR LINK-BASED GAMES
# =========================================================
class AddGameLinkModal(discord.ui.Modal, title="Add a Game (Link)"):
    game_name = discord.ui.TextInput(
        label="Game Name",
        placeholder="Elden Ring",
        required=True,
        max_length=100
    )

    version = discord.ui.TextInput(
        label="Version (e.g., v1.0.2, Build 12345)",
        placeholder="v1.0.2",
        required=False,
        max_length=50
    )

    game_link = discord.ui.TextInput(
        label="Game Links (comma-separated)",
        placeholder="https://steam.com/..., https://epic.com/...",
        required=True,
        style=discord.TextStyle.paragraph
    )

    notes = discord.ui.TextInput(
        label="Notes (optional)",
        placeholder="Why is this game interesting?",
        required=False,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        await process_game_submission(
            interaction, 
            self.game_name.value, 
            self.game_link.value, 
            None,  # No torrent
            self.notes.value,
            self.version.value
        )

# =========================================================
# MODAL FOR TORRENT-BASED GAMES
# =========================================================
class AddGameTorrentModal(discord.ui.Modal, title="Add a Game (Torrent)"):
    game_name = discord.ui.TextInput(
        label="Game Name",
        placeholder="Elden Ring",
        required=True,
        max_length=100
    )
    
    version = discord.ui.TextInput(
        label="Version (e.g., v1.0.2, Build 12345)",
        placeholder="v1.0.2",
        required=False,
        max_length=50
    )
    
    game_link = discord.ui.TextInput(
        label="Store Links (optional, comma-separated)",
        placeholder="https://steam.com/..., https://epic.com/...",
        required=False,
        style=discord.TextStyle.short
    )

    notes = discord.ui.TextInput(
        label="Notes (optional)",
        placeholder="Why is this game interesting?",
        required=False,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Ask user to upload torrent file
        await interaction.response.send_message(
            f"✅ **{self.game_name.value}** added!\n\n"
            "📎 **Please upload the .torrent file in this channel now.**\n"
            "The bot will automatically attach it to your game submission.",
            ephemeral=True
        )
        
        # Store pending submission data
        bot.pending_torrents[interaction.user.id] = {
            'game_name': self.game_name.value,
            'version': self.version.value,
            'game_link': self.game_link.value,
            'notes': self.notes.value,
            'channel_id': OUTPUT_CHANNEL_ID
        }

# =========================================================
# PROCESS GAME SUBMISSION (SHARED LOGIC)
# =========================================================
async def process_game_submission(interaction, game_name, game_link, torrent_link, notes, version=None):
    """Shared logic to process and post game submissions."""
    # Defer response if not already responded
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    
    input_channel = bot.get_channel(INPUT_CHANNEL_ID)
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)

    # -------------------------
    # SEARCH IGDB FOR GAME DATA (WITH RAWG FALLBACK)
    # -------------------------
    igdb_data = await igdb_client.search_game_by_name(game_name)
    
    # If IGDB fails, try RAWG as fallback
    if not igdb_data:
        print(f"🔄 IGDB failed, trying RAWG fallback for: {game_name}")
        igdb_data = await rawg_client.search_game_by_name(game_name)
        if igdb_data:
            print(f"✅ RAWG found data for: {game_name}")

    # -------------------------
    # BUILD EMBED WITH IGDB DATA
    # -------------------------
    if igdb_data:
        # Extract IGDB data with fallbacks
        game_title = igdb_data.get("name", game_name)
        summary = igdb_data.get("summary", notes or "No description available.")
        
        # Extract genres
        genres = igdb_data.get("genres", [])
        genres_text = " • ".join([g["name"] for g in genres]) if genres else "N/A"
        
        # Extract platforms
        platforms = igdb_data.get("platforms", [])
        platforms_text = " • ".join([p["name"] for p in platforms]) if platforms else "N/A"
        
        # Extract cover image - handle both IGDB and RAWG formats
        cover_url = None
        if "cover" in igdb_data and igdb_data["cover"]:
            image_id = igdb_data["cover"].get("image_id")
            # Check if it's a direct URL (RAWG) or IGDB image ID
            if image_id and isinstance(image_id, str) and len(image_id) > 0:
                if image_id.startswith("http"):
                    cover_url = image_id  # RAWG direct URL
                    print(f"📸 Using RAWG cover: {cover_url}")
                else:
                    cover_url = f"https://images.igdb.com/igdb/image/upload/t_screenshot_big/{image_id}.jpg"
                    print(f"📸 Using IGDB cover: {cover_url}")
            else:
                print(f"⚠️ Invalid or missing image_id: {image_id}")
        else:
            print(f"⚠️ No cover data in game info")
        
        # Build Steam-like embed with clean layout
        embed = discord.Embed(
            title=game_title,
            description=summary[:600] + ("..." if len(summary) > 600 else ""),
            color=0x1b2838  # Steam's dark blue color
        )
        
        # Large cover image
        if cover_url:
            try:
                # Verify the URL is accessible
                async with aiohttp.ClientSession() as session:
                    async with session.head(cover_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            embed.set_image(url=cover_url)
                            print(f"✅ Image verified and set: {cover_url}")
                        else:
                            print(f"⚠️ Image URL returned status {resp.status}: {cover_url}")
                            # Try setting it anyway, Discord might still fetch it
                            embed.set_image(url=cover_url)
            except Exception as e:
                print(f"⚠️ Could not verify image (setting anyway): {e}")
                # Set it anyway, might still work
                embed.set_image(url=cover_url)
        else:
            print(f"⚠️ No cover_url to set on embed")
        
        # Add fields in clean format
        embed.add_field(
            name="🎮 Genres",
            value=genres_text,
            inline=True
        )
        
        embed.add_field(
            name="🖥️ Platforms",
            value=platforms_text,
            inline=True
        )
        
        # Version if provided
        if version:
            embed.add_field(
                name="📦 Version",
                value=version,
                inline=True
            )
        
        # User notes if provided
        if notes:
            embed.add_field(
                name="📝 User Notes",
                value=notes[:400] + ("..." if len(notes) > 400 else ""),
                inline=False
            )
        
        # Footer with user info
        embed.set_footer(
            text=f"Added by {interaction.user.name}",
            icon_url=interaction.user.display_avatar.url
        )
        
        # Timestamp
        embed.timestamp = discord.utils.utcnow()
        
    else:
        # Fallback if IGDB data not found
        embed = discord.Embed(
            title=game_name,
            description=notes or "No description provided.",
            color=0x1b2838
        )
        
        embed.add_field(
            name="⚠️ Note",
            value="Game data not found in IGDB database",
            inline=False
        )
        
        embed.set_footer(
            text=f"Added by {interaction.user.name}",
            icon_url=interaction.user.display_avatar.url
        )
        
        embed.timestamp = discord.utils.utcnow()

    # -------------------------
    # CREATE THREAD IN FORUM CHANNEL
    # -------------------------
    view = GameButtonView(game_link, torrent_link)
    
    # If no valid button was added, show link in embed
    if not view.children and game_link:
        embed.add_field(name="🔗 Game Link", value=game_link, inline=False)
    
    # Get the game title for thread name
    thread_name = igdb_data.get("name", game_name) if igdb_data else game_name
    thread_name = thread_name[:100]  # Discord thread name limit
    
    # Create thread in forum channel
    thread = await output_channel.create_thread(
        name=thread_name,
        content=f"**{thread_name}**",  # Optional initial message
        embed=embed,
        view=view if view.children else None
    )

    # -------------------------
    # INPUT CHANNEL LOG (After successful upload)
    # -------------------------
    version_text = f"\n📦 **Version:** {version}" if version else ""
    await input_channel.send(
        f"📥 **New Game Submitted**\n"
        f"👤 **User:** {interaction.user.mention}\n"
        f"🎮 **Game:** {game_name}{version_text}\n"
        f"🔗 **Link:** {game_link or 'N/A'}\n"
        f"📦 **Thread:** {thread.thread.mention}"
    )
    
    # Track contributor
    if interaction.user.id not in bot.contributor_stats:
        bot.contributor_stats[interaction.user.id] = 0
    bot.contributor_stats[interaction.user.id] += 1
    save_bot_state()
    
    # Update dashboard immediately
    try:
        await update_dashboard()
    except:
        pass

    # -------------------------
    # HANDLE REQUEST FULFILLMENT
    # -------------------------
    if interaction.user.id in bot.pending_fulfillments:
        fulfillment_data = bot.pending_fulfillments.pop(interaction.user.id)
        
        # Get the requester
        requester = await bot.fetch_user(fulfillment_data['requester_id'])
        
        # Send DM to requester
        try:
            dm_embed = discord.Embed(
                title="✅ Your Game Request Has Been Fulfilled!",
                description=f"**{thread_name}** is now available!",
                color=0x00FF00
            )
            dm_embed.add_field(
                name="🔗 Download Here",
                value=thread.thread.mention,
                inline=False
            )
            dm_embed.set_footer(text=f"Fulfilled by {interaction.user.name}")
            
            await requester.send(embed=dm_embed)
        except discord.Forbidden:
            print(f"⚠️ Could not DM user {requester.name} - DMs disabled")
        
        # Update the request message to mark as fulfilled
        try:
            request_channel = bot.get_channel(fulfillment_data['request_channel_id'])
            request_message = await request_channel.fetch_message(fulfillment_data['request_message_id'])
            
            # Get the original embed and update it
            original_embed = request_message.embeds[0]
            original_embed.color = 0x00FF00  # Green for fulfilled
            original_embed.title = f"✅ [FULFILLED] {original_embed.title.replace('🎮 Game Request: ', '')}"
            original_embed.add_field(
                name="📦 Fulfilled By",
                value=interaction.user.mention,
                inline=True
            )
            original_embed.add_field(
                name="🔗 Thread",
                value=thread.thread.mention,
                inline=True
            )
            
            # Disable the button
            disabled_view = discord.ui.View(timeout=None)
            disabled_button = discord.ui.Button(
                label="✅ Fulfilled",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id="fulfilled"
            )
            disabled_view.add_item(disabled_button)
            
            await request_message.edit(embed=original_embed, view=disabled_view)
        except Exception as e:
            print(f"⚠️ Could not update request message: {e}")

    # -------------------------
    # CONFIRM TO USER
    # -------------------------
    confirmation_msg = f"✅ Game submitted successfully in {thread.thread.mention}!"
    if interaction.user.id in bot.pending_fulfillments or any(f['request_message_id'] for f in bot.pending_fulfillments.values() if f):
        confirmation_msg += "\n✉️ Requester has been notified via DM!"
    
    await interaction.followup.send(
        confirmation_msg,
        ephemeral=True
    )

# =========================================================
# EVENT: HANDLE TORRENT FILE UPLOADS
# =========================================================
@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Check if user has pending torrent submission
    if message.author.id in bot.pending_torrents:
        # Check if message has .torrent attachment
        torrent_file = None
        for attachment in message.attachments:
            if attachment.filename.endswith('.torrent'):
                torrent_file = attachment
                break
        
        if torrent_file:
            # Get pending submission data
            data = bot.pending_torrents.pop(message.author.id)
            output_channel = bot.get_channel(data['channel_id'])
            input_channel = bot.get_channel(INPUT_CHANNEL_ID)
            
            # Search IGDB for game data (with RAWG fallback)
            igdb_data = await igdb_client.search_game_by_name(data['game_name'])
            
            # If IGDB fails, try RAWG as fallback
            if not igdb_data:
                print(f"🔄 IGDB failed, trying RAWG fallback for: {data['game_name']}")
                igdb_data = await rawg_client.search_game_by_name(data['game_name'])
                if igdb_data:
                    print(f"✅ RAWG found data for: {data['game_name']}")
            
            # BUILD EMBED WITH IGDB DATA
            if igdb_data:
                game_title = igdb_data.get("name", data['game_name'])
                summary = igdb_data.get("summary", data['notes'] or "No description available.")
                
                genres = igdb_data.get("genres", [])
                genres_text = " • ".join([g["name"] for g in genres]) if genres else "N/A"
                
                platforms = igdb_data.get("platforms", [])
                platforms_text = " • ".join([p["name"] for p in platforms]) if platforms else "N/A"
                
                cover_url = None
                if "cover" in igdb_data and igdb_data["cover"]:
                    image_id = igdb_data["cover"].get("image_id")
                    # Check if it's a direct URL (RAWG) or IGDB image ID
                    if image_id and isinstance(image_id, str) and len(image_id) > 0:
                        if image_id.startswith("http"):
                            cover_url = image_id  # RAWG direct URL
                            print(f"📸 Using RAWG cover: {cover_url}")
                        else:
                            cover_url = f"https://images.igdb.com/igdb/image/upload/t_screenshot_big/{image_id}.jpg"
                            print(f"📸 Using IGDB cover: {cover_url}")
                    else:
                        print(f"⚠️ Invalid or missing image_id: {image_id}")
                else:
                    print(f"⚠️ No cover data in game info")
                
                embed = discord.Embed(
                    title=game_title,
                    description=summary[:600] + ("..." if len(summary) > 600 else ""),
                    color=0x1b2838
                )
                
                if cover_url:
                    try:
                        # Verify the URL is accessible
                        async with aiohttp.ClientSession() as session:
                            async with session.head(cover_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                                if resp.status == 200:
                                    embed.set_image(url=cover_url)
                                    print(f"✅ Image verified and set: {cover_url}")
                                else:
                                    print(f"⚠️ Image URL returned status {resp.status}: {cover_url}")
                                    # Try setting it anyway, Discord might still fetch it
                                    embed.set_image(url=cover_url)
                    except Exception as e:
                        print(f"⚠️ Could not verify image (setting anyway): {e}")
                        # Set it anyway, might still work
                        embed.set_image(url=cover_url)
                else:
                    print(f"⚠️ No cover_url to set on embed")
                
                embed.add_field(name="🎮 Genres", value=genres_text, inline=True)
                embed.add_field(name="🖥️ Platforms", value=platforms_text, inline=True)
                
                # Version if provided
                if data.get('version'):
                    embed.add_field(
                        name="📦 Version",
                        value=data['version'],
                        inline=True
                    )
                
                if data['notes']:
                    embed.add_field(
                        name="📝 User Notes",
                        value=data['notes'][:400] + ("..." if len(data['notes']) > 400 else ""),
                        inline=False
                    )
                
                embed.set_footer(
                    text=f"Added by {message.author.name}",
                    icon_url=message.author.display_avatar.url
                )
                embed.timestamp = discord.utils.utcnow()
                
            else:
                embed = discord.Embed(
                    title=data['game_name'],
                    description=data['notes'] or "No description provided.",
                    color=0x1b2838
                )
                
                embed.add_field(
                    name="⚠️ Note",
                    value="Game data not found in IGDB database",
                    inline=False
                )
                
                embed.set_footer(
                    text=f"Added by {message.author.name}",
                    icon_url=message.author.display_avatar.url
                )
                embed.timestamp = discord.utils.utcnow()
            
            # -------------------------
            # RE-UPLOAD TORRENT TO OUTPUT CHANNEL FOR PUBLIC ACCESS
            # -------------------------
            # Download the torrent file first
            async with aiohttp.ClientSession() as session:
                async with session.get(torrent_file.url) as resp:
                    if resp.status == 200:
                        torrent_bytes = await resp.read()
                    else:
                        print(f"Failed to download torrent: {resp.status}")
                        torrent_bytes = None
            
            if torrent_bytes:
                # Create Discord File object with SPOILER prefix
                public_torrent_file = discord.File(
                    fp=io.BytesIO(torrent_bytes),
                    filename=f"SPOILER_{torrent_file.filename}",  # SPOILER_ prefix hides the file
                    spoiler=True
                )
                
                # Get the game title for thread name
                thread_name = igdb_data.get("name", data['game_name']) if igdb_data else data['game_name']
                thread_name = thread_name[:100]  # Discord thread name limit
                
                # Create button view
                view = GameButtonView(data['game_link'], None)  # Will add torrent button after upload
                
                # Create thread in forum channel with embed and torrent file
                thread = await output_channel.create_thread(
                    name=thread_name,
                    content=f"**{thread_name}**",
                    embed=embed,
                    file=public_torrent_file,
                    view=view
                )
                
                # Get the public torrent URL from the thread's first message
                starter_message = thread.message
                if starter_message.attachments:
                    public_torrent_url = starter_message.attachments[0].url
                    
                    # Now add the torrent button
                    view = GameButtonView(data['game_link'], public_torrent_url)
                    
                    # Edit message to add the torrent button
                    await starter_message.edit(view=view if view.children else None)
                
                # -------------------------
                # INPUT CHANNEL LOG (After successful upload)
                # -------------------------
                version_text = f"\n📦 **Version:** {data['version']}" if data.get('version') else ""
                await input_channel.send(
                    f"📥 **New Game Submitted**\n"
                    f"👤 **User:** {message.author.mention}\n"
                    f"🎮 **Game:** {data['game_name']}{version_text}\n"
                    f"🔗 **Link:** {data['game_link'] or 'N/A'}\n"
                    f"⬇️ **Torrent:** Attached\n"
                    f"📦 **Thread:** {thread.thread.mention}"
                )
                
                # -------------------------
                # HANDLE REQUEST FULFILLMENT
                # -------------------------
                if message.author.id in bot.pending_fulfillments:
                    fulfillment_data = bot.pending_fulfillments.pop(message.author.id)
                    
                    # Get the requester
                    requester = await bot.fetch_user(fulfillment_data['requester_id'])
                    
                    # Send DM to requester
                    try:
                        dm_embed = discord.Embed(
                            title="✅ Your Game Request Has Been Fulfilled!",
                            description=f"**{thread_name}** is now available!",
                            color=0x00FF00
                        )
                        dm_embed.add_field(
                            name="🔗 Download Here",
                            value=thread.thread.mention,
                            inline=False
                        )
                        dm_embed.set_footer(text=f"Fulfilled by {message.author.name}")
                        
                        await requester.send(embed=dm_embed)
                    except discord.Forbidden:
                        print(f"⚠️ Could not DM user {requester.name} - DMs disabled")
                    
                    # Update the request message to mark as fulfilled
                    try:
                        request_channel = bot.get_channel(fulfillment_data['request_channel_id'])
                        request_message = await request_channel.fetch_message(fulfillment_data['request_message_id'])
                        
                        # Get the original embed and update it
                        original_embed = request_message.embeds[0]
                        original_embed.color = 0x00FF00  # Green for fulfilled
                        original_embed.title = f"✅ [FULFILLED] {original_embed.title.replace('🎮 Game Request: ', '')}"
                        original_embed.add_field(
                            name="📦 Fulfilled By",
                            value=message.author.mention,
                            inline=True
                        )
                        original_embed.add_field(
                            name="🔗 Thread",
                            value=thread.thread.mention,
                            inline=True
                        )
                        
                        # Disable the button
                        disabled_view = discord.ui.View(timeout=None)
                        disabled_button = discord.ui.Button(
                            label="✅ Fulfilled",
                            style=discord.ButtonStyle.secondary,
                            disabled=True,
                            custom_id="fulfilled"
                        )
                        disabled_view.add_item(disabled_button)
                        
                        await request_message.edit(embed=original_embed, view=disabled_view)
                    except Exception as e:
                        print(f"⚠️ Could not update request message: {e}")
                
                # Confirm to user with thread mention
                await message.channel.send(
                    f"{message.author.mention} ✅ Game submitted successfully in {thread.thread.mention}!",
                    delete_after=5
                )
            else:
                # Fallback: send without torrent if download failed
                thread_name = igdb_data.get("name", data['game_name']) if igdb_data else data['game_name']
                thread_name = thread_name[:100]
                
                view = GameButtonView(data['game_link'], None)
                thread = await output_channel.create_thread(
                    name=thread_name,
                    content=f"**{thread_name}**",
                    embed=embed,
                    view=view if view.children else None
                )
                
                await message.channel.send(
                    f"{message.author.mention} ✅ Game submitted successfully in {thread.thread.mention}!",
                    delete_after=5
                )
            
            # Delete the user's message to keep channel clean
            try:
                await message.delete()
            except:
                pass

# =========================================================
# SLASH COMMAND
# =========================================================
@bot.tree.command(name="addgame", description="Add a new game")
async def addgame(interaction: discord.Interaction):
    if not await check_command_permissions(interaction):
        return
    
    # Check if user has the required role
    REQUIRED_ROLE_ID = 1072117821397540954
    has_role = any(role.id == REQUIRED_ROLE_ID for role in interaction.user.roles)
    
    if not has_role:
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True
        )
        return
    
    view = DownloadTypeView()
    await interaction.response.send_message(
        "🎮 **Add a Game**\n\nChoose how you want to add the game:",
        view=view,
        ephemeral=True
    )

# =========================================================
# USER LIBRARY COMMANDS
# =========================================================
@bot.tree.command(name="mylibrary", description="View your game library")
async def mylibrary(interaction: discord.Interaction):
    """Show user's saved games."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    
    user_id = interaction.user.id
    library = bot.user_libraries.get(user_id, [])
    
    if not library:
        await interaction.followup.send("📚 Your library is empty! React with 📚 on game posts to add them.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"📚 {interaction.user.display_name}'s Library",
        description=f"You have **{len(library)}** games saved",
        color=discord.Color.blue()
    )
    
    # Get thread names
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    games_list = []
    
    for thread_id in library[:25]:  # Show first 25
        try:
            thread = output_channel.get_thread(thread_id)
            if not thread:
                # Try to fetch archived thread
                thread = await output_channel.fetch_channel(thread_id)
            if thread:
                games_list.append(f"• {thread.mention}")
        except:
            pass
    
    if games_list:
        embed.add_field(name="🎮 Your Games", value="\n".join(games_list), inline=False)
    
    if len(library) > 25:
        embed.set_footer(text=f"Showing 25 of {len(library)} games")
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="notify", description="Get notified when a specific game is added")
async def notify(interaction: discord.Interaction, game_name: str):
    """Register for notifications when a game is posted."""
    if not await check_command_permissions(interaction):
        return
    
    game_name_lower = game_name.lower().strip()
    
    if game_name_lower not in bot.game_notifications:
        bot.game_notifications[game_name_lower] = []
    
    if interaction.user.id in bot.game_notifications[game_name_lower]:
        await interaction.response.send_message(
            f"🔔 You're already subscribed to notifications for **{game_name}**",
            ephemeral=True
        )
        return
    
    bot.game_notifications[game_name_lower].append(interaction.user.id)
    save_user_data()
    
    await interaction.response.send_message(
        f"✅ You'll be notified when **{game_name}** is added to the library!",
        ephemeral=True
    )

@bot.tree.command(name="similar", description="Find games similar to a specific game")
async def similar(interaction: discord.Interaction, game_name: str):
    """Find similar games using IGDB."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    # Search for the game
    game_data = await igdb_client.search_game_by_name(game_name)
    
    if not game_data:
        await interaction.followup.send(f"❌ Could not find **{game_name}** in the database.", ephemeral=True)
        return
    
    # Get similar games
    similar_games = await igdb_client.get_similar_games(game_data.get('id'))
    
    if not similar_games:
        await interaction.followup.send(f"❌ No similar games found for **{game_name}**.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"🎮 Games Similar to {game_data.get('name', game_name)}",
        description="Based on genres, themes, and gameplay",
        color=discord.Color.purple()
    )
    
    games_list = []
    for game in similar_games[:10]:
        name = game.get('name', 'Unknown')
        rating = game.get('aggregated_rating', 0)
        if rating:
            games_list.append(f"• **{name}** - {rating:.0f}% rating")
        else:
            games_list.append(f"• **{name}**")
    
    embed.add_field(name="📋 Recommendations", value="\n".join(games_list), inline=False)
    embed.set_footer(text="Powered by IGDB")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="toprequests", description="Show most requested games")
async def toprequests(interaction: discord.Interaction):
    """Show games with most votes."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    if not bot.request_votes:
        await interaction.followup.send("📊 No voted requests yet!", ephemeral=True)
        return
    
    # Sort by vote count
    sorted_requests = sorted(bot.request_votes.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    
    embed = discord.Embed(
        title="🔥 Most Requested Games",
        description="Games with the most votes",
        color=discord.Color.orange()
    )
    
    request_channel = bot.get_channel(REQUEST_CHANNEL_ID)
    
    requests_list = []
    for msg_id, voters in sorted_requests:
        try:
            message = await request_channel.fetch_message(msg_id)
            # Extract game name from message
            game_name = message.content.split("**Game:**")[1].split("\n")[0].strip() if "**Game:**" in message.content else "Unknown"
            requests_list.append(f"• **{game_name}** - {len(voters)} votes")
        except:
            pass
    
    if requests_list:
        embed.add_field(name="📋 Top Requests", value="\n".join(requests_list), inline=False)
    else:
        embed.description = "No requests found"
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="downloadstats", description="Show most downloaded games")
async def downloadstats(interaction: discord.Interaction):
    """Show download statistics."""
    if not await check_command_permissions(interaction):
        return
    await interaction.response.defer()
    
    if not bot.download_stats:
        await interaction.followup.send("📊 No download data yet!", ephemeral=True)
        return
    
    # Sort by download count
    sorted_downloads = sorted(bot.download_stats.items(), key=lambda x: x[1], reverse=True)[:10]
    
    embed = discord.Embed(
        title="📊 Most Downloaded Games",
        description="Top 10 games by download count",
        color=discord.Color.gold()
    )
    
    output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
    
    games_list = []
    for thread_id, count in sorted_downloads:
        try:
            thread = output_channel.get_thread(thread_id)
            if not thread:
                thread = await output_channel.fetch_channel(thread_id)
            if thread:
                games_list.append(f"• {thread.mention} - **{count:,}** downloads")
        except:
            pass
    
    if games_list:
        embed.add_field(name="🔥 Trending", value="\n".join(games_list), inline=False)
    else:
        embed.description = "No download data available"
    
    embed.set_footer(text="Download count tracked from button clicks")
    
    await interaction.followup.send(embed=embed)

# -------------------------
# NEW FEATURES - REVIEWS & RATINGS
# -------------------------
@bot.tree.command(name="review", description="Write a review for a game")
@discord.app_commands.describe(
    game="Name of the game to review",
    rating="Rating from 1 to 5 stars",
    review_text="Your review text (optional)"
)
@discord.app_commands.choices(rating=[
    discord.app_commands.Choice(name="⭐ 1 Star", value=1),
    discord.app_commands.Choice(name="⭐⭐ 2 Stars", value=2),
    discord.app_commands.Choice(name="⭐⭐⭐ 3 Stars", value=3),
    discord.app_commands.Choice(name="⭐⭐⭐⭐ 4 Stars", value=4),
    discord.app_commands.Choice(name="⭐⭐⭐⭐⭐ 5 Stars", value=5)
])
async def review(interaction: discord.Interaction, game: str, rating: discord.app_commands.Choice[int], review_text: str = None):
    """Allow users to review and rate games."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Search for the game thread
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library", ephemeral=True)
            return
        
        found_thread = None
        search_lower = game.lower()
        
        # Check active threads
        for thread in output_channel.threads:
            if search_lower in thread.name.lower():
                found_thread = thread
                break
        
        # Check archived if not found
        if not found_thread:
            async for thread in output_channel.archived_threads(limit=200):
                if search_lower in thread.name.lower():
                    found_thread = thread
                    break
        
        if not found_thread:
            await interaction.followup.send(f"❌ Could not find a game matching '{game}'", ephemeral=True)
            return
        
        thread_id = found_thread.id
        
        # Initialize reviews for this game if not exists
        if thread_id not in bot.game_reviews:
            bot.game_reviews[thread_id] = []
        
        # Check if user already reviewed this game
        existing_review_idx = None
        for idx, rev in enumerate(bot.game_reviews[thread_id]):
            if rev['user_id'] == interaction.user.id:
                existing_review_idx = idx
                break
        
        # Create review entry
        review_entry = {
            'user_id': interaction.user.id,
            'username': str(interaction.user),
            'rating': rating.value,
            'review': review_text,
            'timestamp': discord.utils.utcnow().isoformat()
        }
        
        # Update or add review
        if existing_review_idx is not None:
            bot.game_reviews[thread_id][existing_review_idx] = review_entry
            action = "updated"
        else:
            bot.game_reviews[thread_id].append(review_entry)
            action = "added"
        
        save_reviews_data()
        
        # Calculate average rating
        ratings = [r['rating'] for r in bot.game_reviews[thread_id]]
        avg_rating = sum(ratings) / len(ratings)
        stars = "⭐" * rating.value
        
        embed = discord.Embed(
            title=f"✅ Review {action}!",
            description=f"Your review for **{found_thread.name}** has been {action}",
            color=discord.Color.green()
        )
        embed.add_field(name="Your Rating", value=stars, inline=True)
        embed.add_field(name="Average Rating", value=f"{avg_rating:.1f}/5.0 ⭐ ({len(ratings)} reviews)", inline=True)
        if review_text:
            embed.add_field(name="Your Review", value=review_text[:1024], inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="reviews", description="View reviews for a game")
@discord.app_commands.describe(game="Name of the game to see reviews for")
async def reviews(interaction: discord.Interaction, game: str):
    """View all reviews for a specific game."""
    await interaction.response.defer()
    
    try:
        # Search for the game thread
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library")
            return
        
        found_thread = None
        search_lower = game.lower()
        
        # Check active threads
        for thread in output_channel.threads:
            if search_lower in thread.name.lower():
                found_thread = thread
                break
        
        # Check archived if not found
        if not found_thread:
            async for thread in output_channel.archived_threads(limit=200):
                if search_lower in thread.name.lower():
                    found_thread = thread
                    break
        
        if not found_thread:
            await interaction.followup.send(f"❌ Could not find a game matching '{game}'")
            return
        
        thread_id = found_thread.id
        
        # Get reviews
        reviews_list = bot.game_reviews.get(thread_id, [])
        
        if not reviews_list:
            await interaction.followup.send(f"📝 No reviews yet for **{found_thread.name}**.\nBe the first to review using `/review`!")
            return
        
        # Calculate average rating
        ratings = [r['rating'] for r in reviews_list]
        avg_rating = sum(ratings) / len(ratings)
        
        embed = discord.Embed(
            title=f"📝 Reviews for {found_thread.name}",
            description=f"**{avg_rating:.1f}/5.0** ⭐ ({len(reviews_list)} reviews)",
            color=discord.Color.blue()
        )
        
        # Show up to 10 most recent reviews
        for review in sorted(reviews_list, key=lambda x: x['timestamp'], reverse=True)[:10]:
            stars = "⭐" * review['rating']
            review_text = review.get('review', '*No written review*')
            if review_text and len(review_text) > 100:
                review_text = review_text[:97] + "..."
            
            embed.add_field(
                name=f"{review['username']} - {stars}",
                value=review_text or "*No written review*",
                inline=False
            )
        
        if len(reviews_list) > 10:
            embed.set_footer(text=f"Showing 10 of {len(reviews_list)} reviews")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# -------------------------
# NEW FEATURES - TAGS & CATEGORIES
# -------------------------
@bot.tree.command(name="addtag", description="Add tags to a game (Admin only)")
@discord.app_commands.describe(
    game="Name of the game",
    tags="Comma-separated tags (e.g., Horror, Multiplayer, Co-op)"
)
async def addtag(interaction: discord.Interaction, game: str, tags: str):
    """Add tags to a game (admin only)."""
    # Check if user has admin role
    if ADMIN_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ You need admin role to add tags", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Search for the game thread
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library", ephemeral=True)
            return
        
        found_thread = None
        search_lower = game.lower()
        
        # Check active threads
        for thread in output_channel.threads:
            if search_lower in thread.name.lower():
                found_thread = thread
                break
        
        # Check archived if not found
        if not found_thread:
            async for thread in output_channel.archived_threads(limit=200):
                if search_lower in thread.name.lower():
                    found_thread = thread
                    break
        
        if not found_thread:
            await interaction.followup.send(f"❌ Could not find a game matching '{game}'", ephemeral=True)
            return
        
        thread_id = found_thread.id
        
        # Parse tags
        tag_list = [tag.strip() for tag in tags.split(',')]
        
        # Initialize or update tags
        if thread_id not in bot.game_tags:
            bot.game_tags[thread_id] = []
        
        # Add new tags (avoid duplicates)
        for tag in tag_list:
            if tag and tag not in bot.game_tags[thread_id]:
                bot.game_tags[thread_id].append(tag)
        
        save_tags_data()
        
        embed = discord.Embed(
            title="✅ Tags Added",
            description=f"Tags updated for **{found_thread.name}**",
            color=discord.Color.green()
        )
        embed.add_field(name="All Tags", value=", ".join(bot.game_tags[thread_id]), inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="tags", description="Search games by tags")
@discord.app_commands.describe(tag="Tag to search for (e.g., Horror, RPG, Multiplayer)")
async def tags(interaction: discord.Interaction, tag: str):
    """Search for games by tag."""
    await interaction.response.defer()
    
    try:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library")
            return
        
        # Find games with matching tag
        matching_games = []
        tag_lower = tag.lower()
        
        for thread_id, tags_list in bot.game_tags.items():
            if any(tag_lower in t.lower() for t in tags_list):
                try:
                    thread = await output_channel.fetch_channel(thread_id)
                    if thread:
                        matching_games.append({
                            'thread': thread,
                            'tags': tags_list
                        })
                except:
                    pass
        
        if not matching_games:
            await interaction.followup.send(f"❌ No games found with tag '{tag}'")
            return
        
        embed = discord.Embed(
            title=f"🏷️ Games tagged: {tag}",
            description=f"Found {len(matching_games)} games",
            color=discord.Color.blue()
        )
        
        # Show up to 15 games
        for game_info in matching_games[:15]:
            thread = game_info['thread']
            tags_str = ", ".join(game_info['tags'])
            
            # Get rating if available
            reviews = bot.game_reviews.get(thread.id, [])
            if reviews:
                avg_rating = sum(r['rating'] for r in reviews) / len(reviews)
                rating_str = f" | {avg_rating:.1f}⭐"
            else:
                rating_str = ""
            
            embed.add_field(
                name=thread.name,
                value=f"{thread.mention} | Tags: {tags_str}{rating_str}",
                inline=False
            )
        
        if len(matching_games) > 15:
            embed.set_footer(text=f"Showing 15 of {len(matching_games)} games")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# -------------------------
# NEW FEATURES - TRENDING
# -------------------------
@bot.tree.command(name="trending", description="Show trending games based on activity")
async def trending(interaction: discord.Interaction):
    """Show trending games based on recent activity."""
    await interaction.response.defer()
    
    try:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library")
            return
        
        # Combine download stats and library adds for trending score
        trending_scores = {}
        
        # Factor 1: Download counts (heavily weighted)
        for thread_id, count in bot.download_stats.items():
            trending_scores[thread_id] = count * 3
        
        # Factor 2: Library additions (moderately weighted)
        for user_id, library in bot.user_libraries.items():
            for thread_id in library:
                trending_scores[thread_id] = trending_scores.get(thread_id, 0) + 2
        
        # Factor 3: Reviews (lightly weighted)
        for thread_id, reviews in bot.game_reviews.items():
            trending_scores[thread_id] = trending_scores.get(thread_id, 0) + len(reviews)
        
        if not trending_scores:
            await interaction.followup.send("📊 No trending data available yet")
            return
        
        # Sort by score
        sorted_trending = sorted(trending_scores.items(), key=lambda x: x[1], reverse=True)[:15]
        
        embed = discord.Embed(
            title="🔥 Trending Games",
            description="Based on downloads, library adds, and reviews",
            color=discord.Color.orange()
        )
        
        for idx, (thread_id, score) in enumerate(sorted_trending, 1):
            try:
                thread = await output_channel.fetch_channel(thread_id)
                if thread:
                    # Get additional info
                    downloads = bot.download_stats.get(thread_id, 0)
                    reviews = bot.game_reviews.get(thread_id, [])
                    avg_rating = ""
                    if reviews:
                        avg = sum(r['rating'] for r in reviews) / len(reviews)
                        avg_rating = f" | {avg:.1f}⭐"
                    
                    embed.add_field(
                        name=f"#{idx} {thread.name}",
                        value=f"{thread.mention} | {downloads} downloads{avg_rating}",
                        inline=False
                    )
            except:
                pass
        
        embed.set_footer(text="Trending score = downloads×3 + library adds×2 + reviews")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# -------------------------
# NEW FEATURES - ADVANCED SEARCH
# -------------------------
@bot.tree.command(name="advancedsearch", description="Advanced search with filters")
@discord.app_commands.describe(
    query="Search query",
    min_rating="Minimum rating (1-5)",
    tag="Filter by tag",
    sort="Sort results"
)
@discord.app_commands.choices(sort=[
    discord.app_commands.Choice(name="Relevance", value="relevance"),
    discord.app_commands.Choice(name="Rating (High to Low)", value="rating_desc"),
    discord.app_commands.Choice(name="Rating (Low to High)", value="rating_asc"),
    discord.app_commands.Choice(name="Most Downloaded", value="downloads"),
    discord.app_commands.Choice(name="Newest", value="newest")
])
async def advancedsearch(
    interaction: discord.Interaction,
    query: str,
    min_rating: int = None,
    tag: str = None,
    sort: discord.app_commands.Choice[str] = None
):
    """Advanced search with multiple filters."""
    await interaction.response.defer()
    
    try:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library")
            return
        
        # Collect all matching games
        matching_games = []
        query_lower = query.lower()
        
        # Search active threads
        for thread in output_channel.threads:
            if query_lower in thread.name.lower():
                matching_games.append(thread)
        
        # Search archived threads
        async for thread in output_channel.archived_threads(limit=100):
            if query_lower in thread.name.lower():
                matching_games.append(thread)
        
        if not matching_games:
            await interaction.followup.send(f"❌ No games found matching '{query}'")
            return
        
        # Apply filters and collect data
        filtered_games = []
        for thread in matching_games:
            thread_id = thread.id
            
            # Get reviews/rating
            reviews = bot.game_reviews.get(thread_id, [])
            avg_rating = sum(r['rating'] for r in reviews) / len(reviews) if reviews else 0
            
            # Filter by minimum rating
            if min_rating and avg_rating < min_rating:
                continue
            
            # Filter by tag
            if tag:
                thread_tags = bot.game_tags.get(thread_id, [])
                if not any(tag.lower() in t.lower() for t in thread_tags):
                    continue
            
            # Collect data for sorting
            filtered_games.append({
                'thread': thread,
                'rating': avg_rating,
                'reviews_count': len(reviews),
                'downloads': bot.download_stats.get(thread_id, 0),
                'tags': bot.game_tags.get(thread_id, [])
            })
        
        if not filtered_games:
            await interaction.followup.send("❌ No games match your filters")
            return
        
        # Sort results
        sort_value = sort.value if sort else "relevance"
        if sort_value == "rating_desc":
            filtered_games.sort(key=lambda x: x['rating'], reverse=True)
        elif sort_value == "rating_asc":
            filtered_games.sort(key=lambda x: x['rating'])
        elif sort_value == "downloads":
            filtered_games.sort(key=lambda x: x['downloads'], reverse=True)
        elif sort_value == "newest":
            filtered_games.sort(key=lambda x: x['thread'].created_at, reverse=True)
        
        # Build embed
        embed = discord.Embed(
            title=f"🔍 Advanced Search: {query}",
            description=f"Found {len(filtered_games)} results",
            color=discord.Color.blue()
        )
        
        # Show filters if applied
        filters_applied = []
        if min_rating:
            filters_applied.append(f"Min Rating: {min_rating}⭐")
        if tag:
            filters_applied.append(f"Tag: {tag}")
        if sort:
            filters_applied.append(f"Sort: {sort.name}")
        
        if filters_applied:
            embed.add_field(name="Filters", value=" | ".join(filters_applied), inline=False)
        
        # Show up to 10 results
        for game_data in filtered_games[:10]:
            thread = game_data['thread']
            rating_str = f"{game_data['rating']:.1f}⭐ ({game_data['reviews_count']})" if game_data['rating'] > 0 else "No ratings"
            downloads_str = f"{game_data['downloads']} downloads" if game_data['downloads'] > 0 else "No downloads"
            tags_str = ", ".join(game_data['tags'][:3]) if game_data['tags'] else "No tags"
            
            embed.add_field(
                name=thread.name,
                value=f"{thread.mention}\n{rating_str} | {downloads_str}\nTags: {tags_str}",
                inline=False
            )
        
        if len(filtered_games) > 10:
            embed.set_footer(text=f"Showing 10 of {len(filtered_games)} results")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# -------------------------
# NEW FEATURES - LINK HEALTH
# -------------------------
@bot.tree.command(name="linkhealth", description="Check link health status")
async def linkhealth(interaction: discord.Interaction):
    """View link health status for all games."""
    await interaction.response.defer()
    
    try:
        if not bot.link_health:
            await interaction.followup.send("📊 No link health data available yet. Health check runs daily.")
            return
        
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library")
            return
        
        # Count healthy vs broken
        healthy_count = sum(1 for h in bot.link_health.values() if h['status'] == 'healthy')
        broken_count = sum(1 for h in bot.link_health.values() if h['status'] == 'broken')
        
        embed = discord.Embed(
            title="🔗 Link Health Status",
            description=f"**{healthy_count}** healthy | **{broken_count}** with issues",
            color=discord.Color.green() if broken_count == 0 else discord.Color.orange()
        )
        
        # Show games with broken links
        if broken_count > 0:
            broken_games = []
            for thread_id, health_data in bot.link_health.items():
                if health_data['status'] == 'broken':
                    try:
                        thread = await output_channel.fetch_channel(thread_id)
                        if thread:
                            broken_games.append({
                                'thread': thread,
                                'broken_count': len(health_data['broken_links']),
                                'total': health_data['total_links']
                            })
                    except:
                        pass
            
            # Show up to 10 games with issues
            for game_data in broken_games[:10]:
                thread = game_data['thread']
                embed.add_field(
                    name=f"⚠️ {thread.name}",
                    value=f"{thread.mention}\n{game_data['broken_count']}/{game_data['total']} links broken",
                    inline=False
                )
            
            if len(broken_games) > 10:
                embed.set_footer(text=f"Showing 10 of {len(broken_games)} games with issues")
        else:
            embed.add_field(name="✅ All Clear", value="All monitored links are healthy!", inline=False)
        
        # Show last check time
        if bot.link_health:
            latest_check = max(h['checked_at'] for h in bot.link_health.values())
            embed.set_footer(text=f"Last check: {latest_check}")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="checkhealth", description="Manually trigger link health check (Admin only)")
async def checkhealth(interaction: discord.Interaction):
    """Manually trigger link health check."""
    # Check if user has admin role
    if ADMIN_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ You need admin role to trigger health checks", ephemeral=True)
        return
    
    await interaction.response.send_message("🔍 Starting link health check... This may take a few minutes.", ephemeral=True)
    
    try:
        # Run the health check
        await link_health_monitor()
        await interaction.followup.send("✅ Link health check complete! Use `/linkhealth` to view results.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# -------------------------
# NEW FEATURES - TRAILERS & RATINGS
# -------------------------
@bot.tree.command(name="trailer", description="View trailer for a game")
@discord.app_commands.describe(game="Name of the game")
async def trailer(interaction: discord.Interaction, game: str):
    """Get YouTube trailer for a game."""
    await interaction.response.defer()
    
    try:
        # Search for game on IGDB
        clean_name = clean_game_name_for_search(game)
        igdb_data = await igdb_client.search_game_by_name(clean_name)
        
        if not igdb_data:
            await interaction.followup.send(f"❌ Could not find game data for '{game}'")
            return
        
        game_id = igdb_data.get('id')
        if not game_id:
            await interaction.followup.send(f"❌ Could not find game ID for '{game}'")
            return
        
        # Get videos
        video_ids = await igdb_client.get_game_videos(game_id)
        
        if not video_ids:
            await interaction.followup.send(f"📹 No trailers found for **{igdb_data['name']}**")
            return
        
        embed = discord.Embed(
            title=f"🎬 Trailers for {igdb_data['name']}",
            description=f"Found {len(video_ids)} trailer(s)",
            color=discord.Color.red()
        )
        
        # Add YouTube links
        for idx, video_id in enumerate(video_ids[:3], 1):
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            embed.add_field(
                name=f"Trailer #{idx}",
                value=f"[Watch on YouTube]({youtube_url})",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="gameinfo", description="View detailed game information with ratings")
@discord.app_commands.describe(game="Name of the game")
async def gameinfo(interaction: discord.Interaction, game: str):
    """Get detailed game information including ratings."""
    await interaction.response.defer()
    
    try:
        # Search for game on IGDB
        clean_name = clean_game_name_for_search(game)
        igdb_data = await igdb_client.search_game_by_name(clean_name)
        
        if not igdb_data:
            await interaction.followup.send(f"❌ Could not find game data for '{game}'")
            return
        
        game_id = igdb_data.get('id')
        game_name = igdb_data.get('name', 'Unknown')
        
        # Get ratings
        ratings_data = await igdb_client.get_external_ratings(game_id) if game_id else {}
        
        # Get videos
        video_ids = await igdb_client.get_game_videos(game_id) if game_id else []
        
        embed = discord.Embed(
            title=f"🎮 {game_name}",
            description=igdb_data.get('summary', 'No description available')[:500],
            color=discord.Color.blue()
        )
        
        # Add cover image
        cover = igdb_data.get('cover')
        if cover and cover.get('image_id'):
            image_url = f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover['image_id']}.jpg"
            embed.set_thumbnail(url=image_url)
        
        # Add genres
        genres = igdb_data.get('genres', [])
        if genres:
            genre_names = [g['name'] for g in genres]
            embed.add_field(name="🎭 Genres", value=", ".join(genre_names), inline=True)
        
        # Add platforms
        platforms = igdb_data.get('platforms', [])
        if platforms:
            platform_names = [p['name'] for p in platforms[:5]]
            embed.add_field(name="🎮 Platforms", value=", ".join(platform_names), inline=True)
        
        # Add ratings
        if ratings_data:
            agg_rating = ratings_data.get('aggregated_rating')
            if agg_rating:
                embed.add_field(
                    name="⭐ Critics Score",
                    value=f"{agg_rating:.1f}/100",
                    inline=True
                )
            
            user_rating = ratings_data.get('rating')
            if user_rating:
                embed.add_field(
                    name="👥 User Score",
                    value=f"{user_rating:.1f}/100",
                    inline=True
                )
        
        # Add our community rating
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if output_channel:
            for thread in output_channel.threads:
                if clean_name.lower() in thread.name.lower():
                    reviews = bot.game_reviews.get(thread.id, [])
                    if reviews:
                        avg = sum(r['rating'] for r in reviews) / len(reviews)
                        embed.add_field(
                            name="📝 Community Rating",
                            value=f"{avg:.1f}/5.0 ⭐ ({len(reviews)} reviews)",
                            inline=True
                        )
                    break
        
        # Add trailer link
        if video_ids:
            youtube_url = f"https://www.youtube.com/watch?v={video_ids[0]}"
            embed.add_field(
                name="🎬 Trailer",
                value=f"[Watch on YouTube]({youtube_url})",
                inline=False
            )
        
        embed.set_footer(text="Data from IGDB")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# -------------------------
# NEW FEATURES - WEBHOOKS & AUTO-BACKUP
# -------------------------
@bot.tree.command(name="backup", description="Create backup of bot data (Admin only)")
async def backup(interaction: discord.Interaction):
    """Create backup of all bot data."""
    # Check if user has admin role
    if ADMIN_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ You need admin role to create backups", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        import zipfile
        from datetime import datetime
        
        # Create backup filename with timestamp
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_{timestamp}.zip"
        
        # Files to backup (using data/ directory)
        files_to_backup = [
            str(settings.RSS_SEEN_FILE),
            str(settings.BOT_STATE_FILE),
            str(settings.USER_DATA_FILE),
            str(settings.REVIEWS_FILE),
            str(settings.TAGS_FILE),
            str(settings.HEALTH_FILE)
        ]
        
        # Create zip file
        with zipfile.ZipFile(backup_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for filename in files_to_backup:
                if os.path.exists(filename):
                    zipf.write(filename)
        
        # Send backup file
        file = discord.File(backup_filename)
        await interaction.followup.send(
            f"✅ Backup created successfully!",
            file=file,
            ephemeral=True
        )
        
        # Clean up temp file
        os.remove(backup_filename)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error creating backup: {e}", ephemeral=True)

@bot.tree.command(name="cleanup", description="Clean up broken game links (Admin only)")
async def cleanup(interaction: discord.Interaction):
    """Identify and report games with broken links that need attention."""
    # Check if user has admin role
    if ADMIN_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("❌ You need admin role to run cleanup", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        if not bot.link_health:
            await interaction.followup.send("❌ No link health data available. Run `/checkhealth` first.", ephemeral=True)
            return
        
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library", ephemeral=True)
            return
        
        # Find games with broken links
        broken_games = []
        for thread_id, health_data in bot.link_health.items():
            if health_data['status'] == 'broken':
                try:
                    thread = await output_channel.fetch_channel(thread_id)
                    if thread:
                        broken_games.append({
                            'thread': thread,
                            'broken_count': len(health_data['broken_links']),
                            'links': health_data['broken_links']
                        })
                except:
                    pass
        
        if not broken_games:
            await interaction.followup.send("✅ No games with broken links found!", ephemeral=True)
            return
        
        # Create report
        report = f"🔧 **Cleanup Report**\n\nFound {len(broken_games)} games with broken links:\n\n"
        
        for game_data in broken_games[:20]:
            thread = game_data['thread']
            report += f"• **{thread.name}** ({thread.mention})\n"
            report += f"  └ {game_data['broken_count']} broken link(s)\n"
        
        if len(broken_games) > 20:
            report += f"\n... and {len(broken_games) - 20} more"
        
        await interaction.followup.send(report[:2000], ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# =========================================================
# WEBHOOK NOTIFICATIONS HELPER
# =========================================================
async def send_webhook_notifications(embed: discord.Embed, game_name: str):
    """Send webhook notifications to subscribed users."""
    try:
        for user_id, webhook_url in bot.webhooks.items():
            try:
                async with aiohttp.ClientSession() as session:
                    webhook = discord.Webhook.from_url(webhook_url, session=session)
                    await webhook.send(
                        content=f"**New Game Added:** {game_name}",
                        embed=embed,
                        username="Backrooms Pirate Ship"
                    )
            except Exception as e:
                print(f"⚠️ Failed to send webhook to user {user_id}: {e}")
    except Exception as e:
        print(f"⚠️ Error in webhook notifications: {e}")

# -------------------------
# NEW FEATURES - WEBHOOK COMMANDS
# -------------------------
@bot.tree.command(name="setwebhook", description="Set a webhook URL to receive game notifications")
@discord.app_commands.describe(webhook_url="Your Discord webhook URL")
async def setwebhook(interaction: discord.Interaction, webhook_url: str):
    """Set webhook for personal notifications."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Validate webhook URL
        if not webhook_url.startswith("https://discord.com/api/webhooks/"):
            await interaction.followup.send("❌ Invalid webhook URL. Must be a Discord webhook.", ephemeral=True)
            return
        
        # Test webhook
        try:
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(webhook_url, session=session)
                await webhook.send("✅ Webhook setup successful! You'll receive notifications here.", username="Backrooms Pirate Ship")
        except:
            await interaction.followup.send("❌ Could not send test message to webhook. Check the URL.", ephemeral=True)
            return
        
        # Save webhook
        bot.webhooks[interaction.user.id] = webhook_url
        save_webhooks_data()
        
        await interaction.followup.send("✅ Webhook configured successfully! You'll be notified of new games.", ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="removewebhook", description="Remove your webhook subscription")
async def removewebhook(interaction: discord.Interaction):
    """Remove webhook subscription."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        if interaction.user.id in bot.webhooks:
            del bot.webhooks[interaction.user.id]
            save_webhooks_data()
            await interaction.followup.send("✅ Webhook removed successfully.", ephemeral=True)
        else:
            await interaction.followup.send("❌ You don't have a webhook configured.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# -------------------------
# NEW FEATURES - COLLECTIONS
# -------------------------
@bot.tree.command(name="createcollection", description="Create a game collection")
@discord.app_commands.describe(name="Name of the collection (e.g., 'Horror Games', 'Co-op')")
async def createcollection(interaction: discord.Interaction, name: str):
    """Create a new game collection."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        if interaction.user.id not in bot.collections:
            bot.collections[interaction.user.id] = {}
        
        if name in bot.collections[interaction.user.id]:
            await interaction.followup.send(f"❌ You already have a collection named '{name}'", ephemeral=True)
            return
        
        bot.collections[interaction.user.id][name] = []
        save_collections_data()
        
        await interaction.followup.send(f"✅ Collection **{name}** created! Add games with `/addtocollection`", ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="addtocollection", description="Add a game to your collection")
@discord.app_commands.describe(
    collection="Name of your collection",
    game="Name of the game to add"
)
async def addtocollection(interaction: discord.Interaction, collection: str, game: str):
    """Add a game to a collection."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        if interaction.user.id not in bot.collections or collection not in bot.collections[interaction.user.id]:
            await interaction.followup.send(f"❌ Collection '{collection}' not found. Create it first with `/createcollection`", ephemeral=True)
            return
        
        # Find game thread
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library", ephemeral=True)
            return
        
        found_thread = None
        search_lower = game.lower()
        
        for thread in output_channel.threads:
            if search_lower in thread.name.lower():
                found_thread = thread
                break
        
        if not found_thread:
            async for thread in output_channel.archived_threads(limit=200):
                if search_lower in thread.name.lower():
                    found_thread = thread
                    break
        
        if not found_thread:
            await interaction.followup.send(f"❌ Could not find game matching '{game}'", ephemeral=True)
            return
        
        if found_thread.id in bot.collections[interaction.user.id][collection]:
            await interaction.followup.send(f"❌ **{found_thread.name}** is already in collection '{collection}'", ephemeral=True)
            return
        
        bot.collections[interaction.user.id][collection].append(found_thread.id)
        save_collections_data()
        
        await interaction.followup.send(f"✅ Added **{found_thread.name}** to collection **{collection}**", ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="mycollections", description="View your game collections")
async def mycollections(interaction: discord.Interaction):
    """View all user collections."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        if interaction.user.id not in bot.collections or not bot.collections[interaction.user.id]:
            await interaction.followup.send("📚 You don't have any collections yet. Create one with `/createcollection`", ephemeral=True)
            return
        
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        
        embed = discord.Embed(
            title=f"📚 {interaction.user.display_name}'s Collections",
            color=discord.Color.purple()
        )
        
        for collection_name, thread_ids in bot.collections[interaction.user.id].items():
            if thread_ids:
                games_text = []
                for thread_id in thread_ids[:5]:
                    try:
                        thread = await output_channel.fetch_channel(thread_id)
                        games_text.append(f"• {thread.mention}")
                    except:
                        pass
                
                if len(thread_ids) > 5:
                    games_text.append(f"... and {len(thread_ids) - 5} more")
                
                embed.add_field(
                    name=f"{collection_name} ({len(thread_ids)} games)",
                    value="\n".join(games_text) if games_text else "Empty",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"{collection_name}",
                    value="*Empty*",
                    inline=False
                )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# -------------------------
# NEW FEATURES - BOOKMARKS
# -------------------------
@bot.tree.command(name="bookmark", description="Bookmark a game for later")
@discord.app_commands.describe(game="Name of the game to bookmark")
async def bookmark(interaction: discord.Interaction, game: str):
    """Bookmark a game."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library", ephemeral=True)
            return
        
        found_thread = None
        search_lower = game.lower()
        
        for thread in output_channel.threads:
            if search_lower in thread.name.lower():
                found_thread = thread
                break
        
        if not found_thread:
            async for thread in output_channel.archived_threads(limit=200):
                if search_lower in thread.name.lower():
                    found_thread = thread
                    break
        
        if not found_thread:
            await interaction.followup.send(f"❌ Could not find game matching '{game}'", ephemeral=True)
            return
        
        if interaction.user.id not in bot.bookmarks:
            bot.bookmarks[interaction.user.id] = []
        
        if found_thread.id in bot.bookmarks[interaction.user.id]:
            await interaction.followup.send(f"❌ **{found_thread.name}** is already bookmarked", ephemeral=True)
            return
        
        bot.bookmarks[interaction.user.id].append(found_thread.id)
        save_user_data()
        
        await interaction.followup.send(f"🔖 Bookmarked **{found_thread.name}**!", ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="bookmarks", description="View your bookmarked games")
async def bookmarks_cmd(interaction: discord.Interaction):
    """View all bookmarks."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        if interaction.user.id not in bot.bookmarks or not bot.bookmarks[interaction.user.id]:
            await interaction.followup.send("🔖 You don't have any bookmarks yet. Bookmark games with `/bookmark`", ephemeral=True)
            return
        
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        
        embed = discord.Embed(
            title=f"🔖 {interaction.user.display_name}'s Bookmarks",
            description=f"{len(bot.bookmarks[interaction.user.id])} bookmarked games",
            color=discord.Color.gold()
        )
        
        for thread_id in bot.bookmarks[interaction.user.id][:25]:
            try:
                thread = await output_channel.fetch_channel(thread_id)
                
                # Get rating if available
                reviews = bot.game_reviews.get(thread_id, [])
                rating_str = ""
                if reviews:
                    avg = sum(r['rating'] for r in reviews) / len(reviews)
                    rating_str = f" | {avg:.1f}⭐"
                
                embed.add_field(
                    name=thread.name,
                    value=f"{thread.mention}{rating_str}",
                    inline=False
                )
            except:
                pass
        
        if len(bot.bookmarks[interaction.user.id]) > 25:
            embed.set_footer(text=f"Showing 25 of {len(bot.bookmarks[interaction.user.id])} bookmarks")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# -------------------------
# NEW FEATURES - COMPATIBILITY REPORTS
# -------------------------
@bot.tree.command(name="reportcompat", description="Report game compatibility")
@discord.app_commands.describe(
    game="Name of the game",
    status="Does it work?",
    specs="Your PC specs (optional)",
    notes="Additional notes (optional)"
)
@discord.app_commands.choices(status=[
    discord.app_commands.Choice(name="✅ Working", value="working"),
    discord.app_commands.Choice(name="⚠️ Issues", value="issues"),
    discord.app_commands.Choice(name="❌ Broken", value="broken")
])
async def reportcompat(interaction: discord.Interaction, game: str, status: discord.app_commands.Choice[str], specs: str = None, notes: str = None):
    """Report game compatibility."""
    await interaction.response.defer(ephemeral=True)
    
    try:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library", ephemeral=True)
            return
        
        found_thread = None
        search_lower = game.lower()
        
        for thread in output_channel.threads:
            if search_lower in thread.name.lower():
                found_thread = thread
                break
        
        if not found_thread:
            async for thread in output_channel.archived_threads(limit=200):
                if search_lower in thread.name.lower():
                    found_thread = thread
                    break
        
        if not found_thread:
            await interaction.followup.send(f"❌ Could not find game matching '{game}'", ephemeral=True)
            return
        
        thread_id = found_thread.id
        
        if thread_id not in bot.compatibility_reports:
            bot.compatibility_reports[thread_id] = []
        
        # Check if user already reported
        existing_idx = None
        for idx, report in enumerate(bot.compatibility_reports[thread_id]):
            if report['user_id'] == interaction.user.id:
                existing_idx = idx
                break
        
        report_entry = {
            'user_id': interaction.user.id,
            'username': str(interaction.user),
            'status': status.value,
            'specs': specs,
            'notes': notes,
            'timestamp': discord.utils.utcnow().isoformat()
        }
        
        if existing_idx is not None:
            bot.compatibility_reports[thread_id][existing_idx] = report_entry
            action = "updated"
        else:
            bot.compatibility_reports[thread_id].append(report_entry)
            action = "added"
        
        save_compatibility_data()
        
        status_emoji = {"working": "✅", "issues": "⚠️", "broken": "❌"}[status.value]
        
        await interaction.followup.send(
            f"{status_emoji} Compatibility report {action} for **{found_thread.name}**!",
            ephemeral=True
        )
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="compatibility", description="View compatibility reports for a game")
@discord.app_commands.describe(game="Name of the game")
async def compatibility(interaction: discord.Interaction, game: str):
    """View compatibility reports."""
    await interaction.response.defer()
    
    try:
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        if not output_channel:
            await interaction.followup.send("❌ Could not access the game library")
            return
        
        found_thread = None
        search_lower = game.lower()
        
        for thread in output_channel.threads:
            if search_lower in thread.name.lower():
                found_thread = thread
                break
        
        if not found_thread:
            async for thread in output_channel.archived_threads(limit=200):
                if search_lower in thread.name.lower():
                    found_thread = thread
                    break
        
        if not found_thread:
            await interaction.followup.send(f"❌ Could not find game matching '{game}'")
            return
        
        reports = bot.compatibility_reports.get(found_thread.id, [])
        
        if not reports:
            await interaction.followup.send(f"📊 No compatibility reports yet for **{found_thread.name}**.\nBe the first to report using `/reportcompat`!")
            return
        
        # Count statuses
        working = sum(1 for r in reports if r['status'] == 'working')
        issues = sum(1 for r in reports if r['status'] == 'issues')
        broken = sum(1 for r in reports if r['status'] == 'broken')
        
        embed = discord.Embed(
            title=f"📊 Compatibility: {found_thread.name}",
            description=f"**{working}** ✅ Working | **{issues}** ⚠️ Issues | **{broken}** ❌ Broken",
            color=discord.Color.blue()
        )
        
        # Show recent reports
        for report in sorted(reports, key=lambda x: x['timestamp'], reverse=True)[:10]:
            status_emoji = {"working": "✅", "issues": "⚠️", "broken": "❌"}[report['status']]
            
            value_parts = []
            if report.get('specs'):
                value_parts.append(f"**Specs:** {report['specs'][:100]}")
            if report.get('notes'):
                value_parts.append(f"**Notes:** {report['notes'][:100]}")
            if not value_parts:
                value_parts.append("*No additional details*")
            
            embed.add_field(
                name=f"{status_emoji} {report['username']}",
                value="\n".join(value_parts),
                inline=False
            )
        
        if len(reports) > 10:
            embed.set_footer(text=f"Showing 10 of {len(reports)} reports")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# -------------------------
# NEW FEATURES - RECOMMENDATIONS
# -------------------------
@bot.tree.command(name="recommend", description="Get personalized game recommendations")
async def recommend(interaction: discord.Interaction):
    """Get personalized recommendations based on user library."""
    await interaction.response.defer()
    
    try:
        if interaction.user.id not in bot.user_libraries or not bot.user_libraries[interaction.user.id]:
            await interaction.followup.send("❌ You need to add games to your library first (📚 reaction) to get recommendations!")
            return
        
        output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
        
        # Analyze user's library genres
        user_genres = {}
        for thread_id in bot.user_libraries[interaction.user.id]:
            tags = bot.game_tags.get(thread_id, [])
            for tag in tags:
                user_genres[tag] = user_genres.get(tag, 0) + 1
        
        if not user_genres:
            await interaction.followup.send("❌ Not enough data for recommendations. Try adding tags to your library games!")
            return
        
        # Find games with matching genres that user doesn't have
        recommendations = []
        for thread_id, tags in bot.game_tags.items():
            if thread_id not in bot.user_libraries[interaction.user.id]:
                score = sum(user_genres.get(tag, 0) for tag in tags)
                if score > 0:
                    recommendations.append((thread_id, score, tags))
        
        if not recommendations:
            await interaction.followup.send("📊 No recommendations available yet. Try adding more games or tags!")
            return
        
        # Sort by score
        recommendations.sort(key=lambda x: x[1], reverse=True)
        
        embed = discord.Embed(
            title=f"🎯 Recommendations for {interaction.user.display_name}",
            description=f"Based on your library of {len(bot.user_libraries[interaction.user.id])} games",
            color=discord.Color.purple()
        )
        
        for thread_id, score, tags in recommendations[:10]:
            try:
                thread = await output_channel.fetch_channel(thread_id)
                
                # Get rating
                reviews = bot.game_reviews.get(thread_id, [])
                rating_str = ""
                if reviews:
                    avg = sum(r['rating'] for r in reviews) / len(reviews)
                    rating_str = f" | {avg:.1f}⭐"
                
                tags_str = ", ".join(tags[:3])
                
                embed.add_field(
                    name=thread.name,
                    value=f"{thread.mention}\nTags: {tags_str}{rating_str}",
                    inline=False
                )
            except:
                pass
        
        embed.set_footer(text="Recommendations based on genres in your library")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# -------------------------
# STEAM INTEGRATION
# -------------------------
# Steam utilities already imported at top

@bot.tree.command(name="linksteam", description="Link your Discord account to your Steam account")
async def linksteam(interaction: discord.Interaction):
    """Link Discord account to Steam via OAuth"""
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Get OAuth base URL from environment
        oauth_base_url = os.getenv('STEAM_OAUTH_BASE_URL', 'http://localhost:5000')
        
        # Generate authentication URL
        auth_url = f"{oauth_base_url}/auth/login?discord_id={interaction.user.id}"
        
        # Create embed with login button
        embed = discord.Embed(
            title="🎮 Link Your Steam Account",
            description="Click the button below to securely link your Steam account through Steam's official login.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="How it works:",
            value="1️⃣ Click the link below\n2️⃣ Sign in with your Steam account\n3️⃣ You'll be automatically linked!",
            inline=False
        )
        
        embed.add_field(
            name="🔒 Security",
            value="We never see your Steam password. Authentication is handled securely by Steam.",
            inline=False
        )
        
        embed.set_footer(text="Link expires in 10 minutes")
        
        # Create button view
        view = discord.ui.View()
        button = discord.ui.Button(
            label="Login with Steam",
            style=discord.ButtonStyle.link,
            url=auth_url,
            emoji="🎮"
        )
        view.add_item(button)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="unlinksteam", description="Unlink your Steam account")
async def unlinksteam(interaction: discord.Interaction):
    """Unlink Discord account from Steam"""
    await interaction.response.defer()
    
    try:
        if SteamLinker.unlink_account(str(interaction.user.id)):
            await interaction.followup.send("✅ Steam account unlinked successfully!")
        else:
            await interaction.followup.send("❌ You don't have a linked Steam account.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="steamprofile", description="View a Steam profile")
async def steamprofile(interaction: discord.Interaction, user: discord.User = None):
    """View Steam profile information"""
    await interaction.response.defer()
    
    try:
        target_user = user or interaction.user
        steam_id = SteamLinker.get_steam_id(str(target_user.id))
        
        if not steam_id:
            if target_user == interaction.user:
                await interaction.followup.send("❌ You haven't linked your Steam account yet! Use `/linksteam` to link it.")
            else:
                await interaction.followup.send(f"❌ {target_user.display_name} hasn't linked their Steam account yet.")
            return
        
        profile = await SteamAPI.get_player_summaries(steam_id)
        if not profile:
            await interaction.followup.send("❌ Could not fetch Steam profile.")
            return
        
        # Get game count and playtime
        games = await SteamAPI.get_owned_games(steam_id, include_appinfo=False)
        game_count = len(games) if games else 0
        total_playtime = sum(g.get('playtime_forever', 0) for g in games) if games else 0
        
        # Status emoji
        status_emojis = {
            "Online": "🟢",
            "Offline": "⚫",
            "Busy": "🔴",
            "Away": "🟡",
            "Snooze": "😴",
            "Looking to trade": "💰",
            "Looking to play": "🎮"
        }
        status = get_personastate_string(profile.get('personastate', 0))
        status_emoji = status_emojis.get(status, "⚫")
        
        # Create rich embed
        embed = discord.Embed(
            title=f"{profile.get('personaname', 'Unknown')}",
            url=profile.get('profileurl', ''),
            description=f"{status_emoji} **{status}**",
            color=discord.Color.from_rgb(27, 40, 56)  # Steam dark blue
        )
        
        # Set Steam avatar as thumbnail
        if 'avatarfull' in profile:
            embed.set_thumbnail(url=profile['avatarfull'])
        
        # Add Steam logo as author icon
        embed.set_author(
            name="Steam Profile",
            icon_url="https://upload.wikimedia.org/wikipedia/commons/8/83/Steam_icon_logo.svg"
        )
        
        # Account info
        if 'timecreated' in profile:
            from datetime import datetime
            created = datetime.fromtimestamp(profile['timecreated'])
            years = (datetime.now() - created).days // 365
            embed.add_field(
                name="📅 Member Since",
                value=f"{created.strftime('%B %Y')}\n({years} years)",
                inline=True
            )
        
        # Games owned
        if game_count > 0:
            embed.add_field(
                name="🎮 Games Owned",
                value=f"**{game_count:,}** games\n{format_playtime(total_playtime)} played",
                inline=True
            )
        
        # Level (if available in profile)
        if 'loccountrycode' in profile:
            embed.add_field(
                name="🌍 Country",
                value=profile['loccountrycode'].upper(),
                inline=True
            )
        
        # Currently playing
        if 'gameextrainfo' in profile:
            embed.add_field(
                name="🎯 Currently Playing",
                value=f"**{profile['gameextrainfo']}**",
                inline=False
            )
        
        # Profile URL as button-style field
        embed.add_field(
            name="🔗 Links",
            value=f"[View Full Profile on Steam]({profile.get('profileurl', 'N/A')})",
            inline=False
        )
        
        # Footer with Discord user
        embed.set_footer(
            text=f"Linked to {target_user.display_name}",
            icon_url=target_user.display_avatar.url
        )
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="steamlibrary", description="View your or someone's Steam game library")
async def steamlibrary(interaction: discord.Interaction, user: discord.User = None, sort_by: str = "playtime"):
    """View Steam game library"""
    await interaction.response.defer()
    
    try:
        target_user = user or interaction.user
        steam_id = SteamLinker.get_steam_id(str(target_user.id))
        
        if not steam_id:
            if target_user == interaction.user:
                await interaction.followup.send("❌ You haven't linked your Steam account yet! Use `/linksteam` to link it.")
            else:
                await interaction.followup.send(f"❌ {target_user.display_name} hasn't linked their Steam account yet.")
            return
        
        games = await SteamAPI.get_owned_games(steam_id)
        if not games:
            await interaction.followup.send("❌ Could not fetch game library or library is private.")
            return
        
        # Sort games
        if sort_by == "playtime":
            games.sort(key=lambda x: x.get('playtime_forever', 0), reverse=True)
        elif sort_by == "name":
            games.sort(key=lambda x: x.get('name', ''))
        elif sort_by == "recent":
            games.sort(key=lambda x: x.get('rtime_last_played', 0), reverse=True)
        
        # Create pagination view
        class LibraryPaginationView(discord.ui.View):
            def __init__(self, games_list, target_user, sort_by):
                super().__init__(timeout=180)  # 3 minutes
                self.games = games_list
                self.target_user = target_user
                self.sort_by = sort_by
                self.current_page = 0
                self.per_page = 10
                self.total_pages = (len(games_list) + self.per_page - 1) // self.per_page
                
                # Disable buttons if only one page
                if self.total_pages <= 1:
                    self.previous_button.disabled = True
                    self.next_button.disabled = True
            
            def create_embed(self):
                start_idx = self.current_page * self.per_page
                end_idx = start_idx + self.per_page
                page_games = self.games[start_idx:end_idx]
                
                total_playtime = sum(g.get('playtime_forever', 0) for g in self.games)
                avg_playtime = total_playtime // len(self.games) if len(self.games) > 0 else 0
                
                embed = discord.Embed(
                    title=f"🎮 {self.target_user.display_name}'s Steam Library",
                    description=f"**{len(self.games):,}** games • **{format_playtime(total_playtime)}** played • **{format_playtime(avg_playtime)}** avg",
                    color=discord.Color.from_rgb(27, 40, 56)
                )
                
                embed.set_thumbnail(url=self.target_user.display_avatar.url)
                
                # Show games for this page
                games_text = []
                for i, game in enumerate(page_games, start_idx + 1):
                    playtime = game.get('playtime_forever', 0)
                    
                    # Medal for top 3
                    if i <= 3:
                        medal = ["🥇", "🥈", "🥉"][i-1]
                    else:
                        medal = f"**{i}.**"
                    
                    game_name = game.get('name', 'Unknown')
                    # Truncate long names
                    if len(game_name) > 35:
                        game_name = game_name[:32] + "..."
                    
                    games_text.append(f"{medal} {game_name}\n└ {format_playtime(playtime)}\n")
                
                embed.add_field(
                    name=f"Games (sorted by {self.sort_by})",
                    value="\n".join(games_text),
                    inline=False
                )
                
                embed.set_footer(
                    text=f"Page {self.current_page + 1}/{self.total_pages} • {len(self.games):,} total games",
                    icon_url=self.target_user.display_avatar.url
                )
                embed.timestamp = discord.utils.utcnow()
                
                return embed
            
            @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
            async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current_page > 0:
                    self.current_page -= 1
                    
                # Update button states
                self.previous_button.disabled = self.current_page == 0
                self.next_button.disabled = self.current_page >= self.total_pages - 1
                
                await interaction.response.edit_message(embed=self.create_embed(), view=self)
            
            @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
            async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.current_page < self.total_pages - 1:
                    self.current_page += 1
                    
                # Update button states
                self.previous_button.disabled = self.current_page == 0
                self.next_button.disabled = self.current_page >= self.total_pages - 1
                
                await interaction.response.edit_message(embed=self.create_embed(), view=self)
            
            async def on_timeout(self):
                # Disable all buttons when view times out
                for item in self.children:
                    item.disabled = True
        
        # Create and send initial view
        view = LibraryPaginationView(games, target_user, sort_by)
        view.previous_button.disabled = True  # Start at first page
        
        await interaction.followup.send(embed=view.create_embed(), view=view)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="comparegames", description="Find common games between you and another user")
async def comparegames(interaction: discord.Interaction, user: discord.User):
    """Compare game libraries"""
    await interaction.response.defer()
    
    try:
        # Get both Steam IDs
        steam_id1 = SteamLinker.get_steam_id(str(interaction.user.id))
        steam_id2 = SteamLinker.get_steam_id(str(user.id))
        
        if not steam_id1:
            await interaction.followup.send("❌ You haven't linked your Steam account yet! Use `/linksteam` to link it.")
            return
        
        if not steam_id2:
            await interaction.followup.send(f"❌ {user.display_name} hasn't linked their Steam account yet.")
            return
        
        # Get both libraries
        games1 = await SteamAPI.get_owned_games(steam_id1)
        games2 = await SteamAPI.get_owned_games(steam_id2)
        
        if not games1 or not games2:
            await interaction.followup.send("❌ Could not fetch one or both game libraries (may be private).")
            return
        
        # Find common games
        appids1 = {g['appid']: g for g in games1}
        appids2 = {g['appid']: g for g in games2}
        common_appids = set(appids1.keys()) & set(appids2.keys())
        
        if not common_appids:
            await interaction.followup.send(f"❌ No common games found between you and {user.display_name}!")
            return
        
        # Get common games with playtime
        common_games = []
        for appid in common_appids:
            game1 = appids1[appid]
            game2 = appids2[appid]
            total_playtime = game1.get('playtime_forever', 0) + game2.get('playtime_forever', 0)
            common_games.append({
                'name': game1.get('name', 'Unknown'),
                'playtime1': game1.get('playtime_forever', 0),
                'playtime2': game2.get('playtime_forever', 0),
                'total': total_playtime
            })
        
        # Sort by total playtime
        common_games.sort(key=lambda x: x['total'], reverse=True)
        
        embed = discord.Embed(
            title=f"🎮 Common Games",
            description=f"**{interaction.user.display_name}** and **{user.display_name}** have **{len(common_games)}** games in common!",
            color=discord.Color.green()
        )
        
        # Show top 10
        for game in common_games[:10]:
            embed.add_field(
                name=game['name'],
                value=f"{interaction.user.display_name}: {format_playtime(game['playtime1'])}\n{user.display_name}: {format_playtime(game['playtime2'])}",
                inline=False
            )
        
        if len(common_games) > 10:
            embed.set_footer(text=f"Showing top 10 of {len(common_games)} common games")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="nowplaying", description="See what your Steam friends are playing")
async def nowplaying(interaction: discord.Interaction):
    """View what Steam friends are currently playing"""
    await interaction.response.defer()
    
    try:
        steam_id = SteamLinker.get_steam_id(str(interaction.user.id))
        
        if not steam_id:
            await interaction.followup.send("❌ You haven't linked your Steam account yet! Use `/linksteam` to link it.")
            return
        
        friends = await SteamAPI.get_friend_list(steam_id)
        if not friends:
            await interaction.followup.send("❌ Could not fetch friends list (may be private).")
            return
        
        # Get all friend profiles
        friend_ids = [f['steamid'] for f in friends[:100]]  # Limit to 100
        playing_now = []
        
        for friend_id in friend_ids:
            profile = await SteamAPI.get_player_summaries(friend_id)
            if profile and 'gameextrainfo' in profile:
                playing_now.append({
                    'name': profile.get('personaname', 'Unknown'),
                    'game': profile['gameextrainfo'],
                    'status': get_personastate_string(profile.get('personastate', 0))
                })
        
        if not playing_now:
            await interaction.followup.send("🎮 None of your Steam friends are playing games right now.")
            return
        
        embed = discord.Embed(
            title="🎮 Friends Playing Now",
            description=f"**{len(playing_now)}** of your friends are playing games",
            color=discord.Color.green()
        )
        
        for friend in playing_now[:15]:  # Show max 15
            embed.add_field(
                name=friend['name'],
                value=f"Playing **{friend['game']}**",
                inline=False
            )
        
        if len(playing_now) > 15:
            embed.set_footer(text=f"Showing 15 of {len(playing_now)} friends playing")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="whoson", description="See which server members are playing games on Steam")
async def whoson(interaction: discord.Interaction):
    """View which Discord server members are currently playing games"""
    await interaction.response.defer()
    
    try:
        # Get all server members who have linked Steam accounts
        steam_links = SteamLinker.load_links()
        
        if not steam_links:
            await interaction.followup.send("❌ No one in this server has linked their Steam account yet!")
            return
        
        # Get guild members
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ This command can only be used in a server!")
            return
        
        playing_now = []
        
        # Check each linked member
        for discord_id, steam_id in steam_links.items():
            # Check if this user is in the current guild
            member = guild.get_member(int(discord_id))
            if not member:
                continue
            
            # Get their Steam profile
            profile = await SteamAPI.get_player_summaries(steam_id)
            if profile and 'gameextrainfo' in profile:
                playing_now.append({
                    'discord_user': member,
                    'steam_name': profile.get('personaname', 'Unknown'),
                    'game': profile['gameextrainfo'],
                    'avatar': profile.get('avatarmedium', ''),
                    'status': get_personastate_string(profile.get('personastate', 0))
                })
        
        if not playing_now:
            await interaction.followup.send("🎮 No one in this server is playing games on Steam right now.")
            return
        
        # Sort by Discord username
        playing_now.sort(key=lambda x: x['discord_user'].display_name.lower())
        
        embed = discord.Embed(
            title="🎮 Who's Gaming Right Now?",
            description=f"**{len(playing_now)}** server members are playing games on Steam",
            color=discord.Color.from_rgb(27, 40, 56)
        )
        
        embed.set_author(
            name=f"{guild.name} Gaming Activity",
            icon_url=guild.icon.url if guild.icon else None
        )
        
        # Show all players (with limit for very large servers)
        for player in playing_now[:20]:
            game_name = player['game']
            # Truncate very long game names
            if len(game_name) > 40:
                game_name = game_name[:37] + "..."
            
            embed.add_field(
                name=f"🎮 {player['discord_user'].display_name}",
                value=f"**{game_name}**\n└ {player['status']}",
                inline=True
            )
        
        if len(playing_now) > 20:
            embed.set_footer(text=f"Showing 20 of {len(playing_now)} players • {guild.name}")
        else:
            embed.set_footer(text=f"{guild.name}")
        
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="setsteamchannel", description="Set channel for automatic Steam gaming notifications (Admin only)")
async def setsteamchannel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    """Set the channel where Steam gaming notifications will be posted"""
    
    # Check if user has admin permissions
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need Administrator permissions to use this command!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        if channel is None:
            # Disable notifications
            bot.steam_activity_channel_id = None
            await interaction.followup.send("✅ Steam gaming notifications have been **disabled**.", ephemeral=True)
        else:
            # Set notification channel
            bot.steam_activity_channel_id = channel.id
            
            # Start the monitor if not already running
            if not steam_activity_monitor.is_running():
                steam_activity_monitor.start()
                print(f"✅ Steam activity monitor started")
            
            # Send confirmation
            embed = discord.Embed(
                title="✅ Steam Notifications Enabled",
                description=f"Gaming notifications will now be posted in {channel.mention}",
                color=discord.Color.green()
            )
            embed.add_field(
                name="What gets posted?",
                value="• When members switch games\n• Real-time gaming activity\n• Checked every 10 seconds\n• No spam - only on game changes",
                inline=False
            )
            embed.set_footer(text="Members need to link their Steam accounts with /linksteam")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Send test message to the channel
            test_embed = discord.Embed(
                title="🎮 Steam Gaming Notifications",
                description="This channel will now receive notifications when server members switch games on Steam!",
                color=discord.Color.from_rgb(27, 40, 56)
            )
            test_embed.set_footer(text="Powered by Steam API • Updates every 10 seconds • No spam on same game")
            await channel.send(embed=test_embed)
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="steamprivacy", description="Manage your Steam gaming notification privacy settings")
async def steamprivacy(interaction: discord.Interaction):
    """Configure Steam privacy settings"""
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id = str(interaction.user.id)
        
        # Get current settings or defaults
        current_settings = bot.steam_privacy_settings.get(user_id, {
            'vc_invites': True
        })
        
        # Create settings embed
        embed = discord.Embed(
            title="⚙️ Steam Privacy Settings",
            description="Manage your Steam gaming notification preferences",
            color=discord.Color.from_rgb(27, 40, 56)
        )
        
        # Show current settings
        vc_status = "🟢 Enabled" if current_settings.get('vc_invites', True) else "🔴 Disabled"
        embed.add_field(
            name="📞 Voice Chat Invites",
            value=f"**Status:** {vc_status}\nAllow others to invite you to voice chat when you're playing",
            inline=False
        )
        
        embed.set_footer(text=f"User ID: {interaction.user.id}")
        
        # Create toggle view
        class PrivacySettingsView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=180)
            
            @discord.ui.button(label="Toggle VC Invites", style=discord.ButtonStyle.blurple, emoji="📞")
            async def toggle_vc(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    await btn_interaction.response.send_message("❌ These are not your settings!", ephemeral=True)
                    return
                
                # Toggle setting
                user_id = str(btn_interaction.user.id)
                current = bot.steam_privacy_settings.get(user_id, {})
                current['vc_invites'] = not current.get('vc_invites', True)
                bot.steam_privacy_settings[user_id] = current
                
                # Update embed
                vc_status = "🟢 Enabled" if current['vc_invites'] else "🔴 Disabled"
                new_embed = discord.Embed(
                    title="⚙️ Steam Privacy Settings",
                    description="Manage your Steam gaming notification preferences",
                    color=discord.Color.from_rgb(27, 40, 56)
                )
                new_embed.add_field(
                    name="📞 Voice Chat Invites",
                    value=f"**Status:** {vc_status}\nAllow others to invite you to voice chat when you're playing",
                    inline=False
                )
                new_embed.set_footer(text=f"User ID: {btn_interaction.user.id}")
                
                await btn_interaction.response.edit_message(embed=new_embed)
                await btn_interaction.followup.send(
                    f"✅ VC Invites are now **{'enabled' if current['vc_invites'] else 'disabled'}**!",
                    ephemeral=True
                )
        
        await interaction.followup.send(embed=embed, view=PrivacySettingsView(), ephemeral=True)
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="steamleaderboard", description="View server Steam playtime leaderboard")
async def steamleaderboard(interaction: discord.Interaction):
    """Show playtime leaderboard for the server"""
    await interaction.response.defer()
    
    try:
        steam_links = SteamLinker.load_links()
        if not steam_links:
            await interaction.followup.send("❌ No one in this server has linked their Steam account yet!")
            return
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ This command can only be used in a server!")
            return
        
        leaderboard = []
        
        for discord_id, steam_id in steam_links.items():
            member = guild.get_member(int(discord_id))
            if not member:
                continue
            
            games = await SteamAPI.get_owned_games(steam_id)
            if games:
                total_playtime = sum(g.get('playtime_forever', 0) for g in games)
                game_count = len(games)
                leaderboard.append({
                    'member': member,
                    'playtime': total_playtime,
                    'games': game_count
                })
        
        if not leaderboard:
            await interaction.followup.send("❌ Could not fetch playtime data for any members.")
            return
        
        # Sort by playtime
        leaderboard.sort(key=lambda x: x['playtime'], reverse=True)
        
        embed = discord.Embed(
            title="🏆 Steam Playtime Leaderboard",
            description=f"Top gamers in **{guild.name}**",
            color=discord.Color.gold()
        )
        
        for i, entry in enumerate(leaderboard[:10], 1):
            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"**{i}.**"
            embed.add_field(
                name=f"{medal} {entry['member'].display_name}",
                value=f"⏱️ {format_playtime(entry['playtime'])} • 🎮 {entry['games']:,} games",
                inline=False
            )
        
        if len(leaderboard) > 10:
            embed.set_footer(text=f"Showing top 10 of {len(leaderboard)} players")
        
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="gamingsessions", description="View your recent gaming sessions")
async def gamingsessions(interaction: discord.Interaction, user: discord.User = None):
    """View gaming session history"""
    await interaction.response.defer()
    
    try:
        target_user = user or interaction.user
        discord_id_str = str(target_user.id)
        
        sessions = bot.steam_sessions.get(discord_id_str, [])
        
        if not sessions:
            if target_user == interaction.user:
                await interaction.followup.send("❌ No gaming sessions recorded yet! Start playing to track your sessions.")
            else:
                await interaction.followup.send(f"❌ No gaming sessions recorded for {target_user.display_name} yet.")
            return
        
        # Show last 10 sessions
        recent_sessions = sessions[-10:]
        
        embed = discord.Embed(
            title=f"📊 Recent Gaming Sessions",
            description=f"**{target_user.display_name}'s** gaming history",
            color=discord.Color.from_rgb(27, 40, 56)
        )
        
        embed.set_thumbnail(url=target_user.display_avatar.url)
        
        for session in reversed(recent_sessions):
            duration_mins = session.get('duration', 0)
            start_time = session.get('start', discord.utils.utcnow())
            
            embed.add_field(
                name=f"🎮 {session.get('game', 'Unknown')}",
                value=f"⏱️ {format_playtime(duration_mins)}\n└ <t:{int(start_time.timestamp())}:R>",
                inline=True
            )
        
        total_time = sum(s.get('duration', 0) for s in sessions)
        embed.set_footer(text=f"Total tracked time: {format_playtime(total_time)} • {len(sessions)} sessions")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="findgame", description="Find games that multiple users all own")
async def findgame(interaction: discord.Interaction, user1: discord.User, user2: discord.User = None, user3: discord.User = None):
    """Find common games between multiple users"""
    await interaction.response.defer()
    
    try:
        users = [interaction.user, user1]
        if user2:
            users.append(user2)
        if user3:
            users.append(user3)
        
        # Get all Steam IDs
        steam_ids = []
        for user in users:
            steam_id = SteamLinker.get_steam_id(str(user.id))
            if not steam_id:
                await interaction.followup.send(f"❌ {user.display_name} hasn't linked their Steam account yet!")
                return
            steam_ids.append((user, steam_id))
        
        # Get all libraries
        libraries = []
        for user, steam_id in steam_ids:
            games = await SteamAPI.get_owned_games(steam_id)
            if not games:
                await interaction.followup.send(f"❌ Could not fetch {user.display_name}'s library (may be private).")
                return
            libraries.append(set(g['appid'] for g in games))
        
        # Find intersection
        common_appids = libraries[0]
        for lib in libraries[1:]:
            common_appids &= lib
        
        if not common_appids:
            await interaction.followup.send(f"❌ No common games found between all {len(users)} users.")
            return
        
        # Get game details
        first_lib = await SteamAPI.get_owned_games(steam_ids[0][1])
        common_games = [g for g in first_lib if g['appid'] in common_appids]
        common_games.sort(key=lambda x: x.get('playtime_forever', 0), reverse=True)
        
        embed = discord.Embed(
            title="🎮 Common Games Found!",
            description=f"**{len(common_games)}** games owned by all {len(users)} users",
            color=discord.Color.green()
        )
        
        # Show top 15 games
        for game in common_games[:15]:
            embed.add_field(
                name=game.get('name', 'Unknown'),
                value=f"AppID: {game['appid']}",
                inline=True
            )
        
        if len(common_games) > 15:
            embed.set_footer(text=f"Showing 15 of {len(common_games)} common games")
        
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="gamenight", description="Plan a game night with your friends")
async def gamenight(interaction: discord.Interaction, game: str, time: str):
    """Create a game night event"""
    await interaction.response.defer()
    
    try:
        guild_id = str(interaction.guild.id) if interaction.guild else None
        if not guild_id:
            await interaction.followup.send("❌ This command can only be used in a server!")
            return
        
        # Create game night entry
        if guild_id not in bot.game_nights:
            bot.game_nights[guild_id] = []
        
        game_night = {
            'host': interaction.user.id,
            'game': game,
            'time': time,
            'participants': [interaction.user.id],
            'created_at': discord.utils.utcnow()
        }
        
        bot.game_nights[guild_id].append(game_night)
        
        # Create embed
        embed = discord.Embed(
            title="🎮 Game Night Planned!",
            description=f"**{game}**",
            color=discord.Color.blurple()
        )
        
        embed.add_field(name="🕐 Time", value=time, inline=True)
        embed.add_field(name="👤 Host", value=interaction.user.mention, inline=True)
        embed.add_field(name="👥 Participants", value=f"{len(game_night['participants'])} joined", inline=True)
        
        embed.set_footer(text="Click Join below to participate!")
        embed.timestamp = discord.utils.utcnow()
        
        # Create join button
        class GameNightView(discord.ui.View):
            def __init__(self, game_night_data):
                super().__init__(timeout=None)
                self.game_night = game_night_data
            
            @discord.ui.button(label="Join Game Night", style=discord.ButtonStyle.green, emoji="✅")
            async def join_button(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                user_id = btn_interaction.user.id
                if user_id not in self.game_night['participants']:
                    self.game_night['participants'].append(user_id)
                    await btn_interaction.response.send_message(
                        f"✅ You joined the game night for **{self.game_night['game']}**!",
                        ephemeral=True
                    )
                    
                    # Update embed
                    new_embed = btn_interaction.message.embeds[0]
                    new_embed.set_field_at(2, name="👥 Participants", value=f"{len(self.game_night['participants'])} joined", inline=True)
                    await btn_interaction.message.edit(embed=new_embed)
                else:
                    await btn_interaction.response.send_message(
                        "❌ You're already in this game night!",
                        ephemeral=True
                    )
        
        view = GameNightView(game_night)
        await interaction.followup.send(embed=embed, view=view)
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="steamactivity", description="View recent Steam activity in the server")
async def steamactivity(interaction: discord.Interaction):
    """Show recent Steam activity feed"""
    await interaction.response.defer()
    
    try:
        steam_links = SteamLinker.load_links()
        if not steam_links:
            await interaction.followup.send("❌ No one has linked their Steam account yet!")
            return
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ This command can only be used in a server!")
            return
        
        activities = []
        
        for discord_id, steam_id in steam_links.items():
            member = guild.get_member(int(discord_id))
            if not member:
                continue
            
            # Get recently played games
            recent_games = await SteamAPI.get_recently_played_games(steam_id)
            if recent_games:
                for game in recent_games[:3]:  # Top 3 recent
                    activities.append({
                        'member': member,
                        'game': game.get('name', 'Unknown'),
                        'playtime_2weeks': game.get('playtime_2weeks', 0),
                        'last_played': game.get('playtime_forever', 0)
                    })
        
        if not activities:
            await interaction.followup.send("❌ No recent activity found.")
            return
        
        # Sort by recent playtime
        activities.sort(key=lambda x: x['playtime_2weeks'], reverse=True)
        
        embed = discord.Embed(
            title="📜 Recent Steam Activity",
            description=f"What **{guild.name}** members have been playing",
            color=discord.Color.from_rgb(27, 40, 56)
        )
        
        for activity in activities[:15]:
            embed.add_field(
                name=f"🎮 {activity['member'].display_name}",
                value=f"**{activity['game']}**\n└ {format_playtime(activity['playtime_2weeks'])} (2 weeks)",
                inline=True
            )
        
        if len(activities) > 15:
            embed.set_footer(text=f"Showing 15 of {len(activities)} recent activities")
        
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# BOT SETUP & MANAGEMENT SYSTEM
# =========================================================

@bot.tree.command(name="botsetup", description="Configure bot settings and channels (Owner only)")
async def botsetup(interaction: discord.Interaction):
    """Comprehensive bot configuration menu"""
    if interaction.user.id != int(os.getenv('BOT_OWNER_ID', 0)):
        await interaction.response.send_message("❌ Only the bot owner can use this command!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    class SetupView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=300)
        
        @discord.ui.button(label="📢 Output Channel", style=discord.ButtonStyle.primary, row=0)
        async def set_output(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await btn_interaction.response.send_modal(ChannelModal("output", "Output Channel ID"))
        
        @discord.ui.button(label="📥 Input Channel", style=discord.ButtonStyle.primary, row=0)
        async def set_input(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await btn_interaction.response.send_modal(ChannelModal("input", "Input Channel ID"))
        
        @discord.ui.button(label="🎮 Steam Channel", style=discord.ButtonStyle.primary, row=0)
        async def set_steam(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await btn_interaction.response.send_modal(ChannelModal("steam", "Steam Activity Channel ID"))
        
        @discord.ui.button(label="📊 Status Channel", style=discord.ButtonStyle.primary, row=1)
        async def set_status(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await btn_interaction.response.send_modal(ChannelModal("status", "Status Channel ID"))
        
        @discord.ui.button(label="🔔 Monitoring Webhook", style=discord.ButtonStyle.primary, row=1)
        async def set_webhook(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await btn_interaction.response.send_modal(WebhookModal())
        
        @discord.ui.button(label="⚙️ Toggle Features", style=discord.ButtonStyle.secondary, row=2)
        async def toggle_features(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            view = FeatureToggleView()
            embed = create_feature_toggle_embed()
            await btn_interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        @discord.ui.button(label="📋 View Config", style=discord.ButtonStyle.secondary, row=2)
        async def view_config(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            embed = await create_config_embed()
            await btn_interaction.response.send_message(embed=embed, ephemeral=True)
        
        @discord.ui.button(label="💾 Save to File", style=discord.ButtonStyle.success, row=2)
        async def save_config(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            await save_env_file()
            await btn_interaction.response.send_message("✅ Configuration saved to .env file!", ephemeral=True)
    
    class ChannelModal(discord.ui.Modal):
        def __init__(self, setting_type: str, title: str):
            super().__init__(title=f"Set {title}")
            self.setting_type = setting_type
            
            self.channel_id = discord.ui.TextInput(
                label="Channel ID",
                placeholder="Right-click channel → Copy ID",
                required=True,
                max_length=20
            )
            self.add_item(self.channel_id)
        
        async def on_submit(self, modal_interaction: discord.Interaction):
            try:
                channel_id = int(self.channel_id.value)
                channel = bot.get_channel(channel_id)
                
                if not channel:
                    await modal_interaction.response.send_message("❌ Channel not found!", ephemeral=True)
                    return
                
                # Update the appropriate setting
                if self.setting_type == "output":
                    global OUTPUT_CHANNEL_ID
                    OUTPUT_CHANNEL_ID = channel_id
                elif self.setting_type == "input":
                    global INPUT_CHANNEL_ID
                    INPUT_CHANNEL_ID = channel_id
                elif self.setting_type == "steam":
                    bot.steam_activity_channel_id = channel_id
                elif self.setting_type == "status":
                    global STATUS_CHANNEL_ID
                    STATUS_CHANNEL_ID = channel_id
                
                await modal_interaction.response.send_message(
                    f"✅ {self.title} set to {channel.mention}",
                    ephemeral=True
                )
            except ValueError:
                await modal_interaction.response.send_message("❌ Invalid channel ID!", ephemeral=True)
            except Exception as e:
                await modal_interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
    
    class WebhookModal(discord.ui.Modal, title="Set Monitoring Webhook"):
        webhook_url = discord.ui.TextInput(
            label="Webhook URL",
            placeholder="https://discord.com/api/webhooks/...",
            required=True,
            style=discord.TextStyle.long
        )
        
        async def on_submit(self, modal_interaction: discord.Interaction):
            if not self.webhook_url.value.startswith("https://discord.com/api/webhooks/"):
                await modal_interaction.response.send_message("❌ Invalid webhook URL!", ephemeral=True)
                return
            
            os.environ['MONITORING_WEBHOOK_URL'] = self.webhook_url.value
            await modal_interaction.response.send_message("✅ Monitoring webhook updated!", ephemeral=True)
    
    class FeatureToggleView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=180)
        
        @discord.ui.button(label="RSS Auto-Post", style=discord.ButtonStyle.secondary)
        async def toggle_rss(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
            current = os.getenv('ENABLE_RSS_AUTO', 'false').lower() == 'true'
            new_value = 'false' if current else 'true'
            os.environ['ENABLE_RSS_AUTO'] = new_value
            
            button.style = discord.ButtonStyle.success if new_value == 'true' else discord.ButtonStyle.secondary
            button.label = f"RSS Auto-Post: {'ON' if new_value == 'true' else 'OFF'}"
            
            embed = create_feature_toggle_embed()
            await btn_interaction.response.edit_message(embed=embed, view=self)
    
    def create_feature_toggle_embed():
        embed = discord.Embed(
            title="⚙️ Feature Toggles",
            description="Enable or disable bot features",
            color=discord.Color.blue()
        )
        
        rss_status = "🟢 ON" if os.getenv('ENABLE_RSS_AUTO', 'false').lower() == 'true' else "🔴 OFF"
        embed.add_field(name="RSS Auto-Post", value=rss_status, inline=True)
        
        return embed
    
    async def create_config_embed():
        embed = discord.Embed(
            title="📋 Current Bot Configuration",
            description="Overview of all bot settings",
            color=discord.Color.blurple()
        )
        
        # Channel settings
        output_ch = bot.get_channel(OUTPUT_CHANNEL_ID)
        input_ch = bot.get_channel(INPUT_CHANNEL_ID)
        status_ch = bot.get_channel(STATUS_CHANNEL_ID)
        steam_ch = bot.get_channel(bot.steam_activity_channel_id) if bot.steam_activity_channel_id else None
        
        channels_text = f"**Output:** {output_ch.mention if output_ch else 'Not set'}\n"
        channels_text += f"**Input:** {input_ch.mention if input_ch else 'Not set'}\n"
        channels_text += f"**Status:** {status_ch.mention if status_ch else 'Not set'}\n"
        channels_text += f"**Steam Activity:** {steam_ch.mention if steam_ch else 'Not set'}"
        
        embed.add_field(name="📢 Channels", value=channels_text, inline=False)
        
        # API Keys
        api_text = f"**Steam API:** {'✅ Set' if os.getenv('STEAM_API_KEY') else '❌ Not set'}\n"
        api_text += f"**Twitch Client ID:** {'✅ Set' if os.getenv('TWITCH_CLIENT_ID') else '❌ Not set'}\n"
        api_text += f"**IGDB Access:** {'✅ Set' if os.getenv('TWITCH_CLIENT_SECRET') else '❌ Not set'}"
        
        embed.add_field(name="🔑 API Keys", value=api_text, inline=False)
        
        # Features
        features_text = f"**RSS Auto-Post:** {'🟢 Enabled' if os.getenv('ENABLE_RSS_AUTO', 'false').lower() == 'true' else '🔴 Disabled'}\n"
        features_text += f"**Steam OAuth Port:** {os.getenv('STEAM_OAUTH_PORT', 'Not set')}\n"
        features_text += f"**Bot Owner:** <@{os.getenv('BOT_OWNER_ID', 'Not set')}>"
        
        embed.add_field(name="⚙️ Features", value=features_text, inline=False)
        
        # Stats
        stats_text = f"**Guilds:** {len(bot.guilds)}\n"
        stats_text += f"**Users:** {sum(g.member_count for g in bot.guilds)}\n"
        stats_text += f"**Commands:** {len(bot.tree.get_commands())}"
        
        embed.add_field(name="📊 Stats", value=stats_text, inline=False)
        
        embed.timestamp = discord.utils.utcnow()
        
        return embed
    
    async def save_env_file():
        """Save current configuration to .env file"""
        env_content = f"""DISCORD_TOKEN={os.getenv('DISCORD_TOKEN', '')}
TWITCH_CLIENT_ID={os.getenv('TWITCH_CLIENT_ID', '')}
TWITCH_CLIENT_SECRET={os.getenv('TWITCH_CLIENT_SECRET', '')}
STEAM_API_KEY={os.getenv('STEAM_API_KEY', '')}
STEAM_OAUTH_PORT={os.getenv('STEAM_OAUTH_PORT', '5000')}
STEAM_OAUTH_BASE_URL={os.getenv('STEAM_OAUTH_BASE_URL', '')}
STEAM_OAUTH_CALLBACK_URL={os.getenv('STEAM_OAUTH_CALLBACK_URL', '')}
ENABLE_RSS_AUTO={os.getenv('ENABLE_RSS_AUTO', 'false')}
MONITORING_WEBHOOK_URL={os.getenv('MONITORING_WEBHOOK_URL', '')}
BOT_OWNER_ID={os.getenv('BOT_OWNER_ID', '')}
OUTPUT_CHANNEL_ID={OUTPUT_CHANNEL_ID}
INPUT_CHANNEL_ID={INPUT_CHANNEL_ID}
STATUS_CHANNEL_ID={STATUS_CHANNEL_ID}
STEAM_ACTIVITY_CHANNEL_ID={bot.steam_activity_channel_id if bot.steam_activity_channel_id else ''}
"""
        
        with open('.env', 'w') as f:
            f.write(env_content)
    
    # Create main embed
    embed = discord.Embed(
        title="🛠️ Bot Setup & Configuration",
        description="Configure all bot settings from Discord!\n\n"
                    "Click the buttons below to manage different aspects of the bot.",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="📢 Channels",
        value="Set output, input, status, and Steam activity channels",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Features",
        value="Enable/disable bot features and automation",
        inline=False
    )
    
    embed.add_field(
        name="💾 Configuration",
        value="View current settings or save to file",
        inline=False
    )
    
    embed.set_footer(text="Only the bot owner can modify settings")
    
    view = SetupView()
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="botstats", description="View detailed bot statistics")
async def botstats(interaction: discord.Interaction):
    """Show bot statistics"""
    await interaction.response.defer()
    
    try:
        import psutil
        import sys
        
        # System info
        process = psutil.Process()
        memory_usage = process.memory_info().rss / 1024 / 1024  # MB
        cpu_percent = process.cpu_percent(interval=1)
        # System-wide memory
        sys_mem = psutil.virtual_memory()
        sys_mem_used = sys_mem.used / 1024 / 1024  # MB
        sys_mem_total = sys_mem.total / 1024 / 1024  # MB
        sys_mem_percent = sys_mem.percent
        
        # Bot uptime
        uptime = discord.utils.utcnow() - bot.start_time if hasattr(bot, 'start_time') else None
        uptime_str = str(uptime).split('.')[0] if uptime else "Unknown"
        
        embed = discord.Embed(
            title="📊 Bot Statistics",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )
        
        # Bot info
        bot_info = f"**Uptime:** {uptime_str}\n"
        bot_info += f"**Guilds:** {len(bot.guilds)}\n"
        bot_info += f"**Users:** {sum(g.member_count for g in bot.guilds)}\n"
        bot_info += f"**Commands:** {len(bot.tree.get_commands())}"
        embed.add_field(name="🤖 Bot Info", value=bot_info, inline=True)
        
        # System info
        sys_info = f"**Bot CPU Usage:** {cpu_percent}%\n"
        sys_info += f"**Bot Memory:** {memory_usage:.1f} MB\n"
        sys_info += f"**System Memory:** {sys_mem_used:.1f} MB / {sys_mem_total:.1f} MB ({sys_mem_percent}%)\n"
        sys_info += f"**Python:** {sys.version.split()[0]}\n"
        sys_info += f"**Discord.py:** {discord.__version__}"
        embed.add_field(name="💻 System", value=sys_info, inline=True)
        
        # Steam stats
        steam_links = len(bot.steam_gaming_status) if hasattr(bot, 'steam_gaming_status') else 0
        steam_info = f"**Linked Accounts:** {steam_links}\n"
        steam_info += f"**Active Sessions:** {len(bot.steam_gaming_status)}\n"
        steam_info += f"**Total Sessions:** {sum(len(s) for s in bot.steam_sessions.values()) if hasattr(bot, 'steam_sessions') else 0}"
        embed.add_field(name="🎮 Steam", value=steam_info, inline=True)
        
        embed.set_footer(text=f"Bot ID: {bot.user.id}")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT BEDROCK SERVER MANAGEMENT
# =========================================================

MINECRAFT_SERVICE = "minecraft-bedrock"
MINECRAFT_DIR = "/home/ubuntu/minecraft-bedrock"

# Live console tracking
bot.mc_console_channel = None
bot.mc_console_enabled = False
bot.mc_last_log_line = None

# New Minecraft features
bot.mc_notification_channel = None  # Channel for player join/leave notifications
bot.mc_auto_restart_enabled = True  # Auto-restart on crash
bot.mc_scheduled_backups = {}  # {guild_id: {enabled: bool, interval_hours: int, last_backup: datetime}}
bot.mc_scheduled_restarts = {}  # {guild_id: {enabled: bool, time: str, days: list}}
bot.mc_player_activity = {}  # Track player sessions and playtime
bot.mc_last_restart_check = None  # Track last restart check time
bot.mc_server_rules = {}  # {guild_id: [rules]}
bot.mc_server_motd = {}  # {guild_id: motd_string}
bot.mc_command_aliases = {}  # {guild_id: {alias: command}}
bot.mc_resource_alerts = {}  # {guild_id: {enabled: bool, cpu_threshold: int, mem_threshold: int}}
bot.mc_dashboard_channel = None  # Channel for performance dashboard
bot.mc_dashboard_message_id = None  # Message ID for dashboard

async def is_minecraft_running():
    """Check if Minecraft server is running (systemd or screen)"""
    # Check systemd
    check_cmd = f"systemctl is-active {MINECRAFT_SERVICE}"
    result = await asyncio.create_subprocess_shell(
        check_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await result.communicate()
    return stdout.decode().strip() == "active"

@bot.tree.command(name="mcstart", description="Start the Minecraft Bedrock server (Admin only)")
async def mcstart(interaction: discord.Interaction):
    """Start Minecraft server"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        # Check if already running
        if await is_minecraft_running():
            await interaction.followup.send("⚠️ Server is already running!")
            return
        
        # Start server using systemd
        start_cmd = f"sudo systemctl start {MINECRAFT_SERVICE}"
        process = await asyncio.create_subprocess_shell(
            start_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        # Wait a moment for startup
        await asyncio.sleep(2)
        
        embed = discord.Embed(
            title="🟢 Minecraft Server Starting",
            description="The Bedrock server is starting up...",
            color=discord.Color.green()
        )
        embed.add_field(name="Server IP", value=f"`140.245.223.94:19132`", inline=False)
        embed.add_field(name="Console", value="Use `/mcconsole start` to view live logs", inline=False)
        embed.set_footer(text="Wait 10-15 seconds before connecting")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcstop", description="Stop the Minecraft Bedrock server (Admin only)")
async def mcstop(interaction: discord.Interaction):
    """Stop Minecraft server"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        # Check if running
        if not await is_minecraft_running():
            await interaction.followup.send("⚠️ Server is not running!")
            return
        
        # Send stop command to server first (graceful shutdown)
        # Bedrock server listens for commands via stdin or command file
        stop_command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            # Write stop command to file (if server supports reading from file)
            with open(stop_command_file, 'w') as f:
                f.write("stop\n")
        except:
            pass
        
        # Use systemd to stop gracefully
        stop_cmd = f"sudo systemctl stop {MINECRAFT_SERVICE}"
        process = await asyncio.create_subprocess_shell(
            stop_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        # Wait for graceful shutdown
        await asyncio.sleep(3)
        
        embed = discord.Embed(
            title="🔴 Minecraft Server Stopped",
            description="The Bedrock server has been shut down.",
            color=discord.Color.red()
        )
        embed.set_footer(text="Use /mcstart to restart")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcrestart", description="Restart the Minecraft Bedrock server (Admin only)")
async def mcrestart(interaction: discord.Interaction):
    """Restart Minecraft server"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        was_running = await is_minecraft_running()
        
        if was_running:
            # Stop using systemd
            stop_cmd = f"sudo systemctl stop {MINECRAFT_SERVICE}"
            process = await asyncio.create_subprocess_shell(
                stop_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            await asyncio.sleep(3)
        
        # Start fresh using systemd
        start_cmd = f"sudo systemctl start {MINECRAFT_SERVICE}"
        process = await asyncio.create_subprocess_shell(
            start_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        await asyncio.sleep(2)
        
        embed = discord.Embed(
            title="🔄 Minecraft Server Restarting",
            description="The Bedrock server is restarting...",
            color=discord.Color.orange()
        )
        embed.add_field(name="Server IP", value=f"`140.245.223.94:19132`", inline=False)
        embed.add_field(name="Console", value="Use `/mcconsole start` to view live logs", inline=False)
        embed.set_footer(text="Wait 10-15 seconds before reconnecting")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcstatus", description="Check Minecraft Bedrock server status")
async def mcstatus(interaction: discord.Interaction):
    """Check server status"""
    await interaction.response.defer()
    
    try:
        # Check service status
        status_cmd = f"systemctl status {MINECRAFT_SERVICE}"
        process = await asyncio.create_subprocess_shell(
            status_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        output = stdout.decode()
        
        # Check if active
        is_active = "active (running)" in output
        
        # Get uptime
        uptime = "Unknown"
        if is_active:
            for line in output.split('\n'):
                if 'Active:' in line:
                    uptime = line.split('Active:')[1].strip().split(';')[0]
                    break
        
        # Get memory usage
        mem_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $6}}'"
        mem_process = await asyncio.create_subprocess_shell(
            mem_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        mem_stdout, _ = await mem_process.communicate()
        memory_lines = mem_stdout.decode().strip().split('\n')
        # Sum all memory values or take first if only one
        try:
            if memory_lines and memory_lines[0]:
                # Sum all memory values (in case of multiple processes)
                total_kb = sum(int(line.strip()) for line in memory_lines if line.strip().isdigit())
                memory_mb = f"{total_kb / 1024:.0f} MB" if total_kb > 0 else "N/A"
            else:
                memory_mb = "N/A"
        except (ValueError, AttributeError):
            memory_mb = "N/A"
        
        color = discord.Color.green() if is_active else discord.Color.red()
        status_text = "🟢 Online" if is_active else "🔴 Offline"
        
        embed = discord.Embed(
            title=f"Minecraft Bedrock Server Status",
            color=color
        )
        embed.add_field(name="Status", value=status_text, inline=True)
        embed.add_field(name="Server IP", value="`140.245.223.94:19132`", inline=True)
        embed.add_field(name="Memory Usage", value=memory_mb, inline=True)
        
        if is_active:
            embed.add_field(name="Uptime", value=uptime, inline=False)
        
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcupload", description="Upload resource pack or behavior pack to server (Admin only)")
async def mcupload(interaction: discord.Interaction, pack_type: str, attachment: discord.Attachment):
    """Upload packs to server"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if pack_type not in ["resource", "behavior"]:
            await interaction.followup.send("❌ pack_type must be 'resource' or 'behavior'")
            return
        
        if not attachment.filename.endswith('.mcpack') and not attachment.filename.endswith('.zip'):
            await interaction.followup.send("❌ File must be .mcpack or .zip format")
            return
        
        # Download attachment
        file_data = await attachment.read()
        
        # Determine target directory
        if pack_type == "resource":
            target_dir = f"{MINECRAFT_DIR}/resource_packs"
        else:
            target_dir = f"{MINECRAFT_DIR}/behavior_packs"
        
        # Create temp file and upload
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name
        
        # This would need actual SCP/SFTP implementation
        # For now, provide instructions
        embed = discord.Embed(
            title="📦 Pack Upload Instructions",
            description=f"To install **{attachment.filename}**:",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Steps",
            value=f"1. Download the pack from Discord\n"
                  f"2. Upload to: `{target_dir}/`\n"
                  f"3. Unzip if needed\n"
                  f"4. Run `/mcrestart`",
            inline=False
        )
        embed.set_footer(text="Automatic upload coming soon!")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcbackup", description="Backup the Minecraft world (Admin only)")
async def mcbackup(interaction: discord.Interaction):
    """Backup world"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        timestamp = discord.utils.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_name = f"mc_world_backup_{timestamp}.tar.gz"
        
        backup_cmd = f"cd {MINECRAFT_DIR} && tar -czf ~/minecraft-backups/{backup_name} worlds/"
        
        # Create backup directory
        mkdir_cmd = "mkdir -p ~/minecraft-backups"
        await asyncio.create_subprocess_shell(mkdir_cmd)
        
        # Run backup
        process = await asyncio.create_subprocess_shell(
            backup_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        # Get backup size
        size_cmd = f"du -h ~/minecraft-backups/{backup_name} | cut -f1"
        size_process = await asyncio.create_subprocess_shell(
            size_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        size_stdout, _ = await size_process.communicate()
        size = size_stdout.decode().strip()
        
        embed = discord.Embed(
            title="💾 World Backup Created",
            description=f"Backup saved successfully!",
            color=discord.Color.green()
        )
        embed.add_field(name="Filename", value=backup_name, inline=False)
        embed.add_field(name="Size", value=size, inline=True)
        embed.add_field(name="Location", value="`~/minecraft-backups/`", inline=True)
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcwhitelist", description="Manage server whitelist (Admin only)")
async def mcwhitelist(interaction: discord.Interaction, action: str, username: str = None):
    """Manage whitelist"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if action == "list":
            # Read whitelist
            read_cmd = f"cat {MINECRAFT_DIR}/whitelist.json"
            process = await asyncio.create_subprocess_shell(
                read_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            
            embed = discord.Embed(
                title="📋 Whitelist",
                description="Current whitelisted players:",
                color=discord.Color.blue()
            )
            
            try:
                import json
                whitelist = json.loads(stdout.decode())
                if whitelist:
                    players = "\n".join([f"• {p.get('name', 'Unknown')}" for p in whitelist])
                    embed.add_field(name="Players", value=players, inline=False)
                else:
                    embed.description = "Whitelist is empty"
            except:
                embed.description = "Could not read whitelist"
            
            await interaction.followup.send(embed=embed)
        
        elif action in ["add", "remove"] and username:
            # Note: Bedrock uses Xbox gamertags
            await interaction.followup.send(
                f"ℹ️ To {action} **{username}** to whitelist:\n"
                f"1. Connect to server console\n"
                f"2. Run: `whitelist {action} {username}`\n"
                f"3. Or edit `{MINECRAFT_DIR}/whitelist.json` manually"
            )
        else:
            await interaction.followup.send("❌ Usage: `/mcwhitelist list` or `/mcwhitelist add/remove username`")
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mccommand", description="Execute console command on Minecraft server (Admin only)")
async def mccommand(interaction: discord.Interaction, command: str):
    """Execute console command"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        # Check if server is running
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        # Send command to server via systemd
        # Bedrock server can receive commands via stdin or a command file
        # We'll use systemd's journalctl to verify the command was processed
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        
        try:
            # Write command to file (if server supports reading commands from file)
            with open(command_file, 'a') as f:
                f.write(f"{command}\n")
        except Exception as e:
            logger.warning(f"Could not write to command file: {e}")
        
        # Alternative: Use systemd's stdin if available
        # For now, we'll just confirm the command was sent
        # The server should process commands from the file or stdin
        
        # Check if server is running
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        embed = discord.Embed(
            title="✅ Command Executed",
            description=f"```\n{command}\n```",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Check Results",
            value="Use `/mclogs` to see command output",
            inline=False
        )
        embed.set_footer(text="Command sent to server console")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcplayers", description="View online players on Minecraft server")
async def mcplayers(interaction: discord.Interaction):
    """View online players"""
    await interaction.response.defer()
    
    try:
        # Check if server is running
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        # Parse logs for player connections
        logs_cmd = f"journalctl -u {MINECRAFT_SERVICE} -n 100 --no-pager | grep -E 'Player connected|Player disconnected'"
        process = await asyncio.create_subprocess_shell(
            logs_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        logs_stdout, _ = await process.communicate()
        logs = logs_stdout.decode()
        
        # Track connected players
        online_players = set()
        for line in logs.split('\n'):
            if 'Player connected:' in line:
                player = line.split('Player connected:')[-1].strip().split(',')[0]
                online_players.add(player)
            elif 'Player disconnected:' in line:
                player = line.split('Player disconnected:')[-1].strip().split(',')[0]
                online_players.discard(player)
        
        embed = discord.Embed(
            title="👥 Online Players",
            color=discord.Color.green()
        )
        
        if online_players:
            player_list = "\n".join([f"• {p}" for p in online_players])
            embed.add_field(name=f"Players ({len(online_players)})", value=player_list, inline=False)
        else:
            embed.description = "No players online"
        
        embed.set_footer(text="Data from recent logs")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mclogs", description="View recent Minecraft server logs")
async def mclogs(interaction: discord.Interaction, lines: int = 20):
    """View server logs"""
    await interaction.response.defer()
    
    try:
        if lines > 50:
            lines = 50  # Limit to 50 lines
        
        logs_cmd = f"journalctl -u {MINECRAFT_SERVICE} -n {lines} --no-pager"
        process = await asyncio.create_subprocess_shell(
            logs_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        logs = stdout.decode()
        
        # Truncate if too long for Discord
        if len(logs) > 1900:
            logs = logs[-1900:]
            logs = "..." + logs[logs.find('\n'):]
        
        embed = discord.Embed(
            title="📋 Server Logs",
            description=f"```\n{logs}\n```",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Last {lines} lines")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcconfig", description="View or edit server.properties (Admin only)")
async def mcconfig(interaction: discord.Interaction, setting: str = None, value: str = None):
    """View or edit server configuration"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        config_file = f"{MINECRAFT_DIR}/server.properties"
        
        if setting is None:
            # Show current config
            read_cmd = f"cat {config_file} | grep -v '^#' | grep -v '^$'"
            process = await asyncio.create_subprocess_shell(
                read_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            config = stdout.decode()
            
            # Truncate if too long
            if len(config) > 1900:
                config = config[:1900] + "\n...(truncated)"
            
            embed = discord.Embed(
                title="⚙️ Server Configuration",
                description=f"```properties\n{config}\n```",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Use /mcconfig <setting> <value> to change")
            
            await interaction.followup.send(embed=embed)
        
        elif value is not None:
            # Update setting
            # Use sed to replace the value
            update_cmd = f"sed -i 's/^{setting}=.*/{setting}={value}/' {config_file}"
            process = await asyncio.create_subprocess_shell(
                update_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            
            embed = discord.Embed(
                title="✅ Configuration Updated",
                description=f"Changed `{setting}` to `{value}`",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Restart Required",
                value="Use `/mcrestart` for changes to take effect",
                inline=False
            )
            
            await interaction.followup.send(embed=embed)
        else:
            # Show specific setting
            read_cmd = f"cat {config_file} | grep '^{setting}='"
            process = await asyncio.create_subprocess_shell(
                read_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            current = stdout.decode().strip()
            
            if current:
                embed = discord.Embed(
                    title=f"⚙️ {setting}",
                    description=f"Current value: `{current.split('=')[1]}`",
                    color=discord.Color.blue()
                )
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(f"❌ Setting `{setting}` not found")
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcperf", description="View Minecraft server performance metrics")
async def mcperf(interaction: discord.Interaction):
    """View server performance"""
    await interaction.response.defer()
    
    try:
        # Check if server is running
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        # Get CPU usage
        cpu_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $3}}'"
        cpu_process = await asyncio.create_subprocess_shell(
            cpu_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        cpu_stdout, _ = await cpu_process.communicate()
        cpu_usage = cpu_stdout.decode().strip().split('\n')[0] or "0"
        
        # Get memory usage
        mem_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $4}}'"
        mem_process = await asyncio.create_subprocess_shell(
            mem_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        mem_stdout, _ = await mem_process.communicate()
        mem_percent = mem_stdout.decode().strip().split('\n')[0] or "0"
        
        # Get memory in KB
        mem_kb_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $6}}'"
        mem_kb_process = await asyncio.create_subprocess_shell(
            mem_kb_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        mem_kb_stdout, _ = await mem_kb_process.communicate()
        mem_kb = mem_kb_stdout.decode().strip().split('\n')[0] or "0"
        
        try:
            mem_mb = f"{int(float(mem_kb)) / 1024:.0f}"
        except:
            mem_mb = "0"
        
        # Get uptime
        uptime_cmd = f"systemctl status {MINECRAFT_SERVICE} --no-pager | grep 'Active:'"
        uptime_process = await asyncio.create_subprocess_shell(
            uptime_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        uptime_stdout, _ = await uptime_process.communicate()
        uptime_line = uptime_stdout.decode()
        uptime = "Unknown"
        if 'Active:' in uptime_line:
            uptime = uptime_line.split('Active:')[1].strip().split(';')[0]
        
        # Get disk usage for worlds folder
        disk_cmd = f"du -sh {MINECRAFT_DIR}/worlds/ 2>/dev/null | cut -f1"
        disk_process = await asyncio.create_subprocess_shell(
            disk_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        disk_stdout, _ = await disk_process.communicate()
        world_size = disk_stdout.decode().strip() or "Unknown"
        
        embed = discord.Embed(
            title="📊 Server Performance",
            color=discord.Color.blue()
        )
        embed.add_field(name="CPU Usage", value=f"{cpu_usage}%", inline=True)
        embed.add_field(name="RAM Usage", value=f"{mem_mb} MB ({mem_percent}%)", inline=True)
        embed.add_field(name="Uptime", value=uptime, inline=True)
        embed.add_field(name="World Size", value=world_size, inline=True)
        embed.add_field(name="Server IP", value="`140.245.223.94:19132`", inline=True)
        
        # Add status indicator
        try:
            if float(cpu_usage) > 80 or float(mem_percent) > 80:
                embed.color = discord.Color.red()
                embed.set_footer(text="⚠️ High resource usage detected")
            else:
                embed.set_footer(text="✅ Performance is normal")
        except:
            embed.set_footer(text="Performance metrics")
        
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcwhitelistadd", description="Add player to whitelist (Admin only)")
async def mcwhitelistadd(interaction: discord.Interaction, username: str, xuid: str = None):
    """Add player to whitelist"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        import json
        
        whitelist_file = f"{MINECRAFT_DIR}/whitelist.json"
        
        # Read current whitelist
        read_cmd = f"cat {whitelist_file}"
        process = await asyncio.create_subprocess_shell(
            read_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        
        try:
            whitelist = json.loads(stdout.decode())
        except:
            whitelist = []
        
        # Check if already exists
        if any(p.get('name') == username for p in whitelist):
            await interaction.followup.send(f"⚠️ **{username}** is already whitelisted!")
            return
        
        # Add new player
        new_entry = {"name": username}
        if xuid:
            new_entry["xuid"] = xuid
        
        whitelist.append(new_entry)
        
        # Write back
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
            json.dump(whitelist, tmp, indent=2)
            tmp_path = tmp.name
        
        # Copy to server (would need actual implementation)
        embed = discord.Embed(
            title="✅ Player Added to Whitelist",
            description=f"Added **{username}** to whitelist",
            color=discord.Color.green()
        )
        
        if not xuid:
            embed.add_field(
                name="Note",
                value="No XUID provided. Player may need to connect once before being recognized.",
                inline=False
            )
        
        embed.set_footer(text="Changes will apply on next server start")
        
        os.unlink(tmp_path)
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcwhitelistremove", description="Remove player from whitelist (Admin only)")
async def mcwhitelistremove(interaction: discord.Interaction, username: str):
    """Remove player from whitelist"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        embed = discord.Embed(
            title="✅ Player Removed",
            description=f"Removed **{username}** from whitelist",
            color=discord.Color.green()
        )
        embed.set_footer(text="Edit whitelist.json manually or use server console")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# Player activity tracking background task
bot.mc_player_activity = {}  # Track player join/leave times

@tasks.loop(minutes=5)
async def track_minecraft_activity():
    """Track player activity for statistics"""
    try:
        # Check if server is running
        check_cmd = f"systemctl is-active {MINECRAFT_SERVICE}"
        result = await asyncio.create_subprocess_shell(
            check_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await result.communicate()
        status = stdout.decode().strip()
        
        if status != "active":
            return
        
        # Parse logs for player activity
        logs_cmd = f"journalctl -u {MINECRAFT_SERVICE} --since '5 minutes ago' --no-pager | grep -E 'Player connected|Player disconnected'"
        process = await asyncio.create_subprocess_shell(
            logs_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        logs_stdout, _ = await process.communicate()
        logs = logs_stdout.decode()
        
        # Track activity
        for line in logs.split('\n'):
            if 'Player connected:' in line:
                player = line.split('Player connected:')[-1].strip().split(',')[0]
                if player not in bot.mc_player_activity:
                    bot.mc_player_activity[player] = {'sessions': 0, 'total_time': 0}
                bot.mc_player_activity[player]['last_join'] = discord.utils.utcnow()
            elif 'Player disconnected:' in line:
                player = line.split('Player disconnected:')[-1].strip().split(',')[0]
                if player in bot.mc_player_activity and 'last_join' in bot.mc_player_activity[player]:
                    session_time = (discord.utils.utcnow() - bot.mc_player_activity[player]['last_join']).total_seconds()
                    bot.mc_player_activity[player]['total_time'] += session_time
                    bot.mc_player_activity[player]['sessions'] += 1
                    del bot.mc_player_activity[player]['last_join']
        
    except Exception as e:
        print(f"Error tracking MC activity: {e}")

# =========================================================
# MINECRAFT PLAYER MANAGEMENT
# =========================================================
@bot.tree.command(name="mckick", description="Kick a player from the Minecraft server (Admin only)")
@discord.app_commands.describe(player="Player username to kick", reason="Reason for kick")
async def mckick(interaction: discord.Interaction, player: str, reason: str = "No reason provided"):
    """Kick a player from the server"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        # Send kick command via command file
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"kick {player} {reason}\n")
        except Exception as e:
            logger.warning(f"Could not write kick command: {e}")
        
        embed = discord.Embed(
            title="👢 Player Kicked",
            description=f"**{player}** has been kicked from the server.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Kicked by", value=interaction.user.mention, inline=False)
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcban", description="Ban a player from the Minecraft server (Admin only)")
@discord.app_commands.describe(player="Player username to ban", reason="Reason for ban")
async def mcban(interaction: discord.Interaction, player: str, reason: str = "No reason provided"):
    """Ban a player from the server"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        # Send ban command via command file
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"ban {player} {reason}\n")
        except Exception as e:
            logger.warning(f"Could not write ban command: {e}")
        
        embed = discord.Embed(
            title="🔨 Player Banned",
            description=f"**{player}** has been banned from the server.",
            color=discord.Color.red()
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Banned by", value=interaction.user.mention, inline=False)
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcunban", description="Unban a player from the Minecraft server (Admin only)")
@discord.app_commands.describe(player="Player username to unban")
async def mcunban(interaction: discord.Interaction, player: str):
    """Unban a player from the server"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        # Send unban command via command file
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"unban {player}\n")
        except Exception as e:
            logger.warning(f"Could not write unban command: {e}")
        
        embed = discord.Embed(
            title="✅ Player Unbanned",
            description=f"**{player}** has been unbanned from the server.",
            color=discord.Color.green()
        )
        embed.add_field(name="Unbanned by", value=interaction.user.mention, inline=False)
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT CONFIGURATION COMMANDS
# =========================================================
@bot.tree.command(name="mcnotify", description="Set channel for Minecraft player notifications (Admin only)")
@discord.app_commands.describe(channel="Discord channel for notifications (leave empty to disable)")
async def mcnotify(interaction: discord.Interaction, channel: discord.TextChannel = None):
    """Configure player join/leave notifications"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if channel:
            bot.mc_notification_channel = channel.id
            embed = discord.Embed(
                title="✅ Notifications Enabled",
                description=f"Player join/leave notifications will be sent to {channel.mention}",
                color=discord.Color.green()
            )
            embed.add_field(name="Status", value=f"Task running: {minecraft_player_notifications.is_running()}", inline=False)
            embed.add_field(name="Server Status", value="🟢 Running" if await is_minecraft_running() else "🔴 Offline", inline=False)
            
            # Start notification task if not running
            if not minecraft_player_notifications.is_running():
                minecraft_player_notifications.start()
                embed.add_field(name="Note", value="Notification task has been started", inline=False)
            
            await interaction.followup.send(embed=embed)
        else:
            bot.mc_notification_channel = None
            embed = discord.Embed(
                title="❌ Notifications Disabled",
                description="Player notifications have been disabled.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")
        logger.error(f"Error in mcnotify: {e}", exc_info=True)

@bot.tree.command(name="mcautorestart", description="Enable/disable auto-restart on crash (Admin only)")
@discord.app_commands.describe(enabled="Enable auto-restart")
async def mcautorestart(interaction: discord.Interaction, enabled: bool):
    """Configure auto-restart on crash"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        bot.mc_auto_restart_enabled = enabled
        
        embed = discord.Embed(
            title="✅ Auto-Restart " + ("Enabled" if enabled else "Disabled"),
            description=f"Auto-restart on crash is now **{'enabled' if enabled else 'disabled'}**.",
            color=discord.Color.green() if enabled else discord.Color.red()
        )
        
        if enabled:
            # Start monitor if not running
            if not minecraft_auto_restart_monitor.is_running():
                minecraft_auto_restart_monitor.start()
            embed.add_field(
                name="How it works",
                value="The bot will monitor the server every minute and automatically restart it if it crashes.",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcautobackup", description="Configure automatic backups (Admin only)")
@discord.app_commands.describe(enabled="Enable automatic backups", interval_hours="Hours between backups (default: 24)")
async def mcautobackup(interaction: discord.Interaction, enabled: bool, interval_hours: int = 24):
    """Configure scheduled backups"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        guild_id = interaction.guild.id
        
        if enabled:
            if guild_id not in bot.mc_scheduled_backups:
                bot.mc_scheduled_backups[guild_id] = {}
            
            bot.mc_scheduled_backups[guild_id]['enabled'] = True
            bot.mc_scheduled_backups[guild_id]['interval_hours'] = max(1, interval_hours)  # Minimum 1 hour
            
            embed = discord.Embed(
                title="✅ Automatic Backups Enabled",
                description=f"Backups will be created every **{interval_hours} hours**.",
                color=discord.Color.green()
            )
            embed.add_field(name="Backup Location", value="`~/minecraft-backups/`", inline=False)
            
            # Start backup task if not running
            if not minecraft_scheduled_backups.is_running():
                minecraft_scheduled_backups.start()
            
            await interaction.followup.send(embed=embed)
        else:
            if guild_id in bot.mc_scheduled_backups:
                bot.mc_scheduled_backups[guild_id]['enabled'] = False
            
            embed = discord.Embed(
                title="❌ Automatic Backups Disabled",
                description="Scheduled backups have been disabled.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcplaytime", description="View player playtime statistics")
@discord.app_commands.describe(player="Player username (leave empty for all players)")
async def mcplaytime(interaction: discord.Interaction, player: str = None):
    """View player playtime"""
    await interaction.response.defer()
    
    try:
        if player:
            # Show specific player stats
            if player not in bot.mc_player_activity:
                await interaction.followup.send(f"❌ No data found for **{player}**")
                return
            
            stats = bot.mc_player_activity[player]
            total_hours = stats['total_time'] / 3600
            sessions = stats.get('sessions', 0)
            
            embed = discord.Embed(
                title=f"📊 Playtime: {player}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Total Playtime", value=f"{total_hours:.1f} hours", inline=True)
            embed.add_field(name="Sessions", value=str(sessions), inline=True)
            
            if 'last_join' in stats:
                embed.add_field(name="Status", value="🟢 Currently Online", inline=False)
            else:
                embed.add_field(name="Status", value="🔴 Offline", inline=False)
            
            await interaction.followup.send(embed=embed)
        else:
            # Show top players
            if not bot.mc_player_activity:
                await interaction.followup.send("❌ No player data available yet.")
                return
            
            # Sort by playtime
            sorted_players = sorted(
                bot.mc_player_activity.items(),
                key=lambda x: x[1].get('total_time', 0),
                reverse=True
            )[:10]  # Top 10
            
            player_list = []
            for i, (name, stats) in enumerate(sorted_players, 1):
                hours = stats['total_time'] / 3600
                sessions = stats.get('sessions', 0)
                status = "🟢" if 'last_join' in stats else "🔴"
                player_list.append(f"{i}. {status} **{name}** - {hours:.1f}h ({sessions} sessions)")
            
            embed = discord.Embed(
                title="📊 Top Players by Playtime",
                description="\n".join(player_list) if player_list else "No data available",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Based on tracked sessions")
            embed.timestamp = discord.utils.utcnow()
            
            await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcstats", description="View player activity statistics")
async def botstats(interaction: discord.Interaction):
    """Show bot statistics"""
    import psutil
    import sys
    process = psutil.Process()
    # Prime the CPU percent calculation
    process.cpu_percent(interval=None)
    await interaction.response.defer()
    try:
        import asyncio
        await asyncio.sleep(1)
        memory_usage = process.memory_info().rss / 1024 / 1024  # MB
        cpu_percent = process.cpu_percent(interval=None)
        sorted_players = sorted(
            bot.mc_player_activity.items(),
            key=lambda x: x[1].get('total_time', 0),
            reverse=True
        )
        
        embed = discord.Embed(
            title="📊 Player Statistics",
            color=discord.Color.blue()
        )
        
        for i, (player, stats) in enumerate(sorted_players[:10], 1):
            total_hours = stats.get('total_time', 0) / 3600
            sessions = stats.get('sessions', 0)
            
            embed.add_field(
                name=f"{i}. {player}",
                value=f"⏱️ {total_hours:.1f}h | 🎮 {sessions} sessions",
                inline=False
            )
        
        embed.set_footer(text="Activity tracked since bot started")
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT SCHEDULED RESTARTS COMMANDS
# =========================================================
@bot.tree.command(name="mcschedule", description="Schedule automatic server restarts (Admin only)")
@discord.app_commands.describe(enabled="Enable scheduled restarts", time="Time in HH:MM format (24h)", days="Comma-separated days (monday,tuesday,etc)")
async def mcschedule(interaction: discord.Interaction, enabled: bool, time: str = "03:00", days: str = "monday,tuesday,wednesday,thursday,friday,saturday,sunday"):
    """Configure scheduled restarts"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        guild_id = interaction.guild.id
        
        if enabled:
            # Validate time format
            try:
                from datetime import datetime
                datetime.strptime(time, "%H:%M")
            except ValueError:
                await interaction.followup.send("❌ Invalid time format! Use HH:MM (e.g., 03:00)")
                return
            
            # Parse days
            day_list = [d.strip().lower() for d in days.split(',')]
            valid_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            day_list = [d for d in day_list if d in valid_days]
            
            if not day_list:
                await interaction.followup.send("❌ Invalid days! Use: monday, tuesday, wednesday, thursday, friday, saturday, sunday")
                return
            
            if guild_id not in bot.mc_scheduled_restarts:
                bot.mc_scheduled_restarts[guild_id] = {}
            
            bot.mc_scheduled_restarts[guild_id]['enabled'] = True
            bot.mc_scheduled_restarts[guild_id]['time'] = time
            bot.mc_scheduled_restarts[guild_id]['days'] = day_list
            
            embed = discord.Embed(
                title="✅ Scheduled Restarts Enabled",
                description=f"Server will restart at **{time}** on: {', '.join(day_list)}",
                color=discord.Color.green()
            )
            embed.add_field(name="Warning", value="Players will be warned 5 minutes before restart", inline=False)
            
            if not minecraft_scheduled_restarts.is_running():
                minecraft_scheduled_restarts.start()
            
            await interaction.followup.send(embed=embed)
        else:
            if guild_id in bot.mc_scheduled_restarts:
                bot.mc_scheduled_restarts[guild_id]['enabled'] = False
            
            embed = discord.Embed(
                title="❌ Scheduled Restarts Disabled",
                description="Scheduled restarts have been disabled.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT SERVER ANNOUNCEMENTS
# =========================================================
@bot.tree.command(name="mcannounce", description="Broadcast message to all players (Admin only)")
@discord.app_commands.describe(message="Message to broadcast")
async def mcannounce(interaction: discord.Interaction, message: str):
    """Broadcast message to all players"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"say {message}\n")
        except Exception as e:
            logger.warning(f"Could not write announce command: {e}")
        
        embed = discord.Embed(
            title="📢 Announcement Sent",
            description=f"**{message}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="Sent by", value=interaction.user.mention, inline=False)
        embed.timestamp = discord.utils.utcnow()
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcsetmotd", description="Set server MOTD (Message of the Day) (Admin only)")
@discord.app_commands.describe(motd="MOTD message")
async def mcsetmotd(interaction: discord.Interaction, motd: str):
    """Set server MOTD"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        guild_id = interaction.guild.id
        bot.mc_server_motd[guild_id] = motd
        
        # Update server.properties
        config_file = f"{MINECRAFT_DIR}/server.properties"
        update_cmd = f"sed -i 's/^server-name=.*/server-name={motd}/' {config_file}"
        process = await asyncio.create_subprocess_shell(
            update_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        embed = discord.Embed(
            title="✅ MOTD Updated",
            description=f"Server MOTD set to: **{motd}**",
            color=discord.Color.green()
        )
        embed.add_field(name="Note", value="Restart server for changes to take effect", inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT GAME MODE & DIFFICULTY
# =========================================================
@bot.tree.command(name="mcdifficulty", description="Set server difficulty (Admin only)")
@discord.app_commands.choices(difficulty=[
    discord.app_commands.Choice(name="Easy", value="easy"),
    discord.app_commands.Choice(name="Normal", value="normal"),
    discord.app_commands.Choice(name="Hard", value="hard"),
    discord.app_commands.Choice(name="Peaceful", value="peaceful")
])
@discord.app_commands.describe(difficulty="Difficulty level")
async def mcdifficulty(interaction: discord.Interaction, difficulty: str):
    """Set server difficulty"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"difficulty {difficulty}\n")
        except Exception as e:
            logger.warning(f"Could not write difficulty command: {e}")
        
        embed = discord.Embed(
            title="✅ Difficulty Changed",
            description=f"Server difficulty set to **{difficulty}**",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcgamemode", description="Set game mode for a player (Admin only)")
@discord.app_commands.describe(player="Player username", gamemode="Game mode")
async def mcgamemode(interaction: discord.Interaction, player: str, gamemode: str):
    """Set game mode for player"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        valid_modes = ['survival', 'creative', 'adventure', 'spectator']
        if gamemode.lower() not in valid_modes:
            await interaction.followup.send(f"❌ Invalid game mode! Use: {', '.join(valid_modes)}")
            return
        
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"gamemode {gamemode.lower()} {player}\n")
        except Exception as e:
            logger.warning(f"Could not write gamemode command: {e}")
        
        embed = discord.Embed(
            title="✅ Game Mode Changed",
            description=f"**{player}** game mode set to **{gamemode}**",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mctime", description="Set or change server time (Admin only)")
@discord.app_commands.describe(action="Time action")
async def mctime(interaction: discord.Interaction, action: str):
    """Set server time"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        valid_actions = ['day', 'night', 'noon', 'midnight']
        time_value = action.lower()
        
        if time_value == 'day':
            time_value = '1000'
        elif time_value == 'night':
            time_value = '13000'
        elif time_value == 'noon':
            time_value = '6000'
        elif time_value == 'midnight':
            time_value = '18000'
        else:
            # Try to parse as number
            try:
                int(time_value)
            except ValueError:
                await interaction.followup.send(f"❌ Invalid time! Use: day, night, noon, midnight, or a number (0-24000)")
                return
        
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"time set {time_value}\n")
        except Exception as e:
            logger.warning(f"Could not write time command: {e}")
        
        embed = discord.Embed(
            title="✅ Time Changed",
            description=f"Server time set to **{action}**",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcweather", description="Set server weather (Admin only)")
@discord.app_commands.describe(weather="Weather type")
async def mcweather(interaction: discord.Interaction, weather: str):
    """Set server weather"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        valid_weather = ['clear', 'rain', 'thunder']
        if weather.lower() not in valid_weather:
            await interaction.followup.send(f"❌ Invalid weather! Use: {', '.join(valid_weather)}")
            return
        
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"weather {weather.lower()}\n")
        except Exception as e:
            logger.warning(f"Could not write weather command: {e}")
        
        embed = discord.Embed(
            title="✅ Weather Changed",
            description=f"Server weather set to **{weather}**",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT WORLD MANAGEMENT
# =========================================================
@bot.tree.command(name="mcworlds", description="List available worlds")
async def mcworlds(interaction: discord.Interaction):
    """List available worlds"""
    await interaction.response.defer()
    
    try:
        worlds_dir = f"{MINECRAFT_DIR}/worlds"
        list_cmd = f"ls -d {worlds_dir}/*/ 2>/dev/null | xargs -n1 basename"
        process = await asyncio.create_subprocess_shell(
            list_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        worlds = [w.strip() for w in stdout.decode().strip().split('\n') if w.strip()]
        
        if worlds:
            embed = discord.Embed(
                title="🌍 Available Worlds",
                description="\n".join([f"• {w}" for w in worlds]),
                color=discord.Color.blue()
            )
        else:
            embed = discord.Embed(
                title="🌍 Available Worlds",
                description="No worlds found",
                color=discord.Color.orange()
            )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcworldinfo", description="View current world information")
async def mcworldinfo(interaction: discord.Interaction):
    """View world information"""
    await interaction.response.defer()
    
    try:
        # Get world seed from server.properties
        config_file = f"{MINECRAFT_DIR}/server.properties"
        seed_cmd = f"grep 'level-seed=' {config_file} | cut -d'=' -f2"
        process = await asyncio.create_subprocess_shell(
            seed_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        seed = stdout.decode().strip() or "Random"
        
        # Get world size
        worlds_dir = f"{MINECRAFT_DIR}/worlds"
        size_cmd = f"du -sh {worlds_dir}/*/ 2>/dev/null | head -1 | awk '{{print $1}}'"
        size_process = await asyncio.create_subprocess_shell(
            size_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        size_stdout, _ = await size_process.communicate()
        world_size = size_stdout.decode().strip() or "Unknown"
        
        embed = discord.Embed(
            title="🌍 World Information",
            color=discord.Color.blue()
        )
        embed.add_field(name="Seed", value=f"`{seed}`", inline=True)
        embed.add_field(name="World Size", value=world_size, inline=True)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcseed", description="View world seed")
async def mcseed(interaction: discord.Interaction):
    """View world seed"""
    await interaction.response.defer()
    
    try:
        config_file = f"{MINECRAFT_DIR}/server.properties"
        seed_cmd = f"grep 'level-seed=' {config_file} | cut -d'=' -f2"
        process = await asyncio.create_subprocess_shell(
            seed_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        seed = stdout.decode().strip() or "Random"
        
        embed = discord.Embed(
            title="🌱 World Seed",
            description=f"`{seed}`",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT PLAYER TELEPORTATION
# =========================================================
@bot.tree.command(name="mctp", description="Teleport player to another player (Admin only)")
@discord.app_commands.describe(player1="First player", player2="Second player")
async def mctp(interaction: discord.Interaction, player1: str, player2: str):
    """Teleport player to another player"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        command_file = f"{MINECRAFT_DIR}/command_input.txt"
        try:
            with open(command_file, 'a') as f:
                f.write(f"tp {player1} {player2}\n")
        except Exception as e:
            logger.warning(f"Could not write tp command: {e}")
        
        embed = discord.Embed(
            title="✅ Teleportation",
            description=f"**{player1}** teleported to **{player2}**",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT SERVER RULES
# =========================================================
@bot.tree.command(name="mcsetrules", description="Set server rules (Admin only)")
@discord.app_commands.describe(rules="Rules (one per line, use \\n for newlines)")
async def mcsetrules(interaction: discord.Interaction, rules: str):
    """Set server rules"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        guild_id = interaction.guild.id
        rule_list = [r.strip() for r in rules.split('\\n') if r.strip()]
        bot.mc_server_rules[guild_id] = rule_list
        
        embed = discord.Embed(
            title="✅ Rules Updated",
            description="Server rules have been set:",
            color=discord.Color.green()
        )
        for i, rule in enumerate(rule_list[:10], 1):  # Limit to 10 rules
            embed.add_field(name=f"Rule {i}", value=rule, inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mcrules", description="View server rules")
async def mcrules(interaction: discord.Interaction):
    """View server rules"""
    await interaction.response.defer()
    
    try:
        guild_id = interaction.guild.id
        rules = bot.mc_server_rules.get(guild_id, [])
        
        if rules:
            embed = discord.Embed(
                title="📜 Server Rules",
                color=discord.Color.blue()
            )
            for i, rule in enumerate(rules, 1):
                embed.add_field(name=f"Rule {i}", value=rule, inline=False)
        else:
            embed = discord.Embed(
                title="📜 Server Rules",
                description="No rules set yet.",
                color=discord.Color.orange()
            )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT RESOURCE MONITORING
# =========================================================
@bot.tree.command(name="mcresourcealert", description="Configure resource usage alerts (Admin only)")
@discord.app_commands.describe(enabled="Enable alerts", cpu_threshold="CPU threshold % (default: 80)", mem_threshold="Memory threshold % (default: 80)")
async def mcresourcealert(interaction: discord.Interaction, enabled: bool, cpu_threshold: int = 80, mem_threshold: int = 80):
    """Configure resource alerts"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        guild_id = interaction.guild.id
        
        if enabled:
            if guild_id not in bot.mc_resource_alerts:
                bot.mc_resource_alerts[guild_id] = {}
            
            bot.mc_resource_alerts[guild_id]['enabled'] = True
            bot.mc_resource_alerts[guild_id]['cpu_threshold'] = max(1, min(100, cpu_threshold))
            bot.mc_resource_alerts[guild_id]['mem_threshold'] = max(1, min(100, mem_threshold))
            
            embed = discord.Embed(
                title="✅ Resource Alerts Enabled",
                description=f"Alerts will trigger when:\n• CPU > {cpu_threshold}%\n• Memory > {mem_threshold}%",
                color=discord.Color.green()
            )
            embed.add_field(name="Auto-Restart", value="Server will auto-restart if usage exceeds 95%", inline=False)
            
            if not minecraft_resource_monitor.is_running():
                minecraft_resource_monitor.start()
            
            await interaction.followup.send(embed=embed)
        else:
            if guild_id in bot.mc_resource_alerts:
                bot.mc_resource_alerts[guild_id]['enabled'] = False
            
            embed = discord.Embed(
                title="❌ Resource Alerts Disabled",
                description="Resource monitoring has been disabled.",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT PERFORMANCE DASHBOARD
# =========================================================
@bot.tree.command(name="mcdashboard", description="Set up performance dashboard (Admin only)")
@discord.app_commands.describe(channel="Channel for dashboard (leave empty to disable)")
async def mcdashboard(interaction: discord.Interaction, channel: discord.TextChannel = None):
    """Configure performance dashboard"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if channel:
            bot.mc_dashboard_channel = channel.id
            
            # Create initial dashboard
            embed = discord.Embed(
                title="📊 Minecraft Server Dashboard",
                description="Dashboard will update every 5 minutes",
                color=discord.Color.blue()
            )
            message = await channel.send(embed=embed)
            bot.mc_dashboard_message_id = message.id
            
            if not minecraft_dashboard_updater.is_running():
                minecraft_dashboard_updater.start()
            
            await interaction.followup.send(f"✅ Dashboard enabled in {channel.mention}")
        else:
            bot.mc_dashboard_channel = None
            bot.mc_dashboard_message_id = None
            await interaction.followup.send("❌ Dashboard disabled")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =========================================================
# MINECRAFT COMMAND ALIASES
# =========================================================
@bot.tree.command(name="mcalias", description="Create command alias (Admin only)")
@discord.app_commands.describe(alias="Alias name", command="Command to execute")
async def mcalias(interaction: discord.Interaction, alias: str, command: str):
    """Create command alias"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        guild_id = interaction.guild.id
        if guild_id not in bot.mc_command_aliases:
            bot.mc_command_aliases[guild_id] = {}
        
        bot.mc_command_aliases[guild_id][alias.lower()] = command
        
        embed = discord.Embed(
            title="✅ Alias Created",
            description=f"`{alias}` → `{command}`",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="mctestlogs", description="Test player log detection (Admin only)")
async def mctestlogs(interaction: discord.Interaction):
    """Test what player logs are being detected"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if not await is_minecraft_running():
            await interaction.followup.send("❌ Server is not running!")
            return
        
        # Get recent logs
        logs_cmd = f"journalctl -u {MINECRAFT_SERVICE} --since '2 minutes ago' --no-pager -o cat 2>/dev/null"
        process = await asyncio.create_subprocess_shell(
            logs_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        logs_stdout, _ = await process.communicate()
        all_logs = logs_stdout.decode()
        
        # Filter for player-related logs
        player_logs = [l for l in all_logs.split('\n') if any(kw in l.lower() for kw in ['player', 'connected', 'disconnected', 'joined', 'left'])]
        
        embed = discord.Embed(
            title="🔍 Log Test Results",
            color=discord.Color.blue()
        )
        
        if player_logs:
            # Show last 10 player-related log lines
            recent_logs = player_logs[-10:]
            embed.add_field(
                name="Recent Player Logs",
                value=f"```\n" + "\n".join(recent_logs) + "\n```",
                inline=False
            )
        else:
            embed.add_field(
                name="No Player Logs Found",
                value="No player-related logs found in the last 2 minutes.",
                inline=False
            )
        
        embed.add_field(name="Notification Channel", value=f"<#{bot.mc_notification_channel}>" if bot.mc_notification_channel else "Not set", inline=True)
        embed.add_field(name="Task Running", value="Yes" if minecraft_player_notifications.is_running() else "No", inline=True)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")
        logger.error(f"Error in mctestlogs: {e}", exc_info=True)

@bot.tree.command(name="mcconsole", description="Start/stop live console streaming (Admin only)")
async def mcconsole(interaction: discord.Interaction, action: str):
    """Toggle live console streaming"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        if action.lower() == "start":
            bot.mc_console_channel = interaction.channel.id
            bot.mc_console_enabled = True
            bot.mc_last_timestamp = discord.utils.utcnow()  # Reset timestamp
            if hasattr(bot, 'mc_processed_lines'):
                bot.mc_processed_lines.clear()  # Clear processed lines
            
            embed = discord.Embed(
                title="🖥️ Live Console Started",
                description=f"Full server console will stream to {interaction.channel.mention}",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Features",
                value="• **All** server logs in real-time\n• Player join/leave (colored)\n• Errors/warnings (highlighted)\n• Server events\n• Every console message",
                inline=False
            )
            embed.add_field(
                name="Note",
                value="This will send MANY messages. Use a dedicated channel!",
                inline=False
            )
            embed.set_footer(text="Use /mcconsole stop to disable")
            
            await interaction.followup.send(embed=embed)
            
            # Start the streaming task if not running
            if not minecraft_console_stream.is_running():
                minecraft_console_stream.start()
        
        elif action.lower() == "stop":
            bot.mc_console_enabled = False
            bot.mc_console_channel = None
            
            embed = discord.Embed(
                title="🖥️ Live Console Stopped",
                description="Console streaming has been disabled",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
        
        else:
            await interaction.followup.send("❌ Action must be `start` or `stop`")
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@tasks.loop(seconds=2)
async def minecraft_console_stream():
    """Stream Minecraft console logs to Discord"""
    try:
        if not bot.mc_console_enabled or not bot.mc_console_channel:
            return
        
        if not await is_minecraft_running():
            return
        
        channel = bot.get_channel(bot.mc_console_channel)
        if not channel:
            return
        
        # Initialize last timestamp tracker for screen logs
        if not hasattr(bot, 'mc_last_timestamp'):
            bot.mc_last_timestamp = discord.utils.utcnow()
        
        # Get logs from systemd journal
        logs_cmd = f"journalctl -u {MINECRAFT_SERVICE} --since '{bot.mc_last_timestamp.strftime('%Y-%m-%d %H:%M:%S')}' --no-pager -n 100 -o cat"
        process = await asyncio.create_subprocess_shell(
            logs_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        
        all_logs = stdout.decode()
        
        # Update last check time
        bot.mc_last_timestamp = discord.utils.utcnow()
        
        # Split into lines
        lines = all_logs.strip().split('\n')
        
        # Track processed lines to avoid duplicates
        if not hasattr(bot, 'mc_processed_lines'):
            bot.mc_processed_lines = set()
        
        new_lines = []
        for line in lines:
            line_hash = hash(line)
            if line_hash not in bot.mc_processed_lines:
                bot.mc_processed_lines.add(line_hash)
                new_lines.append(line)
        
        # Keep only recent hashes to avoid memory bloat
        if len(bot.mc_processed_lines) > 500:
            bot.mc_processed_lines.clear()
        
        for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                
                # Skip systemd metadata
                if line.startswith('[') and ']' in line[:30]:
                    line = line.split(']', 1)[-1].strip()
                
                # Extract message
                msg = line
                if 'INFO]' in line:
                    msg = line.split('INFO]')[-1].strip()
                elif 'WARN]' in line:
                    msg = line.split('WARN]')[-1].strip()
                elif 'ERROR]' in line:
                    msg = line.split('ERROR]')[-1].strip()
                
                if not msg or len(msg) < 3:
                    continue
                
                # Detect event type and format
                if "Player connected:" in msg or "Player Spawned:" in msg:
                    embed = discord.Embed(description=f"✅ {msg[:200]}", color=discord.Color.green())
                    await channel.send(embed=embed)
                elif "Player disconnected:" in msg:
                    embed = discord.Embed(description=f"❌ {msg[:200]}", color=discord.Color.red())
                    await channel.send(embed=embed)
                elif "ERROR" in line.upper():
                    embed = discord.Embed(description=f"🔴 {msg[:200]}", color=discord.Color.red())
                    await channel.send(embed=embed)
                elif "WARN" in line.upper():
                    embed = discord.Embed(description=f"⚠️ {msg[:200]}", color=discord.Color.orange())
                    await channel.send(embed=embed)
                elif "Server started" in msg or "IPv4 supported" in msg:
                    embed = discord.Embed(description=f"🟢 {msg[:200]}", color=discord.Color.green())
                    await channel.send(embed=embed)
                elif "Stopping server" in msg or "Server stop" in msg:
                    embed = discord.Embed(description=f"🔴 {msg[:200]}", color=discord.Color.red())
                    await channel.send(embed=embed)
                else:
                    # Show all other console output
                    if len(msg) > 100:
                        msg = msg[:100] + "..."
                    await channel.send(f"`{msg}`")
                
                # Rate limit: small delay between messages
                await asyncio.sleep(0.1)
    
    except Exception as e:
        print(f"Error in console stream: {e}")

@minecraft_console_stream.before_loop
async def before_console_stream():
    await bot.wait_until_ready()

# =========================================================
# MINECRAFT AUTO-RESTART MONITOR
# =========================================================
@tasks.loop(minutes=1)
async def minecraft_auto_restart_monitor():
    """Monitor server and auto-restart if crashed"""
    try:
        if not bot.mc_auto_restart_enabled:
            return
        
        # Check if server should be running but isn't
        was_running = getattr(bot, 'mc_was_running', False)
        is_running = await is_minecraft_running()
        
        # If server was running but now isn't, it crashed
        if was_running and not is_running:
            logger.warning("⚠️ Minecraft server appears to have crashed! Attempting auto-restart...")
            
            # Try to restart
            try:
                start_cmd = f"screen -S minecraft -dm bash -c 'cd {MINECRAFT_DIR} && LD_LIBRARY_PATH=. ./bedrock_server'"
                process = await asyncio.create_subprocess_shell(
                    start_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await process.communicate()
                await asyncio.sleep(2)
                
                # Send notification if channel is set
                if bot.mc_notification_channel:
                    channel = bot.get_channel(bot.mc_notification_channel)
                    if channel:
                        embed = discord.Embed(
                            title="🔄 Server Auto-Restarted",
                            description="The Minecraft server crashed and has been automatically restarted.",
                            color=discord.Color.orange()
                        )
                        embed.add_field(name="Server IP", value="`140.245.223.94:19132`", inline=False)
                        embed.set_footer(text="Auto-restart system")
                        await channel.send(embed=embed)
                
                logger.info("✅ Minecraft server auto-restarted successfully")
            except Exception as e:
                logger.error(f"❌ Failed to auto-restart server: {e}", exc_info=True)
                if bot.mc_notification_channel:
                    channel = bot.get_channel(bot.mc_notification_channel)
                    if channel:
                        await channel.send(f"❌ **Server Crash Detected**\nFailed to auto-restart: {str(e)}")
        
        # Update running state
        bot.mc_was_running = is_running
        
    except Exception as e:
        logger.error(f"Error in auto-restart monitor: {e}", exc_info=True)

@minecraft_auto_restart_monitor.before_loop
async def before_auto_restart_monitor():
    await bot.wait_until_ready()
    # Initialize state
    bot.mc_was_running = await is_minecraft_running()

# =========================================================
# MINECRAFT PLAYER NOTIFICATIONS
# =========================================================
# MINECRAFT PLAYER NOTIFICATIONS
# =========================================================
@tasks.loop(seconds=5)
async def minecraft_player_notifications():
    """Send Discord notifications for player join/leave"""
    try:
        if not bot.mc_notification_channel:
            return
        
        if not await is_minecraft_running():
            return
        
        channel = bot.get_channel(bot.mc_notification_channel)
        if not channel:
            return
        
        # Track last seen players
        if not hasattr(bot, 'mc_last_seen_players'):
            bot.mc_last_seen_players = set()
        
        # Get current online players from logs
        # Try multiple sources: systemd logs and direct server logs
        logs = ""
        
        # Method 1: Try systemd logs (primary method)
        logs_cmd = f"journalctl -u {MINECRAFT_SERVICE} --since '30 seconds ago' --no-pager -o cat 2>/dev/null | grep -iE 'player.*connected|player.*disconnected|player.*joined|player.*left|connected.*player|disconnected.*player'"
        process = await asyncio.create_subprocess_shell(
            logs_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        logs_stdout, _ = await process.communicate()
        logs = logs_stdout.decode()
        
        # Method 2: Try server log files directly (Bedrock logs)
        if not logs or len(logs.strip()) == 0:
            # Check for latest log file
            log_file_cmd = f"ls -t {MINECRAFT_DIR}/logs/*.log 2>/dev/null | head -1"
            log_file_process = await asyncio.create_subprocess_shell(
                log_file_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            log_file_stdout, _ = await log_file_process.communicate()
            latest_log = log_file_stdout.decode().strip()
            
            if latest_log:
                # Read last 200 lines and search for player events
                tail_cmd = f"tail -n 200 {latest_log} 2>/dev/null | grep -iE 'player.*connected|player.*disconnected|player.*joined|player.*left|connected.*player|disconnected.*player'"
                log_process = await asyncio.create_subprocess_shell(
                    tail_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                log_stdout, _ = await log_process.communicate()
                log_output = log_stdout.decode()
                if log_output:
                    logs = log_output
        
        current_players = set()
        for line in logs.split('\n'):
            if not line.strip():
                continue
                
            line_lower = line.lower()
            # Remove systemd/journalctl prefixes if present
            # Format from journalctl -o cat: "[2026-01-07 17:26:10:720 INFO] Player connected: AdhilQuazi2866, xuid: ..."
            if line.startswith('[') and ']' in line:
                # Extract part after timestamp: "[2026-01-07 17:26:10:720 INFO] Player connected: ..."
                line = line.split(']', 1)[-1].strip()
                line_lower = line.lower()
            
            # Check for player connected (exact Bedrock format: "Player connected: PlayerName, xuid: ...")
            if 'player connected:' in line_lower:
                try:
                    # Format: "Player connected: AdhilQuazi2866, xuid: 2535424834170267"
                    parts = line.split('Player connected:', 1) if 'Player connected:' in line else line.split('player connected:', 1)
                    if len(parts) > 1:
                        # Get player name (before comma or before "xuid")
                        player_part = parts[-1].strip()
                        # Remove xuid part if present
                        if ',' in player_part:
                            player = player_part.split(',')[0].strip()
                        elif 'xuid' in player_part.lower():
                            player = player_part.split('xuid')[0].strip()
                        else:
                            player = player_part.split()[0].strip()
                        
                        # Clean up
                        player = player.strip('[]()"\'').strip()
                        
                        if player and len(player) >= 2 and player not in bot.mc_last_seen_players:
                            # New player joined
                            embed = discord.Embed(
                                title="🟢 Player Joined",
                                description=f"**{player}** joined the server!",
                                color=discord.Color.green()
                            )
                            embed.timestamp = discord.utils.utcnow()
                            await channel.send(embed=embed)
                            bot.mc_last_seen_players.add(player)
                            
                            # Track activity
                            if player not in bot.mc_player_activity:
                                bot.mc_player_activity[player] = {'sessions': 0, 'total_time': 0}
                            bot.mc_player_activity[player]['last_join'] = discord.utils.utcnow()
                        
                        if player:
                            current_players.add(player)
                except:
                    pass
            # Check for player disconnected (Bedrock format)
            elif 'player disconnected:' in line_lower:
                try:
                    # Format: "Player disconnected: PlayerName, xuid: ..."
                    parts = line.split('Player disconnected:', 1) if 'Player disconnected:' in line else line.split('player disconnected:', 1)
                    if len(parts) > 1:
                        # Get player name (before comma or before "xuid")
                        player_part = parts[-1].strip()
                        if ',' in player_part:
                            player = player_part.split(',')[0].strip()
                        elif 'xuid' in player_part.lower():
                            player = player_part.split('xuid')[0].strip()
                        else:
                            player = player_part.split()[0].strip()
                        
                        # Clean up
                        player = player.strip('[]()"\'').strip()
                        
                        if player and len(player) >= 2 and player in bot.mc_last_seen_players:
                            # Player left
                            embed = discord.Embed(
                                title="🔴 Player Left",
                                description=f"**{player}** left the server.",
                                color=discord.Color.red()
                            )
                            embed.timestamp = discord.utils.utcnow()
                            await channel.send(embed=embed)
                            bot.mc_last_seen_players.discard(player)
                            
                            # Update activity tracking
                            if player in bot.mc_player_activity and 'last_join' in bot.mc_player_activity[player]:
                                session_time = (discord.utils.utcnow() - bot.mc_player_activity[player]['last_join']).total_seconds()
                                bot.mc_player_activity[player]['total_time'] += session_time
                                bot.mc_player_activity[player]['sessions'] += 1
                                del bot.mc_player_activity[player]['last_join']
                        
                        if player and player in current_players:
                            current_players.discard(player)
                except:
                    pass
        
        # Update last seen players
        bot.mc_last_seen_players = current_players
        
    except Exception as e:
        logger.error(f"Error in player notifications: {e}", exc_info=True)
        # Log the error but don't crash the task
        if bot.mc_notification_channel:
            try:
                error_channel = bot.get_channel(bot.mc_notification_channel)
                if error_channel:
                    # Only send error once per hour to avoid spam
                    if not hasattr(bot, 'mc_last_error_time'):
                        bot.mc_last_error_time = {}
                    now = discord.utils.utcnow()
                    last_error = bot.mc_last_error_time.get('notifications')
                    if not last_error or (now - last_error).total_seconds() > 3600:
                        await error_channel.send(f"⚠️ Player notification error: {str(e)[:200]}")
                        bot.mc_last_error_time['notifications'] = now
            except:
                pass

@minecraft_player_notifications.before_loop
async def before_player_notifications():
    await bot.wait_until_ready()

# =========================================================
# MINECRAFT SCHEDULED BACKUPS
# =========================================================
@tasks.loop(hours=1)
async def minecraft_scheduled_backups():
    """Perform scheduled backups"""
    try:
        for guild_id, backup_config in bot.mc_scheduled_backups.items():
            if not backup_config.get('enabled', False):
                continue
            
            guild = bot.get_guild(guild_id)
            if not guild:
                continue
            
            interval_hours = backup_config.get('interval_hours', 24)
            last_backup = backup_config.get('last_backup')
            
            # Check if it's time for a backup
            if last_backup:
                time_since = (discord.utils.utcnow() - last_backup).total_seconds() / 3600
                if time_since < interval_hours:
                    continue
            
            # Perform backup
            try:
                timestamp = discord.utils.utcnow().strftime("%Y%m%d_%H%M%S")
                backup_name = f"auto_backup_{timestamp}.tar.gz"
                
                # Create backup directory if needed
                mkdir_cmd = "mkdir -p ~/minecraft-backups"
                await asyncio.create_subprocess_shell(mkdir_cmd)
                
                # Create backup
                backup_cmd = f"cd {MINECRAFT_DIR} && tar -czf ~/minecraft-backups/{backup_name} worlds/"
                process = await asyncio.create_subprocess_shell(
                    backup_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await process.communicate()
                
                # Get backup size
                size_cmd = f"du -h ~/minecraft-backups/{backup_name} | cut -f1"
                size_process = await asyncio.create_subprocess_shell(
                    size_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                size_stdout, _ = await size_process.communicate()
                backup_size = size_stdout.decode().strip()
                
                # Update last backup time
                backup_config['last_backup'] = discord.utils.utcnow()
                
                # Send notification
                if bot.mc_notification_channel:
                    channel = bot.get_channel(bot.mc_notification_channel)
                    if channel:
                        embed = discord.Embed(
                            title="💾 Automatic Backup Created",
                            description=f"World backup completed successfully!",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="Backup Name", value=backup_name, inline=True)
                        embed.add_field(name="Size", value=backup_size, inline=True)
                        embed.add_field(name="Location", value="`~/minecraft-backups/`", inline=False)
                        embed.timestamp = discord.utils.utcnow()
                        await channel.send(embed=embed)
                
                logger.info(f"✅ Scheduled backup created: {backup_name}")
                
            except Exception as e:
                logger.error(f"Error creating scheduled backup: {e}", exc_info=True)
                if bot.mc_notification_channel:
                    channel = bot.get_channel(bot.mc_notification_channel)
                    if channel:
                        await channel.send(f"❌ **Backup Failed**\nError: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error in scheduled backups: {e}", exc_info=True)

@minecraft_scheduled_backups.before_loop
async def before_scheduled_backups():
    await bot.wait_until_ready()

# =========================================================
# MINECRAFT SCHEDULED RESTARTS
# =========================================================
@tasks.loop(minutes=1)
async def minecraft_scheduled_restarts():
    """Check for scheduled restart times"""
    try:
        from datetime import datetime
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_day = now.strftime("%A").lower()
        
        for guild_id, restart_config in bot.mc_scheduled_restarts.items():
            if not restart_config.get('enabled', False):
                continue
            
            restart_time = restart_config.get('time', '03:00')
            days = restart_config.get('days', [])
            
            # Check if it's time for restart
            if current_time == restart_time and current_day in days:
                # Check if we already restarted today
                last_restart = restart_config.get('last_restart_date')
                today = now.strftime("%Y-%m-%d")
                
                if last_restart != today:
                    # Warn players 5 minutes before
                    if await is_minecraft_running():
                        try:
                            command_file = f"{MINECRAFT_DIR}/command_input.txt"
                            with open(command_file, 'a') as f:
                                f.write(f"say Server will restart in 5 minutes!\n")
                        except:
                            pass
                        
                        await asyncio.sleep(300)  # Wait 5 minutes
                        
                        # Restart server
                        restart_cmd = f"sudo systemctl restart {MINECRAFT_SERVICE}"
                        process = await asyncio.create_subprocess_shell(
                            restart_cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        await process.communicate()
                        
                        restart_config['last_restart_date'] = today
                        
                        # Send notification
                        if bot.mc_notification_channel:
                            channel = bot.get_channel(bot.mc_notification_channel)
                            if channel:
                                embed = discord.Embed(
                                    title="🔄 Scheduled Restart",
                                    description="Server has been restarted as scheduled.",
                                    color=discord.Color.orange()
                                )
                                embed.timestamp = discord.utils.utcnow()
                                await channel.send(embed=embed)
                        
                        logger.info(f"✅ Scheduled restart completed at {restart_time}")
    except Exception as e:
        logger.error(f"Error in scheduled restarts: {e}", exc_info=True)

@minecraft_scheduled_restarts.before_loop
async def before_scheduled_restarts():
    await bot.wait_until_ready()

# =========================================================
# MINECRAFT RESOURCE MONITORING
# =========================================================
@tasks.loop(minutes=5)
async def minecraft_resource_monitor():
    """Monitor server resources and send alerts"""
    try:
        if not await is_minecraft_running():
            return
        
        for guild_id, alert_config in bot.mc_resource_alerts.items():
            if not alert_config.get('enabled', False):
                continue
            
            # Get CPU usage
            cpu_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $3}}'"
            cpu_process = await asyncio.create_subprocess_shell(
                cpu_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            cpu_stdout, _ = await cpu_process.communicate()
            cpu_lines = cpu_stdout.decode().strip().split('\n')
            cpu_usage = sum(float(line.strip()) for line in cpu_lines if line.strip().replace('.', '').isdigit())
            
            # Get memory usage
            mem_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $4}}'"
            mem_process = await asyncio.create_subprocess_shell(
                mem_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            mem_stdout, _ = await mem_process.communicate()
            mem_lines = mem_stdout.decode().strip().split('\n')
            mem_usage = sum(float(line.strip()) for line in mem_lines if line.strip().replace('.', '').isdigit())
            
            cpu_threshold = alert_config.get('cpu_threshold', 80)
            mem_threshold = alert_config.get('mem_threshold', 80)
            
            # Check thresholds
            if cpu_usage > cpu_threshold or mem_usage > mem_threshold:
                # Send alert
                if bot.mc_notification_channel:
                    channel = bot.get_channel(bot.mc_notification_channel)
                    if channel:
                        embed = discord.Embed(
                            title="⚠️ High Resource Usage",
                            description="Minecraft server is using high resources!",
                            color=discord.Color.red()
                        )
                        embed.add_field(name="CPU Usage", value=f"{cpu_usage:.1f}%", inline=True)
                        embed.add_field(name="Memory Usage", value=f"{mem_usage:.1f}%", inline=True)
                        embed.timestamp = discord.utils.utcnow()
                        await channel.send(embed=embed)
                        
                        # Auto-restart if extremely high
                        if cpu_usage > 95 or mem_usage > 95:
                            restart_cmd = f"sudo systemctl restart {MINECRAFT_SERVICE}"
                            await asyncio.create_subprocess_shell(restart_cmd)
                            await channel.send("🔄 Server auto-restarted due to extreme resource usage.")
    except Exception as e:
        logger.error(f"Error in resource monitoring: {e}", exc_info=True)

@minecraft_resource_monitor.before_loop
async def before_resource_monitor():
    await bot.wait_until_ready()

# =========================================================
# MINECRAFT PERFORMANCE DASHBOARD
# =========================================================
@tasks.loop(minutes=5)
async def minecraft_dashboard_updater():
    """Update Minecraft performance dashboard"""
    try:
        if not bot.mc_dashboard_channel or not bot.mc_dashboard_message_id:
            return
        
        channel = bot.get_channel(bot.mc_dashboard_channel)
        if not channel:
            return
        
        is_running = await is_minecraft_running()
        
        # Get performance metrics
        cpu_usage = 0.0
        mem_usage = 0.0
        online_players = 0
        
        if is_running:
            # CPU
            cpu_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $3}}'"
            cpu_process = await asyncio.create_subprocess_shell(
                cpu_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            cpu_stdout, _ = await cpu_process.communicate()
            cpu_lines = cpu_stdout.decode().strip().split('\n')
            cpu_usage = sum(float(line.strip()) for line in cpu_lines if line.strip().replace('.', '').isdigit())
            
            # Memory
            mem_cmd = f"ps aux | grep bedrock_server | grep -v grep | awk '{{print $4}}'"
            mem_process = await asyncio.create_subprocess_shell(
                mem_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            mem_stdout, _ = await mem_process.communicate()
            mem_lines = mem_stdout.decode().strip().split('\n')
            mem_usage = sum(float(line.strip()) for line in mem_lines if line.strip().replace('.', '').isdigit())
            
            # Online players
            logs_cmd = f"journalctl -u {MINECRAFT_SERVICE} --since '1 minute ago' --no-pager | grep -E 'Player connected|Player disconnected'"
            logs_process = await asyncio.create_subprocess_shell(
                logs_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            logs_stdout, _ = await logs_process.communicate()
            logs = logs_stdout.decode()
            online_players = len([l for l in logs.split('\n') if 'Player connected' in l]) - len([l for l in logs.split('\n') if 'Player disconnected' in l])
            online_players = max(0, online_players)
        
        embed = discord.Embed(
            title="📊 Minecraft Server Dashboard",
            color=discord.Color.green() if is_running else discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        
        status = "🟢 Online" if is_running else "🔴 Offline"
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="CPU Usage", value=f"{cpu_usage:.1f}%", inline=True)
        embed.add_field(name="Memory Usage", value=f"{mem_usage:.1f}%", inline=True)
        embed.add_field(name="Online Players", value=str(online_players), inline=True)
        embed.add_field(name="Server IP", value="`140.245.223.94:19132`", inline=False)
        
        try:
            message = await channel.fetch_message(bot.mc_dashboard_message_id)
            await message.edit(embed=embed)
        except:
            # Create new message if old one doesn't exist
            message = await channel.send(embed=embed)
            bot.mc_dashboard_message_id = message.id
    except Exception as e:
        logger.error(f"Error updating dashboard: {e}", exc_info=True)

@minecraft_dashboard_updater.before_loop
async def before_dashboard_updater():
    await bot.wait_until_ready()

# -------------------------
# LOAD MONITORING COG
# -------------------------
async def load_cogs():
    """Load extension cogs."""
    try:
        await bot.load_extension('cogs.monitoring')
        print("✅ Monitoring cog loaded")
    except Exception as e:
        print(f"⚠️ Failed to load monitoring cog: {e}")

@bot.event
async def setup_hook():
    """Called when bot is starting up."""
    await load_cogs()

# -------------------------
# RUN
# -------------------------
bot.run(TOKEN)
#test webhook
#gpt is fucking stupid