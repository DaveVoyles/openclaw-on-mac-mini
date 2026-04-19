"""Trend tracking commands: !track, !trending, !untrack, !trends."""

import discord

# Import skills
import trend_skills
from discord.ext import commands

from audit import audit_log


def _register_trend_management_commands(bot: commands.Bot) -> None:
    """Register commands for managing tracked topics (track/untrack)."""

    @bot.tree.command(name="track", description="Start tracking a topic for trends")
    @discord.app_commands.describe(
        topic="Topic to track (e.g., 'Bitcoin', 'Moana 2', 'Lakers')",
        category="Category: Entertainment, Finance, Sports, News, General",
    )
    async def track_cmd(
        interaction: discord.Interaction,
        topic: str,
        category: str = "General",
    ):
        """Start tracking a topic."""
        await interaction.response.defer()

        result = await trend_skills.track_topic(topic, category, user_id=str(interaction.user.id))

        if result["status"] == "ok":
            embed = discord.Embed(
                title="✅ Tracking Started",
                description=result["message"],
                color=discord.Color.green(),
            )
            embed.add_field(name="Topic", value=topic, inline=True)
            embed.add_field(name="Category", value=category, inline=True)
            embed.set_footer(text="Use /trends to view trajectory")
            await interaction.followup.send(embed=embed)
            audit_log(interaction.user, "track_topic", detail=f"{category}/{topic}")
        else:
            await interaction.followup.send(
                f"❌ Failed to start tracking: {result.get('message', 'Unknown error')}",
                ephemeral=True,
            )

    @bot.tree.command(name="untrack", description="Stop tracking a topic")
    @discord.app_commands.describe(topic="Topic to stop tracking")
    async def untrack_cmd(interaction: discord.Interaction, topic: str):
        """Stop tracking a topic."""
        result = await trend_skills.untrack_topic(topic)

        if result["status"] == "ok":
            await interaction.response.send_message(f"⏹️ {result['message']}")
            audit_log(interaction.user, "untrack_topic", detail=topic)
        else:
            await interaction.response.send_message(f"❌ {result.get('message', 'Unknown error')}", ephemeral=True)


