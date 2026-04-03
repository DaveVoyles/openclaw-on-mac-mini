"""Monitoring commands: /health-trend, /audit-export."""

import csv
import io
import json
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log

from ._helpers import require_auth

log = logging.getLogger("openclaw")


def _register_monitoring_commands(bot: commands.Bot) -> None:
    """Register /health-trend and /audit-export."""

    # ------------------------------------------------------------------
    # /health-trend
    # ------------------------------------------------------------------

    @bot.tree.command(name="health-trend", description="Show health trend for a service over time")
    @app_commands.describe(service="Service name (e.g. sonarr, radarr, plex)", days="Number of days to look back (default 7)")
    @require_auth
    async def health_trend_cmd(interaction: discord.Interaction, service: str, days: int = 7):
        await interaction.response.defer(ephemeral=True)
        try:
            from health_history import get_trend
            trend = get_trend(service, days)
        except Exception as exc:
            await interaction.followup.send(f"❌ Could not fetch health trend: {exc}", ephemeral=True)
            return

        color = 0x2ecc71 if trend["uptime_pct"] > 95 else (0xf39c12 if trend["uptime_pct"] > 80 else 0xe74c3c)
        embed = discord.Embed(
            title=f"📊 {service} — {days}d Health Trend",
            color=color,
        )
        embed.add_field(name="Uptime", value=f"{trend['uptime_pct']}%", inline=True)
        embed.add_field(name="Total Checks", value=str(trend["total_checks"]), inline=True)
        embed.add_field(name="Sparkline", value=f"`{trend['sparkline']}`", inline=False)

        if trend["status_counts"]:
            counts_text = " · ".join(f"{k}: {v}" for k, v in trend["status_counts"].items())
            embed.add_field(name="Status Breakdown", value=counts_text, inline=False)

        if trend["recent_incidents"]:
            incidents_text = "\n".join(f"• {s}: {m}" for s, m, _ in trend["recent_incidents"][:5])
            embed.add_field(name="Recent Incidents", value=incidents_text[:1024], inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)
        audit_log(interaction.user, "health_trend", detail=f"{service} days={days}")

    # ------------------------------------------------------------------
    # /audit-export
    # ------------------------------------------------------------------

    @bot.tree.command(name="audit-export", description="Export audit log as a downloadable CSV file")
    @app_commands.describe(days="Number of days to export (default 7)")
    @require_auth
    async def audit_export_cmd(interaction: discord.Interaction, days: int = 7):
        await interaction.response.defer(ephemeral=True)

        audit_dir = Path(os.getenv("AUDIT_DIR", "/audit"))
        entries: list[dict] = []
        cutoff = time.time() - (days * 86400)

        for jsonl_file in sorted(audit_dir.glob("*.jsonl")):
            for line in jsonl_file.read_text().splitlines():
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("ts", "")
                    if ts_str:
                        import datetime as _dt
                        try:
                            ts_epoch = _dt.datetime.fromisoformat(ts_str).timestamp()
                        except (ValueError, TypeError):
                            ts_epoch = 0
                    else:
                        ts_epoch = 0
                    if ts_epoch >= cutoff:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue

        if not entries:
            await interaction.followup.send("No audit entries found for the specified period.", ephemeral=True)
            return

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["ts", "user", "action", "detail", "result"])
        writer.writeheader()
        for e in entries:
            writer.writerow({
                "ts": e.get("ts", ""),
                "user": e.get("user", ""),
                "action": e.get("action", ""),
                "detail": str(e.get("detail", ""))[:200],
                "result": e.get("result", ""),
            })

        file = discord.File(io.BytesIO(buf.getvalue().encode()), filename=f"audit_{days}d.csv")
        await interaction.followup.send(
            f"📋 Audit log ({len(entries)} entries, last {days} days)", file=file, ephemeral=True
        )
        audit_log(interaction.user, "audit_export", detail=f"days={days} entries={len(entries)}")
