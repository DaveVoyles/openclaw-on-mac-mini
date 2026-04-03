"""
Todo Cog — Personal task list with /todo slash commands.
Supports add, list, done, delete with priority colours and interactive buttons.
"""

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from todo_manager import TodoManager

log = logging.getLogger("openclaw")

PRIORITY_COLOURS = {"low": discord.Color.green(), "medium": discord.Color.gold(), "high": discord.Color.red()}
PRIORITY_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def _parse_due(raw: str | None) -> str | None:
    """Convert human-friendly due date strings to ISO-8601 date."""
    if not raw:
        return None
    low = raw.strip().lower()
    today = datetime.now(timezone.utc).date()
    if low == "today":
        return today.isoformat()
    if low == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    if low in weekdays:
        target = weekdays[low]
        days_ahead = (target - today.weekday()) % 7 or 7
        return (today + timedelta(days=days_ahead)).isoformat()
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _build_list_embed(items, title: str) -> discord.Embed:
    if not items:
        return discord.Embed(title=title, description="No tasks found.", color=discord.Color.light_grey())
    embed = discord.Embed(title=title, color=discord.Color.blurple())
    for item in items[:25]:
        status = "✅" if item.completed else PRIORITY_EMOJI.get(item.priority, "⬜")
        due = f" | 📅 {item.due_date}" if item.due_date else ""
        embed.add_field(
            name=f"{status} `{item.id}` — {item.title}",
            value=f"Priority: **{item.priority}**{due}",
            inline=False,
        )
    return embed


class TodoCompleteButton(discord.ui.Button["TodoListView"]):
    def __init__(self, item_id: str, label: str) -> None:
        super().__init__(style=discord.ButtonStyle.success, label=label, emoji="✅", custom_id=f"todo_done_{item_id}")
        self.item_id = item_id

    async def callback(self, interaction: discord.Interaction) -> None:
        mgr = TodoManager()
        result = mgr.complete(self.item_id, interaction.user.id)
        if result:
            await interaction.response.send_message(f"✅ Marked **{result.title}** as done!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Task not found or not yours.", ephemeral=True)


class TodoListView(discord.ui.View):
    def __init__(self, items) -> None:
        super().__init__(timeout=300)
        for item in items[:25]:
            if not item.completed:
                self.add_item(TodoCompleteButton(item.id, item.id))


todo_group = app_commands.Group(name="todo", description="Personal task list")


class TodoCog(commands.Cog, name="Todo"):
    """Personal per-user task manager with priorities and due dates."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.mgr = TodoManager()

    # ── /todo add ──────────────────────────────────────────

    @todo_group.command(name="add", description="Add a new task")
    @app_commands.describe(
        task="What do you need to do?",
        priority="Priority level (low / medium / high)",
        due="Due date: 'today', 'tomorrow', a weekday name, or YYYY-MM-DD",
    )
    @app_commands.choices(
        priority=[
            app_commands.Choice(name="low", value="low"),
            app_commands.Choice(name="medium", value="medium"),
            app_commands.Choice(name="high", value="high"),
        ]
    )
    async def todo_add(
        self,
        interaction: discord.Interaction,
        task: str,
        priority: app_commands.Choice[str] | None = None,
        due: str | None = None,
    ) -> None:
        prio = priority.value if priority else "medium"
        due_date = _parse_due(due)
        item = self.mgr.add(task, interaction.user.id, priority=prio, due_date=due_date)
        colour = PRIORITY_COLOURS.get(prio, discord.Color.default())
        embed = discord.Embed(title="📝 Task Added", color=colour)
        embed.add_field(name="Task", value=item.title, inline=False)
        embed.add_field(name="Priority", value=f"{PRIORITY_EMOJI[prio]} {prio}", inline=True)
        embed.add_field(name="ID", value=f"`{item.id}`", inline=True)
        if due_date:
            embed.add_field(name="Due", value=f"📅 {due_date}", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /todo list ─────────────────────────────────────────

    @todo_group.command(name="list", description="Show your tasks")
    @app_commands.describe(filter="Filter tasks: all / today / overdue / done")
    @app_commands.choices(
        filter=[
            app_commands.Choice(name="all (pending)", value="all"),
            app_commands.Choice(name="today", value="today"),
            app_commands.Choice(name="overdue", value="overdue"),
            app_commands.Choice(name="done", value="done"),
        ]
    )
    async def todo_list(
        self,
        interaction: discord.Interaction,
        filter: app_commands.Choice[str] | None = None,
    ) -> None:
        f = filter.value if filter else "all"
        items = self.mgr.list_for_user(interaction.user.id, filter_=f)
        embed = _build_list_embed(items, f"📋 Your Tasks — {f}")
        view = TodoListView(items) if items else None
        await interaction.response.send_message(embed=embed, view=view)

    # ── /todo done ─────────────────────────────────────────

    @todo_group.command(name="done", description="Mark a task as complete")
    @app_commands.describe(id="Task ID (shown in /todo list)")
    async def todo_done(self, interaction: discord.Interaction, id: str) -> None:
        result = self.mgr.complete(id, interaction.user.id)
        if result:
            await interaction.response.send_message(f"✅ Marked **{result.title}** as done!")
        else:
            await interaction.response.send_message("❌ Task not found or not yours.", ephemeral=True)

    # ── /todo delete ───────────────────────────────────────

    @todo_group.command(name="delete", description="Remove a task")
    @app_commands.describe(id="Task ID (shown in /todo list)")
    async def todo_delete(self, interaction: discord.Interaction, id: str) -> None:
        if self.mgr.delete(id, interaction.user.id):
            await interaction.response.send_message("🗑️ Task deleted.")
        else:
            await interaction.response.send_message("❌ Task not found or not yours.", ephemeral=True)


# Register group on the cog
TodoCog.__cog_app_commands__.append(todo_group)


async def setup(bot: commands.Bot):
    await bot.add_cog(TodoCog(bot))
