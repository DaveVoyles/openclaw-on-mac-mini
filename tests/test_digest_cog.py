"""Tests for DigestCog commands."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_cogs.db")

import pytest

# Patch require_auth before import so @require_auth (without parens) works
import cog_helpers as _ch

_orig_require_auth = _ch.require_auth


def _noop_auth(*args, **kwargs):
    """Accepts both @require_auth and @require_auth() usage."""
    if args and callable(args[0]):
        return args[0]
    return lambda f: f


_ch.require_auth = _noop_auth

import cogs.digest_cog as mod

# Restore after import (other tests may need real auth)
_ch.require_auth = _orig_require_auth


# ── Fixtures ─────────────────────────────────────────────────────────────────

class _FakeTree:
    def add_command(self, *a, **k):
        pass
    def remove_command(self, *a, **k):
        pass


class _FakeBot:
    def __init__(self):
        self.tree = _FakeTree()


def _make_bot():
    return _FakeBot()


def _make_interaction(user_id=1, done=False):
    inter = AsyncMock()
    inter.user.id = user_id
    inter.user.display_name = "TestUser"
    inter.user.__str__ = lambda self: "TestUser#0001"
    inter.channel_id = 100
    inter.guild_id = 999
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.is_done = MagicMock(return_value=done)
    inter.followup.send = AsyncMock()
    return inter


def _make_cog():
    return mod.DigestCog(_make_bot())


# ── cog_command_error ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cog_command_error_check_failure_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.CheckFailure("Not authorized")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    assert "Not authorized" in inter.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_cog_command_error_check_failure_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=True)
    err = app_commands.CheckFailure("Blocked")
    await cog.cog_command_error(inter, err)
    inter.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cog_command_error_generic_error_not_done():
    from discord import app_commands
    cog = _make_cog()
    inter = _make_interaction(done=False)
    err = app_commands.AppCommandError("something broke")
    await cog.cog_command_error(inter, err)
    inter.response.send_message.assert_awaited_once()
    msg = inter.response.send_message.call_args[0][0]
    assert "Command failed" in msg


# ── digest_now ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_now_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.generate_digest = AsyncMock(return_value="Today's digest content")

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager), \
         patch("runtime_state.set_current_user_id"):
        await cog.digest_now.callback(cog, inter)

    inter.followup.send.assert_awaited()
    embed = inter.followup.send.call_args.kwargs.get("embed") or inter.followup.send.call_args[1].get("embed")
    assert "Digest" in embed.title


@pytest.mark.asyncio
async def test_digest_now_multiple_chunks():
    cog = _make_cog()
    inter = _make_interaction()

    # Return a very long string that split_response would break into multiple chunks
    long_content = "a" * 8000
    mock_manager = MagicMock()
    mock_manager.generate_digest = AsyncMock(return_value=long_content)

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager), \
         patch("runtime_state.set_current_user_id"):
        await cog.digest_now.callback(cog, inter)

    assert inter.followup.send.await_count >= 2


@pytest.mark.asyncio
async def test_digest_now_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("service down")), \
         patch("runtime_state.set_current_user_id"):
        await cog.digest_now.callback(cog, inter)

    assert "Failed to generate" in inter.followup.send.call_args[0][0]


# ── digest_preview ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_preview_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.generate_digest = AsyncMock(return_value="Preview content here")

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager), \
         patch("runtime_state.set_current_user_id"):
        await cog.digest_preview.callback(cog, inter)

    inter.followup.send.assert_awaited()
    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "Preview" in embed.title


@pytest.mark.asyncio
async def test_digest_preview_multiple_chunks():
    cog = _make_cog()
    inter = _make_interaction()
    long_content = "b" * 8000
    mock_manager = MagicMock()
    mock_manager.generate_digest = AsyncMock(return_value=long_content)

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager), \
         patch("runtime_state.set_current_user_id"):
        await cog.digest_preview.callback(cog, inter)

    assert inter.followup.send.await_count >= 2


@pytest.mark.asyncio
async def test_digest_preview_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("oops")), \
         patch("runtime_state.set_current_user_id"):
        await cog.digest_preview.callback(cog, inter)

    assert "Failed to preview" in inter.followup.send.call_args[0][0]


# ── digest_config ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_config_full_prefs():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = {
        "topics": ["AI", "space"] + [f"topic{i}" for i in range(12)],
        "stocks": ["TSLA", "NVDA"] + [f"STK{i}" for i in range(12)],
        "teams": ["Lakers", "Patriots"] + [f"team{i}" for i in range(12)],
        "keywords": ["machine learning", "robotics"],
        "exclude": ["sports", "celebrity"],
        "schedule": "weekly",
        "delivery_time": "09:00",
        "timezone": "US/Eastern",
        "delivery_day": "Friday",
        "format": "detailed",
        "max_items": 20,
        "enabled": True,
    }
    mock_manager = MagicMock()
    mock_manager.get_preferences = MagicMock(return_value=prefs)

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_config.callback(cog, inter)

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "Digest Configuration" in embed.title
    desc = embed.description
    assert "AI" in desc
    assert "TSLA" in desc
    assert "Lakers" in desc
    assert "machine learning" in desc
    assert "sports" in desc
    assert "weekly" in desc
    assert "Friday" in desc
    assert "detailed" in desc
    assert "Enabled" in desc


@pytest.mark.asyncio
async def test_digest_config_empty_prefs():
    cog = _make_cog()
    inter = _make_interaction()

    prefs = {"enabled": False, "schedule": "daily", "delivery_time": "08:00", "timezone": "UTC", "format": "concise", "max_items": 10}
    mock_manager = MagicMock()
    mock_manager.get_preferences = MagicMock(return_value=prefs)

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_config.callback(cog, inter)

    embed = inter.followup.send.call_args.kwargs.get("embed")
    assert "None configured" in embed.description
    assert "Disabled" in embed.description


@pytest.mark.asyncio
async def test_digest_config_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("unavailable")):
        await cog.digest_config.callback(cog, inter)

    assert "Failed to get configuration" in inter.followup.send.call_args[0][0]


# ── digest_topic ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_topic_add():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.add_to_list = MagicMock()
    mock_manager.get_preferences = MagicMock(return_value={"topics": ["AI", "Space"]})

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_topic.callback(cog, inter, action="add", topic="AI")

    assert "Added topic" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_topic_remove():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.remove_from_list = MagicMock()

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_topic.callback(cog, inter, action="remove", topic="Sports")

    assert "Removed topic" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_topic_invalid_action():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()

    with patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_topic.callback(cog, inter, action="update", topic="AI")

    assert "Invalid action" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_topic_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("db error")):
        await cog.digest_topic.callback(cog, inter, action="add", topic="AI")

    assert "Failed to manage topic" in inter.followup.send.call_args[0][0]


# ── digest_stock ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_stock_add():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.add_to_list = MagicMock()
    mock_manager.get_preferences = MagicMock(return_value={"stocks": ["TSLA"]})

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_stock.callback(cog, inter, action="add", ticker="tsla")

    assert "Added stock: **TSLA**" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_stock_remove():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.remove_from_list = MagicMock()

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_stock.callback(cog, inter, action="remove", ticker="NVDA")

    assert "Removed stock: **NVDA**" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_stock_invalid_action():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()

    with patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_stock.callback(cog, inter, action="toggle", ticker="TSLA")

    assert "Invalid action" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_stock_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("db error")):
        await cog.digest_stock.callback(cog, inter, action="add", ticker="TSLA")

    assert "Failed to manage stock" in inter.followup.send.call_args[0][0]


# ── digest_team ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_team_add():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.add_to_list = MagicMock()
    mock_manager.get_preferences = MagicMock(return_value={"teams": ["Lakers", "Celtics"]})

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_team.callback(cog, inter, action="add", team="Lakers")

    assert "Added team" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_team_remove():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.remove_from_list = MagicMock()

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_team.callback(cog, inter, action="remove", team="Patriots")

    assert "Removed team" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_team_invalid_action():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()

    with patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_team.callback(cog, inter, action="view", team="Lakers")

    assert "Invalid action" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_team_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("network")):
        await cog.digest_team.callback(cog, inter, action="add", team="Celtics")

    assert "Failed to manage team" in inter.followup.send.call_args[0][0]


# ── digest_schedule ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_schedule_daily():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.update_preference = MagicMock()

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_schedule.callback(cog, inter, frequency="daily", time="07:00", day="Monday")

    msg = inter.followup.send.call_args[0][0]
    assert "schedule updated" in msg
    assert "daily" in msg


@pytest.mark.asyncio
async def test_digest_schedule_weekly():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.update_preference = MagicMock()

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_schedule.callback(cog, inter, frequency="weekly", time="09:00", day="Friday")

    msg = inter.followup.send.call_args[0][0]
    assert "Friday" in msg


@pytest.mark.asyncio
async def test_digest_schedule_invalid_frequency():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()

    with patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_schedule.callback(cog, inter, frequency="hourly", time="08:00", day="Monday")

    assert "Invalid frequency" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_schedule_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("db down")):
        await cog.digest_schedule.callback(cog, inter, frequency="daily", time="08:00", day="Monday")

    assert "Failed to set schedule" in inter.followup.send.call_args[0][0]


# ── digest_enable / digest_disable ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_enable_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.update_preference = MagicMock()

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_enable.callback(cog, inter)

    assert "enabled" in inter.followup.send.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_digest_enable_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("unavailable")):
        await cog.digest_enable.callback(cog, inter)

    assert "Failed to enable" in inter.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_digest_disable_success():
    cog = _make_cog()
    inter = _make_interaction()

    mock_manager = MagicMock()
    mock_manager.update_preference = MagicMock()

    with patch("cogs.digest_cog.audit_log"), \
         patch("digest_manager.get_digest_manager", return_value=mock_manager):
        await cog.digest_disable.callback(cog, inter)

    msg = inter.followup.send.call_args[0][0]
    assert "disabled" in msg.lower()


@pytest.mark.asyncio
async def test_digest_disable_error():
    cog = _make_cog()
    inter = _make_interaction()

    with patch("digest_manager.get_digest_manager", side_effect=Exception("unavailable")):
        await cog.digest_disable.callback(cog, inter)

    assert "Failed to disable" in inter.followup.send.call_args[0][0]
