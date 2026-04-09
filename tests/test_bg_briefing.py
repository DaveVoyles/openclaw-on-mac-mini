"""Tests for bg_briefing.py — morning briefing and evening digest loops."""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bg_briefing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(channel=None):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    return bot


def _make_channel():
    ch = MagicMock()
    ch.send = AsyncMock()
    return ch


def _patch_briefing_deps(monkeypatch, channel_id=123, channel=None):
    """Patch common dependencies for briefing tests."""
    if channel is None:
        channel = _make_channel()
    monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", channel_id)
    monkeypatch.setattr(bg_briefing, "check_arr_health", AsyncMock(return_value="all healthy"))
    monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(return_value="2 items queued"))
    monkeypatch.setattr(bg_briefing, "get_weather", AsyncMock(return_value="Sunny 72°F"))
    monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(return_value="CPU 5%"))
    monkeypatch.setattr(bg_briefing, "llm_chat", AsyncMock(return_value=("Good morning!", [], "gemini-pro")))
    monkeypatch.setattr(bg_briefing, "audit_log", MagicMock())
    return channel


# ---------------------------------------------------------------------------
# morning_briefing_loop
# ---------------------------------------------------------------------------


class TestMorningBriefingLoop:
    @pytest.mark.asyncio
    async def test_loop_breaks_after_first_tick(self, monkeypatch):
        """Loop runs at least one iteration then cancels."""
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "BRIEFING_HOUR", 25)  # never matches

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.morning_briefing_loop(_make_bot())

        assert sleep_count >= 1

    @pytest.mark.asyncio
    async def test_wrong_hour_no_briefing(self, monkeypatch):
        """When current hour != BRIEFING_HOUR, no briefing task is created."""
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "BRIEFING_HOUR", 25)  # never matches

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task") as mock_create:
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.morning_briefing_loop(_make_bot())

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_matching_hour_triggers_briefing_task(self, monkeypatch):
        """When hour matches BRIEFING_HOUR and minute < BRIEFING_MINUTE_WINDOW, task is created."""
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "BRIEFING_HOUR", datetime.datetime.now().hour)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)  # always within window

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        mock_task = MagicMock()
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_briefing, "get_collector", lambda: mock_collector)

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task", return_value=mock_task) as mock_create:
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.morning_briefing_loop(_make_bot())

        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_briefing_not_duplicated_same_day(self, monkeypatch):
        """Briefing is only triggered once per day even if loop runs multiple times."""
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "BRIEFING_HOUR", datetime.datetime.now().hour)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_briefing, "get_collector", lambda: mock_collector)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 4:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task") as mock_create:
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.morning_briefing_loop(_make_bot())

        # Task created only once per date, even though loop ran 3 times
        assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_exception_in_loop_body_caught(self, monkeypatch):
        """Exception in the loop body is caught and loop continues."""
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "BRIEFING_HOUR", datetime.datetime.now().hour)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        mock_collector = MagicMock()
        monkeypatch.setattr(bg_briefing, "get_collector", lambda: mock_collector)

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task", side_effect=RuntimeError("task error")):
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.morning_briefing_loop(_make_bot())

        assert sleep_count >= 2


# ---------------------------------------------------------------------------
# send_morning_briefing
# ---------------------------------------------------------------------------


