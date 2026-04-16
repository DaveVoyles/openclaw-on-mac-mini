"""Uptime Kuma commands: /uptime-status."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log

from ._helpers import require_auth

log = logging.getLogger("openclaw")


def _register_uptime_kuma_commands(bot: commands.Bot) -> None:
    """Register /uptime-status command."""

    @bot.tree.command(
        name="uptime-status",
        description="Show current status of all monitored services from Uptime Kuma",
    )
    @app_commands.describe(
        service="Optional: filter to a specific service name (partial match)",
    )
    @require_auth
    async def uptime_status_cmd(
        interaction: discord.Interaction, service: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            if service:
                from uptime_kuma_skills import get_monitor_detail
                result = await get_monitor_detail(service)
            else:
                from uptime_kuma_skills import get_monitors_down
                down_result = await get_monitors_down()

                from uptime_kuma_skills import get_uptime_summary
                summary = await get_uptime_summary()

                result = f"{down_result}\n\n{summary}"
        except Exception as exc:  # broad: intentional
            await interaction.followup.send(
                f"❌ Error querying Uptime Kuma: {exc}", ephemeral=True
            )
            return

        # Split into embed if short enough, plain text otherwise
        if len(result) <= 4096:
            is_all_up = "✅" in result.split("\n")[0]
            color = 0x2ECC71 if is_all_up else 0xE74C3C
            embed = discord.Embed(
                title="📡 Uptime Kuma Status",
                description=result,
                color=color,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(result[:1900], ephemeral=True)

        audit_log(
            interaction.user,
            "uptime_status",
            detail=f"service={service or 'all'}",
        )
