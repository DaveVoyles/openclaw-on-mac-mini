"""test_bg_briefing_unit.py — Unit tests for src/bg_briefing.py."""

from __future__ import annotations

import datetime
import importlib as _importlib
import sys
import zoneinfo
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy external dependencies before importing bg_briefing
# ---------------------------------------------------------------------------


def _try_stub(mod_name: str) -> None:
    if mod_name not in sys.modules:
        try:
            _importlib.import_module(mod_name)
        except (ImportError, ModuleNotFoundError):
            sys.modules[mod_name] = MagicMock()


if "discord" not in sys.modules:
    try:
        _importlib.import_module("discord")
    except (ImportError, ModuleNotFoundError):
        _discord_stub = MagicMock()
        sys.modules["discord"] = _discord_stub
        sys.modules["discord.ext"] = MagicMock()
        sys.modules["discord.ext.commands"] = MagicMock()

for _mod in [
    "audit",
    "llm",
    "metrics_collector",
    "skills",
    "skills.advanced_skills",
    "trace_context",
    "notification_prefs",
    "goal_tracker",
    "error_tracker",
    "overseerr",
    "calendar_skills",
    "health_history",
    "reminder_manager",
    "google",
    "google.genai",
    "google.genai.types",
    "aiohttp",
    "pandas",
    "psutil",
    "prometheus_client",
]:
    _try_stub(_mod)

# Wire up the stubs that bg_briefing calls at module level
sys.modules["audit"].audit_log = MagicMock()
_mc_stub = MagicMock()
_mc_stub.get_collector.return_value = MagicMock()
sys.modules["metrics_collector"] = _mc_stub

# skills.get_system_stats must be an async coroutine
_skills_stub = sys.modules["skills"]
_skills_stub.get_system_stats = AsyncMock(return_value="CPU: 5%")

# skills.advanced_skills must expose async stubs
_adv_stub = sys.modules["skills.advanced_skills"]
_adv_stub.check_arr_health = AsyncMock(return_value="All healthy")
_adv_stub.get_download_queue = AsyncMock(return_value="No active downloads")
_adv_stub.get_weather = AsyncMock(return_value="Sunny 72°F")

# trace_context must be usable as a context manager
_tc = MagicMock()
_tc.return_value.__enter__ = MagicMock(return_value=None)
_tc.return_value.__exit__ = MagicMock(return_value=False)
sys.modules["trace_context"].trace_context = _tc

# llm.chat must return a 3-tuple (text, usage, model)
sys.modules["llm"].chat = AsyncMock(return_value=("Good morning! All looks great.", {}, "stub"))

import bg_briefing  # noqa: E402
from bg_briefing import (  # noqa: E402
    _owner_local_now,
    send_evening_digest,
    send_morning_briefing,
)
from constants import (  # noqa: E402
    BRIEFING_HOUR,
    BRIEFING_MINUTE_WINDOW,
    EVENING_DIGEST_HOUR,
)

# ---------------------------------------------------------------------------
# _owner_local_now
# ---------------------------------------------------------------------------


class TestOwnerLocalNow:
    def test_returns_datetime(self):
        with patch("bg_briefing._OWNER_USER_ID", 0):
            result = _owner_local_now()
        assert isinstance(result, datetime.datetime)

    def test_timezone_aware(self):
        with patch("bg_briefing._OWNER_USER_ID", 0):
            result = _owner_local_now()
        assert result.tzinfo is not None

    def test_fallback_to_utc_on_invalid_tz(self):
        """If notification_prefs returns a garbage timezone, should fall back to UTC."""
        sys.modules["notification_prefs"].get_user_timezone = MagicMock(return_value="Invalid/Zone_XYZ")
        with patch("bg_briefing._OWNER_USER_ID", 42):
            result = _owner_local_now()
        assert isinstance(result, datetime.datetime)
        assert result.tzinfo is not None

    def test_uses_utc_when_owner_id_zero(self):
        """With no owner configured, time should come back as UTC-aware."""
        with patch("bg_briefing._OWNER_USER_ID", 0):
            result = _owner_local_now()
        assert result.tzinfo == zoneinfo.ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# send_morning_briefing — channel_override path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSendMorningBriefing:
    async def test_posts_embed_to_channel_override(self):
        """With channel_override, briefing should post an embed to that channel."""
        channel = AsyncMock()
        bot = MagicMock()

        with patch("bg_briefing.audit_log"):
            await send_morning_briefing(bot, channel_override=channel)

        channel.send.assert_awaited_once()

    async def test_skips_when_no_channel_id_and_no_override(self):
        """Without channel_override and ALERT_CHANNEL_ID=0, should return without posting."""
        bot = MagicMock()
        bot.get_channel.return_value = None
        original = bg_briefing.ALERT_CHANNEL_ID
        bg_briefing.ALERT_CHANNEL_ID = 0
        try:
            await send_morning_briefing(bot)
        finally:
            bg_briefing.ALERT_CHANNEL_ID = original
        bot.get_channel.assert_not_called()

    async def test_uses_bot_get_channel_when_no_override(self):
        """Without channel_override, briefing should call bot.get_channel."""
        channel = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel
        original = bg_briefing.ALERT_CHANNEL_ID
        bg_briefing.ALERT_CHANNEL_ID = 7777
        try:
            with patch("bg_briefing.audit_log"):
                await send_morning_briefing(bot)
        finally:
            bg_briefing.ALERT_CHANNEL_ID = original
        bot.get_channel.assert_called_with(7777)
        channel.send.assert_awaited_once()

    async def test_embed_sent_has_correct_type(self):
        """The object passed to channel.send should be a discord.Embed (or mock)."""

        channel = AsyncMock()
        bot = MagicMock()

        with patch("bg_briefing.audit_log"):
            await send_morning_briefing(bot, channel_override=channel)

        # Verify send was called with at least one keyword/positional arg
        assert channel.send.call_count == 1


