"""Research & web browsing commands — extracted from bot.py.

Handles: /research, /research-search, /sources, /websearch, /browse, /compare
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, split_response, truncate_for_embed
from constants import MEMORY_SNIPPET_MAX_CHARS
from discord_error import build_error_embed
from ui_components import EmbedColors

log = logging.getLogger("openclaw")


def _format_persistence_receipts(receipts: dict) -> str:
    """Format persistence receipts into a compact markdown block."""
    if not receipts:
        return "🧾 **Persistence receipts**\n• No persistence metadata available."

    ordered = [
        ("session", "Session"),
        ("vault", "Vault"),
        ("vector", "Vector"),
        ("gdoc", "Google Doc"),
    ]
    lines = ["🧾 **Persistence receipts**"]
    for key, label in ordered:
        info = receipts.get(key, {})
        saved = bool(info.get("saved"))
        icon = "✅" if saved else "⚪"
        location = str(info.get("location", "")).strip() or "n/a"
        detail = str(info.get("detail", "")).strip()
        line = f"• {icon} **{label}** → `{location}`"
        if detail:
            line += f" — {detail}"
        lines.append(line)
    return "\n".join(lines)


class _ResearchView(discord.ui.View):
    """Action buttons attached to a completed research report."""

    def __init__(self, query: str, report: str):
        super().__init__(timeout=300)
        self._query = query
        self._report = report

    @discord.ui.button(label="📌 Save to Memory", style=discord.ButtonStyle.secondary)
    async def save_to_memory(self, interaction: discord.Interaction, _button: discord.ui.Button):
        from qmd import remember_fact

        snippet = self._report[:MEMORY_SNIPPET_MAX_CHARS].strip()
        result = await remember_fact(
            content=f"[Research] {self._query}: {snippet}",
            tags="research",
        )
        await interaction.response.send_message(result, ephemeral=True)
        audit_log(interaction.user, "research_save_memory", detail=self._query[:80])

    @discord.ui.button(label="💾 Save to Vault", style=discord.ButtonStyle.green)
    async def save_to_vault_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from obsidian_writer import save_to_vault

        button.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            result = await save_to_vault(
                title=self._query,
                content=self._report,
                content_type="research",
                tags=["research"],
            )
            await interaction.followup.send(f"💾 {result}", ephemeral=True)
        except Exception as e:  # broad: intentional — Discord button handler; vault + Discord can fail
            await interaction.followup.send(embed=build_error_embed(e, context="/research save"), ephemeral=True)
        audit_log(interaction.user, "research_save_vault", detail=self._query[:80])

    @discord.ui.button(label="🔄 Re-run full research in 24h", style=discord.ButtonStyle.secondary)
    async def schedule_rerun(self, interaction: discord.Interaction, _button: discord.ui.Button):
        from scheduler import scheduler

        task = scheduler.create(
            action="run_scheduled_research",
            args={"query": self._query, "deep": False},
            hour=-1,
            minute=0,
            interval_minutes=1440,  # 24 hours
            created_by=str(interaction.user),
        )
        await interaction.response.send_message(
            f"✅ Scheduled full research re-run every 24h for **{self._query[:60]}** (task `{task.task_id}`).",
            ephemeral=True,
        )
        audit_log(interaction.user, "research_schedule_rerun", detail=self._query[:80])


class ResearchCog(commands.Cog, name="Research"):
    """Research, web search, and browsing commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        embed = build_error_embed(error, context="research command")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /websearch ────────────────────────────────────────────────────
    @app_commands.command(name="websearch", description="Search the live web for current information")
    @app_commands.describe(query="What to search for", results="Number of results (1-10, default 5)")
    @require_auth()
    async def websearch_cmd(self, interaction: discord.Interaction, query: str, results: int = 5):
        from skills.advanced_skills import search_web

        await interaction.response.defer(thinking=True)  # Progress indicator
        result = await search_web(query, num_results=results)
        result = truncate_for_embed(result)
        embed = discord.Embed(
            title=f"🔍 Web Search: {query[:80]}",
            description=result,
            color=EmbedColors.INFO,
        )
        embed.set_footer(text="via Tavily AI Search (with DuckDuckGo fallback)")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "websearch", detail=query)

    # ── /browse ───────────────────────────────────────────────────────
    @app_commands.command(name="browse", description="Fetch and read the content of a web page")
    @app_commands.describe(url="URL to fetch (must start with http:// or https://)", question="Optional: what to focus on")
    @require_auth()
    async def browse_cmd(self, interaction: discord.Interaction, url: str, question: str = ""):
        from llm import analyze_document as llm_analyze_document
        from skills.advanced_skills import browse_url

        if not url.startswith(("http://", "https://")):
            await interaction.response.send_message(
                "❌ URL must start with `http://` or `https://`\n"
                "💡 Example: `/browse url:https://example.com`",
                ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True)  # Progress indicator
        page_text = await browse_url(url)
        if question and not page_text.startswith("❌") and not page_text.startswith("⚠️"):
            answer = await llm_analyze_document(
                page_text,
                f"Based on the page content above, answer this question: {question}",
            )
            result = f"**Question**: {question}\n\n**Answer**: {answer}"
        else:
            result = page_text
        result = truncate_for_embed(result)
        embed = discord.Embed(
            title=f"🌐 Browse: {url[:80]}",
            description=result,
            color=EmbedColors.INFO,
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "browse", detail=url)

    # ── /research ─────────────────────────────────────────────────────
    @app_commands.command(name="research", description="Autonomous multi-step research — searches, reads sources, synthesizes a report")
    @app_commands.describe(
        query="What you want researched (be specific for best results)",
        deep="Enable deep research: 2-3 iterative passes that refine based on gaps (slower but more thorough)",
    )
    @require_auth()
    async def research_cmd(self, interaction: discord.Interaction, query: str, deep: bool = False):
        from approvals import is_emergency_stopped
        from cooldowns import check_cooldown
        from llm import is_configured as llm_is_configured
        from research_agent import ResearchAgent

        remaining = check_cooldown("research", interaction.user.id, cooldown_seconds=15.0)
        if remaining > 0:
            await interaction.response.send_message(
                f"⏱ Please wait {remaining:.1f}s before starting another research.", ephemeral=True
            )
            return

        if is_emergency_stopped():
            await interaction.response.send_message(
                "🛑 **Emergency stop active.** Use `/estop resume` to resume.", ephemeral=True
            )
            return

        if not llm_is_configured():
            await interaction.response.send_message(
                "⚠️ LLM not configured. Set `GOOGLE_API_KEY`.", ephemeral=True
            )
            return

        mode_label = "🔬 **Deep research started**" if deep else "🔍 **Research started**"
        await interaction.response.send_message(
            f"{mode_label} — I'll post updates and a final report here.\n> {query[:120]}"
        )
        original = await interaction.original_response()

        # Create a Discord thread for streaming progress
        try:
            thread = await original.create_thread(
                name=f"Research: {query[:80]}",
                auto_archive_duration=1440,
            )
            await thread.send("🔍 Decomposing query…")
        except discord.HTTPException as e:
            log.warning("Could not create research thread: %s", e)
            thread = None

        async def on_progress(msg: str):
            if thread:
                try:
                    await thread.send(msg)
                except (discord.HTTPException, discord.Forbidden, discord.NotFound) as exc:
                    log.debug("Research progress send failed: %s", exc)

        agent = ResearchAgent(max_searches=4, browse_top_n=2, timeout_seconds=300 if deep else 180)

        try:
            from runtime_state import request_context

            scoped_channel_id = interaction.channel_id
            scoped_thread_id = interaction.channel.id if isinstance(interaction.channel, discord.Thread) else None
            if isinstance(interaction.channel, discord.Thread) and interaction.channel.parent_id:
                scoped_channel_id = interaction.channel.parent_id
            with request_context(
                channel_id=scoped_channel_id,
                thread_id=scoped_thread_id,
                user_id=str(interaction.user.id),
            ):
                report = await agent.run(query, on_progress=on_progress, deep=deep)
        except Exception as e:  # broad: intentional — research agent spans LLM + HTTP + parsing
            log.error("Research command failed: %s", e)
            report = f"❌ Research failed: {e}"

        view = _ResearchView(query=query, report=report)
        chunks = split_response(report)
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                description=chunk,
                color=discord.Color.from_rgb(0, 150, 200),
            )
            if i == 0:
                embed.set_author(name=f"Research: {query[:100]}")
            if i == len(chunks) - 1:
                embed.set_footer(text="✅ Research complete — Gemini 2.5 Flash with extended thinking")
                if thread:
                    await thread.send(embed=embed, view=view)
                else:
                    await interaction.followup.send(embed=embed, view=view)
            else:
                if thread:
                    await thread.send(embed=embed)
                else:
                    await interaction.followup.send(embed=embed)

        session_location = (
            f"discord-thread:{thread.id}" if thread else f"discord-channel:{interaction.channel_id}"
        )
        receipts = agent.get_last_receipts()
        receipts["session"] = {
            "saved": True,
            "location": session_location,
            "detail": "Final report posted to this conversation",
        }
        receipts_text = _format_persistence_receipts(receipts)
        if thread:
            await thread.send(receipts_text)
        else:
            await interaction.followup.send(receipts_text)

        try:
            follow_ups = await agent.generate_follow_ups(query, report)
            if follow_ups:
                follow_up_text = "**💡 Suggested follow-ups:**\n" + "\n".join(
                    f"{i}. {fq}" for i, fq in enumerate(follow_ups, 1)
                )
                if thread:
                    await thread.send(follow_up_text)
                else:
                    await interaction.followup.send(follow_up_text)
        except Exception as e:  # broad: intentional — follow-up generation spans LLM + Discord
            log.debug("Follow-up generation skipped: %s", e)

        audit_log(interaction.user, "research", detail=query[:200])

    # ── /research-search ──────────────────────────────────────────────
    @app_commands.command(name="research-search", description="Search across all your past research reports by topic")
    @app_commands.describe(query="What to search for in past research")
    @require_auth()
    async def research_search_cmd(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)

        lines = [f"🔍 **Research search: *{query}***\n"]

        try:
            import vector_store
            results = await vector_store.search(
                vector_store.RESEARCH_COLLECTION, query, top_k=5
            )
            if results:
                for r in results:
                    meta = r.get("metadata", {})
                    original_query = meta.get("query", "unknown topic")
                    sim = r.get("similarity", 0)
                    preview = r["text"][:200].replace("\n", " ")
                    lines.append(f"📄 **{original_query}** ({sim:.0%} match)")
                    lines.append(f"  _{preview}_\n")
            else:
                lines.append("No matching research found. Use `/research <query>` to start new research.")
        except Exception as e:  # broad: intentional — vector store can fail in many ways
            lines.append(f"⚠️ Search unavailable: {e}")

        await interaction.followup.send("\n".join(lines), ephemeral=True)
        audit_log(interaction.user, "research_search", detail=query)

    # ── /sources ──────────────────────────────────────────────────────
    @app_commands.command(name="sources", description="Search your library of previously browsed web sources")
    @app_commands.describe(query="Topic or keyword to find in past browsed sources")
    @require_auth()
    async def sources_cmd(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)

        lines = [f"📚 **Source library search: *{query}***\n"]

        try:
            import vector_store
            results = await vector_store.search(
                vector_store.RESEARCH_COLLECTION, query, top_k=10,
                where={"type": "source"},
            )
            if results:
                for r in results:
                    meta = r.get("metadata", {})
                    url = meta.get("url", "unknown")
                    domain = meta.get("domain", "")
                    sim = r.get("similarity", 0)
                    excerpt = r["text"][:150].replace("\n", " ")
                    lines.append(f"🔗 [{domain}]({url}) ({sim:.0%} match)")
                    lines.append(f"  _{excerpt}_\n")
            else:
                lines.append("No matching sources found. Sources are automatically cataloged during `/research`.")
        except Exception as e:  # broad: intentional — vector store can fail in many ways
            lines.append(f"⚠️ Source search unavailable: {e}")

        await interaction.followup.send("\n".join(lines), ephemeral=True)
        audit_log(interaction.user, "sources_search", detail=query)

    # ── /compare ─────────────────────────────────────────────────────
    @app_commands.command(name="compare", description="Compare answers from multiple search providers side-by-side")
    @app_commands.describe(query="The question to compare across providers")
    @require_auth()
    async def compare_cmd(self, interaction: discord.Interaction, query: str):
        from skills.search_skills import _firecrawl_search, _perplexity_search, serper_search

        await interaction.response.defer()

        results = await asyncio.gather(
            _perplexity_search(query, 3),
            _firecrawl_search(query, 3),
            serper_search(query, 3),
            return_exceptions=True,
        )

        providers = ["🔮 Perplexity", "🔥 Firecrawl", "🔍 Serper (Google)"]

        for provider_name, result in zip(providers, results):
            if isinstance(result, Exception):
                embed = discord.Embed(
                    title=provider_name,
                    description=f"❌ Failed: {result}",
                    color=discord.Color.red(),
                )
            elif not result:
                embed = discord.Embed(
                    title=provider_name,
                    description="No results (provider may not be configured)",
                    color=discord.Color.greyple(),
                )
            else:
                embed = discord.Embed(
                    title=provider_name,
                    description=truncate_for_embed(result),
                    color=discord.Color.blue(),
                )
            await interaction.followup.send(embed=embed)

        audit_log(interaction.user, "compare", detail=query)


async def setup(bot: commands.Bot):
    """Called automatically by bot.load_extension()."""
    await bot.add_cog(ResearchCog(bot))
