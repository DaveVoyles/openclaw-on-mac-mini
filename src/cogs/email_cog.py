"""
Email Cog — read and send email from Discord.

Commands:
  /email inbox   — view recent inbox messages
  /email search  — search inbox by keyword
  /email read    — fetch full body of a single email by IMAP ID
  /email send    — send an email (auth-gated)
"""

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import require_auth, truncate_for_embed
from discord_error import build_error_embed

log = logging.getLogger("openclaw.email_cog")

_PROVIDER_CHOICES = [
    app_commands.Choice(name="Gmail", value="gmail"),
    app_commands.Choice(name="Outlook", value="outlook"),
]


class EmailCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    email = app_commands.Group(name="email", description="Read and send email via Gmail or Outlook")

    # ── /email inbox ──────────────────────────────────────────────────────

    @email.command(name="inbox", description="View recent inbox messages")
    @app_commands.describe(
        count="Number of messages to fetch (1–50, default 10)",
        provider="Email provider (default: gmail)",
    )
    @app_commands.choices(provider=_PROVIDER_CHOICES)
    async def email_inbox(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 50] = 10,
        provider: str = "gmail",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from email_skills import read_inbox

            result = await read_inbox(provider=provider, count=count)
            embed = discord.Embed(
                title=f"📬 Inbox ({provider})",
                description=truncate_for_embed(result),
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("email inbox failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/email inbox"), ephemeral=True)

    # ── /email search ─────────────────────────────────────────────────────

    @email.command(name="search", description="Search inbox by keyword")
    @app_commands.describe(
        query="Search term",
        provider="Email provider (default: gmail)",
    )
    @app_commands.choices(provider=_PROVIDER_CHOICES)
    async def email_search(
        self,
        interaction: discord.Interaction,
        query: str,
        provider: str = "gmail",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from email_skills import search_emails

            result = await search_emails(query=query, provider=provider)
            embed = discord.Embed(
                title=f"🔍 Email Search ({provider}): {query}",
                description=truncate_for_embed(result),
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("email search failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/email search"), ephemeral=True)

    # ── /email read ───────────────────────────────────────────────────────

    @email.command(name="read", description="Fetch the full body of an email by its IMAP ID")
    @app_commands.describe(
        id="Email ID shown in /email inbox",
        provider="Email provider (default: gmail)",
    )
    @app_commands.choices(provider=_PROVIDER_CHOICES)
    async def email_read(
        self,
        interaction: discord.Interaction,
        id: str,
        provider: str = "gmail",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from email_skills import read_email_by_id

            result = await read_email_by_id(msg_id=id, provider=provider)
            if len(result) > 3000:
                file = discord.File(
                    io.BytesIO(result.encode()), filename=f"email_{id}.txt"
                )
                await interaction.followup.send(
                    f"📧 Email {id} (full content attached)", file=file, ephemeral=True
                )
            else:
                embed = discord.Embed(
                    title=f"📧 Email ID {id} ({provider})",
                    description=truncate_for_embed(result),
                    color=discord.Color.blue(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("email read failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/email read"), ephemeral=True)

    # ── /email send ───────────────────────────────────────────────────────

    @email.command(name="send", description="Send an email (requires authorization)")
    @app_commands.describe(
        to="Recipient email address",
        subject="Email subject",
        body="Email body",
        provider="Email provider (default: gmail)",
    )
    @require_auth()
    async def email_send(
        self,
        interaction: discord.Interaction,
        to: str,
        subject: str,
        body: str,
        provider: str = "gmail",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            from email_skills import send_email

            result = await send_email(to=to, subject=subject, body=body, provider=provider)
            embed = discord.Embed(
                title="✉️ Email Sent",
                description=result,
                color=discord.Color.green(),
            )
            embed.add_field(name="To", value=to, inline=True)
            embed.add_field(name="Subject", value=subject, inline=True)
            embed.add_field(name="Provider", value=provider.title(), inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional
            log.exception("email send failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/email send"), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(EmailCog(bot))
