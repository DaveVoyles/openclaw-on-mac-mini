"""Utility commands: /ping, /about, /whoami, /help, /tutorial, /permissions."""

import platform
import time

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from config import cfg
from onboarding import OnboardingManager, TutorialStep
from permissions import PermissionLevel

from ._helpers import _is_allowed, require_auth

VERSION = cfg.version

# ---------------------------------------------------------------------------
# Permission level description table (used by /whoami and /permissions list)
# ---------------------------------------------------------------------------

_LEVEL_INFO: dict[PermissionLevel, dict] = {
    PermissionLevel.PUBLIC: {
        "label": "🌐 PUBLIC",
        "desc": "No authentication required. Available to everyone.",
        "categories": ["Basic info commands (if unlocked)"],
    },
    PermissionLevel.MEMBER: {
        "label": "👤 MEMBER",
        "desc": "Server members only (no DMs).",
        "categories": ["🤖 AI & Chat", "🎬 Media & Downloads", "🧠 Memory & Knowledge"],
    },
    PermissionLevel.TRUSTED: {
        "label": "🔑 TRUSTED",
        "desc": "Users with a designated trusted role.",
        "categories": ["All MEMBER categories", "🐳 Docker & System"],
    },
    PermissionLevel.ADMIN: {
        "label": "🛡️ ADMIN",
        "desc": "Server administrators.",
        "categories": ["All TRUSTED categories", "⚙️ Admin & Analytics"],
    },
    PermissionLevel.OWNER: {
        "label": "👑 OWNER",
        "desc": "Bot owner only.",
        "categories": ["All ADMIN categories", "🔧 Internal / Diagnostics"],
    },
}


