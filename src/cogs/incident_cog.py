"""Incident room automation commands."""

from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from incident_workflows import incident_store

log = logging.getLogger("openclaw.incident_cog")

SEVERITY_CHOICES = [
    app_commands.Choice(name="low", value="low"),
    app_commands.Choice(name="medium", value="medium"),
    app_commands.Choice(name="high", value="high"),
    app_commands.Choice(name="critical", value="critical"),
]

STATUS_CHOICES = [
    app_commands.Choice(name="open", value="open"),
    app_commands.Choice(name="investigating", value="investigating"),
    app_commands.Choice(name="monitoring", value="monitoring"),
]

incident_group = app_commands.Group(name="incident", description="Incident room workflow commands")


class IncidentCog(commands.Cog, name="Incident"):
    """Create, update, and resolve incidents with persisted postmortem notes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @incident_group.command(name="create", description="Create a new incident room entry")
    @app_commands.describe(
        title="Short incident title",
        severity="Incident severity",
        details="Initial incident details",
    )
    @app_commands.choices(severity=SEVERITY_CHOICES)
    async def incident_create(
        self,
        interaction: discord.Interaction,
        title: str,
        severity: app_commands.Choice[str],
        details: str = "",
    ) -> None:
        channel = interaction.channel
        channel_id = getattr(channel, "id", None)
        channel_name = getattr(channel, "name", "") if channel else ""
        thread_id = channel_id if isinstance(channel, discord.Thread) else None
        thread_name = channel_name if isinstance(channel, discord.Thread) else None
        if isinstance(channel, discord.Thread):
            channel_id = channel.parent_id
            channel_name = getattr(channel.parent, "name", "") if channel.parent else channel_name

        incident = incident_store.create_incident(
            title=title,
            severity=severity.value,
            description=details,
            channel_id=channel_id,
            channel_name=channel_name,
            thread_id=thread_id,
            thread_name=thread_name,
            created_by=getattr(interaction.user, "id", None),
            created_by_name=str(interaction.user),
        )

        created_thread = await self._try_create_thread(interaction, incident)
        if created_thread is not None:
            incident = incident_store.set_context(
                incident["id"],
                channel_id=channel_id,
                channel_name=channel_name,
                thread_id=created_thread.id,
                thread_name=created_thread.name,
            ) or incident

        embed = discord.Embed(
            title=f"🚨 Incident #{incident['id']} Created",
            description=f"**{incident['title']}**",
            color=discord.Color.red(),
        )
        embed.add_field(name="Severity", value=incident["severity"].upper(), inline=True)
        embed.add_field(name="Status", value=incident["status"].upper(), inline=True)
        if details.strip():
            embed.add_field(name="Details", value=details[:1000], inline=False)
        if incident.get("thread_id"):
            embed.add_field(name="Incident Room", value=f"<#{incident['thread_id']}>", inline=False)

        audit_log(interaction.user, "incident_create", f"incident#{incident['id']} {incident['severity']} {incident['title']}")
        await interaction.response.send_message(embed=embed)

    @incident_group.command(name="status", description="View or update incident status")
    @app_commands.describe(
        incident_id="Incident ID from /incident create",
        state="Optional new state (omit to view current status)",
        note="Status note update",
    )
    @app_commands.choices(state=STATUS_CHOICES)
    async def incident_status(
        self,
        interaction: discord.Interaction,
        incident_id: int,
        state: app_commands.Choice[str] | None = None,
        note: str = "",
    ) -> None:
        if state is None:
            incident = incident_store.get_incident(incident_id)
            if incident is None:
                await interaction.response.send_message(f"❌ Incident #{incident_id} not found.", ephemeral=True)
                return
            await interaction.response.send_message(embed=self._status_embed(incident))
            return

        try:
            incident = incident_store.transition_status(
                incident_id,
                new_status=state.value,
                note=note,
                actor_id=getattr(interaction.user, "id", None),
                actor_name=str(interaction.user),
            )
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        if incident is None:
            await interaction.response.send_message(f"❌ Incident #{incident_id} not found.", ephemeral=True)
            return

        audit_log(interaction.user, "incident_status", f"incident#{incident_id} -> {state.value} {note[:120]}")
        await interaction.response.send_message(embed=self._status_embed(incident, note=note))

    @incident_group.command(name="resolve", description="Resolve an incident and capture postmortem notes")
    @app_commands.describe(
        incident_id="Incident ID from /incident create",
        summary="Incident resolution summary",
        action_items="Action items (one per line or semicolon separated)",
        notes="Additional postmortem notes",
    )
    async def incident_resolve(
        self,
        interaction: discord.Interaction,
        incident_id: int,
        summary: str,
        action_items: str = "",
        notes: str = "",
    ) -> None:
        incident = incident_store.resolve_incident(
            incident_id,
            summary=summary,
            action_items=action_items,
            postmortem_notes=notes,
            actor_id=getattr(interaction.user, "id", None),
            actor_name=str(interaction.user),
        )
        if incident is None:
            await interaction.response.send_message(
                f"❌ Incident #{incident_id} not found or already resolved.",
                ephemeral=True,
            )
            return

        await self._try_archive_thread(incident.get("thread_id"))
        embed = discord.Embed(
            title=f"✅ Incident #{incident['id']} Resolved",
            description=summary[:1500],
            color=discord.Color.green(),
        )
        action_item_list = incident.get("action_items", [])
        if action_item_list:
            rendered = "\n".join(f"• {item}" for item in action_item_list[:8])
            embed.add_field(name="Postmortem Action Items", value=rendered[:1000], inline=False)
        if notes.strip():
            embed.add_field(name="Notes", value=notes[:1000], inline=False)

        audit_log(interaction.user, "incident_resolve", f"incident#{incident_id} resolved")
        await interaction.response.send_message(embed=embed)

    async def _try_create_thread(
        self,
        interaction: discord.Interaction,
        incident: dict,
    ) -> discord.Thread | None:
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            return channel
        if not isinstance(channel, discord.TextChannel):
            return None
        safe = re.sub(r"[^a-zA-Z0-9-]+", "-", incident["title"]).strip("-").lower()
        thread_name = f"incident-{incident['id']}-{safe}"[:80] if safe else f"incident-{incident['id']}"
        try:
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                reason=f"Incident #{incident['id']} created",
            )
            await thread.send(f"🚨 Incident #{incident['id']} room created for **{incident['title']}**")
            return thread
        except Exception as exc:
            log.debug("Incident thread create failed: %s", exc)
            return None

    async def _try_archive_thread(self, thread_id: int | None) -> None:
        if not thread_id:
            return
        channel = self.bot.get_channel(thread_id)
        if not isinstance(channel, discord.Thread):
            return
        try:
            await channel.edit(archived=True, reason="Incident resolved")
        except Exception as exc:
            log.debug("Failed to archive incident thread %s: %s", thread_id, exc)

    @staticmethod
    def _status_embed(incident: dict, note: str = "") -> discord.Embed:
        palette = {
            "open": discord.Color.red(),
            "investigating": discord.Color.orange(),
            "monitoring": discord.Color.gold(),
            "resolved": discord.Color.green(),
        }
        embed = discord.Embed(
            title=f"🚨 Incident #{incident['id']}",
            description=f"**{incident['title']}**",
            color=palette.get(incident.get("status", ""), discord.Color.blurple()),
        )
        embed.add_field(name="Severity", value=str(incident.get("severity", "")).upper(), inline=True)
        embed.add_field(name="Status", value=str(incident.get("status", "")).upper(), inline=True)
        if incident.get("thread_id"):
            embed.add_field(name="Incident Room", value=f"<#{incident['thread_id']}>", inline=True)
        if note.strip():
            embed.add_field(name="Note", value=note[:1000], inline=False)
        return embed


IncidentCog.__cog_app_commands__.append(incident_group)


async def setup(bot: commands.Bot):
    await bot.add_cog(IncidentCog(bot))

