"""
OpenClaw Discord Bot - Phase 6: Remote Access & Monitoring
Autonomous AI agent for home automation and system management.

This is the core bot file — init, /ask command, and entry point.
Slash commands live in discord_commands.py, background tasks in
discord_background.py, and the web/health server in discord_web.py.
"""

import asyncio
import datetime
import io
import json
import logging
import os
import re
import sys
import threading
import time
import pathlib
from pathlib import Path
from typing import Any

import aiofiles
import discord
import yaml
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from agent_loop import scan_interrupted as scan_interrupted_plans
from agentmail import send_agent_mail
from approvals import is_emergency_stopped
from ask_orchestrator import (
    apply_repair_budget,
    get_latency_load_snapshot,
    normalize_model_preference,
    run_ask_stream,
    select_latency_budget_policy,
)
from config import cfg
from constants import (
    EMBED_DESC_LIMIT,
    EMBED_SPLIT_LIMIT,
    MAX_FILE_SIZE,
)
from llm import SUPPORTED_IMAGE_MIMES, get_rate_info
from llm import chat as llm_chat
from llm import chat_stream as llm_chat_stream
from llm import is_configured as llm_is_configured
from memory import get_model_preference
from memory import store as conversation_store
from permissions import (  # noqa: F401 — re-exported for backward compat
    ALLOWED_USER_IDS,
    is_allowed,
    is_service_allowed,
    require_auth,
)
from qmd import remember_fact
from runtime_state import (
    get_anchor_state,
    get_effective_channel_profile,
    request_context,
    reset_anchor_state,
    reset_context_lock,
    resolve_context_lock,
    set_anchor_state,
    set_bot,
    set_context_lock,
)
from scheduler import scheduler
from skills import SKILLS
from trace_context import get_trace_id, setup_trace_logging

# --- Refactored modules ---
from feedback_guardrails import (
    _FEEDBACK_CHANNEL_EVENTS,
    _FEEDBACK_CHANNEL_RATE_LIMIT_MAX,
    _FEEDBACK_CHANNEL_RATE_LIMIT_WINDOW_SECONDS,
    _FEEDBACK_DEDUPE_WINDOW_SECONDS,
    _FEEDBACK_GUARDRAIL_LOCK,
    _FEEDBACK_RECENT_EVENTS,
    _FEEDBACK_USER_EVENTS,
    _FEEDBACK_USER_RATE_LIMIT_MAX,
    _FEEDBACK_USER_RATE_LIMIT_WINDOW_SECONDS,
    _apply_feedback_guardrails,
    _prune_feedback_event_buffer,
    _reset_feedback_guardrails_for_tests,
)
from quality_helpers import (
    _append_explainability_footer,
    _build_ask_context_controls,
    _build_ask_failure_message,
    _build_ask_recovery_block,
    _build_ask_timeout_message,
    _build_coverage_summary_for_embed,
    _build_quality_broadening_prompt,
    _classify_ask_failure,
    _count_markdown_table_items,
    _explainability_note_from_meta,
    _extract_distinct_source_domains,
    _extract_reported_evidence_completeness,
    _extract_requested_item_count,
    _quality_retry_improved,
    _QUALITY_RETRY_MAX_ATTEMPTS,
    _QUALITY_RETRY_TIMEOUT_SECONDS,
    _record_budget_policy_metric,
    _record_quality_metric,
    _run_quality_auto_repair,
    _safe_score_answer_quality,
    _score_answer_quality,
    _should_prefer_file_for_multichunk_response,
    _with_requested_item_target,
    _UNCERTAINTY_MARKERS,
    _FRESHNESS_MARKERS,
    _EVIDENCE_COMPLETENESS_RE,
    _REQUESTED_ITEMS_PREFIX_RE,
    _REQUESTED_ITEMS_BARE_RE,
)
from response_actions import ResponseActions, _generate_follow_ups
from bot_formatting import truncate_for_embed

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/audit"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/logs"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))

# ---------------------------------------------------------------------------
# Channel role architecture — prevents context bleed between workflows
# ---------------------------------------------------------------------------

_CHANNEL_ROLES: dict[int, str] = {}
_CHANNEL_PROMPTS: dict[str, str] = {}
_DEFAULT_ASK_THREAD_CACHE: dict[tuple[int, int, int], tuple[int, float]] = {}
_DEFAULT_ASK_THREAD_CACHE_TTL_SECONDS = 60 * 60 * 24
_MESSAGE_CONTENT_HINT_CACHE: dict[int, float] = {}
_MESSAGE_CONTENT_HINT_COOLDOWN_SECONDS = 60 * 30


def _resolve_channel_thread_scope(
    channel: Any,
    channel_id: int | None,
    *,
    user_id: int | str | None = None,
) -> tuple[int | None, int | None]:
    """Normalize Discord channel/thread into (channel_id, thread_id) scope."""
    resolved_channel_id = channel_id
    resolved_thread_id = None
    if isinstance(channel, discord.Thread):
        resolved_thread_id = channel.id
        if channel.parent_id:
            resolved_channel_id = channel.parent_id
    lock, _ = resolve_context_lock(
        user_id=user_id,
        channel_id=resolved_channel_id,
        thread_id=resolved_thread_id,
    )
    if lock and lock.get("mode") in {"channel", "thread", "prior_report"}:
        if lock.get("channel_id"):
            resolved_channel_id = int(lock["channel_id"])
        if lock.get("mode") in {"thread", "prior_report"}:
            resolved_thread_id = int(lock["thread_id"]) if lock.get("thread_id") is not None else None
        elif lock.get("mode") == "channel":
            resolved_thread_id = None
    return resolved_channel_id, resolved_thread_id


async def _load_channel_config() -> None:
    """Load channel roles from config.yaml and map them to env-provided IDs."""
    global _CHANNEL_ROLES, _CHANNEL_PROMPTS
    config_file = CONFIG_DIR / "config.yaml"
    if config_file.exists():
        try:
            async with aiofiles.open(config_file) as f:
                content = await f.read()
                cfg_yaml = yaml.safe_load(content) or {}
            roles = cfg_yaml.get("channels", {}).get("roles", {})
            for role_name, role_cfg in roles.items():
                prompt = role_cfg.get("prompt_override", "")
                if prompt:
                    _CHANNEL_PROMPTS[role_name] = prompt
        except Exception as e:
            log.warning("Failed to load channel config: %s", e)

    for role in ("research", "analytics", "bookmarks", "real_estate"):
        raw = os.getenv(f"DISCORD_CHANNEL_{role.upper()}_ID", "0")
        try:
            cid = int(raw)
            if cid:
                _CHANNEL_ROLES[cid] = role
        except ValueError:
            pass

    if _CHANNEL_ROLES:
        role_summary = {v: k for k, v in _CHANNEL_ROLES.items()}
        log.info("Channel roles loaded: %s", role_summary)
    else:
        log.info("No channel role IDs configured (DISCORD_CHANNEL_<ROLE>_ID not set)")