def _register_trend_view_commands(bot: commands.Bot) -> None:
    """Register read-only trend viewing commands."""

    @bot.tree.command(name="trending", description="Show trending topics")
    @discord.app_commands.describe(
        category="Filter by category (Entertainment, Finance, Sports, News, General)",
        timeframe="Time window: 24h, 7d, or 30d",
        limit="Maximum number of results (1-20)",
    )
    async def trending_cmd(
        interaction: discord.Interaction,
        category: str = "",
        timeframe: str = "24h",
        limit: int = 10,
    ):
        """Show trending topics."""
        await interaction.response.defer()

        # Validate timeframe
        if timeframe not in ["24h", "7d", "30d"]:
            await interaction.followup.send("❌ Invalid timeframe. Use: 24h, 7d, or 30d", ephemeral=True)
            return

        # Validate limit
        limit = max(1, min(20, limit))

        result = await trend_skills.get_trending_topics(category, timeframe, limit)

        if result["status"] == "ok":
            trending = result["trending_topics"]

            if not trending:
                await interaction.followup.send(
                    f"📊 No trending topics found in {category or 'all categories'} for the last {timeframe}."
                )
                return

            # Build embed
            embed = discord.Embed(
                title=f"🔥 Trending Topics — {timeframe}",
                description=f"Showing top {len(trending)} trending topics"
                + (f" in **{category}**" if category else ""),
                color=discord.Color.red(),
            )

            for i, item in enumerate(trending[:10], 1):
                # Format trend indicator
                if item["is_spike"]:
                    indicator = "🚨"
                elif item["is_breakout"]:
                    indicator = "🆕"
                elif item["trend_direction"] == "up":
                    indicator = "📈"
                elif item["trend_direction"] == "down":
                    indicator = "📉"
                else:
                    indicator = "➡️"

                # Sentiment emoji
                sent = item["sentiment"]
                sent_emoji = "🟢" if sent > 0.3 else "🔴" if sent < -0.3 else "⚪"

                # Build field
                field_name = f"{i}. {indicator} {item['topic']}"
                field_value = (
                    f"**Volume:** {item['volume']} ({item['volume_change']})\n"
                    f"**Sentiment:** {sent_emoji} {sent} ({item['sentiment_change']})\n"
                    f"**Category:** {item['category']}"
                )

                embed.add_field(name=field_name, value=field_value, inline=False)

            embed.set_footer(text=f"Data from {', '.join(result.get('sources', ['NewsAPI']))}")
            await interaction.followup.send(embed=embed)

        else:
            await interaction.followup.send(f"❌ {result.get('message', 'Unknown error')}", ephemeral=True)

    @bot.tree.command(name="trends", description="Show trend trajectory for a topic")
    @discord.app_commands.describe(
        topic="Topic to analyze",
        category="Optional category filter",
        timeframe="Time window: 24h, 7d, or 30d",
    )
    async def trends_cmd(
        interaction: discord.Interaction,
        topic: str,
        category: str = "",
        timeframe: str = "24h",
    ):
        """Show trend trajectory for a specific topic."""
        await interaction.response.defer()

        # Validate timeframe
        if timeframe not in ["24h", "7d", "30d"]:
            await interaction.followup.send("❌ Invalid timeframe. Use: 24h, 7d, or 30d", ephemeral=True)
            return

        result = await trend_skills.get_topic_trajectory(topic, category, timeframe)

        if result["status"] == "ok":
            # Build embed
            color = discord.Color.red() if result["is_trending"] else discord.Color.blue()
            title_emoji = "🚨" if result["is_spike"] else "🆕" if result["is_breakout"] else "📊"

            embed = discord.Embed(
                title=f"{title_emoji} {topic} — Trend Analysis",
                description=result["analysis"],
                color=color,
            )

            # Add metrics
            embed.add_field(
                name="Volume",
                value=f"{result['current_volume']} ({result['volume_change']})",
                inline=True,
            )
            embed.add_field(
                name="Sentiment",
                value=f"{result['sentiment']} ({result['sentiment_change']})",
                inline=True,
            )
            embed.add_field(
                name="Trend",
                value=result["trend_direction"].title(),
                inline=True,
            )

            # Add indicators
            indicators = []
            if result["is_trending"]:
                indicators.append("🔥 TRENDING")
            if result["is_spike"]:
                indicators.append("🚨 SPIKE")
            if result["is_breakout"]:
                indicators.append("🆕 BREAKOUT")
            if result["velocity"] > 2.0:
                indicators.append(f"⚡ Velocity: {result['velocity']}x")

            if indicators:
                embed.add_field(
                    name="Indicators",
                    value=" | ".join(indicators),
                    inline=False,
                )

            # Add chart in code block
            embed.add_field(
                name=f"Volume Chart — {timeframe}",
                value=f"```\n{result['chart']}\n```",
                inline=False,
            )

            embed.set_footer(text=f"Category: {result['category']} | Z-Score: {result['z_score']}")
            await interaction.followup.send(embed=embed)

        else:
            await interaction.followup.send(f"❌ {result.get('message', 'Unknown error')}", ephemeral=True)

    @bot.tree.command(name="breaking", description="Detect breaking news and spikes")
    @discord.app_commands.describe(
        category="Category to analyze",
        threshold="Spike threshold (2.0 = 2x normal volume)",
    )
    async def breaking_cmd(
        interaction: discord.Interaction,
        category: str = "News",
        threshold: float = 3.0,
    ):
        """Detect breaking news."""
        await interaction.response.defer()

        result = await trend_skills.detect_breaking_news(category, threshold)

        if result["status"] == "ok":
            breaking = result["breaking_news"]

            if not breaking:
                await interaction.followup.send(
                    f"📰 No breaking news detected in {category} (threshold: {threshold}x normal volume)"
                )
                return

            embed = discord.Embed(
                title=f"🚨 Breaking News — {category}",
                description=f"Detected {len(breaking)} topics with significant spikes",
                color=discord.Color.red(),
            )

            for i, item in enumerate(breaking[:10], 1):
                # Sentiment indicator
                sent = item["sentiment"]
                sent_emoji = "🟢" if sent > 0.3 else "🔴" if sent < -0.3 else "⚪"

                field_name = f"{i}. {item['topic']}"
                field_value = (
                    f"**Spike:** {item['spike_multiplier']}x normal volume\n"
                    f"**Volume:** {item['volume']} articles\n"
                    f"**Sentiment:** {sent_emoji} {sent}\n"
                    f"**Peak:** {item['hours_ago']}h ago"
                )

                embed.add_field(name=field_name, value=field_value, inline=False)

            embed.set_footer(text=f"Spike threshold: {threshold}x | Z-score filter applied")
            await interaction.followup.send(embed=embed)

        else:
            await interaction.followup.send(f"❌ {result.get('message', 'Unknown error')}", ephemeral=True)

    @bot.tree.command(name="tracked", description="List all tracked topics")
    async def tracked_cmd(interaction: discord.Interaction):
        """List tracked topics."""
        result = await trend_skills.list_tracked_topics()

        if result["status"] == "ok":
            topics = result["tracked_topics"]

            if not topics:
                await interaction.response.send_message(
                    "📋 No topics are currently being tracked.\nUse `/track` to start tracking a topic."
                )
                return

            embed = discord.Embed(
                title=f"📋 Tracked Topics ({len(topics)})",
                description="Topics currently being monitored for trends",
                color=discord.Color.blue(),
            )

            # Group by category
            by_category = {}
            for topic in topics:
                cat = topic["category"]
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(topic)

            for category, cat_topics in sorted(by_category.items()):
                topic_list = []
                for t in cat_topics[:10]:  # Limit to 10 per category
                    status = "✅" if t["enabled"] else "⏸️"
                    topic_list.append(f"{status} {t['topic']}")

                if len(cat_topics) > 10:
                    topic_list.append(f"... +{len(cat_topics) - 10} more")

                embed.add_field(
                    name=f"{category} ({len(cat_topics)})",
                    value="\n".join(topic_list),
                    inline=False,
                )

            embed.set_footer(text="Use /trends <topic> to view trajectory")
            await interaction.response.send_message(embed=embed)

        else:
            await interaction.response.send_message(f"❌ {result.get('message', 'Unknown error')}", ephemeral=True)


def _register_trend_commands(bot: commands.Bot) -> None:
    """Register all trend tracking commands (orchestrator)."""
    _register_trend_management_commands(bot)
    _register_trend_view_commands(bot)
