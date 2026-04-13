"""Feedback summary slash command: /feedback-summary."""

from __future__ import annotations

import json
import os
from collections import defaultdict

import discord
from discord.ext import commands

from ._helpers import require_auth

_MAX_DISPLAY = 200


def _load_feedback(path: str) -> list[dict]:
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return records[-_MAX_DISPLAY:]


def _register_feedback_commands(bot: commands.Bot) -> None:
    """Register /feedback-summary."""

    @bot.tree.command(name="feedback-summary", description="Show thumbs-up/down stats from the feedback log")
    @require_auth
    async def feedback_summary_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            log_path = os.getenv("FEEDBACK_LOG", "data/feedback.jsonl")
            records = _load_feedback(log_path)

            if not records:
                embed = discord.Embed(
                    title="📊 Feedback Summary",
                    description="No feedback has been recorded yet.",
                    color=discord.Color.greyple(),
                )
                await interaction.followup.send(embed=embed)
                return

            total = len(records)
            thumbs_up = sum(1 for r in records if r.get("rating") == 1)
            thumbs_down = total - thumbs_up
            pct = thumbs_up / total * 100 if total else 0.0

            # Per-provider stats
            provider_up: dict[str, int] = defaultdict(int)
            provider_total: dict[str, int] = defaultdict(int)
            for r in records:
                p = r.get("provider") or "unknown"
                provider_total[p] += 1
                if r.get("rating") == 1:
                    provider_up[p] += 1

            provider_lines = [
                f"`{p}`: {provider_up[p] / provider_total[p] * 100:.1f}% ({provider_total[p]} ratings)"
                for p in sorted(provider_total)
            ]

            # Per-skill stats
            skill_up: dict[str, int] = defaultdict(int)
            skill_total: dict[str, int] = defaultdict(int)
            for r in records:
                for skill in r.get("skills") or []:
                    skill_total[skill] += 1
                    if r.get("rating") == 1:
                        skill_up[skill] += 1

            skill_lines = [
                f"`{s}`: {skill_up[s] / skill_total[s] * 100:.1f}% ({skill_total[s]} ratings)"
                for s in sorted(skill_total)
            ]

            embed = discord.Embed(
                title=f"📊 Feedback Summary (last {total} ratings)",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="Overall",
                value=f"👍 {thumbs_up}  👎 {thumbs_down}  ({pct:.1f}% positive)",
                inline=False,
            )
            embed.add_field(
                name="By Provider",
                value="\n".join(provider_lines) if provider_lines else "—",
                inline=False,
            )
            embed.add_field(
                name="By Skill",
                value="\n".join(skill_lines) if skill_lines else "—",
                inline=False,
            )

            await interaction.followup.send(embed=embed)

        except Exception as exc:
            embed = discord.Embed(
                title="❌ Feedback summary failed",
                description=str(exc),
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
