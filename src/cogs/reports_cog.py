"""Reports Cog — weekly recaps and sports watch guides for Discord."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, split_response
from permissions import is_allowed
from scheduler import scheduler

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
    ) -> None:
        chunks = split_response(body)
        for idx, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=title if idx == 0 else f"{title} (cont.)",
                description=chunk,
                color=color,
            )
            await interaction.followup.send(embed=embed)

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
        embed = discord.Embed(
            title="📝 Thread Recap",
            description=report[:4000],
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        audit_log(interaction.user, "context_recap", detail=f"channel={message.channel.id}")

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
        from reporting_skills import generate_channel_recap_report

        await interaction.response.defer()

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
            color=discord.Color.blurple(),
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
        from reporting_skills import build_sports_watch_query, generate_sports_watch_report

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
