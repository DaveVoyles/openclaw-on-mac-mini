"""Utility commands: /ping, /about, /whoami, /help."""

import platform
import time

import discord
from discord.ext import commands

from audit import audit_log
from config import cfg

from ._helpers import _is_allowed, require_auth

VERSION = cfg.version


def _register_utility_commands(bot: commands.Bot) -> None:
    """Register /ping, /about, /whoami, and /help."""

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
    """Display comprehensive help for all OpenClaw commands and features.
    
    Shows:
    - Core commands (/ask, /research, /plan)
    - Utility commands (help, logs, config, estop)
    - Docker/NAS management
    - Conversation/memory management
    - Calendar, email, media features
    """
        categories = {
            "🤖 AI & Chat": [
                ("`/ask <question>`", "Ask OpenClaw anything (AI-powered)"),
                ("`/clear`", "Clear your conversation history"),
                ("`/websearch <query>`", "Search the live web"),
                ("`/browse <url>`", "Fetch and read a web page"),
                ("`/research <topic>`", "Deep multi-source research"),
                ("`/recap weekly`", "Summarize the current Discord channel or thread"),
                ("`/sports upcoming`", "Create a sports watch guide with table output"),
                ("`/analyze-image <image>`", "Analyze an image with Gemini Vision"),
                ("`/analyze-file <file>`", "Analyze a document/PDF with AI"),
            ],
            "🐳 Docker & System": [
                ("`/containers`", "List running Docker containers"),
                ("`/status <service>`", "Detailed container status"),
                ("`/logs <service>`", "View container logs"),
                ("`/system`", "System resource usage"),
                ("`/dockerstats`", "Per-container resource usage"),
                ("`/restart <service>`", "Restart a container (approval required)"),
                ("`/ports`", "Check service port connectivity"),
                ("`/report`", "Full system status report"),
                ("`/analyze <service>`", "AI-powered log analysis"),
            ],
            "🎬 Media & Downloads": [
                ("`/search <query>`", "Search Sonarr/Radarr for media"),
                ("`/queue`", "Active downloads (SABnzbd + qBit)"),
                ("`/recent`", "Recently added media"),
                ("`/health`", "Check *arr services & download clients"),
                ("`/nowplaying`", "What's playing on Plex"),
                ("`/watch`", "Manage monitoring watches"),
            ],
            "🧠 Memory & Knowledge": [
                ("`/remember <fact>`", "Store a long-term memory"),
                ("`/recall <query>`", "Search stored memories"),
                ("`/rules`", "View learned behavioral rules"),
                ("`/profile`", "View your user profile"),
                ("`/goals`", "View active goals"),
                ("`/dream`", "Trigger memory consolidation"),
                ("`/memory-health`", "Memory system health metrics"),
            ],
            "⚙️ Admin & Analytics": [
                ("`/spending`", "Gemini API spending & budget"),
                ("`/schedule`", "Manage scheduled tasks"),
                ("`/auditlog`", "Recent audit log entries"),
                ("`/skills`", "List all available skills"),
                ("`/pending`", "Pending approval requests"),
                ("`/estop`", "Emergency stop / resume"),
                ("`/help`", "This help message"),
            ],
        }

        embed = discord.Embed(
            title="📖 OpenClaw Commands",
            description="Choose a category below, or browse all commands:",
            color=discord.Color.blurple(),
        )
        for cat_name, cmds in categories.items():
            cmd_names = ", ".join(c[0].split("`")[1].split(" ")[0] for c in cmds)
            embed.add_field(name=cat_name, value=cmd_names, inline=False)
        embed.set_footer(text=f"OpenClaw v{VERSION} • {len(sum(categories.values(), []))} commands")

        class HelpSelect(discord.ui.Select):
            def __init__(self):
                options = [
                    discord.SelectOption(label=cat.split(" ", 1)[1], emoji=cat.split(" ")[0], value=cat)
                    for cat in categories
                ]
                super().__init__(placeholder="Choose a category…", options=options)

            async def callback(self, inter: discord.Interaction):
                cat = self.values[0]
                cmds = categories.get(cat, [])
                cat_embed = discord.Embed(
                    title=f"📖 {cat}",
                    color=discord.Color.blurple(),
                )
                for name, desc in cmds:
                    cat_embed.add_field(name=name, value=desc, inline=False)
                cat_embed.set_footer(text="Use the dropdown to switch categories")
                await inter.response.edit_message(embed=cat_embed)

        view = discord.ui.View(timeout=300)
        view.add_item(HelpSelect())

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        audit_log(interaction.user, "help")