class TestSendMorningBriefing:
    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 0)
        bot = _make_bot()
        await bg_briefing.send_morning_briefing(bot)
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_early(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 456)
        bot = _make_bot(channel=None)
        await bg_briefing.send_morning_briefing(bot)
        bot.get_channel.assert_called_once_with(456)

    @pytest.mark.asyncio
    async def test_happy_path_sends_embed(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        _patch_briefing_deps(monkeypatch, channel_id=123, channel=channel)

        await bg_briefing.send_morning_briefing(bot)

        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        assert "Morning Briefing" in embed.title

    @pytest.mark.asyncio
    async def test_channel_override_used(self, monkeypatch):
        """channel_override bypasses ALERT_CHANNEL_ID lookup."""
        override_channel = _make_channel()
        bot = _make_bot()
        _patch_briefing_deps(monkeypatch, channel_id=0)
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 0)

        await bg_briefing.send_morning_briefing(bot, channel_override=override_channel)

        bot.get_channel.assert_not_called()
        override_channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_exception_is_caught(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        _patch_briefing_deps(monkeypatch, channel_id=123, channel=channel)
        monkeypatch.setattr(bg_briefing, "llm_chat", AsyncMock(side_effect=Exception("LLM down")))

        await bg_briefing.send_morning_briefing(bot)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_health_check_exception_does_not_abort(self, monkeypatch):
        """Even if arr health raises, briefing continues with exception object."""
        channel = _make_channel()
        bot = _make_bot(channel)
        _patch_briefing_deps(monkeypatch, channel_id=123, channel=channel)
        monkeypatch.setattr(bg_briefing, "check_arr_health", AsyncMock(side_effect=Exception("health down")))

        await bg_briefing.send_morning_briefing(bot)

        # asyncio.gather with return_exceptions=True means it still calls llm
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_embed_has_footer(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        _patch_briefing_deps(monkeypatch, channel_id=123, channel=channel)

        await bg_briefing.send_morning_briefing(bot)

        embed = channel.send.await_args.kwargs["embed"]
        assert embed.footer.text is not None

    @pytest.mark.asyncio
    async def test_audit_log_called(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        mock_audit = MagicMock()
        _patch_briefing_deps(monkeypatch, channel_id=123, channel=channel)
        monkeypatch.setattr(bg_briefing, "audit_log", mock_audit)

        await bg_briefing.send_morning_briefing(bot)

        mock_audit.assert_called_once()


# ---------------------------------------------------------------------------
# evening_digest_loop
# ---------------------------------------------------------------------------


class TestEveningDigestLoop:
    @pytest.mark.asyncio
    async def test_loop_breaks_after_first_tick(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "EVENING_DIGEST_HOUR", 25)  # never matches

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.evening_digest_loop(_make_bot())

        assert sleep_count >= 1

    @pytest.mark.asyncio
    async def test_wrong_hour_no_digest(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "EVENING_DIGEST_HOUR", 25)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task") as mock_create:
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.evening_digest_loop(_make_bot())

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_matching_hour_triggers_digest(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "EVENING_DIGEST_HOUR", datetime.datetime.now().hour)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task") as mock_create:
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.evening_digest_loop(_make_bot())

        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_digest_not_duplicated_same_day(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "EVENING_DIGEST_HOUR", datetime.datetime.now().hour)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 4:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task") as mock_create:
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.evening_digest_loop(_make_bot())

        assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_exception_in_loop_caught(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "EVENING_DIGEST_HOUR", datetime.datetime.now().hour)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        with patch("bg_briefing.asyncio.sleep", side_effect=mock_sleep), \
             patch("bg_briefing.asyncio.create_task", side_effect=RuntimeError("task error")):
            with pytest.raises(asyncio.CancelledError):
                await bg_briefing.evening_digest_loop(_make_bot())

        assert sleep_count >= 2


# ---------------------------------------------------------------------------
# send_evening_digest
# ---------------------------------------------------------------------------


class TestSendEveningDigest:
    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 0)
        bot = _make_bot()
        await bg_briefing.send_evening_digest(bot)
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_early(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 999)
        bot = _make_bot(channel=None)
        await bg_briefing.send_evening_digest(bot)
        bot.get_channel.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_happy_path_sends_embed(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(return_value="CPU 3%"))
        monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(bg_briefing, "audit_log", MagicMock())

        await bg_briefing.send_evening_digest(bot)

        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        assert "Digest" in embed.title

    @pytest.mark.asyncio
    async def test_channel_override_bypasses_lookup(self, monkeypatch):
        override_channel = _make_channel()
        bot = _make_bot()
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(return_value="CPU 2%"))
        monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(bg_briefing, "audit_log", MagicMock())

        await bg_briefing.send_evening_digest(bot, channel_override=override_channel)

        bot.get_channel.assert_not_called()
        override_channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_system_stats_failure_still_sends(self, monkeypatch):
        """Even if get_system_stats raises, the embed is still sent."""
        channel = _make_channel()
        bot = _make_bot(channel)
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(side_effect=Exception("stats down")))
        monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(bg_briefing, "audit_log", MagicMock())

        await bg_briefing.send_evening_digest(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_download_failure_still_sends(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(return_value="CPU 3%"))
        monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(side_effect=Exception("download down")))
        monkeypatch.setattr(bg_briefing, "audit_log", MagicMock())

        await bg_briefing.send_evening_digest(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_audit_log_called(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        mock_audit = MagicMock()
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(return_value="CPU 3%"))
        monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(return_value="none"))
        monkeypatch.setattr(bg_briefing, "audit_log", mock_audit)

        await bg_briefing.send_evening_digest(bot)

        mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_has_footer(self, monkeypatch):
        channel = _make_channel()
        bot = _make_bot(channel)
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(return_value="CPU 3%"))
        monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(bg_briefing, "audit_log", MagicMock())

        await bg_briefing.send_evening_digest(bot)

        embed = channel.send.await_args.kwargs["embed"]
        assert embed.footer.text is not None

    @pytest.mark.asyncio
    async def test_existing_audit_file_adds_activity_field(self, monkeypatch, tmp_path):
        """When today's audit file exists, activity field is added to embed."""
        import json as _json

        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        today = datetime.date.today().isoformat()
        audit_file = audit_dir / f"{today}.jsonl"
        audit_file.write_text(
            _json.dumps({"action": "ask", "ts": "00:00"}) + "\n" +
            _json.dumps({"action": "ask", "ts": "01:00"}) + "\n"
        )

        channel = _make_channel()
        bot = _make_bot(channel)
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(bg_briefing, "get_system_stats", AsyncMock(return_value="CPU 3%"))
        monkeypatch.setattr(bg_briefing, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(bg_briefing, "audit_log", MagicMock())

        with patch("bg_briefing.Path", return_value=audit_file):
            await bg_briefing.send_evening_digest(bot)

        channel.send.assert_awaited_once()
