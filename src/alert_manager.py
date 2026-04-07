"""
OpenClaw Alert Manager — Discord alerting for trend detection.

Sends formatted alerts to Discord channels when trends are detected.
Includes rate limiting, configurable thresholds, and alert formatting.
"""

import logging
import time
from datetime import datetime

import discord

from trend_tracker import TrendAnalysis, get_tracker

log = logging.getLogger("openclaw.alert_manager")

# Alert cooldown (1 hour default)
DEFAULT_COOLDOWN = 3600
QUALITY_DRIFT_ALERT_COOLDOWN = 6 * 3600
_BOUNDED_ALERT_CACHE: dict[str, tuple[float, str]] = {}


def should_route_bounded_alert(
    route_key: str,
    *,
    fingerprint: str,
    cooldown_seconds: int = DEFAULT_COOLDOWN,
    now_ts: float | None = None,
) -> tuple[bool, str]:
    """Return whether an alert should be routed, with de-duplication + cooldown."""
    normalized_route = str(route_key or "").strip().lower() or "default"
    normalized_fp = str(fingerprint or "").strip()
    now_value = float(now_ts) if now_ts is not None else time.time()

    cached = _BOUNDED_ALERT_CACHE.get(normalized_route)
    if cached:
        previous_sent_at, previous_fp = cached
        elapsed = max(0.0, now_value - float(previous_sent_at))
        if elapsed < max(0, int(cooldown_seconds)):
            if previous_fp == normalized_fp:
                return False, "duplicate_within_cooldown"
            return False, "cooldown_active"

    _BOUNDED_ALERT_CACHE[normalized_route] = (now_value, normalized_fp)
    return True, "routed"


def reset_bounded_alert_cache() -> None:
    """Test helper to clear in-memory bounded alert state."""
    _BOUNDED_ALERT_CACHE.clear()


def format_trend_alert(analysis: TrendAnalysis, alert_type: str = "TRENDING") -> discord.Embed:
    """
    Format trend analysis as a Discord embed.

    Args:
        analysis: TrendAnalysis object
        alert_type: Type of alert ("TRENDING", "SPIKE", "BREAKOUT", "SENTIMENT")

    Returns:
        Discord Embed object
    """
    # Determine emoji and color
    emoji_map = {
        "TRENDING": "🚨",
        "SPIKE": "📈",
        "BREAKOUT": "🆕",
        "SENTIMENT": "💭",
    }
    emoji = emoji_map.get(alert_type, "📊")

    color_map = {
        "TRENDING": discord.Color.red(),
        "SPIKE": discord.Color.orange(),
        "BREAKOUT": discord.Color.green(),
        "SENTIMENT": discord.Color.blue(),
    }
    color = color_map.get(alert_type, discord.Color.greyple())

    # Build title
    title = f"{emoji} {alert_type} ALERT: {analysis.topic}"

    # Build description
    description_parts = []

    # Category
    description_parts.append(f"**Category:** {analysis.category}")

    # Volume metrics
    volume_arrow = "↑" if analysis.volume_change_pct > 0 else "↓" if analysis.volume_change_pct < 0 else "→"
    description_parts.append(
        f"**Volume:** {analysis.current_volume} articles {volume_arrow} {abs(analysis.volume_change_pct):.0f}% vs 7d avg"
    )

    # Sentiment metrics
    sent_emoji = "🟢" if analysis.current_sentiment > 0.3 else "🔴" if analysis.current_sentiment < -0.3 else "⚪"
    sent_label = "Bullish" if analysis.current_sentiment > 0.3 else "Bearish" if analysis.current_sentiment < -0.3 else "Neutral"
    sent_change = f"({'+' if analysis.sentiment_change_24h > 0 else ''}{analysis.sentiment_change_24h:.2f})" if analysis.sentiment_change_24h != 0 else ""
    description_parts.append(
        f"**Sentiment:** {sent_emoji} {analysis.current_sentiment:.2f} {sent_label} {sent_change}"
    )

    # Trend direction
    trend_arrow = "🔥" if analysis.trend_direction == "up" else "❄️" if analysis.trend_direction == "down" else "➡️"
    description_parts.append(f"**Trend:** {trend_arrow} {analysis.trend_direction.title()}")

    # Peak time
    if analysis.peak_time:
        peak_dt = datetime.fromtimestamp(analysis.peak_time)
        now = datetime.now()
        hours_ago = int((now.timestamp() - analysis.peak_time) / 3600)
        if hours_ago < 1:
            peak_str = "< 1 hour ago"
        elif hours_ago < 24:
            peak_str = f"{hours_ago} hours ago"
        else:
            peak_str = peak_dt.strftime("%Y-%m-%d %H:%M")
        description_parts.append(f"**Peak:** {peak_str}")

    # Sources
    if analysis.sources:
        sources_str = ", ".join(analysis.sources[:5])  # Limit to 5 sources
        if len(analysis.sources) > 5:
            sources_str += f" (+{len(analysis.sources) - 5} more)"
        description_parts.append(f"**Sources:** {sources_str}")

    # Metrics footer
    footer_parts = []
    if analysis.is_spike:
        footer_parts.append("🚨 SPIKE DETECTED")
    if analysis.is_breakout:
        footer_parts.append("🆕 BREAKOUT")
    if analysis.velocity > 2.0:
        footer_parts.append(f"⚡ High Velocity ({analysis.velocity:.1f}x)")
    if abs(analysis.z_score) > 2.0:
        footer_parts.append(f"📊 Z-Score: {analysis.z_score:.1f}")

    embed = discord.Embed(
        title=title,
        description="\n".join(description_parts),
        color=color,
        timestamp=datetime.now(),
    )

    if footer_parts:
        embed.set_footer(text=" | ".join(footer_parts))

    return embed


