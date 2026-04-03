"""Safety commands: /pending, /estop."""

import discord
from discord import app_commands
from discord.ext import commands

from approvals import approval_store, is_emergency_stopped, set_emergency_stop
from audit import audit_log

from ._helpers import require_auth


def _register_safety_commands(bot: commands.Bot) -> None:
    """Register /pending and /estop."""

    @bot.tree.command(name="pending", description="List pending approval requests")
    @require_auth
    async def pending_cmd(interaction: discord.Interaction):
        pending = approval_store.list_pending()
        if not pending:
            await interaction.response.send_message("\u2705 No pending approval requests.", ephemeral=True)
            return

        lines = []
        for req in pending:
            lines.append(
                f"\u2022 `{req.request_id}` \u2014 **{req.action}** `{req.target}` "
                f"(by {req.requester_name}, {req.age_seconds}s ago)"
            )

        embed = discord.Embed(
            title=f"\u23f3 Pending Approvals ({len(pending)})",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "pending")

    @bot.tree.command(name="estop", description="Emergency stop \u2014 halt or resume all bot actions")
    @app_commands.describe(action="'stop' to halt, 'resume' to resume (default: stop)")
    @require_auth
    async def estop_cmd(interaction: discord.Interaction, action: str = "stop"):
        if action.lower() in ("resume", "start", "off", "deactivate"):
            set_emergency_stop(False)
            embed = discord.Embed(
                title="✅ Emergency Stop Deactivated",
                description="Bot is now accepting actions.",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed)
            audit_log(interaction.user, "estop", detail="resume")
        else:
            set_emergency_stop(True)
            embed = discord.Embed(
                title="🛑 EMERGENCY STOP ACTIVATED",
                description=(
                    "All write actions (restart, etc.) are now blocked.\n"
                    "Use `/estop resume` to resume normal operations."
                ),
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed)
            audit_log(interaction.user, "estop", detail="activated")
