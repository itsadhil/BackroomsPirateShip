"""
Instagram Reels integration: manage config and monitor dashboard from Discord.
Scrapes reels/shorts and auto-posts to your Instagram account; all config via Discord.
"""
import asyncio
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config.settings import settings

logger = logging.getLogger(__name__)

# Lazy init of instagram_reels (after first use)
try:
    import instagram_reels as ig
except Exception as e:
    ig = None
    _ig_import_error = str(e)


def _admin_only(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    if getattr(settings, "ADMIN_ROLE_ID", None):
        role = discord.utils.get(interaction.user.roles, id=settings.ADMIN_ROLE_ID)
        if role:
            return True
    return False


def _ensure_ig():
    if ig is None:
        raise RuntimeError(f"Instagram Reels module not available: {_ig_import_error}")
    if not ig.is_initialized():
        base = getattr(settings, "INSTAGRAM_DATA_DIR", "data/instagram")
        ig.init(base)
    return ig


class Instagram(commands.Cog):
    """Manage Instagram Reels scraper and auto-poster from Discord."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="instagram")
        self._ig_api = None
        self._next_reels_at: Optional[datetime] = None
        self._next_poster_at: Optional[datetime] = None
        self._next_remover_at: Optional[datetime] = None
        self._next_shorts_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        try:
            _ensure_ig()
            self.reels_loop.start()
            self.poster_loop.start()
            self.remover_loop.start()
            self.shorts_loop.start()
        except Exception as e:
            logger.warning("Instagram Reels cog: could not start loops (module or config missing): %s", e)

    def cog_unload(self):
        self.reels_loop.cancel()
        self.poster_loop.cancel()
        self.remover_loop.cancel()
        self.shorts_loop.cancel()
        self._executor.shutdown(wait=False)

    async def _get_api(self):
        """Get or create Instagram API client (blocking call in executor)."""
        if self._ig_api is not None:
            return self._ig_api
        loop = asyncio.get_event_loop()
        username = getattr(settings, "INSTAGRAM_USERNAME", "") or None
        password = getattr(settings, "INSTAGRAM_PASSWORD", "") or None
        if not username or not password:
            cfg = _ensure_ig()
            username = cfg.get_config("USERNAME") or ""
            password = cfg.get_config("PASSWORD") or ""
        if not username or not password:
            return None

        def _login():
            cfg = _ensure_ig()
            if username:
                cfg.set_config("USERNAME", username)
            if password:
                cfg.set_config("PASSWORD", password)
            return cfg.login(username, password)

        try:
            self._ig_api = await loop.run_in_executor(self._executor, _login)
            return self._ig_api
        except Exception as e:
            self._last_error = str(e)
            logger.exception("Instagram login failed")
            return None

    @tasks.loop(seconds=60)
    async def reels_loop(self):
        """Periodically scrape reels from configured accounts."""
        try:
            cfg = _ensure_ig()
            if cfg.get_config("IS_ENABLED_REELS_SCRAPER") != "1":
                return
            if self._next_reels_at and datetime.now() < self._next_reels_at:
                return
            api = await self._get_api()
            if not api:
                return
            interval_min = int(cfg.get_config("SCRAPER_INTERVAL_IN_MIN") or "720")
            self._next_reels_at = datetime.now() + timedelta(minutes=interval_min)
            await asyncio.get_event_loop().run_in_executor(
                self._executor, lambda: cfg.run_reels_scrape(api)
            )
        except Exception as e:
            self._last_error = str(e)
            logger.exception("Reels scrape failed")

    @reels_loop.before_loop
    async def before_reels_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def poster_loop(self):
        """Periodically post one reel to Instagram."""
        try:
            cfg = _ensure_ig()
            if cfg.get_config("IS_ENABLED_AUTO_POSTER") != "1":
                return
            if self._next_poster_at and datetime.now() < self._next_poster_at:
                return
            api = await self._get_api()
            if not api:
                return
            interval_min = int(cfg.get_config("POSTING_INTERVAL_IN_MIN") or "15")
            self._next_poster_at = datetime.now() + timedelta(
                seconds=interval_min * 60 + random.randint(5, 20)
            )
            await asyncio.get_event_loop().run_in_executor(
                self._executor, lambda: cfg.run_poster(api)
            )
        except Exception as e:
            self._last_error = str(e)
            logger.exception("Poster failed")

    @poster_loop.before_loop
    async def before_poster_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def remover_loop(self):
        """Remove posted files from disk periodically."""
        try:
            cfg = _ensure_ig()
            if cfg.get_config("IS_REMOVE_FILES") != "1":
                return
            if self._next_remover_at and datetime.now() < self._next_remover_at:
                return
            interval_min = int(cfg.get_config("REMOVE_FILE_AFTER_MINS") or "120")
            self._next_remover_at = datetime.now() + timedelta(minutes=interval_min)
            await asyncio.get_event_loop().run_in_executor(self._executor, cfg.run_remover)
        except Exception as e:
            self._last_error = str(e)
            logger.exception("Remover failed")

    @remover_loop.before_loop
    async def before_remover_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def shorts_loop(self):
        """Periodically scrape YouTube shorts."""
        try:
            cfg = _ensure_ig()
            if cfg.get_config("IS_ENABLED_YOUTUBE_SCRAPING") != "1":
                return
            if self._next_shorts_at and datetime.now() < self._next_shorts_at:
                return
            interval_min = int(cfg.get_config("SCRAPER_INTERVAL_IN_MIN") or "720")
            self._next_shorts_at = datetime.now() + timedelta(minutes=interval_min)
            await asyncio.get_event_loop().run_in_executor(self._executor, cfg.run_shorts)
        except Exception as e:
            self._last_error = str(e)
            logger.exception("Shorts scrape failed")

    @shorts_loop.before_loop
    async def before_shorts_loop(self):
        await self.bot.wait_until_ready()

    # ---------- Slash command group ----------
    ig_group = app_commands.Group(name="instagram", description="Manage Instagram Reels scraper and auto-poster")

    @ig_group.command(name="dashboard", description="View Instagram Reels dashboard (total, posted, pending, next runs)")
    async def dashboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            cfg = _ensure_ig()
            data = cfg.get_dashboard_data()
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        embed = discord.Embed(
            title="📊 Instagram Reels Dashboard",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Total reels", value=str(data["total"]), inline=True)
        embed.add_field(name="Posted", value=str(data["posted"]), inline=True)
        embed.add_field(name="Pending", value=str(data["pending"]), inline=True)
        if self._next_reels_at:
            embed.add_field(name="Next scrape", value=f"<t:{int(self._next_reels_at.timestamp())}:R>", inline=True)
        if self._next_poster_at:
            embed.add_field(name="Next post", value=f"<t:{int(self._next_poster_at.timestamp())}:R>", inline=True)
        if self._last_error:
            embed.add_field(name="Last error", value=self._last_error[:500], inline=False)
        rows = []
        for r in data["latest"][:5]:
            rid, post_id, account, code, is_posted, posted_at = r
            status = "✅" if is_posted else "⏳"
            link = f"https://instagram.com/p/{code}/" if code else "-"
            rows.append(f"{status} [{account}] [View]({link})")
        if rows:
            embed.add_field(name="Latest", value="\n".join(rows), inline=False)
        await interaction.followup.send(embed=embed)

    @ig_group.command(name="config", description="Show current Instagram Reels configuration")
    async def config_show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            cfg = _ensure_ig()
            all_cfg = cfg.get_all_config()
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        embed = discord.Embed(
            title="⚙️ Instagram Reels Config",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow(),
        )
        # Mask password
        for k, v in list(all_cfg.items()):
            if k == "PASSWORD" and v:
                all_cfg[k] = "••••••••"
        for k in ["IS_ENABLED_REELS_SCRAPER", "IS_ENABLED_AUTO_POSTER", "IS_POST_TO_STORY", "IS_REMOVE_FILES", "IS_ENABLED_YOUTUBE_SCRAPING",
                  "SCRAPER_INTERVAL_IN_MIN", "POSTING_INTERVAL_IN_MIN", "FETCH_LIMIT", "REMOVE_FILE_AFTER_MINS",
                  "USERNAME", "ACCOUNTS", "HASTAGS", "CHANNEL_LINKS"]:
            v = all_cfg.get(k, "-")
            if isinstance(v, str) and len(v) > 80:
                v = v[:77] + "..."
            embed.add_field(name=k, value=str(v) or "-", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @ig_group.command(name="accounts", description="List Instagram accounts to scrape reels from")
    async def accounts_list(self, interaction: discord.Interaction):
        try:
            cfg = _ensure_ig()
            raw = cfg.get_config("ACCOUNTS") or ""
            accounts = [a.strip() for a in raw.split(",") if a.strip()]
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        if not accounts:
            await interaction.response.send_message("No accounts configured. Use `/instagram accounts-set` to add (comma-separated).", ephemeral=True)
            return
        await interaction.response.send_message(
            "**Accounts to scrape:**\n" + "\n".join(f"• `{a}`" for a in accounts),
            ephemeral=True,
        )

    @ig_group.command(name="accounts-set", description="Set Instagram accounts to scrape (comma-separated). Admin only.")
    @app_commands.describe(usernames="Comma-separated usernames, e.g. user1,user2")
    async def accounts_set(self, interaction: discord.Interaction, usernames: str):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        try:
            cfg = _ensure_ig()
            cfg.set_config("ACCOUNTS", usernames.replace(" ", ""))
            await interaction.response.send_message(f"✅ Accounts set to: {usernames}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @ig_group.command(name="channels", description="List YouTube channels to scrape shorts from")
    async def channels_list(self, interaction: discord.Interaction):
        try:
            cfg = _ensure_ig()
            raw = cfg.get_config("CHANNEL_LINKS") or ""
            channels = [c.strip() for c in raw.split(",") if c.strip()]
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        if not channels:
            await interaction.response.send_message("No channels configured. Use `/instagram channels-set` to add.", ephemeral=True)
            return
        await interaction.response.send_message(
            "**YouTube channels:**\n" + "\n".join(f"• {c}" for c in channels),
            ephemeral=True,
        )

    @ig_group.command(name="channels-set", description="Set YouTube channel URLs (comma-separated). Admin only.")
    @app_commands.describe(urls="Comma-separated channel URLs")
    async def channels_set(self, interaction: discord.Interaction, urls: str):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        try:
            cfg = _ensure_ig()
            cfg.set_config("CHANNEL_LINKS", urls.strip())
            await interaction.response.send_message("✅ YouTube channels set.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @ig_group.command(name="set-interval", description="Set scraper or posting interval (minutes). Admin only.")
    @app_commands.describe(kind="scraping or posting", minutes="Interval in minutes")
    @app_commands.choices(kind=[
        app_commands.Choice(name="scraping", value="scraping"),
        app_commands.Choice(name="posting", value="posting"),
    ])
    async def set_interval(self, interaction: discord.Interaction, kind: app_commands.Choice[str], minutes: int):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        if minutes < 1 or minutes > 10080:
            await interaction.response.send_message("❌ Minutes must be between 1 and 10080.", ephemeral=True)
            return
        try:
            cfg = _ensure_ig()
            if kind.value == "scraping":
                cfg.set_config("SCRAPER_INTERVAL_IN_MIN", str(minutes))
                await interaction.response.send_message(f"✅ Scraper interval set to **{minutes}** minutes.", ephemeral=True)
            else:
                cfg.set_config("POSTING_INTERVAL_IN_MIN", str(minutes))
                await interaction.response.send_message(f"✅ Posting interval set to **{minutes}** minutes.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @ig_group.command(name="set-hashtags", description="Set hashtags for auto-posted reels. Admin only.")
    @app_commands.describe(hashtags="Hashtags string, e.g. #reels #shorts")
    async def set_hashtags(self, interaction: discord.Interaction, hashtags: str):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        try:
            cfg = _ensure_ig()
            cfg.set_config("HASTAGS", hashtags.strip())
            await interaction.response.send_message("✅ Hashtags updated.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @ig_group.command(name="enable", description="Enable scraper, poster, YouTube shorts, or file remover. Admin only.")
    @app_commands.describe(feature="Feature to enable")
    @app_commands.choices(feature=[
        app_commands.Choice(name="scraper", value="scraper"),
        app_commands.Choice(name="poster", value="poster"),
        app_commands.Choice(name="youtube", value="youtube"),
        app_commands.Choice(name="remover", value="remover"),
    ])
    async def enable_feature(self, interaction: discord.Interaction, feature: app_commands.Choice[str]):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        key = {"scraper": "IS_ENABLED_REELS_SCRAPER", "poster": "IS_ENABLED_AUTO_POSTER",
               "youtube": "IS_ENABLED_YOUTUBE_SCRAPING", "remover": "IS_REMOVE_FILES"}[feature.value]
        try:
            cfg = _ensure_ig()
            cfg.set_config(key, "1")
            await interaction.response.send_message(f"✅ **{feature.name}** enabled.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @ig_group.command(name="disable", description="Disable scraper, poster, YouTube shorts, or file remover. Admin only.")
    @app_commands.describe(feature="Feature to disable")
    @app_commands.choices(feature=[
        app_commands.Choice(name="scraper", value="scraper"),
        app_commands.Choice(name="poster", value="poster"),
        app_commands.Choice(name="youtube", value="youtube"),
        app_commands.Choice(name="remover", value="remover"),
    ])
    async def disable_feature(self, interaction: discord.Interaction, feature: app_commands.Choice[str]):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        key = {"scraper": "IS_ENABLED_REELS_SCRAPER", "poster": "IS_ENABLED_AUTO_POSTER",
               "youtube": "IS_ENABLED_YOUTUBE_SCRAPING", "remover": "IS_REMOVE_FILES"}[feature.value]
        try:
            cfg = _ensure_ig()
            cfg.set_config(key, "0")
            await interaction.response.send_message(f"✅ **{feature.name}** disabled.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @ig_group.command(name="scrape-now", description="Manually trigger reels scrape once. Admin only.")
    async def scrape_now(self, interaction: discord.Interaction):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            cfg = _ensure_ig()
            api = await self._get_api()
            if not api:
                await interaction.followup.send("❌ Instagram not logged in. Set INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD in env (or credentials in config).", ephemeral=True)
                return
            await asyncio.get_event_loop().run_in_executor(
                self._executor, lambda: cfg.run_reels_scrape(api)
            )
            self._next_reels_at = datetime.now() + timedelta(minutes=int(cfg.get_config("SCRAPER_INTERVAL_IN_MIN") or "720"))
            await interaction.followup.send("✅ Reels scrape finished.")
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")

    @ig_group.command(name="post-now", description="Manually post one reel now. Admin only.")
    async def post_now(self, interaction: discord.Interaction):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            cfg = _ensure_ig()
            api = await self._get_api()
            if not api:
                await interaction.followup.send("❌ Instagram not logged in.", ephemeral=True)
                return
            await asyncio.get_event_loop().run_in_executor(
                self._executor, lambda: cfg.run_poster(api)
            )
            await interaction.followup.send("✅ Post attempt finished (check dashboard for status).")
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")

    @ig_group.command(name="scrape-shorts-now", description="Manually trigger YouTube shorts scrape once. Admin only.")
    async def scrape_shorts_now(self, interaction: discord.Interaction):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            cfg = _ensure_ig()
            await asyncio.get_event_loop().run_in_executor(self._executor, cfg.run_shorts)
            await interaction.followup.send("✅ Shorts scrape finished.")
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")

    @ig_group.command(name="set-youtube-key", description="Set YouTube API key for shorts scraping. Admin only.")
    @app_commands.describe(api_key="YouTube Data API v3 key")
    async def set_youtube_key(self, interaction: discord.Interaction, api_key: str):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        try:
            cfg = _ensure_ig()
            cfg.set_config("YOUTUBE_API_KEY", api_key.strip())
            await interaction.response.send_message("✅ YouTube API key saved.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)

    @ig_group.command(name="set-fetch-limit", description="Set how many reels to fetch per account per run. Admin only.")
    @app_commands.describe(limit="Number of latest reels to fetch (e.g. 10)")
    async def set_fetch_limit(self, interaction: discord.Interaction, limit: int):
        if not _admin_only(interaction):
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return
        if limit < 1 or limit > 50:
            await interaction.response.send_message("❌ Limit must be between 1 and 50.", ephemeral=True)
            return
        try:
            cfg = _ensure_ig()
            cfg.set_config("FETCH_LIMIT", str(limit))
            await interaction.response.send_message(f"✅ Fetch limit set to **{limit}**.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Instagram(bot))
