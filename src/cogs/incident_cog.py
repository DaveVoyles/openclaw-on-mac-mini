"""Incident room automation commands."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from approvals import ApprovalView, RiskLevel, approval_store, build_approval_embed
from audit import audit_log
from discord_error import build_error_embed
from discord_progress import ProgressTracker
from incident_copilot import execute_incident_action, generate_incident_report
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

LIST_STATE_CHOICES = [
    app_commands.Choice(name="active", value="active"),
    app_commands.Choice(name="all", value="all"),
    app_commands.Choice(name="open", value="open"),
    app_commands.Choice(name="investigating", value="investigating"),
    app_commands.Choice(name="monitoring", value="monitoring"),
    app_commands.Choice(name="resolved", value="resolved"),
]

incident_group = app_commands.Group(name="incident", description="Incident room workflow commands")


class _IncidentActionButton(discord.ui.Button):
    def __init__(self, label: str, index: int):
        super().__init__(label=label[:80], style=discord.ButtonStyle.secondary, custom_id=f"incident_action_{index}")
        self.action_index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, IncidentActionView):
            await interaction.response.send_message("❌ Incident action view is unavailable.", ephemeral=True)
            return
        await view.request_execution(interaction, self.action_index)


class IncidentActionView(discord.ui.View):
    def __init__(
        self,
        *,
        incident_id: int,
        actions: list[dict[str, Any]],
        timeout: float = 900,
    ):
        super().__init__(timeout=timeout)
        self.incident_id = incident_id
        self.actions = actions[:3]
        for idx, action in enumerate(self.actions):
            if action.get("executable"):
                self.add_item(_IncidentActionButton(f"Run: {action.get('title', 'Action')}", idx))

    async def request_execution(self, interaction: discord.Interaction, action_index: int) -> None:
        if action_index < 0 or action_index >= len(self.actions):
            await interaction.response.send_message("❌ Invalid incident action selection.", ephemeral=True)
            return

        action = self.actions[action_index]
        if not action.get("executable"):
            await interaction.response.send_message("ℹ️ This action is recommendation-only.", ephemeral=True)
            return

        command = str(action.get("command", ""))
        target = str(action.get("target", ""))
        risk = str(action.get("risk_level", "high")).lower()
        risk_level = {
            "critical": RiskLevel.CRITICAL,
            "high": RiskLevel.HIGH,
            "medium": RiskLevel.MEDIUM,
            "low": RiskLevel.MEDIUM,
        }.get(risk, RiskLevel.HIGH)

        req = approval_store.create(
            action=command or "incident_action",
            target=target or f"incident#{self.incident_id}",
            risk_level=risk_level,
            requester_id=interaction.user.id,
            requester_name=str(interaction.user),
            channel_id=interaction.channel_id,
            detail=f"incident#{self.incident_id} {action.get('title', '')}"[:220],
        )

        incident_store.append_event(
            self.incident_id,
            event_type="copilot_action_requested",
            note=f"{command}:{target} requested by {interaction.user}",
            actor_id=getattr(interaction.user, "id", None),
            actor_name=str(interaction.user),
        )

        async def _execute_approved(_req):
            result = await execute_incident_action(action)
            incident_store.append_event(
                self.incident_id,
                event_type="copilot_action_executed",
                note=f"{command}:{target} => {result[:300]}",
                actor_id=getattr(interaction.user, "id", None),
                actor_name=str(interaction.user),
            )
            return result

        view = ApprovalView(req.request_id, _execute_approved)
        embed = build_approval_embed(req)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        audit_log(
            interaction.user,
            "incident_action_approval_requested",
            f"incident#{self.incident_id} {command}:{target}",
        )


class IncidentCog(commands.Cog, name="Incident"):
    """Create, update, and resolve incidents with persisted postmortem notes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _incident_operator_next_steps() -> str:
        return (
            "Next steps: `/incident status` • `/incident timeline` • `/incident resolve`"
        )

    async def cog_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        root_error = getattr(error, "original", error)
        if isinstance(error, app_commands.CheckFailure):
            embed = build_error_embed(error, context="incident command", category="auth")
        elif isinstance(root_error, asyncio.TimeoutError):
            embed = build_error_embed(root_error, context="incident command", category="timeout")
        else:
            embed = build_error_embed(root_error, context="incident command")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @incident_group.command(name="start", description="Start an incident with Copilot summary and approval-gated actions")
    @app_commands.describe(
        title="Short incident title",
        severity="Incident severity",
        details="Initial incident details",
        services="Optional comma-separated services to prioritize (e.g. sonarr,radarr)",
    )
    @app_commands.choices(severity=SEVERITY_CHOICES)
    async def incident_start(
        self,
        interaction: discord.Interaction,
        title: str,
        severity: app_commands.Choice[str],
        details: str = "",
        services: str = "",
    ) -> None:
        progress = ProgressTracker(interaction, title="🚨 Starting Incident")
        await progress.start()
        await progress.update("📊 Gathering data…")
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

        target_channel = created_thread or channel
        if target_channel and hasattr(target_channel, "send"):
            await target_channel.send(
                f"🚨 Incident Copilot started for **Incident #{incident['id']}** — collecting telemetry and drafting actions..."
            )

        report_error: str | None = None
        await progress.update("🤖 Generating report…")
        try:
            report = await asyncio.wait_for(
                generate_incident_report(incident, requested_services=services),
                timeout=45,
            )
            summary = str(report.get("summary", "")).strip()
            causes = [str(item) for item in report.get("suspected_causes", []) if str(item).strip()]
            actions = [item for item in report.get("actions", []) if isinstance(item, dict)]
            model_used = str(report.get("model_used", "unknown"))[:80]
        except asyncio.TimeoutError:
            summary = "Incident Copilot timed out while gathering telemetry."
            causes = []
            actions = []
            model_used = "timeout"
            report_error = "timeout"
            log.warning("Incident Copilot timed out for incident #%s", incident["id"])
        except Exception as exc:  # broad: intentional
            summary = "Incident Copilot was unavailable. Manual incident workflow is still active."
            causes = []
            actions = []
            model_used = "error"
            report_error = str(exc)[:200]
            log.warning("Incident Copilot failed for incident #%s: %s", incident["id"], exc)

        incident_store.append_event(
            incident["id"],
            event_type="copilot_summary",
            note=json_dumps_compact({"summary": summary, "causes": causes, "model": model_used}),
            actor_id=getattr(interaction.user, "id", None),
            actor_name=str(interaction.user),
        )
        incident_store.append_event(
            incident["id"],
            event_type="copilot_actions",
            note=json_dumps_compact({"actions": actions}),
            actor_id=getattr(interaction.user, "id", None),
            actor_name=str(interaction.user),
        )
        if report_error:
            incident_store.append_event(
                incident["id"],
                event_type="copilot_summary_error",
                note=report_error,
                actor_id=getattr(interaction.user, "id", None),
                actor_name=str(interaction.user),
            )

        embed = discord.Embed(
            title=f"🚨 Incident #{incident['id']} Copilot Report",
            description=summary[:1500] if summary else "No summary generated.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Severity", value=incident["severity"].upper(), inline=True)
        embed.add_field(name="Status", value=incident["status"].upper(), inline=True)
        embed.add_field(name="Model", value=model_used, inline=True)
        if incident.get("thread_id"):
            embed.add_field(name="Incident Room", value=f"<#{incident['thread_id']}>", inline=False)
        if causes:
            embed.add_field(name="Suspected Causes", value="\n".join(f"• {cause}" for cause in causes[:4])[:1000], inline=False)
        if actions:
            action_lines = []
            for action in actions[:5]:
                marker = "🟠 executable (approval)" if action.get("executable") else "🔹 recommendation"
                action_lines.append(f"• **{action.get('title', 'Action')}** — {marker}")
            embed.add_field(name="Suggested Next Actions", value="\n".join(action_lines)[:1000], inline=False)
        if report_error:
            embed.add_field(
                name="Copilot Status",
                value=(
                    "⚠️ Report unavailable — continue manual ops in this room.\n"
                    f"{self._incident_operator_next_steps()}"
                )[:1000],
                inline=False,
            )

        action_view = IncidentActionView(incident_id=incident["id"], actions=actions) if any(
            action.get("executable") for action in actions
        ) else None
        if target_channel and hasattr(target_channel, "send"):
            await target_channel.send(embed=embed, view=action_view)
        else:
            await interaction.followup.send(embed=embed, view=action_view)

        launch_embed = discord.Embed(
            title=f"✅ Incident #{incident['id']} Started",
            description=f"**{incident['title']}**",
            color=discord.Color.red(),
        )
        if created_thread is None:
            launch_embed.add_field(
                name="Incident Room",
                value=(
                    "⚠️ Could not auto-create a private thread. "
                    "Run commands in this channel or create a thread manually."
                )[:1000],
                inline=False,
            )
        if incident.get("thread_id"):
            launch_embed.add_field(name="Incident Room", value=f"<#{incident['thread_id']}>", inline=False)
        launch_embed.add_field(
            name="Operator Commands",
            value=self._incident_operator_next_steps()[:1000],
            inline=False,
        )
        await progress.done(f"Incident #{incident['id']} started")
        await interaction.followup.send(embed=launch_embed)
        audit_log(interaction.user, "incident_start", f"incident#{incident['id']} {incident['severity']} {incident['title']}")

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
            await interaction.response.send_message(embed=build_error_embed(exc, context="/incident status"), ephemeral=True)
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

    @incident_group.command(name="list", description="List recent incidents")
    @app_commands.describe(
        state="Filter incidents by state",
        limit="Number of incidents to return (1-20)",
    )
    @app_commands.choices(state=LIST_STATE_CHOICES)
    async def incident_list(
        self,
        interaction: discord.Interaction,
        state: app_commands.Choice[str] | None = None,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        selected_state = state.value if state else "active"
        try:
            if selected_state == "active":
                incidents = incident_store.list_recent(limit=limit, include_resolved=False)
            elif selected_state == "all":
                incidents = incident_store.list_recent(limit=limit, include_resolved=True)
            else:
                incidents = incident_store.list_recent(limit=limit, status=selected_state)
        except ValueError as exc:
            await interaction.response.send_message(embed=build_error_embed(exc, context="/incident list"), ephemeral=True)
            return

        if not incidents:
            await interaction.response.send_message("ℹ️ No incidents found for that filter.", ephemeral=True)
            return

        await interaction.response.send_message(embed=self._list_embed(incidents, selected_state))

    @incident_group.command(name="timeline", description="Show incident timeline events")
    @app_commands.describe(
        incident_id="Incident ID (optional in incident thread)",
        limit="Number of events to return (1-20)",
    )
    async def incident_timeline(
        self,
        interaction: discord.Interaction,
        incident_id: int | None = None,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        incident = incident_store.get_incident(incident_id) if incident_id is not None else self._resolve_incident_from_context(interaction)
        if incident is None:
            await interaction.response.send_message(
                "❌ Incident not found. Provide `incident_id` or run this inside an incident thread.",
                ephemeral=True,
            )
            return

        timeline = incident_store.get_timeline(incident["id"], limit=limit)
        if not timeline:
            await interaction.response.send_message(f"ℹ️ Incident #{incident['id']} has no timeline entries yet.", ephemeral=True)
            return

        await interaction.response.send_message(embed=self._timeline_embed(incident, timeline))

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
        except Exception as exc:  # broad: intentional
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
        except discord.HTTPException as exc:
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

    @staticmethod
    def _list_embed(incidents: list[dict[str, Any]], selected_state: str) -> discord.Embed:
        palette = {
            "open": "🟥",
            "investigating": "🟧",
            "monitoring": "🟨",
            "resolved": "🟩",
        }
        lines = []
        for incident in incidents:
            status = str(incident.get("status", ""))
            updated_at = int(float(incident.get("updated_at") or 0))
            sev = str(incident.get("severity", "")).upper() or "?"
            room = f" • <#{incident['thread_id']}>" if incident.get("thread_id") else ""
            lines.append(
                f"{palette.get(status, '⬜')} **#{incident['id']}** `{sev}` {incident['title'][:80]} "
                f"— **{status.upper()}** • <t:{updated_at}:R>{room}"
            )
        embed = discord.Embed(
            title=f"🚨 Incident List — {selected_state.upper()}",
            description="\n".join(lines)[:4000],
            color=discord.Color.orange(),
        )
        return embed

    @staticmethod
    def _render_timeline_note(event: dict[str, Any]) -> str:
        note = str(event.get("note", "")).strip()
        event_type = str(event.get("event_type", ""))
        if not note:
            return "No details"
        if event_type == "copilot_summary":
            try:
                payload = json.loads(note)
                summary = str(payload.get("summary", "")).strip()
                return summary[:200] if summary else "Copilot summary generated"
            except json.JSONDecodeError:
                return note[:200]
        if event_type == "copilot_actions":
            try:
                payload = json.loads(note)
                actions = payload.get("actions", [])
                if isinstance(actions, list):
                    return f"Suggested actions: {min(len(actions), 20)}"
            except json.JSONDecodeError:
                return note[:200]
        if event_type == "postmortem":
            return "Postmortem details captured"
        return note[:200]

    @classmethod
    def _timeline_embed(cls, incident: dict[str, Any], timeline: list[dict[str, Any]]) -> discord.Embed:
        labels = {
            "created": "Created",
            "status_update": "Status Updated",
            "resolved": "Resolved",
            "postmortem": "Postmortem",
            "copilot_summary": "Copilot Summary",
            "copilot_actions": "Copilot Actions",
            "copilot_action_requested": "Action Requested",
            "copilot_action_executed": "Action Executed",
        }
        lines = []
        for event in timeline:
            event_type = str(event.get("event_type", ""))
            label = labels.get(event_type, event_type.replace("_", " ").title())
            actor = str(event.get("actor_name", "")).strip() or "system"
            timestamp = int(float(event.get("created_at") or 0))
            rendered_note = cls._render_timeline_note(event)
            lines.append(f"• <t:{timestamp}:f> — **{label}** by `{actor}`\n  └ {rendered_note}")

        embed = discord.Embed(
            title=f"🧵 Incident #{incident['id']} Timeline",
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Title", value=incident["title"][:200], inline=False)
        embed.add_field(name="Severity", value=str(incident.get("severity", "")).upper(), inline=True)
        embed.add_field(name="Status", value=str(incident.get("status", "")).upper(), inline=True)
        if incident.get("thread_id"):
            embed.add_field(name="Incident Room", value=f"<#{incident['thread_id']}>", inline=True)
        return embed

    @staticmethod
    def _resolve_incident_from_context(interaction: discord.Interaction) -> dict[str, Any] | None:
        channel = interaction.channel
        thread_id = getattr(channel, "id", None) if getattr(channel, "parent_id", None) is not None else None
        channel_id = getattr(interaction, "channel_id", None)
        if thread_id is not None:
            incident = incident_store.get_incident_for_thread(thread_id, include_resolved=True)
            if incident is not None:
                return incident
            parent_id = getattr(channel, "parent_id", None)
            if parent_id is not None:
                channel_id = parent_id
        if channel_id is None:
            return None
        return incident_store.get_latest_for_channel(channel_id, include_resolved=False)


IncidentCog.__cog_app_commands__.append(incident_group)


async def setup(bot: commands.Bot):
    await bot.add_cog(IncidentCog(bot))


def json_dumps_compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
