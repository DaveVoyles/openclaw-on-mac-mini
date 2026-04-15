"""
OpenClaw Alert Manager — Discord alerting for trend detection.

Sends formatted alerts to Discord channels when trends are detected.
Includes rate limiting, configurable thresholds, and alert formatting.
"""

import logging
import os
import time
from collections import OrderedDict
from datetime import datetime

import discord

from trend_tracker import TrendAnalysis, get_tracker

log = logging.getLogger("openclaw.alert_manager")

# Alert cooldown (1 hour default)
DEFAULT_COOLDOWN = 3600
QUALITY_DRIFT_ALERT_COOLDOWN = 6 * 3600
# LRU-capped cache — oldest entries evicted when limit reached.
# Prevents unbounded memory growth on long-running bots (10 alerts/day × 365 days
# would otherwise accumulate ~3,650 entries; 10k cap = ~27 years headroom).
_CACHE_MAX_SIZE = 10_000
_BOUNDED_ALERT_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()

# ---------------------------------------------------------------------------
# W13-5 — Remediation hints per drift category
# ---------------------------------------------------------------------------

REMEDIATION_HINTS: dict[str, str] = {
    "provider_degradation": "💡 Switch primary model: set `MODEL_PRIMARY` to an alternative provider.",
    "tool_failure_spike": "💡 Check API key validity and tool endpoint health.",
    "recall_drop": "💡 Run vector store maintenance: `/admin rebuild-index`.",
    "latency_spike": "💡 Check network connectivity and provider status pages.",
}


def get_remediation_hint(drift_category: str) -> str | None:
    """Return a remediation hint string for the given drift category, or None."""
    return REMEDIATION_HINTS.get(str(drift_category).lower().strip())


# ---------------------------------------------------------------------------
# W13-2 — 30-minute deduplication window with resolved follow-up
# ---------------------------------------------------------------------------

_DEDUP_WINDOW_SECONDS = 1800  # 30 minutes
# alert_key → (sent_at, channel_id, message_id, severity)
_recent_alerts: dict[str, tuple[float, int, int, str]] = {}

_SEVERITY_ORDER = ["debug", "info", "warning", "critical"]


def _escalate_severity(severity: str) -> str:
    """Return the next severity level up, capped at critical."""
    lower = severity.lower()
    idx = _SEVERITY_ORDER.index(lower) if lower in _SEVERITY_ORDER else 1
    return _SEVERITY_ORDER[min(idx + 1, len(_SEVERITY_ORDER) - 1)]


# ---------------------------------------------------------------------------
# W13-3 — Alert snooze/resolve via reaction
# ---------------------------------------------------------------------------

_SNOOZE_DURATION = 3600  # 1 hour
# alert_key → snoozed-until timestamp
_snoozed: dict[str, float] = {}
# message_id → alert_key (for reaction lookup)
_alert_messages: dict[int, str] = {}

_OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", os.getenv("BOT_OWNER_ID", "0")))


async def handle_alert_reaction(message_id: int, emoji: str, user_id: int) -> None:
    """Handle a Discord reaction on an alert message.

    ⏰ → snooze the alert class for 1 hour.
    ✅ → mark as resolved and post a resolved follow-up.

    Called from bot.py's on_raw_reaction_add event handler.
    """
    if _OWNER_USER_ID and user_id != _OWNER_USER_ID:
        return  # Only the bot owner can snooze/resolve via reaction

    alert_key = _alert_messages.get(message_id)
    if not alert_key:
        return

    if emoji in ("⏰", "⏰"):
        _snoozed[alert_key] = time.time() + _SNOOZE_DURATION
        log.info("Alert snoozed for 1 hour: %s", alert_key)

    elif emoji in ("✅", "✅"):
        await _send_resolved_followup(alert_key)


def _is_snoozed(alert_key: str) -> bool:
    """Return True if the alert is currently snoozed."""
    until = _snoozed.get(alert_key, 0)
    if until and time.time() < until:
        return True
    _snoozed.pop(alert_key, None)
    return False


# ---------------------------------------------------------------------------
# W13-1 — Severity-based alert routing
# ---------------------------------------------------------------------------

