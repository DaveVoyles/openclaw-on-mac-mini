"""
DNS Cog — AdGuard Home DNS management from Discord.

Commands:
  /dns status  — show AdGuard status, version, and filtering info
  /dns stats   — query/block counts, top domains
  /dns block   — rewrite a domain to 0.0.0.0  [auth required]
  /dns allow   — remove a rewrite rule          [auth required]
  /dns blocked — list all custom rewrite rules
"""

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import require_auth, truncate_for_embed
from config import cfg

log = logging.getLogger("openclaw")


async def _ag_request(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated request to AdGuard Home."""
    from aiohttp import BasicAuth

    auth = BasicAuth(cfg.adguard_user, cfg.adguard_password)
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.request(method, f"{cfg.adguard_url}{path}", **kwargs) as r:
            r.raise_for_status()
            if r.content_type == "application/json":
                return await r.json()
            return {"text": await r.text()}


class DnsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    dns = app_commands.Group(name="dns", description="AdGuard Home DNS management")

    # ── /dns status ───────────────────────────────────────────────────────────

    @dns.command(name="status", description="Show AdGuard Home status and filtering info")
    async def dns_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _ag_request("GET", "/control/status")
            running: bool = data.get("running", False)
            filtering = data.get("filtering", {})

            color = discord.Color.green() if running else discord.Color.red()
            status_str = "🟢 Running" if running else "🔴 Stopped"
            filtering_str = "✅ Enabled" if filtering.get("enabled") else "❌ Disabled"

            dns_addrs = data.get("dns_addresses", [])
            addrs_str = ", ".join(f"`{a}`" for a in dns_addrs) if dns_addrs else "—"

            embed = discord.Embed(
                title="🛡️ AdGuard Home Status",
                color=color,
            )
            embed.add_field(name="Status", value=status_str, inline=True)
            embed.add_field(name="Version", value=f"`{data.get('version', '?')}`", inline=True)
            embed.add_field(name="Filtering", value=filtering_str, inline=True)
            embed.add_field(
                name="Rules Count",
                value=f"{filtering.get('rules_count', 0):,}",
                inline=True,
            )
            embed.add_field(name="DNS Addresses", value=addrs_str, inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception("dns status failed")
            await interaction.followup.send("❌ Failed to fetch AdGuard status.", ephemeral=True)

    # ── /dns stats ────────────────────────────────────────────────────────────

    @dns.command(name="stats", description="Show DNS query statistics from AdGuard Home")
    async def dns_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _ag_request("GET", "/control/stats")

            total: int = data.get("num_dns_queries", 0)
            blocked: int = data.get("num_blocked_filtering", 0)
            avg_ms: float = data.get("avg_processing_time", 0.0) * 1000
            block_pct = (blocked / total * 100) if total else 0.0

            top_queried = data.get("top_queried_domains", [])[:5]
            top_blocked = data.get("top_blocked_domains", [])[:5]

            def _fmt_top(entries: list) -> str:
                if not entries:
                    return "—"
                lines = []
                for entry in entries:
                    for domain, count in entry.items():
                        lines.append(f"`{domain}` — {count:,}")
                return "\n".join(lines)

            embed = discord.Embed(title="📊 AdGuard Home Stats", color=discord.Color.blue())
            embed.add_field(name="Total Queries", value=f"{total:,}", inline=True)
            embed.add_field(name="Blocked", value=f"{blocked:,} ({block_pct:.1f}%)", inline=True)
            embed.add_field(name="Avg Response", value=f"{avg_ms:.2f} ms", inline=True)
            embed.add_field(
                name="🔝 Top Queried (5)",
                value=truncate_for_embed(_fmt_top(top_queried), 1000),
                inline=True,
            )
            embed.add_field(
                name="🚫 Top Blocked (5)",
                value=truncate_for_embed(_fmt_top(top_blocked), 1000),
                inline=True,
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            log.exception("dns stats failed")
            await interaction.followup.send("❌ Failed to fetch AdGuard stats.", ephemeral=True)

    # ── /dns block ────────────────────────────────────────────────────────────

    @dns.command(name="block", description="Block a domain by rewriting it to 0.0.0.0")
    @app_commands.describe(domain="Domain to block, e.g. ads.example.com")
    @require_auth()
    async def dns_block(self, interaction: discord.Interaction, domain: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await _ag_request(
                "POST",
                "/control/rewrite/add",
                json={"domain": domain, "answer": "0.0.0.0"},
            )
            await interaction.followup.send(
                f"🚫 `{domain}` is now blocked (rewrites to 0.0.0.0)", ephemeral=True
            )
        except Exception:
            log.exception("dns block failed for %s", domain)
            await interaction.followup.send(f"❌ Failed to block `{domain}`.", ephemeral=True)

    # ── /dns allow ────────────────────────────────────────────────────────────

    @dns.command(name="allow", description="Unblock a previously blocked domain")
    @app_commands.describe(domain="Domain to unblock, e.g. ads.example.com")
    @require_auth()
    async def dns_allow(self, interaction: discord.Interaction, domain: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await _ag_request(
                "POST",
                "/control/rewrite/delete",
                json={"domain": domain, "answer": "0.0.0.0"},
            )
            await interaction.followup.send(f"✅ `{domain}` unblocked", ephemeral=True)
        except Exception:
            log.exception("dns allow failed for %s", domain)
            await interaction.followup.send(f"❌ Failed to unblock `{domain}`.", ephemeral=True)

    # ── /dns blocked ──────────────────────────────────────────────────────────

    @dns.command(name="blocked", description="List all custom rewrite rules (blocked domains)")
    async def dns_blocked(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            rewrites: list = await _ag_request("GET", "/control/rewrite/list")

            if not rewrites:
                await interaction.followup.send(
                    "📋 No custom rewrite rules found.", ephemeral=True
                )
                return

            PAGE_SIZE = 15
            pages = [rewrites[i : i + PAGE_SIZE] for i in range(0, len(rewrites), PAGE_SIZE)]
            embeds: list[discord.Embed] = []

            for idx, page in enumerate(pages):
                lines = [
                    f"`{r.get('domain', '?')}` → `{r.get('answer', '?')}`" for r in page
                ]
                embed = discord.Embed(
                    title=f"📋 Blocked Domains ({len(rewrites)} total)",
                    description=truncate_for_embed("\n".join(lines)),
                    color=discord.Color.orange(),
                )
                if len(pages) > 1:
                    embed.set_footer(text=f"Page {idx + 1}/{len(pages)}")
                embeds.append(embed)

            # Send first page; additional pages as follow-ups
            await interaction.followup.send(embed=embeds[0], ephemeral=True)
            for extra in embeds[1:]:
                await interaction.followup.send(embed=extra, ephemeral=True)

        except Exception:
            log.exception("dns blocked failed")
            await interaction.followup.send(
                "❌ Failed to fetch rewrite rules.", ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(DnsCog(bot))
