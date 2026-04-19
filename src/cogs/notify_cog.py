"""
Notify Cog — per-user notification preference management.

Slash commands under the /notify group let users customise which
alerts they receive, mute notifications temporarily, and toggle DM
delivery.
"""

import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from notification_prefs import notif_prefs
from ui_components import EmbedColors

log = logging.getLogger(__name__)

DURATION_RE = re.compile(r"^(\d+)\s*(m|min|h|hr|hours?|minutes?)$", re.IGNORECASE)
DURATION_MULTIPLIERS = {"m": 60, "min": 60, "minutes": 60, "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600}


# Common service names for autocomplete
COMMON_SERVICES = [
    "sonarr",
    "radarr",
    "sabnzbd",
    "overseerr",
    "plex",
    "tautulli",
    "prowlarr",
    "lidarr",
    "readarr",
    "transmission",
    "qbittorrent",
    "jackett",
    "nzbget",
    "jellyfin",
    "emby",
]


async def _service_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for service names - combines common services with user's blocked services."""
    prefs = notif_prefs.get(interaction.user.id)
    all_services = set(COMMON_SERVICES + prefs.blocked_services)

    return [app_commands.Choice(name=s, value=s) for s in sorted(all_services) if current.lower() in s.lower()][:25]


def _parse_duration(text: str) -> int | None:
    """Return seconds for a human duration string like '30m' or '2h'."""
    m = DURATION_RE.match(text.strip())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    for key, mult in DURATION_MULTIPLIERS.items():
        if unit.startswith(key[0]) and len(unit) <= len(key):
            return value * mult
    return None


class NotifyCog(commands.GroupCog, group_name="notify"):
    """Per-user notification preferences."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- /notify show --------------------------------------------------------

    @app_commands.command(name="show", description="Show your notification preferences")
    async def show(self, interaction: discord.Interaction) -> None:
        prefs = notif_prefs.get(interaction.user.id)
        muted = prefs.muted_until > time.time()
        mute_info = f"🔇 Muted until <t:{int(prefs.muted_until)}:R>" if muted else "🔔 Not muted"
        blocked = ", ".join(prefs.blocked_services) or "None"
        embed = discord.Embed(
            title="🔔 Notification Preferences",
            colour=EmbedColors.INFO,
        )
        embed.add_field(name="Enabled", value="✅" if prefs.enabled else "❌", inline=True)
        embed.add_field(name="DM Alerts", value="✅" if prefs.dm_alerts else "❌", inline=True)
        embed.add_field(name="Severity Filter", value=prefs.severity_filter, inline=True)
        embed.add_field(name="Mute Status", value=mute_info, inline=False)
        embed.add_field(name="Blocked Services", value=blocked, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /notify mute --------------------------------------------------------

    @app_commands.command(name="mute", description="Mute all alerts for a duration (e.g. 1h, 30m)")
    @app_commands.describe(duration="How long to mute, e.g. '30m', '2h'")
    async def mute(self, interaction: discord.Interaction, duration: str) -> None:
        seconds = _parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message("❌ Invalid duration. Use e.g. `30m`, `1h`, `8h`.", ephemeral=True)
            return
        prefs = notif_prefs.get(interaction.user.id)
        prefs.muted_until = time.time() + seconds
        await notif_prefs.update(prefs)
        await interaction.response.send_message(
            f"🔇 Muted for **{duration}** (until <t:{int(prefs.muted_until)}:R>).",
            ephemeral=True,
        )

    # -- /notify unmute ------------------------------------------------------

    @app_commands.command(name="unmute", description="Unmute alerts")
    async def unmute(self, interaction: discord.Interaction) -> None:
        prefs = notif_prefs.get(interaction.user.id)
        prefs.muted_until = 0.0
        await notif_prefs.update(prefs)
        await interaction.response.send_message("🔔 Alerts unmuted.", ephemeral=True)

    # -- /notify filter ------------------------------------------------------

    @app_commands.command(name="filter", description="Set severity filter (all / warning / critical)")
    @app_commands.describe(level="Minimum severity to receive: all, warning, or critical")
    @app_commands.choices(
        level=[
            app_commands.Choice(name="all", value="all"),
            app_commands.Choice(name="warning", value="warning"),
            app_commands.Choice(name="critical", value="critical"),
        ]
    )
    async def filter_cmd(self, interaction: discord.Interaction, level: app_commands.Choice[str]) -> None:
        prefs = notif_prefs.get(interaction.user.id)
        prefs.severity_filter = level.value
        await notif_prefs.update(prefs)
        await interaction.response.send_message(f"✅ Severity filter set to **{level.value}**.", ephemeral=True)

    # -- /notify block -------------------------------------------------------

    @app_commands.command(name="block", description="Block alerts from a service")
    @app_commands.describe(service="Service name to block, e.g. sonarr, sabnzbd")
    @app_commands.autocomplete(service=_service_autocomplete)
    async def block(self, interaction: discord.Interaction, service: str) -> None:
        prefs = notif_prefs.get(interaction.user.id)
        svc = service.strip().lower()
        if svc in [s.lower() for s in prefs.blocked_services]:
            await interaction.response.send_message(f"ℹ️ **{svc}** is already blocked.", ephemeral=True)
            return
        prefs.blocked_services.append(svc)
        await notif_prefs.update(prefs)
        await interaction.response.send_message(f"🚫 Blocked alerts from **{svc}**.", ephemeral=True)

    # -- /notify unblock -----------------------------------------------------

    @app_commands.command(name="unblock", description="Unblock alerts from a service")
    @app_commands.describe(service="Service name to unblock")
    @app_commands.autocomplete(service=_service_autocomplete)
    async def unblock(self, interaction: discord.Interaction, service: str) -> None:
        prefs = notif_prefs.get(interaction.user.id)
        svc = service.strip().lower()
        lower_list = [s.lower() for s in prefs.blocked_services]
        if svc not in lower_list:
            await interaction.response.send_message(f"ℹ️ **{svc}** is not blocked.", ephemeral=True)
            return
        idx = lower_list.index(svc)
        prefs.blocked_services.pop(idx)
        await notif_prefs.update(prefs)
        await interaction.response.send_message(f"✅ Unblocked alerts from **{svc}**.", ephemeral=True)

    # -- /notify dm ----------------------------------------------------------

    @app_commands.command(name="dm", description="Toggle DM alert delivery")
    @app_commands.describe(enabled="on or off")
    @app_commands.choices(
        enabled=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    async def dm_toggle(self, interaction: discord.Interaction, enabled: app_commands.Choice[str]) -> None:
        prefs = notif_prefs.get(interaction.user.id)
        prefs.dm_alerts = enabled.value == "on"
        await notif_prefs.update(prefs)
        state = "enabled" if prefs.dm_alerts else "disabled"
        await interaction.response.send_message(f"✅ DM alerts **{state}**.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotifyCog(bot))
