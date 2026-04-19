"""
Habit Cog — daily streak tracking with sparkline visualization.
Commands: /habit add, /habit checkin, /habit streak, /habit list, /habit delete
"""

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from habit_tracker import HabitTracker

log = logging.getLogger(__name__)


class HabitCog(commands.Cog):
    """Track daily/weekly habits with streaks and sparklines."""

    habit_group = app_commands.Group(name="habit", description="Habit tracking commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tracker = HabitTracker()

    @habit_group.command(name="add", description="Register a new habit to track")
    @app_commands.describe(name="Habit name", frequency="daily or weekly (default: daily)")
    async def habit_add(
        self,
        interaction: discord.Interaction,
        name: str,
        frequency: str = "daily",
    ) -> None:
        if frequency not in ("daily", "weekly"):
            await interaction.response.send_message(
                "❌ Frequency must be `daily` or `weekly`", ephemeral=True
            )
            return

        user_id = str(interaction.user.id)
        existing = self.tracker.list_for_user(user_id)
        if any(h.name.lower() == name.lower() for h in existing):
            await interaction.response.send_message(
                f"❌ You already track **{name}**", ephemeral=True
            )
            return

        habit = self.tracker.add_habit(user_id, name, frequency)
        await interaction.response.send_message(
            f"✅ Now tracking **{habit.name}** ({habit.frequency})"
        )

    @habit_group.command(name="checkin", description="Check in for a habit today")
    @app_commands.describe(name="Habit name")
    async def habit_checkin(self, interaction: discord.Interaction, name: str) -> None:
        user_id = str(interaction.user.id)
        habit = self.tracker.checkin(user_id, name)
        if not habit:
            await interaction.response.send_message(
                f"❌ Habit **{name}** not found", ephemeral=True
            )
            return

        streak = self.tracker.get_streak(habit)
        await interaction.response.send_message(
            f"✅ Checked in for **{habit.name}**! 🔥 Streak: **{streak}** day(s)"
        )

    @habit_group.command(name="streak", description="Show current streaks")
    @app_commands.describe(name="Habit name (leave blank for all)")
    async def habit_streak(
        self, interaction: discord.Interaction, name: str | None = None
    ) -> None:
        user_id = str(interaction.user.id)
        habits = self.tracker.list_for_user(user_id)

        if name:
            habits = [h for h in habits if h.name.lower() == name.lower()]

        if not habits:
            await interaction.response.send_message(
                "No habits found.", ephemeral=True
            )
            return

        embed = discord.Embed(title="🔥 Habit Streaks", color=discord.Color.orange())
        for h in habits:
            streak = self.tracker.get_streak(h)
            sparkline = self.tracker.sparkline(h)
            embed.add_field(
                name=h.name,
                value=f"Streak: **{streak}** day(s)\n`{sparkline}` (8 weeks)",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @habit_group.command(name="list", description="Show all habits with today's status")
    async def habit_list(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        habits = self.tracker.list_for_user(user_id)

        if not habits:
            await interaction.response.send_message(
                "No habits yet. Use `/habit add` to start!", ephemeral=True
            )
            return

        today = datetime.now(timezone.utc).date()
        lines = []
        for h in habits:
            checkin_dates = {
                datetime.fromisoformat(ts).date() for ts in h.checkins
            }
            status = "✅" if today in checkin_dates else "⬜"
            streak = self.tracker.get_streak(h)
            lines.append(f"{status} **{h.name}** — 🔥 {streak} day(s) ({h.frequency})")

        embed = discord.Embed(
            title="📋 Your Habits",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    @habit_group.command(name="delete", description="Remove a habit")
    @app_commands.describe(name="Habit name to delete")
    async def habit_delete(self, interaction: discord.Interaction, name: str) -> None:
        user_id = str(interaction.user.id)
        if self.tracker.delete_habit(user_id, name):
            await interaction.response.send_message(f"🗑️ Deleted habit **{name}**")
        else:
            await interaction.response.send_message(
                f"❌ Habit **{name}** not found", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HabitCog(bot))
