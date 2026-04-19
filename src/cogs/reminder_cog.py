"""
Reminder & Timer Cog — personal reminders and countdown timers.

Commands
--------
/remind <when> <message>  — e.g. ``/remind in 30m Check the oven``
/remind list              — show your pending reminders
/remind cancel <id>       — cancel a reminder by ID
/timer <duration>         — simple countdown (e.g. ``/timer 25m``)
"""

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from reminder_manager import parse_time_expression, reminder_manager

log = logging.getLogger("openclaw.reminder_cog")


def _relative_time(ts: float) -> str:
    """Return a human-readable relative time string like 'in 28 minutes'."""
    delta = ts - time.time()
    if delta <= 0:
        return "now"
    if delta < 60:
        return f"in {int(delta)}s"
    if delta < 3600:
        mins = int(delta / 60)
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    if delta < 86400:
        hrs = int(delta / 3600)
        return f"in {hrs} hour{'s' if hrs != 1 else ''}"
    days = int(delta / 86400)
    return f"in {days} day{'s' if days != 1 else ''}"


def _parse_duration_seconds(expr: str) -> int | None:
    """Parse a simple duration like '25m', '90s', '2h' into seconds."""
    import re

    m = re.match(r"^(\d+)\s*(s|sec|m|min|h|hr|hour)s?$", expr.strip().lower())
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2)[0]
    return val * {"s": 1, "m": 60, "h": 3600}.get(unit, 60)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class ReminderCog(commands.Cog):
    """Personal reminders and countdown timers."""

    remind_group = app_commands.Group(name="remind", description="Set, list, or cancel personal reminders")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- /remind set <when> <message> [recurring] ---------------------------

    @remind_group.command(name="set", description="Create a reminder")
    @app_commands.describe(
        when="When to fire — e.g. 'in 30m', 'at 3pm', 'at 15:00'",
        message="What to remind you about",
        recurring="Repeat schedule: daily, weekly, or leave blank for one-shot",
    )
    @app_commands.choices(
        recurring=[
            app_commands.Choice(name="One-shot", value=""),
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Weekly", value="weekly"),
        ]
    )
    async def remind_set(
        self,
        interaction: discord.Interaction,
        when: str,
        message: str,
        recurring: str = "",
    ) -> None:
        fire_at = parse_time_expression(when)
        if fire_at is None:
            await interaction.response.send_message(
                "❌ Could not parse time. Try `in 30m`, `at 3pm`, or `at 15:00`.",
                ephemeral=True,
            )
            return

        r = reminder_manager.add(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            message=message,
            fire_at=fire_at,
            recurring=recurring,
        )

        embed = discord.Embed(
            title="✅ Reminder set",
            description=message,
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Fires",
            value=f"{_relative_time(fire_at)} — <t:{int(fire_at)}:F>",
            inline=False,
        )
        if recurring:
            embed.add_field(name="Recurring", value=recurring.capitalize())
        embed.set_footer(text=f"ID: {r.id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /remind list --------------------------------------------------------

    @remind_group.command(name="list", description="List your pending reminders")
    async def remind_list(self, interaction: discord.Interaction) -> None:
        reminders = reminder_manager.list_for_user(interaction.user.id)
        if not reminders:
            await interaction.response.send_message("📭 No pending reminders.", ephemeral=True)
            return

        embed = discord.Embed(
            title="📋 Your Reminders",
            color=discord.Color.blue(),
        )
        for r in sorted(reminders, key=lambda x: x.fire_at):
            recur_tag = f" 🔁 {r.recurring}" if r.recurring else ""
            embed.add_field(
                name=f"`{r.id}` — {_relative_time(r.fire_at)}{recur_tag}",
                value=r.message[:200],
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /remind cancel <id> -------------------------------------------------

    @remind_group.command(name="cancel", description="Cancel a reminder by ID")
    @app_commands.describe(reminder_id="The 8-character reminder ID")
    async def remind_cancel(self, interaction: discord.Interaction, reminder_id: str) -> None:
        if reminder_manager.cancel(reminder_id, interaction.user.id):
            await interaction.response.send_message(f"🗑️ Reminder `{reminder_id}` cancelled.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ No reminder with ID `{reminder_id}` found (or it's not yours).",
                ephemeral=True,
            )

    # -- /timer <duration> ---------------------------------------------------

    @app_commands.command(name="timer", description="Start a countdown timer")
    @app_commands.describe(duration="Duration — e.g. '25m', '90s', '2h'")
    async def timer(self, interaction: discord.Interaction, duration: str) -> None:
        seconds = _parse_duration_seconds(duration)
        if seconds is None or seconds <= 0:
            await interaction.response.send_message("❌ Invalid duration. Try `25m`, `90s`, or `2h`.", ephemeral=True)
            return

        end_ts = time.time() + seconds
        embed = discord.Embed(
            title="⏱️ Timer started",
            description=f"**{duration}** — ends <t:{int(end_ts)}:R>",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

        # Wait, then ping the user
        await asyncio.sleep(seconds)
        await interaction.followup.send(f"⏰ {interaction.user.mention} — your **{duration}** timer is up!")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReminderCog(bot))
