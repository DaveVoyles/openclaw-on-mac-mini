"""RSS feed commands — Discord slash-command wrapper for rss_skills.

Provides /rss list, /rss fetch, /rss search, /rss digest as a command group.
"""

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, truncate_for_embed

log = logging.getLogger("openclaw")


class RSSCog(commands.GroupCog, group_name="rss", group_description="RSS feed monitoring"):
    """RSS feed monitoring commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error)
        else:
            msg = f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ── /rss list ─────────────────────────────────────────────────────
    @app_commands.command(name="list", description="Show all configured/saved RSS feeds")
    @require_auth()
    async def rss_list_cmd(self, interaction: discord.Interaction) -> None:
        from rss_skills import list_rss_feeds

        result = await list_rss_feeds()
        embed = discord.Embed(
            title="📡 Saved RSS Feeds",
            description=truncate_for_embed(result),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "rss_list")

    # ── /rss fetch ────────────────────────────────────────────────────
    @app_commands.command(name="fetch", description="Fetch latest items from an RSS/Atom feed")
    @app_commands.describe(
        url="Full URL of the RSS/Atom feed",
        limit="Number of items to return (1-20, default 10)",
    )
    @require_auth()
    async def rss_fetch_cmd(self, interaction: discord.Interaction, url: str, limit: int = 10) -> None:
        from rss_skills import fetch_rss_feed

        await interaction.response.defer()
        result = await fetch_rss_feed(url, limit=limit)

        if result.startswith("❌") or result.startswith("⚠️"):
            embed = discord.Embed(
                title="RSS Fetch",
                description=result,
                color=discord.Color.red(),
            )
        else:
            embed = discord.Embed(
                title=f"📰 {result.splitlines()[0] if result.splitlines() else 'RSS Feed'}",
                description=truncate_for_embed("\n".join(result.splitlines()[1:])),
                color=discord.Color.orange(),
            )
            embed.set_footer(text=f"Feed: {url[:120]}")

        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "rss_fetch", detail=url)

    # ── /rss search ───────────────────────────────────────────────────
    @app_commands.command(name="search", description="Search within an RSS/Atom feed by keyword")
    @app_commands.describe(
        url="Full URL of the RSS/Atom feed",
        query="Keyword(s) to search for in titles and summaries",
    )
    @require_auth()
    async def rss_search_cmd(self, interaction: discord.Interaction, url: str, query: str) -> None:
        from rss_skills import search_rss

        await interaction.response.defer()
        result = await search_rss(url, query)

        if result.startswith("❌"):
            color = discord.Color.red()
        elif result.startswith("🔍 No items"):
            color = discord.Color.greyple()
        else:
            color = discord.Color.teal()

        embed = discord.Embed(
            title=f"🔍 RSS Search: {query[:80]}",
            description=truncate_for_embed(result),
            color=color,
        )
        embed.set_footer(text=f"Feed: {url[:120]}")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "rss_search", detail=f"{query} in {url}")

    # ── /rss digest ───────────────────────────────────────────────────
    @app_commands.command(name="digest", description="Fetch all saved feeds and produce an LLM-summarized digest")
    @app_commands.describe(
        topic="Optional focus topic — only include articles related to this",
    )
    @require_auth()
    async def rss_digest_cmd(self, interaction: discord.Interaction, topic: str = "") -> None:
        from rss_skills import _load_feeds, get_rss_digest

        await interaction.response.defer()

        feeds = _load_feeds()
        if not feeds:
            embed = discord.Embed(
                title="📰 RSS Digest",
                description="No feeds saved yet. Use `/rss fetch <url>` to subscribe to a feed first.",
                color=discord.Color.greyple(),
            )
            await interaction.followup.send(embed=embed)
            return

        urls = [f["url"] for f in feeds]
        result = await get_rss_digest(json.dumps(urls), topic=topic)

        if result.startswith("❌"):
            color = discord.Color.red()
        else:
            color = discord.Color.gold()

        title = "📰 RSS Digest"
        if topic:
            title += f" — {topic[:60]}"

        embed = discord.Embed(
            title=title,
            description=truncate_for_embed(result),
            color=color,
        )
        embed.set_footer(text=f"{len(urls)} feed(s) summarized")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "rss_digest", detail=topic or "(all feeds)")


async def setup(bot: commands.Bot) -> None:
    """Called automatically by bot.load_extension()."""
    await bot.add_cog(RSSCog(bot))
