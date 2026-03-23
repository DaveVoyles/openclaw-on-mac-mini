"""
OpenClaw Discord Bot - Phase 3: LLM Integration
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

from llm import chat as llm_chat, is_configured as llm_is_configured, get_rate_info
from memory import store as conversation_store

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

VERSION = "0.3.0"

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
    embed.set_footer(text=f"OpenClaw v{VERSION} • Phase 3")

    await interaction.response.send_message(embed=embed)
    audit_log(interaction.user, "ping", f"latency={latency_ms}ms")


@bot.tree.command(name="about", description="Show OpenClaw version and system info")
async def about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 OpenClaw",
        description="Autonomous AI agent for home automation and system management.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Version", value=f"{VERSION} (Phase 3)", inline=True)
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
        ("`/restart <service>`", "Restart a container (authorized users only)"),
        ("`/help`", "This help message"),
    ]
    for name, desc in commands_list:
        embed.add_field(name=name, value=desc, inline=False)

    embed.set_footer(text=f"OpenClaw v{VERSION} • Phase 3")
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


@bot.tree.command(name="restart", description="Restart a Docker container (authorized users only)")
@app_commands.describe(service="Container name to restart")
async def restart_cmd(interaction: discord.Interaction, service: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
        audit_log(interaction.user, "restart", detail=service, result="denied")
        return

    # Check permissions.yaml allow/deny lists
    if not is_service_allowed("restart_container", service):
        await interaction.response.send_message(
            f"🚫 Restarting `{service}` is not permitted by policy.", ephemeral=True,
        )
        audit_log(interaction.user, "restart", detail=service, result="blocked_by_policy")
        return

    await interaction.response.defer()
    result = await restart_container(service)
    color = discord.Color.green() if result.startswith("✅") else discord.Color.red()
    embed = discord.Embed(
        title=f"🔄 Restart: {service}",
        description=result,
        color=color,
    )
    await interaction.followup.send(embed=embed)
    audit_log(interaction.user, "restart", detail=service, result="success" if result.startswith("✅") else "failed")


# ---------------------------------------------------------------------------
# Slash commands — Phase 3 (LLM integration)
# ---------------------------------------------------------------------------


@bot.tree.command(name="ask", description="Ask OpenClaw anything (AI-powered with function calling)")
@app_commands.describe(question="Your question or request")
async def ask_cmd(interaction: discord.Interaction, question: str):
    if not is_allowed(interaction):
        await interaction.response.send_message("❌ Not authorized.", ephemeral=True)
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