VERSION = cfg.version

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "openclaw.log"),
    ],
)
log = logging.getLogger("openclaw")
setup_trace_logging()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_user_allowed(user_id: int) -> bool:
    """Return True when *user_id* is in the configured allow-list."""
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def _bot_can_read_channel(channel: Any) -> bool:
    """Best-effort check that the bot has read access to *channel*."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return True
    permissions_for = getattr(channel, "permissions_for", None)
    if not callable(permissions_for):
        return False
    bot_member = getattr(guild, "me", None)
    if bot_member is None and bot.user is not None and hasattr(guild, "get_member"):
        bot_member = guild.get_member(bot.user.id)
    if bot_member is None:
        return True
    perms = permissions_for(bot_member)
    return bool(getattr(perms, "read_messages", getattr(perms, "view_channel", False)))


def _should_send_message_content_hint(channel: Any) -> bool:
    """Rate-limit message-content intent hints to avoid channel spam."""
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return False
    now = time.time()
    last_sent = _MESSAGE_CONTENT_HINT_CACHE.get(int(channel_id), 0.0)
    if now - last_sent < _MESSAGE_CONTENT_HINT_COOLDOWN_SECONDS:
        return False
    _MESSAGE_CONTENT_HINT_CACHE[int(channel_id)] = now
    return True


def _default_ask_thread_cache_key(channel: Any, user_id: int) -> tuple[int, int, int]:
    guild_id = 0
    guild = getattr(channel, "guild", None)
    if guild is not None and getattr(guild, "id", None):
        guild_id = int(guild.id)
    return guild_id, int(channel.id), int(user_id)


def _default_ask_thread_user_tag(user_id: int) -> str:
    return f"u{int(user_id)}"


def _build_default_ask_thread_name(user_question: str, user_id: int) -> str:
    snippet = re.sub(r"\s+", " ", (user_question or "").strip())
    if not snippet:
        snippet = "conversation"
    snippet = snippet[:50].strip()
    if len(snippet) == 50:
        snippet += "…"
    tag = _default_ask_thread_user_tag(user_id)
    name = f"💬 {snippet} · {tag}"
    if len(name) > 100:
        keep = max(1, 100 - len(f" · {tag}") - 1)
        name = f"💬 {snippet[:keep].rstrip()} · {tag}"
    return name


def _is_reusable_bot_thread(candidate: Any, *, parent_channel_id: int) -> bool:
    if not isinstance(candidate, discord.Thread):
        return False
    if bot.user is None:
        return False
    if getattr(candidate, "owner_id", None) != bot.user.id:
        return False
    if getattr(candidate, "parent_id", None) != parent_channel_id:
        return False
    if bool(getattr(candidate, "archived", False)):
        return False
    if bool(getattr(candidate, "locked", False)):
        return False
    return True


def _remember_default_ask_thread(channel: Any, user_id: int, thread_id: int) -> None:
    _DEFAULT_ASK_THREAD_CACHE[_default_ask_thread_cache_key(channel, user_id)] = (thread_id, time.time())


def _pick_most_recent_thread(candidates: list[discord.Thread]) -> discord.Thread:
    def _thread_sort_key(thread: discord.Thread) -> int:
        last_msg = getattr(thread, "last_message_id", None)
        try:
            return int(last_msg or thread.id)
        except Exception:
            return int(thread.id)

    return sorted(candidates, key=_thread_sort_key, reverse=True)[0]


async def _get_or_create_default_ask_thread(
    channel: Any,
    *,
    user_id: int,
    user_question: str,
) -> tuple[discord.Thread | None, bool]:
    """Return (thread, created_new) for top-level default ask routing."""
    if (
        not cfg.thread_auto_create
        or isinstance(channel, discord.DMChannel)
        or not hasattr(channel, "create_thread")
        or bot.user is None
    ):
        return None, False

    key = _default_ask_thread_cache_key(channel, user_id)
    cached = _DEFAULT_ASK_THREAD_CACHE.get(key)
    if cached:
        thread_id, last_seen = cached
        if time.time() - last_seen <= _DEFAULT_ASK_THREAD_CACHE_TTL_SECONDS:
            candidate = bot.get_channel(thread_id)
            if candidate is None:
                guild = getattr(channel, "guild", None)
                get_thread = getattr(guild, "get_thread", None)
                if callable(get_thread):
                    candidate = get_thread(thread_id)
            if _is_reusable_bot_thread(candidate, parent_channel_id=int(channel.id)):
                _remember_default_ask_thread(channel, user_id, int(candidate.id))
                return candidate, False
        else:
            _DEFAULT_ASK_THREAD_CACHE.pop(key, None)

    user_tag = _default_ask_thread_user_tag(user_id)
    channel_threads = getattr(channel, "threads", None)
    if channel_threads is not None:
        matching_threads = [
            thread
            for thread in list(channel_threads)
            if _is_reusable_bot_thread(thread, parent_channel_id=int(channel.id))
            and user_tag in str(getattr(thread, "name", ""))
        ]
        if matching_threads:
            chosen = _pick_most_recent_thread(matching_threads)
            _remember_default_ask_thread(channel, user_id, int(chosen.id))
            return chosen, False

    try:
        archive_duration = 60 if cfg.thread_archive_minutes <= 60 else 1440
        created = await channel.create_thread(
            name=_build_default_ask_thread_name(user_question, user_id),
            auto_archive_duration=archive_duration,
            reason=f"Auto-threaded default ask for user {user_id}",
        )
        _remember_default_ask_thread(channel, user_id, int(created.id))
        return created, True
    except Exception as exc:
        log.debug("Default ask auto-thread creation failed: %s", exc)
        return None, False


# ---------------------------------------------------------------------------
# Audit logger — uses shared audit module; buffer lives in audit.py
# ---------------------------------------------------------------------------

AUDIT_DIR.mkdir(parents=True, exist_ok=True)

from audit import _audit_buffer, audit_log  # noqa: E402
from constants import HTTP_TIMEOUT_DEFAULT

# ---------------------------------------------------------------------------
# Shared HTTP session (reused for attachment downloads)
# ---------------------------------------------------------------------------
from http_session import SessionManager as _SessionManager

_bot_sessions = _SessionManager(timeout=HTTP_TIMEOUT_DEFAULT, name="bot")


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True


class OpenClawBot(commands.Bot):
    """Discord bot with slash commands, cog extensions, and app-command tree."""

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.start_time = time.monotonic()
        self._health_runner = None

    async def setup_hook(self) -> None:
        """Load cogs dynamically, register commands, and sync on startup."""
        cogs_dir = Path(__file__).parent / "cogs"
        loaded: list[str] = []
        for cog_file in sorted(cogs_dir.glob("*_cog.py")):
            if cog_file.name.startswith("_"):
                continue
            module = f"cogs.{cog_file.stem}"
            try:
                await self.load_extension(module)
                loaded.append(module)
                log.info("Loaded cog: %s", module)
            except Exception as e:
                log.error("Failed to load cog %s: %s", module, e)
        log.info("Loaded %d cogs: %s", len(loaded), ", ".join(loaded))

        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to guild %s", DISCORD_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Synced commands globally")

        # Start health-check HTTP server
        from discord_web import start_health_server
        self._health_runner = await start_health_server(self)

        # Start background metrics collector for Prometheus export updates
        try:
            from metrics_collector import start_metrics_collector

            await start_metrics_collector()
        except Exception as exc:
            log.warning("Failed to start metrics collector: %s", exc)

    async def on_ready(self) -> None:
        """Initialize bot on connection to Discord.

        Sets up:
        - Channel configurations
        - Skill scheduler with daily maintenance
        - Background health monitors (container status, log scanning)
        - Proactive monitoring loops (every 2 hours)
        - GitHub workflow tracking for failed jobs
        """
        log.info("OpenClaw online as %s (ID %s)", self.user, self.user.id)
        audit_log(None, "bot_ready", f"Logged in as {self.user}")
        set_bot(self)

        await _load_channel_config()

        scheduler.register_skills(SKILLS)
        scheduler.start()
        log.info("Scheduler started with %d registered skills", len(SKILLS))

        # Register recurring cron jobs (idempotent)
        existing_actions = {t.action for t in scheduler.list_tasks()}
        if "run_maintenance" not in existing_actions:
            scheduler.create(
                action="run_maintenance", args={}, hour=4, minute=0,
                created_by="system", notify_channel_id=ALERT_CHANNEL_ID, alert_only=False,
            )
            log.info("Registered 4:00 AM maintenance cron job")
        if "index_vault_to_qmd" not in existing_actions:
            scheduler.create(
                action="index_vault_to_qmd", args={}, hour=3, minute=50,
                created_by="system", notify_channel_id=0, alert_only=False,
            )
            log.info("Registered 3:50 AM vault indexer cron job")

        # Register Patreon monitoring (every 30 minutes)
        patreon_tasks = [t for t in scheduler.list_tasks() if "patreon" in t.action.lower()]
        if not patreon_tasks:
            # Import and register the monitoring function
            from patreon_scheduled import scheduled_patreon_health_check
            scheduler.register_skills({"patreon_health_check": scheduled_patreon_health_check})
            scheduler.create(
                action="patreon_health_check",
                args={
                    "discord_client": self,
                    "alert_channel_id": ALERT_CHANNEL_ID,
                },
                interval_minutes=30,
                created_by="system",
                notify_channel_id=0,  # Don't notify - alerts handled internally
                alert_only=False,
            )
            log.info("Registered Patreon health monitoring (every 30 minutes)")

        # Wire scheduler -> Discord notification callback
        async def _scheduler_notify(task_id: str, action: str, result: str, is_alert: bool) -> None:
            task = scheduler.get(task_id)
            if task is None:
                return
            channel = self.get_channel(task.notify_channel_id)
            if channel is None:
                return
            color = discord.Color.red() if is_alert else discord.Color.green()
            icon = "🚨" if is_alert else "✅"
            chunks = _split_response(result) if result else ["(no output)"]
            try:
                for idx, chunk in enumerate(chunks):
                    embed = discord.Embed(
                        title=f"{icon} Watch Alert: `{action}`" if idx == 0 else None,
                        description=chunk,
                        color=color,
                    )
                    if idx == len(chunks) - 1:
                        embed.set_footer(text=f"Task {task_id} • {action}")
                    await channel.send(embed=embed)
            except Exception as e:
                log.error("Failed to post scheduler result for %s: %s", task_id, e)

        scheduler.notify_callback = _scheduler_notify

        # Start background tasks (cleanup, audit writer, proactive loops)
        from discord_background import start_background_tasks

        active_background_loops = 0
        try:
            active_background_loops = int(start_background_tasks(self))
        except Exception as exc:
            log.error("Failed to start background task supervisor: %s", exc)

        if active_background_loops <= 0:
            log.warning(
                "Background loops unavailable; proactive monitoring is disabled until restart",
            )
            if ALERT_CHANNEL_ID:
                alert_channel = self.get_channel(ALERT_CHANNEL_ID)
                if alert_channel is not None:
                    try:
                        await alert_channel.send(
                            "⚠️ Background monitoring loops failed to start. "
                            "Slash commands remain online, but proactive alerts are paused.",
                        )
                    except Exception as exc:
                        log.debug("Failed to post background-loop warning: %s", exc)

        # Set bot presence/activity
        container_count = len(self.guilds)
        try:
            from skills import list_containers
            result = await list_containers()
            container_count = len([ln for ln in result.split("\n") if ln.strip() and not ln.startswith("NAMES")])
        except (ImportError, RuntimeError, ConnectionError):
            # list_containers may fail if Docker socket unavailable; use guild count as fallback
            pass
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{container_count} containers | /ask",
            )
        )

        # Scan for interrupted plans from previous runs
        if ALERT_CHANNEL_ID:
            interrupted = scan_interrupted_plans()
            if interrupted:
                channel = self.get_channel(ALERT_CHANNEL_ID)
                if channel:
                    names = ", ".join(f"`{p.plan_id}`" for p in interrupted[:5])
                    try:
                        await channel.send(
                            f"🔄 Found **{len(interrupted)}** interrupted plan(s) from a previous session: {names}\n"
                            f"Use `/resume <plan_id>` to continue, or `/plans` to review."
                        )
                    except Exception as e:
                        log.warning("Failed to post interrupted plan notice: %s", e)
                log.info("Found %d interrupted plan(s) on startup", len(interrupted))

    async def close(self) -> None:
        """Graceful shutdown: flush audit log, close sessions, stop health server."""
        from discord_background import stop_background_tasks

        await stop_background_tasks()

        if _audit_buffer:
            entries = list(_audit_buffer)
            _audit_buffer.clear()
            today = datetime.date.today().isoformat()
            audit_file = AUDIT_DIR / f"{today}.jsonl"
            try:
                async with aiofiles.open(audit_file, "a") as f:
                    for e in entries:
                        await f.write(json.dumps(e) + "\n")
            except Exception as exc:
                log.warning("Failed to flush audit buffer on shutdown: %s", exc)

        _close_fns = [
            ("llm", lambda: __import__("llm").close_sessions()),
            ("agentmail", lambda: __import__("agentmail").close_session()),
            ("nas", lambda: __import__("nas").close_session()),
            ("http_sessions", lambda: __import__("http_session").close_all()),
        ]
        for name, fn in _close_fns:
            try:
                await fn()
            except Exception as exc:
                log.debug("close %s: %s", name, exc)
        try:
            from metrics_collector import stop_metrics_collector

            await stop_metrics_collector()
        except Exception as exc:
            log.debug("close metrics_collector: %s", exc)
        if self._health_runner:
            await self._health_runner.cleanup()
        await super().close()


bot = OpenClawBot()

# ---------------------------------------------------------------------------
# Register standalone slash commands (everything except /ask)
# ---------------------------------------------------------------------------

from discord_commands import register_commands  # noqa: E402

register_commands(bot)

# ---------------------------------------------------------------------------
# /ask command and helpers (core — stays in bot.py)
# ---------------------------------------------------------------------------


async def _send_app_command_error_message(
    interaction: discord.Interaction,
    message: str,
) -> None:
    """Safely send slash-command error output regardless of response state."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Global fallback for non-cog app-command errors."""
    command_name = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
    user_id = getattr(getattr(interaction, "user", None), "id", "unknown")
    channel_id = getattr(interaction, "channel_id", "unknown")
    guild_id = getattr(interaction, "guild_id", "dm")

    if isinstance(error, app_commands.CheckFailure):
        user_message = str(error).strip() or "⛔ You don't have permission to use this command."
        log.warning(
            "App command check failed command=%s user_id=%s channel_id=%s guild_id=%s error=%r",
            command_name,
            user_id,
            channel_id,
            guild_id,
            error,
        )
    elif isinstance(error, app_commands.TransformerError):
        user_message = "⚠️ I couldn't parse one of your inputs. Please check the command options and try again."
        log.warning(
            "App command transformer error command=%s user_id=%s channel_id=%s guild_id=%s error=%r",
            command_name,
            user_id,
            channel_id,
            guild_id,
            error,
        )
    elif isinstance(error, app_commands.CommandInvokeError):
        original = getattr(error, "original", error)
        if isinstance(original, (asyncio.TimeoutError, TimeoutError)):
            user_message = "⏱️ This command timed out before it finished. Please try again."
            log.warning(
                "App command timeout command=%s user_id=%s channel_id=%s guild_id=%s error=%r",
                command_name,
                user_id,
                channel_id,
                guild_id,
                original,
            )
        else:
            user_message = "⚠️ Something went wrong while running that command. Please try again."
            log.exception(
                "App command invoke error command=%s user_id=%s channel_id=%s guild_id=%s",
                command_name,
                user_id,
                channel_id,
                guild_id,
                exc_info=original,
            )
    else:
        user_message = "⚠️ Something went wrong while handling that command. Please try again."
        log.exception(
            "Unhandled app command error command=%s user_id=%s channel_id=%s guild_id=%s",
            command_name,
            user_id,
            channel_id,
            guild_id,
            exc_info=error,
        )

    try:
        await _send_app_command_error_message(interaction, user_message)
    except Exception as send_exc:
        log.exception(
            "Failed to send app command error response command=%s user_id=%s channel_id=%s guild_id=%s",
            command_name,
            user_id,
            channel_id,
            guild_id,
            exc_info=send_exc,
        )

