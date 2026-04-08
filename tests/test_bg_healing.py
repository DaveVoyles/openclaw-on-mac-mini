"""Tests for bg_healing.py — audit writer, cleanup, proactive scan, and self-healing."""

import asyncio
import json
import sys
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bg_healing

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


# ---------------------------------------------------------------------------
# audit_writer_loop
# ---------------------------------------------------------------------------


class TestAuditWriterLoop:
    @pytest.mark.asyncio
    async def test_loop_writes_entries_to_file(self, tmp_path, monkeypatch):
        """When buffer has entries and loop runs once, they are written to disk."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        monkeypatch.setattr(bg_healing, "AUDIT_DIR", audit_dir)
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)

        fake_buffer = deque([{"action": "test", "ts": "2025-01-01T00:00:00"}])
        monkeypatch.setattr(bg_healing, "_audit_buffer", fake_buffer)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.audit_writer_loop()

        import datetime
        today = datetime.date.today().isoformat()
        audit_file = audit_dir / f"{today}.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["action"] == "test"

    @pytest.mark.asyncio
    async def test_empty_buffer_skips_write(self, tmp_path, monkeypatch):
        """When buffer is empty, no file is written."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        monkeypatch.setattr(bg_healing, "AUDIT_DIR", audit_dir)
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)
        monkeypatch.setattr(bg_healing, "_audit_buffer", deque())

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.audit_writer_loop()

        # No file should exist
        import datetime
        today = datetime.date.today().isoformat()
        assert not (audit_dir / f"{today}.jsonl").exists()

    @pytest.mark.asyncio
    async def test_oserror_is_caught(self, tmp_path, monkeypatch):
        """OSError during file write is caught and does not propagate."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        monkeypatch.setattr(bg_healing, "AUDIT_DIR", audit_dir)
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)
        fake_buffer = deque([{"action": "x"}])
        monkeypatch.setattr(bg_healing, "_audit_buffer", fake_buffer)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep), \
             patch("builtins.open", side_effect=OSError("disk full")):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.audit_writer_loop()

    @pytest.mark.asyncio
    async def test_multiple_entries_written(self, tmp_path, monkeypatch):
        """Multiple buffered entries all get written."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        monkeypatch.setattr(bg_healing, "AUDIT_DIR", audit_dir)
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)
        fake_buffer = deque([{"action": "a"}, {"action": "b"}, {"action": "c"}])
        monkeypatch.setattr(bg_healing, "_audit_buffer", fake_buffer)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.audit_writer_loop()

        import datetime
        today = datetime.date.today().isoformat()
        lines = (audit_dir / f"{today}.jsonl").read_text().strip().splitlines()
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# background_cleanup_loop
# ---------------------------------------------------------------------------


class TestBackgroundCleanupLoop:
    @pytest.mark.asyncio
    async def test_cleanup_expired_called_on_both_stores(self, monkeypatch):
        """Both conversation_store and approval_store cleanup_expired are called."""
        monkeypatch.setattr(bg_healing, "CLEANUP_INTERVAL", 0)
        mock_conv = MagicMock()
        mock_appr = MagicMock()
        monkeypatch.setattr(bg_healing, "conversation_store", mock_conv)
        monkeypatch.setattr(bg_healing, "approval_store", mock_appr)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_healing, "get_collector", lambda: mock_collector)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.background_cleanup_loop()

        mock_conv.cleanup_expired.assert_called_once()
        mock_appr.cleanup_expired.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_exception_does_not_propagate(self, monkeypatch):
        """If cleanup_expired raises, the loop continues."""
        monkeypatch.setattr(bg_healing, "CLEANUP_INTERVAL", 0)
        mock_conv = MagicMock()
        mock_conv.cleanup_expired.side_effect = RuntimeError("db error")
        mock_appr = MagicMock()
        monkeypatch.setattr(bg_healing, "conversation_store", mock_conv)
        monkeypatch.setattr(bg_healing, "approval_store", mock_appr)
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_healing, "get_collector", lambda: mock_collector)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.background_cleanup_loop()

        # Should still have attempted cleanup
        mock_conv.cleanup_expired.assert_called_once()

    @pytest.mark.asyncio
    async def test_records_metrics(self, monkeypatch):
        """Metrics are recorded after cleanup."""
        monkeypatch.setattr(bg_healing, "CLEANUP_INTERVAL", 0)
        monkeypatch.setattr(bg_healing, "conversation_store", MagicMock())
        monkeypatch.setattr(bg_healing, "approval_store", MagicMock())
        mock_collector = MagicMock()
        monkeypatch.setattr(bg_healing, "get_collector", lambda: mock_collector)

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.background_cleanup_loop()

        mock_collector.record_command.assert_called_once()
        call_kwargs = mock_collector.record_command.call_args.kwargs
        assert call_kwargs["command"] == "background_cleanup"
        assert call_kwargs["success"] is True


