"""
OpenClaw Discord Bot - Phase 6: Remote Access & Monitoring
Autonomous AI agent for home automation and system management.
"""

import asyncio
import collections
import datetime
import functools
import io
import json
import logging
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord.ext import commands
import yaml
from aiohttp import web
from discord import app_commands
from dotenv import load_dotenv

from skills import (
    SKILLS,
    get_container_logs,
    get_container_status,
    get_docker_stats,
    get_system_stats,
    get_uptime,
    list_containers,
    restart_container,
)
from skills.advanced_skills import (
    browse_url,
    check_arr_health,
    check_download_clients,
    check_plex_status,
    check_service_ports,
    create_status_report,
    get_download_queue,
    get_plex_activity,
    get_weather,
    ping_host,
    search_web,
)
from analyzer import analyze_logs
from scheduler import scheduler

from agentmail import send_agent_mail
from calendar_skills import get_upcoming_events
from git_skills import git_status, git_diff
from mission_control import get_mission_tasks
from qmd import remember_fact, recall_fact
from research_agent import ResearchAgent

from llm import chat as llm_chat, is_configured as llm_is_configured, get_rate_info
from llm import chat_stream as llm_chat_stream
from llm import analyze_image as llm_analyze_image, analyze_document as llm_analyze_document
from llm import SUPPORTED_IMAGE_MIMES
from memory import store as conversation_store
from memory import get_model_preference, set_model_preference
from dashboard import api_dashboard_handler, dashboard_handler, guide_handler
from image_gen import generate_image, is_available as sd_is_available
from code_sandbox import run_code as sandbox_run_code
from approvals import (
    ApprovalView,
    RiskLevel,
    approval_store,
    build_approval_embed,
    is_emergency_stopped,
    set_emergency_stop,
)
from agent_loop import scan_interrupted as scan_interrupted_plans, list_plans as al_list_plans, resume_plan as al_resume_plan, read_plan as al_read_plan, cancel_plan as al_cancel_plan
from config import cfg
from constants import (
    EMBED_DESC_LIMIT,
    EMBED_SPLIT_LIMIT,
    EMBED_FIELD_LIMIT,
    EMBED_PROMPT_LIMIT,
    PROACTIVE_SCAN_INTERVAL,
    CLEANUP_INTERVAL,
    AUDIT_FLUSH_INTERVAL,
    BRIEFING_CHECK_INTERVAL,
    BRIEFING_HOUR,
    BRIEFING_MINUTE_WINDOW,
    LOG_SNIPPET_MAX_CHARS,
    MEMORY_SNIPPET_MAX_CHARS,
    DOCUMENT_MAX_CHARS,
    ATTACHMENT_TEXT_MAX_CHARS,
    PROACTIVE_LOG_LINES,
    DEFAULT_ANALYZE_LINES,
    PDF_MAX_PAGES,
    MAX_FILE_SIZE,
    OUTPUT_MAX_CHARS,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8765"))
AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/audit"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/logs"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
# Channel for proactive push notifications (morning briefing, alerts)
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# ---------------------------------------------------------------------------
# Channel role architecture — prevents context bleed between workflows
# ---------------------------------------------------------------------------

# Map: Discord channel_id → role name ('research', 'analytics', 'bookmarks')
_CHANNEL_ROLES: dict[int, str] = {}
# Map: role name → prompt override text (loaded from config.yaml)
_CHANNEL_PROMPTS: dict[str, str] = {}


def _load_channel_config() -> None:
    """Load channel roles from config.yaml and map them to env-provided IDs."""
    global _CHANNEL_ROLES, _CHANNEL_PROMPTS
    config_file = CONFIG_DIR / "config.yaml"
    if config_file.exists():
        try:
            with open(config_file) as f:
                cfg = yaml.safe_load(f) or {}
            roles = cfg.get("channels", {}).get("roles", {})
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def truncate_for_embed(text: str, limit: int = EMBED_DESC_LIMIT) -> str:
    """Truncate *text* to fit in a Discord embed description."""
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… (truncated)"


# ---------------------------------------------------------------------------
# Audit logger — uses shared audit module; buffer lives in audit.py
# ---------------------------------------------------------------------------

AUDIT_DIR.mkdir(parents=True, exist_ok=True)

from audit import audit_log, _audit_buffer  # noqa: E402


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------


def is_allowed(interaction: discord.Interaction) -> bool:
    """Return True if the invoking user is on the allow-list."""
    if not ALLOWED_USER_IDS:
        return True  # No allowlist configured → allow all (dev mode)
    return interaction.user.id in ALLOWED_USER_IDS


def require_auth(func):
    """Decorator that gates a slash-command handler behind the allow-list.

    Usage::

        @bot.tree.command(name="foo", description="…")
        @require_auth
        async def foo_cmd(interaction: discord.Interaction):
            ...

    The decorated function receives an *already-deferred* interaction (via
    ``interaction.response.defer()``) so it can freely use followup.send().
    """

    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        if not is_allowed(interaction):
            await interaction.response.send_message(
                "🔒 You are not authorized to use this command.", ephemeral=True
            )
            return
        return await func(interaction, *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Permissions helper (reads config/permissions.yaml)
# ---------------------------------------------------------------------------

_permissions_cache: dict | None = None
_permissions_mtime: float = 0.0


def _load_permissions() -> dict:
    global _permissions_cache, _permissions_mtime
    perms_file = CONFIG_DIR / "permissions.yaml"
    try:
        current_mtime = perms_file.stat().st_mtime if perms_file.exists() else 0.0
    except OSError:
        current_mtime = 0.0
    if _permissions_cache is not None and current_mtime == _permissions_mtime:
        return _permissions_cache
    if perms_file.exists():
        try:
            with open(perms_file) as f:
                _permissions_cache = yaml.safe_load(f) or {}
        except Exception as exc:
            log.warning("Failed to parse permissions YAML: %s", exc)
            _permissions_cache = _permissions_cache or {}
    else:
        _permissions_cache = {}
    _permissions_mtime = current_mtime
    return _permissions_cache


def is_service_allowed(skill: str, service: str) -> bool:
    """Check permissions.yaml to see if a service is allowed for a skill."""
    perms = _load_permissions()
    cmd_perms = perms.get("commands", {}).get(skill, {})
    denied = cmd_perms.get("denied_services", [])
    allowed = cmd_perms.get("allowed_services", [])
    if service in denied:
        return False
    if allowed and service not in allowed:
        return False
    return True


# ---------------------------------------------------------------------------
# Module-level aiohttp session (reused for attachment downloads)
# ---------------------------------------------------------------------------

_bot_http_session: aiohttp.ClientSession | None = None


def _get_bot_http_session() -> aiohttp.ClientSession:
    global _bot_http_session
    if _bot_http_session is None or _bot_http_session.closed:
        _bot_http_session = aiohttp.ClientSession()
    return _bot_http_session


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True


class OpenClawBot(commands.Bot):
    """Discord bot with slash commands, cog extensions, and app-command tree."""

    def __init__(self):
        # command_prefix is required by commands.Bot — unused since we only use slash commands
        super().__init__(command_prefix="!", intents=intents)
        self.start_time = time.monotonic()
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self):
        """Load cogs and sync commands on startup."""
        # Load cog extensions
        for cog in ("cogs.docker_cog", "cogs.media_cog", "cogs.network_cog", "cogs.analytics_cog"):
            await self.load_extension(cog)
        log.info("Loaded cogs: docker, media, network, analytics")

        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to guild %s", DISCORD_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Synced commands globally")

        # Start health-check HTTP server
        await self._start_health_server()

    async def on_ready(self):
        log.info("OpenClaw online as %s (ID %s)", self.user, self.user.id)
        audit_log(None, "bot_ready", f"Logged in as {self.user}")

        # Load channel role configuration
        _load_channel_config()

        # Start scheduler and register skills
        scheduler.register_skills(SKILLS)
        scheduler.start()
        log.info("Scheduler started with %d registered skills", len(SKILLS))

        # Register recurring cron jobs (idempotent — skip if already persisted)
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

        # Wire scheduler → Discord notification callback
        async def _scheduler_notify(task_id: str, action: str, result: str, is_alert: bool) -> None:
            task = scheduler.get(task_id)
            if task is None:
                return
            channel = self.get_channel(task.notify_channel_id)
            if channel is None:
                return
            color = discord.Color.red() if is_alert else discord.Color.green()
            icon = "🚨" if is_alert else "✅"
            embed = discord.Embed(
                title=f"{icon} Watch Alert: `{action}`",
                description=result[:EMBED_SPLIT_LIMIT] or "(no output)",
                color=color,
            )
            embed.set_footer(text=f"Task {task_id} • {action}")
            try:
                await channel.send(embed=embed)
            except Exception as e:
                log.error("Failed to post scheduler result for %s: %s", task_id, e)

        scheduler.notify_callback = _scheduler_notify

        # Background maintenance: clean up expired conversations and approvals
        asyncio.create_task(self._background_cleanup())
        # Background audit log flusher
        asyncio.create_task(self._audit_writer())
        # Proactive features: morning briefing, real estate watcher
        if ALERT_CHANNEL_ID:
            asyncio.create_task(self._morning_briefing_loop())
            asyncio.create_task(self._proactive_insight_loop())
            log.info("Proactive tasks started (alert channel: %d)", ALERT_CHANNEL_ID)

            # Scan for interrupted plans from previous runs
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
        else:
            log.info("ALERT_CHANNEL_ID not set — proactive push notifications disabled")

    async def _audit_writer(self):
        """Flush buffered audit entries to disk every 30 seconds."""
        while True:
            await asyncio.sleep(AUDIT_FLUSH_INTERVAL)
            if not _audit_buffer:
                continue
            entries = []
            while _audit_buffer:
                try:
                    entries.append(_audit_buffer.popleft())
                except IndexError:
                    break
            if entries:
                today = datetime.date.today().isoformat()
                audit_file = AUDIT_DIR / f"{today}.jsonl"
                try:
                    with open(audit_file, "a") as f:
                        for e in entries:
                            f.write(json.dumps(e) + "\n")
                except Exception as ex:
                    log.warning("Audit flush failed: %s", ex)

    async def _background_cleanup(self):
        """Periodically clean up expired conversations and approval requests."""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)  # every 5 minutes
            try:
                conversation_store.cleanup_expired()
                approval_store.cleanup_expired()
            except Exception as e:
                log.warning("Background cleanup error: %s", e)

    # ------------------------------------------------------------------
    # Proactive: morning briefing (Phase C)
    # ------------------------------------------------------------------

    async def _morning_briefing_loop(self):
        """Post a morning briefing to ALERT_CHANNEL_ID each day at ~8:00 AM."""
        last_briefing_date: str = ""
        while True:
            try:
                now = datetime.datetime.now()
                if now.hour == BRIEFING_HOUR and now.minute < BRIEFING_MINUTE_WINDOW:
                    today_str = now.strftime("%Y-%m-%d")
                    if today_str != last_briefing_date:
                        last_briefing_date = today_str
                        asyncio.create_task(self._send_morning_briefing())
            except Exception as e:
                log.warning("Morning briefing scheduler error: %s", e)
            await asyncio.sleep(BRIEFING_CHECK_INTERVAL)  # check every minute

    async def _send_morning_briefing(self, channel_override=None):
        """Compose and post the daily morning briefing.

        If channel_override is provided (e.g. a discord.TextChannel or Interaction channel),
        post there instead of ALERT_CHANNEL_ID. Used by the /briefing slash command.
        """
        channel = channel_override
        if channel is None:
            if not ALERT_CHANNEL_ID:
                return
            channel = self.get_channel(ALERT_CHANNEL_ID)
            if not channel:
                log.warning("Morning briefing: channel %d not found", ALERT_CHANNEL_ID)
                return

        log.info("Generating morning briefing for channel %d", ALERT_CHANNEL_ID)
        try:
            # Gather data concurrently
            health, queue, weather, sysstat = await asyncio.gather(
                check_arr_health(),
                get_download_queue(),
                get_weather(),
                get_system_stats(),
                return_exceptions=True,
            )

            try:
                calendar = await asyncio.wait_for(get_upcoming_events(days=1), timeout=8)
            except Exception as exc:
                log.debug("Calendar fetch failed for briefing: %s", exc)
                calendar = "Calendar not available."

            today = datetime.date.today().strftime("%A, %B %d, %Y")
            prompt = (
                f"Good morning! Generate a concise morning briefing for {today}. "
                "Keep it under 600 words. Include:\n"
                f"**Weather**: {weather}\n"
                f"**System health**: {health}\n"
                f"**Downloads**: {queue}\n"
                f"**Today's calendar**: {calendar}\n"
                f"**System**: {sysstat}\n"
                "Format with clear sections, use emojis, be friendly but brief."
            )

            response_text, _, _ = await llm_chat(prompt)

            embed = discord.Embed(
                title=f"🌅 Morning Briefing — {today}",
                description=response_text[:EMBED_DESC_LIMIT],
                color=discord.Color.from_rgb(255, 165, 0),
            )
            embed.set_footer(text="🤖 OpenClaw Autonomous Briefing")
            await channel.send(embed=embed)
            audit_log(None, "morning_briefing", detail=f"channel={ALERT_CHANNEL_ID}")
        except Exception as e:
            log.error("Morning briefing failed: %s", e)

    async def _proactive_insight_loop(self):
        """Scan for anomalies every 2 hours and post a Discord alert if noteworthy."""
        # Wait 2 hours after startup before first scan (let the bot settle)
        await asyncio.sleep(PROACTIVE_SCAN_INTERVAL)
        while True:
            try:
                await self._run_proactive_scan()
            except Exception as e:
                log.warning("Proactive scan error: %s", e)
            await asyncio.sleep(PROACTIVE_SCAN_INTERVAL)

    _SAFE_RESTART_TARGETS = frozenset({
        "sonarr", "radarr", "lidarr", "prowlarr",
        "sabnzbd", "qbittorrent", "tautulli", "overseerr",
    })
    _error_re = re.compile(r"error|warn|exception|critical|failed", re.IGNORECASE)

    async def _gather_system_signals(self) -> tuple[str, dict[str, str]] | None:
        """Collect health checks and log snippets. Returns None if all clean."""
        health, dl_clients, plex = await asyncio.gather(
            check_arr_health(),
            check_download_clients(),
            check_plex_status(),
            return_exceptions=True,
        )

        key_containers = ["sonarr", "radarr", "sabnzbd", "plex"]
        log_snippets: dict[str, str] = {}
        for svc in key_containers:
            try:
                logs = await asyncio.wait_for(get_container_logs(svc, lines=PROACTIVE_LOG_LINES), timeout=6)
                if logs and self._error_re.search(logs):
                    log_snippets[svc] = logs[:LOG_SNIPPET_MAX_CHARS]
            except Exception as exc:
                log.debug("Container log fetch for %s failed: %s", svc, exc)

        all_clean = all(
            isinstance(r, str) and not self._error_re.search(r)
            for r in [health, dl_clients, plex]
            if isinstance(r, str)
        )
        if all_clean and not log_snippets:
            return None

        summary_parts = [
            f"Health checks:\n  *arr: {health}\n  Download clients: {dl_clients}\n  Plex: {plex}"
        ]
        if log_snippets:
            summary_parts.append("Log anomalies:")
            for svc, snippet in log_snippets.items():
                summary_parts.append(f"  {svc}:\n{snippet}")

        return "\n\n".join(summary_parts), log_snippets

    async def _execute_self_healing(self, analysis: str) -> tuple[str, list[str]]:
        """Parse SELF_HEAL directives and execute safe restarts.

        Returns (cleaned_analysis, heal_results).
        """
        heal_actions: list[str] = []
        display_analysis = analysis
        for line in analysis.split("\n"):
            if line.strip().startswith("SELF_HEAL:"):
                parts = line.strip().split()
                if len(parts) >= 3 and parts[1] == "restart_container":
                    target = parts[2].lower().strip()
                    if target in self._SAFE_RESTART_TARGETS:
                        heal_actions.append(target)
                display_analysis = display_analysis.replace(line, "").strip()

        heal_results: list[str] = []
        for target in heal_actions:
            try:
                result = await asyncio.wait_for(restart_container(target), timeout=60)
                heal_results.append(f"🔧 `{target}`: {result}")
                audit_log(None, "self_heal", detail=f"restart {target}: {result}")
                log.info("Self-heal: restarted %s → %s", target, result[:80])
            except Exception as exc:
                heal_results.append(f"❌ `{target}`: {exc}")
                log.warning("Self-heal restart failed for %s: %s", target, exc)

        return display_analysis, heal_results

    async def _run_proactive_scan(self):
        """Gather system signals + log snippets, ask Gemini for assessment, post if actionable."""
        if not ALERT_CHANNEL_ID:
            return

        result = await self._gather_system_signals()
        if result is None:
            log.debug("Proactive scan: all clear")
            return
        summary, _ = result

        prompt = (
            "You are OpenClaw's autonomous monitoring system running a background scan.\n"
            "Based on the signals below, determine if there is anything the operator should be "
            "aware of — errors, service failures, degraded performance, or unusual activity.\n"
            "ONLY respond if there is something genuinely actionable. "
            "If everything is within normal operation, respond with exactly: NO_ALERT\n\n"
            "If you find an issue, also include a SELF_HEAL section at the end with the format:\n"
            "SELF_HEAL: restart_container <container_name>\n"
            "Only suggest restart_container for non-critical services (sonarr, radarr, lidarr, "
            "prowlarr, sabnzbd, tautulli, overseerr). Do NOT suggest restarting plex, postgres, "
            "or openclaw itself. If no safe fix exists, omit the SELF_HEAL line.\n\n"
            f"{summary[:EMBED_PROMPT_LIMIT]}"
        )

        try:
            analysis, _, _ = await asyncio.wait_for(llm_chat(prompt), timeout=35)
            if not analysis or "NO_ALERT" in analysis.upper():
                log.debug("Proactive scan: LLM found nothing notable")
                return

            display_analysis, heal_results = await self._execute_self_healing(analysis)

            channel = self.get_channel(ALERT_CHANNEL_ID)
            if not channel:
                return

            embed = discord.Embed(
                title="🔭 Proactive Insight",
                description=display_analysis[:EMBED_SPLIT_LIMIT],
                color=discord.Color.gold(),
            )
            if heal_results:
                embed.add_field(
                    name="🔧 Auto-Repair Actions",
                    value="\n".join(heal_results)[:1000],
                    inline=False,
                )

            embed.set_footer(text="Autonomous monitoring scan • every 2h")
            await channel.send(embed=embed)
            audit_log(None, "proactive_scan", detail="insight posted")
            log.info("Proactive scan posted an insight (healed: %d)", len(heal_results))
        except asyncio.TimeoutError:
            log.warning("Proactive scan LLM call timed out")
        except Exception as e:
            log.warning("Proactive scan failed: %s", e)

    async def _analyze_webhook_event(
        self,
        source: str,
        payload: dict,
        channel,
    ):
        """Run a quick LLM analysis on an error-bearing webhook payload and post as follow-up."""
        prompt = (
            f"A '{source}' webhook arrived with the following payload:\n"
            f"{json.dumps(payload, indent=2)[:2000]}\n\n"
            "In 2-3 sentences: what happened, and what (if any) action should the operator take?"
        )
        try:
            analysis, _, _ = await asyncio.wait_for(llm_chat(prompt), timeout=20)
            if analysis:
                embed = discord.Embed(
                    title="🔍 AI Assessment",
                    description=analysis[:EMBED_FIELD_LIMIT],
                    color=discord.Color.orange(),
                )
                await channel.send(embed=embed)
        except Exception as e:
            log.warning("Webhook auto-analysis failed: %s", e)

    async def close(self):
        """Graceful shutdown: flush audit log, close sessions, stop health server."""
        # Flush any remaining audit entries
        if _audit_buffer:
            entries = list(_audit_buffer)
            _audit_buffer.clear()
            today = datetime.date.today().isoformat()
            audit_file = AUDIT_DIR / f"{today}.jsonl"
            try:
                with open(audit_file, "a") as f:
                    for e in entries:
                        f.write(json.dumps(e) + "\n")
            except Exception as exc:
                log.warning("Failed to flush audit buffer on shutdown: %s", exc)

        # Close all async sessions — lazy imports to avoid errors if modules weren't loaded
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
        # Close attachment download session
        global _bot_http_session
        if _bot_http_session and not _bot_http_session.closed:
            await _bot_http_session.close()
            _bot_http_session = None
        if self._health_runner:
            await self._health_runner.cleanup()
        await super().close()

    # ------------------------------------------------------------------
    # Health-check HTTP server (for Docker HEALTHCHECK / Uptime Kuma)
    # ------------------------------------------------------------------

    async def _start_health_server(self):
        app = web.Application()
        app["bot"] = self
        app.router.add_get("/", dashboard_handler)
        app.router.add_get("/health", self._health_handler)
        app.router.add_get("/metrics", self._metrics_handler)
        app.router.add_get("/dashboard", dashboard_handler)
        app.router.add_get("/api/dashboard", api_dashboard_handler)
        app.router.add_get("/guide", guide_handler)
        app.router.add_get("/smoke", self._smoke_handler)
        app.router.add_post("/webhook/{source}", self._webhook_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
        await site.start()
        self._health_runner = runner
        log.info("Health endpoint listening on :%d/health (and /metrics, /smoke, /dashboard, /guide, /webhook/<source>)", HEALTH_PORT)

    async def _smoke_handler(self, _request: web.Request) -> web.Response:
        """Run lightweight subsystem smoke tests and return JSON results."""
        from datetime import datetime, timezone

        checks: dict[str, dict] = {}
        overall = "pass"

        # 1. gemini_api
        try:
            t0 = time.monotonic()
            from llm import _get_model
            model = await asyncio.wait_for(_get_model(), timeout=10)
            resp = await asyncio.wait_for(
                asyncio.to_thread(model.generate_content, "Say hello"),
                timeout=10,
            )
            latency = round((time.monotonic() - t0) * 1000)
            if resp and resp.text:
                checks["gemini_api"] = {"status": "pass", "latency_ms": latency}
            else:
                checks["gemini_api"] = {"status": "fail", "error": "empty response"}
                overall = "fail"
        except Exception as exc:
            checks["gemini_api"] = {"status": "fail", "error": str(exc)[:200]}
            overall = "fail"

        # 2. ollama
        try:
            from llm import _ollama_available, LOCAL_LLM_ENABLED
            if not LOCAL_LLM_ENABLED:
                checks["ollama"] = {"status": "skipped", "reason": "LOCAL_LLM_ENABLED=false"}
            else:
                t0 = time.monotonic()
                up = await asyncio.wait_for(_ollama_available(), timeout=10)
                latency = round((time.monotonic() - t0) * 1000)
                if up:
                    checks["ollama"] = {"status": "pass", "latency_ms": latency}
                else:
                    checks["ollama"] = {"status": "fail", "error": "ollama not reachable"}
                    overall = "fail"
        except Exception as exc:
            checks["ollama"] = {"status": "fail", "error": str(exc)[:200]}
            overall = "fail"

        # 3. chromadb
        try:
            t0 = time.monotonic()
            from vector_store import _get_client
            client = _get_client()
            client.heartbeat()
            latency = round((time.monotonic() - t0) * 1000)
            checks["chromadb"] = {"status": "pass", "latency_ms": latency}
        except Exception as exc:
            checks["chromadb"] = {"status": "fail", "error": str(exc)[:200]}
            overall = "fail"

        # 4. memory_sqlite
        try:
            import sqlite3 as _sqlite3
            from thread_store import DB_PATH as _threads_db_path
            t0 = time.monotonic()
            conn = _sqlite3.connect(str(_threads_db_path), timeout=5)
            try:
                row = conn.execute("SELECT count(*) FROM threads").fetchone()
                thread_count = row[0] if row else 0
            finally:
                conn.close()
            latency = round((time.monotonic() - t0) * 1000)
            checks["memory_sqlite"] = {"status": "pass", "threads": thread_count}
        except Exception as exc:
            checks["memory_sqlite"] = {"status": "fail", "error": str(exc)[:200]}
            overall = "fail"

        # 5. config
        try:
            from config import cfg as _cfg
            if _cfg.discord_bot_token and _cfg.google_api_key:
                checks["config"] = {"status": "pass"}
            else:
                missing = []
                if not _cfg.discord_bot_token:
                    missing.append("discord_bot_token")
                if not _cfg.google_api_key:
                    missing.append("google_api_key")
                checks["config"] = {"status": "fail", "error": f"missing: {', '.join(missing)}"}
                overall = "fail"
        except Exception as exc:
            checks["config"] = {"status": "fail", "error": str(exc)[:200]}
            overall = "fail"

        # 6. skill_registry
        try:
            from skills import SKILLS as _skills
            count = len(_skills)
            has_search = "search_web" in _skills
            if count > 0 and has_search:
                checks["skill_registry"] = {"status": "pass", "skill_count": count}
            else:
                checks["skill_registry"] = {
                    "status": "fail",
                    "error": f"count={count}, search_web={'found' if has_search else 'missing'}",
                }
                overall = "fail"
        except Exception as exc:
            checks["skill_registry"] = {"status": "fail", "error": str(exc)[:200]}
            overall = "fail"

        payload = {
            "status": overall,
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        status_code = 200 if overall == "pass" else 503
        return web.json_response(payload, status=status_code)

    async def _health_handler(self, _request: web.Request) -> web.Response:
        uptime_s = time.monotonic() - self.start_time
        payload = {
            "status": "healthy",
            "uptime_seconds": round(uptime_s, 1),
            "bot_user": str(self.user) if self.user else None,
            "guilds": len(self.guilds),
            "python": platform.python_version(),
            "discord_py": discord.__version__,
        }
        return web.json_response(payload)

    async def _metrics_handler(self, _request: web.Request) -> web.Response:
        """Expose Prometheus-format metrics for Grafana / Uptime Kuma scraping."""
        uptime_s = time.monotonic() - self.start_time
        guilds = len(self.guilds)
        latency_ms = round(self.latency * 1000, 1) if self.latency else 0

        lines = [
            "# HELP openclaw_up Whether the bot is running (1=up)",
            "# TYPE openclaw_up gauge",
            "openclaw_up 1",
            "",
            "# HELP openclaw_uptime_seconds Seconds since bot started",
            "# TYPE openclaw_uptime_seconds counter",
            f"openclaw_uptime_seconds {uptime_s:.1f}",
            "",
            "# HELP openclaw_guilds Number of Discord guilds connected to",
            "# TYPE openclaw_guilds gauge",
            f"openclaw_guilds {guilds}",
            "",
            "# HELP openclaw_latency_ms Discord gateway latency in milliseconds",
            "# TYPE openclaw_latency_ms gauge",
            f"openclaw_latency_ms {latency_ms}",
            "",
        ]
        return web.Response(
            text="\n".join(lines),
            content_type="text/plain",
        )

    async def _webhook_handler(self, request: web.Request) -> web.Response:
        """Receive inbound webhooks from Sonarr, Radarr, Plex, qBittorrent, etc.

        POST /webhook/<source>
        Payload: arbitrary JSON from the upstream service.
        The handler formats a human-readable Discord notification and posts it
        to ALERT_CHANNEL_ID (if configured), then returns 200 OK.
        """
        if WEBHOOK_SECRET:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {WEBHOOK_SECRET}":
                return web.json_response({"error": "unauthorized"}, status=401)

        from webhook_formatter import FORMATTERS, format_generic

        source = request.match_info.get("source", "unknown").lower()
        try:
            payload = await request.json()
        except Exception as exc:
            log.debug("Webhook JSON parse failed: %s", exc)
            payload = {}

        if not isinstance(payload, dict):
            payload = {"raw": str(payload)}

        # -- Format by source --------------------------------------------------
        formatter = FORMATTERS.get(source)
        if formatter:
            title, description, color = formatter(payload)
        else:
            title, description, color = format_generic(source, payload)
        log.info("Webhook received from %s: %s", source, description[:120])

        if ALERT_CHANNEL_ID:
            channel = self.get_channel(ALERT_CHANNEL_ID)
            if channel:
                embed = discord.Embed(title=title, description=description, color=color)
                embed.set_footer(text=f"Incoming webhook → {source}")
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    log.error("Failed to send webhook notification: %s", e)

                # Auto-analyze if the payload contains error/failure signals
                _error_keywords = {"error", "fail", "critical", "down", "unhealthy", "exception", "warning"}
                payload_lower = json.dumps(payload).lower()
                event_lower = (payload.get("eventType") or payload.get("event") or "").lower()
                is_error_event = (
                    any(kw in payload_lower for kw in _error_keywords)
                    or event_lower in ("error", "warning", "applicationupdate", "health")
                )
                if is_error_event:
                    asyncio.create_task(self._analyze_webhook_event(source, payload, channel))

        return web.json_response({"ok": True})


bot = OpenClawBot()

# ---------------------------------------------------------------------------
# Slash commands — Phase 1 (foundation)
# ---------------------------------------------------------------------------


@bot.tree.command(name="ping", description="Check if OpenClaw is alive")
@require_auth
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000, 1)
    uptime_s = round(time.monotonic() - bot.start_time)
    hours, remainder = divmod(uptime_s, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    embed = discord.Embed(
        title="🏓 Pong!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Latency", value=f"{latency_ms} ms", inline=True)
    embed.add_field(name="Uptime", value=uptime_str, inline=True)
    embed.set_footer(text=f"OpenClaw v{VERSION} \u2022 Phase 5")

    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "ping", f"latency={latency_ms}ms")


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


@bot.tree.command(name="whoami", description="Show your Discord identity and permission level")
@require_auth
async def whoami(interaction: discord.Interaction):
    allowed = is_allowed(interaction)
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

    embed.set_footer(text=f"OpenClaw v{VERSION} \u2022 Phase 5")
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "help")


