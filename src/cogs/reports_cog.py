"""Reports Cog — weekly recaps and sports watch guides for Discord."""

from __future__ import annotations

import io
import logging
import datetime as dt
import re

import discord
from discord import app_commands
from discord.ext import commands

from bot_formatting import (
    build_attachment_embed_summary,
    build_brief_detail_bundle,
    build_copy_safe_text_bundle,
    format_markdown_for_discord,
    format_tables_for_context,
    format_tables_for_copy,
    should_package_as_attachment,
    split_mobile_safe_bundle,
    split_response,
)
from cog_helpers import audit_log, require_auth
from copy_workflow_formatter import build_copy_workflow_payload
from memory import store as conversation_store
from permissions import is_allowed
from runtime_state import record_channel_profile_signal
from scheduler import scheduler
from ui_components import EmbedColors

log = logging.getLogger("openclaw.reports_cog")

_DEFAULT_WEEKLY_CRON = "0 9 * * 1"
_STATUS_LINE_RE = re.compile(r"^- Status:\s*(.+)$", re.MULTILINE)
_SHORTFALL_LINE_RE = re.compile(r"^- (?:Source diversity shortfall|Coverage shortfall):\s*(.+)$", re.MULTILINE)
_SCOPE_HINT_LINE_RE = re.compile(r"^- (?:Retry scope hint):\s*(.+)$", re.MULTILINE)
_RUNTIME_LINE_RE = re.compile(r"^- (?:Runtime mode|Degrade mode):\s*(.+)$", re.MULTILINE)


def _interaction_scope(interaction: discord.Interaction) -> tuple[int | None, int | None]:
    channel_obj = getattr(interaction, "channel", None)
    thread_id = channel_obj.id if isinstance(channel_obj, discord.Thread) else None
    channel_id = getattr(interaction, "channel_id", None)
    if channel_id is None and channel_obj is not None:
        channel_id = getattr(channel_obj, "id", None)
    if isinstance(channel_obj, discord.Thread) and channel_obj.parent_id:
        channel_id = channel_obj.parent_id
    return channel_id, thread_id


def _extract_report_recovery_summary(text: str) -> str | None:
    """Extract a compact coverage/shortfall summary from report markdown."""
    body = text or ""
    has_runtime_signal = _RUNTIME_LINE_RE.search(body) is not None or "degrade_mode=constrained" in body
    if "## 📎 Coverage Summary" not in body and "Partial coverage warning" not in body and not has_runtime_signal:
        return None
    status_match = _STATUS_LINE_RE.search(body)
    shortfall_match = _SHORTFALL_LINE_RE.search(body)
    scope_hint_match = _SCOPE_HINT_LINE_RE.search(body)
    runtime_match = _RUNTIME_LINE_RE.search(body)
    warning_detected = "Partial coverage warning" in body
    runtime_detected = "degrade_mode=constrained" in body
    runtime_text = runtime_match.group(1).strip() if runtime_match else ""
    runtime_constrained = runtime_detected or ("constrained" in runtime_text.lower())

    parts: list[str] = []
    if status_match:
        parts.append(status_match.group(1).strip())
    elif warning_detected:
        parts.append("⚠️ Partial coverage")
    if runtime_constrained:
        parts.append("Runtime constrained")
    if shortfall_match:
        parts.append(shortfall_match.group(1).strip())
    if scope_hint_match:
        parts.append(f"Retry scope: {scope_hint_match.group(1).strip()}")
    return " · ".join(part for part in parts if part).strip() or None