def format_text_alert(analysis: TrendAnalysis, alert_type: str = "TRENDING") -> str:
    """
    Format trend analysis as plain text (fallback for non-embed contexts).

    Args:
        analysis: TrendAnalysis object
        alert_type: Type of alert

    Returns:
        Formatted text string
    """
    emoji_map = {
        "TRENDING": "🚨",
        "SPIKE": "📈",
        "BREAKOUT": "🆕",
        "SENTIMENT": "💭",
    }
    emoji = emoji_map.get(alert_type, "📊")

    volume_arrow = "↑" if analysis.volume_change_pct > 0 else "↓" if analysis.volume_change_pct < 0 else "→"
    sent_emoji = "🟢" if analysis.current_sentiment > 0.3 else "🔴" if analysis.current_sentiment < -0.3 else "⚪"
    sent_label = "Bullish" if analysis.current_sentiment > 0.3 else "Bearish" if analysis.current_sentiment < -0.3 else "Neutral"

    lines = [
        f"{emoji} {alert_type} ALERT",
        f"Topic: {analysis.topic}",
        f"Category: {analysis.category}",
        f"Volume: {analysis.current_volume} articles ({volume_arrow} {abs(analysis.volume_change_pct):.0f}% vs 7d avg)",
        f"Sentiment: {sent_emoji} {analysis.current_sentiment:.2f} {sent_label}",
    ]

    if analysis.peak_time:
        peak_dt = datetime.fromtimestamp(analysis.peak_time)
        now = datetime.now()
        hours_ago = int((now.timestamp() - analysis.peak_time) / 3600)
        if hours_ago < 1:
            peak_str = "< 1 hour ago"
        elif hours_ago < 24:
            peak_str = f"{hours_ago} hours ago"
        else:
            peak_str = peak_dt.strftime("%Y-%m-%d %H:%M")
        lines.append(f"Peak: {peak_str}")

    if analysis.sources:
        sources_str = ", ".join(analysis.sources[:3])
        lines.append(f"Sources: {sources_str}")

    return "\n".join(lines)


