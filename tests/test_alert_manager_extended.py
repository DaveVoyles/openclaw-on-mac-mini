"""Extended tests for alert_manager.py — Discord trend alerting."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from alert_manager import (
    check_and_alert_all,
    format_text_alert,
    format_trend_alert,
    render_text_chart,
    reset_bounded_alert_cache,
    send_trend_alert,
    should_route_bounded_alert,
)
from trend_tracker import TrendAnalysis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_analysis(**overrides) -> TrendAnalysis:
    defaults = dict(
        topic="Bitcoin",
        category="Crypto",
        current_volume=100,
        avg_volume_24h=50.0,
        avg_volume_7d=40.0,
        volume_change_pct=50.0,
        current_sentiment=0.5,
        sentiment_change_24h=0.1,
        velocity=1.5,
        is_trending=True,
        is_spike=False,
        is_breakout=False,
        trend_direction="up",
        z_score=1.2,
        peak_time=None,
        sources=["reuters.com", "bbc.com"],
    )
    defaults.update(overrides)
    return TrendAnalysis(**defaults)


# ---------------------------------------------------------------------------
# should_route_bounded_alert — extra scenarios
# ---------------------------------------------------------------------------

def test_bounded_alert_different_fingerprint_still_rate_limited():
    reset_bounded_alert_cache()
    should_route_bounded_alert("key", fingerprint="fp-a", cooldown_seconds=300, now_ts=1000.0)
    allowed, reason = should_route_bounded_alert("key", fingerprint="fp-b", cooldown_seconds=300, now_ts=1100.0)
    assert allowed is False
    assert reason == "cooldown_active"


def test_bounded_alert_empty_route_key_normalizes_to_default():
    reset_bounded_alert_cache()
    allowed, reason = should_route_bounded_alert("", fingerprint="fp", cooldown_seconds=60, now_ts=5000.0)
    assert allowed is True
    assert reason == "routed"


def test_bounded_alert_zero_cooldown_always_routes():
    reset_bounded_alert_cache()
    should_route_bounded_alert("k2", fingerprint="fp", cooldown_seconds=0, now_ts=3000.0)
    allowed, reason = should_route_bounded_alert("k2", fingerprint="fp", cooldown_seconds=0, now_ts=3001.0)
    assert allowed is True


def test_bounded_alert_first_call_always_routes():
    reset_bounded_alert_cache()
    allowed, reason = should_route_bounded_alert("new-key", fingerprint="x", cooldown_seconds=3600)
    assert allowed is True
    assert reason == "routed"


def test_bounded_alert_updates_cache_on_route():
    reset_bounded_alert_cache()
    should_route_bounded_alert("track", fingerprint="fp1", cooldown_seconds=60, now_ts=1000.0)
    # Second call within cooldown with same fp → duplicate
    allowed, reason = should_route_bounded_alert("track", fingerprint="fp1", cooldown_seconds=60, now_ts=1050.0)
    assert reason == "duplicate_within_cooldown"


# ---------------------------------------------------------------------------
# format_trend_alert
# ---------------------------------------------------------------------------

def test_format_trend_alert_returns_embed():
    analysis = make_analysis()
    embed = format_trend_alert(analysis, "TRENDING")
    assert isinstance(embed, discord.Embed)


def test_format_trend_alert_trending():
    embed = format_trend_alert(make_analysis(), "TRENDING")
    assert "🚨" in embed.title
    assert "TRENDING" in embed.title
    assert "Bitcoin" in embed.title


def test_format_trend_alert_spike():
    embed = format_trend_alert(make_analysis(is_spike=True, volume_change_pct=400.0), "SPIKE")
    assert "📈" in embed.title
    assert "SPIKE" in embed.title


def test_format_trend_alert_breakout():
    embed = format_trend_alert(make_analysis(is_breakout=True), "BREAKOUT")
    assert "🆕" in embed.title
    assert "BREAKOUT" in embed.title


def test_format_trend_alert_sentiment():
    embed = format_trend_alert(make_analysis(current_sentiment=-0.5), "SENTIMENT")
    assert "💭" in embed.title
    assert "Bearish" in embed.description


def test_format_trend_alert_neutral_sentiment():
    embed = format_trend_alert(make_analysis(current_sentiment=0.1, sentiment_change_24h=0.0))
    assert "Neutral" in embed.description


def test_format_trend_alert_bullish_sentiment():
    embed = format_trend_alert(make_analysis(current_sentiment=0.8))
    assert "Bullish" in embed.description


def test_format_trend_alert_unknown_type_uses_fallback():
    embed = format_trend_alert(make_analysis(), "CUSTOM")
    assert "📊" in embed.title
    assert embed is not None


def test_format_trend_alert_trend_down():
    embed = format_trend_alert(make_analysis(trend_direction="down", volume_change_pct=-50.0))
    assert "❄️" in embed.description


def test_format_trend_alert_trend_stable():
    embed = format_trend_alert(make_analysis(trend_direction="stable", volume_change_pct=0.0))
    assert "➡️" in embed.description


def test_format_trend_alert_volume_arrow_down():
    embed = format_trend_alert(make_analysis(volume_change_pct=-30.0))
    assert "↓" in embed.description


def test_format_trend_alert_volume_arrow_stable():
    embed = format_trend_alert(make_analysis(volume_change_pct=0.0))
    assert "→" in embed.description


def test_format_trend_alert_with_peak_under_1h():
    analysis = make_analysis(peak_time=time.time() - 1800)
    embed = format_trend_alert(analysis)
    assert "< 1 hour ago" in embed.description


def test_format_trend_alert_with_peak_hours_ago():
    analysis = make_analysis(peak_time=time.time() - 7200)
    embed = format_trend_alert(analysis)
    assert "hours ago" in embed.description


def test_format_trend_alert_with_peak_old_date():
    analysis = make_analysis(peak_time=time.time() - 86400 * 3)
    embed = format_trend_alert(analysis)
    assert "Peak" in embed.description


def test_format_trend_alert_many_sources_truncated():
    analysis = make_analysis(sources=["a.com", "b.com", "c.com", "d.com", "e.com", "f.com"])
    embed = format_trend_alert(analysis)
    assert "+1 more" in embed.description


def test_format_trend_alert_footer_with_spike_and_breakout():
    analysis = make_analysis(is_spike=True, is_breakout=True, velocity=3.5, z_score=2.5)
    embed = format_trend_alert(analysis)
    assert embed.footer is not None
    footer_text = embed.footer.text
    assert "SPIKE" in footer_text
    assert "BREAKOUT" in footer_text
    assert "Velocity" in footer_text
    assert "Z-Score" in footer_text


def test_format_trend_alert_no_footer_when_no_flags():
    analysis = make_analysis(is_spike=False, is_breakout=False, velocity=1.0, z_score=1.0)
    embed = format_trend_alert(analysis)
    assert embed.footer.text is None


def test_format_trend_alert_no_sources():
    analysis = make_analysis(sources=[])
    embed = format_trend_alert(analysis)
    assert "Sources" not in embed.description


def test_format_trend_alert_sentiment_change_shown():
    analysis = make_analysis(sentiment_change_24h=0.25)
    embed = format_trend_alert(analysis)
    assert "+0.25" in embed.description


# ---------------------------------------------------------------------------
# format_text_alert
# ---------------------------------------------------------------------------

def test_format_text_alert_basic():
    text = format_text_alert(make_analysis(), "TRENDING")
    assert "🚨 TRENDING ALERT" in text
    assert "Bitcoin" in text
    assert "Crypto" in text


def test_format_text_alert_bullish():
    text = format_text_alert(make_analysis(current_sentiment=0.5))
    assert "Bullish" in text
    assert "🟢" in text


def test_format_text_alert_bearish():
    text = format_text_alert(make_analysis(current_sentiment=-0.5))
    assert "Bearish" in text
    assert "🔴" in text


def test_format_text_alert_neutral():
    text = format_text_alert(make_analysis(current_sentiment=0.0))
    assert "Neutral" in text
    assert "⚪" in text


def test_format_text_alert_volume_up():
    text = format_text_alert(make_analysis(volume_change_pct=50.0))
    assert "↑" in text


def test_format_text_alert_volume_down():
    text = format_text_alert(make_analysis(volume_change_pct=-30.0))
    assert "↓" in text


def test_format_text_alert_volume_stable():
    text = format_text_alert(make_analysis(volume_change_pct=0.0))
    assert "→" in text


def test_format_text_alert_with_peak_under_1h():
    text = format_text_alert(make_analysis(peak_time=time.time() - 1800))
    assert "< 1 hour ago" in text


def test_format_text_alert_with_peak_hours_ago():
    text = format_text_alert(make_analysis(peak_time=time.time() - 7200))
    assert "hours ago" in text


def test_format_text_alert_with_peak_old_date():
    text = format_text_alert(make_analysis(peak_time=time.time() - 86400 * 2))
    assert "Peak" in text


def test_format_text_alert_with_sources():
    text = format_text_alert(make_analysis(sources=["reuters.com", "bbc.com", "cnn.com", "fox.com"]))
    assert "reuters.com" in text


def test_format_text_alert_no_sources():
    text = format_text_alert(make_analysis(sources=[]))
    assert "Bitcoin" in text  # renders without sources


def test_format_text_alert_spike_type():
    text = format_text_alert(make_analysis(), "SPIKE")
    assert "📈 SPIKE ALERT" in text


# ---------------------------------------------------------------------------
# send_trend_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_trend_alert_rate_limited():
    mock_tracker = MagicMock()
    mock_tracker.can_alert.return_value = False
    bot = MagicMock(spec=discord.Client)
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await send_trend_alert(bot, 12345, make_analysis())
    assert result is False


@pytest.mark.asyncio
async def test_send_trend_alert_channel_not_found():
    mock_tracker = MagicMock()
    mock_tracker.can_alert.return_value = True
    bot = MagicMock(spec=discord.Client)
    bot.get_channel.return_value = None
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await send_trend_alert(bot, 99999, make_analysis())
    assert result is False


@pytest.mark.asyncio
async def test_send_trend_alert_success():
    mock_tracker = MagicMock()
    mock_tracker.can_alert.return_value = True
    mock_tracker.record_alert = MagicMock()
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    bot = MagicMock(spec=discord.Client)
    bot.get_channel.return_value = mock_channel
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await send_trend_alert(bot, 12345, make_analysis(), "TRENDING")
    assert result is True
    mock_channel.send.assert_called_once()
    mock_tracker.record_alert.assert_called_once_with("Bitcoin")


@pytest.mark.asyncio
async def test_send_trend_alert_discord_exception():
    mock_tracker = MagicMock()
    mock_tracker.can_alert.return_value = True
    bot = MagicMock(spec=discord.Client)
    bot.get_channel.side_effect = Exception("discord error")
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await send_trend_alert(bot, 12345, make_analysis())
    assert result is False


@pytest.mark.asyncio
async def test_send_trend_alert_uses_custom_cooldown():
    mock_tracker = MagicMock()
    mock_tracker.can_alert.return_value = False
    bot = MagicMock(spec=discord.Client)
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        await send_trend_alert(bot, 12345, make_analysis(), cooldown=7200)
    mock_tracker.can_alert.assert_called_once_with("Bitcoin", 7200)


# ---------------------------------------------------------------------------
# check_and_alert_all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_and_alert_all_empty_trending():
    mock_tracker = MagicMock()
    mock_tracker.get_trending_topics.return_value = []
    bot = MagicMock(spec=discord.Client)
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await check_and_alert_all(bot, 12345)
    assert result == []


@pytest.mark.asyncio
async def test_check_and_alert_all_spike_type():
    analysis = make_analysis(is_spike=True)
    mock_tracker = MagicMock()
    mock_tracker.get_trending_topics.return_value = [analysis]
    mock_tracker.can_alert.return_value = True
    mock_tracker.record_alert = MagicMock()
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    bot = MagicMock(spec=discord.Client)
    bot.get_channel.return_value = mock_channel
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await check_and_alert_all(bot, 12345)
    assert "Bitcoin" in result


@pytest.mark.asyncio
async def test_check_and_alert_all_breakout_type():
    analysis = make_analysis(is_spike=False, is_breakout=True)
    mock_tracker = MagicMock()
    mock_tracker.get_trending_topics.return_value = [analysis]
    mock_tracker.can_alert.return_value = True
    mock_tracker.record_alert = MagicMock()
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    bot = MagicMock(spec=discord.Client)
    bot.get_channel.return_value = mock_channel
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await check_and_alert_all(bot, 12345)
    assert "Bitcoin" in result


@pytest.mark.asyncio
async def test_check_and_alert_all_sentiment_type():
    analysis = make_analysis(is_spike=False, is_breakout=False, sentiment_change_24h=0.5)
    mock_tracker = MagicMock()
    mock_tracker.get_trending_topics.return_value = [analysis]
    mock_tracker.can_alert.return_value = True
    mock_tracker.record_alert = MagicMock()
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    bot = MagicMock(spec=discord.Client)
    bot.get_channel.return_value = mock_channel
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await check_and_alert_all(bot, 12345)
    assert "Bitcoin" in result


@pytest.mark.asyncio
async def test_check_and_alert_all_rate_limited_not_in_result():
    analysis = make_analysis()
    mock_tracker = MagicMock()
    mock_tracker.get_trending_topics.return_value = [analysis]
    mock_tracker.can_alert.return_value = False
    bot = MagicMock(spec=discord.Client)
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = await check_and_alert_all(bot, 12345)
    assert result == []


@pytest.mark.asyncio
async def test_check_and_alert_all_with_category_filter():
    mock_tracker = MagicMock()
    mock_tracker.get_trending_topics.return_value = []
    bot = MagicMock(spec=discord.Client)
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        await check_and_alert_all(bot, 12345, category="Crypto", hours=48)
    mock_tracker.get_trending_topics.assert_called_once_with("Crypto", 48, limit=20)


# ---------------------------------------------------------------------------
# render_text_chart
# ---------------------------------------------------------------------------

def test_render_text_chart_no_data():
    mock_tracker = MagicMock()
    mock_tracker.get_trend.return_value = []
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = render_text_chart("Bitcoin")
    assert "No data" in result
    assert "Bitcoin" in result


def test_render_text_chart_with_data():
    from trend_tracker import DataPoint
    now = time.time()
    points = [
        DataPoint(
            timestamp=now - 3600 * i,
            topic="Bitcoin",
            category="Crypto",
            volume=10 + i * 5,
            sentiment=0.3,
            sources="reuters.com",
        )
        for i in range(5)
    ]
    mock_tracker = MagicMock()
    mock_tracker.get_trend.return_value = points
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = render_text_chart("Bitcoin", hours=6)
    assert "Bitcoin" in result
    assert "Max:" in result
    assert "│" in result


def test_render_text_chart_with_category_and_width():
    from trend_tracker import DataPoint
    now = time.time()
    points = [
        DataPoint(
            timestamp=now - 1800,
            topic="Ethereum",
            category="Crypto",
            volume=50,
            sentiment=0.2,
            sources="coindesk.com",
        )
    ]
    mock_tracker = MagicMock()
    mock_tracker.get_trend.return_value = points
    with patch("alert_manager.get_tracker", return_value=mock_tracker):
        result = render_text_chart("Ethereum", category="Crypto", hours=24, width=20)
    assert "Ethereum" in result
    assert "Avg:" in result

# --- Merged from test_alert_manager.py ---
"""Tests for bounded alert routing helpers."""

from alert_manager import reset_bounded_alert_cache, should_route_bounded_alert


def test_bounded_alert_deduplicates_within_cooldown():
    reset_bounded_alert_cache()
    allowed_first, reason_first = should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="same-payload",
        cooldown_seconds=300,
        now_ts=1000.0,
    )
    allowed_second, reason_second = should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="same-payload",
        cooldown_seconds=300,
        now_ts=1100.0,
    )

    assert allowed_first is True
    assert reason_first == "routed"
    assert allowed_second is False
    assert reason_second == "duplicate_within_cooldown"


def test_bounded_alert_allows_after_cooldown():
    reset_bounded_alert_cache()
    should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="payload-a",
        cooldown_seconds=120,
        now_ts=1000.0,
    )
    allowed, reason = should_route_bounded_alert(
        "quality_calibration_drift",
        fingerprint="payload-a",
        cooldown_seconds=120,
        now_ts=1121.0,
    )

    assert allowed is True
    assert reason == "routed"

