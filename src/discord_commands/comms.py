"""Communication commands: /mail."""

import discord
from discord import app_commands
from discord.ext import commands

from agentmail import send_agent_mail
from approvals import is_emergency_stopped
from audit import audit_log

from ._helpers import require_auth


def _register_comms_commands(bot: commands.Bot) -> None:
    """Register /mail."""

    @bot.tree.command(name="mail", description="Send an automated e-mail message via AgentMail")
    @app_commands.describe(to="Recipient email", subject="Email subject", body="Message body")
    @require_auth
    async def mail_cmd(interaction: discord.Interaction, to: str, subject: str, body: str):
        if is_emergency_stopped():
            await interaction.response.send_message("🛑 Emergency stop active.", ephemeral=True)
            return
        await interaction.response.defer()
        result = await send_agent_mail(to, subject, body)
        await interaction.followup.send(result)
        audit_log(interaction.user, "mail", detail=f"to={to} subj={subject}")