# ---------------------------------------------------------------------------
# Docker / infra commands → cogs/docker_cog.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Slash commands — Phase 3 (LLM integration)
# ---------------------------------------------------------------------------

# Discord embed description limit is 4096 chars; stay safely under it
_EMBED_LIMIT = EMBED_SPLIT_LIMIT

# Regex to find image URLs in LLM responses:
#   - Markdown images: ![alt](url)
#   - Photo links the prompt produces: [📸 ...](url)  or  [Photo](url)
#   - Bare image URLs on their own line

_IMAGE_LINK_RE = re.compile(
    r"!?\[(?:[^\]]*(?:photo|image|📸|🖼️|property|listing)[^\]]*)\]\((https?://[^)]+)\)",
    re.IGNORECASE,
)
_BARE_IMAGE_RE = re.compile(
    r"(https?://\S+\.(?:jpg|jpeg|png|webp|gif)(?:\?\S*)?)",
    re.IGNORECASE,
)


def _extract_image_url(text: str) -> str | None:
    """Return the first image URL found in the response text, or None."""
    m = _IMAGE_LINK_RE.search(text)
    if m:
        return m.group(1)
    m = _BARE_IMAGE_RE.search(text)
    if m:
        return m.group(1)
    return None


def _split_response(text: str) -> list[str]:
    """
    Split a long response into chunks that fit within Discord's embed limit.
    Tries to break on newlines to avoid cutting mid-sentence.
    Appends a continuation marker when a hard character split is needed.
    """
    if len(text) <= _EMBED_LIMIT:
        return [text]

    chunks = []
    while text:
        if len(text) <= _EMBED_LIMIT:
            chunks.append(text)
            break
        # Try to split on the last newline within the limit
        split_at = text.rfind("\n", 0, _EMBED_LIMIT)
        if split_at <= 0:
            # Hard cut — no newline found; mark the boundary for readability
            split_at = _EMBED_LIMIT
            chunks.append(text[:split_at] + "…")
            text = "…" + text[split_at:].lstrip("\n")
        else:
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Streaming: progressive Discord message edits
# ---------------------------------------------------------------------------

