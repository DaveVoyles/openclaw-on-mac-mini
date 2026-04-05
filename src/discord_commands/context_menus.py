"""Context menu (right-click) commands."""

import discord
from discord import app_commands
from discord.ext import commands

from cogs.sms_cog import SMSSendConfirmView
from sms_ux import format_sms_error, validate_sms_body

from ._helpers import _is_allowed


def _register_context_menus(bot: commands.Bot) -> None:
    """Register standalone context-menu commands."""

    async def _send_to_sms(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.",
                ephemeral=True,
            )
            return

        raw = (message.content or "").strip()
        if not raw:
            await interaction.response.send_message(
                "❌ Selected message has no text content to send via SMS.",
                ephemeral=True,
            )
            return

        try:
            cleaned = validate_sms_body(raw)
            preview = cleaned if len(cleaned) <= 220 else f"{cleaned[:220]}…"
            embed = discord.Embed(
                title="📲 Send Selected Message to SMS?",
                description=f"```text\n{preview}\n```",
                color=discord.Color.orange(),
            )
            embed.set_footer(text="Confirm to send to your configured phone.")
            await interaction.response.send_message(
                embed=embed,
                view=SMSSendConfirmView(interaction.user.id, cleaned),
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.response.send_message(format_sms_error(exc), ephemeral=True)

    bot.tree.add_command(app_commands.ContextMenu(name="Send to SMS", callback=_send_to_sms))
