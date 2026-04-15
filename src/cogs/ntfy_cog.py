"""
Ntfy Cog — push notifications to phone via ntfy.

Commands:
  /ntfy send  — send a push notification
  /ntfy test  — send a test notification to verify setup

Module-level helper:
  push_notification(title, message, priority)  — importable by other cogs
"""

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import require_auth, truncate_for_embed
from discord_error import build_error_embed

log = logging.getLogger("openclaw")


async def push_notification(title: str, message: str, priority: str = "default") -> bool:
    """Send a push notification via ntfy. Returns True on success."""
    try:
        from config import cfg

        if not cfg.ntfy_topic:
            log.warning("push_notification: NTFY_TOPIC not configured")
            return False

        url = f"{cfg.ntfy_url.rstrip('/')}/{cfg.ntfy_topic}"
        headers = {
            "Title": title,
            "Priority": priority,
            "Tags": "bell",
            "Content-Type": "text/plain",
        }
        if cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {cfg.ntfy_token}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=message.encode(),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                return True
    except Exception:
        log.exception("push_notification failed")
        return False


async def _push(title: str, message: str, priority: str = "default", tags: str = "bell") -> bool:
    """Send a push notification to ntfy. Returns True on success."""
    try:
        from config import cfg

        if not cfg.ntfy_topic:
            return False

        url = f"{cfg.ntfy_url.rstrip('/')}/{cfg.ntfy_topic}"
        headers = {
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Content-Type": "text/plain",
        }
        if cfg.ntfy_token:
            headers["Authorization"] = f"Bearer {cfg.ntfy_token}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=message.encode(),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                return True
    except Exception:
        log.exception("_push failed")
        return False


class NtfyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    ntfy = app_commands.Group(name="ntfy", description="Push notifications via ntfy")

    # ── /ntfy send ────────────────────────────────────────────────────────

    @ntfy.command(name="send", description="Send a push notification to your phone")
    @app_commands.describe(
        message="Notification body text",
        title="Notification title (default: OpenClaw)",
        priority="Delivery priority",
    )
    @app_commands.choices(
        priority=[
            app_commands.Choice(name="low", value="low"),
            app_commands.Choice(name="default", value="default"),
            app_commands.Choice(name="high", value="high"),
            app_commands.Choice(name="urgent", value="urgent"),
        ]
    )
    @require_auth()
    async def ntfy_send(
        self,
        interaction: discord.Interaction,
        message: str,
        title: str = "OpenClaw",
        priority: str = "default",
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            from config import cfg

            if not cfg.ntfy_topic:
                await interaction.followup.send(
                    "❌ Ntfy not configured. Set `NTFY_URL` and `NTFY_TOPIC` in `.env`",
                    ephemeral=True,
                )
                return

            ok = await _push(title=title, message=message, priority=priority)
            if ok:
                embed = discord.Embed(
                    title="📱 Push Sent",
                    description=f"**{title}**\n{truncate_for_embed(message, 512)}",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Priority", value=priority, inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(
                    "❌ Failed to send notification. Check ntfy logs.", ephemeral=True
                )
        except Exception as e:
            log.exception("ntfy send failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/ntfy send"), ephemeral=True)

    # ── /ntfy test ────────────────────────────────────────────────────────

    @ntfy.command(name="test", description="Send a test notification to verify ntfy setup")
    @require_auth()
    async def ntfy_test(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            from config import cfg

            if not cfg.ntfy_topic:
                await interaction.followup.send(
                    "❌ Ntfy not configured. Set `NTFY_URL` and `NTFY_TOPIC` in `.env`",
                    ephemeral=True,
                )
                return

            ok = await _push(
                title="Test",
                message="OpenClaw ntfy is working! 🎉",
                priority="default",
                tags="white_check_mark",
            )
            if ok:
                await interaction.followup.send(
                    "✅ Test notification sent! Check your phone.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Test failed. Check ntfy server and logs.", ephemeral=True
                )
        except Exception:
            log.exception("ntfy test failed")
            await interaction.followup.send("❌ Failed to send test notification.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(NtfyCog(bot))