_EMBED_LIMIT = EMBED_SPLIT_LIMIT
_FILE_THRESHOLD = 8000

# ---------------------------------------------------------------------------
# Message formatting (extracted to bot_formatting.py)
# ---------------------------------------------------------------------------

from bot_formatting import (
    build_attachment_embed_summary as _build_attachment_embed_summary,
    extract_file_attachment as _extract_file_attachment,
    extract_image_url as _extract_image_url,
    format_markdown_for_discord as _format_markdown_for_discord,
    format_tables_for_context as _format_tables_for_context,
    should_package_as_attachment as _should_package_as_attachment,
    split_response as _split_response,
)

_STREAM_EDIT_INTERVAL = 3.0


from bot_attachments import (
    handle_doc_attachment as _handle_doc_attachment,
)
from bot_attachments import (
    handle_image_attachment as _handle_image_attachment,
)


@bot.tree.command(name="ask", description="Ask OpenClaw anything (AI-powered with function calling)")
@app_commands.describe(
    question="Your question or request",
    attachment="Optional image or document to include in your question",
    model="LLM routing: auto (smart), local (Gemma), gemini (cloud), openai (GPT-4o), or anthropic (Claude). Alias: claude → anthropic",
    scope="Context scope: current channel/thread, cross-channel, or prior-report anchor mode",
    reset_context="Reset the current anchor context before recall (optional)",
    anchor="Optional anchor override ID. Use 'none' to disable anchor targeting",
)
@app_commands.choices(
    model=[
        app_commands.Choice(name="🔄 Auto (Copilot → Gemini)", value="auto"),
        app_commands.Choice(name="🏠 Local (Gemma/Ollama)", value="local"),
        app_commands.Choice(name="☁️ Gemini (cloud)", value="gemini"),
        app_commands.Choice(name="🟢 OpenAI (GPT-4o)", value="openai"),
        app_commands.Choice(name="🟣 Anthropic (Claude)", value="anthropic"),
    ],
    scope=[
        app_commands.Choice(name="Current channel/thread", value="current"),
        app_commands.Choice(name="Cross-channel recall", value="cross-channel"),
        app_commands.Choice(name="Prior report anchor", value="prior-report"),
    ],
)
async def ask_cmd(
    interaction: discord.Interaction,
    question: str,
    attachment: discord.Attachment | None = None,
    model: app_commands.Choice[str] | None = None,
    scope: app_commands.Choice[str] | None = None,
    reset_context: bool | None = None,
    anchor: str | None = None,
) -> None:
    """Main user query handler — routes to Gemini (tool-capable) or Ollama (conversational)."""

    if is_emergency_stopped():
        await interaction.response.send_message(
            "🛑 **Emergency stop is active.** `/ask` is disabled. Use `/estop resume` to resume.",
            ephemeral=True,
        )
        return

    if not llm_is_configured():
        await interaction.response.send_message(
            "⚠️ LLM not configured. Set `GOOGLE_API_KEY` in your `.env` file.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    _ask_start = time.monotonic()

    # Set up request tracing
    from trace_context import TraceContext, _current_trace
    _trace = TraceContext(command="ask", user_id=interaction.user.id,
                          channel_id=interaction.channel_id)
    _trace_token = _current_trace.set(_trace)
    log.info("ask_cmd start question=%.80s", question)
    context_channel_id, context_thread_id = _resolve_channel_thread_scope(
        interaction.channel,
        interaction.channel_id,
        user_id=interaction.user.id,
    )

    _progress_lines: list[str] = []
    _progress_start = time.monotonic()

    async def _think(status: str) -> None:
        elapsed = time.monotonic() - _progress_start
        _progress_lines.append(f"💭 {status} ({elapsed:.0f}s)")
        progress = "\n".join(_progress_lines) + "\n\n⏳ *thinking…*"
        try:
            embed = discord.Embed(description=progress, color=discord.Color.dark_grey())
            embed.set_author(
                name=f"Replying to: {question[:100]}",
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
            )
            await interaction.edit_original_response(content=None, embed=embed)
        except Exception as exc:
            log.debug("Progress edit failed: %s", exc)

    async def _on_tool_call(tool_name: str, round_num: int, *, args: dict | None = None, result_preview: str | None = None) -> None:
        elapsed = time.monotonic() - _progress_start
        if result_preview is not None:
            _progress_lines.append(f"✅ `{tool_name}` → {result_preview[:80]}")
        elif args is not None:
            args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
            _progress_lines.append(f"🔄 Using `{tool_name}({args_str})`… ({elapsed:.0f}s)")
        else:
            _progress_lines.append(f"🔄 Using `{tool_name}`… ({elapsed:.0f}s)")
        progress = "\n".join(_progress_lines) + "\n\n⏳ *working…*"
        try:
            embed = discord.Embed(
                description=progress,
                color=discord.Color.dark_grey(),
            )
            embed.set_author(
                name=f"Replying to: {question[:100]}",
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
            )
            await interaction.edit_original_response(content=None, embed=embed)
        except Exception as exc:
            log.debug("Failed to update tool progress: %s", exc)

    # If an attachment was provided, route through the appropriate analyzer
    if attachment:
        mime = (attachment.content_type or "").split(";")[0].strip()
        if mime in SUPPORTED_IMAGE_MIMES and attachment.size <= MAX_FILE_SIZE:
            question = await _handle_image_attachment(attachment, question)
        elif attachment.size > MAX_FILE_SIZE:
            log.info("ask_cmd: attachment too large (%d bytes), skipping", attachment.size)
        else:
            question = await _handle_doc_attachment(attachment, question)

    from llm.context import _extract_cross_channel_opt_in
    retrieval_question, cross_channel_retrieval = _extract_cross_channel_opt_in(question)

    # Get or create conversation context
    conv = conversation_store.get(
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        user_name=str(interaction.user.display_name),
    )

    # Research thread context injection
    if isinstance(interaction.channel, discord.Thread) and not conv.history:
        thread_name = interaction.channel.name or ""
        if thread_name.startswith("Research:"):
            try:
                report_text = ""
                async for msg in interaction.channel.history(limit=20, oldest_first=True):
                    if msg.embeds:
                        for embed in msg.embeds:
                            if embed.description:
                                report_text += embed.description + "\n"
                if report_text:
                    conv.history.append({
                        "role": "model",
                        "parts": [f"[Previous Research Report]\n{report_text[:8000]}"],
                    })
                    log.info("Injected research context (%d chars) for thread: %s",
                             len(report_text), thread_name)
            except Exception as e:
                log.debug("Research context injection failed: %s", e)

    # Thread continuation suggestion
    thread_hint = ""
    if not conv.history:
        try:
            import vector_store
            hits = await vector_store.search(
                vector_store.CONVERSATIONS_COLLECTION,
                retrieval_question,
                top_k=1,
                threshold=0.75,
                channel_id=context_channel_id,
                thread_id=context_thread_id,
                cross_channel=cross_channel_retrieval,
            )
            if hits:
                meta = hits[0].get("metadata", {})
                thread_name = meta.get("thread_name", "")
                sim = hits[0].get("similarity", 0)
                if thread_name and sim >= 0.75:
                    thread_hint = (
                        f"\n\n> 💡 *This looks related to your thread "
                        f"**{thread_name}**. Use `/resume {thread_name}` to continue it.*"
                    )
        except Exception as exc:
            log.debug("Thread hint search failed: %s", exc)

    # Channel role injection
    if not conv.history:
        channel_role = _CHANNEL_ROLES.get(interaction.channel_id)
        if channel_role:
            role_prompt = _CHANNEL_PROMPTS.get(channel_role, "")
            if role_prompt:
                conv.history.append({
                    "role": "model",
                    "parts": [f"📌 *{channel_role.capitalize()} mode active.* {role_prompt}"],
                })
                log.debug("Injected %s channel role prompt for channel %d", channel_role, interaction.channel_id)

    response_text = ""
    model_used = "unknown"
    model_pref = model.value if model else get_model_preference(interaction.user.id)
    context_controls = _build_ask_context_controls(
        scope=scope.value if scope else None,
        reset_context=reset_context,
        anchor=anchor,
    )

    # Guardrail: if user picks "local" but query clearly needs tools, auto-upgrade
    from llm import _needs_tools as llm_needs_tools
    model_pref, upgraded_to_gemini = normalize_model_preference(
        question, model_pref, llm_needs_tools,
    )
    if upgraded_to_gemini:
        guardrail_note = "\n\n> ⚡ *Auto-upgraded to Gemini (your query requires tool access)*"
    else:
        guardrail_note = ""

    try:
        # Contextual recall
        await _think("Recalling relevant memories…")
        try:
            import vector_store
            context_hits = await vector_store.recall(
                retrieval_question,
                top_k=3,
                channel_id=context_channel_id,
                thread_id=context_thread_id,
                cross_channel=cross_channel_retrieval,
            )
            if context_hits:
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Relevant context from memory]\n{context_hits}"],
                })
        except Exception as e:
            log.debug("Contextual recall skipped: %s", e)

        # Inject learned rules (Phase 14A)
        await _think("Checking learned rules…")
        try:
            from rules_engine import get_relevant_rules
            rules = await get_relevant_rules(question, top_k=3)
            if rules:
                rules_block = "\n".join(f"• {r}" for r in rules)
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Learned rules — follow these]\n{rules_block}"],
                })
        except Exception as e:
            log.debug("Rules injection skipped: %s", e)

        # Inject user profile context (Phase 14C)
        try:
            from user_profile import get_profile_prompt
            profile_ctx = get_profile_prompt()
            if profile_ctx:
                conv.history.append({
                    "role": "model",
                    "parts": [profile_ctx],
                })
        except Exception as e:
            log.debug("Profile injection skipped: %s", e)

        # Streaming response with progressive Discord edits
        _routing_notes: list[str] = []
        _context_explainability_note = ""
        _final_meta: dict[str, Any] = {}
        _model_labels = {"auto": "smart routing", "local": "Gemma (local)", "gemini": "Gemini", "openai": "GPT-4o", "anthropic": "Claude"}
        await _think(f"Routing to {_model_labels.get(model_pref, model_pref)}…")
        last_edit = 0.0
        display_question = question if len(question) < 200 else question[:197] + "..."

        try:
            def _update_history(updated_history: list[dict[str, Any]]) -> None:
                conv.update_from_llm(updated_history)
                conversation_store.auto_save_thread(
                    interaction.user.id, interaction.channel_id, str(interaction.user.display_name),
                )

            async def _handle_partial_chunk(chunk_text: str) -> None:
                nonlocal last_edit
                now = time.monotonic()
                if now - last_edit < _STREAM_EDIT_INTERVAL:
                    return
                try:
                    preview = chunk_text[:_EMBED_LIMIT - 50] + "\n\n*⏳ streaming…*"
                    embed = discord.Embed(description=preview, color=discord.Color.purple())
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    await interaction.edit_original_response(content=None, embed=embed)
                    last_edit = now
                except Exception as exc:
                    log.debug("Stream edit failed: %s", exc)

            result = await run_ask_stream(
                llm_stream=llm_chat_stream,
                user_message=question,
                history=conv.history,
                user_name=str(interaction.user.display_name),
                model_preference=model_pref,
                channel_id=context_channel_id,
                thread_id=context_thread_id,
                user_id=str(interaction.user.id),
                on_tool_call=_on_tool_call,
                on_partial_chunk=_handle_partial_chunk,
                update_history=_update_history,
                context_controls=context_controls,
            )
            response_text = result.response_text
            model_used = result.model_used
            _final_meta = result.final_meta
            _final_meta = _with_requested_item_target(_final_meta, question=question)
            _context_explainability_note = _explainability_note_from_meta(_final_meta)
            _routing_notes.extend(result.routing_notes)
            if not _context_explainability_note:
                _routing_notes.extend(result.context_badges)

            quality_meta = _safe_score_answer_quality(
                response_text,
                final_meta=_final_meta,
                context="ask",
            )
            async def _run_retry_stream(retry_question: str) -> Any:
                return await run_ask_stream(
                    llm_stream=llm_chat_stream,
                    user_message=retry_question,
                    history=conv.history,
                    user_name=str(interaction.user.display_name),
                    model_preference=model_pref,
                    channel_id=context_channel_id,
                    thread_id=context_thread_id,
                    user_id=str(interaction.user.id),
                    on_tool_call=_on_tool_call,
                    on_partial_chunk=_handle_partial_chunk,
                    update_history=_update_history,
                    context_controls=context_controls,
                )

            repair_result = await _run_quality_auto_repair(
                question=question,
                response_text=response_text,
                model_used=model_used,
                final_meta=_final_meta,
                quality_meta=quality_meta,
                context="ask",
                run_retry_stream=_run_retry_stream,
                think_hook=_think,
            )
            response_text = str(repair_result["response_text"])
            model_used = str(repair_result["model_used"])
            _final_meta = dict(repair_result["final_meta"])
            retry_result = repair_result.get("retry_result")
            if retry_result is not None:
                _context_explainability_note = _explainability_note_from_meta(_final_meta)
                _routing_notes = list(retry_result.routing_notes)
                if not _context_explainability_note:
                    _routing_notes.extend(retry_result.context_badges)

            final_quality = _final_meta.get("answer_quality")
            if isinstance(final_quality, dict) and final_quality.get("status") == "low":
                _routing_notes.append("Quality: low confidence")
            recovery_block = _build_ask_recovery_block(_final_meta)
            if recovery_block and "Recovery note" not in response_text:
                response_text = f"{response_text.rstrip()}{recovery_block}"
            log.info("ask_cmd LLM done model=%s chars=%d", model_used, len(response_text))

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - _progress_start
            log.warning("LLM response timed out after %.0fs for: %.80s", elapsed, question)
            response_text = _build_ask_timeout_message(
                elapsed_seconds=elapsed,
                progress_lines=_progress_lines,
                model_pref=model_pref,
                trace_id=_trace.trace_id,
            )
            model_used = "timeout"

    except Exception as e:
        log.error("LLM error: %s", e)
        response_text = _build_ask_failure_message(
            question=question,
            model_pref=model_pref,
            trace_id=_trace.trace_id,
            category=_classify_ask_failure(str(e)),
        )
        model_used = "error"

    # Empty/useless response detection
    if response_text and model_used != "error":
        stripped = response_text.strip()
        is_empty = len(stripped) < 10
        is_echo = stripped.lower().replace("'", "").replace('"', "") == question.lower().replace("'", "").replace('"', "")[:len(stripped)]
        if is_empty or is_echo:
            log.warning("Empty/echo response detected for: %.80s (response: %.80s)", question, stripped)
            response_text = (
                f"⚠️ I wasn't able to generate a useful response for this query.\n\n"
                f"**What happened:** The model returned {'an empty response' if is_empty else 'your question echoed back'}.\n"
                f"**Trace ID:** `{_trace.trace_id}`\n"
                f"**Suggestion:** Try rephrasing, or use `/ask model:gemini` to force Gemini with tools.\n\n"
                f"```\n{question[:300]}\n```"
            )
            _routing_notes.append("Empty/echo response detected")
            model_used = "error"

    # Final response with embeds, file attachments, and action buttons
    if guardrail_note:
        response_text += guardrail_note
    if thread_hint:
        response_text += thread_hint

    # Optional image fallback for large/complex tables
    table_image_file = None
    try:
        from table_renderer import (
            extract_table_text,
            render_table_image,
            should_render_table_image,
        )
        table_text = extract_table_text(response_text)
        if table_text and should_render_table_image(table_text):
            img_bytes = render_table_image(table_text)
            if img_bytes:
                table_image_file = discord.File(io.BytesIO(img_bytes), filename="table.png")
    except Exception as e:
        log.debug("Table image rendering failed: %s", e)

    response_text = _format_markdown_for_discord(response_text)
    response_text = _format_tables_for_context(
        response_text,
        channel_id=context_channel_id,
        thread_id=context_thread_id,
    )
    if context_channel_id is not None and response_text.strip():
        anchor_id = f"ask_{int(time.time())}_{interaction.id}"
        set_anchor_state(
            int(context_channel_id),
            int(context_thread_id) if context_thread_id is not None else None,
            anchor_id,
        )
    chunks = _split_response(response_text)
    image_url = _extract_image_url(response_text)
    file_attachment = _extract_file_attachment(response_text)
    force_file_response = _should_prefer_file_for_multichunk_response(
        question=question,
        chunks=chunks,
        response_text=response_text,
    )

    # Generate follow-up questions asynchronously
    follow_ups = await _generate_follow_ups(question, response_text)
    action_view = ResponseActions(
        response_text=response_text,
        question=question,
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        thread_id=context_thread_id,
        follow_ups=follow_ups,
        bot=None,
    )

    # Auto-create thread for /ask responses (if not already in a thread/DM)
    _auto_thread = None
    if (
        cfg.thread_auto_create
        and not isinstance(interaction.channel, discord.Thread)
        and not isinstance(interaction.channel, discord.DMChannel)
        and hasattr(interaction.channel, "create_thread")
    ):
        try:
            _thread_name = question[:50].strip() + ("…" if len(question) > 50 else "")
            _archive_dur = 60 if cfg.thread_archive_minutes <= 60 else 1440
            _auto_thread = await interaction.channel.create_thread(
                name=f"💬 {_thread_name}",
                auto_archive_duration=_archive_dur,
                reason="Auto-threaded /ask conversation",
            )
            log.info("Auto-created thread '%s' for %s",
                     _auto_thread.name, interaction.user)
        except Exception as e:
            log.debug("Auto-thread creation failed: %s", e)

    def _build_footer() -> str:
        display_model = model_used.replace("models/", "") if model_used else "unknown"
        if "gemini" not in display_model.lower() and "gpt" not in display_model.lower() and "claude" not in display_model.lower():
            rate_str = "local · unlimited"
        else:
            rate_str = get_rate_info()
        if "gemma" in display_model.lower() or "ollama" in display_model.lower():
            actual_icon = "🏠"
        elif "gemini" in display_model.lower():
            actual_icon = "☁️"
        elif "gpt" in display_model.lower() or "openai" in display_model.lower():
            actual_icon = "🟢"
        elif "claude" in display_model.lower() or "anthropic" in display_model.lower():
            actual_icon = "🟣"
        else:
            actual_icon = "🔄"
        ft = f"💬 {conv.message_count} msgs | {rate_str} | {actual_icon} {display_model}"
        ft = _append_explainability_footer(ft, _context_explainability_note)
        if _routing_notes:
            ft += " | ⚠️ " + " → ".join(_routing_notes)
        return ft

    # Long-response path: send as downloadable .md file
    if len(response_text) > _FILE_THRESHOLD or force_file_response:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        md_file = discord.File(
            io.BytesIO(response_text.encode()),
            filename=f"openclaw-response-{ts}.md",
        )

        summary = _build_attachment_embed_summary(
            response_text,
            coverage_summary=_build_coverage_summary_for_embed(_final_meta),
            attachment_note="📎 **Full response attached as file**",
        )

        embed = discord.Embed(description=summary, color=discord.Color.purple())
        display_question = question if len(question) < 200 else question[:197] + "..."
        embed.set_author(
            name=f"Replying to: {display_question}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text=_build_footer())

        attachments = [md_file]
        if file_attachment:
            attachments.append(file_attachment[0])

        try:
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=attachments, view=action_view,
            )
        except discord.NotFound:
            log.warning("Interaction expired, using followup for long response file")
            await interaction.followup.send(
                embed=embed, file=md_file, view=action_view,
            )

    # Normal path: split across embeds
    else:
        # If auto-threaded, redirect responses there and leave a link in the channel
        if _auto_thread:
            await interaction.edit_original_response(
                content=f"💬 Conversation continued in {_auto_thread.mention}",
                embed=None,
            )
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                if i == 0:
                    display_question = question if len(question) < 200 else question[:197] + "..."
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    if image_url:
                        embed.set_image(url=image_url)
                is_last = i == len(chunks) - 1
                if is_last:
                    embed.set_footer(text=_build_footer())
                send_kwargs = {"embed": embed}
                if is_last:
                    send_kwargs["view"] = action_view
                if file_attachment and is_last:
                    send_kwargs["file"] = file_attachment[0]
                await _auto_thread.send(**send_kwargs)
        else:
            for i, chunk in enumerate(chunks):
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                if i == 0:
                    display_question = question if len(question) < 200 else question[:197] + "..."
                    embed.set_author(
                        name=f"Replying to: {display_question}",
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    if image_url:
                        embed.set_image(url=image_url)

                is_last = i == len(chunks) - 1
                if is_last:
                    embed.set_footer(text=_build_footer())

                if i == 0:
                    kwargs: dict[str, Any] = {"content": None, "embed": embed}
                    if is_last:
                        kwargs["view"] = action_view
                    if file_attachment and is_last:
                        kwargs["attachments"] = [file_attachment[0]]
                    try:
                        await interaction.edit_original_response(**kwargs)
                    except discord.NotFound:
                        log.warning("Interaction expired, using followup for response")
                        fb_kwargs = {"embed": embed}
                        if is_last:
                            fb_kwargs["view"] = action_view
                        if file_attachment and is_last:
                            fb_kwargs["file"] = file_attachment[0]
                        await interaction.followup.send(**fb_kwargs)
                else:
                    kwargs = {"embed": embed}
                    if is_last:
                        kwargs["view"] = action_view
                    if file_attachment and is_last:
                        kwargs["file"] = file_attachment[0]
                    await interaction.followup.send(**kwargs)

    # Send table image if one was rendered
    if table_image_file:
        try:
            await interaction.followup.send(file=table_image_file)
        except Exception as e:
            log.debug("Failed to send table image: %s", e)

    audit_log(interaction.user, "ask", detail=question[:200])

    # Error tracking: record /ask outcome
    try:
        from error_tracker import record_outcome
        explainability = _final_meta.get("explainability") if isinstance(_final_meta.get("explainability"), dict) else {}
        scope_mode = _final_meta.get("scope_mode") or explainability.get("scope_mode")
        lock_mode = explainability.get("lock_mode")
        anchor_id = explainability.get("anchor_id")
        anchor_age = explainability.get("anchor_age_seconds")
        profile_values = explainability.get("effective_profile") or explainability.get("effective_profile_values")
        record_outcome(
            user_id=interaction.user.id,
            question=question,
            model_used=model_used,
            success=(model_used != "error"),
            error_msg=response_text if model_used == "error" else "",
            trace_id=get_trace_id(),
            response_preview=response_text[:2000],
            latency_ms=int((time.monotonic() - _ask_start) * 1000),
            routing_notes=_routing_notes,
            scope_mode=scope_mode,
            lock_mode=lock_mode,
            anchor_id=anchor_id,
            anchor_age=anchor_age,
            profile_values=profile_values if isinstance(profile_values, dict) else {},
            explainability=explainability if isinstance(explainability, dict) else {},
        )
    except Exception as exc:
        log.debug("Error tracking record failed: %s", exc)

    # Response-time tracking
    try:
        from spending import record_response_time
        elapsed_ms = (time.monotonic() - _ask_start) * 1000
        record_response_time(elapsed_ms, model=model_used)
    except Exception as exc:
        log.debug("Response time tracking failed: %s", exc)

    conversation_store.cleanup_expired()

    # Fire-and-forget: correction detection & profile learning (Phase 14)
    async def _post_response_learning():
        with request_context(channel_id=context_channel_id, thread_id=context_thread_id):
            try:
                from rules_engine import add_rule, detect_correction, extract_rule
                if detect_correction(question):
                    prev_bot_msg = ""
                    for msg in reversed(conv.history[:-1]):
                        if msg.get("role") == "model":
                            parts = msg.get("parts", [])
                            prev_bot_msg = " ".join(p for p in parts if isinstance(p, str))[:500]
                            break
                    if prev_bot_msg:
                        rule = await extract_rule(question, prev_bot_msg)
                        if rule:
                            await add_rule(rule, question[:300])
                            try:
                                await interaction.followup.send(
                                    f"📝 Got it — I'll remember: *{rule}*", ephemeral=True
                                )
                            except Exception as exc:
                                log.debug("Correction followup send failed: %s", exc)
            except Exception as e:
                log.debug("Correction detection failed (non-critical): %s", e)

            try:
                from user_profile import learn_from_message
                await learn_from_message(question, response_text)
            except Exception as e:
                log.debug("Profile learning failed (non-critical): %s", e)

            try:
                from fact_extractor import extract_and_store_facts, should_extract
                if should_extract(interaction.user.id, question):
                    await extract_and_store_facts(question, response_text, interaction.user.id)
            except Exception as e:
                log.debug("Fact extraction failed (non-critical): %s", e)

            try:
                from goal_tracker import detect_goal, extract_and_store_goal
                if detect_goal(question):
                    goal = await extract_and_store_goal(question, interaction.user.id)
                    if goal:
                        try:
                            await interaction.followup.send(
                                f"🎯 Tracking goal: *{goal}*", ephemeral=True
                            )
                        except Exception as exc:
                            log.debug("Goal followup send failed: %s", exc)
            except Exception as e:
                log.debug("Goal tracking failed (non-critical): %s", e)

    asyncio.get_running_loop().create_task(_post_response_learning())


# ---------------------------------------------------------------------------
# Thread follow-up listener — treat messages in bot-created threads as /ask
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message) -> None:
    """Handle thread follow-ups and default plain-text /ask messages."""
    # Ignore bot messages
    if message.author.bot:
        return

    user_question = (message.content or "").strip()
    if user_question.startswith("/"):
        await bot.process_commands(message)
        return

    in_thread = isinstance(message.channel, discord.Thread)
    bot_owns_thread = in_thread and bot.user is not None and message.channel.owner_id == bot.user.id
    original_bot_owned_thread = bot_owns_thread

    # Allow default plain-message ask flow in user-owned/forum threads too.
    # Previously these returned early, which made non-slash messages appear ignored.
    if in_thread and not _bot_can_read_channel(message.channel):
        await bot.process_commands(message)
        return

    if not in_thread and not _bot_can_read_channel(message.channel):
        await bot.process_commands(message)
        return

    # Auth check
    if not _is_user_allowed(message.author.id):
        return

    if is_emergency_stopped():
        await message.channel.send(
            "🛑 **Emergency stop is active.** Conversation is disabled. Use `/estop resume` to resume."
        )
        return

    if not llm_is_configured():
        await message.channel.send("⚠️ LLM not configured.")
        return

    flow_channel = message.channel
    if original_bot_owned_thread:
        parent_channel = getattr(message.channel, "parent", None)
        if parent_channel is not None and getattr(parent_channel, "id", None):
            _remember_default_ask_thread(parent_channel, message.author.id, int(message.channel.id))

    if not in_thread:
        routed_thread, _created_new = await _get_or_create_default_ask_thread(
            message.channel,
            user_id=message.author.id,
            user_question=user_question,
        )
        if routed_thread is not None:
            flow_channel = routed_thread
            in_thread = True
            bot_owns_thread = True
            _remember_default_ask_thread(message.channel, message.author.id, int(routed_thread.id))
            try:
                await message.channel.send(f"💬 Continuing in {routed_thread.mention}")
            except Exception as exc:
                log.debug("Failed to send default-ask thread redirect: %s", exc)

    # Max message guard (threads only)
    if bot_owns_thread and cfg.thread_max_messages > 0:
        conv = conversation_store.get(
            user_id=message.author.id,
            channel_id=flow_channel.id,
            user_name=str(message.author.display_name),
        )
        if conv.message_count >= cfg.thread_max_messages * 2:
            await flow_channel.send(
                f"⚠️ This thread has reached {cfg.thread_max_messages} exchanges. "
                "Please start a new `/ask` for a fresh conversation."
            )
            return

    if not user_question:
        if (
            getattr(message, "guild", None) is not None
            and _is_user_allowed(message.author.id)
            and _should_send_message_content_hint(message.channel)
        ):
            try:
                await message.channel.send(
                    "ℹ️ I received a message with no readable content. "
                    "If plain-message chat isn't working, enable **Message Content Intent** "
                    "for this bot in the Discord Developer Portal, then restart OpenClaw. "
                    "You can still use `/ask` immediately."
                )
            except Exception as exc:
                log.debug("Failed to send message-content hint: %s", exc)
        return

    _ask_start = time.monotonic()

    async with flow_channel.typing():
        conv = conversation_store.get(
            user_id=message.author.id,
            channel_id=flow_channel.id,
            user_name=str(message.author.display_name),
        )

        model_pref = get_model_preference(message.author.id)
        from llm import _needs_tools as llm_needs_tools
        model_pref, _ = normalize_model_preference(user_question, model_pref, llm_needs_tools)

        response_text = ""

        try:
            scoped_channel_id, scoped_thread_id = _resolve_channel_thread_scope(
                flow_channel,
                flow_channel.id,
                user_id=message.author.id,
            )
            def _update_history(updated_history: list[dict[str, Any]]) -> None:
                conv.update_from_llm(updated_history)
                conversation_store.auto_save_thread(
                    message.author.id, flow_channel.id, str(message.author.display_name),
                )

            result = await run_ask_stream(
                llm_stream=llm_chat_stream,
                user_message=user_question,
                history=conv.history,
                user_name=str(message.author.display_name),
                model_preference=model_pref,
                channel_id=scoped_channel_id,
                thread_id=scoped_thread_id,
                user_id=str(message.author.id),
                update_history=_update_history,
            )
            response_text = result.response_text
            model_used = result.model_used

            final_meta: dict[str, Any] = _with_requested_item_target(result.final_meta, question=user_question)
            quality_meta = _safe_score_answer_quality(
                response_text,
                final_meta=final_meta,
                context="ask_message_flow",
            )
            async def _run_retry_stream(retry_question: str) -> Any:
                return await run_ask_stream(
                    llm_stream=llm_chat_stream,
                    user_message=retry_question,
                    history=conv.history,
                    user_name=str(message.author.display_name),
                    model_preference=model_pref,
                    channel_id=scoped_channel_id,
                    thread_id=scoped_thread_id,
                    user_id=str(message.author.id),
                    update_history=_update_history,
                )

            repair_result = await _run_quality_auto_repair(
                question=user_question,
                response_text=response_text,
                model_used=model_used,
                final_meta=final_meta,
                quality_meta=quality_meta,
                context="ask_message_flow",
                run_retry_stream=_run_retry_stream,
            )
            response_text = str(repair_result["response_text"])
            final_meta = dict(repair_result["final_meta"])
            recovery_block = _build_ask_recovery_block(final_meta)
            if recovery_block and "Recovery note" not in response_text:
                response_text = f"{response_text.rstrip()}{recovery_block}"
            log.info(
                "message ask quality status=%s path=%s",
                final_meta.get("answer_quality", {}).get("status", "unknown"),
                final_meta.get("answer_quality_retry", {}).get("status_path"),
            )
        except Exception as e:
            log.error("Message ask-flow LLM error: %s", e)
            response_text = f"❌ **Error:** {e}"

        if not response_text or len(response_text.strip()) < 5:
            response_text = "⚠️ I wasn't able to generate a useful response. Try rephrasing your question."

        # Optional image fallback for large/complex tables in thread follow-ups
        table_image_file = None
        try:
            from table_renderer import (
                extract_table_text,
                render_table_image,
                should_render_table_image,
            )
            table_text = extract_table_text(response_text)
            if table_text and should_render_table_image(table_text):
                img_bytes = render_table_image(table_text)
                if img_bytes:
                    table_image_file = discord.File(io.BytesIO(img_bytes), filename="table.png")
        except Exception as e:
            log.debug("Thread table image rendering failed: %s", e)

        response_text = _format_markdown_for_discord(response_text)
        response_text = _format_tables_for_context(
            response_text,
            channel_id=scoped_channel_id,
            thread_id=scoped_thread_id,
        )
        chunks = _split_response(response_text)

        try:
            for chunk in chunks:
                embed = discord.Embed(description=chunk, color=discord.Color.purple())
                await flow_channel.send(embed=embed)
            if table_image_file:
                await flow_channel.send(file=table_image_file)
        except Exception as exc:
            log.warning("Failed to send default ask response in flow channel: %s", exc)
            if flow_channel is not message.channel:
                for chunk in chunks:
                    embed = discord.Embed(description=chunk, color=discord.Color.purple())
                    await message.channel.send(embed=embed)
                if table_image_file:
                    await message.channel.send(file=table_image_file)

    audit_action = "thread_followup" if original_bot_owned_thread else "ask_default"
    audit_log(message.author, audit_action, detail=user_question[:200])
    conversation_store.cleanup_expired()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the OpenClaw Discord bot."""
    # Run config validation and log results
    issues = cfg.validate()
    for issue in issues:
        if issue.startswith("❌"):
            log.error("Config: %s", issue)
        elif issue.startswith("⚠️"):
            log.warning("Config: %s", issue)
        else:
            log.info("Config: %s", issue)

    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set. Create a .env file or set the environment variable.")
        sys.exit(1)

    log.info("Starting OpenClaw bot...")
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
