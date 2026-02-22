import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import subprocess
import psutil
import platform
from datetime import datetime
import traceback
import sys
import os

class Monitoring(commands.Cog):
    """System monitoring and logging features"""
    
    def __init__(self, bot):
        self.bot = bot
        self.webhook_url = os.getenv("MONITORING_WEBHOOK_URL")
        self.owner_id = int(os.getenv("BOT_OWNER_ID", "0"))
        self.monitoring_loop.start()
        self.last_cpu_alert = None
        self.last_memory_alert = None
        
    def cog_unload(self):
        self.monitoring_loop.cancel()
    
    async def send_webhook(self, embed: discord.Embed):
        """Send alert to Discord webhook"""
        if not self.webhook_url:
            return
            
        try:
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(
                    self.webhook_url, 
                    session=session
                )
                await webhook.send(embed=embed)
        except Exception as e:
            print(f"Failed to send webhook: {e}")
    
    @tasks.loop(minutes=5)
    async def monitoring_loop(self):
        """Monitor system resources every 5 minutes"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Alert on high CPU usage (>80%)
            if cpu_percent > 80:
                if not self.last_cpu_alert or (datetime.now() - self.last_cpu_alert).seconds > 1800:
                    embed = discord.Embed(
                        title="⚠️ High CPU Usage Alert",
                        description=f"CPU usage is at **{cpu_percent}%**",
                        color=discord.Color.orange(),
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="Threshold", value="80%", inline=True)
                    embed.add_field(name="Current", value=f"{cpu_percent}%", inline=True)
                    await self.send_webhook(embed)
                    self.last_cpu_alert = datetime.now()
            
            # Alert on high memory usage (>85%)
            if memory.percent > 85:
                if not self.last_memory_alert or (datetime.now() - self.last_memory_alert).seconds > 1800:
                    embed = discord.Embed(
                        title="⚠️ High Memory Usage Alert",
                        description=f"Memory usage is at **{memory.percent}%**",
                        color=discord.Color.orange(),
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="Threshold", value="85%", inline=True)
                    embed.add_field(name="Current", value=f"{memory.percent}%", inline=True)
                    embed.add_field(name="Used", value=f"{memory.used / (1024**3):.2f} GB", inline=True)
                    embed.add_field(name="Available", value=f"{memory.available / (1024**3):.2f} GB", inline=True)
                    await self.send_webhook(embed)
                    self.last_memory_alert = datetime.now()
                    
        except Exception as e:
            print(f"Monitoring loop error: {e}")
    
    @monitoring_loop.before_loop
    async def before_monitoring(self):
        await self.bot.wait_until_ready()
    
    @app_commands.command(name="serverstats", description="View server statistics")
    async def serverstats(self, interaction: discord.Interaction):
        """Display current server statistics"""
        
        # Get system info
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        
        embed = discord.Embed(
            title="📊 Server Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # System Info
        embed.add_field(
            name="🖥️ System",
            value=f"**OS:** {platform.system()} {platform.release()}\n"
                  f"**Python:** {platform.python_version()}\n"
                  f"**Uptime:** {uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m",
            inline=False
        )
        
        # CPU
        cpu_color = "🟢" if cpu_percent < 50 else "🟡" if cpu_percent < 80 else "🔴"
        embed.add_field(
            name=f"{cpu_color} CPU",
            value=f"**Usage:** {cpu_percent}%\n"
                  f"**Cores:** {psutil.cpu_count()}",
            inline=True
        )
        
        # Memory
        mem_color = "🟢" if memory.percent < 70 else "🟡" if memory.percent < 85 else "🔴"
        embed.add_field(
            name=f"{mem_color} Memory",
            value=f"**Usage:** {memory.percent}%\n"
                  f"**Used:** {memory.used / (1024**3):.2f} GB\n"
                  f"**Total:** {memory.total / (1024**3):.2f} GB",
            inline=True
        )
        
        # Disk
        disk_color = "🟢" if disk.percent < 70 else "🟡" if disk.percent < 85 else "🔴"
        embed.add_field(
            name=f"{disk_color} Disk",
            value=f"**Usage:** {disk.percent}%\n"
                  f"**Used:** {disk.used / (1024**3):.2f} GB\n"
                  f"**Total:** {disk.total / (1024**3):.2f} GB",
            inline=True
        )
        
        # Bot Stats
        embed.add_field(
            name="🤖 Bot",
            value=f"**Latency:** {round(self.bot.latency * 1000)}ms\n"
                  f"**Guilds:** {len(self.bot.guilds)}\n"
                  f"**Users:** {len(self.bot.users)}",
            inline=True
        )
        
        embed.set_footer(text=f"Requested by {interaction.user.name}")
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="serverlogs", description="View recent bot logs (Owner only)")
    async def serverlogs(self, interaction: discord.Interaction, lines: int = 50):
        """View recent systemd logs"""
        
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This command is owner-only!", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get logs from systemd
            result = subprocess.run(
                ['journalctl', '-u', 'discord-bot', '-n', str(lines), '--no-pager'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            logs = result.stdout
            
            if not logs:
                await interaction.followup.send("No logs found.", ephemeral=True)
                return
            
            # Split into chunks if too long
            if len(logs) > 1900:
                chunks = [logs[i:i+1900] for i in range(0, len(logs), 1900)]
                for i, chunk in enumerate(chunks[:3]):  # Max 3 chunks
                    embed = discord.Embed(
                        title=f"📋 Bot Logs (Part {i+1}/{min(len(chunks), 3)})",
                        description=f"```\n{chunk}\n```",
                        color=discord.Color.blue(),
                        timestamp=datetime.now()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(
                    title="📋 Bot Logs",
                    description=f"```\n{logs}\n```",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                
        except subprocess.TimeoutExpired:
            await interaction.followup.send("❌ Command timed out.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    @app_commands.command(name="restartbot", description="Restart the bot (Owner only)")
    async def restartbot(self, interaction: discord.Interaction):
        """Restart the bot service"""
        
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This command is owner-only!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🔄 Restarting Bot",
            description="Bot will restart in a few seconds...",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        await interaction.response.send_message(embed=embed)
        
        # Send webhook notification
        webhook_embed = discord.Embed(
            title="🔄 Bot Restart Initiated",
            description=f"Restart requested by {interaction.user.mention}",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        await self.send_webhook(webhook_embed)
        
        # Restart using systemctl
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'discord-bot'])
        except Exception as e:
            print(f"Restart error: {e}")
            # Fallback: exit and let systemd restart us
            await asyncio.sleep(1)
            await self.bot.close()
            sys.exit(0)
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Send startup notification"""
        embed = discord.Embed(
            title="✅ Bot Started",
            description=f"**{self.bot.user.name}** is now online!",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        
        # System info
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        embed.add_field(name="CPU", value=f"{cpu_percent}%", inline=True)
        embed.add_field(name="Memory", value=f"{memory.percent}%", inline=True)
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        
        await self.send_webhook(embed)
    
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Log command errors to webhook"""
        if isinstance(error, commands.CommandNotFound):
            return
            
        embed = discord.Embed(
            title="❌ Command Error",
            description=f"```py\n{str(error)[:500]}\n```",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Command", value=ctx.command.name if ctx.command else "Unknown", inline=True)
        embed.add_field(name="User", value=str(ctx.author), inline=True)
        embed.add_field(name="Channel", value=ctx.channel.mention if hasattr(ctx.channel, 'mention') else str(ctx.channel), inline=True)
        
        # Add traceback
        tb = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        if len(tb) > 1000:
            tb = tb[-1000:]
        embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)
        
        await self.send_webhook(embed)

async def setup(bot):
    await bot.add_cog(Monitoring(bot))
