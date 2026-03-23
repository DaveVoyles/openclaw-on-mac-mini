"""
OpenClaw Discord Bot - Phase 5: Advanced Skills
Autonomous AI agent for home automation and system management.
"""

import asyncio
import datetime
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path

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
from advanced_skills import (
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

from llm import chat as llm_chat, is_configured as llm_is_configured, get_rate_info
from memory import store as conversation_store
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

VERSION = "0.5.0"

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
# Audit logger
# ---------------------------------------------------------------------------

AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def audit_log(user: discord.User | discord.Member | None, action: str, detail: str = "", result: str = "success"):
    """Append a single JSON-Lines entry to today's audit file."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "user": str(user) if user else "system",
        "user_id": str(user.id) if user else "0",
        "action": action,
        "detail": detail,
        "result": result,
    }
    today = datetime.date.today().isoformat()
    audit_file = AUDIT_DIR / f"{today}.jsonl"
    with open(audit_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------


def is_allowed(interaction: discord.Interaction) -> bool:
    """Return True if the invoking user is on the allow-list."""
    if not ALLOWED_USER_IDS:
        return True  # No allowlist configured → allow all (dev mode)
    return interaction.user.id in ALLOWED_USER_IDS


# ---------------------------------------------------------------------------
# Permissions helper (reads config/permissions.yaml)
# ---------------------------------------------------------------------------

_permissions_cache: dict | None = None


def _load_permissions() -> dict:
    global _permissions_cache
    if _permissions_cache is not None:
        return _permissions_cache
    perms_file = CONFIG_DIR / "permissions.yaml"
    if perms_file.exists():
        with open(perms_file) as f:
            _permissions_cache = yaml.safe_load(f) or {}
    else:
        _permissions_cache = {}
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
        """Sync commands on startup."""
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

    # ------------------------------------------------------------------
    # Health-check HTTP server (for Docker HEALTHCHECK / Uptime Kuma)
    # ------------------------------------------------------------------

    async def _start_health_server(self):
        app = web.Application()
        app.router.add_get("/health", self._health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
        await site.start()
        self._health_runner = runner
        log.info("Health endpoint listening on :%d/health", HEALTH_PORT)

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


bot = OpenClawBot()

# ---------------------------------------------------------------------------
# Slash commands — Phase 1 (foundation)
# ---------------------------------------------------------------------------


@bot.tree.command(name="ping", description="Check if OpenClaw is alive")
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
        ("`/skills`", "List all available skills"),
        ("`/pending`", "List pending approval requests"),
        ("`/auditlog [lines]`", "View recent audit log entries"),
        ("`/estop`", "Emergency stop — halt all bot actions"),
        ("`/estop resume`", "Resume bot after emergency stop"),
        ("`/help`", "This help message"),
    ]
    for name, desc in commands_list:
        embed.add_field(name=name, value=desc, inline=False)

    embed.set_footer(text=f"OpenClaw v{VERSION} \u2022 Phase 5")
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "help")


# ---------------------------------------------------------------------------
# Slash commands — Phase 2 (core skills)
# ---------------------------------------------------------------------------


@bot.tree.command(name="containers", description="List all running Docker containers")
async def containers_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    await interaction.response.defer()
    result = await list_containers()
    embed = discord.Embed(
        title="🐳 Running Containers",
        description=f"```\n{result}\n```",
        color=discord.Color.blue(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "containers")


@bot.tree.command(name="status", description="Get detailed status for a container")
@app_commands.describe(service="Container name (e.g. sonarr, radarr, plex)")
async def status_cmd(interaction: discord.Interaction, service: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    await interaction.response.defer()
    result = await get_container_status(service)
    embed = discord.Embed(
        title=f"📦 Status: {service}",
        description=f"```\n{result}\n```",
        color=discord.Color.blue(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "status", detail=service)


@bot.tree.command(name="logs", description="View recent logs from a container")
@app_commands.describe(service="Container name", lines="Number of lines (5-100, default 30)")
async def logs_cmd(interaction: discord.Interaction, service: str, lines: int = 30):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    await interaction.response.defer()
    result = await get_container_logs(service, lines)
    embed = discord.Embed(
        title=f"📜 Logs: {service} (last {min(max(lines, 5), 100)})",
        description=f"```\n{result}\n```",
        color=discord.Color.greyple(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "logs", detail=f"{service} lines={lines}")


@bot.tree.command(name="system", description="Show system resource usage")
async def system_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    await interaction.response.defer()
    stats = await get_system_stats()
    uptime_str = await get_uptime()
    embed = discord.Embed(
        title="🖥️ System Stats",
        description=stats,
        color=discord.Color.green(),
    )
    embed.add_field(name="Uptime", value=f"```{uptime_str}```", inline=False)
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "system")


@bot.tree.command(name="dockerstats", description="Show resource usage per container")
async def dockerstats_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    await interaction.response.defer()
    result = await get_docker_stats()
    embed = discord.Embed(
        title="📊 Docker Resource Usage",
        description=f"```\n{result}\n```",
        color=discord.Color.orange(),
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "dockerstats")


@bot.tree.command(name="restart", description="Restart a Docker container (requires approval)")
@app_commands.describe(service="Container name to restart")
async def restart_cmd(interaction: discord.Interaction, service: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        audit_log(interaction.user, "restart", detail=service, result="denied")
        return

    if is_emergency_stopped():
        await interaction.response.send_message(
            "🛑 **Emergency stop is active.** All actions are halted. Use `/estop resume` to resume.",
            ephemeral=True,
        )
        audit_log(interaction.user, "restart", detail=service, result="blocked_estop")
        return

    # Check permissions.yaml allow/deny lists
    if not is_service_allowed("restart_container", service):
        await interaction.response.send_message(
            f"🚫 Restarting `{service}` is not permitted by policy.", ephemeral=True,
        )
        audit_log(interaction.user, "restart", detail=service, result="blocked_by_policy")
        return

    # Create approval request with button UI
    req = approval_store.create(
        action="restart_container",
        target=service,
        risk_level=RiskLevel.HIGH,
        requester_id=interaction.user.id,
        requester_name=str(interaction.user),
        channel_id=interaction.channel_id,
    )

    async def execute_restart(approved_req):
        """Callback invoked when the approval button is clicked."""
        result = await restart_container(approved_req.target)
        color = discord.Color.green() if result.startswith("✅") else discord.Color.red()
        embed = discord.Embed(
            title=f"🔄 Restart: {approved_req.target}",
            description=result,
            color=color,
        )
        audit_log(
            None, "restart_executed",
            detail=f"{approved_req.target} approved_by={approved_req.resolver_name}",
            result="success" if result.startswith("✅") else "failed",
        )
        return embed

    view = ApprovalView(req.request_id, execute_restart)
    embed = build_approval_embed(req)

    await interaction.response.send_message(embed=embed, view=view)
    audit_log(interaction.user, "restart_requested", detail=service)


# ---------------------------------------------------------------------------
# Slash commands — Phase 3 (LLM integration)
# ---------------------------------------------------------------------------


@bot.tree.command(name="ask", description="Ask OpenClaw anything (AI-powered with function calling)")
@app_commands.describe(question="Your question or request")
async def ask_cmd(interaction: discord.Interaction, question: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

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

    # Get or create conversation context
    conv = conversation_store.get(
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        user_name=str(interaction.user.display_name),
    )

    try:
        response_text, updated_history = await llm_chat(
            user_message=question,
            history=conv.history,
            user_name=str(interaction.user.display_name),
        )
        conv.update_from_llm(updated_history)
    except Exception as e:
        log.error("LLM error: %s", e)
        response_text = f"❌ LLM error: {e}"

    # Truncate to Discord's limit
    if len(response_text) > 1900:
        response_text = response_text[:1880] + "\n… (truncated)"

    embed = discord.Embed(
        title="🧠 OpenClaw",
        description=response_text,
        color=discord.Color.purple(),
    )
    embed.set_footer(text=f"💬 {conv.message_count} msgs | {get_rate_info()}")

    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "ask", detail=question[:200])

    # Periodic cleanup
    conversation_store.cleanup_expired()


@bot.tree.command(name="clear", description="Clear your conversation history with OpenClaw")
async def clear_cmd(interaction: discord.Interaction):
    conversation_store.clear_user(interaction.user.id, interaction.channel_id)
    await interaction.response.send_message("🧹 Conversation cleared. Starting fresh!", ephemeral=True)
    audit_log(interaction.user, "clear")


# ---------------------------------------------------------------------------
# Slash commands — Phase 5 (advanced skills)
# ---------------------------------------------------------------------------


@bot.tree.command(name="search", description="Search for TV shows or movies")
@app_commands.describe(
    query="Search term (e.g. 'Breaking Bad')",
    media_type="'tv', 'movie', or 'all' (default: all)",
)
async def search_cmd(interaction: discord.Interaction, query: str, media_type: str = "all"):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
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
async def queue_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
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
async def recent_cmd(interaction: discord.Interaction, count: int = 10):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
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
async def health_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
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
async def ports_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
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
async def report_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
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
async def analyze_cmd(interaction: discord.Interaction, service: str, lines: int = 50):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
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
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

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
async def skills_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return

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
async def pending_cmd(interaction: discord.Interaction):
    if not is_allowed(interaction):
        await interaction.response.send_message("\u274c Not authorized.", ephemeral=True)
        return

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
async def auditlog_cmd(interaction: discord.Interaction, lines: int = 10):
    if not is_allowed(interaction):
        await interaction.response.send_message("\u274c Not authorized.", ephemeral=True)
        return

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
async def estop_cmd(interaction: discord.Interaction, action: str = "stop"):
    if not is_allowed(interaction):
        await interaction.response.send_message("\u274c Not authorized.", ephemeral=True)
        return

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
async def remember_cmd(interaction: discord.Interaction, content: str, tags: str = ""):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    from qmd import remember_fact
    result = await remember_fact(content, tags)
    await interaction.response.send_message(result)
    audit_log(interaction.user, "remember", detail=content)


@bot.tree.command(name="recall", description="Search long-term memory (QMD)")
@app_commands.describe(query="Keywords to search for")
async def recall_cmd(interaction: discord.Interaction, query: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    from qmd import recall_fact
    result = await recall_fact(query)
    embed = discord.Embed(title=f"🧠 Recall: {query}", description=result, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "recall", detail=query)


@bot.tree.command(name="mail", description="Send an automated e-mail message via AgentMail")
@app_commands.describe(to="Recipient email", subject="Email subject", body="Message body")
async def mail_cmd(interaction: discord.Interaction, to: str, subject: str, body: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        return
    if is_emergency_stopped():
        await interaction.response.send_message("🛑 Emergency stop active.", ephemeral=True)
        return
    from agentmail import send_agent_mail
    await interaction.response.defer()
    result = await send_agent_mail(to, subject, body)
    await interaction.followup.send(result)
    audit_log(interaction.user, "mail", detail=f"to={to} subj={subject}")


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
