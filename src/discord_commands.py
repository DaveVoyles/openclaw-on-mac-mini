"""
OpenClaw slash commands — extracted from bot.py.

All slash commands *except* ``/ask`` (which stays in bot.py) are registered
here via ``register_commands(bot)``.  This function is called once after the
bot object is created.
"""

import asyncio
import functools
import io
import logging
import os
import platform
import time

import aiohttp
import discord
from discord import app_commands

from agent_loop import cancel_plan as al_cancel_plan
from agent_loop import list_plans as al_list_plans
from agent_loop import read_plan as al_read_plan
from agent_loop import resume_plan as al_resume_plan
from agentmail import send_agent_mail
from analyzer import analyze_logs
from approvals import (
    approval_store,
    is_emergency_stopped,
    set_emergency_stop,
)
from audit import audit_log
from code_sandbox import run_code as sandbox_run_code
from config import cfg
from constants import (
    DEFAULT_ANALYZE_LINES,
    DOCUMENT_MAX_CHARS,
    EMBED_DESC_LIMIT,
    EMBED_SPLIT_LIMIT,
    MAX_FILE_SIZE,
    OUTPUT_MAX_CHARS,
    PDF_MAX_PAGES,
)
from git_skills import git_diff, git_status
from image_gen import generate_image
from image_gen import is_available as sd_is_available
from llm import SUPPORTED_IMAGE_MIMES
from llm import analyze_document as llm_analyze_document
from llm import analyze_image as llm_analyze_image
from llm import chat as llm_chat
from llm import is_configured as llm_is_configured
from memory import get_model_preference, set_model_preference
from memory import store as conversation_store
from mission_control import get_mission_tasks
from scheduler import scheduler
from skills import SKILLS
from skills.advanced_skills import (
    check_service_ports,
    create_status_report,
    get_weather,
)

log = logging.getLogger("openclaw")

VERSION = cfg.version

# ---------------------------------------------------------------------------
# Auth helpers (self-contained to avoid circular imports with bot.py)
# ---------------------------------------------------------------------------

ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]


def _is_allowed(interaction: discord.Interaction) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return interaction.user.id in ALLOWED_USER_IDS


def require_auth(func):
    """Decorator that gates a slash-command handler behind the allow-list."""

    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        if not _is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.", ephemeral=True
            )
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


