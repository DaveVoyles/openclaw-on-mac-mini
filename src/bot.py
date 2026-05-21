"""
OpenClaw Discord Bot - Phase 6: Remote Access & Monitoring
Autonomous AI agent for home automation and system management.

This is the core bot file — init, /ask command, and entry point.
Slash commands live in discord_commands.py, background tasks in
discord_background.py, and the web/health server in discord_web.py.
"""

import asyncio
import datetime
import hashlib
import json
import logging
import os
import re
import sys
import time
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
from onboarding import OnboardingManager
from bot_helpers import (
    _build_default_ask_thread_name,
    _default_ask_thread_cache_key,
    _default_ask_thread_user_tag,
    _is_user_allowed,
    _pick_most_recent_thread,
    _resolve_channel_thread_scope,
    _should_send_message_content_hint,
    make_discord_stream_handler,
)

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
# When set to "1", the bot sends a "thinking…" placeholder and edits it as
# LLM chunks arrive rather than waiting for the full response.
PROVIDER_STREAM = os.getenv("PROVIDER_STREAM", "").strip() == "1"
# _STREAM_DISCORD_EDIT_INTERVAL and _SHOW_THINKING_PLACEHOLDER live in bot_helpers.py

# Reaction-based feedback logging (W11-B)
_FEEDBACK_ENABLED: bool = os.getenv("FEEDBACK_ENABLED", "1").lower() in ("1", "true", "yes")
_FEEDBACK_LOG_PATH: str = os.getenv("FEEDBACK_LOG", "data/feedback.jsonl")
_FEEDBACK_TIMEOUT_S: int = int(os.getenv("FEEDBACK_TIMEOUT", "60"))

# ---------------------------------------------------------------------------
# Channel role architecture — prevents context bleed between workflows
# ---------------------------------------------------------------------------

_CHANNEL_ROLES: dict[int, str] = {}
_CHANNEL_PROMPTS: dict[str, str] = {}
_DEFAULT_ASK_THREAD_CACHE: dict[tuple[int, int, int], tuple[int, float]] = {}
_DEFAULT_ASK_THREAD_CACHE_TTL_SECONDS = 60 * 60 * 24
# _MESSAGE_CONTENT_HINT_CACHE and _MESSAGE_CONTENT_HINT_COOLDOWN_SECONDS live in bot_helpers.py

# ---------------------------------------------------------------------------
# First-ask tip tracking (W1-2)
# ---------------------------------------------------------------------------

_ONBOARDING_SEEN_PATH = Path("data/onboarding_seen.json")
_onboarding_seen_ids: set[int] = set()


def _load_onboarding_seen() -> None:
    """Load set of user IDs who have already received the first-ask tip."""
    global _onboarding_seen_ids
    if _ONBOARDING_SEEN_PATH.exists():
        try:
            with open(_ONBOARDING_SEEN_PATH) as f:
                _onboarding_seen_ids = set(json.load(f))
        except (json.JSONDecodeError, ValueError, OSError):
            _onboarding_seen_ids = set()


def _save_onboarding_seen() -> None:
    """Persist the set of greeted user IDs to disk."""
    _ONBOARDING_SEEN_PATH.parent.mkdir(exist_ok=True)
    with open(_ONBOARDING_SEEN_PATH, "w") as f:
        json.dump(list(_onboarding_seen_ids), f)


_load_onboarding_seen()


