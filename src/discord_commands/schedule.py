"""Schedule commands: /schedule (list, add, remove, toggle) as subcommands."""

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from permissions import require_auth
from scheduler import scheduler
from ui_components import EmbedColors


def _register_schedule_commands(bot: commands.Bot) -> None:
    """Register /schedule group with subcommands: list, add, remove, toggle."""

    schedule_group = app_commands.Group(name="schedule", description="Manage scheduled tasks")

    # ------------------------------------------------------------------
    # /schedule list
    # ------------------------------------------------------------------
    @schedule_group.command(name="list", description="Show all scheduled tasks")
    @require_auth
    async def schedule_list(interaction: discord.Interaction):
        tasks = scheduler.list_tasks()
        if not tasks:
            await interaction.response.send_message("📅 No scheduled tasks.", ephemeral=True)
            return

        lines = []
        for t in tasks:
            status = "✅" if t.enabled else "⏸️"
            schedule_str = (
                f"every {t.interval_minutes}m" if t.interval_minutes > 0 else f"{t.cron_hour:02d}:{t.cron_minute:02d}"
            )
            lines.append(
                f"{status} `{t.task_id}` — **{t.action}** @ {schedule_str} "
                f"(runs: {t.run_count}, next: {t.next_run_str})"
            )

        embed = discord.Embed(
            title=f"📅 Scheduled Tasks ({len(tasks)})",
            description="\n".join(lines),
            color=EmbedColors.INFO,
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /schedule add
    # ------------------------------------------------------------------
    @schedule_group.command(name="add", description="Add a new scheduled task")
    @app_commands.describe(
        skill="Skill name to execute (e.g., check_arr_health)",
        hour="Hour (0-23) for daily schedule (use -1 with interval)",
        minute="Minute (0-59)",
        interval="Interval in minutes (overrides hour/minute if set)",
    )
    @require_auth
    async def schedule_add(
        interaction: discord.Interaction,
        skill: str,
        hour: int = -1,
        minute: int = 0,
        interval: int = 0,
    ):
        if not skill:
            await interaction.response.send_message(
                "❌ Skill name is required.\n💡 Example: `/schedule add skill:check_arr_health hour:6 minute:30`",
                ephemeral=True,
            )
            return

        task = scheduler.create(
            action=skill,
            hour=hour,
            minute=minute,
            interval_minutes=interval,
            created_by=str(interaction.user),
        )

        schedule_str = f"every {interval}m" if interval > 0 else f"daily at {hour:02d}:{minute:02d}"

        await interaction.response.send_message(
            f"✅ Scheduled `{task.task_id}`: **{skill}** — {schedule_str}", ephemeral=True
        )
        audit_log(interaction.user, "schedule_add", detail=f"{task.task_id} {skill}")

    # ------------------------------------------------------------------
    # /schedule remove
    # ------------------------------------------------------------------
    @schedule_group.command(name="remove", description="Remove a scheduled task")
    @app_commands.describe(task_id="Task ID to remove (e.g., sched-1)")
    @require_auth
    async def schedule_remove(
        interaction: discord.Interaction,
        task_id: str,
    ):
        if not task_id:
            await interaction.response.send_message(
                "❌ Task ID is required.\n💡 Use `/schedule list` to see available task IDs.",
                ephemeral=True,
            )
            return

        if scheduler.remove(task_id):
            await interaction.response.send_message(f"🗑️ Removed `{task_id}`.", ephemeral=True)
            audit_log(interaction.user, "schedule_remove", detail=task_id)
        else:
            await interaction.response.send_message(
                f"❌ Task `{task_id}` not found.\n💡 Use `/schedule list` to see available tasks.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /schedule toggle
    # ------------------------------------------------------------------
    @schedule_group.command(name="toggle", description="Enable or disable a scheduled task")
    @app_commands.describe(task_id="Task ID to toggle (e.g., sched-1)")
    @require_auth
    async def schedule_toggle(
        interaction: discord.Interaction,
        task_id: str,
    ):
        if not task_id:
            await interaction.response.send_message(
                "❌ Task ID is required.\n💡 Use `/schedule list` to see available task IDs.",
                ephemeral=True,
            )
            return

        new_state = scheduler.toggle(task_id)
        if new_state is None:
            await interaction.response.send_message(
                f"❌ Task `{task_id}` not found.\n💡 Use `/schedule list` to see available tasks.",
                ephemeral=True,
            )
        else:
            emoji = "✅" if new_state else "⏸️"
            await interaction.response.send_message(
                f"{emoji} Task `{task_id}` {'enabled' if new_state else 'disabled'}.", ephemeral=True
            )
            audit_log(interaction.user, "schedule_toggle", detail=f"{task_id} enabled={new_state}")

    # Register the group with the bot
    bot.tree.add_command(schedule_group)
