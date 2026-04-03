"""System commands: /ports, /report, /analyze."""

import discord
from discord import app_commands

from analyzer import analyze_logs
from audit import audit_log
from constants import DEFAULT_ANALYZE_LINES
from skills.advanced_skills import check_service_ports, create_status_report

from ._helpers import require_auth, truncate_for_embed


def _register_system_commands(bot):
    """Register /ports, /report, and /analyze."""

    # ------------------------------------------------------------------
    # /ports
    # ------------------------------------------------------------------

    @bot.tree.command(name="ports", description="Check service port connectivity")
    @require_auth
    async def ports_cmd(interaction: discord.Interaction):
        await interaction.response.defer()
        result = await check_service_ports()
        embed = discord.Embed(
            title="🔌 Port Status",
            description=result,
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "ports")

    # ------------------------------------------------------------------
    # /report
    # ------------------------------------------------------------------

    @bot.tree.command(name="report", description="Generate a comprehensive system status report")
    @require_auth
    async def report_cmd(interaction: discord.Interaction):
        await interaction.response.defer()
        result = await create_status_report()
        embed = discord.Embed(
            title="📊 System Report",
            description=result,
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "report")

    # ------------------------------------------------------------------
    # /analyze
    # ------------------------------------------------------------------

    @bot.tree.command(name="analyze", description="AI-powered container log analysis")
    @app_commands.describe(service="Container name to analyze", lines="Log lines to analyze (10-200, default 50)")
    @require_auth
    async def analyze_cmd(interaction: discord.Interaction, service: str, lines: int = DEFAULT_ANALYZE_LINES):
        await interaction.response.defer()
        result = await analyze_logs(service, lines)
        result = truncate_for_embed(result)
        embed = discord.Embed(
            title=f"🔬 Log Analysis: {service}",
            description=result,
            color=discord.Color.dark_orange(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "analyze", detail=f"{service} lines={lines}")
