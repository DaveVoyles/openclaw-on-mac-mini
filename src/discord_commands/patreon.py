"""Patreon monitoring commands: /patreon status, /patreon refresh-cookies."""

import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from audit import audit_log
from patreon_monitor import PatreonHealthStatus, get_patreon_checker
from patreon_recovery import get_recovery_manager

from ._helpers import require_auth

log = logging.getLogger("openclaw")


def _register_patreon_commands(bot: commands.Bot) -> None:
    """Register /patreon commands."""

    # ------------------------------------------------------------------
    # /patreon status
    # ------------------------------------------------------------------

    @bot.tree.command(name="patreon", description="Check Patreon/MonsterVision status")
    @app_commands.describe(
        action="""Status: Show health status | Refresh: Show cookie refresh guide"""
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="status", value="status"),
            app_commands.Choice(name="refresh-cookies", value="refresh"),
        ]
    )
    @require_auth
    async def patreon_cmd(interaction: discord.Interaction, action: str = "status"):
        await interaction.response.defer(ephemeral=True)

        if action == "refresh":
            # Show cookie refresh guide
            embed = _create_cookie_refresh_embed()
            await interaction.followup.send(embed=embed, ephemeral=True)
            audit_log(interaction.user, "patreon_refresh_guide")
            return

        # Default: Show status
        try:
            checker = get_patreon_checker()
            health = await checker.check_health()

            embed = _create_status_embed(health)
            await interaction.followup.send(embed=embed, ephemeral=True)
            audit_log(interaction.user, "patreon_status", detail=health.status.value)

        except Exception as exc:
            log.error(f"Error checking Patreon status: {exc}", exc_info=True)
            await interaction.followup.send(
                f"❌ Error checking Patreon status: {exc}", ephemeral=True
            )
            audit_log(interaction.user, "patreon_status", result="error")


def _create_status_embed(health) -> discord.Embed:
    """Create status embed from health result."""
    # Determine color and emoji
    if health.status == PatreonHealthStatus.OK:
        color = discord.Color.green()
        emoji = "✅"
    elif health.status == PatreonHealthStatus.WARNING:
        color = discord.Color.orange()
        emoji = "⚠️"
    elif health.status == PatreonHealthStatus.CRITICAL:
        color = discord.Color.red()
        emoji = "🚨"
    else:
        color = discord.Color.greyple()
        emoji = "❓"

    embed = discord.Embed(
        title=f"{emoji} Patreon Downloads Status",
        description=health.message,
        color=color,
        timestamp=health.timestamp,
    )

    # Status details
    metadata = health.metadata
    status_parts = []

    if "container_status" in metadata:
        status = metadata["container_status"]
        status_emoji = "🟢" if status == "running" else "🔴"
        status_parts.append(f"{status_emoji} **Container:** {status}")

    if "api_available" in metadata:
        api_ok = metadata["api_available"]
        api_emoji = "🟢" if api_ok else "🔴"
        status_parts.append(f"{api_emoji} **API:** {'Available' if api_ok else 'Unreachable'}")

    if "cookie_age_hours" in metadata and metadata["cookie_age_hours"] is not None:
        age_h = metadata["cookie_age_hours"]
        if age_h > 72:
            cookie_emoji = "🔴"
            cookie_status = f"Expired ({age_h:.0f}h old)"
        elif age_h > 48:
            cookie_emoji = "🟡"
            cookie_status = f"Expiring ({age_h:.0f}h old)"
        else:
            cookie_emoji = "🟢"
            cookie_status = f"Fresh ({age_h:.0f}h old)"
        status_parts.append(f"{cookie_emoji} **Cookies:** {cookie_status}")

    if "failed_downloads" in metadata:
        failed = metadata["failed_downloads"]
        if failed > 0:
            status_parts.append(f"❌ **Failed Downloads:** {failed}")
        else:
            status_parts.append(f"✅ **Downloads:** No failures")

    if status_parts:
        embed.add_field(name="Health Status", value="\n".join(status_parts), inline=False)

    # Issues
    if health.issues:
        issues_text = "\n".join(f"• {issue}" for issue in health.issues[:5])
        embed.add_field(name="⚠️ Issues Detected", value=issues_text, inline=False)

    # Action items
    if health.action_items:
        # Separate quick actions from detailed steps
        quick_actions = [item for item in health.action_items if not item[0].isdigit()]
        steps = [item for item in health.action_items if item[0].isdigit()]

        if quick_actions:
            embed.add_field(
                name="🔧 Recommended Actions",
                value="\n".join(f"• {action}" for action in quick_actions[:3]),
                inline=False,
            )

        if steps:
            embed.add_field(
                name="📋 Cookie Refresh (if needed)",
                value="\n".join(steps[:4]) + "\n...(use `/patreon refresh-cookies` for full guide)",
                inline=False,
            )

    # Recovery history
    try:
        recovery_mgr = get_recovery_manager()
        recent_recovery = recovery_mgr.get_recovery_history(limit=1)
        if recent_recovery:
            last = recent_recovery[0]
            time_ago = (datetime.now() - last.timestamp).total_seconds() / 60
            recovery_emoji = "✅" if last.success else "❌"
            embed.add_field(
                name="🔄 Last Recovery Attempt",
                value=f"{recovery_emoji} {last.action.value} ({time_ago:.0f}m ago)\n{last.message}",
                inline=False,
            )
    except Exception:
        pass

    embed.set_footer(text="Use /patreon refresh-cookies for cookie update guide")

    return embed