# Minimum interval (seconds) between Discord message edits to stay under rate limits
_STREAM_EDIT_INTERVAL = 1.5


# ---------------------------------------------------------------------------
# File attachment extraction — detect code blocks and offer as files
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(
    r"```(\w+)?\n([\s\S]+?)```",
)


def _extract_file_attachment(text: str) -> tuple[discord.File, str] | None:
    """If the response contains a large code block (>500 chars), extract it as a discord.File.

    Returns ``(discord.File, language)`` or ``None``.
    """
    matches = list(_CODE_BLOCK_RE.finditer(text))
    if not matches:
        return None

    # Find the largest code block
    best = max(matches, key=lambda m: len(m.group(2)))
    code = best.group(2).strip()
    lang = (best.group(1) or "txt").lower()

    if len(code) < 500:
        return None

    ext_map = {
        "python": "py", "py": "py", "javascript": "js", "js": "js",
        "typescript": "ts", "ts": "ts", "json": "json", "yaml": "yaml",
        "yml": "yaml", "html": "html", "css": "css", "sql": "sql",
        "bash": "sh", "sh": "sh", "csv": "csv", "markdown": "md", "md": "md",
    }
    ext = ext_map.get(lang, "txt")

    buffer = io.BytesIO(code.encode("utf-8"))
    return discord.File(buffer, filename=f"openclaw_output.{ext}"), lang


