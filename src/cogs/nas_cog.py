"""
NAS Cog — Synology NAS management via Discord slash commands.
Exposes /nas status, /nas health, /nas alerts, /nas browse as a command group.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, truncate_for_embed
from nas import (
    get_disk_smart_status,
    get_nas_alerts,
    get_nas_storage_health,
    nas_list_folder,
)

log = logging.getLogger("openclaw.nas_cog")


def _severity_color(text: str) -> discord.Color:
    """Pick embed color based on status indicators in the response text."""
    if "❌" in text:
        return discord.Color.red()
    if "⚠️" in text:
        return discord.Color.gold()
    return discord.Color.green()


def _severity_label(text: str) -> str:
    if "❌" in text:
        return "🔴 Critical"
    if "⚠️" in text:
        return "🟡 Warning"
    return "🟢 OK"


class NasCog(commands.Cog, name="NAS"):
    """Synology NAS management commands."""

    nas = app_commands.Group(name="nas", description="Synology NAS management")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        msg = f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # -- /nas status ----------------------------------------------------------

    @nas.command(name="status", description="NAS storage health (disk usage, RAID, temperature)")
    async def status_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            result = await get_nas_storage_health()
        except Exception as exc:
            log.exception("NAS status failed")
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🔴 NAS Unreachable",
                    description=f"Could not connect to the NAS.\n```{exc}```",
                    color=discord.Color.red(),
                )
            )
            return

        embed = discord.Embed(
            title="📊 NAS Storage Health",
            description=truncate_for_embed(result),
            color=_severity_color(result),
        )
        embed.set_footer(text=_severity_label(result))
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "nas_status")

    # -- /nas health ----------------------------------------------------------

    @nas.command(name="health", description="Per-drive SMART / I/O status")
    async def health_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            result = await get_disk_smart_status()
        except Exception as exc:
            log.exception("NAS health failed")
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🔴 NAS Unreachable",
                    description=f"Could not connect to the NAS.\n```{exc}```",
                    color=discord.Color.red(),
                )
            )
            return

        embed = discord.Embed(
            title="💽 Drive Health",
            description=truncate_for_embed(result),
            color=_severity_color(result),
        )
        embed.set_footer(text=_severity_label(result))
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "nas_health")

    # -- /nas alerts ----------------------------------------------------------

    @nas.command(name="alerts", description="System alerts and warnings")
    async def alerts_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            result = await get_nas_alerts()
        except Exception as exc:
            log.exception("NAS alerts failed")
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🔴 NAS Unreachable",
                    description=f"Could not connect to the NAS.\n```{exc}```",
                    color=discord.Color.red(),
                )
            )
            return

        embed = discord.Embed(
            title="🔔 NAS Alerts",
            description=truncate_for_embed(result),
            color=_severity_color(result),
        )
        embed.set_footer(text=_severity_label(result))
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "nas_alerts")

    # -- /nas browse ----------------------------------------------------------

    @nas.command(name="browse", description="List contents of a NAS folder")
    @app_commands.describe(path="Folder path on the NAS (default: /volume1)")
    async def browse_cmd(self, interaction: discord.Interaction, path: str = "/volume1"):
        await interaction.response.defer()
        try:
            result = await nas_list_folder(path)
        except Exception as exc:
            log.exception("NAS browse failed")
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🔴 NAS Unreachable",
                    description=f"Could not connect to the NAS.\n```{exc}```",
                    color=discord.Color.red(),
                )
            )
            return

        embed = discord.Embed(
            title=f"📂 Browse: {path}",
            description=truncate_for_embed(result),
            color=_severity_color(result),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "nas_browse", detail=path)


async def setup(bot: commands.Bot):
    await bot.add_cog(NasCog(bot))
