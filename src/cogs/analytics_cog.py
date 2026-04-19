"""
Analytics Cog — extracted from bot.py
Handles: /spending, /auditlog, /audit-summary
"""

import collections
import datetime
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log
from spending import tracker as spending_tracker

log = logging.getLogger(__name__)

AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/audit"))


class AnalyticsCog(commands.Cog, name="Analytics"):
    """Spending and audit log commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        msg = f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="spending", description="View Gemini API spending and budget status")
    @app_commands.describe(breakdown="Show daily breakdown (default: summary)")
    async def spending_cmd(self, interaction: discord.Interaction, breakdown: bool = False) -> None:
        if breakdown:
            text = spending_tracker.daily_breakdown()
        else:
            text = spending_tracker.summary()
        embed = discord.Embed(
            title="💰 Gemini API Spending",
            description=text,
            color=discord.Color.green() if not spending_tracker.is_over_budget else discord.Color.red(),
        )
        embed.set_footer(text=f"Model: gemini-1.5-flash | Tier 1 | Budget: ${spending_tracker.budget_limit:.2f}")
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "spending")

    @app_commands.command(name="auditlog", description="View recent audit log entries")
    @app_commands.describe(lines="Number of entries to show (default 10, max 25)")
    async def auditlog_cmd(self, interaction: discord.Interaction, lines: int = 10) -> None:
        lines = min(max(lines, 1), 25)
        today = datetime.date.today().isoformat()
        audit_file = AUDIT_DIR / f"{today}.jsonl"

        if not audit_file.exists():
            await interaction.response.send_message("No audit entries for today.", ephemeral=True)
            return

        all_lines = audit_file.read_text().strip().split("\n")
        recent = all_lines[-lines:]

        formatted = []
        for line in recent:
            try:
                entry = json.loads(line)
                ts = entry.get("ts", "")[:19].replace("T", " ")
                user = entry.get("user", "?")
                action = entry.get("action", "?")
                detail = entry.get("detail", "")
                result = entry.get("result", "")
                formatted.append(f"`{ts}` **{action}** {detail} [{result}] — {user}")
            except json.JSONDecodeError:
                continue

        embed = discord.Embed(
            title=f"📋 Audit Log (last {len(formatted)})",
            description="\n".join(formatted) or "No entries.",
            color=discord.Color.light_grey(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "auditlog", detail=f"lines={lines}")

    @app_commands.command(name="audit-summary", description="Analytics summary of today's audit log")
    async def audit_summary_cmd(self, interaction: discord.Interaction) -> None:
        today = datetime.date.today().isoformat()
        audit_file = AUDIT_DIR / f"{today}.jsonl"
        if not audit_file.exists():
            await interaction.response.send_message("No audit entries for today yet.", ephemeral=True)
            return

        entries: list[dict] = []
        for line in audit_file.read_text().strip().splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not entries:
            await interaction.response.send_message("No parseable audit entries for today.", ephemeral=True)
            return

        action_counts: dict[str, int] = collections.Counter(e.get("action", "?") for e in entries)
        error_entries = [e for e in entries if e.get("result", "success") not in ("success", "")]
        hour_counts: dict[int, int] = collections.Counter(
            int(e.get("ts", "T00")[11:13]) for e in entries if len(e.get("ts", "")) >= 13
        )

        top_actions = "\n".join(f"  `{action}` — {count}x" for action, count in action_counts.most_common(10))
        top_hours = ", ".join(f"{h:02d}:xx ({c})" for h, c in sorted(hour_counts.items(), key=lambda x: -x[1])[:5])
        errors_text = (
            "\n".join(
                f"  `{e.get('ts', '')[:19]}` {e.get('action', '?')} → {e.get('result', '?')}" for e in error_entries[:5]
            )
            or "  None"
        )

        embed = discord.Embed(
            title=f"📊 Audit Summary — {today}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name=f"Total actions ({len(entries)})", value=(top_actions or "—")[:1024], inline=False)
        embed.add_field(name="Most active hours", value=(top_hours or "—")[:1024], inline=False)
        embed.add_field(name=f"Non-success results ({len(error_entries)})", value=(errors_text)[:1024], inline=False)
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "audit-summary")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyticsCog(bot))
