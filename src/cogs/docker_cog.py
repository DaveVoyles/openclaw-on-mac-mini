"""
Docker / Infrastructure Cog — extracted from bot.py
Handles: /containers, /status, /logs, /system, /dockerstats, /restart
"""

import discord
from discord import app_commands
from discord.ext import commands

from approvals import (
    ApprovalView,
    RiskLevel,
    approval_store,
    build_approval_embed,
    is_emergency_stopped,
)
from cog_helpers import audit_log, is_service_allowed
from skills import (
    get_container_logs,
    get_container_status,
    get_docker_stats,
    get_system_stats,
    get_uptime,
    list_containers,
    restart_container,
)


class DockerCog(commands.Cog, name="Docker"):
    """Docker and infrastructure management commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="containers", description="List all running Docker containers")
    async def containers_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await list_containers()
        embed = discord.Embed(
            title="🐳 Running Containers",
            description=f"```\n{result}\n```",
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "containers")

    @app_commands.command(name="status", description="Get detailed status for a container")
    @app_commands.describe(service="Container name (e.g. sonarr, radarr, plex)")
    async def status_cmd(self, interaction: discord.Interaction, service: str):
        await interaction.response.defer()
        result = await get_container_status(service)
        embed = discord.Embed(
            title=f"📦 Status: {service}",
            description=f"```\n{result}\n```",
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "status", detail=service)

    @app_commands.command(name="logs", description="View recent logs from a container")
    @app_commands.describe(service="Container name", lines="Number of lines (5-100, default 30)")
    async def logs_cmd(self, interaction: discord.Interaction, service: str, lines: int = 30):
        await interaction.response.defer()
        result = await get_container_logs(service, lines)
        embed = discord.Embed(
            title=f"📜 Logs: {service} (last {min(max(lines, 5), 100)})",
            description=f"```\n{result}\n```",
            color=discord.Color.greyple(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "logs", detail=f"{service} lines={lines}")

    @app_commands.command(name="system", description="Show system resource usage")
    async def system_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        stats = await get_system_stats()
        uptime_str = await get_uptime()
        embed = discord.Embed(
            title="🖥️ System Stats",
            description=stats,
            color=discord.Color.green(),
        )
        embed.add_field(name="Uptime", value=f"```{uptime_str}```", inline=False)
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "system")

    @app_commands.command(name="dockerstats", description="Show resource usage per container")
    async def dockerstats_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await get_docker_stats()
        embed = discord.Embed(
            title="📊 Docker Resource Usage",
            description=f"```\n{result}\n```",
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "dockerstats")

    @app_commands.command(name="restart", description="Restart a Docker container (requires approval)")
    @app_commands.describe(service="Container name to restart")
    async def restart_cmd(self, interaction: discord.Interaction, service: str):
        if is_emergency_stopped():
            await interaction.response.send_message(
                "🛑 **Emergency stop is active.** All actions are halted. Use `/estop resume` to resume.",
                ephemeral=True,
            )
            audit_log(interaction.user, "restart", detail=service, result="blocked_estop")
            return

        if not is_service_allowed("restart_container", service):
            await interaction.response.send_message(
                f"🚫 Restarting `{service}` is not permitted by policy.", ephemeral=True,
            )
            audit_log(interaction.user, "restart", detail=service, result="blocked_by_policy")
            return

        req = approval_store.create(
            action="restart_container",
            target=service,
            risk_level=RiskLevel.HIGH,
            requester_id=interaction.user.id,
            requester_name=str(interaction.user),
            channel_id=interaction.channel_id,
        )

        async def execute_restart(approved_req):
            result = await restart_container(approved_req.target)
            color = discord.Color.green() if result.startswith("✅") else discord.Color.red()
            embed = discord.Embed(
                title=f"🔄 Restart: {approved_req.target}",
                description=result,
                color=color,
            )
            audit_log(
                None, "restart_executed",
                detail=f"{approved_req.target} approved_by={approved_req.resolver_name}",
                result="success" if result.startswith("✅") else "failed",
            )
            return embed

        view = ApprovalView(req.request_id, execute_restart)
        embed = build_approval_embed(req)

        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        audit_log(interaction.user, "restart_requested", detail=service)


async def setup(bot: commands.Bot):
    await bot.add_cog(DockerCog(bot))
