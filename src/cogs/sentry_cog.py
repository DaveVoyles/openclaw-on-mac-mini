"""
Sentry Cog — Sentry error monitoring from Discord.

Commands:
  /sentry issues   — list unresolved issues (org-wide or per project)
  /sentry projects — list all projects in the org
  /sentry resolve  — mark an issue as resolved
  /sentry stats    — hourly error rate for a project (last 24h)
"""

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import require_auth, truncate_for_embed
from config import cfg

log = logging.getLogger("openclaw")

_LEVEL_EMOJI = {"error": "🔴", "warning": "🟡", "info": "🔵", "fatal": "💀"}
_SETUP_MSG = (
    "⚙️ **Sentry not configured.**\n"
    "Set the following in your `.env`:\n"
    "```\nSENTRY_AUTH_TOKEN=<your token>\nSENTRY_ORG=<your org slug>\n```\n"
    "Create a token at <https://sentry.io/settings/account/api/auth-tokens/>"
)


async def _sentry(method: str, path: str, **kwargs) -> Any:
    headers = {
        "Authorization": f"Bearer {cfg.sentry_auth_token}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.request(
            method, f"{cfg.sentry_url}/api/0/{path}", **kwargs
        ) as r:
            r.raise_for_status()
            return await r.json()


def _fmt_dt(iso: str) -> str:
    """Return a compact human-readable timestamp from an ISO-8601 string."""
    try:
        dt = datetime.fromisoformat(iso.rstrip("Z")).replace(tzinfo=timezone.utc)
        return discord.utils.format_dt(dt, style="R")
    except (ValueError, TypeError):
        return iso


class SentryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    sentry = app_commands.Group(name="sentry", description="Sentry error monitoring")

    # ── /sentry issues ────────────────────────────────────────────────────

    @sentry.command(name="issues", description="List unresolved Sentry issues")
    @app_commands.describe(project="Project slug to filter by (optional)")
    async def sentry_issues(
        self,
        interaction: discord.Interaction,
        project: str = "",
    ):
        await interaction.response.defer(ephemeral=True)
        if not cfg.sentry_auth_token:
            await interaction.followup.send(_SETUP_MSG, ephemeral=True)
            return
        try:
            if project:
                data = await _sentry(
                    "GET",
                    f"projects/{cfg.sentry_org}/{project}/issues/",
                    params={"query": "is:unresolved", "limit": 10},
                )
            else:
                data = await _sentry(
                    "GET",
                    f"organizations/{cfg.sentry_org}/issues/",
                    params={"query": "is:unresolved", "limit": 10},
                )

            if not data:
                await interaction.followup.send(
                    "✅ No unresolved issues found.", ephemeral=True
                )
                return

            scope = f"`{project}`" if project else "org-wide"
            embed = discord.Embed(
                title=f"🐛 Unresolved Issues ({scope})",
                color=discord.Color.red(),
            )
            lines = []
            for issue in data[:10]:
                emoji = _LEVEL_EMOJI.get(issue.get("level", ""), "⚪")
                title = truncate_for_embed(issue.get("title", "Untitled"), 80)
                count = issue.get("count", "?")
                last_seen = _fmt_dt(issue.get("lastSeen", ""))
                issue_id = issue.get("id", "")
                lines.append(
                    f"{emoji} **{title}**\n"
                    f"  ID: `{issue_id}` · {count} events · last seen {last_seen}"
                )

            embed.description = truncate_for_embed("\n\n".join(lines))
            embed.set_footer(text=f"Sentry · {cfg.sentry_org}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:  # broad: intentional
            log.exception("sentry issues failed")
            await interaction.followup.send(
                "❌ Failed to fetch Sentry issues.", ephemeral=True
            )

    # ── /sentry projects ──────────────────────────────────────────────────

    @sentry.command(name="projects", description="List all Sentry projects in the org")
    async def sentry_projects(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not cfg.sentry_auth_token:
            await interaction.followup.send(_SETUP_MSG, ephemeral=True)
            return
        try:
            data = await _sentry(
                "GET", f"organizations/{cfg.sentry_org}/projects/"
            )

            if not data:
                await interaction.followup.send(
                    "No projects found in this org.", ephemeral=True
                )
                return

            embed = discord.Embed(
                title=f"📦 Sentry Projects — {cfg.sentry_org}",
                color=discord.Color.blurple(),
            )
            lines = []
            for proj in data:
                name = proj.get("name", "Unknown")
                slug = proj.get("slug", "")
                platform = proj.get("platform") or "unknown"
                lines.append(f"• **{name}** (`{slug}`) — {platform}")

            embed.description = truncate_for_embed("\n".join(lines))
            embed.set_footer(text=f"{len(data)} project(s) · Sentry")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:  # broad: intentional
            log.exception("sentry projects failed")
            await interaction.followup.send(
                "❌ Failed to fetch Sentry projects.", ephemeral=True
            )

    # ── /sentry resolve ───────────────────────────────────────────────────

    @sentry.command(name="resolve", description="Mark a Sentry issue as resolved")
    @app_commands.describe(issue_id="Numeric Sentry issue ID")
    @require_auth()
    async def sentry_resolve(self, interaction: discord.Interaction, issue_id: str):
        await interaction.response.defer(ephemeral=True)
        if not cfg.sentry_auth_token:
            await interaction.followup.send(_SETUP_MSG, ephemeral=True)
            return
        try:
            # The bulk update endpoint accepts ?id= with a body payload
            await _sentry(
                "PUT",
                f"organizations/{cfg.sentry_org}/issues/",
                params={"id": issue_id},
                json={"status": "resolved"},
            )
            await interaction.followup.send(
                f"✅ Issue `#{issue_id}` marked as resolved.", ephemeral=True
            )
        except Exception:  # broad: intentional
            log.exception("sentry resolve failed")
            await interaction.followup.send(
                f"❌ Failed to resolve issue `#{issue_id}`.", ephemeral=True
            )

    # ── /sentry stats ─────────────────────────────────────────────────────

    @sentry.command(
        name="stats", description="Hourly error rate for a project (last 24h)"
    )
    @app_commands.describe(project="Project slug")
    async def sentry_stats(
        self,
        interaction: discord.Interaction,
        project: str = "",
    ):
        await interaction.response.defer(ephemeral=True)
        if not cfg.sentry_auth_token:
            await interaction.followup.send(_SETUP_MSG, ephemeral=True)
            return
        try:
            if not project:
                # Fall back to first available project
                projects = await _sentry(
                    "GET", f"organizations/{cfg.sentry_org}/projects/"
                )
                if not projects:
                    await interaction.followup.send(
                        "No projects found in this org.", ephemeral=True
                    )
                    return
                project = projects[0]["slug"]

            data = await _sentry(
                "GET",
                f"projects/{cfg.sentry_org}/{project}/stats/",
                params={"stat": "received", "resolution": "1h"},
            )

            if not data:
                await interaction.followup.send(
                    f"No stats available for `{project}`.", ephemeral=True
                )
                return

            # data is a list of [timestamp_seconds, count] pairs
            recent = data[-24:] if len(data) >= 24 else data
            total = sum(row[1] for row in recent)

            bars = []
            max_count = max((row[1] for row in recent), default=1) or 1
            for row in recent[-12:]:  # show last 12 h for readability
                ts, count = row[0], row[1]
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                hour_label = dt.strftime("%H:00")
                filled = round((count / max_count) * 8)
                bar = "█" * filled + "░" * (8 - filled)
                bars.append(f"`{hour_label}` {bar} {count}")

            embed = discord.Embed(
                title=f"📊 Error Stats — `{project}` (last 24h)",
                description=truncate_for_embed("\n".join(bars)),
                color=discord.Color.orange(),
            )
            embed.add_field(name="Total received", value=str(total))
            embed.set_footer(text=f"Sentry · {cfg.sentry_org}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:  # broad: intentional
            log.exception("sentry stats failed")
            await interaction.followup.send(
                "❌ Failed to fetch Sentry stats.", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(SentryCog(bot))
