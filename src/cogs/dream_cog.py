"""Dream cycle & memory health commands — Auto-Dream integration.

Handles: /dream, /memory-health, /memory-export
"""

import io
import json
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, truncate_for_embed

log = logging.getLogger("openclaw")

ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))


class DreamCog(commands.Cog, name="Dream"):
    """Auto-Dream memory consolidation and health commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error)
        else:
            msg = f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ── /dream ────────────────────────────────────────────────────────
    @app_commands.command(name="dream", description="Run a memory dream cycle — consolidate, score, and organize knowledge")
    @require_auth()
    async def dream_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()

        from dream_cycle import dream_now

        report = await dream_now()

        if len(report) > 4000:
            # Send as file if too long for embed
            buf = io.BytesIO(report.encode("utf-8"))
            buf.seek(0)
            file = discord.File(buf, filename="dream-report.md")
            await interaction.followup.send("🌙 **Dream cycle complete** — report attached:", file=file)
        else:
            embed = discord.Embed(
                title="🌙 Dream Cycle Report",
                description=truncate_for_embed(report),
                color=discord.Color.purple(),
            )
            await interaction.followup.send(embed=embed)

        # Also post to alert channel
        if ALERT_CHANNEL_ID:
            channel = self.bot.get_channel(ALERT_CHANNEL_ID)
            if channel:
                summary = report[:1900] + ("…" if len(report) > 1900 else "")
                await channel.send(f"🌙 **Dream cycle triggered by {interaction.user.display_name}**\n{summary}")

        audit_log(interaction.user, "dream", detail="manual trigger")

    # ── /memory-health ────────────────────────────────────────────────
    @app_commands.command(name="memory-health", description="Show memory health score and metrics")
    @require_auth()
    async def memory_health_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        from dream_cycle import get_memory_health

        report = await get_memory_health()

        embed = discord.Embed(
            title="📊 Memory Health",
            description=truncate_for_embed(report),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        audit_log(interaction.user, "memory_health")

    # ── /memory-export ────────────────────────────────────────────────
    @app_commands.command(name="memory-export", description="Export memory as JSON bundle")
    @require_auth()
    async def memory_export_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        from pathlib import Path

        data_dir = Path("data/dream")
        bundle: dict = {"exported_at": None, "index": None, "memory_md": None}

        import datetime
        bundle["exported_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

        index_path = data_dir / "index.json"
        if index_path.exists():
            bundle["index"] = json.loads(index_path.read_text(encoding="utf-8"))

        memory_path = data_dir / "MEMORY.md"
        if memory_path.exists():
            bundle["memory_md"] = memory_path.read_text(encoding="utf-8")

        if bundle["index"] is None and bundle["memory_md"] is None:
            await interaction.followup.send(
                "⚠️ No dream data found. Run `/dream` first to generate memory index.",
                ephemeral=True,
            )
            return

        payload = json.dumps(bundle, indent=2, ensure_ascii=False)
        buf = io.BytesIO(payload.encode("utf-8"))
        buf.seek(0)
        file = discord.File(buf, filename="memory-export.json")
        await interaction.followup.send("📦 Memory export:", file=file, ephemeral=True)
        audit_log(interaction.user, "memory_export")


async def setup(bot: commands.Bot):
    """Called automatically by bot.load_extension()."""
    await bot.add_cog(DreamCog(bot))
