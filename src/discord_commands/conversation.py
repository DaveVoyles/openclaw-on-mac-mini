"""Conversation commands: /clear, /model, /save, /resume, /threads, /threads-search, /forget."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from memory import get_model_preference, get_routing_profile, set_model_preference, set_routing_profile
from memory import store as conversation_store
from permissions import require_auth
from ui_components import EmbedColors

log = logging.getLogger("openclaw")


def _register_conversation_commands(bot: commands.Bot) -> None:
    """Register /clear, /model show|set, /save, /resume, /threads, /threads-search, /forget."""

    # ------------------------------------------------------------------
    # /clear
    # ------------------------------------------------------------------

    @bot.tree.command(name="clear", description="Clear your conversation history with OpenClaw")
    @require_auth
    async def clear_cmd(interaction: discord.Interaction):
        conversation_store.clear_user(interaction.user.id, interaction.channel_id)
        await interaction.response.send_message("🧹 Conversation cleared. Starting fresh!", ephemeral=True)
        audit_log(interaction.user, "clear")

    # ------------------------------------------------------------------
    # /model show | set
    # ------------------------------------------------------------------

    model_group = app_commands.Group(name="model", description="View or change your LLM model preference")

    @model_group.command(name="show", description="Show your current model routing preference")
    @require_auth
    async def model_show_cmd(interaction: discord.Interaction):
        pref = get_model_preference(interaction.user.id)
        user_profile = get_routing_profile(interaction.user.id)
        labels = {
            "auto": "🔄 Auto (routing profile)",
            "local": "🏠 Local (Gemma/Ollama)",
            "gemini": "☁️ Gemini (cloud)",
            "openai": "🟢 OpenAI (GPT-4o)",
            "anthropic": "🟣 Anthropic (Claude)",
            "copilot": "🟦 Copilot (enterprise proxy)",
        }
        profile_labels = {
            "copilot-first": "🟦 Copilot-first",
            "balanced": "⚖️ Balanced",
            "gemini-first": "☁️ Gemini-first",
            "cost-saver": "💰 Cost-saver",
        }
        from config import cfg
        system_profile = cfg.routing_profile or "copilot-first"
        profile_display = (
            f"{profile_labels.get(user_profile, user_profile)} *(your override)*"
            if user_profile
            else f"{profile_labels.get(system_profile, system_profile)} *(system default)*"
        )
        embed = discord.Embed(
            title="🤖 Model Preference",
            description=f"**Model:** {labels.get(pref, pref)}\n\n"
            f"**Routing profile:** {profile_display}\n\n"
            "Auto mode follows the routing profile for non-tool asks and keeps Gemini for tool-native flows.\n\n"
            "Use `/model set` to change provider · `/profile set` to change routing profile.\n"
            "Use `/ask model:` to override per-message.",
            color=EmbedColors.INFO,
        )
        try:
            from llm.providers import COPILOT_PROXY_ENABLED
            proxy_status = "🟢 Enabled" if COPILOT_PROXY_ENABLED else "🔴 Disabled"
            embed.add_field(name="Copilot Proxy", value=proxy_status, inline=False)
        except Exception as exc:
            log.debug("Copilot proxy status check failed: %s", exc)
        try:
            from llm import LOCAL_LLM_ENABLED, OLLAMA_MODEL, _ollama_available
            ollama_up = await _ollama_available() if LOCAL_LLM_ENABLED else False
            status = f"{'🟢' if ollama_up else '🔴'} Ollama ({OLLAMA_MODEL}): {'online' if ollama_up else 'offline'}"
            if not LOCAL_LLM_ENABLED:
                status = "⚪ Local LLM disabled"
            embed.add_field(name="Local LLM", value=status, inline=False)
        except Exception as exc:
            log.debug("Ollama status check failed: %s", exc)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @model_group.command(name="set", description="Set your default LLM routing preference")
    @app_commands.describe(preference="Which model to use by default")
    @app_commands.choices(preference=[
        app_commands.Choice(name="🔄 Auto — follow active routing profile", value="auto"),
        app_commands.Choice(name="🏠 Local — Gemma/Ollama (free, no tools)", value="local"),
        app_commands.Choice(name="☁️ Gemini — cloud (tools, best quality)", value="gemini"),
        app_commands.Choice(name="🟢 OpenAI — GPT-4o via Copilot", value="openai"),
        app_commands.Choice(name="🟣 Anthropic — Claude via Copilot", value="anthropic"),
        app_commands.Choice(name="🟦 Copilot — enterprise proxy", value="copilot"),
    ])
    @require_auth
    async def model_set_cmd(interaction: discord.Interaction, preference: app_commands.Choice[str]):
        result = set_model_preference(interaction.user.id, preference.value)
        embed = discord.Embed(
            title="⚙️ Model Preference Updated",
            description=result,
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "model_set", detail=preference.value)

    bot.tree.add_command(model_group)

    # ------------------------------------------------------------------
    # /profile show | set
    # ------------------------------------------------------------------

    profile_group = app_commands.Group(name="profile", description="View or change your auto-routing profile")

    @profile_group.command(name="show", description="Show your current routing profile")
    @require_auth
    async def profile_show_cmd(interaction: discord.Interaction):
        user_profile = get_routing_profile(interaction.user.id)
        from config import cfg
        system_profile = cfg.routing_profile or "copilot-first"
        profile_labels = {
            "copilot-first": "🟦 Copilot-first — Copilot for non-tool asks, Gemini for tools",
            "balanced": "⚖️ Balanced — best provider per query type",
            "gemini-first": "☁️ Gemini-first — Gemini preferred for everything",
            "cost-saver": "💰 Cost-saver — local Ollama first, Gemini only when needed",
        }
        if user_profile:
            description = (
                f"**Your profile:** {profile_labels.get(user_profile, user_profile)}\n\n"
                f"System default: `{system_profile}` *(overridden by your setting)*"
            )
        else:
            description = (
                f"**Active profile:** {profile_labels.get(system_profile, system_profile)} *(system default)*\n\n"
                "Use `/profile set` to choose your own routing profile."
            )
        embed = discord.Embed(
            title="🔀 Routing Profile",
            description=description,
            color=EmbedColors.INFO,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @profile_group.command(name="set", description="Set your auto-routing profile")
    @app_commands.describe(profile="How OpenClaw should route non-tool asks in auto mode")
    @app_commands.choices(profile=[
        app_commands.Choice(name="🟦 Copilot-first — Copilot for non-tool asks, Gemini for tools", value="copilot-first"),
        app_commands.Choice(name="⚖️ Balanced — best provider per query type", value="balanced"),
        app_commands.Choice(name="☁️ Gemini-first — Gemini preferred for everything", value="gemini-first"),
        app_commands.Choice(name="💰 Cost-saver — local Ollama first, Gemini only when needed", value="cost-saver"),
    ])
    @require_auth
    async def profile_set_cmd(interaction: discord.Interaction, profile: app_commands.Choice[str]):
        result = set_routing_profile(interaction.user.id, profile.value)
        is_err = result.startswith("❌")
        embed = discord.Embed(
            title="⚙️ Routing Profile Updated" if not is_err else "❌ Update Failed",
            description=result,
            color=discord.Color.red() if is_err else discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "profile_set", detail=profile.value)

    bot.tree.add_command(profile_group)

    # ------------------------------------------------------------------
    # /save, /resume, /threads, /threads-search, /forget
    # ------------------------------------------------------------------

    @bot.tree.command(name="save", description="Save the current conversation as a named thread (persists across restarts)")
    @app_commands.describe(name="A short name for this thread, e.g. 'media-research' (letters, digits, - or _)")
    @require_auth
    async def save_cmd(interaction: discord.Interaction, name: str):
        result = conversation_store.save_thread(interaction.user.id, interaction.channel_id, name)
        is_err = result.startswith("❌")
        embed = discord.Embed(
            title="💾 Save Thread" if not is_err else "❌ Save Failed",
            description=result,
            color=discord.Color.red() if is_err else discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "save_thread", detail=name)

    @bot.tree.command(name="resume", description="Resume a previously saved conversation thread")
    @app_commands.describe(name="Name of the thread to resume (use /threads to see your saved threads)")
    @require_auth
    async def resume_cmd(interaction: discord.Interaction, name: str):
        result = conversation_store.load_thread(interaction.user.id, interaction.channel_id, name)
        is_err = result.startswith("❌")
        embed = discord.Embed(
            title="▶️ Resume Thread" if not is_err else "❌ Resume Failed",
            description=result,
            color=discord.Color.red() if is_err else discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "resume_thread", detail=name)

    @bot.tree.command(name="threads", description="List all your saved conversation threads")
    @require_auth
    async def threads_cmd(interaction: discord.Interaction):
        result = conversation_store.list_threads(interaction.user.id)
        await interaction.response.send_message(result, ephemeral=True)

    @bot.tree.command(name="threads-search", description="Search across all your saved threads by keyword or topic")
    @app_commands.describe(query="Search term to find in thread titles, names, or message content")
    @require_auth
    async def threads_search_cmd(interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)

        try:
            from thread_store import search_threads as sqlite_search
            db_results = await sqlite_search(interaction.user.id, query, limit=10)
        except Exception as e:
            log.debug("SQLite thread search failed: %s", e)
            db_results = []

        semantic_lines = []
        try:
            import vector_store
            scoped_channel_id = interaction.channel_id
            scoped_thread_id = None
            if isinstance(interaction.channel, discord.Thread):
                scoped_thread_id = interaction.channel.id
                if interaction.channel.parent_id:
                    scoped_channel_id = interaction.channel.parent_id
            vec_results = await vector_store.search(
                vector_store.CONVERSATIONS_COLLECTION,
                query,
                top_k=5,
                channel_id=scoped_channel_id,
                thread_id=scoped_thread_id,
            )
            for r in vec_results:
                meta = r.get("metadata", {})
                name = meta.get("thread_name", "unknown")
                sim = r.get("similarity", 0)
                preview = r["text"][:100].replace("\n", " ")
                semantic_lines.append(f"🔮 **{name}** ({sim:.0%} match) — {preview}…")
        except Exception as e:
            log.debug("Vector thread search failed: %s", e)

        lines = [f"🔍 **Thread search: *{query}***\n"]

        if db_results:
            lines.append("**Keyword matches:**")
            for t in db_results:
                import time as _t
                name = t.get("name") or t.get("title") or f"thread-{t['id']}"
                msgs = t.get("message_count", 0)
                updated = _t.strftime("%Y-%m-%d", _t.localtime(t.get("updated_at", 0)))
                status_icon = {"active": "💬", "archived": "📦", "pinned": "📌"}.get(t.get("status", ""), "💬")
                lines.append(f"{status_icon} **{name}** — {msgs} msgs · {updated}")

        if semantic_lines:
            lines.append("\n**Semantic matches:**")
            lines.extend(semantic_lines)

        if not db_results and not semantic_lines:
            lines.append("No matching threads found.")

        await interaction.followup.send("\n".join(lines), ephemeral=True)
        audit_log(interaction.user, "threads_search", detail=query)

    @bot.tree.command(name="forget", description="Delete a saved conversation thread")
    @app_commands.describe(name="Name of the thread to delete")
    @require_auth
    async def forget_cmd(interaction: discord.Interaction, name: str):
        result = conversation_store.delete_thread(interaction.user.id, name)
        is_err = result.startswith("❌")
        embed = discord.Embed(
            title="🗑️ Delete Thread" if not is_err else "❌ Delete Failed",
            description=result,
            color=discord.Color.red() if is_err else discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "forget_thread", detail=name)
