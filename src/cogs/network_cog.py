"""
Network Cog — extracted from bot.py
Handles: /network, /tailscale, /speedtest
"""

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log
from network import get_network_status, get_tailscale_status, run_speed_test


class NetworkCog(commands.Cog, name="Network"):
    """Network and remote-access status commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="network", description="Show network connectivity status (LAN, internet, Tailscale)")
    async def network_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await get_network_status()
        embed = discord.Embed(
            title="🌐 Network Status",
            description=result,
            color=discord.Color.blue(),
        )
        embed.set_footer(text="LAN • Internet • DNS • Tailscale • OpenClaw health")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "network")

    @app_commands.command(name="tailscale", description="Show Tailscale VPN status and this device's Tailscale IP")
    async def tailscale_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await get_tailscale_status()
        embed = discord.Embed(
            title="🔒 Tailscale Status",
            description=result,
            color=discord.Color.dark_green(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "tailscale")

    @app_commands.command(name="speedtest", description="Run a quick network speed test")
    async def speedtest_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await run_speed_test()
        embed = discord.Embed(
            title="⚡ Speed Test",
            description=result,
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Download test via Cloudflare (10MB sample)")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "speedtest")


async def setup(bot: commands.Bot):
    await bot.add_cog(NetworkCog(bot))
