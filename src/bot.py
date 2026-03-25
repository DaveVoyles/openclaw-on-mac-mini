"""
OpenClaw Discord Bot - Phase 6: Remote Access & Monitoring
Autonomous AI agent for home automation and system management.
"""

import asyncio
import collections
import datetime
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path

import aiohttp
import discord
import yaml
from aiohttp import web
from discord import app_commands
from dotenv import load_dotenv

from skills import (
    get_container_logs,
    get_container_status,
    get_docker_stats,
    get_system_stats,
    get_uptime,
    list_containers,
    restart_container,
)
from skills.advanced_skills import (
    check_arr_health,
    check_download_clients,
    check_plex_status,
    check_service_ports,
    create_status_report,
    get_download_queue,
    get_recent_additions,
    ping_host,
    search_media,
)
from analyzer import analyze_logs
from scheduler import scheduler
from network import get_network_status, get_tailscale_status, run_speed_test

from llm import chat as llm_chat, is_configured as llm_is_configured, get_rate_info
from llm import analyze_image as llm_analyze_image, analyze_document as llm_analyze_document
from llm import SUPPORTED_IMAGE_MIMES
from memory import store as conversation_store
from spending import tracker as spending_tracker, get_spending, get_daily_spending
from dashboard import api_dashboard_handler, dashboard_handler, guide_handler
from approvals import (
    ApprovalView,
    RiskLevel,
    approval_store,
    build_approval_embed,
    is_emergency_stopped,
    set_emergency_stop,
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

VERSION = "0.6.0"

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
# Audit logger — buffered write to avoid per-command file open
# ---------------------------------------------------------------------------

AUDIT_DIR.mkdir(parents=True, exist_ok=True)

_audit_buffer: collections.deque = collections.deque(maxlen=10_000)


def audit_log(user: discord.User | discord.Member | None, action: str, detail: str = "", result: str = "success"):
    """Buffer an audit entry (flushed to disk every 30 seconds by _audit_writer)."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user": str(user) if user else "system",
        "user_id": str(user.id) if user else "0",
        "action": action,
        "detail": detail,
        "result": result,
    }
    _audit_buffer.append(entry)


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
    import functools

    @functools.wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
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
        except Exception:
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


class OpenClawBot(discord.Client):
    """Discord client with an application-command tree."""

    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.start_time = time.monotonic()
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self):
        """Load cogs and sync commands on startup."""
        # Load cog extensions
        await self.load_extension("cogs.docker_cog")
        log.info("Loaded cogs: docker_cog")

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

        # Start scheduler and register skills
        from skills import SKILLS as all_skills
        scheduler.register_skills(all_skills)
        scheduler.start()
        log.info("Scheduler started with %d registered skills", len(all_skills))

        # Wire scheduler → Discord notification callback
        async def _scheduler_notify(task_id: str, action: str, result: str, is_alert: bool) -> None:
            from skills import SKILLS as _sk
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
                description=result[:3800] or "(no output)",
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
        else:
            log.info("ALERT_CHANNEL_ID not set — proactive push notifications disabled")

    async def _audit_writer(self):
        """Flush buffered audit entries to disk every 30 seconds."""
        while True:
            await asyncio.sleep(30)
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
            await asyncio.sleep(300)  # every 5 minutes
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
        import datetime as _dt
        last_briefing_date: str = ""
        while True:
            try:
                now = _dt.datetime.now()
                if now.hour == 8 and now.minute < 5:
                    today_str = now.strftime("%Y-%m-%d")
                    if today_str != last_briefing_date:
                        last_briefing_date = today_str
                        asyncio.create_task(self._send_morning_briefing())
            except Exception as e:
                log.warning("Morning briefing scheduler error: %s", e)
            await asyncio.sleep(60)  # check every minute

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
            from skills.advanced_skills import check_arr_health, get_download_queue, get_weather
            from skills import get_system_stats
            from calendar_skills import get_upcoming_events
            from llm import chat as llm_chat

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
            except Exception:
                calendar = "Calendar not available."

            import datetime as _dt
            today = _dt.date.today().strftime("%A, %B %d, %Y")
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
                description=response_text[:4000],
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
        await asyncio.sleep(7200)
        while True:
            try:
                await self._run_proactive_scan()
            except Exception as e:
                log.warning("Proactive scan error: %s", e)
            await asyncio.sleep(7200)

    async def _run_proactive_scan(self):
        """Gather system signals + log snippets, ask Gemini for assessment, post if actionable."""
        if not ALERT_CHANNEL_ID:
            return

        from skills.advanced_skills import check_arr_health, check_download_clients, check_plex_status
        from skills import get_container_logs

        health, dl_clients, plex = await asyncio.gather(
            check_arr_health(),
            check_download_clients(),
            check_plex_status(),
            return_exceptions=True,
        )

        # Collect recent error-bearing log snippets from key containers
        key_containers = ["sonarr", "radarr", "sabnzbd", "plex"]
        log_snippets: dict[str, str] = {}
        _error_re = __import__("re").compile(r"error|warn|exception|critical|failed", __import__("re").IGNORECASE)
        for svc in key_containers:
            try:
                logs = await asyncio.wait_for(get_container_logs(svc, lines=25), timeout=6)
                if logs and _error_re.search(logs):
                    log_snippets[svc] = logs[:600]
            except Exception:
                pass

        # If all health checks look clean AND no logs have anomalies, skip LLM call
        all_clean = all(
            isinstance(r, str) and not _error_re.search(r)
            for r in [health, dl_clients, plex]
            if isinstance(r, str)
        )
        if all_clean and not log_snippets:
            log.debug("Proactive scan: all clear")
            return

        summary_parts = [
            f"Health checks:\n  *arr: {health}\n  Download clients: {dl_clients}\n  Plex: {plex}"
        ]
        if log_snippets:
            summary_parts.append("Log anomalies:")
            for svc, snippet in log_snippets.items():
                summary_parts.append(f"  {svc}:\n{snippet}")

        summary = "\n\n".join(summary_parts)

        prompt = (
            "You are OpenClaw's autonomous monitoring system running a background scan.\n"
            "Based on the signals below, determine if there is anything the operator should be "
            "aware of — errors, service failures, degraded performance, or unusual activity.\n"
            "ONLY respond if there is something genuinely actionable. "
            "If everything is within normal operation, respond with exactly: NO_ALERT\n\n"
            f"{summary[:3500]}"
        )

        try:
            analysis, _, _ = await asyncio.wait_for(llm_chat(prompt), timeout=35)
            if not analysis or "NO_ALERT" in analysis.upper():
                log.debug("Proactive scan: LLM found nothing notable")
                return

            channel = self.get_channel(ALERT_CHANNEL_ID)
            if not channel:
                return

            embed = discord.Embed(
                title="🔭 Proactive Insight",
                description=analysis[:3800],
                color=discord.Color.gold(),
            )
            embed.set_footer(text="Autonomous monitoring scan • every 2h")
            await channel.send(embed=embed)
            audit_log(None, "proactive_scan", detail="insight posted")
            log.info("Proactive scan posted an insight")
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
                    description=analysis[:1024],
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

        # Close all async sessions — log errors instead of silently swallowing
        _close_fns = [
            ("llm", lambda: __import__("llm").close_sessions()),
            ("gateway", lambda: __import__("gateway").close_gateway_session()),
            ("agentmail", lambda: __import__("agentmail").close_session()),
            ("overseerr", lambda: __import__("overseerr").close_session()),
            ("calendar_skills", lambda: __import__("calendar_skills").close_session()),
            ("nas", lambda: __import__("nas").close_session()),
            ("network", lambda: __import__("network").close_session()),
            ("advanced_skills", lambda: __import__("skills.advanced_skills", fromlist=["close_session"]).close_session()),
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
        app.router.add_post("/webhook/{source}", self._webhook_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
        await site.start()
        self._health_runner = runner
        log.info("Health endpoint listening on :%d/health (and /metrics, /dashboard, /guide, /webhook/<source>)", HEALTH_PORT)

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
        source = request.match_info.get("source", "unknown").lower()
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            payload = {"raw": str(payload)}

        # -- Format by source --------------------------------------------------
        title = f"🔔 Webhook: {source.capitalize()}"
        color = discord.Color.blurple()
        lines: list[str] = []

        if source in ("sonarr", "radarr", "lidarr"):
            event = payload.get("eventType", "Event")
            series = payload.get("series", {})
            movie = payload.get("movie", {})
            name = series.get("title") or movie.get("title") or payload.get("artist", {}).get("name", "Unknown")
            ep = payload.get("episodes", [{}])[0] if payload.get("episodes") else {}
            ep_title = ep.get("title", "")
            ep_num = f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}" if ep else ""
            lines.append(f"**Event**: {event}")
            lines.append(f"**Title**: {name}" + (f" — {ep_num} {ep_title}" if ep_title else ""))
            if payload.get("isUpgrade"):
                lines.append("⬆️ Quality upgrade")
            if event == "Grab":
                color = discord.Color.yellow()
            elif event == "Download":
                color = discord.Color.green()
            elif event in ("EpisodeFileDelete", "MovieFileDelete"):
                color = discord.Color.red()
                title = f"🗑️ {source.capitalize()}: File Deleted"

        elif source == "plex":
            event = payload.get("event", payload.get("type", "Event"))
            meta = payload.get("Metadata", {})
            p_title = meta.get("title", "Unknown")
            p_type = meta.get("type", "")
            user = payload.get("Account", {}).get("title", "")
            lines.append(f"**Event**: {event}")
            lines.append(f"**{'Episode' if p_type == 'episode' else 'Title'}**: {p_title}")
            if user:
                lines.append(f"**User**: {user}")
            if "play" in event.lower():
                color = discord.Color.green()
                title = "▶️ Plex: Now Playing"

        elif source == "qbittorrent":
            name = payload.get("name", payload.get("hash", "Unknown"))
            category = payload.get("category", "")
            lines.append(f"**Torrent**: {name}")
            if category:
                lines.append(f"**Category**: {category}")
            color = discord.Color.green()
            title = "✅ qBittorrent: Download Complete"

        else:
            # Generic fallback — show top-level keys
            for k, v in list(payload.items())[:8]:
                if isinstance(v, (str, int, float, bool)):
                    lines.append(f"**{k}**: {v}")

        description = "\n".join(lines) or "*(no details)*"
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
_EMBED_LIMIT = 3800


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


@bot.tree.command(name="ask", description="Ask OpenClaw anything (AI-powered with function calling)")
@app_commands.describe(
    question="Your question or request",
    attachment="Optional image or document to include in your question",
)
async def ask_cmd(
    interaction: discord.Interaction,
    question: str,
    attachment: discord.Attachment | None = None,
):

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
        except Exception:
            pass

    # If an attachment was provided, route through the appropriate analyzer
    if attachment:
        mime = (attachment.content_type or "").split(";")[0].strip()
        if mime in SUPPORTED_IMAGE_MIMES and attachment.size <= 20 * 1024 * 1024:
            try:
                session = _get_bot_http_session()
                async with session.get(attachment.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        image_answer = await llm_analyze_image(img_bytes, mime, question)
                        question = f"{question}\n\n[Attachment analysis: {image_answer}]"
            except Exception as e:
                log.warning("ask_cmd: failed to analyze image attachment: %s", e)
        else:
            # Non-image attachment: download and pass as text context
            try:
                session = _get_bot_http_session()
                async with session.get(attachment.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        try:
                            doc_text = raw.decode("utf-8", errors="replace")[:8000]
                        except Exception:
                            doc_text = ""
                        if doc_text:
                            question = f"{question}\n\n[Attached file `{attachment.filename}`]:\n{doc_text}"
            except Exception as e:
                log.warning("ask_cmd: failed to read attachment: %s", e)

    # Get or create conversation context
    conv = conversation_store.get(
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        user_name=str(interaction.user.display_name),
    )

    try:
        response_text, updated_history, model_used = await llm_chat(
            user_message=question,
            history=conv.history,
            user_name=str(interaction.user.display_name),
            on_tool_call=_on_tool_call,
        )
        conv.update_from_llm(updated_history)
        # Auto-save after every exchange so conversations survive restarts
        conversation_store.auto_save_thread(
            interaction.user.id, interaction.channel_id, str(interaction.user.display_name)
        )
    except Exception as e:
        log.error("LLM error: %s", e)
        # Wrap the original message in a code block for easy copy-pasting
        safe_question = discord.utils.escape_markdown(question)
        response_text = (
            f"❌ **LLM Error:** {str(e)}\n\n"
            "**Your message was saved below for easy copy-pasting/retry:**\n"
            f"```\n{safe_question}\n```"
        )
        model_used = "error"

    # Split into multiple messages if response exceeds Discord's embed limit
    chunks = _split_response(response_text)

    for i, chunk in enumerate(chunks):
        embed = discord.Embed(
            description=chunk,
            color=discord.Color.purple(),
        )
        if i == 0:
            # Add the user's question in a collapsed-style field on the first embed
            # Discord limits author.name to 256 chars. We use 200 for safety + "..."
            display_question = question if len(question) < 200 else question[:197] + "..."
            embed.set_author(
                name=f"Replying to: {display_question}",
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None
            )

        if i == len(chunks) - 1:
            # Footer only on the last embed
            if model_used and "gemini" not in model_used.lower():
                rate_str = "local · unlimited"
            else:
                rate_str = get_rate_info()
            embed.set_footer(text=f"💬 {conv.message_count} msgs | {rate_str} | via {model_used}")
        if i == 0:
            # Replace the deferred progress placeholder with the actual response
            await interaction.edit_original_response(content=None, embed=embed)
        else:
            await interaction.followup.send(embed=embed)

    audit_log(interaction.user, "ask", detail=question[:200])

    # Periodic cleanup
    conversation_store.cleanup_expired()


@bot.tree.command(name="clear", description="Clear your conversation history with OpenClaw")
@require_auth
async def clear_cmd(interaction: discord.Interaction):
    conversation_store.clear_user(interaction.user.id, interaction.channel_id)
    await interaction.response.send_message("🧹 Conversation cleared. Starting fresh!", ephemeral=True)
    audit_log(interaction.user, "clear")


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


@bot.tree.command(name="forget", description="Delete a saved conversation thread")
@app_commands.describe(name="Name of the thread to delete")
@require_auth
async def forget_cmd(interaction: discord.Interaction, name: str):
    result = conversation_store.delete_thread(interaction.user.id, name)
    await interaction.response.send_message(result, ephemeral=True)
    audit_log(interaction.user, "forget_thread", detail=name)


# ---------------------------------------------------------------------------
# Slash commands — Phase 5 (advanced skills)
# ---------------------------------------------------------------------------


@bot.tree.command(name="search", description="Search for TV shows or movies")
@app_commands.describe(
    query="Search term (e.g. 'Breaking Bad')",
    media_type="'tv', 'movie', or 'all' (default: all)",
)
@require_auth
async def search_cmd(interaction: discord.Interaction, query: str, media_type: str = "all"):
    await interaction.response.defer()
    result = await search_media(query, media_type)
    embed = discord.Embed(
        title=f"🔍 Search: {query}",
        description=result,
        color=discord.Color.teal(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "search", detail=f"{query} type={media_type}")


@bot.tree.command(name="queue", description="Show active downloads from SABnzbd and qBittorrent")
@require_auth
async def queue_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    result = await get_download_queue()
    embed = discord.Embed(
        title="📥 Download Queue",
        description=result,
        color=discord.Color.dark_teal(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "queue")


@bot.tree.command(name="recent", description="Show recently added media from Plex")
@app_commands.describe(count="Number of items to show (1-25, default 10)")
@require_auth
async def recent_cmd(interaction: discord.Interaction, count: int = 10):
    await interaction.response.defer()
    result = await get_recent_additions(count)
    embed = discord.Embed(
        title=f"🆕 Recently Added ({count})",
        description=result,
        color=discord.Color.purple(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "recent", detail=f"count={count}")


@bot.tree.command(name="health", description="Check *arr services and download client health")
@require_auth
async def health_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    arr_health = await check_arr_health()
    dl_health = await check_download_clients()
    plex_health = await check_plex_status()

    embed = discord.Embed(
        title="🏥 Service Health",
        color=discord.Color.green(),
    )
    embed.add_field(name="*arr Services", value=arr_health, inline=False)
    embed.add_field(name="Download Clients", value=dl_health, inline=False)
    embed.add_field(name="Plex", value=plex_health, inline=False)
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "health")


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
async def analyze_cmd(interaction: discord.Interaction, service: str, lines: int = 50):
    await interaction.response.defer()
    result = await analyze_logs(service, lines)
    if len(result) > 4000:
        result = result[:3980] + "\n… (truncated)"
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

    from skills import SKILLS as all_skills
    lines = []
    for name, fn in sorted(all_skills.items()):
        doc = (fn.__doc__ or "No description").strip().split("\n")[0][:80]
        lines.append(f"• `{name}` — {doc}")

    embed = discord.Embed(
        title=f"🧰 Available Skills ({len(all_skills)})",
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


@bot.tree.command(name="auditlog", description="View recent audit log entries")
@app_commands.describe(lines="Number of entries to show (default 10, max 25)")
@require_auth
async def auditlog_cmd(interaction: discord.Interaction, lines: int = 10):
    lines = min(max(lines, 1), 25)
    today = datetime.date.today().isoformat()
    audit_file = AUDIT_DIR / f"{today}.jsonl"

    if not audit_file.exists():
        await interaction.response.send_message("No audit entries for today.", ephemeral=True)
        return

    # Read last N lines
    all_lines = audit_file.read_text().strip().split("\n")
    recent = all_lines[-lines:]

    formatted = []
    for line in recent:
        try:
            entry = json.loads(line)
            ts = entry.get("ts", "")[:19].replace("T", " ")
            user = entry.get("user", "?")
            action = entry.get("action", "?")
            detail = entry.get("detail", "")
            result = entry.get("result", "")
            formatted.append(f"`{ts}` **{action}** {detail} [{result}] \u2014 {user}")
        except json.JSONDecodeError:
            continue

    embed = discord.Embed(
        title=f"\U0001f4cb Audit Log (last {len(formatted)})",
        description="\n".join(formatted) or "No entries.",
        color=discord.Color.light_grey(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    audit_log(interaction.user, "auditlog", detail=f"lines={lines}")


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
    from qmd import remember_fact
    result = await remember_fact(content, tags)
    await interaction.response.send_message(result)
    audit_log(interaction.user, "remember", detail=content)


@bot.tree.command(name="recall", description="Search long-term memory (QMD)")
@app_commands.describe(query="Keywords to search for")
@require_auth
async def recall_cmd(interaction: discord.Interaction, query: str):
    from qmd import recall_fact
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
    from agentmail import send_agent_mail
    await interaction.response.defer()
    result = await send_agent_mail(to, subject, body)
    await interaction.followup.send(result)
    audit_log(interaction.user, "mail", detail=f"to={to} subj={subject}")


# ---------------------------------------------------------------------------
# Phase 6: Network & Remote Access commands
# ---------------------------------------------------------------------------


@bot.tree.command(name="network", description="Show network connectivity status (LAN, internet, Tailscale)")
@require_auth
async def network_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    result = await get_network_status()
    embed = discord.Embed(
        title="🌐 Network Status",
        description=result,
        color=discord.Color.blue(),
    )
    embed.set_footer(text="LAN • Internet • DNS • Tailscale • OpenClaw health")
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "network")


@bot.tree.command(name="tailscale", description="Show Tailscale VPN status and this device's Tailscale IP")
@require_auth
async def tailscale_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    result = await get_tailscale_status()
    embed = discord.Embed(
        title="🔒 Tailscale Status",
        description=result,
        color=discord.Color.dark_green(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "tailscale")


@bot.tree.command(name="speedtest", description="Run a quick network speed test")
@require_auth
async def speedtest_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    result = await run_speed_test()
    embed = discord.Embed(
        title="⚡ Speed Test",
        description=result,
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Download test via Cloudflare (10MB sample)")
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "speedtest")


@bot.tree.command(name="spending", description="View Gemini API spending and budget status")
@app_commands.describe(breakdown="Show daily breakdown (default: summary)")
@require_auth
async def spending_cmd(interaction: discord.Interaction, breakdown: bool = False):
    if breakdown:
        text = spending_tracker.daily_breakdown()
    else:
        text = spending_tracker.summary()
    embed = discord.Embed(
        title="💰 Gemini API Spending",
        description=text,
        color=discord.Color.green() if not spending_tracker.is_over_budget else discord.Color.red(),
    )
    embed.set_footer(text=f"Model: gemini-1.5-flash | Tier 1 | Budget: ${spending_tracker.budget_limit:.2f}")
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "spending")


# ---------------------------------------------------------------------------
# Phase 8: Web Search, Browsing, Image Analysis, Document Analysis
# ---------------------------------------------------------------------------


@bot.tree.command(name="websearch", description="Search the live web for current information")
@app_commands.describe(query="What to search for", results="Number of results (1-10, default 5)")
@require_auth
async def websearch_cmd(interaction: discord.Interaction, query: str, results: int = 5):
    await interaction.response.defer()
    from skills.advanced_skills import search_web
    result = await search_web(query, num_results=results)
    # Discord embed description limit is 4096 chars
    if len(result) > 4000:
        result = result[:3980] + "\n… (truncated)"
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
    from skills.advanced_skills import browse_url
    page_text = await browse_url(url)
    if question and not page_text.startswith("❌") and not page_text.startswith("⚠️"):
        # Use Gemini to answer a specific question about the page content
        from llm import analyze_document as llm_doc
        answer = await llm_doc(
            page_text,
            f"Based on the page content above, answer this question: {question}",
        )
        result = f"**Question**: {question}\n\n**Answer**: {answer}"
    else:
        result = page_text
    if len(result) > 4000:
        result = result[:3980] + "\n… (truncated)"
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

    if image.size > 20 * 1024 * 1024:
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

    if len(result) > 4000:
        result = result[:3980] + "\n… (truncated)"

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
    if file.size > 20 * 1024 * 1024:
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
            for page in reader.pages[:50]:  # limit to first 50 pages
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
    MAX_CHARS = 50_000
    truncated = False
    if len(extracted_text) > MAX_CHARS:
        extracted_text = extracted_text[:MAX_CHARS]
        truncated = True

    result = await llm_analyze_document(extracted_text, question)

    if len(result) > 4000:
        result = result[:3980] + "\n… (truncated)"

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
    from mission_control import get_mission_tasks
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
# ---------------------------------------------------------------------------


class _ResearchView(discord.ui.View):
    """Action buttons attached to a completed research report."""

    def __init__(self, query: str, report: str):
        super().__init__(timeout=300)
        self._query = query
        self._report = report

    @discord.ui.button(label="📌 Save to Memory", style=discord.ButtonStyle.secondary)
    async def save_to_memory(self, interaction: discord.Interaction, _button: discord.ui.Button):
        from qmd import remember_fact
        # Store first 500 chars as the memory fact
        snippet = self._report[:500].strip()
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
            except Exception:
                pass

    # Run the research agent
    from research_agent import ResearchAgent
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


# ---------------------------------------------------------------------------
# /watch — persistent user-defined alert conditions
# ---------------------------------------------------------------------------

# Maps known NL intent keywords to skills + default intervals
_WATCH_SKILL_MAP = {
    "disk": ("get_nas_storage_health", 60),
    "storage": ("get_nas_storage_health", 60),
    "nas": ("get_nas_alerts", 15),
    "plex": ("check_plex_status", 10),
    "download": ("check_download_clients", 5),
    "queue": ("check_download_clients", 5),
    "cpu": ("get_system_stats", 10),
    "memory": ("get_system_stats", 10),
    "health": ("check_arr_health", 15),
    "sonarr": ("check_arr_health", 15),
    "radarr": ("check_arr_health", 15),
    "network": ("get_network_status", 10),
    "ping": ("ping_host", 5),
    "speed": ("run_speed_test", 60),
    "tailscale": ("get_tailscale_status", 10),
}


@bot.tree.command(name="watch", description="Create a persistent alert that runs on a schedule")
@app_commands.describe(
    condition="What to watch in plain English (e.g. 'check disk usage every hour', 'monitor plex every 10 min')",
    action="'add' to create, 'list' to view, 'remove' to delete",
    watch_id="Task ID to remove (e.g. sched-5) — only needed for 'remove'",
)
async def watch_cmd(
    interaction: discord.Interaction,
    condition: str = "",
    action: str = "list",
    watch_id: str = "",
):

    if action == "list":
        tasks = [t for t in scheduler.list_tasks() if t.created_by.startswith("watch:")]
        if not tasks:
            await interaction.response.send_message(
                "👁️ No active watches. Use `/watch add condition:\"check disk every hour\"`.",
                ephemeral=True,
            )
            return
        lines = []
        for t in tasks:
            status = "✅" if t.enabled else "⏸️"
            lines.append(
                f"{status} `{t.task_id}` — **{t.action}** every {t.interval_minutes}m "
                f"(runs: {t.run_count}, next: {t.next_run_str})"
            )
        embed = discord.Embed(
            title=f"👁️ Active Watches ({len(tasks)})",
            description="\n".join(lines),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Use /watch remove watch_id:<id> to delete a watch")
        await interaction.response.send_message(embed=embed)
        return

    if action == "remove":
        if not watch_id:
            await interaction.response.send_message("❌ Provide a watch_id. Example: `/watch remove watch_id:sched-5`", ephemeral=True)
            return
        if scheduler.remove(watch_id):
            await interaction.response.send_message(f"🗑️ Watch `{watch_id}` removed.")
            audit_log(interaction.user, "watch_remove", detail=watch_id)
        else:
            await interaction.response.send_message(f"❌ Watch `{watch_id}` not found.", ephemeral=True)
        return

    # action == "add"
    if not condition:
        await interaction.response.send_message(
            "❌ Describe what to watch. Examples:\n"
            "• `/watch add condition:\"check disk usage every hour\"`\n"
            "• `/watch add condition:\"monitor plex every 5 minutes\"`\n"
            "• `/watch add condition:\"alert if downloads stall every 10 min\"`",
            ephemeral=True,
        )
        return

    # Parse the condition string
    lower = condition.lower()

    # Detect interval from the text (e.g. "every 30 min", "every 2 hours")
    import re as _re
    interval = 30  # default
    m = _re.search(r"every\s+(\d+)\s*(min|minute|minutes|hour|hours|h)", lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        interval = n * 60 if unit.startswith("h") else n

    # Pick the best skill based on keywords
    matched_skill = None
    for keyword, (skill_name, default_interval) in _WATCH_SKILL_MAP.items():
        if keyword in lower:
            matched_skill = skill_name
            if not m:  # no explicit interval in text → use the sensible default
                interval = default_interval
            break

    if not matched_skill:
        # Fall back to a general system stats check
        matched_skill = "get_system_stats"

    # Clamp interval to a sane range (1 min – 24 hours)
    interval = max(1, min(interval, 1440))

    task = scheduler.create(
        action=matched_skill,
        interval_minutes=interval,
        created_by=f"watch:{interaction.user}",
        notify_channel_id=interaction.channel_id or 0,
        alert_only=True,
    )

    embed = discord.Embed(
        title="👁️ Watch Created",
        description=(
            f"**Condition**: {condition}\n"
            f"**Skill**: `{matched_skill}`\n"
            f"**Interval**: every {interval} minute{'s' if interval != 1 else ''}\n"
            f"**ID**: `{task.task_id}`"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Results will post to this channel | Remove with /watch remove watch_id:{task.task_id}")
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "watch_add", detail=f"{task.task_id} {matched_skill} every {interval}m")


# ---------------------------------------------------------------------------
# /nowplaying — Plex active streams
# ---------------------------------------------------------------------------


@bot.tree.command(name="nowplaying", description="Show what's currently playing on Plex (active streams)")
@require_auth
async def nowplaying_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    from skills.advanced_skills import get_plex_activity
    result = await get_plex_activity()
    embed = discord.Embed(
        title="🎬 Plex — Now Playing",
        description=result,
        color=discord.Color.from_rgb(229, 160, 13),  # Plex orange
    )
    embed.set_footer(text="via Tautulli · real-time activity")
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "nowplaying")


# ---------------------------------------------------------------------------
# /diff — git status + diff summary
# ---------------------------------------------------------------------------


@bot.tree.command(name="diff", description="Show uncommitted git changes in the OpenClaw repo")
@require_auth
async def diff_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    from git_skills import git_status, git_diff
    status, diff = await asyncio.gather(git_status(), git_diff())
    description = f"**Status**\n```\n{status[:800]}\n```\n**Diff**\n```diff\n{diff[:2600]}\n```"
    if len(description) > 3900:
        description = description[:3880] + "\n… (truncated)"
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
    except Exception:
        pass
    audit_log(interaction.user, "briefing")


# ---------------------------------------------------------------------------
# /audit-summary — analytics on today's audit log
# ---------------------------------------------------------------------------


@bot.tree.command(name="audit-summary", description="Analytics summary of today's audit log (top commands, errors, active hours)")
@require_auth
async def audit_summary_cmd(interaction: discord.Interaction):

    today = datetime.date.today().isoformat()
    audit_file = AUDIT_DIR / f"{today}.jsonl"
    if not audit_file.exists():
        await interaction.response.send_message("No audit entries for today yet.", ephemeral=True)
        return

    entries: list[dict] = []
    for line in audit_file.read_text().strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not entries:
        await interaction.response.send_message("No parseable audit entries for today.", ephemeral=True)
        return

    import collections as _col
    action_counts: dict[str, int] = _col.Counter(e.get("action", "?") for e in entries)
    error_entries = [e for e in entries if e.get("result", "success") not in ("success", "")]
    hour_counts: dict[int, int] = _col.Counter(
        int(e.get("ts", "T00")[11:13]) for e in entries if len(e.get("ts", "")) >= 13
    )

    top_actions = "\n".join(
        f"  `{action}` — {count}x"
        for action, count in action_counts.most_common(10)
    )
    top_hours = ", ".join(
        f"{h:02d}:xx ({c})" for h, c in sorted(hour_counts.items(), key=lambda x: -x[1])[:5]
    )
    errors_text = "\n".join(
        f"  `{e.get('ts','')[:19]}` {e.get('action','?')} → {e.get('result','?')}"
        for e in error_entries[:5]
    ) or "  None"

    embed = discord.Embed(
        title=f"📊 Audit Summary — {today}",
        color=discord.Color.blurple(),
    )
    embed.add_field(name=f"Total actions ({len(entries)})", value=top_actions or "—", inline=False)
    embed.add_field(name="Most active hours", value=top_hours or "—", inline=False)
    embed.add_field(name=f"Non-success results ({len(error_entries)})", value=errors_text, inline=False)
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "audit-summary")


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
