"""Schedule commands: /schedule."""

import discord
from discord.ext import commands

from audit import audit_log
from scheduler import scheduler


def _register_schedule_commands(bot: commands.Bot) -> None:
    """Register /schedule (list, add, remove, toggle)."""

    @bot.tree.command(name="schedule", description="Manage scheduled tasks")
    @discord.app_commands.describe(
        action="list, add, remove, or toggle",
        skill="Skill name for 'add' (e.g. check_arr_health)",
        hour="Hour (0-23) for daily schedule (-1 for interval)",
        minute="Minute (0-59)",
        interval="Interval in minutes (overrides hour/minute)",
        task_id="Task ID for remove/toggle (e.g. sched-1)",
    )
    async def schedule_cmd(
        interaction: discord.Interaction,
        action: str = "list",
        skill: str = "",
        hour: int = -1,
        minute: int = 0,
        interval: int = 0,
        task_id: str = "",
    ):

        if action == "list":
            tasks = scheduler.list_tasks()
            if not tasks:
                await interaction.response.send_message("📅 No scheduled tasks.", ephemeral=True)
                return
            lines = []
            for t in tasks:
                status = "✅" if t.enabled else "⏸️"
                schedule_str = f"every {t.interval_minutes}m" if t.interval_minutes > 0 else f"{t.cron_hour:02d}:{t.cron_minute:02d}"
                lines.append(
                    f"{status} `{t.task_id}` — **{t.action}** @ {schedule_str} "
                    f"(runs: {t.run_count}, next: {t.next_run_str})"
                )
            embed = discord.Embed(
                title=f"📅 Scheduled Tasks ({len(tasks)})",
                description="\n".join(lines),
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed)

        elif action == "add":
            if not skill:
                await interaction.response.send_message(
                    "❌ Provide a skill name. Example: `/schedule add check_arr_health hour:6`",
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
                f"✅ Scheduled `{task.task_id}`: **{skill}** — {schedule_str}"
            )
            audit_log(interaction.user, "schedule_add", detail=f"{task.task_id} {skill}")

        elif action == "remove":
            if not task_id:
                await interaction.response.send_message("❌ Provide a task_id. Example: `/schedule remove task_id:sched-1`", ephemeral=True)
                return
            if scheduler.remove(task_id):
                await interaction.response.send_message(f"🗑️ Removed `{task_id}`.")
                audit_log(interaction.user, "schedule_remove", detail=task_id)
            else:
                await interaction.response.send_message(f"❌ Task `{task_id}` not found.", ephemeral=True)

        elif action == "toggle":
            if not task_id:
                await interaction.response.send_message("❌ Provide a task_id.", ephemeral=True)
                return
            new_state = scheduler.toggle(task_id)
            if new_state is None:
                await interaction.response.send_message(f"❌ Task `{task_id}` not found.", ephemeral=True)
            else:
                emoji = "✅" if new_state else "⏸️"
                await interaction.response.send_message(f"{emoji} Task `{task_id}` {'enabled' if new_state else 'disabled'}.")
                audit_log(interaction.user, "schedule_toggle", detail=f"{task_id} enabled={new_state}")
        else:
            await interaction.response.send_message(
                "❌ Unknown action. Use: `list`, `add`, `remove`, or `toggle`.",
                ephemeral=True,
            )
