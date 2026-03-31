"""Memory & knowledge management commands — extracted from bot.py.

Handles: /remember, /recall, /memory-stats, /memory-refresh,
         /rules, /profile, /profile-edit, /goals
"""

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth

log = logging.getLogger("openclaw")


class MemoryCog(commands.Cog, name="Memory"):
    """Memory, profile, rules, and goal management commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error)
        else:
            msg = f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ── /remember ─────────────────────────────────────────────────────
    @app_commands.command(name="remember", description="Store a fact in long-term memory (QMD)")
    @app_commands.describe(content="Fact to remember", tags="Comma-separated tags")
    @require_auth()
    async def remember_cmd(self, interaction: discord.Interaction, content: str, tags: str = ""):
        from qmd import remember_fact

        result = await remember_fact(content, tags)
        await interaction.response.send_message(result)
        audit_log(interaction.user, "remember", detail=content)

    # ── /recall ───────────────────────────────────────────────────────
    @app_commands.command(name="recall", description="Search long-term memory (QMD)")
    @app_commands.describe(query="Keywords to search for")
    @require_auth()
    async def recall_cmd(self, interaction: discord.Interaction, query: str):
        from qmd import recall_fact

        result = await recall_fact(query)
        embed = discord.Embed(title=f"🧠 Recall: {query}", description=result, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "recall", detail=query)

    # ── /goals ────────────────────────────────────────────────────────
    @app_commands.command(name="goals", description="View your active goals and intentions")
    @require_auth()
    async def goals_cmd(self, interaction: discord.Interaction):
        from goal_tracker import get_active_goals

        goals = get_active_goals(interaction.user.id)
        if not goals:
            await interaction.response.send_message(
                "No active goals tracked yet. I'll detect them from your conversations automatically!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🎯 Active Goals ({len(goals)})",
            color=discord.Color.green(),
        )
        for g in goals[:10]:
            mentions = g.get("mention_count", 1)
            created = time.strftime("%b %d", time.localtime(g.get("created_at", 0)))
            embed.add_field(
                name=g["goal"],
                value=f"Since {created} · mentioned {mentions}x",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /memory-stats ─────────────────────────────────────────────────
    @app_commands.command(name="memory-stats", description="Show memory and vector store statistics")
    @require_auth()
    async def memory_stats_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        lines = ["📊 **Memory Statistics**\n"]

        # QMD stats
        try:
            from qmd import qmd_store
            qmd_count = len(qmd_store._memory)
            lines.append(f"**QMD Facts:** {qmd_count:,} entries")
        except Exception:
            lines.append("**QMD Facts:** unavailable")

        # Vector store stats
        try:
            import vector_store
            stats = await vector_store.get_stats()
            for name, info in stats.items():
                label = name.replace("_", " ").title()
                lines.append(f"**{label} vectors:** {info['count']:,}")
        except Exception:
            lines.append("**Vector store:** unavailable")

        # Thread store stats
        try:
            from thread_store import get_stats as thread_stats
            ts = await thread_stats()
            lines.append(f"\n**Threads:** {ts['total_threads']} total ({ts['active_threads']} active, {ts['archived_threads']} archived)")
            lines.append(f"**Messages stored:** {ts['total_messages']:,}")
        except Exception:
            lines.append("**Thread store:** unavailable")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ── /memory-refresh ───────────────────────────────────────────────
    @app_commands.command(name="memory-refresh", description="Reinforce a memory so it doesn't decay (bump its access score)")
    @app_commands.describe(query="Search query to find the memory to reinforce")
    @require_auth()
    async def memory_refresh_cmd(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        try:
            import vector_store
            results = await vector_store.search_all(query, top_k=3)
            if not results:
                await interaction.followup.send("No matching memories found.", ephemeral=True)
                return
            for r in results:
                col = r.get("collection", "memories")
                await vector_store.bump_access(col, [r["id"]])
            lines = [f"🔄 **Reinforced {len(results)} memories:**\n"]
            for r in results:
                sim = r.get("similarity", 0)
                text = r["text"][:120].replace("\n", " ")
                lines.append(f"• ({sim:.0%}) {text}")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Refresh failed: {e}", ephemeral=True)
        audit_log(interaction.user, "memory_refresh", detail=query)

    # ── /rules ────────────────────────────────────────────────────────
    @app_commands.command(name="rules", description="View or manage learned behavioral rules")
    @app_commands.describe(action="list (default), search, or delete", query="Search query or rule ID to delete")
    @require_auth()
    async def rules_cmd(self, interaction: discord.Interaction, action: str = "list", query: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            from rules_engine import delete_rule, get_all_rules, get_relevant_rules

            if action == "delete" and query:
                success = await delete_rule(query)
                if success:
                    await interaction.followup.send(f"✅ Rule `{query}` deleted.", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Rule `{query}` not found.", ephemeral=True)
                return

            if action == "search" and query:
                rules = await get_relevant_rules(query, top_k=10)
                if rules:
                    lines = [f"🔍 **Rules matching *{query}*:**\n"]
                    for i, r in enumerate(rules, 1):
                        lines.append(f"{i}. {r}")
                    await interaction.followup.send("\n".join(lines), ephemeral=True)
                else:
                    await interaction.followup.send("No matching rules found.", ephemeral=True)
                return

            # Default: list all
            all_rules = await get_all_rules()
            if not all_rules:
                await interaction.followup.send("📝 No learned rules yet. I'll learn them when you correct me!", ephemeral=True)
                return
            lines = [f"📝 **Learned Rules ({len(all_rules)} total):**\n"]
            for r in all_rules[-20:]:
                lines.append(f"• {r['rule']}  `{r['id']}`")
            if len(all_rules) > 20:
                lines.append(f"\n_...and {len(all_rules) - 20} more (use `/rules action:search` to find specific rules)_")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Rules unavailable: {e}", ephemeral=True)
        audit_log(interaction.user, "rules", detail=f"{action} {query}")

    # ── /profile ──────────────────────────────────────────────────────
    @app_commands.command(name="profile", description="View your user profile (preferences, interests, tools)")
    @require_auth()
    async def profile_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            from user_profile import load_profile
            profile = load_profile()

            lines = ["👤 **Your Profile**\n"]
            if profile.get("preferences"):
                pairs = ", ".join(f"`{k}`: {v}" for k, v in profile["preferences"].items())
                lines.append(f"**Preferences:** {pairs}")
            if profile.get("interests"):
                lines.append(f"**Interests:** {', '.join(profile['interests'])}")
            if profile.get("tools"):
                lines.append(f"**Tools:** {', '.join(profile['tools'])}")
            if profile.get("working_style"):
                lines.append(f"**Working style:** {profile['working_style']}")
            if profile.get("communication_style"):
                lines.append(f"**Communication style:** {profile['communication_style']}")
            if profile.get("context_notes"):
                lines.append(f"\n**Context notes:** {len(profile['context_notes'])} entries")
                for note in profile["context_notes"][-5:]:
                    lines.append(f"  • {note}")

            if len(lines) == 1:
                lines.append("_Empty — I'll learn about you as we chat! You can also tell me things like 'I prefer concise answers' or 'my timezone is US/Eastern'._")

            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Profile unavailable: {e}", ephemeral=True)
        audit_log(interaction.user, "profile")

    # ── /profile-edit ─────────────────────────────────────────────────
    @app_commands.command(name="profile-edit", description="Manually update your user profile")
    @app_commands.describe(
        field="Field to update: preference, interest, note, working_style, communication_style",
        value="Value to set (for preference, use 'key=value' format)",
    )
    @require_auth()
    async def profile_edit_cmd(self, interaction: discord.Interaction, field: str, value: str):
        await interaction.response.defer(ephemeral=True)
        try:
            from user_profile import (
                add_context_note,
                add_interest,
                sync_profile_to_vectors,
                update_field,
                update_preference,
            )

            if field == "preference" and "=" in value:
                k, v = value.split("=", 1)
                update_preference(k.strip(), v.strip())
                msg = f"✅ Preference set: `{k.strip()}` = {v.strip()}"
            elif field == "interest":
                add_interest(value)
                msg = f"✅ Interest added: {value}"
            elif field == "note":
                add_context_note(value)
                msg = "✅ Context note added"
            elif field in ("working_style", "communication_style"):
                update_field(field, value)
                msg = f"✅ {field.replace('_', ' ').title()} updated"
            else:
                msg = "❌ Unknown field. Use: preference, interest, note, working_style, or communication_style"

            try:
                await sync_profile_to_vectors()
            except Exception:
                pass

            await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Update failed: {e}", ephemeral=True)
        audit_log(interaction.user, "profile_edit", detail=f"{field}={value[:100]}")


async def setup(bot: commands.Bot):
    """Called automatically by bot.load_extension()."""
    await bot.add_cog(MemoryCog(bot))
