"""
Journal Cog — daily journaling from Discord, saved to the Obsidian vault.

Commands:
  /journal write  — save a journal entry (inline or via modal)
  /journal read   — read a past journal entry by date
  /journal streak — show consecutive days journaled
  /journal prompt — get an AI-generated journaling prompt
"""

import io
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from discord_error import build_error_embed

log = logging.getLogger("openclaw.journal_cog")

VAULT_DIR = Path(os.getenv("VAULT_DIR", "/vault"))


def _parse_date(date_str: str) -> date:
    """Parse 'today', 'yesterday', or an ISO date string into a date object."""
    s = date_str.strip().lower()
    if s == "today":
        return date.today()
    if s == "yesterday":
        return date.today() - timedelta(days=1)
    # Try common formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    # Fallback: dateutil if available
    try:
        from dateutil import parser as dateutil_parser
        return dateutil_parser.parse(date_str).date()
    except (ImportError, ValueError) as e:
        raise ValueError(f"Cannot parse date: {date_str!r}") from e


def _journal_title(d: date) -> str:
    return f"Journal - {d.isoformat()}"


def _find_journal_file(d: date) -> Path | None:
    """Search vault/Journal/ for a file whose frontmatter title matches the date."""
    journal_dir = VAULT_DIR / "Journal"
    if not journal_dir.exists():
        return None
    target_title = _journal_title(d)
    for f in journal_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(rf'^title:\s*["\']?{re.escape(target_title)}["\']?\s*$', text, re.MULTILINE):
                return f
        except OSError:
            continue
    return None


async def _save_journal_entry(entry: str, d: date) -> str:
    from obsidian_writer import save_to_vault

    title = _journal_title(d)
    weekday = d.strftime("%A").lower()
    return await save_to_vault(
        title=title,
        content=entry,
        content_type="journal",
        tags=["journal", weekday],
        source_url="",
    )


# ── Modal ──────────────────────────────────────────────────────────────────────

class JournalEntryModal(discord.ui.Modal, title="📓 Journal Entry"):
    entry = discord.ui.TextInput(
        label="What's on your mind?",
        style=discord.TextStyle.paragraph,
        placeholder="Write your journal entry here...",
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            today = date.today()
            result = await _save_journal_entry(str(self.entry), today)
            embed = discord.Embed(
                title="📓 Journal Saved",
                description=result,
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"Entry for {today.isoformat()}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional — Discord modal handler must not crash
            log.exception("journal modal save failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/journal write"), ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class JournalCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    journal = app_commands.Group(name="journal", description="Daily journaling to Obsidian vault")

    # ── /journal write ────────────────────────────────────────────────────────

    @journal.command(name="write", description="Save a journal entry for today")
    @app_commands.describe(entry="Your journal entry (leave blank for a text input modal)")
    async def journal_write(self, interaction: discord.Interaction, entry: str = "") -> None:
        if not entry:
            await interaction.response.send_modal(JournalEntryModal())
            return

        await interaction.response.defer(ephemeral=True)
        try:
            today = date.today()
            result = await _save_journal_entry(entry, today)
            embed = discord.Embed(
                title="📓 Journal Saved",
                description=result,
                color=discord.Color.green(),
            )
            embed.set_footer(text=f"Entry for {today.isoformat()}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional — Discord command handler must not crash
            log.exception("journal write failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/journal write"), ephemeral=True)

    # ── /journal read ─────────────────────────────────────────────────────────

    @journal.command(name="read", description="Read a past journal entry")
    @app_commands.describe(date="Date to read: 'today', 'yesterday', or YYYY-MM-DD (default: today)")
    async def journal_read(self, interaction: discord.Interaction, date: str = "today") -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            target = _parse_date(date)
        except ValueError as e:
            await interaction.followup.send(embed=build_error_embed(e, context="/journal read"), ephemeral=True)
            return

        try:
            found = _find_journal_file(target)
            if not found:
                await interaction.followup.send(
                    f"No journal entry for **{target.isoformat()}**. "
                    "Use `/journal write` to create one.",
                    ephemeral=True,
                )
                return

            content = found.read_text(encoding="utf-8", errors="replace")
            if len(content) > 3000:
                file = discord.File(io.BytesIO(content.encode()), filename=found.name)
                await interaction.followup.send(
                    f"📓 Journal — {target.isoformat()} (attached as file)",
                    file=file,
                    ephemeral=True,
                )
            else:
                embed = discord.Embed(
                    title=f"📓 {_journal_title(target)}",
                    description=content,
                    color=discord.Color.blurple(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional — Discord command handler must not crash
            log.exception("journal read failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/journal read"), ephemeral=True)

    # ── /journal streak ───────────────────────────────────────────────────────

    @journal.command(name="streak", description="Show your consecutive journaling streak")
    async def journal_streak(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            streak = 0
            last_entry: date | None = None
            check = date.today()

            while _find_journal_file(check) is not None:
                if last_entry is None:
                    last_entry = check
                streak += 1
                check -= timedelta(days=1)

            if streak == 0:
                embed = discord.Embed(
                    title="📓 Journal Streak",
                    description="No journal entries yet. Start with `/journal write`!",
                    color=discord.Color.orange(),
                )
            else:
                embed = discord.Embed(
                    title="📓 Journal Streak",
                    color=discord.Color.gold(),
                )
                embed.add_field(name="🔥 Current Streak", value=f"**{streak} day{'s' if streak != 1 else ''}**", inline=True)
                embed.add_field(name="📅 Last Entry", value=last_entry.isoformat() if last_entry else "—", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional — Discord command handler must not crash
            log.exception("journal streak failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/journal streak"), ephemeral=True)

    # ── /journal prompt ───────────────────────────────────────────────────────

    @journal.command(name="prompt", description="Get an AI-generated journaling prompt")
    async def journal_prompt(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            from llm.chat import chat

            prompt_text = await chat(
                "Generate a thoughtful, open-ended daily journal prompt. "
                "Keep it to 1-2 sentences. Be creative and varied — cover themes like "
                "reflection, gratitude, goals, creativity, or relationships.",
                model_preference="auto",
            )
            embed = discord.Embed(
                title="✍️ Journal Prompt",
                description=prompt_text,
                color=discord.Color.purple(),
            )
            embed.set_footer(text="Use /journal write to respond to this prompt")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:  # broad: intentional — Discord command handler must not crash
            log.exception("journal prompt failed")
            await interaction.followup.send(embed=build_error_embed(e, context="/journal prompt"), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(JournalCog(bot))
