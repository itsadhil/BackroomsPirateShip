import os
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiohttp
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

# -------------------------
# LOAD ENV
# -------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

# -------------------------
# CONFIG (REPLACE IDS)
# -------------------------
GUILD_ID = 1053630118360260650
INPUT_CHANNEL_ID = 1456371838325096733
OUTPUT_CHANNEL_ID = 1456386547879514317 # This should be a FORUM CHANNEL
REQUEST_CHANNEL_ID = 1456387358952919132 # Channel for game requests
DASHBOARD_CHANNEL_ID = 1456480472128425985 # UI/Dashboard channel
ALLOWED_CHANNEL_ID = 1456371610473988388 # Channel where non-admins can use commands
ADMIN_ROLE_ID = 1072117821397540954 # Admin role that can use commands anywhere

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

# Playwright queue system
bot.playwright_queue = asyncio.Queue()  # Queue for download requests
bot.playwright_active = False  # Track if Playwright is currently running
bot.queue_position = {}  # Track position in queue for users

# Files for persistent storage
RSS_SEEN_FILE = "fitgirl_seen_posts.json"
BOT_STATE_FILE = "bot_state.json"

# Load previously seen posts
def load_seen_posts():
    try:
        if os.path.exists(RSS_SEEN_FILE):
            with open(RSS_SEEN_FILE, 'r') as f:
                bot.seen_rss_posts = set(json.load(f))
                print(f"✅ Loaded {len(bot.seen_rss_posts)} seen RSS posts")
    except Exception as e:
        print(f"⚠️ Could not load seen posts: {e}")

def save_seen_posts():
    try:
        with open(RSS_SEEN_FILE, 'w') as f:
            json.dump(list(bot.seen_rss_posts), f)
    except Exception as e:
        print(f"⚠️ Could not save seen posts: {e}")

# Load bot state (dashboard ID, contributor stats)
def load_bot_state():
    try:
        if os.path.exists(BOT_STATE_FILE):
            with open(BOT_STATE_FILE, 'r') as f:
                state = json.load(f)
                bot.dashboard_message_id = state.get('dashboard_message_id')
                bot.contributor_stats = state.get('contributor_stats', {})
                bot.status_message_id = state.get('status_message_id')
                print(f"✅ Loaded bot state (Dashboard ID: {bot.dashboard_message_id}, Contributors: {len(bot.contributor_stats)})")
    except Exception as e:
        print(f"⚠️ Could not load bot state: {e}")

def save_bot_state():
    try:
        state = {
            'dashboard_message_id': bot.dashboard_message_id,
            'contributor_stats': bot.contributor_stats,
            'status_message_id': bot.status_message_id,
            'last_updated': discord.utils.utcnow().isoformat()
        }
        with open(BOT_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        print(f"💾 Bot state saved (Dashboard: {bot.dashboard_message_id})")
    except Exception as e:
        print(f"⚠️ Could not save bot state: {e}")

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
            embed = discord.Embed(
                title="🟢 Bot Online",
                description=f"Bot is now online and ready to serve!\n\n**Started:** {timestamp}",
                color=discord.Color.green(),
                timestamp=now
            )
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
        print(f"✅ Created new status message: {status}")
        
    except Exception as e:
        print(f"⚠️ Error updating status message: {e}")

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
    
    async def close(self):
        """Close the aiohttp session."""
        if self.session:
            await self.session.close()

# Initialize IGDB client
igdb_client = IGDBClient(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)

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

# Initialize RAWG client
rawg_client = RAWGClient()

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
        try:
            # Add to queue
            if request_id:
                queue_size = bot.playwright_queue.qsize()
                bot.queue_position[request_id] = queue_size + 1
            
            print(f"🌐 Opening headless browser for: {paste_url}")
            
            async with async_playwright() as p:
                # Launch browser in headless mode
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                page = await context.new_page()
                
                # Navigate to the paste URL
                await page.goto(paste_url, wait_until='domcontentloaded', timeout=30000)
                print(f"📄 Page loaded, waiting for download link...")
                
                # Wait for the download link to appear (it's generated by JavaScript)
                # The link has class 'alert-link' and download attribute
                try:
                    await page.wait_for_selector('a.alert-link[download]', timeout=15000)
                    print(f"✅ Download link appeared!")
                except:
                    print(f"⚠️ Download link did not appear in time")
                    await browser.close()
                    return None
                
                # Get the download link element
                download_link = await page.query_selector('a.alert-link[download]')
                if not download_link:
                    print(f"⚠️ Could not find download link")
                    await browser.close()
                    return None
                
                # Get the blob URL
                href = await download_link.get_attribute('href')
                print(f"🔗 Found download link: {href}")
                
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
                
                await browser.close()
                
                if torrent_base64:
                    import base64
                    torrent_data = base64.b64decode(torrent_base64)
                    print(f"✅ Downloaded torrent: {len(torrent_data)} bytes")
                    return torrent_data
                else:
                    print(f"⚠️ Failed to extract torrent data")
                    return None
                    
        except Exception as e:
            print(f"⚠️ Error downloading torrent with browser: {e}")
            import traceback
            traceback.print_exc()
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
    
    # SECOND: Update status to show bot is starting (will edit existing message if found)
    await update_status_message("starting")
    
    # Copy global commands to the guild for fast development
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"✅ Logged in as {bot.user}")
    print(f"✅ Commands synced to guild ID: {GUILD_ID}")
    
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
    
    # Start Playwright queue processor
    if not playwright_queue_processor.is_running():
        playwright_queue_processor.start()
        print(f"✅ Playwright queue processor started")
    
    # LAST: Update status to online after everything is loaded
    await update_status_message("online")