class ReportsCog(commands.Cog, name="Reports"):
    """High-value Discord workflows for recaps and watch guides."""

    recap = app_commands.Group(name="recap", description="Weekly channel recaps and summaries")
    sports = app_commands.Group(name="sports", description="Sports schedules and watch guides")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.recap_ctx = app_commands.ContextMenu(
            name="Create recap from thread",
            callback=self._recap_from_message,
        )
        self.bot.tree.add_command(self.recap_ctx)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.recap_ctx.name, type=self.recap_ctx.type)

    async def cog_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        msg = str(error) if isinstance(error, app_commands.CheckFailure) else f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    async def _send_chunks(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        body: str,
        color: discord.Color,
        ephemeral: bool = False,
    ) -> None:
        table_image_file = None
        try:
            from table_renderer import (
                extract_table_text,
                render_table_image,
                should_render_table_image,
            )
            table_text = extract_table_text(body)
            if table_text and should_render_table_image(table_text):
                img_bytes = render_table_image(table_text)
                if img_bytes:
                    table_image_file = discord.File(io.BytesIO(img_bytes), filename="table.png")
        except Exception as exc:
            log.debug("Report table image fallback unavailable: %s", exc)

        formatted_body = format_markdown_for_discord(body)
        channel_obj = getattr(interaction, "channel", None)
        thread_id = channel_obj.id if isinstance(channel_obj, discord.Thread) else None
        channel_id = getattr(interaction, "channel_id", None)
        if channel_id is None and channel_obj is not None:
            channel_id = getattr(channel_obj, "id", None)
        if isinstance(channel_obj, discord.Thread) and channel_obj.parent_id:
            channel_id = channel_obj.parent_id
        if channel_id is None and thread_id is None:
            formatted_body = format_tables_for_copy(formatted_body)
        else:
            formatted_body = format_tables_for_context(
                formatted_body,
                channel_id=channel_id,
                thread_id=thread_id,
            )
        chunks = split_response(formatted_body)
        if should_package_as_attachment(formatted_body, chunks):
            ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
            package = discord.File(
                io.BytesIO(formatted_body.encode("utf-8")),
                filename=f"openclaw-report-{ts}.md",
            )
            embed = discord.Embed(
                title=title,
                description=build_attachment_embed_summary(
                    formatted_body,
                    attachment_note="📎 **Full report attached as file**",
                    coverage_summary=_extract_report_recovery_summary(formatted_body),
                ),
                color=color,
            )
            await interaction.followup.send(embed=embed, file=package, ephemeral=ephemeral)
        else:
            embed = discord.Embed(
                title=title,
                description=chunks[0] if chunks else "",
                color=color,
            )
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        if table_image_file:
            await interaction.followup.send(file=table_image_file, ephemeral=ephemeral)

    async def _recap_from_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        """Right-click helper for quickly recapping a thread or channel."""
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        from reporting_skills import generate_channel_recap_report

        focus = "Summarize the discussion around this selected message."
        if message.content:
            focus += f" Anchor message: {message.content[:180]}"

        await interaction.response.defer(ephemeral=True)
        report = await generate_channel_recap_report(
            channel_id=message.channel.id,
            days=7,
            focus=focus,
            style="action-items",
        )
        await self._send_chunks(
            interaction,
            title="📝 Thread Recap",
            body=report,
            color=EmbedColors.INFO,
            ephemeral=True,
        )
        record_channel_profile_signal(message.channel.id, signal="recap_generated")
        audit_log(interaction.user, "context_recap", detail=f"channel={message.channel.id}")

    @staticmethod
    def _latest_model_response_text(conv) -> str:
        latest_response = ""
        for message in reversed(conv.history):
            if message.get("role") != "model":
                continue
            parts = [part for part in message.get("parts", []) if isinstance(part, str)]
            if parts:
                latest_response = "\n".join(part.strip() for part in parts if part.strip()).strip()
            if latest_response:
                break
        return latest_response

    async def _send_text_package(
        self,
        interaction: discord.Interaction,
        *,
        label: str,
        text: str,
    ) -> None:
        chunks = split_mobile_safe_bundle(text)
        if should_package_as_attachment(text, chunks):
            package = discord.File(
                io.BytesIO((text or "").encode("utf-8")),
                filename="report-package.txt",
            )
            embed = discord.Embed(
                title=label,
                description=build_attachment_embed_summary(
                    text,
                    attachment_note="📎 **Full package attached as file**",
                    coverage_summary=_extract_report_recovery_summary(text),
                ),
                color=EmbedColors.INFO,
            )
            await interaction.followup.send(embed=embed, file=package, ephemeral=True)
            return

        chunk = chunks[0] if chunks else ""
        await interaction.followup.send(f"{label}\n{chunk}", ephemeral=True)

    async def _send_artifact_package(
        self,
        interaction: discord.Interaction,
        *,
        report_text: str,
        label: str,
    ) -> None:
        artifact_text = format_tables_for_copy(format_markdown_for_discord(report_text or "")).strip()
        if not artifact_text:
            await interaction.followup.send("❌ No text content available for artifact packaging.", ephemeral=True)
            return

        files: list[discord.File] = [
            discord.File(io.BytesIO(artifact_text.encode("utf-8")), filename="report-package.txt")
        ]
        try:
            from table_renderer import extract_table_text, render_table_image, should_render_table_image

            table_text = extract_table_text(report_text)
            if table_text and should_render_table_image(table_text):
                img_bytes = render_table_image(table_text)
                if img_bytes:
                    files.append(discord.File(io.BytesIO(img_bytes), filename="report-table.png"))
        except Exception as exc:
            log.debug("Artifact table image generation unavailable: %s", exc)

        await interaction.followup.send(
            f"{label}\nAttached: report-package.txt" + (" + report-table.png" if len(files) > 1 else ""),
            files=files,
            ephemeral=True,
        )

    async def _package_response(
        self,
        interaction: discord.Interaction,
        *,
        source_text: str,
        variant: str,
        source_label: str,
    ) -> None:
        if variant == "copy-safe":
            payload = build_copy_safe_text_bundle(source_text)
            if not payload:
                await interaction.followup.send("❌ No text content to export.", ephemeral=True)
                return
            await self._send_text_package(
                interaction,
                label=f"📋 Copy-safe text bundle ({source_label})",
                text=payload,
            )
            return

        if variant == "artifact":
            await self._send_artifact_package(
                interaction,
                report_text=source_text,
                label=f"📦 Attached artifact package ({source_label})",
            )
            return

        payload = build_brief_detail_bundle(source_text)
        if not payload:
            await interaction.followup.send("❌ No text content to export.", ephemeral=True)
            return
        await self._send_text_package(
            interaction,
            label=f"🧾 Brief+Detail package ({source_label})",
            text=payload,
        )

    @recap.command(name="copy-latest", description="Copy-ready export of your latest OpenClaw response")
    @require_auth()
    async def recap_copy_latest(self, interaction: discord.Interaction):
        conv = conversation_store.get(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            user_name=str(interaction.user.display_name),
        )

        latest_response = self._latest_model_response_text(conv)

        if not latest_response:
            await interaction.response.send_message(
                "❌ No recent OpenClaw response found in this channel/thread.",
                ephemeral=True,
            )
            return

        payload = build_copy_workflow_payload(latest_response)
        if not payload:
            await interaction.response.send_message(
                "❌ Latest response has no text content to export.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"📋 Copy-ready export (latest response):\n```text\n{payload}\n```",
            ephemeral=True,
        )
        channel_id, thread_id = _interaction_scope(interaction)
        record_channel_profile_signal(channel_id, thread_id=thread_id, signal="recap_copy_export")
        audit_log(interaction.user, "recap_copy_latest", detail=f"channel={interaction.channel_id}")

    @recap.command(name="package-latest", description="Package your latest OpenClaw response for sharing")
    @app_commands.describe(
        variant="Packaging variant",
    )
    @app_commands.choices(
        variant=[
            app_commands.Choice(name="Copy-safe text bundle", value="copy-safe"),
            app_commands.Choice(name="Attached artifact package", value="artifact"),
            app_commands.Choice(name="Brief + Detail package", value="brief-detail"),
        ]
    )
    @require_auth()
    async def recap_package_latest(
        self,
        interaction: discord.Interaction,
        variant: str = "copy-safe",
    ):
        conv = conversation_store.get(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            user_name=str(interaction.user.display_name),
        )
        latest_response = self._latest_model_response_text(conv)
        if not latest_response:
            await interaction.response.send_message(
                "❌ No recent OpenClaw response found in this channel/thread.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self._package_response(
            interaction,
            source_text=latest_response,
            variant=variant,
            source_label="latest response",
        )
        audit_log(
            interaction.user,
            "recap_package_latest",
            detail=f"channel={interaction.channel_id} variant={variant}",
        )

    @recap.command(name="copy-thread", description="Generate and export a copy-ready recap for this channel/thread")
    @app_commands.describe(
        days="How many days to include (1-30, default 7)",
        focus="Optional topic or angle to emphasize",
        style="Highlights, action items, or a short table",
    )
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Highlights", value="highlights"),
            app_commands.Choice(name="Action Items", value="action-items"),
            app_commands.Choice(name="Table", value="table"),
        ]
    )
    @require_auth()
    async def recap_copy_thread(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 30] = 7,
        focus: str = "",
        style: str = "action-items",
    ):
        from skills.reporting_skills import generate_channel_recap_report

        await interaction.response.defer(ephemeral=True)
        report = await generate_channel_recap_report(
            channel_id=interaction.channel_id,
            days=days,
            focus=focus,
            style=style,
        )

        payload = build_copy_workflow_payload(report)
        if not payload:
            await interaction.followup.send(
                "❌ Recap had no text content to export.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"📋 Copy-ready export (thread recap):\n```text\n{payload}\n```",
            ephemeral=True,
        )
        channel_id, thread_id = _interaction_scope(interaction)
        record_channel_profile_signal(channel_id, thread_id=thread_id, signal="recap_generated")
        record_channel_profile_signal(channel_id, thread_id=thread_id, signal="recap_copy_export")
        audit_log(
            interaction.user,
            "recap_copy_thread",
            detail=f"channel={interaction.channel_id} days={days} style={style}",
        )

    @recap.command(name="package-thread", description="Generate and package a recap for this channel/thread")
    @app_commands.describe(
        days="How many days to include (1-30, default 7)",
        focus="Optional topic or angle to emphasize",
        style="Highlights, action items, or a short table",
        variant="Packaging variant",
    )
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Highlights", value="highlights"),
            app_commands.Choice(name="Action Items", value="action-items"),
            app_commands.Choice(name="Table", value="table"),
        ],
        variant=[
            app_commands.Choice(name="Copy-safe text bundle", value="copy-safe"),
            app_commands.Choice(name="Attached artifact package", value="artifact"),
            app_commands.Choice(name="Brief + Detail package", value="brief-detail"),
        ],
    )
    @require_auth()
    async def recap_package_thread(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 30] = 7,
        focus: str = "",
        style: str = "action-items",
        variant: str = "copy-safe",
    ):
        from skills.reporting_skills import generate_channel_recap_report

        await interaction.response.defer(ephemeral=True)
        report = await generate_channel_recap_report(
            channel_id=interaction.channel_id,
            days=days,
            focus=focus,
            style=style,
        )
        await self._package_response(
            interaction,
            source_text=report,
            variant=variant,
            source_label="thread recap",
        )
        audit_log(
            interaction.user,
            "recap_package_thread",
            detail=f"channel={interaction.channel_id} days={days} style={style} variant={variant}",
        )

    @recap.command(name="weekly", description="Summarize the current channel or thread for the last few days")
    @app_commands.describe(
        days="How many days to include (1-30, default 7)",
        focus="Optional topic or angle to emphasize",
        style="Highlights, action items, or a short table",
        save_to_vault="Also save the recap to the Obsidian vault",
        schedule_weekly="Schedule this recap for Mondays at 09:00",
    )
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Highlights", value="highlights"),
            app_commands.Choice(name="Action Items", value="action-items"),
            app_commands.Choice(name="Table", value="table"),
        ]
    )
    @require_auth()
    async def recap_weekly(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 30] = 7,
        focus: str = "",
        style: str = "highlights",
        save_to_vault: bool = False,
        schedule_weekly: bool = False,
    ):
        from obsidian_writer import save_to_vault as save_report_to_vault
        from skills.reporting_skills import generate_channel_recap_report

        await interaction.response.defer(thinking=True)  # Progress indicator

        report = await generate_channel_recap_report(
            channel_id=interaction.channel_id,
            days=days,
            focus=focus,
            style=style,
        )
        await self._send_chunks(
            interaction,
            title="🗓️ Weekly Recap",
            body=report,
            color=EmbedColors.INFO,
        )
        channel_id, thread_id = _interaction_scope(interaction)
        record_channel_profile_signal(channel_id, thread_id=thread_id, signal="recap_generated")

        if save_to_vault:
            channel_name = getattr(interaction.channel, "name", f"channel-{interaction.channel_id}")
            vault_result = await save_report_to_vault(
                title=f"Weekly Recap - {channel_name}",
                content=report,
                tags=["discord", "weekly-recap", channel_name],
                content_type="note",
            )
            await interaction.followup.send(vault_result)

        if schedule_weekly:
            task = scheduler.create(
                action="generate_channel_recap_report",
                args={
                    "channel_id": int(interaction.channel_id or 0),
                    "days": int(days),
                    "focus": focus,
                    "style": style,
                },
                cron_expression=_DEFAULT_WEEKLY_CRON,
                created_by=str(interaction.user),
                notify_channel_id=int(interaction.channel_id or 0),
                alert_only=False,
            )
            await interaction.followup.send(
                f"📅 Scheduled this recap as `{task.task_id}` for Mondays at 09:00. "
                "Manage it later with `/schedule`."
            )

        audit_log(
            interaction.user,
            "recap_weekly",
            detail=f"channel={interaction.channel_id} days={days} style={style}",
        )

    @sports.command(name="upcoming", description="Create a sports watch guide with times and where to watch")
    @app_commands.describe(
        query="Optional natural-language request, e.g. 'men's division 1 college lacrosse this week'",
        sport="Sport name, e.g. college lacrosse",
        league="League or division, e.g. NCAA Division 1",
        team="Optional team name to narrow the guide",
        days="How many days to look ahead (1-14, default 7)",
        include_watch_info="Include TV/streaming guidance when available",
        save_to_vault="Also save the watch guide to the Obsidian vault",
        schedule_weekly="Schedule this report for Mondays at 09:00",
    )
    @require_auth()
    async def sports_upcoming(
        self,
        interaction: discord.Interaction,
        query: str = "",
        sport: str = "",
        league: str = "",
        team: str = "",
        days: app_commands.Range[int, 1, 14] = 7,
        include_watch_info: bool = True,
        save_to_vault: bool = False,
        schedule_weekly: bool = False,
    ):
        from obsidian_writer import save_to_vault as save_report_to_vault
        from skills.reporting_skills import (
            build_sports_watch_query,
            generate_sports_watch_report,
        )

        await interaction.response.defer()

        effective_query = build_sports_watch_query(
            query=query,
            sport=sport,
            league=league,
            team=team,
            days=days,
        )
        report = await generate_sports_watch_report(
            query=query,
            sport=sport,
            league=league,
            team=team,
            days=days,
            include_watch_info=include_watch_info,
        )
        await self._send_chunks(
            interaction,
            title="🥍 Sports Watch Guide",
            body=report,
            color=discord.Color.green(),
        )

        if save_to_vault:
            title = team.strip() or league.strip() or sport.strip() or "Sports Watch Guide"
            vault_result = await save_report_to_vault(
                title=f"{title} - watch guide",
                content=report,
                tags=["sports", "watch-guide"],
                content_type="research",
            )
            await interaction.followup.send(vault_result)

        if schedule_weekly:
            task = scheduler.create(
                action="generate_sports_watch_report",
                args={
                    "query": query,
                    "sport": sport,
                    "league": league,
                    "team": team,
                    "days": int(days),
                    "include_watch_info": bool(include_watch_info),
                },
                cron_expression=_DEFAULT_WEEKLY_CRON,
                created_by=str(interaction.user),
                notify_channel_id=int(interaction.channel_id or 0),
                alert_only=False,
            )
            await interaction.followup.send(
                f"📅 Scheduled this sports guide as `{task.task_id}` for Mondays at 09:00. "
                "Manage it later with `/schedule`."
            )

        audit_log(
            interaction.user,
            "sports_upcoming",
            detail=effective_query[:200],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ReportsCog(bot))
