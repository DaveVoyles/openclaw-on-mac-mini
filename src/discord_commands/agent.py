"""Agent & mission commands: /tasks, /bookmark, /weather, /plans, /plan-detail, /resume-plan, /cancel-plan."""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from agent_loop import cancel_plan as al_cancel_plan
from agent_loop import list_plans as al_list_plans
from agent_loop import read_plan as al_read_plan
from agent_loop import resume_plan as al_resume_plan
from audit import audit_log
from constants import EMBED_DESC_LIMIT
from llm import chat as llm_chat
from mission_control import get_mission_tasks

from ._helpers import require_auth

log = logging.getLogger(__name__)


def _register_agent_commands(bot: commands.Bot) -> None:
    """Register /tasks, /bookmark, /weather, /plans, /plan-detail, /resume-plan, /cancel-plan."""

    # ------------------------------------------------------------------
    # /tasks
    # ------------------------------------------------------------------

    @bot.tree.command(name="tasks", description="View Mission Control task board")
    @app_commands.describe(
        status="Filter by status: backlog, in_progress, review, done, permanent (default: all)",
    )
    @require_auth
    async def tasks_cmd(interaction: discord.Interaction, status: str = ""):
        await interaction.response.defer()
        result = await get_mission_tasks(status.strip() or None)
        embed = discord.Embed(
            title="📋 Mission Control",
            description=result[:4096],
            color=discord.Color.blue(),
        )
        embed.set_footer(text="davevoyles.github.io/openclaw-dashboard")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "tasks", detail=f"status={status or 'all'}")

    # ------------------------------------------------------------------
    # /bookmark
    # ------------------------------------------------------------------

    @bot.tree.command(name="bookmark", description="Save a URL or note to the Obsidian vault")
    @app_commands.describe(
        url="URL to bookmark (optional)",
        note="Description or notes about this bookmark",
        tags="Comma-separated tags, e.g. 'docker,reference' (optional)",
    )
    @require_auth
    async def bookmark_cmd(
        interaction: discord.Interaction,
        url: str = "",
        note: str = "",
        tags: str = "",
    ):
        await interaction.response.defer()

        from obsidian_writer import save_to_vault

        title = note[:80] or url[:80] or "Untitled Bookmark"
        content_parts: list[str] = []

        if url.startswith("http"):
            content_parts.append(f"**URL**: {url}")

            try:
                from skills.advanced_skills import browse_url
                page_text = await asyncio.wait_for(browse_url(url), timeout=15)
                if page_text and not page_text.startswith("❌"):
                    prompt = (
                        f"Summarize this webpage in 3-5 bullet points for a bookmark note.\n"
                        f"URL: {url}\n\nContent:\n{page_text[:3000]}"
                    )
                    summary, _, model_used = await asyncio.wait_for(
                        llm_chat(user_message=prompt), timeout=30
                    )
                    content_parts.append(f"\n## Summary\n\n{summary}")
                    import re as _re
                    h1 = _re.search(r"^#\s+(.+)$", page_text, _re.MULTILINE)
                    if h1:
                        title = h1.group(1)[:80]
            except Exception as e:  # broad: intentional
                log.debug("Bookmark URL summarize failed: %s", e)

        if note:
            content_parts.append(f"\n## Notes\n\n{note}")

        content = "\n".join(content_parts) or note or url
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        result = await save_to_vault(
            title=title,
            content=content,
            source_url=url if url.startswith("http") else "",
            tags=tag_list,
            content_type="bookmark",
        )

        embed = discord.Embed(
            title="📎 Bookmark Saved",
            description=result,
            color=discord.Color.green() if result.startswith("✅") else discord.Color.red(),
        )
        if url.startswith("http"):
            embed.add_field(name="URL", value=url[:200], inline=False)
        if tags:
            embed.add_field(name="Tags", value=tags[:100], inline=True)

        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "bookmark", detail=url[:200] or note[:200])

    # ------------------------------------------------------------------
    # /weather
    # ------------------------------------------------------------------

    @bot.tree.command(name="weather", description="Get current weather and forecast for a location")
    @app_commands.describe(
        location="City, airport code, or landmark (default: your configured home city)",
        units="'uscs' for °F/mph (default) or 'metric' for °C/km/h",
    )
    @require_auth
    async def weather_cmd(interaction: discord.Interaction, location: str = "", units: str = "uscs"):
        await interaction.response.defer()
        from skills.advanced_skills import get_weather
        result = await get_weather(location=location, units=units)
        embed = discord.Embed(
            title="🌤️ Weather",
            description=result,
            color=discord.Color.from_rgb(135, 206, 235),
        )
        embed.set_footer(text="via wttr.in — no API key required")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "weather", detail=f"loc={location or 'default'}")

    # ------------------------------------------------------------------
    # /plans, /plan-detail, /resume-plan, /cancel-plan
    # ------------------------------------------------------------------

    @bot.tree.command(name="plans", description="List active and recent agent plans")
    @app_commands.describe(status="Filter: all, in-progress, completed, interrupted (default: all)")
    @require_auth
    async def plans_cmd(interaction: discord.Interaction, status: str = "all"):
        await interaction.response.defer()
        result = await al_list_plans(status)
        embed = discord.Embed(
            title="📋 Agent Plans",
            description=result[:EMBED_DESC_LIMIT],
            color=discord.Color.teal(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "plans", detail=f"filter={status}")

    @bot.tree.command(name="plan-detail", description="Show details of a specific agent plan")
    @app_commands.describe(plan_id="The plan identifier (from /plans)")
    @require_auth
    async def plan_detail_cmd(interaction: discord.Interaction, plan_id: str):
        await interaction.response.defer()
        result = await al_read_plan(plan_id)
        embed = discord.Embed(
            title=f"📋 Plan: {plan_id[:60]}",
            description=result[:EMBED_DESC_LIMIT],
            color=discord.Color.teal(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "plan_detail", detail=plan_id[:100])

    @bot.tree.command(name="resume-plan", description="Resume an interrupted agent plan")
    @app_commands.describe(plan_id="The plan identifier to resume (from /plans)")
    @require_auth
    async def resume_plan_cmd(interaction: discord.Interaction, plan_id: str):
        await interaction.response.defer()
        result = await al_resume_plan(plan_id)
        embed = discord.Embed(
            title="🔄 Plan Resumed",
            description=result[:EMBED_DESC_LIMIT],
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "resume_plan", detail=plan_id[:100])

    @bot.tree.command(name="cancel-plan", description="Cancel an active agent plan")
    @app_commands.describe(plan_id="The plan identifier to cancel")
    @require_auth
    async def cancel_plan_cmd(interaction: discord.Interaction, plan_id: str):
        await interaction.response.defer()
        result = await al_cancel_plan(plan_id)
        embed = discord.Embed(
            title="⚠️ Plan Cancelled",
            description=result[:EMBED_DESC_LIMIT],
            color=discord.Color.orange(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "cancel_plan", detail=plan_id[:100])