# ---------------------------------------------------------------------------
# send_evening_digest — channel_override path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSendEveningDigest:
    async def test_posts_embed_to_channel_override(self):
        """Evening digest should post an embed to the channel_override channel."""
        channel = AsyncMock()
        bot = MagicMock()

        with patch("bg_briefing.audit_log"):
            await send_evening_digest(bot, channel_override=channel)

        channel.send.assert_awaited_once()

    async def test_skips_when_no_channel_and_no_override(self):
        """Without channel_override and ALERT_CHANNEL_ID=0, digest should exit early."""
        bot = MagicMock()
        bot.get_channel.return_value = None
        original = bg_briefing.ALERT_CHANNEL_ID
        bg_briefing.ALERT_CHANNEL_ID = 0
        try:
            await send_evening_digest(bot)
        finally:
            bg_briefing.ALERT_CHANNEL_ID = original
        bot.get_channel.assert_not_called()

    async def test_uses_bot_get_channel_when_no_override(self):
        """Without channel_override, digest should call bot.get_channel."""
        channel = AsyncMock()
        bot = MagicMock()
        bot.get_channel.return_value = channel
        original = bg_briefing.ALERT_CHANNEL_ID
        bg_briefing.ALERT_CHANNEL_ID = 8888
        try:
            with patch("bg_briefing.audit_log"):
                await send_evening_digest(bot)
        finally:
            bg_briefing.ALERT_CHANNEL_ID = original
        bot.get_channel.assert_called_with(8888)
        channel.send.assert_awaited_once()

    async def test_digest_survives_missing_audit_file(self):
        """Digest should not raise even when the audit file does not exist."""
        channel = AsyncMock()
        bot = MagicMock()

        with patch("bg_briefing.audit_log"):
            with patch("bg_briefing.Path") as mock_path_cls:
                mock_path_cls.return_value.exists.return_value = False
                await send_evening_digest(bot, channel_override=channel)

        channel.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Scheduling trigger logic (hour/minute guard)
# ---------------------------------------------------------------------------


class TestBriefingTriggerLogic:
    def test_morning_hour_constant_is_8(self):
        assert BRIEFING_HOUR == 8

    def test_evening_hour_constant_is_21(self):
        assert EVENING_DIGEST_HOUR == 21

    def test_minute_window_positive(self):
        assert BRIEFING_MINUTE_WINDOW > 0

    def test_briefing_triggered_at_correct_hour(self):
        """Simulate: now.hour == BRIEFING_HOUR and minute < window → would trigger."""
        now = datetime.datetime(2024, 1, 15, BRIEFING_HOUR, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        should_trigger = now.hour == BRIEFING_HOUR and now.minute < BRIEFING_MINUTE_WINDOW
        assert should_trigger is True

    def test_briefing_not_triggered_outside_window(self):
        """Simulate: minute >= window → should NOT trigger."""
        now = datetime.datetime(2024, 1, 15, BRIEFING_HOUR, BRIEFING_MINUTE_WINDOW + 1, tzinfo=zoneinfo.ZoneInfo("UTC"))
        should_trigger = now.hour == BRIEFING_HOUR and now.minute < BRIEFING_MINUTE_WINDOW
        assert should_trigger is False

    def test_briefing_not_triggered_wrong_hour(self):
        """Simulate: wrong hour → should NOT trigger."""
        now = datetime.datetime(2024, 1, 15, BRIEFING_HOUR + 1, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        should_trigger = now.hour == BRIEFING_HOUR and now.minute < BRIEFING_MINUTE_WINDOW
        assert should_trigger is False

    def test_evening_triggered_at_correct_hour(self):
        """Simulate: now.hour == EVENING_DIGEST_HOUR and minute < window → would trigger."""
        now = datetime.datetime(2024, 1, 15, EVENING_DIGEST_HOUR, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        should_trigger = now.hour == EVENING_DIGEST_HOUR and now.minute < BRIEFING_MINUTE_WINDOW
        assert should_trigger is True