# ---------------------------------------------------------------------------
# _check_quality_drift_alert
# ---------------------------------------------------------------------------


class TestCheckQualityDriftAlert:
    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 0)
        bot = _make_bot()
        result = await bg_healing._check_quality_drift_alert(bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_import_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        with patch.dict("sys.modules", {"dashboard": None, "dashboard.api_handlers": None}):
            result = await bg_healing._check_quality_drift_alert(_make_bot())
        assert result is False

    @pytest.mark.asyncio
    async def test_no_severe_drift_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        calibration = {
            "available": True,
            "drift": {
                "status": "ok",
                "severity": {"level": "minor", "severe": False, "score": 1},
                "regressed_metrics": [],
            },
        }
        mock_module = MagicMock()
        mock_module._build_offline_quality_calibration_payload.return_value = calibration
        with patch.dict("sys.modules", {"dashboard.api_handlers": mock_module}):
            result = await bg_healing._check_quality_drift_alert(_make_bot())
        assert result is False

    @pytest.mark.asyncio
    async def test_severe_drift_posts_embed(self, monkeypatch):
        from alert_manager import reset_bounded_alert_cache
        reset_bounded_alert_cache()

        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)

        calibration = {
            "available": True,
            "drift": {
                "baseline_available": True,
                "status": "drifted",
                "regressed_metrics": ["coverage_proxy"],
                "severity": {"level": "severe", "severe": True, "score": 5, "reasons": ["regression"]},
            },
        }
        mock_module = MagicMock()
        mock_module._build_offline_quality_calibration_payload.return_value = calibration

        with patch.dict("sys.modules", {"dashboard.api_handlers": mock_module}):
            result = await bg_healing._check_quality_drift_alert(bot)

        assert result is True
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_false(self, monkeypatch):
        from alert_manager import reset_bounded_alert_cache
        reset_bounded_alert_cache()

        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(channel=None)  # channel not found

        calibration = {
            "available": True,
            "drift": {
                "status": "drifted",
                "regressed_metrics": ["x"],
                "severity": {"level": "severe", "severe": True, "score": 5, "reasons": []},
            },
        }
        mock_module = MagicMock()
        mock_module._build_offline_quality_calibration_payload.return_value = calibration

        with patch.dict("sys.modules", {"dashboard.api_handlers": mock_module}):
            result = await bg_healing._check_quality_drift_alert(bot)

        assert result is False

    @pytest.mark.asyncio
    async def test_non_dict_calibration_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        mock_module = MagicMock()
        mock_module._build_offline_quality_calibration_payload.return_value = "not a dict"

        with patch.dict("sys.modules", {"dashboard.api_handlers": mock_module}):
            result = await bg_healing._check_quality_drift_alert(_make_bot())

        assert result is False


# ---------------------------------------------------------------------------
# _parse_heal_actions (additional tests beyond test_discord_background_core)
# ---------------------------------------------------------------------------