def _resolve_user_permission_level(interaction: discord.Interaction) -> PermissionLevel:
    """Determine the highest PermissionLevel for the interaction's user."""
    from permissions import check_permission

    user = interaction.user
    guild = interaction.guild

    # Check from highest to lowest
    # OWNER: bot owner (application owner) — use app_info if available; fall
    # back to checking if user is first in ALLOWED_USER_IDS list
    from config import cfg as _cfg
    owner_id = _cfg.allowed_user_ids[0] if _cfg.allowed_user_ids else None
    if check_permission(PermissionLevel.OWNER, interaction, owner_id=owner_id):
        return PermissionLevel.OWNER

    if check_permission(PermissionLevel.ADMIN, interaction):
        return PermissionLevel.ADMIN

    if check_permission(PermissionLevel.TRUSTED, interaction):
        return PermissionLevel.TRUSTED

    if check_permission(PermissionLevel.MEMBER, interaction):
        return PermissionLevel.MEMBER

    return PermissionLevel.PUBLIC


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
        level = _resolve_user_permission_level(interaction)
        info = _LEVEL_INFO[level]

        embed = discord.Embed(
            title="👤 Identity",
            color=discord.Color.green() if allowed else discord.Color.red(),
        )
        embed.add_field(name="User", value=str(interaction.user), inline=True)
        embed.add_field(name="ID", value=str(interaction.user.id), inline=True)
        embed.add_field(name="Status", value="✅ Authorized" if allowed else "❌ Not Authorized", inline=True)
        embed.add_field(name="Permission Level", value=info["label"], inline=True)
        embed.add_field(name="Description", value=info["desc"], inline=False)
        embed.add_field(
            name="Unlocked Categories",
            value="\n".join(f"• {cat}" for cat in info["categories"]),
            inline=False,
        )
        embed.set_footer(text="Use /permissions list to see all levels and their access")

        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "whoami", f"allowed={allowed} level={level.name}")

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
                ("`/ask <question>`", "Ask naturally in plain English - OpenClaw can choose tools and skills for you"),
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
                options.append(discord.SelectOption(label="New here?", emoji="👋", value="__new_here__"))
                super().__init__(placeholder="Choose a category…", options=options)

            async def callback(self, inter: discord.Interaction):
                cat = self.values[0]
                if cat == "__new_here__":
                    new_embed = discord.Embed(
                        title="👋 New Here? Welcome to OpenClaw!",
                        description=(
                            "OpenClaw is your AI-powered assistant that lives in Discord.\n\n"
                            "**Quick Start:**\n"
                            "• Use `/ask <question>` to chat with the AI\n"
                            "• Use `/tutorial start` for an interactive walkthrough\n"
                            "• Use `/help` to browse all commands by category\n"
                            "• Use `/whoami` to see your permission level\n\n"
                            "**What I can do:**\n"
                            "🤖 Natural language AI conversations\n"
                            "📅 Schedule tasks and reminders\n"
                            "🌐 Browse web and analyze content\n"
                            "📊 Monitor systems and services\n"
                            "🔧 Manage Docker containers and NAS"
                        ),
                        color=discord.Color.gold(),
                    )
                    new_embed.set_footer(text="Run /tutorial start for an interactive walkthrough!")
                    await inter.response.edit_message(embed=new_embed)
                    return
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

    # ------------------------------------------------------------------
    # /tutorial
    # ------------------------------------------------------------------

    @bot.tree.command(name="tutorial", description="Interactive OpenClaw tutorial")
    @app_commands.describe(step="Tutorial action to perform")
    @app_commands.choices(step=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="next", value="next"),
        app_commands.Choice(name="skip", value="skip"),
        app_commands.Choice(name="restart", value="restart"),
    ])
    @require_auth
    async def tutorial_cmd(interaction: discord.Interaction, step: app_commands.Choice[str]):
        manager = OnboardingManager()
        user_id = str(interaction.user.id)

        if step.value == "skip":
            manager.skip_tutorial(user_id)
            embed = discord.Embed(
                title="⏭️ Tutorial Skipped",
                description="No problem! You can restart anytime with `/tutorial restart`.\nUse `/help` to explore all commands.",
                color=discord.Color.light_grey(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            audit_log(interaction.user, "tutorial", "step=skip")
            return

        if step.value == "restart":
            manager.restart_tutorial(user_id)

        if step.value in ("start", "restart"):
            progress = manager.start_onboarding(user_id)
            current_step = TutorialStep.WELCOME
        else:
            # "next" — advance from current step
            progress = manager.get_progress(user_id)
            if progress is None:
                progress = manager.start_onboarding(user_id)
                current_step = TutorialStep.WELCOME
            else:
                manager.complete_step(user_id, progress.current_step)
                progress = manager.get_progress(user_id)
                current_step = progress.current_step if progress else TutorialStep.WELCOME

        step_content = manager._get_step_content(current_step)
        if not step_content:
            embed = discord.Embed(
                title="🎉 Tutorial Complete!",
                description="You've finished the tutorial. Use `/help` to explore all commands!",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            audit_log(interaction.user, "tutorial", "step=complete")
            return

        steps_list = list(TutorialStep)
        current_index = steps_list.index(current_step) + 1
        total_steps = len(steps_list)

        embed = discord.Embed(
            title=f"📚 Tutorial: {step_content.get('title', '')}",
            description=step_content.get("description", ""),
            color=0x667EEA,
        )
        if "example" in step_content:
            embed.add_field(name="Try It Out", value=step_content["example"], inline=False)
        if "tips" in step_content:
            embed.add_field(name="💡 Tips", value=step_content["tips"], inline=False)
        embed.set_footer(
            text=f"Step {current_index}/{total_steps} • Use /tutorial next to continue or /tutorial skip to exit"
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "tutorial", f"step={step.value} current={current_step.value}")

    # ------------------------------------------------------------------
    # /permissions list
    # ------------------------------------------------------------------

    @bot.tree.command(name="permissions", description="Show permission levels and the command categories they unlock")
    @require_auth
    async def permissions_cmd(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔐 Permission Levels",
            description="Each level unlocks the categories listed below it.",
            color=discord.Color.blurple(),
        )
        for level in PermissionLevel:
            info = _LEVEL_INFO[level]
            cats = "\n".join(f"  • {cat}" for cat in info["categories"])
            embed.add_field(
                name=f"{info['label']} — {info['desc']}",
                value=cats,
                inline=False,
            )
        embed.set_footer(text="Run /whoami to see your current permission level")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        audit_log(interaction.user, "permissions", "list")