# ---------------------------------------------------------------------------
# Reaction-based action buttons on responses
# ---------------------------------------------------------------------------

class ResponseActions(discord.ui.View):
    """Buttons attached to /ask responses: Save, Regenerate, Email."""

    def __init__(
        self, *, response_text: str, question: str, user_id: int, channel_id: int, timeout: float = 300
    ):
        super().__init__(timeout=timeout)
        self._response_text = response_text
        self._question = question
        self._user_id = user_id
        self._channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the original requester can use these buttons."""
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Only the original requester can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📌 Save", style=discord.ButtonStyle.secondary)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            fact = self._response_text[:500]
            result = await remember_fact(
                f"Saved from /ask: {self._question[:100]}", fact
            )
            await interaction.followup.send(f"📌 Saved to memory.\n{result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Save failed: {e}", ephemeral=True)

    @discord.ui.button(label="🔄 Regenerate", style=discord.ButtonStyle.secondary)
    async def regen_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        conv = conversation_store.get(
            user_id=self._user_id,
            channel_id=self._channel_id,
            user_name=str(interaction.user.display_name),
        )
        # Remove the last exchange so the model regenerates
        if len(conv.history) >= 2:
            conv.history = conv.history[:-2]
        try:
            response_text, updated_history, model_used = await llm_chat(
                user_message=self._question,
                history=conv.history,
                user_name=str(interaction.user.display_name),
            )
            conv.update_from_llm(updated_history)
            embed = discord.Embed(description=response_text[:_EMBED_LIMIT], color=discord.Color.purple())
            embed.set_footer(text=f"🔄 Regenerated | via {model_used}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ Regeneration failed: {e}")

    @discord.ui.button(label="📧 Email", style=discord.ButtonStyle.secondary)
    async def email_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await send_agent_mail(
                subject=f"OpenClaw: {self._question[:80]}",
                body=self._response_text,
            )
            await interaction.followup.send(f"📧 Emailed!\n{result}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Email failed: {e}", ephemeral=True)


async def _handle_image_attachment(
    attachment: discord.Attachment, question: str
) -> str:
    """Download and analyze an image attachment via Gemini vision.

    Returns the augmented question string with the analysis appended.
    """
    try:
        session = _get_bot_http_session()
        async with session.get(
            attachment.url, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status == 200:
                img_bytes = await resp.read()
                mime = (attachment.content_type or "").split(";")[0].strip()
                image_answer = await llm_analyze_image(img_bytes, mime, question)
                return f"{question}\n\n[Attachment analysis: {image_answer}]"
    except Exception as e:
        log.warning("ask_cmd: failed to analyze image attachment: %s", e)
    return question


async def _handle_doc_attachment(
    attachment: discord.Attachment, question: str
) -> str:
    """Download and analyze a document attachment via Gemini.

    Returns the augmented question string with the document text appended.
    """
    try:
        session = _get_bot_http_session()
        async with session.get(
            attachment.url, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status == 200:
                raw = await resp.read()
                try:
                    doc_text = raw.decode("utf-8", errors="replace")[
                        :ATTACHMENT_TEXT_MAX_CHARS
                    ]
                except Exception as exc:
                    log.debug("Attachment text decode failed: %s", exc)
                    doc_text = ""
                if doc_text:
                    return f"{question}\n\n[Attached file `{attachment.filename}`]:\n{doc_text}"
    except Exception as e:
        log.warning("ask_cmd: failed to read attachment: %s", e)
    return question


@bot.tree.command(name="ask", description="Ask OpenClaw anything (AI-powered with function calling)")
@app_commands.describe(
    question="Your question or request",
    attachment="Optional image or document to include in your question",
    model="LLM routing: auto (smart), local (Gemma), gemini (cloud), openai (GPT-4o), or anthropic (Claude)",
)
@app_commands.choices(model=[
    app_commands.Choice(name="🔄 Auto (smart routing)", value="auto"),
    app_commands.Choice(name="🏠 Local (Gemma/Ollama)", value="local"),
    app_commands.Choice(name="☁️ Gemini (cloud)", value="gemini"),
    app_commands.Choice(name="🟢 OpenAI (GPT-4o)", value="openai"),
    app_commands.Choice(name="🟣 Anthropic (Claude)", value="anthropic"),
])
async def ask_cmd(
    interaction: discord.Interaction,
    question: str,
    attachment: discord.Attachment | None = None,
    model: app_commands.Choice[str] | None = None,
):
    """Main user query handler — routes to Gemini (tool-capable) or Ollama (conversational).

    Handles: emergency-stop gating, attachment analysis (images/PDFs/text),
    memory context injection, streaming responses to Discord, and post-response
    action buttons (save, regenerate, email).
    """

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

    # Progressive status: edit the deferred "thinking" placeholder as each tool fires
    async def _on_tool_call(tool_name: str, round_num: int) -> None:
        try:
            await interaction.edit_original_response(
                content=f"🔄 *Using `{tool_name}`…* (step {round_num})"
            )
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

    # Get or create conversation context
    conv = conversation_store.get(
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        user_name=str(interaction.user.display_name),
    )

    # Thread continuation suggestion — on new conversations, check for related threads
    thread_hint = ""
    if not conv.history:
        try:
            import vector_store
            hits = await vector_store.search(
                vector_store.CONVERSATIONS_COLLECTION, question, top_k=1, threshold=0.75
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
        except Exception:
            pass  # non-critical

    # Channel role injection — inject prompt once at session start to prevent context bleed
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
    # Resolve model preference: per-message override > sticky user pref > auto
    model_pref = model.value if model else get_model_preference(interaction.user.id)

    # Guardrail: if user picks "local" but query clearly needs tools, auto-upgrade
    from llm import _needs_tools as llm_needs_tools
    if model_pref == "local" and llm_needs_tools(question):
        model_pref = "gemini"
        guardrail_note = "\n\n> ⚡ *Auto-upgraded to Gemini (your query requires tool access)*"
    else:
        guardrail_note = ""

    try:
        # ── Contextual recall: inject relevant memories before LLM call ───
        try:
            import vector_store
            context_hits = await vector_store.recall(question, top_k=3)
            if context_hits:
                conv.history.append({
                    "role": "model",
                    "parts": [f"[Relevant context from memory]\n{context_hits}"],
                })
        except Exception as e:
            log.debug("Contextual recall skipped: %s", e)

        # ── Inject learned rules relevant to this query (Phase 14A) ───
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

        # ── Inject user profile context (Phase 14C) ───
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

        # ── Streaming response with progressive Discord edits ────────────
        last_edit = 0.0
        display_question = question if len(question) < 200 else question[:197] + "..."

        async for chunk_text, is_final, meta in llm_chat_stream(
            user_message=question,
            history=conv.history,
            user_name=str(interaction.user.display_name),
            on_tool_call=_on_tool_call,
            model_preference=model_pref,
        ):
            model_used = meta.get("model_used", "unknown")

            if is_final:
                response_text = chunk_text
                if "updated_history" in meta:
                    conv.update_from_llm(meta["updated_history"])
                    conversation_store.auto_save_thread(
                        interaction.user.id, interaction.channel_id, str(interaction.user.display_name)
                    )
                break

            # Progressive edit: update every _STREAM_EDIT_INTERVAL seconds
            now = time.monotonic()
            if now - last_edit >= _STREAM_EDIT_INTERVAL and chunk_text:
                try:
                    # Show streaming text in an embed (truncated to embed limit)
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

    except Exception as e:
        log.error("LLM error: %s", e)
        safe_question = discord.utils.escape_markdown(question)
        response_text = (
            f"❌ **LLM Error:** {str(e)}\n\n"
            "**Your message was saved below for easy copy-pasting/retry:**\n"
            f"```\n{safe_question}\n```"
        )
        model_used = "error"

    # ── Final response with embeds, file attachments, and action buttons ──
    if guardrail_note:
        response_text += guardrail_note
    if thread_hint:
        response_text += thread_hint
    chunks = _split_response(response_text)
    image_url = _extract_image_url(response_text)
    file_attachment = _extract_file_attachment(response_text)

    # Build the action buttons view
    action_view = ResponseActions(
        response_text=response_text,
        question=question,
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
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
            if model_used and "gemini" not in model_used.lower():
                rate_str = "local · unlimited"
            else:
                rate_str = get_rate_info()
            mode_label = {"auto": "🔄", "local": "🏠", "gemini": "☁️", "openai": "🟢", "anthropic": "🟣"}.get(model_pref, "🔄")
            embed.set_footer(text=f"💬 {conv.message_count} msgs | {rate_str} | {mode_label} {model_used}")

        if i == 0:
            kwargs: dict[str, Any] = {"content": None, "embed": embed}
            if is_last:
                kwargs["view"] = action_view
            if file_attachment and is_last:
                kwargs["attachments"] = [file_attachment[0]]
            await interaction.edit_original_response(**kwargs)
        else:
            kwargs = {"embed": embed}
            if is_last:
                kwargs["view"] = action_view
            if file_attachment and is_last:
                kwargs["file"] = file_attachment[0]
            await interaction.followup.send(**kwargs)

    audit_log(interaction.user, "ask", detail=question[:200])
    conversation_store.cleanup_expired()

    # ── Fire-and-forget: correction detection & profile learning (Phase 14) ──
    async def _post_response_learning():
        try:
            # Correction detection → rule learning
            from rules_engine import detect_correction, extract_rule, add_rule
            if detect_correction(question):
                # Get the bot's previous response (second-to-last model message)
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
                        except Exception:
                            pass
        except Exception as e:
            log.debug("Correction detection failed (non-critical): %s", e)

        try:
            # Profile auto-learning
            from user_profile import learn_from_message
            await learn_from_message(question, response_text)
        except Exception as e:
            log.debug("Profile learning failed (non-critical): %s", e)

        try:
            # Automatic fact extraction from conversation
            from fact_extractor import should_extract, extract_and_store_facts
            if should_extract(interaction.user.id, question):
                await extract_and_store_facts(question, response_text, interaction.user.id)
        except Exception as e:
            log.debug("Fact extraction failed (non-critical): %s", e)

    asyncio.get_running_loop().create_task(_post_response_learning())


@bot.tree.command(name="clear", description="Clear your conversation history with OpenClaw")
@require_auth
async def clear_cmd(interaction: discord.Interaction):
    conversation_store.clear_user(interaction.user.id, interaction.channel_id)
    await interaction.response.send_message("🧹 Conversation cleared. Starting fresh!", ephemeral=True)
    audit_log(interaction.user, "clear")


# ---------------------------------------------------------------------------
# /model — View or change LLM routing preference
# ---------------------------------------------------------------------------

model_group = app_commands.Group(name="model", description="View or change your LLM model preference")


@model_group.command(name="show", description="Show your current model routing preference")
async def model_show_cmd(interaction: discord.Interaction):
    pref = get_model_preference(interaction.user.id)
    labels = {"auto": "🔄 Auto (smart routing)", "local": "🏠 Local (Gemma/Ollama)", "gemini": "☁️ Gemini (cloud)", "openai": "🟢 OpenAI (GPT-4o)", "anthropic": "🟣 Anthropic (Claude)"}
    embed = discord.Embed(
        title="🤖 Model Preference",
        description=f"**Current:** {labels.get(pref, pref)}\n\n"
        "Use `/model set` to change.\n"
        "Use `/ask model:` to override per-message.",
        color=discord.Color.blue(),
    )
    # Show Ollama status
    try:
        from llm import _ollama_available, LOCAL_LLM_ENABLED, OLLAMA_MODEL
        ollama_up = await _ollama_available() if LOCAL_LLM_ENABLED else False
        status = f"{'🟢' if ollama_up else '🔴'} Ollama ({OLLAMA_MODEL}): {'online' if ollama_up else 'offline'}"
        if not LOCAL_LLM_ENABLED:
            status = "⚪ Local LLM disabled"
        embed.add_field(name="Local LLM", value=status, inline=False)
    except Exception:
        pass
    await interaction.response.send_message(embed=embed, ephemeral=True)


@model_group.command(name="set", description="Set your default LLM routing preference")
@app_commands.describe(preference="Which model to use by default")
@app_commands.choices(preference=[
    app_commands.Choice(name="🔄 Auto — smart routing (default)", value="auto"),
    app_commands.Choice(name="🏠 Local — Gemma/Ollama (free, fast)", value="local"),
    app_commands.Choice(name="☁️ Gemini — cloud (tools, best quality)", value="gemini"),
    app_commands.Choice(name="🟢 OpenAI — GPT-4o via Copilot", value="openai"),
    app_commands.Choice(name="🟣 Anthropic — Claude via Copilot", value="anthropic"),
])
async def model_set_cmd(interaction: discord.Interaction, preference: app_commands.Choice[str]):
    result = set_model_preference(interaction.user.id, preference.value)
    await interaction.response.send_message(result, ephemeral=True)
    audit_log(interaction.user, "model_set", detail=preference.value)


bot.tree.add_command(model_group)


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

    # Keyword search in SQLite thread store
    try:
        from thread_store import search_threads as sqlite_search
        db_results = await sqlite_search(interaction.user.id, query, limit=10)
    except Exception as e:
        log.debug("SQLite thread search failed: %s", e)
        db_results = []

    # Semantic search in ChromaDB conversations collection
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

    # Format results
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


# ---------------------------------------------------------------------------
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


@bot.tree.command(name="skills", description="List all available OpenClaw skills")
@require_auth
async def skills_cmd(interaction: discord.Interaction):

    lines = []
    for name, fn in sorted(SKILLS.items()):
        doc = (fn.__doc__ or "No description").strip().split("\n")[0][:80]
        lines.append(f"• `{name}` — {doc}")

    embed = discord.Embed(
        title=f"🧰 Available Skills ({len(SKILLS)})",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Skills are callable by the LLM via /ask or via scheduled tasks")
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "skills")


# ---------------------------------------------------------------------------
# Slash commands \u2014 Phase 4 (security & approvals)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# QMD / AgentMail commands
# ---------------------------------------------------------------------------


@bot.tree.command(name="remember", description="Store a fact in long-term memory (QMD)")
@app_commands.describe(content="Fact to remember", tags="Comma-separated tags")
@require_auth
async def remember_cmd(interaction: discord.Interaction, content: str, tags: str = ""):
    result = await remember_fact(content, tags)
    await interaction.response.send_message(result)
    audit_log(interaction.user, "remember", detail=content)


@bot.tree.command(name="recall", description="Search long-term memory (QMD)")
@app_commands.describe(query="Keywords to search for")
@require_auth
async def recall_cmd(interaction: discord.Interaction, query: str):
    result = await recall_fact(query)
    embed = discord.Embed(title=f"🧠 Recall: {query}", description=result, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "recall", detail=query)


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


# ---------------------------------------------------------------------------
# Phase 8: Web Search, Browsing, Image Analysis, Document Analysis
# ---------------------------------------------------------------------------


@bot.tree.command(name="websearch", description="Search the live web for current information")
@app_commands.describe(query="What to search for", results="Number of results (1-10, default 5)")
@require_auth
async def websearch_cmd(interaction: discord.Interaction, query: str, results: int = 5):
    await interaction.response.defer()
    result = await search_web(query, num_results=results)
    result = truncate_for_embed(result)
    embed = discord.Embed(
        title=f"🔍 Web Search: {query[:80]}",
        description=result,
        color=discord.Color.blue(),
    )
    embed.set_footer(text="via Tavily AI Search (with DuckDuckGo fallback)")
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "websearch", detail=query)


@bot.tree.command(name="browse", description="Fetch and read the content of a web page")
@app_commands.describe(url="URL to fetch (must start with http:// or https://)", question="Optional: what to focus on")
@require_auth
async def browse_cmd(interaction: discord.Interaction, url: str, question: str = ""):
    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        await interaction.response.send_message(
            "❌ URL must start with `http://` or `https://`", ephemeral=True
        )
        return
    await interaction.response.defer()
    page_text = await browse_url(url)
    if question and not page_text.startswith("❌") and not page_text.startswith("⚠️"):
        # Use Gemini to answer a specific question about the page content
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
        color=discord.Color.teal(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "browse", detail=url)


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
        session = _get_bot_http_session()
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

    # 25MB Discord limit — we enforce 20MB to be safe
    if file.size > MAX_FILE_SIZE:
        await interaction.response.send_message("❌ File too large (max 20 MB).", ephemeral=True)
        return

    filename = file.filename.lower()
    mime = (file.content_type or "").split(";")[0].strip()

    await interaction.response.defer()

    # Download the file bytes
    try:
        session = _get_bot_http_session()
        async with session.get(file.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                await interaction.followup.send(f"❌ Could not download file (HTTP {resp.status}).")
                return
            file_bytes = await resp.read()
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to download file: {e}")
        return

    # Extract text based on file type
    extracted_text: str | None = None
    file_type_label = "text"

    if filename.endswith(".pdf") or mime == "application/pdf":
        file_type_label = "PDF"
        try:
            import io
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for page in reader.pages[:PDF_MAX_PAGES]:  # limit to first 50 pages
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
        # Treat as plain text (txt, log, csv, json, yaml, md, py, js, etc.)
        file_type_label = filename.rsplit(".", 1)[-1].upper() if "." in filename else "text"
        try:
            extracted_text = file_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            await interaction.followup.send(f"❌ Could not decode file as text: {e}")
            return

    # Free raw bytes now that text has been extracted
    del file_bytes

    # Trim to a reasonable context size (Gemini handles large inputs, but be practical)
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


# ---------------------------------------------------------------------------
# Mission Control — Kanban task board
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Phase B: Research command — fire-and-forget multi-step autonomous research
# Phase E: Weather command
# Bookmark command — save URLs and notes to the Obsidian vault
# ---------------------------------------------------------------------------


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

        # Try to fetch + summarize the page for richer vault notes
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
                # Extract page title from first H1 for the vault filename
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



class _ResearchView(discord.ui.View):
    """Action buttons attached to a completed research report."""

    def __init__(self, query: str, report: str):
        super().__init__(timeout=300)
        self._query = query
        self._report = report

    @discord.ui.button(label="📌 Save to Memory", style=discord.ButtonStyle.secondary)
    async def save_to_memory(self, interaction: discord.Interaction, _button: discord.ui.Button):
        # Store first 500 chars as the memory fact
        snippet = self._report[:MEMORY_SNIPPET_MAX_CHARS].strip()
        result = await remember_fact(
            content=f"[Research] {self._query}: {snippet}",
            tags="research",
        )
        await interaction.response.send_message(result, ephemeral=True)
        audit_log(interaction.user, "research_save_memory", detail=self._query[:80])

    @discord.ui.button(label="🔄 Re-run in 24h", style=discord.ButtonStyle.secondary)
    async def schedule_rerun(self, interaction: discord.Interaction, _button: discord.ui.Button):
        task = scheduler.create(
            action="search_web",
            args={"query": self._query, "num_results": 5},
            hour=-1,
            minute=0,
            interval_minutes=1440,  # 24 hours
            created_by=str(interaction.user),
        )
        await interaction.response.send_message(
            f"✅ Scheduled daily re-search for **{self._query[:60]}** (task `{task.task_id}`).",
            ephemeral=True,
        )
        audit_log(interaction.user, "research_schedule_rerun", detail=self._query[:80])


@bot.tree.command(name="research", description="Autonomous multi-step research — searches, reads sources, synthesizes a report")
@app_commands.describe(query="What you want researched (be specific for best results)")
@require_auth
async def research_cmd(interaction: discord.Interaction, query: str):

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

    # Acknowledge the interaction immediately (Discord requires response within 3s)
    await interaction.response.send_message(
        f"🔍 **Research started** — I'll post updates and a final report here.\n> {query[:120]}"
    )
    original = await interaction.original_response()

    # Create a Discord thread for streaming progress
    try:
        thread = await original.create_thread(
            name=f"Research: {query[:80]}",
            auto_archive_duration=1440,  # archive after 24h idle
        )
        await thread.send(f"🗺️ Planning research strategy…")
    except discord.HTTPException as e:
        log.warning("Could not create research thread: %s", e)
        thread = None

    async def on_progress(msg: str):
        """Stream step updates to the Discord thread."""
        if thread:
            try:
                await thread.send(msg)
            except Exception as exc:
                log.debug("Research progress send failed: %s", exc)

    # Run the research agent
    agent = ResearchAgent(max_searches=4, browse_top_n=2, timeout_seconds=180)

    try:
        report = await agent.run(query, on_progress=on_progress)
    except Exception as e:
        log.error("Research command failed: %s", e)
        report = f"❌ Research failed: {e}"

    # Post the final report to the thread with action buttons
    view = _ResearchView(query=query, report=report)
    chunks = _split_response(report)
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(
            description=chunk,
            color=discord.Color.from_rgb(0, 150, 200),
        )
        if i == 0:
            embed.set_author(name=f"Research: {query[:100]}")
        if i == len(chunks) - 1:
            embed.set_footer(text="✅ Research complete — Gemini 2.5 Flash with extended thinking")
            target = thread or interaction
            if thread:
                await thread.send(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed, view=view)
        else:
            if thread:
                await thread.send(embed=embed)
            else:
                await interaction.followup.send(embed=embed)

    audit_log(interaction.user, "research", detail=query[:200])


@bot.tree.command(name="research-search", description="Search across all your past research reports by topic")
@app_commands.describe(query="What to search for in past research")
@require_auth
async def research_search_cmd(interaction: discord.Interaction, query: str):
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
    except Exception as e:
        lines.append(f"⚠️ Search unavailable: {e}")

    await interaction.followup.send("\n".join(lines), ephemeral=True)
    audit_log(interaction.user, "research_search", detail=query)


@bot.tree.command(name="sources", description="Search your library of previously browsed web sources")
@app_commands.describe(query="Topic or keyword to find in past browsed sources")
@require_auth
async def sources_cmd(interaction: discord.Interaction, query: str):
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
    except Exception as e:
        lines.append(f"⚠️ Source search unavailable: {e}")

    await interaction.followup.send("\n".join(lines), ephemeral=True)
    audit_log(interaction.user, "sources_search", detail=query)


@bot.tree.command(name="memory-stats", description="Show memory and vector store statistics")
@require_auth
async def memory_stats_cmd(interaction: discord.Interaction):
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


@bot.tree.command(name="memory-refresh", description="Reinforce a memory so it doesn't decay (bump its access score)")
@app_commands.describe(query="Search query to find the memory to reinforce")
@require_auth
async def memory_refresh_cmd(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    try:
        import vector_store
        results = await vector_store.search_all(query, top_k=3)
        if not results:
            await interaction.followup.send("No matching memories found.", ephemeral=True)
            return
        # Bump access count for all matching results
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


@bot.tree.command(name="rules", description="View or manage learned behavioral rules")
@app_commands.describe(action="list (default), search, or delete", query="Search query or rule ID to delete")
@require_auth
async def rules_cmd(interaction: discord.Interaction, action: str = "list", query: str = ""):
    await interaction.response.defer(ephemeral=True)
    try:
        from rules_engine import get_all_rules, get_relevant_rules, delete_rule

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
        for r in all_rules[-20:]:  # show last 20
            lines.append(f"• {r['rule']}  `{r['id']}`")
        if len(all_rules) > 20:
            lines.append(f"\n_...and {len(all_rules) - 20} more (use `/rules action:search` to find specific rules)_")
        await interaction.followup.send("\n".join(lines), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Rules unavailable: {e}", ephemeral=True)
    audit_log(interaction.user, "rules", detail=f"{action} {query}")


@bot.tree.command(name="profile", description="View your user profile (preferences, interests, tools)")
@require_auth
async def profile_cmd(interaction: discord.Interaction):
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


@bot.tree.command(name="profile-edit", description="Manually update your user profile")
@app_commands.describe(
    field="Field to update: preference, interest, note, working_style, communication_style",
    value="Value to set (for preference, use 'key=value' format)",
)
@require_auth
async def profile_edit_cmd(interaction: discord.Interaction, field: str, value: str):
    await interaction.response.defer(ephemeral=True)
    try:
        from user_profile import update_preference, add_interest, add_context_note, update_field, sync_profile_to_vectors

        if field == "preference" and "=" in value:
            k, v = value.split("=", 1)
            update_preference(k.strip(), v.strip())
            msg = f"✅ Preference set: `{k.strip()}` = {v.strip()}"
        elif field == "interest":
            add_interest(value)
            msg = f"✅ Interest added: {value}"
        elif field == "note":
            add_context_note(value)
            msg = f"✅ Context note added"
        elif field in ("working_style", "communication_style"):
            update_field(field, value)
            msg = f"✅ {field.replace('_', ' ').title()} updated"
        else:
            msg = "❌ Unknown field. Use: preference, interest, note, working_style, or communication_style"

        # Sync to vectors
        try:
            await sync_profile_to_vectors()
        except Exception:
            pass

        await interaction.followup.send(msg, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Update failed: {e}", ephemeral=True)
    audit_log(interaction.user, "profile_edit", detail=f"{field}={value[:100]}")


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


# ---------------------------------------------------------------------------
# /plans — list active plans
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /plan-detail — show a specific plan
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /resume-plan — resume an interrupted plan
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /cancel-plan — cancel an active plan
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /diff — git status + diff summary
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /briefing — on-demand copy of the morning briefing
# ---------------------------------------------------------------------------


@bot.tree.command(name="briefing", description="Generate an on-demand morning briefing (weather, health, downloads, calendar)")
@require_auth
async def briefing_cmd(interaction: discord.Interaction):
    if not llm_is_configured():
        await interaction.response.send_message("⚠️ LLM not configured.", ephemeral=True)
        return
    await interaction.response.defer()
    # Reuse the same logic as the morning briefing — call it directly
    await bot._send_morning_briefing(channel_override=interaction.channel)
    # _send_morning_briefing posts to a channel; acknowledge the slash command
    try:
        await interaction.edit_original_response(content="✅ Briefing posted above.")
    except Exception as exc:
        log.debug("Briefing edit_original_response failed: %s", exc)
    audit_log(interaction.user, "briefing")


# ---------------------------------------------------------------------------
# Slash commands — Image generation (local Stable Diffusion)
# ---------------------------------------------------------------------------

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

    # Check if SD service is reachable
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

    image_bytes, status = await generate_image(
        prompt,
        negative_prompt=negative,
        width=width,
        height=height,
        steps=steps,
    )

    if image_bytes is None:
        await interaction.edit_original_response(content=f"❌ Image generation failed: {status}")
        return

    embed = discord.Embed(
        title="🎨 Generated Image",
        description=f"**Prompt:** {prompt[:200]}",
        color=discord.Color.blue(),
    )
    embed.set_footer(text=f"{width}×{height} · {steps} steps · local Stable Diffusion")
    file = discord.File(io.BytesIO(image_bytes), filename="openclaw_generated.png")
    embed.set_image(url="attachment://openclaw_generated.png")

    await interaction.edit_original_response(content=None, embed=embed, attachments=[file])
    audit_log(interaction.user, "imagine", detail=prompt[:200])


# ---------------------------------------------------------------------------
# Slash commands — Code execution sandbox
# ---------------------------------------------------------------------------

@bot.tree.command(name="run-code", description="Execute Python code in a sandboxed container (safe, isolated)")
@app_commands.describe(
    code="Python code to run (or wrap in a code block ```python ... ```)",
)
@require_auth
async def run_code_cmd(interaction: discord.Interaction, code: str):
    await interaction.response.defer()

    # Strip markdown code fence if present
    if code.startswith("```"):
        lines = code.split("\n")
        # Remove first line (```python) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        elif lines[0].strip().startswith("```"):
            lines[0] = ""
        code = "\n".join(lines).strip()

    if not code:
        await interaction.edit_original_response(content="❌ No code provided.")
        return

    # Safety: basic sanity check — no disk wiping, no network, etc.
    # (the sandbox itself enforces this, but let's give a clear error)
    if len(code) > 10_000:
        await interaction.edit_original_response(content="❌ Code too long (max 10,000 chars).")
        return

    await interaction.edit_original_response(content="⚙️ *Running code in sandboxed container…*")

    stdout, stderr, exit_code = await sandbox_run_code(code)

    # Format output
    parts = []
    if stdout:
        parts.append(f"**stdout:**\n```\n{stdout[:OUTPUT_MAX_CHARS]}\n```")
    if stderr:
        parts.append(f"**stderr:**\n```\n{stderr[:1500]}\n```")
    if not stdout and not stderr:
        parts.append("*(no output)*")

    status = "✅" if exit_code == 0 else "❌"
    header = f"{status} Exit code: {exit_code}"

    embed = discord.Embed(
        title="⚙️ Code Execution Result",
        description=f"{header}\n\n" + "\n".join(parts),
        color=discord.Color.green() if exit_code == 0 else discord.Color.red(),
    )
    embed.set_footer(text="Sandboxed · python:3.12-slim · no network · 256MB RAM · 30s timeout")

    # If output is very long, also attach as a file
    file = None
    if len(stdout) > OUTPUT_MAX_CHARS:
        file = discord.File(io.BytesIO(stdout.encode()), filename="output.txt")

    kwargs: dict[str, Any] = {"content": None, "embed": embed}
    if file:
        kwargs["attachments"] = [file]
    await interaction.edit_original_response(**kwargs)
    audit_log(interaction.user, "run_code", detail=code[:200])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set. Create a .env file or set the environment variable.")
        sys.exit(1)

    log.info("Starting OpenClaw bot...")
    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
