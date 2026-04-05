"""Reports Cog — weekly recaps and sports watch guides for Discord."""

from __future__ import annotations

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot_formatting import (
    format_markdown_for_discord,
    format_tables_for_context,
    format_tables_for_copy,
    split_response,
)
from cog_helpers import audit_log, require_auth
from copy_workflow_formatter import build_copy_workflow_payload
from memory import store as conversation_store
from permissions import is_allowed
from scheduler import scheduler
from ui_components import EmbedColors

log = logging.getLogger("openclaw.reports_cog")

_DEFAULT_WEEKLY_CRON = "0 9 * * 1"


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
        for idx, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=title if idx == 0 else f"{title} (cont.)",
                description=chunk,
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
        audit_log(interaction.user, "context_recap", detail=f"channel={message.channel.id}")

    @recap.command(name="copy-latest", description="Copy-ready export of your latest OpenClaw response")
    @require_auth()
    async def recap_copy_latest(self, interaction: discord.Interaction):
        conv = conversation_store.get(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            user_name=str(interaction.user.display_name),
        )

        latest_response = ""
        for message in reversed(conv.history):
            if message.get("role") != "model":
                continue
            parts = [part for part in message.get("parts", []) if isinstance(part, str)]
            if parts:
                latest_response = "\n".join(part.strip() for part in parts if part.strip()).strip()
            if latest_response:
                break

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
        audit_log(interaction.user, "recap_copy_latest", detail=f"channel={interaction.channel_id}")

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
        audit_log(
            interaction.user,
            "recap_copy_thread",
            detail=f"channel={interaction.channel_id} days={days} style={style}",
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