async def send_severity_alert(
    bot: discord.Client,
    *,
    title: str,
    description: str,
    severity: str,
    component: str = "system",
    alert_type: str = "generic",
    embed_fields: list[tuple[str, str, bool]] | None = None,
    color: discord.Color | None = None,
) -> bool:
    """Send an alert routed by severity level.

    - DEBUG / INFO  → log only, no Discord message
    - WARNING       → post to ALERT_CHANNEL_ID
    - CRITICAL      → post to ALERT_CHANNEL_ID **and** DM the bot owner

    Returns True if a Discord message was sent.
    """
    from config import cfg

    alert_channel_id = int(os.getenv("ALERT_CHANNEL_ID", "0"))
    severity_lower = severity.lower()

    if severity_lower in ("debug", "info"):
        log.info("[%s] %s — %s", severity.upper(), title, description[:200])
        return False

    alert_key = f"{alert_type}:{component}"

    # W13-3 snooze check
    if _is_snoozed(alert_key):
        log.debug("Alert suppressed (snoozed): %s", alert_key)
        return False

    # W13-2 dedup check
    now = time.time()
    cached = _recent_alerts.get(alert_key)
    effective_severity = severity_lower
    if cached:
        sent_at, cached_channel_id, cached_msg_id, cached_severity = cached
        elapsed = now - sent_at
        if elapsed < _DEDUP_WINDOW_SECONDS:
            log.debug("Alert suppressed (dedup %ds remaining): %s", int(_DEDUP_WINDOW_SECONDS - elapsed), alert_key)
            return False
        # Persisted past window → escalate
        effective_severity = _escalate_severity(cached_severity)
        log.info("Alert re-sent with escalated severity %s: %s", effective_severity, alert_key)

    if not alert_channel_id:
        log.warning("[%s] %s — no ALERT_CHANNEL_ID set", severity.upper(), title)
        return False

    channel = bot.get_channel(alert_channel_id)
    if not channel:
        log.warning("Alert channel %d not found", alert_channel_id)
        return False

    # Build embed
    _color = color or (discord.Color.red() if effective_severity == "critical" else discord.Color.orange())
    embed = discord.Embed(title=title, description=description, color=_color)
    for field_name, field_value, inline in (embed_fields or []):
        embed.add_field(name=field_name, value=field_value, inline=inline)
    embed.set_footer(text=f"Severity: {effective_severity.upper()} • {component}")

    sent_msg = None
    try:
        sent_msg = await channel.send(embed=embed)
        if effective_severity == "critical":
            # Add reactions for snooze/resolve
            try:
                await sent_msg.add_reaction("⏰")
                await sent_msg.add_reaction("✅")
            except discord.HTTPException:
                pass
    except discord.HTTPException as exc:
        log.error("Failed to send alert embed: %s", exc)
        return False

    # Record in dedup cache
    msg_id = sent_msg.id if sent_msg else 0
    _recent_alerts[alert_key] = (now, alert_channel_id, msg_id, effective_severity)
    if msg_id:
        _alert_messages[msg_id] = alert_key

    # W13-1 CRITICAL: also DM the bot owner
    if effective_severity == "critical" and _OWNER_USER_ID:
        try:
            owner = await bot.fetch_user(_OWNER_USER_ID)
            if owner:
                dm_embed = discord.Embed(
                    title=f"🚨 CRITICAL: {title}",
                    description=description,
                    color=discord.Color.red(),
                )
                await owner.send(embed=dm_embed)
        except Exception as exc:
            log.warning("Failed to DM owner for critical alert: %s", exc)

    log.warning("[%s] Alert sent: %s", effective_severity.upper(), title)
    return True


async def _send_resolved_followup(alert_key: str) -> None:
    """Post a '✅ Resolved' follow-up message to the channel where the alert was posted."""
    cached = _recent_alerts.get(alert_key)
    if not cached:
        return
    _, channel_id, _, _ = cached

    # Remove from caches so the alert can fire again if the issue recurs
    _recent_alerts.pop(alert_key, None)
    _snoozed.pop(alert_key, None)

    log.info("Alert resolved: %s", alert_key)
    # Note: posting the resolved follow-up requires the bot instance.
    # Callers that have the bot should use send_alert_resolved() instead.


async def send_alert_resolved(bot: discord.Client, *, alert_type: str, component: str, title: str) -> None:
    """Post a '✅ Resolved' message to the channel where the alert was posted.

    Call this from monitoring code when an alert condition clears.
    """
    alert_key = f"{alert_type}:{component}"
    cached = _recent_alerts.get(alert_key)
    channel_id = cached[1] if cached else int(os.getenv("ALERT_CHANNEL_ID", "0"))

    _recent_alerts.pop(alert_key, None)
    _snoozed.pop(alert_key, None)

    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    try:
        embed = discord.Embed(
            title=f"✅ Resolved: {title}",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Alert resolved • {component}")
        await channel.send(embed=embed)
    except discord.HTTPException as exc:
        log.warning("Failed to send resolved follow-up: %s", exc)


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

    # Move-to-end (LRU) then evict oldest if over cap
    _BOUNDED_ALERT_CACHE[normalized_route] = (now_value, normalized_fp)
    _BOUNDED_ALERT_CACHE.move_to_end(normalized_route)
    if len(_BOUNDED_ALERT_CACHE) > _CACHE_MAX_SIZE:
        _BOUNDED_ALERT_CACHE.popitem(last=False)
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
