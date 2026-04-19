"""
Expense Cog — category-based expense logging with period summaries.
Commands: /expense add, /expense list, /expense summary, /expense delete
"""

import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from expense_tracker import CATEGORIES, CATEGORY_EMOJIS, ExpenseTracker

log = logging.getLogger(__name__)


class ExpenseCog(commands.Cog):
    """Log and review personal expenses by category."""

    expense_group = app_commands.Group(name="expense", description="Expense tracking commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tracker = ExpenseTracker()

    @expense_group.command(name="add", description="Log an expense")
    @app_commands.describe(
        amount="Amount spent",
        category="Category (food, transport, entertainment, shopping, bills, health, other)",
        note="Optional note",
    )
    @app_commands.choices(
        category=[app_commands.Choice(name=f"{CATEGORY_EMOJIS[c]} {c.title()}", value=c) for c in CATEGORIES]
    )
    async def expense_add(
        self,
        interaction: discord.Interaction,
        amount: float,
        category: app_commands.Choice[str],
        note: str = "",
    ) -> None:
        if amount <= 0:
            await interaction.response.send_message("❌ Amount must be positive", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        expense = self.tracker.add(user_id, amount, category.value, note)
        emoji = CATEGORY_EMOJIS.get(expense.category, "📦")
        await interaction.response.send_message(
            f"{emoji} Logged **${expense.amount:.2f}** in **{expense.category}**"
            + (f" — _{expense.note}_" if expense.note else "")
        )

    @expense_group.command(name="list", description="Show recent expenses")
    @app_commands.describe(days="Number of days to look back (default: 7)")
    async def expense_list(self, interaction: discord.Interaction, days: int = 7) -> None:
        user_id = str(interaction.user.id)
        expenses = self.tracker.list_for_user(user_id, days)

        if not expenses:
            await interaction.response.send_message(f"No expenses in the last {days} day(s).", ephemeral=True)
            return

        lines = []
        for e in sorted(
            expenses,
            key=lambda x: x.timestamp,
            reverse=True,
        ):
            dt = datetime.fromisoformat(e.timestamp)
            date_str = dt.strftime("%m/%d")
            emoji = CATEGORY_EMOJIS.get(e.category, "📦")
            note_str = f" — _{e.note}_" if e.note else ""
            lines.append(f"`{e.id}` {date_str} {emoji} **${e.amount:.2f}** {e.category}{note_str}")

        total = sum(e.amount for e in expenses)
        embed = discord.Embed(
            title=f"💰 Expenses (last {days} days)",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Total: ${total:.2f}")
        await interaction.response.send_message(embed=embed)

    @expense_group.command(name="summary", description="Category breakdown with totals")
    @app_commands.describe(period="Time period: week, month, or year (default: week)")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Week", value="week"),
            app_commands.Choice(name="Month", value="month"),
            app_commands.Choice(name="Year", value="year"),
        ]
    )
    async def expense_summary(
        self,
        interaction: discord.Interaction,
        period: app_commands.Choice[str] | None = None,
    ) -> None:
        period_val = period.value if period else "week"
        user_id = str(interaction.user.id)
        totals = self.tracker.summary_by_period(user_id, period_val)

        if not totals:
            await interaction.response.send_message(f"No expenses this {period_val}.", ephemeral=True)
            return

        grand_total = sum(totals.values())
        max_amount = max(totals.values()) if totals else 0

        lines = []
        for cat, amount in totals.items():
            emoji = CATEGORY_EMOJIS.get(cat, "📦")
            bar = self.tracker.format_bar(amount, max_amount)
            pct = (amount / grand_total * 100) if grand_total else 0
            lines.append(f"{emoji} **{cat}**: {bar} ${amount:.2f} ({pct:.0f}%)")

        embed = discord.Embed(
            title=f"📊 Expense Summary ({period_val})",
            description="\n".join(lines),
            color=discord.Color.dark_gold(),
        )
        embed.set_footer(text=f"Total: ${grand_total:.2f}")
        await interaction.response.send_message(embed=embed)

    @expense_group.command(name="delete", description="Remove an expense entry")
    @app_commands.describe(expense_id="Expense ID (shown in /expense list)")
    async def expense_delete(self, interaction: discord.Interaction, expense_id: str) -> None:
        user_id = str(interaction.user.id)
        if self.tracker.delete(user_id, expense_id):
            await interaction.response.send_message(f"🗑️ Deleted expense `{expense_id}`")
        else:
            await interaction.response.send_message(f"❌ Expense `{expense_id}` not found", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExpenseCog(bot))