@bot.event
async def on_close():
    """Clean up resources on bot shutdown."""
    await update_status_message("restarting")
    await igdb_client.close()
    save_seen_posts()
    save_bot_state()

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
        
        print(f"📦 Processing queue item: {request_id}")
        
        # Download torrent
        torrent_data = await fitgirl_scraper.download_torrent_from_paste(paste_url, request_id)
        
        # Call callback with result
        await callback(torrent_data)
        
    except Exception as e:
        print(f"⚠️ Error in queue processor: {e}")
        import traceback
        traceback.print_exc()
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
        
        new_posts = []
        for entry in feed.entries[:10]:  # Check last 10 entries for better coverage
            post_id = entry.get('id') or entry.get('link')
            
            # Skip if already seen
            if post_id in bot.seen_rss_posts:
                continue
            
            # Skip "Updates Digest" posts
            title = entry.get('title', '')
            if 'Updates Digest' in title or 'updates digest' in title.lower():
                print(f"⏭️ Skipping Updates Digest: {title}")
                bot.seen_rss_posts.add(post_id)
                continue
            
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
        
        print(f"🆕 Found {len(new_posts)} new FitGirl release(s)!")
        
        # Process each new post
        for post in new_posts:
            try:
                print(f"📥 Processing: {post['title']}")
                
                # Get game details and torrent link
                paste_url = await fitgirl_scraper.get_torrent_link(post['link'])
                if not paste_url:
                    print(f"⚠️ No torrent link found for: {post['title']}")
                    bot.seen_rss_posts.add(post['id'])
                    continue
                
                # Check if game already exists in forum
                clean_name = clean_game_name_for_search(post['title'])
                output_channel = bot.get_channel(OUTPUT_CHANNEL_ID)
                
                existing = False
                async for thread in output_channel.archived_threads(limit=100):
                    thread_clean = clean_game_name_for_search(thread.name)
                    if thread_clean.lower() == clean_name.lower():
                        existing = True
                        print(f"⏭️ Game already exists: {post['title']}")
                        break
                
                if not existing:
                    for thread in output_channel.threads:
                        thread_clean = clean_game_name_for_search(thread.name)
                        if thread_clean.lower() == clean_name.lower():
                            existing = True
                            print(f"⏭️ Game already exists: {post['title']}")
                            break
                
                if existing:
                    bot.seen_rss_posts.add(post['id'])
                    continue
                
                # Download torrent
                print(f"⬇️ Downloading torrent for: {post['title']}")
                torrent_data = await fitgirl_scraper.download_torrent_from_paste(paste_url)
                
                if not torrent_data:
                    print(f"⚠️ Failed to download torrent for: {post['title']}")
                    bot.seen_rss_posts.add(post['id'])
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
                
                # Small delay between posts
                await asyncio.sleep(10)
                
            except Exception as e:
                print(f"⚠️ Error processing RSS post: {e}")
                import traceback
                traceback.print_exc()
                bot.seen_rss_posts.add(post['id'])
        
        # Save seen posts
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
        ),
        inline=False
    )
    
    # Request Commands
    embed.add_field(
        name="🎯 Game Requests",
        value=(
            "`/requestgame` - Request a game from moderators\n"
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
            "• Use `/fgsearch` to find and auto-download FitGirl games\n"
            "• Click the **⚠️ Report Issue** button on game posts to report problems\n"
            "• Games are automatically posted from FitGirl RSS every 2 hours\n"
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
        # Check if user has the required role
        REQUIRED_ROLE_ID = 1072117821397540954
        has_role = any(role.id == REQUIRED_ROLE_ID for role in interaction.user.roles)
        
        if not has_role:
            await interaction.response.send_message(
                "❌ You don't have permission to add games.",
                ephemeral=True,
                delete_after=30
            )
            return
        
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
            
            # Add to queue
            await bot.playwright_queue.put({
                'paste_url': paste_url,
                'request_id': request_id,
                'callback': download_callback
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
            print(f"⚠️ Error in download_button: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send(
                f"❌ An error occurred: {str(e)}",
                ephemeral=True
            )
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
    if not await check_command_permissions(interaction):
        return
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
    if user.id not in bot.pending_torrents:
        return
    
    data = bot.pending_torrents.pop(user.id)
    torrent_data = data.get('torrent_data')
    
    if not torrent_data:
        return
    
    output_channel = bot.get_channel(data['channel_id'])
    input_channel = bot.get_channel(INPUT_CHANNEL_ID)
    
    # Clean the game name for better IGDB/RAWG search results
    clean_name = clean_game_name_for_search(data['game_name'])
    
    # Search IGDB for game data (with RAWG fallback)
    igdb_data = await igdb_client.search_game_by_name(clean_name)
    
    # If IGDB fails, try RAWG as fallback
    if not igdb_data:
        print(f"🔄 IGDB failed, trying RAWG fallback for: {clean_name}")
        igdb_data = await rawg_client.search_game_by_name(clean_name)
        if igdb_data:
            print(f"✅ RAWG found data for: {clean_name}")
    
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
                print(f"⚠️ Could not verify image: {e}")
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
    thread = await output_channel.create_thread(
        name=thread_name,
        content=f"**{thread_name}**",
        embed=embed,
        file=public_torrent_file,
        view=view
    )
    
    # Get public torrent URL and update with button
    starter_message = thread.message
    if starter_message.attachments:
        public_torrent_url = starter_message.attachments[0].url
        view = GameButtonView(data['game_link'], public_torrent_url)
        await starter_message.edit(view=view if view.children else None)
    
    # Log to input channel
    version_text = f"\n📦 **Version:** {data['version']}" if data.get('version') else ""
    await input_channel.send(
        f"📥 **New Game Submitted** (Auto from FitGirl)\n"
        f"👤 **User:** {user.mention}\n"
        f"🎮 **Game:** {data['game_name']}{version_text}\n"
        f"🔗 **Link:** {data['game_link'] or 'N/A'}\n"
        f"⬇️ **Torrent:** Attached\n"
        f"📦 **Thread:** {thread.thread.mention}"
    )
    
    # Track contributor
    if user.id not in bot.contributor_stats:
        bot.contributor_stats[user.id] = 0
    bot.contributor_stats[user.id] += 1
    save_bot_state()
    
    # Update dashboard immediately
    try:
        await update_dashboard()
    except:
        pass
    
    # DM the user
    try:
        dm_embed = discord.Embed(
            title="✅ Game Added Successfully!",
            description=f"**{thread_name}** has been added from FitGirl Repacks!",
            color=0x00FF00
        )
        dm_embed.add_field(
            name="🔗 View Game Thread",
            value=thread.thread.mention,
            inline=False
        )
        dm_embed.set_footer(text="FitGirl Auto-Add")
        
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        print(f"⚠️ Could not DM user {user.name} - DMs disabled")
    
    # Follow up in the interaction
    try:
        await interaction.followup.send(
            f"✅ **{thread_name}** has been added to {thread.thread.mention}!\n"
            f"📬 Check your DMs for details!",
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

# -------------------------
# RUN
# -------------------------
bot.run(TOKEN)
