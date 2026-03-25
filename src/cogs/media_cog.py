"""
Media Cog — extracted from bot.py
Handles: /search, /queue, /recent, /health, /nowplaying, /watch
"""

import re

import discord
from discord import app_commands
from discord.ext import commands

from skills.advanced_skills import (
    check_arr_health,
    check_download_clients,
    check_plex_status,
    get_download_queue,
    get_plex_activity,
    get_recent_additions,
    search_media,
)
from cog_helpers import audit_log


# Maps known NL intent keywords to skills + default intervals
_WATCH_SKILL_MAP = {
    "disk": ("get_nas_storage_health", 60),
    "storage": ("get_nas_storage_health", 60),
    "nas": ("get_nas_alerts", 15),
    "plex": ("check_plex_status", 10),
    "download": ("check_download_clients", 5),
    "queue": ("check_download_clients", 5),
    "cpu": ("get_system_stats", 10),
    "memory": ("get_system_stats", 10),
    "health": ("check_arr_health", 15),
    "sonarr": ("check_arr_health", 15),
    "radarr": ("check_arr_health", 15),
    "network": ("get_network_status", 10),
    "ping": ("ping_host", 5),
    "speed": ("run_speed_test", 60),
    "tailscale": ("get_tailscale_status", 10),
}


class MediaCog(commands.Cog, name="Media"):
    """Media management commands — search, queue, health, playback."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="search", description="Search for TV shows or movies")
    @app_commands.describe(
        query="Search term (e.g. 'Breaking Bad')",
        media_type="'tv', 'movie', or 'all' (default: all)",
    )
    async def search_cmd(self, interaction: discord.Interaction, query: str, media_type: str = "all"):
        await interaction.response.defer()
        result = await search_media(query, media_type)
        embed = discord.Embed(
            title=f"🔍 Search: {query}",
            description=result,
            color=discord.Color.teal(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "search", detail=f"{query} type={media_type}")

    @app_commands.command(name="queue", description="Show active downloads from SABnzbd and qBittorrent")
    async def queue_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await get_download_queue()
        embed = discord.Embed(
            title="📥 Download Queue",
            description=result,
            color=discord.Color.dark_teal(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "queue")

    @app_commands.command(name="recent", description="Show recently added media from Plex")
    @app_commands.describe(count="Number of items to show (1-25, default 10)")
    async def recent_cmd(self, interaction: discord.Interaction, count: int = 10):
        await interaction.response.defer()
        result = await get_recent_additions(count)
        embed = discord.Embed(
            title=f"🆕 Recently Added ({count})",
            description=result,
            color=discord.Color.purple(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "recent", detail=f"count={count}")

    @app_commands.command(name="health", description="Check *arr services and download client health")
    async def health_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        arr_health = await check_arr_health()
        dl_health = await check_download_clients()
        plex_health = await check_plex_status()

        embed = discord.Embed(
            title="🏥 Service Health",
            color=discord.Color.green(),
        )
        embed.add_field(name="*arr Services", value=arr_health, inline=False)
        embed.add_field(name="Download Clients", value=dl_health, inline=False)
        embed.add_field(name="Plex", value=plex_health, inline=False)
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "health")

    @app_commands.command(name="nowplaying", description="Show what's currently playing on Plex (active streams)")
    async def nowplaying_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await get_plex_activity()
        embed = discord.Embed(
            title="🎬 Plex — Now Playing",
            description=result,
            color=discord.Color.from_rgb(229, 160, 13),
        )
        embed.set_footer(text="via Tautulli · real-time activity")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "nowplaying")

    @app_commands.command(name="watch", description="Create a persistent alert that runs on a schedule")
    @app_commands.describe(
        condition="What to watch in plain English (e.g. 'check disk usage every hour')",
        action="'add' to create, 'list' to view, 'remove' to delete",
        watch_id="Task ID to remove (e.g. sched-5) — only needed for 'remove'",
    )
    async def watch_cmd(
        self,
        interaction: discord.Interaction,
        condition: str = "",
        action: str = "list",
        watch_id: str = "",
    ):
        from scheduler import scheduler

        if action == "list":
            tasks = [t for t in scheduler.list_tasks() if t.created_by.startswith("watch:")]
            if not tasks:
                await interaction.response.send_message(
                    "👁️ No active watches. Use `/watch add condition:\"check disk every hour\"`.",
                    ephemeral=True,
                )
                return
            lines = []
            for t in tasks:
                status = "✅" if t.enabled else "⏸️"
                lines.append(
                    f"{status} `{t.task_id}` — **{t.action}** every {t.interval_minutes}m "
                    f"(runs: {t.run_count}, next: {t.next_run_str})"
                )
            embed = discord.Embed(
                title=f"👁️ Active Watches ({len(tasks)})",
                description="\n".join(lines),
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Use /watch remove watch_id:<id> to delete a watch")
            await interaction.response.send_message(embed=embed)
            return

        if action == "remove":
            if not watch_id:
                await interaction.response.send_message(
                    "❌ Provide a watch_id. Example: `/watch remove watch_id:sched-5`",
                    ephemeral=True,
                )
                return
            if scheduler.remove(watch_id):
                await interaction.response.send_message(f"🗑️ Watch `{watch_id}` removed.")
                audit_log(interaction.user, "watch_remove", detail=watch_id)
            else:
                await interaction.response.send_message(f"❌ Watch `{watch_id}` not found.", ephemeral=True)
            return

        # action == "add"
        if not condition:
            await interaction.response.send_message(
                "❌ Describe what to watch. Examples:\n"
                "• `/watch add condition:\"check disk usage every hour\"`\n"
                "• `/watch add condition:\"monitor plex every 5 minutes\"`\n"
                "• `/watch add condition:\"alert if downloads stall every 10 min\"`",
                ephemeral=True,
            )
            return

        lower = condition.lower()
        interval = 30
        m = re.search(r"every\s+(\d+)\s*(min|minute|minutes|hour|hours|h)", lower)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            interval = n * 60 if unit.startswith("h") else n

        matched_skill = None
        for keyword, (skill_name, default_interval) in _WATCH_SKILL_MAP.items():
            if keyword in lower:
                matched_skill = skill_name
                if not m:
                    interval = default_interval
                break

        if not matched_skill:
            matched_skill = "get_system_stats"

        interval = max(1, min(interval, 1440))

        task = scheduler.create(
            action=matched_skill,
            interval_minutes=interval,
            created_by=f"watch:{interaction.user}",
            notify_channel_id=interaction.channel_id or 0,
            alert_only=True,
        )

        embed = discord.Embed(
            title="👁️ Watch Created",
            description=(
                f"**Condition**: {condition}\n"
                f"**Skill**: `{matched_skill}`\n"
                f"**Interval**: every {interval} minute{'s' if interval != 1 else ''}\n"
                f"**ID**: `{task.task_id}`"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Results will post to this channel | Remove with /watch remove watch_id:{task.task_id}")
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "watch_add", detail=f"{task.task_id} {matched_skill} every {interval}m")


async def setup(bot: commands.Bot):
    await bot.add_cog(MediaCog(bot))
