"""Digest Cog — Personalized user digest commands for Discord."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from cog_helpers import audit_log, require_auth, split_response

log = logging.getLogger("openclaw.digest_cog")


class DigestCog(commands.Cog, name="Digest"):
    """Personalized daily/weekly digest configuration and delivery."""

    digest = app_commands.Group(name="digest", description="Personalized digest configuration")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        msg = str(error) if isinstance(error, app_commands.CheckFailure) else f"❌ Command failed: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @digest.command(name="now", description="Get your personalized digest right now")
    @require_auth
    async def digest_now(self, interaction: discord.Interaction):
        """Get an instant personalized digest."""
        from digest_manager import get_digest_manager
        from runtime_state import set_current_user_id

        await interaction.response.defer(ephemeral=False)

        try:
            # Set user context for digest generation
            set_current_user_id(str(interaction.user.id))

            manager = get_digest_manager()
            digest_content = await manager.generate_digest(str(interaction.user.id), preview=False)

            # Split into chunks if needed
            chunks = split_response(digest_content)
            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    embed = discord.Embed(
                        title="📰 Your Personalized Digest",
                        description=chunk,
                        color=discord.Color.blue(),
                    )
                    await interaction.followup.send(embed=embed)
                else:
                    embed = discord.Embed(
                        description=chunk,
                        color=discord.Color.blue(),
                    )
                    await interaction.followup.send(embed=embed)

            audit_log(interaction.user, "digest_now", detail="instant_digest")

        except Exception as exc:
            log.error("Digest generation failed: %s", exc)
            await interaction.followup.send(f"❌ Failed to generate digest: {exc}", ephemeral=True)

    @digest.command(name="preview", description="Preview what your next digest will contain")
    @require_auth
    async def digest_preview(self, interaction: discord.Interaction):
        """Preview the next scheduled digest."""
        from digest_manager import get_digest_manager
        from runtime_state import set_current_user_id

        await interaction.response.defer(ephemeral=True)

        try:
            set_current_user_id(str(interaction.user.id))

            manager = get_digest_manager()
            digest_content = await manager.generate_digest(str(interaction.user.id), preview=True)

            chunks = split_response(digest_content)
            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    embed = discord.Embed(
                        title="🔍 Digest Preview",
                        description=chunk,
                        color=discord.Color.gold(),
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    embed = discord.Embed(
                        description=chunk,
                        color=discord.Color.gold(),
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)

            audit_log(interaction.user, "digest_preview", detail="preview")

        except Exception as exc:
            log.error("Digest preview failed: %s", exc)
            await interaction.followup.send(f"❌ Failed to preview digest: {exc}", ephemeral=True)

    @digest.command(name="config", description="View your current digest configuration")
    @require_auth
    async def digest_config(self, interaction: discord.Interaction):
        """Show current digest configuration."""
        from digest_manager import get_digest_manager

        await interaction.response.defer(ephemeral=True)

        try:
            manager = get_digest_manager()
            prefs = manager.get_preferences(str(interaction.user.id))

            # Build configuration display
            lines = []

            # Topics
            topics = prefs.get("topics", [])
            if topics:
                lines.append(f"📚 **Topics ({len(topics)}):** {', '.join(topics[:10])}")
                if len(topics) > 10:
                    lines.append(f"    ...and {len(topics) - 10} more")
            else:
                lines.append("📚 **Topics:** None configured")

            # Stocks
            stocks = prefs.get("stocks", [])
            if stocks:
                lines.append(f"📈 **Stocks ({len(stocks)}):** {', '.join(stocks[:10])}")
                if len(stocks) > 10:
                    lines.append(f"    ...and {len(stocks) - 10} more")
            else:
                lines.append("📈 **Stocks:** None configured")

            # Teams
            teams = prefs.get("teams", [])
            if teams:
                lines.append(f"🏀 **Teams ({len(teams)}):** {', '.join(teams[:10])}")
                if len(teams) > 10:
                    lines.append(f"    ...and {len(teams) - 10} more")
            else:
                lines.append("🏀 **Teams:** None configured")

            # Keywords
            keywords = prefs.get("keywords", [])
            if keywords:
                lines.append(f"🔍 **Keywords ({len(keywords)}):** {', '.join(keywords[:5])}")

            # Exclusions
            exclude = prefs.get("exclude", [])
            if exclude:
                lines.append(f"🚫 **Excluding ({len(exclude)}):** {', '.join(exclude[:5])}")

            # Schedule
            lines.append("")
            lines.append(
                f"⏰ **Schedule:** {prefs.get('schedule', 'daily')} at "
                f"{prefs.get('delivery_time', '08:00')} {prefs.get('timezone', 'UTC')}"
            )

            if prefs.get("schedule") == "weekly":
                lines.append(f"📅 **Delivery Day:** {prefs.get('delivery_day', 'Monday')}")

            # Format options
            lines.append(f"📝 **Format:** {prefs.get('format', 'concise')}")
            lines.append(f"📊 **Max items:** {prefs.get('max_items', 10)} per section")

            # Status
            enabled = prefs.get("enabled", True)
            status = "✅ Enabled" if enabled else "⏸️ Disabled"
            lines.append(f"\n**Status:** {status}")

            embed = discord.Embed(
                title="📋 Your Digest Configuration",
                description="\n".join(lines),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Use /digest topic, /digest stock, or /digest team to add items")

            await interaction.followup.send(embed=embed, ephemeral=True)
            audit_log(interaction.user, "digest_config", detail="view")

        except Exception as exc:
            log.error("Failed to get digest config: %s", exc)
            await interaction.followup.send(f"❌ Failed to get configuration: {exc}", ephemeral=True)

    @digest.command(name="topic", description="Add or remove a topic from your digest")
    @app_commands.describe(
        action="add or remove",
        topic="Topic to add/remove (e.g., 'AI', 'space exploration', 'climate change')",
    )
    @require_auth
    async def digest_topic(
        self,
        interaction: discord.Interaction,
        action: str,
        topic: str,
    ):
        """Manage digest topics."""
        from digest_manager import get_digest_manager

        await interaction.response.defer(ephemeral=True)

        try:
            manager = get_digest_manager()

            if action.lower() == "add":
                manager.add_to_list(str(interaction.user.id), "topics", topic)
                prefs = manager.get_preferences(str(interaction.user.id))
                count = len(prefs.get("topics", []))
                await interaction.followup.send(
                    f"✅ Added topic: **{topic}**\n\nYou're now following {count} topic(s)",
                    ephemeral=True,
                )
                audit_log(interaction.user, "digest_topic_add", detail=topic)
            elif action.lower() == "remove":
                manager.remove_from_list(str(interaction.user.id), "topics", topic)
                await interaction.followup.send(
                    f"✅ Removed topic: **{topic}**",
                    ephemeral=True,
                )
                audit_log(interaction.user, "digest_topic_remove", detail=topic)
            else:
                await interaction.followup.send(
                    "❌ Invalid action. Use 'add' or 'remove'",
                    ephemeral=True,
                )

        except Exception as exc:
            log.error("Failed to manage topic: %s", exc)
            await interaction.followup.send(f"❌ Failed to manage topic: {exc}", ephemeral=True)

    @digest.command(name="stock", description="Add or remove a stock from your watchlist")
    @app_commands.describe(
        action="add or remove",
        ticker="Stock ticker symbol (e.g., 'TSLA', 'NVDA', 'AAPL')",
    )
    @require_auth
    async def digest_stock(
        self,
        interaction: discord.Interaction,
        action: str,
        ticker: str,
    ):
        """Manage digest stock watchlist."""
        from digest_manager import get_digest_manager

        await interaction.response.defer(ephemeral=True)

        try:
            manager = get_digest_manager()
            ticker_upper = ticker.strip().upper()

            if action.lower() == "add":
                manager.add_to_list(str(interaction.user.id), "stocks", ticker_upper)
                prefs = manager.get_preferences(str(interaction.user.id))
                count = len(prefs.get("stocks", []))
                await interaction.followup.send(
                    f"✅ Added stock: **{ticker_upper}**\n\nYou're now watching {count} stock(s)",
                    ephemeral=True,
                )
                audit_log(interaction.user, "digest_stock_add", detail=ticker_upper)
            elif action.lower() == "remove":
                manager.remove_from_list(str(interaction.user.id), "stocks", ticker_upper)
                await interaction.followup.send(
                    f"✅ Removed stock: **{ticker_upper}**",
                    ephemeral=True,
                )
                audit_log(interaction.user, "digest_stock_remove", detail=ticker_upper)
            else:
                await interaction.followup.send(
                    "❌ Invalid action. Use 'add' or 'remove'",
                    ephemeral=True,
                )

        except Exception as exc:
            log.error("Failed to manage stock: %s", exc)
            await interaction.followup.send(f"❌ Failed to manage stock: {exc}", ephemeral=True)

    @digest.command(name="team", description="Add or remove a sports team from your digest")
    @app_commands.describe(
        action="add or remove",
        team="Team name (e.g., 'Lakers', 'Patriots', 'Yankees')",
    )
    @require_auth
    async def digest_team(
        self,
        interaction: discord.Interaction,
        action: str,
        team: str,
    ):
        """Manage digest sports teams."""
        from digest_manager import get_digest_manager

        await interaction.response.defer(ephemeral=True)

        try:
            manager = get_digest_manager()

            if action.lower() == "add":
                manager.add_to_list(str(interaction.user.id), "teams", team)
                prefs = manager.get_preferences(str(interaction.user.id))
                count = len(prefs.get("teams", []))
                await interaction.followup.send(
                    f"✅ Added team: **{team}**\n\nYou're now following {count} team(s)",
                    ephemeral=True,
                )
                audit_log(interaction.user, "digest_team_add", detail=team)
            elif action.lower() == "remove":
                manager.remove_from_list(str(interaction.user.id), "teams", team)
                await interaction.followup.send(
                    f"✅ Removed team: **{team}**",
                    ephemeral=True,
                )
                audit_log(interaction.user, "digest_team_remove", detail=team)
            else:
                await interaction.followup.send(
                    "❌ Invalid action. Use 'add' or 'remove'",
                    ephemeral=True,
                )

        except Exception as exc:
            log.error("Failed to manage team: %s", exc)
            await interaction.followup.send(f"❌ Failed to manage team: {exc}", ephemeral=True)

    @digest.command(name="schedule", description="Set your digest delivery schedule")
    @app_commands.describe(
        frequency="daily, weekly, or manual",
        time="Delivery time in HH:MM format (e.g., '08:00')",
        day="Day of week for weekly digests (e.g., 'Monday')",
    )
    @require_auth
    async def digest_schedule(
        self,
        interaction: discord.Interaction,
        frequency: str,
        time: str = "08:00",
        day: str = "Monday",
    ):
        """Configure digest schedule."""
        from digest_manager import get_digest_manager

        await interaction.response.defer(ephemeral=True)

        try:
            manager = get_digest_manager()
            user_id = str(interaction.user.id)

            # Validate frequency
            if frequency.lower() not in {"daily", "weekly", "manual", "custom"}:
                await interaction.followup.send(
                    "❌ Invalid frequency. Use 'daily', 'weekly', or 'manual'",
                    ephemeral=True,
                )
                return

            # Update preferences
            manager.update_preference(user_id, "schedule", frequency.lower())
            manager.update_preference(user_id, "delivery_time", time)

            if frequency.lower() == "weekly":
                manager.update_preference(user_id, "delivery_day", day)

            # Build confirmation message
            msg = "✅ Digest schedule updated!\n\n"
            msg += f"📅 **Frequency:** {frequency}\n"
            msg += f"⏰ **Time:** {time} UTC\n"

            if frequency.lower() == "weekly":
                msg += f"📆 **Day:** {day}\n"

            msg += "\nUse `/digest preview` to see what your next digest will contain"

            await interaction.followup.send(msg, ephemeral=True)
            audit_log(interaction.user, "digest_schedule", detail=f"{frequency}@{time}")

        except Exception as exc:
            log.error("Failed to set schedule: %s", exc)
            await interaction.followup.send(f"❌ Failed to set schedule: {exc}", ephemeral=True)

    @digest.command(name="enable", description="Enable your digest delivery")
    @require_auth
    async def digest_enable(self, interaction: discord.Interaction):
        """Enable digest delivery."""
        from digest_manager import get_digest_manager

        await interaction.response.defer(ephemeral=True)

        try:
            manager = get_digest_manager()
            manager.update_preference(str(interaction.user.id), "enabled", True)

            await interaction.followup.send(
                "✅ Digest delivery enabled!\n\nYou'll receive your digest according to your schedule.",
                ephemeral=True,
            )
            audit_log(interaction.user, "digest_enable", detail="enabled")

        except Exception as exc:
            log.error("Failed to enable digest: %s", exc)
            await interaction.followup.send(f"❌ Failed to enable digest: {exc}", ephemeral=True)

    @digest.command(name="disable", description="Disable your digest delivery")
    @require_auth
    async def digest_disable(self, interaction: discord.Interaction):
        """Disable digest delivery."""
        from digest_manager import get_digest_manager

        await interaction.response.defer(ephemeral=True)

        try:
            manager = get_digest_manager()
            manager.update_preference(str(interaction.user.id), "enabled", False)

            await interaction.followup.send(
                "⏸️ Digest delivery disabled.\n\nYou can still get digests on demand with `/digest now`",
                ephemeral=True,
            )
            audit_log(interaction.user, "digest_disable", detail="disabled")

        except Exception as exc:
            log.error("Failed to disable digest: %s", exc)
            await interaction.followup.send(f"❌ Failed to disable digest: {exc}", ephemeral=True)


async def setup(bot: commands.Bot):
    """Register the Digest cog."""
    await bot.add_cog(DigestCog(bot))