async def send_trend_alert(
    bot: discord.Client,
    channel_id: int,
    analysis: TrendAnalysis,
    alert_type: str = "TRENDING",
    cooldown: int = DEFAULT_COOLDOWN,
) -> bool:
    """
    Send a trend alert to a Discord channel with rate limiting.

    Args:
        bot: Discord bot instance
        channel_id: Discord channel ID to send to
        analysis: TrendAnalysis object
        alert_type: Type of alert
        cooldown: Minimum seconds between alerts for this topic

    Returns:
        True if alert was sent, False if rate limited or failed
    """
    tracker = get_tracker()

    # Check rate limit
    if not tracker.can_alert(analysis.topic, cooldown):
        log.debug("Alert rate limited for topic: %s", analysis.topic)
        return False

    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            log.error("Channel %d not found", channel_id)
            return False

        # Send embed
        embed = format_trend_alert(analysis, alert_type)
        await channel.send(embed=embed)

        # Record alert
        tracker.record_alert(analysis.topic)
        log.info("Sent %s alert for %s to channel %d", alert_type, analysis.topic, channel_id)
        return True

    except Exception as e:
        log.error("Failed to send alert for %s: %s", analysis.topic, e)
        return False


async def check_and_alert_all(
    bot: discord.Client,
    channel_id: int,
    category: str = "",
    hours: int = 24,
) -> list[str]:
    """
    Check all trending topics and send alerts for significant ones.

    Args:
        bot: Discord bot instance
        channel_id: Discord channel ID for alerts
        category: Optional category filter
        hours: Hours to analyze

    Returns:
        List of topics that triggered alerts
    """
    tracker = get_tracker()
    trending = tracker.get_trending_topics(category, hours, limit=20)

    alerted_topics = []

    for analysis in trending:
        # Determine alert type
        alert_type = "TRENDING"
        if analysis.is_spike:
            alert_type = "SPIKE"
        elif analysis.is_breakout:
            alert_type = "BREAKOUT"
        elif abs(analysis.sentiment_change_24h) >= 0.3:
            alert_type = "SENTIMENT"

        # Send alert
        if await send_trend_alert(bot, channel_id, analysis, alert_type):
            alerted_topics.append(analysis.topic)

    log.info("Checked trends: %d trending, %d alerts sent", len(trending), len(alerted_topics))
    return alerted_topics


def render_text_chart(
    topic: str, category: str = "", hours: int = 24, width: int = 40
) -> str:
    """
    Render a simple ASCII/emoji chart of volume over time.

    Args:
        topic: Topic name
        category: Optional category filter
        hours: Hours to display
        width: Character width of chart

    Returns:
        ASCII art chart as string
    """
    tracker = get_tracker()
    points = tracker.get_trend(topic, category, hours)

    if not points:
        return f"📊 No data for {topic}"

    # Group by hour
    hourly_volumes = {}
    for point in points:
        hour = int(point.timestamp // 3600) * 3600
        hourly_volumes[hour] = hourly_volumes.get(hour, 0) + point.volume

    if not hourly_volumes:
        return f"📊 No data for {topic}"

    # Sort by time
    sorted_hours = sorted(hourly_volumes.keys())
    volumes = [hourly_volumes[h] for h in sorted_hours]

    # Scale to chart width
    max_vol = max(volumes) if volumes else 1
    scaled = [int((v / max_vol) * width) for v in volumes]

    # Build chart
    lines = [f"📊 **{topic}** — Last {hours}h"]
    lines.append("")

    # Use block characters for better visualization
    blocks = ["░", "▒", "▓", "█"]

    for i, (hour, vol, scale) in enumerate(zip(sorted_hours, volumes, scaled)):
        dt = datetime.fromtimestamp(hour)
        hour_str = dt.strftime("%H:%M")

        # Create bar
        full_blocks = scale // len(blocks)
        partial = scale % len(blocks)
        bar = "█" * full_blocks
        if partial > 0 and full_blocks < width:
            bar += blocks[partial - 1]

        lines.append(f"{hour_str} │{bar:<{width}} {vol}")

    lines.append("")
    lines.append(f"Max: {max_vol} | Avg: {sum(volumes) // len(volumes)}")

    return "\n".join(lines)