async def _collect_feedback(
    bot_message: discord.Message,
    query_hash: str,
    model: str,
    provider: str,
    skills: list[str],
) -> None:
    """Add reactions to bot_message and wait for user feedback. Non-blocking."""
    if not _FEEDBACK_ENABLED:
        return
    try:
        await bot_message.add_reaction("👍")
        await bot_message.add_reaction("👎")
    except (discord.HTTPException, discord.Forbidden, discord.NotFound):
        return

    def check(reaction: discord.Reaction, user: discord.User) -> bool:
        return str(reaction.emoji) in ("👍", "👎") and reaction.message.id == bot_message.id and not user.bot

    try:
        reaction, _user = await asyncio.wait_for(
            bot.wait_for("reaction_add", check=check),
            timeout=_FEEDBACK_TIMEOUT_S,
        )
        rating = 1 if str(reaction.emoji) == "👍" else -1
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "query_hash": query_hash,
            "model": model,
            "provider": provider,
            "skills": skills,
            "rating": rating,
        }
        Path(_FEEDBACK_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(_FEEDBACK_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except (asyncio.TimeoutError, OSError):
        pass  # No feedback received — that's fine
    finally:
        try:
            await bot_message.clear_reactions()
        except (discord.HTTPException, discord.Forbidden, discord.NotFound):
            pass


async def _load_channel_config() -> None:
    """Load channel roles from config.yaml and map them to env-provided IDs.

    Channel role injection is controlled by ``channels.roles_enabled`` in
    config.yaml. When false (the default), channels act as organizational
    labels only and the bot responds generically in all channels.
    """
    config_file = CONFIG_DIR / "config.yaml"
    roles_enabled = False
    if config_file.exists():
        try:
            async with aiofiles.open(config_file) as f:
                content = await f.read()
                cfg_yaml = yaml.safe_load(content) or {}
            channels_cfg = cfg_yaml.get("channels", {})
            roles_enabled = bool(channels_cfg.get("roles_enabled", False))
            roles = channels_cfg.get("roles", {})
            for role_name, role_cfg in roles.items():
                prompt = role_cfg.get("prompt_override", "")
                if prompt:
                    _CHANNEL_PROMPTS[role_name] = prompt
        except (yaml.YAMLError, OSError, KeyError, TypeError) as e:
            log.warning("Failed to load channel config: %s", e)

    if not roles_enabled:
        log.info("Channel roles disabled (channels.roles_enabled=false) — all channels use generic behavior")

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
log = logging.getLogger(__name__)
setup_trace_logging()

# ---------------------------------------------------------------------------
# Helpers (bot-global-dependent; pure helpers live in bot_helpers.py)
# ---------------------------------------------------------------------------


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
    except Exception as exc:  # broad: intentional — thread creation can fail in unexpected ways
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
intents.reactions = True


class OpenClawBot(commands.Bot):
    """Discord bot with slash commands, cog extensions, and app-command tree."""

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.start_time = time.monotonic()
        self._health_runner = None

    async def setup_hook(self) -> None:
        """Load cogs dynamically, register commands, and sync on startup."""
        cogs_dir = Path(__file__).parent / "cogs"
        # Discord caps slash commands at 100 globally. With 40+ cogs, you may need
        # to opt out of some. Set DISCORD_DISABLED_COGS=cog_a,cog_b in .env to skip
        # them (name without the `_cog.py` suffix, or the full `cogs.foo_cog` path).
        disabled_raw = os.getenv("DISCORD_DISABLED_COGS", "")
        disabled = {
            name.strip().removeprefix("cogs.").removesuffix("_cog")
            for name in disabled_raw.split(",")
            if name.strip()
        }
        loaded: list[str] = []
        skipped: list[str] = []
        for cog_file in sorted(cogs_dir.glob("*_cog.py")):
            if cog_file.name.startswith("_"):
                continue
            short = cog_file.stem.removesuffix("_cog")
            if short in disabled:
                skipped.append(cog_file.stem)
                continue
            module = f"cogs.{cog_file.stem}"
            try:
                await self.load_extension(module)
                loaded.append(module)
                log.info("Loaded cog: %s", module)
            except (commands.ExtensionError, ImportError) as e:
                log.error("Failed to load cog %s: %s", module, e)
        if skipped:
            log.info("Skipped %d cog(s) via DISCORD_DISABLED_COGS: %s", len(skipped), ", ".join(skipped))
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
        except (ImportError, AttributeError, RuntimeError) as exc:
            log.warning("Failed to start metrics collector: %s", exc)

        # Startup provider capability scan
        try:
            from llm.startup import scan_providers

            provider_status = await scan_providers()
            available = [p for p, info in provider_status.items() if info["available"]]
            unavailable = [p for p, info in provider_status.items() if not info["available"]]
            log.info("Providers available: %s", ", ".join(available) or "none")
            if unavailable:
                log.warning("Providers unavailable: %s", ", ".join(unavailable))
        except (ImportError, RuntimeError, asyncio.TimeoutError) as exc:
            log.warning("Provider scan failed: %s", exc)

        from llm.providers import start_proxy_health_loop

        start_proxy_health_loop()

        # Schedule audit log rotation background task (interval via AUDIT_ROTATE_INTERVAL env var)
        async def _audit_log_rotation_loop() -> None:
            from llm.telemetry import _AUDIT_ROTATE_INTERVAL, rotate_audit_log

            while True:
                await asyncio.sleep(_AUDIT_ROTATE_INTERVAL)
                try:
                    await rotate_audit_log()
                except (
                    Exception
                ) as exc:  # broad: intentional  # noqa: BLE001 — audit log rotation can fail in many ways
                    log.debug("Audit log rotation failed: %s", exc)

        from bg_tasks import managed_task

        managed_task(_audit_log_rotation_loop(), name="audit-log-rotation", timeout=None)

        # Start Slack bot if configured
        if os.getenv("SLACK_ENABLED", "false").lower() == "true":
            try:
                from slack_bot import create_slack_handler

                _slack_handler = await create_slack_handler()
                if _slack_handler:
                    asyncio.create_task(_slack_handler.start_async(), name="slack-socket-mode")
                    log.info("Slack Socket Mode handler started")
            except Exception as exc:  # broad: intentional — Slack failure must not block Discord startup
                log.warning("Slack bot failed to start: %s", exc)

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
                action="run_maintenance",
                args={},
                hour=4,
                minute=0,
                created_by="system",
                notify_channel_id=ALERT_CHANNEL_ID,
                alert_only=False,
            )
            log.info("Registered 4:00 AM maintenance cron job")
        if "index_vault_to_qmd" not in existing_actions:
            scheduler.create(
                action="index_vault_to_qmd",
                args={},
                hour=3,
                minute=50,
                created_by="system",
                notify_channel_id=0,
                alert_only=False,
            )
            log.info("Registered 3:50 AM vault indexer cron job")

        # Register Patreon monitoring (every 30 minutes)
        patreon_tasks = [t for t in scheduler.list_tasks() if "patreon" in t.action.lower()]
        from patreon_scheduled import scheduled_patreon_health_check, set_discord_client as _set_patreon_client

        _set_patreon_client(self)  # Always refresh module-level ref (survives restarts)
        if not patreon_tasks:
            scheduler.register_skills({"patreon_health_check": scheduled_patreon_health_check})
            scheduler.create(
                action="patreon_health_check",
                args={
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
            except (discord.HTTPException, discord.Forbidden, discord.NotFound) as e:
                log.error("Failed to post scheduler result for %s: %s", task_id, e)

        scheduler.notify_callback = _scheduler_notify

        # Start background tasks (cleanup, audit writer, proactive loops)
        from discord_background import start_background_tasks

        active_background_loops = 0
        try:
            active_background_loops = int(start_background_tasks(self))
        except (ImportError, RuntimeError, AttributeError) as exc:
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
                    except (discord.HTTPException, discord.Forbidden, discord.NotFound) as exc:
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
                    except (discord.HTTPException, discord.Forbidden, discord.NotFound) as e:
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
            except (OSError, IOError) as exc:
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
            except Exception as exc:  # broad: intentional  # noqa: BLE001 — cleanup code; keep broad
                log.debug("close %s: %s", name, exc)
        try:
            from metrics_collector import stop_metrics_collector

            await stop_metrics_collector()
        except (ImportError, RuntimeError, AttributeError) as exc:
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
    except Exception as send_exc:  # broad: intentional  # noqa: BLE001 — error response must not crash the handler
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
    model="LLM routing: auto (active profile), local (Gemma), gemini (cloud), openai (GPT-4o), anthropic (Claude), or copilot (enterprise proxy). Alias: claude → anthropic",
    scope="Context scope: current channel/thread, cross-channel, or prior-report anchor mode",
    reset_context="Reset the current anchor context before recall (optional)",
    anchor="Optional anchor override ID. Use 'none' to disable anchor targeting",
)
@app_commands.choices(
    model=[
        app_commands.Choice(name="🔄 Auto (routing profile)", value="auto"),
        app_commands.Choice(name="🏠 Local (Gemma/Ollama)", value="local"),
        app_commands.Choice(name="☁️ Gemini (cloud)", value="gemini"),
        app_commands.Choice(name="🟢 OpenAI (GPT-4o)", value="openai"),
        app_commands.Choice(name="🟣 Anthropic (Claude)", value="anthropic"),
        app_commands.Choice(name="🟦 Copilot (enterprise proxy)", value="copilot"),
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
    from ask_handler import handle_ask  # lazy to avoid circular import

    await handle_ask(interaction, question, attachment, model, scope, reset_context, anchor)


@bot.tree.command(name="metrics", description="Show last 20 routing telemetry events (provider, model, latency)")
async def metrics_cmd(interaction: discord.Interaction) -> None:
    """Display a brief routing telemetry summary."""
    from ask_handler import handle_metrics  # lazy to avoid circular import

    await handle_metrics(interaction)


# ---------------------------------------------------------------------------
# Thread follow-up listener — treat messages in bot-created threads as /ask
# ---------------------------------------------------------------------------


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Send welcome DM and start onboarding when a new member joins."""
    try:
        dm_channel = await member.create_dm()
        manager = OnboardingManager()
        await manager.send_welcome_message(member, dm_channel)
        log.info("Sent onboarding welcome to new member %s (%s)", member, member.id)
    except discord.Forbidden:
        log.warning("Could not DM new member %s (%s) — DMs disabled", member, member.id)
    except Exception as exc:  # broad: intentional  # noqa: BLE001 — Discord event callback; must not raise
        log.error("on_member_join error for %s: %s", member, exc)


@bot.event
async def on_message(message: discord.Message) -> None:
    """Handle thread follow-ups and default plain-text /ask messages."""
    # W1-2: one-time first-message tip per user
    if (
        not message.author.bot
        and message.author.id not in _onboarding_seen_ids
        and message.guild is not None  # only in guild channels, not DMs
    ):
        _onboarding_seen_ids.add(message.author.id)
        _save_onboarding_seen()
        try:
            await message.channel.send(
                f"💡 New here, {message.author.mention}? Run `/help` to explore all commands.",
                delete_after=60,
            )
        except (discord.HTTPException, discord.Forbidden, discord.NotFound):
            pass
    from discord_events import handle_message  # lazy to avoid circular import

    await handle_message(message, channel_roles=_CHANNEL_ROLES)


# ---------------------------------------------------------------------------
# Reaction handling — alert snooze / resolve
# ---------------------------------------------------------------------------


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    """Forward reactions on alert messages to the alert manager (snooze/resolve)."""
    from alert_manager import handle_alert_reaction  # lazy import — alert_manager optional

    try:
        await handle_alert_reaction(payload.message_id, str(payload.emoji), payload.user_id)
    except Exception as exc:  # broad: intentional  # noqa: BLE001 — reaction handler must not crash
        log.debug("on_raw_reaction_add error: %s", exc)


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