def truncate_for_embed(text: str, limit: int = EMBED_DESC_LIMIT) -> str:
    """Truncate *text* to fit in a Discord embed description."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# Module-level aiohttp session (reused for attachment downloads)
# ---------------------------------------------------------------------------

_http_session: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


def register_commands(bot):  # noqa: C901 — large but flat
    """Register all slash commands (except /ask) on *bot*.tree."""

    # Import send_morning_briefing lazily to avoid circular deps
    from discord_background import send_morning_briefing

    # ------------------------------------------------------------------
    # /ping
    # ------------------------------------------------------------------

    @bot.tree.command(name="ping", description="Check if OpenClaw is alive")
    @require_auth
    async def ping(interaction: discord.Interaction):
        latency_ms = round(bot.latency * 1000, 1)
        uptime_s = round(time.monotonic() - bot.start_time)
        hours, remainder = divmod(uptime_s, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        embed = discord.Embed(title="🏓 Pong!", color=discord.Color.green())
        embed.add_field(name="Latency", value=f"{latency_ms} ms", inline=True)
        embed.add_field(name="Uptime", value=uptime_str, inline=True)
        embed.set_footer(text=f"OpenClaw v{VERSION} • Phase 5")

        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "ping", f"latency={latency_ms}ms")

    # ------------------------------------------------------------------
    # /about
    # ------------------------------------------------------------------

    @bot.tree.command(name="about", description="Show OpenClaw version and system info")
    @require_auth
    async def about(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 OpenClaw",
            description="Autonomous AI agent for home automation and system management.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Version", value=f"{VERSION} (Phase 5)", inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Host", value=platform.node(), inline=True)
        embed.add_field(name="OS", value=f"{platform.system()} {platform.machine()}", inline=True)
        embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
        embed.set_footer(text="Mac Mini M4 Pro • Docker")

        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "about")

    # ------------------------------------------------------------------
    # /whoami
    # ------------------------------------------------------------------

    @bot.tree.command(name="whoami", description="Show your Discord identity and permission level")
    @require_auth
    async def whoami(interaction: discord.Interaction):
        allowed = _is_allowed(interaction)
        status = "✅ Authorized" if allowed else "❌ Not Authorized"

        embed = discord.Embed(
            title="👤 Identity",
            color=discord.Color.green() if allowed else discord.Color.red(),
        )
        embed.add_field(name="User", value=str(interaction.user), inline=True)
        embed.add_field(name="ID", value=str(interaction.user.id), inline=True)
        embed.add_field(name="Status", value=status, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "whoami", f"allowed={allowed}")

    # ------------------------------------------------------------------
    # /help
    # ------------------------------------------------------------------

    @bot.tree.command(name="help", description="List available OpenClaw commands")
    @require_auth
    async def help_cmd(interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 OpenClaw Commands",
            description="Available slash commands:",
            color=discord.Color.blurple(),
        )
        commands_list = [
            ("`/ask <question>`", "Ask OpenClaw anything (AI-powered)"),
            ("`/clear`", "Clear your conversation history"),
            ("`/ping`", "Check if OpenClaw is alive"),
            ("`/about`", "Show version and system info"),
            ("`/whoami`", "Show your identity and permissions"),
            ("`/containers`", "List all running Docker containers"),
            ("`/status <service>`", "Get detailed container status"),
            ("`/logs <service> [lines]`", "View container logs (default 30 lines)"),
            ("`/system`", "Show system resource usage"),
            ("`/dockerstats`", "Show per-container resource usage"),
            ("`/restart <service>`", "Restart a container (requires approval)"),
            ("`/search <query> [type]`", "Search Sonarr/Radarr for media"),
            ("`/queue`", "Show active downloads (SABnzbd + qBit)"),
            ("`/recent [count]`", "Recently added media (via Plex)"),
            ("`/health`", "Check *arr services and download clients"),
            ("`/ports`", "Check service port connectivity"),
            ("`/report`", "Generate full system status report"),
            ("`/analyze <service> [lines]`", "AI-powered log analysis"),
            ("`/schedule`", "Manage scheduled tasks"),
            ("`/spending`", "View Gemini API spending & budget"),
            ("`/skills`", "List all available skills"),
            ("`/pending`", "List pending approval requests"),
            ("`/auditlog [lines]`", "View recent audit log entries"),
            ("`/estop`", "Emergency stop — halt all bot actions"),
            ("`/estop resume`", "Resume bot after emergency stop"),
            ("`/websearch <query> [results]`", "Search the live web via Tavily AI Search"),
            ("`/browse <url> [question]`", "Fetch and read a web page; optionally Q&A it"),
            ("`/analyze-image <image> [question]`", "Analyze an image with Gemini Vision"),
            ("`/analyze-file <file> [question]`", "Analyze a document/PDF with Gemini AI"),
            ("`/help`", "This help message"),
        ]
        for name, desc in commands_list:
            embed.add_field(name=name, value=desc, inline=False)

        embed.set_footer(text=f"OpenClaw v{VERSION} • Phase 5")
        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "help")

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
    async def model_show_cmd(interaction: discord.Interaction):
        pref = get_model_preference(interaction.user.id)
        labels = {
            "auto": "🔄 Auto (Copilot → Gemini)",
            "local": "🏠 Local (Gemma/Ollama)",
            "gemini": "☁️ Gemini (cloud)",
            "openai": "🟢 OpenAI (GPT-4o)",
            "anthropic": "🟣 Anthropic (Claude)",
        }
        embed = discord.Embed(
            title="🤖 Model Preference",
            description=f"**Current:** {labels.get(pref, pref)}\n\n"
            "**Auto routing order:** Copilot proxy (free) → Gemini (tools) → Ollama (last resort)\n\n"
            "Use `/model set` to change.\n"
            "Use `/ask model:` to override per-message.",
            color=discord.Color.blue(),
        )
        try:
            from model_router import COPILOT_PROXY_ENABLED
            proxy_status = "🟢 Copilot proxy: online" if COPILOT_PROXY_ENABLED else "⚪ Copilot proxy: not configured"
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
        app_commands.Choice(name="🔄 Auto — Copilot first, then Gemini (default)", value="auto"),
        app_commands.Choice(name="🏠 Local — Gemma/Ollama (free, no tools)", value="local"),
        app_commands.Choice(name="☁️ Gemini — cloud (tools, best quality)", value="gemini"),
        app_commands.Choice(name="🟢 OpenAI — GPT-4o via Copilot", value="openai"),
        app_commands.Choice(name="🟣 Anthropic — Claude via Copilot", value="anthropic"),
    ])
    async def model_set_cmd(interaction: discord.Interaction, preference: app_commands.Choice[str]):
        result = set_model_preference(interaction.user.id, preference.value)
        await interaction.response.send_message(result, ephemeral=True)
        audit_log(interaction.user, "model_set", detail=preference.value)

    bot.tree.add_command(model_group)

    # ------------------------------------------------------------------
    # /save, /resume, /threads, /threads-search, /forget
    # ------------------------------------------------------------------

    @bot.tree.command(name="save", description="Save the current conversation as a named thread (persists across restarts)")
    @app_commands.describe(name="A short name for this thread, e.g. 'media-research' (letters, digits, - or _)")
    @require_auth
    async def save_cmd(interaction: discord.Interaction, name: str):
        result = conversation_store.save_thread(interaction.user.id, interaction.channel_id, name)
        await interaction.response.send_message(result, ephemeral=True)
        audit_log(interaction.user, "save_thread", detail=name)

    @bot.tree.command(name="resume", description="Resume a previously saved conversation thread")
    @app_commands.describe(name="Name of the thread to resume (use /threads to see your saved threads)")
    @require_auth
    async def resume_cmd(interaction: discord.Interaction, name: str):
        result = conversation_store.load_thread(interaction.user.id, interaction.channel_id, name)
        await interaction.response.send_message(result, ephemeral=True)
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
            vec_results = await vector_store.search(
                vector_store.CONVERSATIONS_COLLECTION, query, top_k=5
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
        await interaction.response.send_message(result, ephemeral=True)
        audit_log(interaction.user, "forget_thread", detail=name)

    # ------------------------------------------------------------------
    # /ports, /report, /analyze
    # ------------------------------------------------------------------

    @bot.tree.command(name="ports", description="Check service port connectivity")
    @require_auth
    async def ports_cmd(interaction: discord.Interaction):
        await interaction.response.defer()
        result = await check_service_ports()
        embed = discord.Embed(
            title="🔌 Port Status",
            description=result,
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "ports")

    @bot.tree.command(name="report", description="Generate a comprehensive system status report")
    @require_auth
    async def report_cmd(interaction: discord.Interaction):
        await interaction.response.defer()
        result = await create_status_report()
        embed = discord.Embed(
            title="📊 System Report",
            description=result,
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "report")

    @bot.tree.command(name="analyze", description="AI-powered container log analysis")
    @app_commands.describe(service="Container name to analyze", lines="Log lines to analyze (10-200, default 50)")
    @require_auth
    async def analyze_cmd(interaction: discord.Interaction, service: str, lines: int = DEFAULT_ANALYZE_LINES):
        await interaction.response.defer()
        result = await analyze_logs(service, lines)
        result = truncate_for_embed(result)
        embed = discord.Embed(
            title=f"🔬 Log Analysis: {service}",
            description=result,
            color=discord.Color.dark_orange(),
        )
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "analyze", detail=f"{service} lines={lines}")

    # ------------------------------------------------------------------
    # /schedule
    # ------------------------------------------------------------------

    @bot.tree.command(name="schedule", description="Manage scheduled tasks")
    @app_commands.describe(
        action="list, add, remove, or toggle",
        skill="Skill name for 'add' (e.g. check_arr_health)",
        hour="Hour (0-23) for daily schedule (-1 for interval)",
        minute="Minute (0-59)",
        interval="Interval in minutes (overrides hour/minute)",
        task_id="Task ID for remove/toggle (e.g. sched-1)",
    )
    async def schedule_cmd(
        interaction: discord.Interaction,
        action: str = "list",
        skill: str = "",
        hour: int = -1,
        minute: int = 0,
        interval: int = 0,
        task_id: str = "",
    ):

        if action == "list":
            tasks = scheduler.list_tasks()
            if not tasks:
                await interaction.response.send_message("📅 No scheduled tasks.", ephemeral=True)
                return
            lines = []
            for t in tasks:
                status = "✅" if t.enabled else "⏸️"
                schedule_str = f"every {t.interval_minutes}m" if t.interval_minutes > 0 else f"{t.cron_hour:02d}:{t.cron_minute:02d}"
                lines.append(
                    f"{status} `{t.task_id}` — **{t.action}** @ {schedule_str} "
                    f"(runs: {t.run_count}, next: {t.next_run_str})"
                )
            embed = discord.Embed(
                title=f"📅 Scheduled Tasks ({len(tasks)})",
                description="\n".join(lines),
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(embed=embed)

        elif action == "add":
            if not skill:
                await interaction.response.send_message(
                    "❌ Provide a skill name. Example: `/schedule add check_arr_health hour:6`",
                    ephemeral=True,
                )
                return
            task = scheduler.create(
                action=skill,
                hour=hour,
                minute=minute,
                interval_minutes=interval,
                created_by=str(interaction.user),
            )
            schedule_str = f"every {interval}m" if interval > 0 else f"daily at {hour:02d}:{minute:02d}"
            await interaction.response.send_message(
                f"✅ Scheduled `{task.task_id}`: **{skill}** — {schedule_str}"
            )
            audit_log(interaction.user, "schedule_add", detail=f"{task.task_id} {skill}")

        elif action == "remove":
            if not task_id:
                await interaction.response.send_message("❌ Provide a task_id. Example: `/schedule remove task_id:sched-1`", ephemeral=True)
                return
            if scheduler.remove(task_id):
                await interaction.response.send_message(f"🗑️ Removed `{task_id}`.")
                audit_log(interaction.user, "schedule_remove", detail=task_id)
            else:
                await interaction.response.send_message(f"❌ Task `{task_id}` not found.", ephemeral=True)

        elif action == "toggle":
            if not task_id:
                await interaction.response.send_message("❌ Provide a task_id.", ephemeral=True)
                return
            new_state = scheduler.toggle(task_id)
            if new_state is None:
                await interaction.response.send_message(f"❌ Task `{task_id}` not found.", ephemeral=True)
            else:
                emoji = "✅" if new_state else "⏸️"
                await interaction.response.send_message(f"{emoji} Task `{task_id}` {'enabled' if new_state else 'disabled'}.")
                audit_log(interaction.user, "schedule_toggle", detail=f"{task_id} enabled={new_state}")
        else:
            await interaction.response.send_message(
                "❌ Unknown action. Use: `list`, `add`, `remove`, or `toggle`.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /skills
    # ------------------------------------------------------------------

    @bot.tree.command(name="skills", description="List all available OpenClaw skills")
    @app_commands.describe(category="Filter by category name (leave empty for overview)")
    @require_auth
    async def skills_cmd(interaction: discord.Interaction, category: str | None = None):
        """List skills grouped by category. Pick a category for details."""
        from skills import SKILL_CATEGORIES

        if category:
            match = None
            for cat_name in SKILL_CATEGORIES:
                if category.lower() in cat_name.lower():
                    match = cat_name
                    break
            if match:
                skill_names = SKILL_CATEGORIES[match]
                lines = []
                for name in sorted(skill_names):
                    fn = SKILLS.get(name)
                    if fn:
                        doc = (fn.__doc__ or "No description").strip().split("\n")[0][:100]
                        lines.append(f"• `{name}` — {doc}")
                embed = discord.Embed(
                    title=f"{match} ({len(lines)} skills)",
                    description="\n".join(lines) or "No skills in this category.",
                    color=discord.Color.blurple(),
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                audit_log(interaction.user, "skills", detail=f"category={match}")
                return
            else:
                await interaction.response.send_message(
                    f"❌ Unknown category `{category}`. Use `/skills` to see all categories.",
                    ephemeral=True,
                )
                return

        embed = discord.Embed(
            title=f"🧰 OpenClaw Skills ({len(SKILLS)} total)",
            description="Skills are grouped by category. Use `/skills category:<name>` to see details.\n"
                        "The LLM calls these automatically via `/ask`.",
            color=discord.Color.blurple(),
        )
        for cat_name, skill_names in SKILL_CATEGORIES.items():
            valid = [n for n in skill_names if n in SKILLS]
            if valid:
                preview = ", ".join(f"`{n}`" for n in sorted(valid)[:5])
                if len(valid) > 5:
                    preview += f" + {len(valid) - 5} more"
                embed.add_field(name=f"{cat_name} ({len(valid)})", value=preview, inline=False)

        await interaction.response.send_message(embed=embed)
        audit_log(interaction.user, "skills")

    # ------------------------------------------------------------------
    # /pending, /estop
    # ------------------------------------------------------------------

    @bot.tree.command(name="pending", description="List pending approval requests")
    @require_auth
    async def pending_cmd(interaction: discord.Interaction):
        pending = approval_store.list_pending()
        if not pending:
            await interaction.response.send_message("\u2705 No pending approval requests.", ephemeral=True)
            return

        lines = []
        for req in pending:
            lines.append(
                f"\u2022 `{req.request_id}` \u2014 **{req.action}** `{req.target}` "
                f"(by {req.requester_name}, {req.age_seconds}s ago)"
            )

        embed = discord.Embed(
            title=f"\u23f3 Pending Approvals ({len(pending)})",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "pending")

    @bot.tree.command(name="estop", description="Emergency stop \u2014 halt or resume all bot actions")
    @app_commands.describe(action="'stop' to halt, 'resume' to resume (default: stop)")
    @require_auth
    async def estop_cmd(interaction: discord.Interaction, action: str = "stop"):
        if action.lower() in ("resume", "start", "off", "deactivate"):
            set_emergency_stop(False)
            await interaction.response.send_message(
                "\u2705 **Emergency stop deactivated.** Bot is now accepting actions."
            )
            audit_log(interaction.user, "estop", detail="resume")
        else:
            set_emergency_stop(True)
            await interaction.response.send_message(
                "\U0001f6d1 **EMERGENCY STOP ACTIVATED**\n"
                "All write actions (restart, etc.) are now blocked.\n"
                "Use `/estop resume` to resume normal operations."
            )
            audit_log(interaction.user, "estop", detail="activated")

    # ------------------------------------------------------------------
    # /mail
    # ------------------------------------------------------------------

    @bot.tree.command(name="mail", description="Send an automated e-mail message via AgentMail")
    @app_commands.describe(to="Recipient email", subject="Email subject", body="Message body")
    @require_auth
    async def mail_cmd(interaction: discord.Interaction, to: str, subject: str, body: str):
        if is_emergency_stopped():
            await interaction.response.send_message("🛑 Emergency stop active.", ephemeral=True)
            return
        await interaction.response.defer()
        result = await send_agent_mail(to, subject, body)
        await interaction.followup.send(result)
        audit_log(interaction.user, "mail", detail=f"to={to} subj={subject}")

    # ------------------------------------------------------------------
    # /analyze-image, /analyze-file
    # ------------------------------------------------------------------

    @bot.tree.command(name="analyze-image", description="Analyze an image using Gemini AI vision")
    @app_commands.describe(
        image="Image file to analyze (PNG, JPEG, WebP, GIF, HEIC)",
        question="What to ask about the image (optional)",
    )
    async def analyze_image_cmd(
        interaction: discord.Interaction,
        image: discord.Attachment,
        question: str = "Describe this image in detail. Note any text, errors, or important information.",
    ):

        mime = (image.content_type or "").split(";")[0].strip()
        if mime not in SUPPORTED_IMAGE_MIMES:
            await interaction.response.send_message(
                f"❌ Unsupported file type `{mime or 'unknown'}`. "
                "Supported: PNG, JPEG, WebP, GIF, HEIC",
                ephemeral=True,
            )
            return

        if image.size > MAX_FILE_SIZE:
            await interaction.response.send_message("❌ Image too large (max 20 MB).", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            session = _get_http_session()
            async with session.get(image.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Could not download image (HTTP {resp.status}).")
                    return
                image_bytes = await resp.read()
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to fetch image: {e}")
            return

        result = await llm_analyze_image(image_bytes, mime, question)
        result = truncate_for_embed(result)

        embed = discord.Embed(
            title="🖼️ Image Analysis",
            description=result,
            color=discord.Color.purple(),
        )
        embed.set_footer(text=f"📎 {image.filename} • via Gemini Vision")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "analyze-image", detail=f"{image.filename} q={question[:60]}")

    @bot.tree.command(name="analyze-file", description="Analyze a document or file using Gemini AI")
    @app_commands.describe(
        file="File to analyze (PDF, TXT, JSON, CSV, YAML, log files, etc.)",
        question="What to ask about the document (optional)",
    )
    async def analyze_file_cmd(
        interaction: discord.Interaction,
        file: discord.Attachment,
        question: str = "Summarize this document and highlight the most important information.",
    ):

        if file.size > MAX_FILE_SIZE:
            await interaction.response.send_message("❌ File too large (max 20 MB).", ephemeral=True)
            return

        filename = file.filename.lower()
        mime = (file.content_type or "").split(";")[0].strip()

        await interaction.response.defer()

        try:
            session = _get_http_session()
            async with session.get(file.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"❌ Could not download file (HTTP {resp.status}).")
                    return
                file_bytes = await resp.read()
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to download file: {e}")
            return

        extracted_text: str | None = None
        file_type_label = "text"

        if filename.endswith(".pdf") or mime == "application/pdf":
            file_type_label = "PDF"
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                pages_text = []
                for page in reader.pages[:PDF_MAX_PAGES]:
                    page_text = page.extract_text()
                    if page_text:
                        pages_text.append(page_text)
                extracted_text = "\n\n".join(pages_text)
                if not extracted_text.strip():
                    await interaction.followup.send(
                        "⚠️ Could not extract text from this PDF (may be scanned/image-based)."
                    )
                    return
            except ImportError:
                await interaction.followup.send(
                    "❌ pypdf not installed. Add `pypdf>=4.0` to requirements.txt."
                )
                return
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to parse PDF: {e}")
                return
        else:
            file_type_label = filename.rsplit(".", 1)[-1].upper() if "." in filename else "text"
            try:
                extracted_text = file_bytes.decode("utf-8", errors="replace")
            except Exception as e:
                await interaction.followup.send(f"❌ Could not decode file as text: {e}")
                return

        del file_bytes

        MAX_CHARS = DOCUMENT_MAX_CHARS
        truncated = False
        if len(extracted_text) > MAX_CHARS:
            extracted_text = extracted_text[:MAX_CHARS]
            truncated = True

        result = await llm_analyze_document(extracted_text, question)
        result = truncate_for_embed(result)

        embed = discord.Embed(
            title=f"📄 {file_type_label} Analysis",
            description=result,
            color=discord.Color.dark_blue(),
        )
        footer = f"📎 {file.filename} ({file.size // 1024} KB)"
        if truncated:
            footer += " • ⚠️ truncated to 50,000 chars"
        embed.set_footer(text=footer + " • via Gemini")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "analyze-file", detail=f"{file.filename} q={question[:60]}")

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
            except Exception as e:
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

    # ------------------------------------------------------------------
    # /diff
    # ------------------------------------------------------------------

    @bot.tree.command(name="diff", description="Show uncommitted git changes in the OpenClaw repo")
    @require_auth
    async def diff_cmd(interaction: discord.Interaction):
        await interaction.response.defer()
        status, diff = await asyncio.gather(git_status(), git_diff())
        description = f"**Status**\n```\n{status[:800]}\n```\n**Diff**\n```diff\n{diff[:2600]}\n```"
        description = truncate_for_embed(description)
        embed = discord.Embed(
            title="🔀 Git Changes",
            description=description,
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Run /ask \"commit these changes\" to commit via LLM")
        await interaction.followup.send(embed=embed)
        audit_log(interaction.user, "diff")

    # ------------------------------------------------------------------
    # /briefing
    # ------------------------------------------------------------------

    @bot.tree.command(name="briefing", description="Generate an on-demand morning briefing (weather, health, downloads, calendar)")
    @require_auth
    async def briefing_cmd(interaction: discord.Interaction):
        if not llm_is_configured():
            await interaction.response.send_message("⚠️ LLM not configured.", ephemeral=True)
            return
        await interaction.response.defer()
        await send_morning_briefing(bot, channel_override=interaction.channel)
        try:
            await interaction.edit_original_response(content="✅ Briefing posted above.")
        except Exception as exc:
            log.debug("Briefing edit_original_response failed: %s", exc)
        audit_log(interaction.user, "briefing")

    # ------------------------------------------------------------------
    # /imagine
    # ------------------------------------------------------------------

    @bot.tree.command(name="imagine", description="Generate an image using local Stable Diffusion (free, on-device)")
    @app_commands.describe(
        prompt="Describe the image you want to generate",
        negative="Things to avoid in the image (optional)",
        width="Image width in pixels (default: 1024, max: 1536)",
        height="Image height in pixels (default: 1024, max: 1536)",
        steps="Inference steps — higher = better quality, slower (default: 20)",
    )
    @require_auth
    async def imagine_cmd(
        interaction: discord.Interaction,
        prompt: str,
        negative: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
    ):
        await interaction.response.defer()

        if not await sd_is_available():
            await interaction.edit_original_response(
                content=(
                    "⚠️ **Stable Diffusion service is not running.**\n"
                    "Start it on the host with: `python scripts/sd_server.py`\n"
                    "Or set `SD_URL` env var to point to your SD API."
                )
            )
            return

        await interaction.edit_original_response(
            content=f"🎨 *Generating image…* ({width}×{height}, {steps} steps)\nPrompt: `{prompt[:100]}`"
        )

        image_bytes, img_status = await generate_image(
            prompt,
            negative_prompt=negative,
            width=width,
            height=height,
            steps=steps,
        )

        if image_bytes is None:
            await interaction.edit_original_response(content=f"❌ Image generation failed: {img_status}")
            return

        embed = discord.Embed(
            title="🎨 Generated Image",
            description=f"**Prompt:** {prompt[:200]}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{width}×{height} · {steps} steps · local Stable Diffusion")
        img_file = discord.File(io.BytesIO(image_bytes), filename="openclaw_generated.png")
        embed.set_image(url="attachment://openclaw_generated.png")

        await interaction.edit_original_response(content=None, embed=embed, attachments=[img_file])
        audit_log(interaction.user, "imagine", detail=prompt[:200])

    # ------------------------------------------------------------------
    # /run-code
    # ------------------------------------------------------------------

    @bot.tree.command(name="run-code", description="Execute Python code in a sandboxed container (safe, isolated)")
    @app_commands.describe(
        code="Python code to run (or wrap in a code block ```python ... ```)",
    )
    @require_auth
    async def run_code_cmd(interaction: discord.Interaction, code: str):
        await interaction.response.defer()

        if code.startswith("```"):
            lines = code.split("\n")
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            elif lines[0].strip().startswith("```"):
                lines[0] = ""
            code = "\n".join(lines).strip()

        if not code:
            await interaction.edit_original_response(content="❌ No code provided.")
            return

        if len(code) > 10_000:
            await interaction.edit_original_response(content="❌ Code too long (max 10,000 chars).")
            return

        await interaction.edit_original_response(content="⚙️ *Running code in sandboxed container…*")

        stdout, stderr, exit_code = await sandbox_run_code(code)

        parts = []
        if stdout:
            parts.append(f"**stdout:**\n```\n{stdout[:OUTPUT_MAX_CHARS]}\n```")
        if stderr:
            parts.append(f"**stderr:**\n```\n{stderr[:1500]}\n```")
        if not stdout and not stderr:
            parts.append("*(no output)*")

        code_status = "✅" if exit_code == 0 else "❌"
        header = f"{code_status} Exit code: {exit_code}"

        embed = discord.Embed(
            title="⚙️ Code Execution Result",
            description=f"{header}\n\n" + "\n".join(parts),
            color=discord.Color.green() if exit_code == 0 else discord.Color.red(),
        )
        embed.set_footer(text="Sandboxed · python:3.12-slim · no network · 256MB RAM · 30s timeout")

        out_file = None
        if len(stdout) > OUTPUT_MAX_CHARS:
            out_file = discord.File(io.BytesIO(stdout.encode()), filename="output.txt")

        from typing import Any
        kwargs: dict[str, Any] = {"content": None, "embed": embed}
        if out_file:
            kwargs["attachments"] = [out_file]
        await interaction.edit_original_response(**kwargs)
        audit_log(interaction.user, "run_code", detail=code[:200])

    log.info("Registered %d standalone slash commands", 30)
