import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
from typing import Optional, Dict, Any
import re
import io

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

# -------------------------
# READY EVENT WITH GUILD-SPECIFIC SYNC
# -------------------------
@bot.event
async def on_ready():
    # Copy global commands to the guild for fast development
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    print(f"✅ Logged in as {bot.user}")
    print(f"✅ Commands synced to guild ID: {GUILD_ID}")

@bot.event
async def on_close():
    """Clean up resources on bot shutdown."""
    await igdb_client.close()

# -------------------------
# PING COMMAND
# -------------------------
@bot.tree.command(name="ping", description="Check if bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot is running.", ephemeral=True)

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
            self.notes.value
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
            'game_link': self.game_link.value,
            'notes': self.notes.value,
            'channel_id': OUTPUT_CHANNEL_ID
        }

# =========================================================
# PROCESS GAME SUBMISSION (SHARED LOGIC)
# =========================================================
async def process_game_submission(interaction, game_name, game_link, torrent_link, notes):
    """Shared logic to process and post game submissions."""
    # Defer response since IGDB API call may take time
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
    await input_channel.send(
        f"📥 **New Game Submitted**\n"
        f"👤 **User:** {interaction.user.mention}\n"
        f"🎮 **Game:** {game_name}\n"
        f"🔗 **Link:** {game_link or 'N/A'}\n"
        f"📦 **Thread:** {thread.thread.mention}"
    )

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
                await input_channel.send(
                    f"📥 **New Game Submitted**\n"
                    f"👤 **User:** {message.author.mention}\n"
                    f"🎮 **Game:** {data['game_name']}\n"
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