def _create_cookie_refresh_embed() -> discord.Embed:
    """Create embed with cookie refresh instructions."""
    embed = discord.Embed(
        title="🍪 Patreon Cookie Refresh Guide",
        description="Follow these steps to update Patreon cookies when they expire:",
        color=discord.Color.blue(),
    )

    # Step 1: Export cookies
    embed.add_field(
        name="1️⃣ Export Cookies from Browser",
        value=(
            "**Chrome:**\n"
            "• Install [EditThisCookie](https://chrome.google.com/webstore/detail/editthiscookie) extension\n"
            "• Go to patreon.com (make sure you're logged in)\n"
            "• Click EditThisCookie icon → Export → Copy to clipboard\n\n"
            "**Firefox:**\n"
            "• Install [Cookie-Editor](https://addons.mozilla.org/en-US/firefox/addon/cookie-editor/) extension\n"
            "• Go to patreon.com (logged in)\n"
            "• Click Cookie-Editor → Export → Netscape format"
        ),
        inline=False,
    )

    # Step 2: Save to file
    embed.add_field(
        name="2️⃣ Save to cookies.txt",
        value=(
            "• Create/edit file named `cookies.txt`\n"
            "• Paste the exported cookies\n"
            "• Save in Netscape HTTP Cookie File format\n"
            "• File should contain lines like: `.patreon.com TRUE / ...`"
        ),
        inline=False,
    )

    # Step 3: Copy to container
    embed.add_field(
        name="3️⃣ Copy to MonsterVision Container",
        value=(
            "Run this command from where you saved cookies.txt:\n"
            "```bash\n"
            "docker cp cookies.txt monstervision:/app/cookies.txt\n"
            "```"
        ),
        inline=False,
    )

    # Step 4: Restart
    embed.add_field(
        name="4️⃣ Restart Container",
        value=(
            "```bash\n"
            "docker restart monstervision\n"
            "```\n"
            "Wait ~30 seconds for container to fully start."
        ),
        inline=False,
    )

    # Step 5: Verify
    embed.add_field(
        name="5️⃣ Verify",
        value=(
            "Check status after a few minutes:\n"
            "• Use `/patreon status` command\n"
            "• Or check container logs:\n"
            "```bash\n"
            "docker logs monstervision --tail 20\n"
            "```"
        ),
        inline=False,
    )

    embed.add_field(
        name="💡 Tips",
        value=(
            "• Cookies typically last 60-90 days\n"
            "• You'll get a warning when they're ~48h from expiring\n"
            "• Keep EditThisCookie/Cookie-Editor installed for quick updates\n"
            "• Make sure you're logged into Patreon before exporting"
        ),
        inline=False,
    )

    embed.set_footer(text="Questions? Check container logs or ping Dave")

    return embed