class TestParseHealActionsExtended:
    def test_restart_lidarr(self):
        actions = bg_healing._parse_heal_actions("SELF_HEAL: restart_container lidarr")
        assert actions == [("restart_container", "lidarr")]

    def test_restart_case_insensitive_target(self):
        actions = bg_healing._parse_heal_actions("SELF_HEAL: restart_container SONARR")
        assert actions == [("restart_container", "sonarr")]

    def test_multiple_mixed_actions(self):
        text = (
            "SELF_HEAL: restart_container radarr\n"
            "SELF_HEAL: auto_cleanup_disk\n"
            "SELF_HEAL: fix_qbit_download_path\n"
        )
        actions = bg_healing._parse_heal_actions(text)
        assert ("restart_container", "radarr") in actions
        assert ("auto_cleanup_disk", "") in actions
        assert ("fix_qbit_download_path", "") in actions

    def test_fix_arr_remote_path(self):
        actions = bg_healing._parse_heal_actions("SELF_HEAL: fix_arr_remote_path")
        assert actions == [("fix_arr_remote_path", "")]

    def test_unknown_directive_ignored(self):
        actions = bg_healing._parse_heal_actions("SELF_HEAL: unknown_action foobar")
        assert actions == []

    def test_whitespace_in_text_does_not_confuse_parser(self):
        actions = bg_healing._parse_heal_actions("  SELF_HEAL: restart_container sonarr  ")
        assert actions == [("restart_container", "sonarr")]

    def test_inline_text_not_matched(self):
        # SELF_HEAL must be at the start of the stripped line
        actions = bg_healing._parse_heal_actions("Note: SELF_HEAL: restart_container sonarr")
        assert actions == []


# ---------------------------------------------------------------------------
# _execute_self_healing
# ---------------------------------------------------------------------------


class TestExecuteSelfHealing:
    @pytest.mark.asyncio
    async def test_restart_container_action(self):
        with patch.object(bg_healing, "_parse_heal_actions", return_value=[("restart_container", "sonarr")]), \
             patch("bg_healing.restart_container", new_callable=AsyncMock, return_value="restarted OK") as mock_rc, \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing("SELF_HEAL: restart_container sonarr")
        mock_rc.assert_awaited_once_with("sonarr")
        assert any("sonarr" in r for r in results)

    @pytest.mark.asyncio
    async def test_no_actions_empty_results(self):
        cleaned, results = await bg_healing._execute_self_healing("all good")
        assert results == []

    @pytest.mark.asyncio
    async def test_fix_qbit_download_path(self):
        mock_fix = AsyncMock(return_value="fixed path")
        with patch.object(bg_healing, "_parse_heal_actions", return_value=[("fix_qbit_download_path", "")]), \
             patch("bg_healing.audit_log"), \
             patch.dict("sys.modules", {"maintenance_skills": MagicMock(fix_qbit_download_path=mock_fix)}):
            cleaned, results = await bg_healing._execute_self_healing("SELF_HEAL: fix_qbit_download_path")
        assert any("qBittorrent" in r for r in results)

    @pytest.mark.asyncio
    async def test_auto_cleanup_disk(self):
        mock_cleanup = AsyncMock(return_value="freed 10GB")
        mock_maintenance = MagicMock()
        mock_maintenance.auto_cleanup_disk = mock_cleanup
        with patch.object(bg_healing, "_parse_heal_actions", return_value=[("auto_cleanup_disk", "")]), \
             patch("bg_healing.audit_log"), \
             patch.dict("sys.modules", {"maintenance_skills": mock_maintenance}):
            cleaned, results = await bg_healing._execute_self_healing("SELF_HEAL: auto_cleanup_disk")
        assert any("Disk" in r or "cleanup" in r.lower() for r in results)

    @pytest.mark.asyncio
    async def test_copilot_fix_pending_no_execution(self):
        with patch.object(bg_healing, "_parse_heal_actions",
                          return_value=[("copilot_fix_pending", "check container logs")]), \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing(
                "SELF_HEAL: copilot_fix check container logs"
            )
        assert any("approval" in r.lower() or "Copilot" in r for r in results)

    @pytest.mark.asyncio
    async def test_restart_container_exception(self):
        with patch.object(bg_healing, "_parse_heal_actions", return_value=[("restart_container", "sonarr")]), \
             patch("bg_healing.restart_container", new_callable=AsyncMock, side_effect=Exception("timeout")), \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing("SELF_HEAL: restart_container sonarr")
        assert any("❌" in r for r in results)

    @pytest.mark.asyncio
    async def test_self_heal_lines_removed_from_display(self):
        analysis = "System is broken\nSELF_HEAL: auto_cleanup_disk\nMore details"
        with patch("bg_healing.audit_log"):
            cleaned, _ = await bg_healing._execute_self_healing(analysis)
        assert "SELF_HEAL:" not in cleaned


# ---------------------------------------------------------------------------
# _run_proactive_scan
# ---------------------------------------------------------------------------


