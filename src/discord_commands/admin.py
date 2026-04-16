"""Admin commands: /admin-reload-routing."""

import importlib

import discord
from discord.ext import commands

from ._helpers import require_auth


def _register_admin_commands(bot: commands.Bot) -> None:
    """Register /admin-reload-routing."""

    @bot.tree.command(
        name="admin-reload-routing",
        description="Reload model_routing_policy without restarting the bot (owner only)",
    )
    @require_auth
    async def admin_reload_routing_cmd(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            import model_routing_policy

            importlib.reload(model_routing_policy)
            await interaction.followup.send(
                "✅ Routing policy reloaded. New settings are live.", ephemeral=True
            )
        except Exception as exc:  # broad: intentional
            await interaction.followup.send(
                f"❌ Reload failed: {exc}", ephemeral=True
            )
