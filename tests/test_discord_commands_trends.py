"""Tests for discord_commands/trends.py — trend tracking slash commands."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

# trend_skills lives in skills/ not src/ — mock it before import
_trend_skills_mock = MagicMock()
sys.modules.setdefault("trend_skills", _trend_skills_mock)

import discord_commands.trends as trends_mod  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_interaction(user_id: int = 111):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.channel_id = 100
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _make_bot():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    trends_mod._register_trend_commands(bot)
    return bot


def _get_cmd(bot, name):
    return next(cmd for cmd in bot.tree.get_commands() if cmd.name == name)


# ---------------------------------------------------------------------------
# /track
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_track_success():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.track_topic = AsyncMock(return_value={
        "status": "ok", "message": "Now tracking Bitcoin"
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "track").callback(interaction, "Bitcoin", "Finance")
    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_track_default_category():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.track_topic = AsyncMock(return_value={
        "status": "ok", "message": "Now tracking Lakers"
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "track").callback(interaction, "Lakers")
    _trend_skills_mock.track_topic.assert_awaited_once()
    call_args = _trend_skills_mock.track_topic.await_args
    assert call_args.args[1] == "General"


@pytest.mark.asyncio
async def test_track_failure():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.track_topic = AsyncMock(return_value={
        "status": "error", "message": "DB unavailable"
    })
    await _get_cmd(bot, "track").callback(interaction, "Ethereum", "Finance")
    interaction.followup.send.assert_awaited_once()
    args = interaction.followup.send.await_args.args
    assert "failed" in args[0].lower() or "❌" in args[0]


# ---------------------------------------------------------------------------
# /untrack
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_untrack_success():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.untrack_topic = AsyncMock(return_value={
        "status": "ok", "message": "Stopped tracking Bitcoin"
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "untrack").callback(interaction, "Bitcoin")
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "Stopped tracking Bitcoin" in msg


@pytest.mark.asyncio
async def test_untrack_failure():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.untrack_topic = AsyncMock(return_value={
        "status": "error", "message": "Topic not found"
    })
    await _get_cmd(bot, "untrack").callback(interaction, "Unknown")
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "❌" in msg or "error" in msg.lower() or "not found" in msg.lower()


# ---------------------------------------------------------------------------
# /trending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trending_invalid_timeframe():
    bot = _make_bot()
    interaction = _make_interaction()
    await _get_cmd(bot, "trending").callback(interaction, "", "3d", 10)
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "invalid timeframe" in msg.lower()


@pytest.mark.asyncio
async def test_trending_no_results():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_trending_topics = AsyncMock(return_value={
        "status": "ok", "trending_topics": []
    })
    await _get_cmd(bot, "trending").callback(interaction, "Finance", "24h", 5)
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "no trending" in msg.lower()


@pytest.mark.asyncio
async def test_trending_with_results():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_trending_topics = AsyncMock(return_value={
        "status": "ok",
        "trending_topics": [
            {
                "topic": "Bitcoin", "is_spike": True, "is_breakout": False,
                "trend_direction": "up", "sentiment": 0.5, "volume": 1000,
                "volume_change": "+20%", "sentiment_change": "+0.1", "category": "Finance",
            },
            {
                "topic": "Lakers", "is_spike": False, "is_breakout": True,
                "trend_direction": "up", "sentiment": -0.4, "volume": 500,
                "volume_change": "+5%", "sentiment_change": "-0.2", "category": "Sports",
            },
            {
                "topic": "Weather", "is_spike": False, "is_breakout": False,
                "trend_direction": "down", "sentiment": 0.0, "volume": 300,
                "volume_change": "-3%", "sentiment_change": "0", "category": "News",
            },
        ],
        "sources": ["NewsAPI"],
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "trending").callback(interaction, "Finance", "7d", 10)
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_trending_limit_clamped():
    """Limit is clamped to 1-20."""
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_trending_topics = AsyncMock(return_value={
        "status": "ok",
        "trending_topics": [{
            "topic": "X", "is_spike": False, "is_breakout": False,
            "trend_direction": "up", "sentiment": 0.1, "volume": 10,
            "volume_change": "+0%", "sentiment_change": "+0", "category": "General",
        }],
        "sources": [],
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "trending").callback(interaction, "", "30d", 0)
    _trend_skills_mock.get_trending_topics.assert_awaited_once()
    call_args = _trend_skills_mock.get_trending_topics.await_args.args
    assert call_args[2] == 1  # clamped from 0 -> 1


@pytest.mark.asyncio
async def test_trending_failure():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_trending_topics = AsyncMock(return_value={
        "status": "error", "message": "Service down"
    })
    await _get_cmd(bot, "trending").callback(interaction, "", "24h", 5)
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "❌" in msg


@pytest.mark.asyncio
async def test_trending_stable_direction():
    """Stable trend (not up/down) shows ➡️ arrow."""
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_trending_topics = AsyncMock(return_value={
        "status": "ok",
        "trending_topics": [{
            "topic": "Stable", "is_spike": False, "is_breakout": False,
            "trend_direction": "stable", "sentiment": 0.0, "volume": 100,
            "volume_change": "0%", "sentiment_change": "0", "category": "General",
        }],
        "sources": [],
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "trending").callback(interaction, "", "24h", 10)
    interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /trends (trajectory)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trends_invalid_timeframe():
    bot = _make_bot()
    interaction = _make_interaction()
    await _get_cmd(bot, "trends").callback(interaction, "Bitcoin", "", "bad")
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "invalid timeframe" in msg.lower()


@pytest.mark.asyncio
async def test_trends_success_trending():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_topic_trajectory = AsyncMock(return_value={
        "status": "ok",
        "is_trending": True, "is_spike": True, "is_breakout": False,
        "analysis": "Bitcoin is spiking", "current_volume": 5000,
        "volume_change": "+200%", "sentiment": 0.7, "sentiment_change": "+0.3",
        "trend_direction": "up", "velocity": 3.5, "z_score": 4.2,
        "chart": "###\n####\n#####", "category": "Finance",
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "trends").callback(interaction, "Bitcoin", "Finance", "24h")
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_trends_success_breakout():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_topic_trajectory = AsyncMock(return_value={
        "status": "ok",
        "is_trending": False, "is_spike": False, "is_breakout": True,
        "analysis": "New topic emerging", "current_volume": 200,
        "volume_change": "+50%", "sentiment": 0.2, "sentiment_change": "+0.0",
        "trend_direction": "up", "velocity": 1.5, "z_score": 2.1,
        "chart": "##\n###", "category": "News",
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "trends").callback(interaction, "NewTopic", "", "7d")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_trends_success_not_trending():
    """No trending/spike/breakout markers should still show embed."""
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_topic_trajectory = AsyncMock(return_value={
        "status": "ok",
        "is_trending": False, "is_spike": False, "is_breakout": False,
        "analysis": "Steady", "current_volume": 100,
        "volume_change": "0%", "sentiment": 0.0, "sentiment_change": "0",
        "trend_direction": "stable", "velocity": 0.9, "z_score": 0.1,
        "chart": "###", "category": "General",
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "trends").callback(interaction, "Stable", "", "30d")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_trends_high_velocity_indicator():
    """Velocity > 2.0 adds ⚡ indicator."""
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_topic_trajectory = AsyncMock(return_value={
        "status": "ok",
        "is_trending": True, "is_spike": False, "is_breakout": False,
        "analysis": "Fast mover", "current_volume": 3000,
        "volume_change": "+100%", "sentiment": 0.5, "sentiment_change": "+0.1",
        "trend_direction": "up", "velocity": 5.0, "z_score": 3.0,
        "chart": "####", "category": "Sports",
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "trends").callback(interaction, "Topic", "", "24h")
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_trends_failure():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.get_topic_trajectory = AsyncMock(return_value={
        "status": "error", "message": "Not tracked"
    })
    await _get_cmd(bot, "trends").callback(interaction, "Unknown", "", "24h")
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "❌" in msg


# ---------------------------------------------------------------------------
# /breaking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_breaking_no_news():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.detect_breaking_news = AsyncMock(return_value={
        "status": "ok", "breaking_news": []
    })
    await _get_cmd(bot, "breaking").callback(interaction, "News", 3.0)
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "no breaking news" in msg.lower()


@pytest.mark.asyncio
async def test_breaking_with_news():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.detect_breaking_news = AsyncMock(return_value={
        "status": "ok",
        "breaking_news": [
            {
                "topic": "Earthquake", "spike_multiplier": 5.2, "volume": 800,
                "sentiment": -0.8, "hours_ago": 2
            },
            {
                "topic": "Storm", "spike_multiplier": 3.1, "volume": 400,
                "sentiment": -0.5, "hours_ago": 1
            },
        ],
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "breaking").callback(interaction, "News", 3.0)
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_breaking_positive_sentiment():
    """Positive sentiment gets 🟢 emoji branch."""
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.detect_breaking_news = AsyncMock(return_value={
        "status": "ok",
        "breaking_news": [{
            "topic": "Good news", "spike_multiplier": 4.0, "volume": 300,
            "sentiment": 0.6, "hours_ago": 0
        }],
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "breaking").callback(interaction, "Sports", 2.0)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_breaking_failure():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.detect_breaking_news = AsyncMock(return_value={
        "status": "error", "message": "Timeout"
    })
    await _get_cmd(bot, "breaking").callback(interaction, "News", 3.0)
    interaction.followup.send.assert_awaited_once()
    msg = interaction.followup.send.await_args.args[0]
    assert "❌" in msg


# ---------------------------------------------------------------------------
# /tracked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tracked_empty():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.list_tracked_topics = AsyncMock(return_value={
        "status": "ok", "tracked_topics": []
    })
    await _get_cmd(bot, "tracked").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "no topics" in msg.lower()


@pytest.mark.asyncio
async def test_tracked_with_topics():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.list_tracked_topics = AsyncMock(return_value={
        "status": "ok",
        "tracked_topics": [
            {"topic": "Bitcoin", "category": "Finance", "enabled": True},
            {"topic": "Lakers", "category": "Sports", "enabled": False},
            {"topic": "Apple", "category": "Finance", "enabled": True},
        ],
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "tracked").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_tracked_with_overflow_per_category(monkeypatch):
    """More than 10 per category adds '... +N more' line."""
    bot = _make_bot()
    interaction = _make_interaction()
    topics = [
        {"topic": f"Topic{i}", "category": "General", "enabled": True}
        for i in range(15)
    ]
    _trend_skills_mock.list_tracked_topics = AsyncMock(return_value={
        "status": "ok", "tracked_topics": topics
    })
    with patch("discord_commands.trends.audit_log"):
        await _get_cmd(bot, "tracked").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "embed" in kwargs


@pytest.mark.asyncio
async def test_tracked_failure():
    bot = _make_bot()
    interaction = _make_interaction()
    _trend_skills_mock.list_tracked_topics = AsyncMock(return_value={
        "status": "error", "message": "DB error"
    })
    await _get_cmd(bot, "tracked").callback(interaction)
    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.await_args.args[0]
    assert "❌" in msg