class TestRunProactiveScan:
    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 0)
        bot = _make_bot()
        # Should return without calling gather signals
        with patch.object(bg_healing, "_gather_system_signals", new_callable=AsyncMock) as mock_gs:
            await bg_healing._run_proactive_scan(bot)
        mock_gs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_clear_signals_no_post(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(_make_channel())

        with patch.object(bg_healing, "_gather_system_signals", new_callable=AsyncMock, return_value=None):
            await bg_healing._run_proactive_scan(bot)

        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_alert_llm_response_no_post(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)

        with patch.object(bg_healing, "_gather_system_signals",
                          new_callable=AsyncMock, return_value=("some signals", {})), \
             patch("bg_healing.llm_chat", new_callable=AsyncMock, return_value=("NO_ALERT", [], "model")):
            await bg_healing._run_proactive_scan(bot)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_actionable_response_posts_embed(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        channel = _make_channel()
        bot = _make_bot(channel)
        msg = AsyncMock()
        msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=msg)

        with patch.object(bg_healing, "_gather_system_signals",
                          new_callable=AsyncMock, return_value=("disk 95% full", {})), \
             patch("bg_healing.llm_chat",
                   new_callable=AsyncMock, return_value=("sonarr is failing\nSELF_HEAL: auto_cleanup_disk", [], "m")), \
             patch.object(bg_healing, "_execute_self_healing",
                          new_callable=AsyncMock, return_value=("sonarr is failing", ["🧹 Disk cleanup: done"])), \
             patch("bg_healing.audit_log"):
            await bg_healing._run_proactive_scan(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_timeout_handled(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(_make_channel())

        with patch.object(bg_healing, "_gather_system_signals",
                          new_callable=AsyncMock, return_value=("signals", {})), \
             patch("bg_healing.llm_chat", new_callable=AsyncMock,
                   side_effect=asyncio.TimeoutError()):
            # Should not raise
            await bg_healing._run_proactive_scan(bot)

    @pytest.mark.asyncio
    async def test_channel_not_found_after_llm(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot(channel=None)  # channel lookup fails

        with patch.object(bg_healing, "_gather_system_signals",
                          new_callable=AsyncMock, return_value=("signals", {})), \
             patch("bg_healing.llm_chat",
                   new_callable=AsyncMock, return_value=("sonarr failing", [], "m")), \
             patch.object(bg_healing, "_execute_self_healing",
                          new_callable=AsyncMock, return_value=("sonarr failing", [])):
            await bg_healing._run_proactive_scan(bot)

        # No error should propagate


# ---------------------------------------------------------------------------
# proactive_insight_loop
# ---------------------------------------------------------------------------


class TestProactiveInsightLoop:
    @pytest.mark.asyncio
    async def test_loop_runs_checks_then_sleeps(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "PROACTIVE_SCAN_INTERVAL", 0)
        check_drift_calls = 0
        scan_calls = 0

        async def mock_check_drift(bot):
            nonlocal check_drift_calls
            check_drift_calls += 1
            return False

        async def mock_scan(bot):
            nonlocal scan_calls
            scan_calls += 1

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        bot = _make_bot()
        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep), \
             patch.object(bg_healing, "_check_quality_drift_alert", side_effect=mock_check_drift), \
             patch.object(bg_healing, "_run_proactive_scan", side_effect=mock_scan):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.proactive_insight_loop(bot)

        assert check_drift_calls >= 1
        assert scan_calls >= 1

    @pytest.mark.asyncio
    async def test_inner_exception_does_not_stop_loop(self, monkeypatch):
        """If scan raises, the loop continues."""
        monkeypatch.setattr(bg_healing, "PROACTIVE_SCAN_INTERVAL", 0)

        async def boom(bot):
            raise RuntimeError("scan failed")

        sleep_count = 0

        async def mock_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError()

        bot = _make_bot()
        with patch("bg_healing.asyncio.sleep", side_effect=mock_sleep), \
             patch.object(bg_healing, "_check_quality_drift_alert", new_callable=AsyncMock, return_value=False), \
             patch.object(bg_healing, "_run_proactive_scan", side_effect=boom):
            with pytest.raises(asyncio.CancelledError):
                await bg_healing.proactive_insight_loop(bot)

        assert sleep_count >= 3
