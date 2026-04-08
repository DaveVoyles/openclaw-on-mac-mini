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


# ---------------------------------------------------------------------------
# Additional coverage for missing lines
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Additional coverage for missing lines
# ---------------------------------------------------------------------------


class TestAuditWriterLoopIndexError:
    """Cover the IndexError branch when popleft() races with concurrent dequeue."""

    @pytest.mark.asyncio
    async def test_index_error_on_popleft_is_caught(self, tmp_path, monkeypatch):
        """IndexError from popleft() during drain is handled gracefully."""
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        monkeypatch.setattr(bg_healing, "AUDIT_DIR", audit_dir)
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)

        class FlakyDeque(deque):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self._calls = 0

            def popleft(self):
                self._calls += 1
                if self._calls == 1:
                    raise IndexError("race")
                return super().popleft()

        fake_buffer = FlakyDeque([{"action": "x"}])
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
        # No exception propagated — test passes


class TestCheckQualityDriftAlertExtended:
    """Cover extra branches in _check_quality_drift_alert."""

    @pytest.mark.asyncio
    async def test_non_dict_drift_returns_false(self, monkeypatch):
        """Calibration with non-dict drift key returns False early (line 148)."""
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 99)

        import types
        fake_mod = types.ModuleType("dashboard.api_handlers")
        fake_mod._build_offline_quality_calibration_payload = lambda: {"drift": "string_not_dict"}
        with patch.dict("sys.modules", {"dashboard.api_handlers": fake_mod}):
            bot = _make_bot()
            result = await bg_healing._check_quality_drift_alert(bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_non_dict_severity_coerced_to_empty(self, monkeypatch):
        """When severity field is not a dict, treated as empty → no-severe branch (line 151)."""
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 99)

        import types
        fake_mod = types.ModuleType("dashboard.api_handlers")
        fake_mod._build_offline_quality_calibration_payload = lambda: {
            "drift": {"severity": "not_a_dict", "regressed_metrics": []}
        }
        with patch.dict("sys.modules", {"dashboard.api_handlers": fake_mod}):
            bot = _make_bot()
            result = await bg_healing._check_quality_drift_alert(bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_alert_skipped_when_routing_not_allowed(self, monkeypatch):
        """should_route_bounded_alert returning False prevents embed (lines 171-172)."""
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 99)

        import types
        fake_mod = types.ModuleType("dashboard.api_handlers")
        fake_mod._build_offline_quality_calibration_payload = lambda: {
            "drift": {
                "severity": {"severe": True, "level": "high", "score": 5, "reasons": ["x"]},
                "status": "degraded",
                "regressed_metrics": ["precision"],
            }
        }
        with patch.dict("sys.modules", {"dashboard.api_handlers": fake_mod}), \
             patch("bg_healing.should_route_bounded_alert", return_value=(False, "cooldown")):
            bot = _make_bot()
            result = await bg_healing._check_quality_drift_alert(bot)
        assert result is False


class TestGatherSystemSignals:
    """Cover _gather_system_signals comprehensively."""

    def _make_maint_hh(self, nas_result="", vpn_result="up"):
        import types
        fake_maint = types.ModuleType("maintenance_skills")
        fake_maint.check_nas_health = AsyncMock(return_value=nas_result)
        fake_maint.check_gluetun_vpn = AsyncMock(return_value=vpn_result)
        fake_hh = types.ModuleType("health_history")
        fake_hh.record = MagicMock()
        fake_hh.record_disk = MagicMock()
        return fake_maint, fake_hh

    @pytest.mark.asyncio
    async def test_returns_none_when_all_clean(self):
        """Returns None when all services healthy and no log anomalies (line 279)."""
        fake_maint, fake_hh = self._make_maint_hh()
        with patch("bg_healing.check_arr_health", new=AsyncMock(return_value="OK: arr")), \
             patch("bg_healing.check_download_clients", new=AsyncMock(return_value="OK: dl")), \
             patch("bg_healing.check_plex_status", new=AsyncMock(return_value="OK: plex")), \
             patch("bg_healing.get_system_stats", new=AsyncMock(return_value="CPU: 10%")), \
             patch("bg_healing.get_container_logs", new=AsyncMock(return_value="normal log output")), \
             patch.dict("sys.modules", {"maintenance_skills": fake_maint, "health_history": fake_hh}):
            result = await bg_healing._gather_system_signals()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_summary_when_errors_found(self):
        """Returns (summary, log_snippets) when error patterns detected (lines 281-293)."""
        fake_maint, fake_hh = self._make_maint_hh()
        with patch("bg_healing.check_arr_health", new=AsyncMock(return_value="ERROR: sonarr down")), \
             patch("bg_healing.check_download_clients", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", new=AsyncMock(return_value="CPU: 10%")), \
             patch("bg_healing.get_container_logs", new=AsyncMock(return_value="normal")), \
             patch.dict("sys.modules", {"maintenance_skills": fake_maint, "health_history": fake_hh}):
            result = await bg_healing._gather_system_signals()
        assert result is not None
        summary, snippets = result
        assert "Health checks" in summary

    @pytest.mark.asyncio
    async def test_disk_alert_triggers_on_high_usage(self):
        """sys_stats with >90% disk usage triggers disk_alert (lines 261-268)."""
        fake_maint, fake_hh = self._make_maint_hh()
        with patch("bg_healing.check_arr_health", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_download_clients", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", new=AsyncMock(return_value="Disk /dev/sda1 (95%)")), \
             patch("bg_healing.get_container_logs", new=AsyncMock(return_value="")), \
             patch.dict("sys.modules", {"maintenance_skills": fake_maint, "health_history": fake_hh}):
            result = await bg_healing._gather_system_signals()
        assert result is not None

    @pytest.mark.asyncio
    async def test_nas_red_disk_triggers_alert(self):
        """NAS health with 🔴 triggers disk_alert path (line 270-271)."""
        fake_maint, fake_hh = self._make_maint_hh(nas_result="🔴 RAID degraded")
        with patch("bg_healing.check_arr_health", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_download_clients", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", new=AsyncMock(return_value="CPU: 5%")), \
             patch("bg_healing.get_container_logs", new=AsyncMock(return_value="")), \
             patch.dict("sys.modules", {"maintenance_skills": fake_maint, "health_history": fake_hh}):
            result = await bg_healing._gather_system_signals()
        assert result is not None
        summary, _ = result
        assert "NAS" in summary

    @pytest.mark.asyncio
    async def test_log_snippets_included_when_error_in_logs(self):
        """Container logs with errors are included in the summary (lines 288-291)."""
        fake_maint, fake_hh = self._make_maint_hh()
        with patch("bg_healing.check_arr_health", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_download_clients", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", new=AsyncMock(return_value="CPU: 5%")), \
             patch("bg_healing.get_container_logs", new=AsyncMock(return_value="ERROR: connection refused")), \
             patch.dict("sys.modules", {"maintenance_skills": fake_maint, "health_history": fake_hh}):
            result = await bg_healing._gather_system_signals()
        assert result is not None
        summary, snippets = result
        assert len(snippets) > 0
        assert "Log anomalies" in summary

    @pytest.mark.asyncio
    async def test_health_history_exception_is_swallowed(self):
        """If health_history.record raises, the exception is caught (lines 313-315)."""
        import types
        fake_maint, _ = self._make_maint_hh()
        fake_hh = types.ModuleType("health_history")
        fake_hh.record = MagicMock(side_effect=RuntimeError("db fail"))
        fake_hh.record_disk = MagicMock()
        with patch("bg_healing.check_arr_health", new=AsyncMock(return_value="ERROR: fail")), \
             patch("bg_healing.check_download_clients", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", new=AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", new=AsyncMock(return_value="CPU: 5%")), \
             patch("bg_healing.get_container_logs", new=AsyncMock(return_value="")), \
             patch.dict("sys.modules", {"maintenance_skills": fake_maint, "health_history": fake_hh}):
            result = await bg_healing._gather_system_signals()
        assert result is not None


class TestExecuteSelfHealingExtended:
    """Cover fix_arr_remote_path action in _execute_self_healing."""

    @pytest.mark.asyncio
    async def test_fix_arr_remote_path(self):
        """fix_arr_remote_path action calls the skill and returns result (lines 345-349)."""
        analysis = "SELF_HEAL: fix_arr_remote_path"
        import types
        fake_maint = types.ModuleType("maintenance_skills")
        fake_maint.fix_arr_remote_path = AsyncMock(return_value="Paths fixed")
        with patch.dict("sys.modules", {"maintenance_skills": fake_maint}), \
             patch("bg_healing.audit_log", MagicMock()):
            _, heal_results = await bg_healing._execute_self_healing(analysis)
        assert any("arr path fix" in r for r in heal_results)

    @pytest.mark.asyncio
    async def test_exception_in_action_appended(self):
        """When an action throws, the error is captured in heal_results (lines 364-366)."""
        analysis = "SELF_HEAL: restart_container sonarr"
        with patch("bg_healing.restart_container", new=AsyncMock(side_effect=RuntimeError("fail"))), \
             patch("bg_healing.audit_log", MagicMock()):
            _, heal_results = await bg_healing._execute_self_healing(analysis)
        assert any("❌" in r for r in heal_results)


class TestCopilotFixView:
    """Cover _CopilotFixView interaction methods."""

    @pytest.mark.asyncio
    async def test_on_timeout_disables_buttons(self):
        """on_timeout disables all child buttons (lines 380-390)."""
        view = bg_healing._CopilotFixView(["fix this"])
        # Real children (buttons) from discord.ui.View
        for child in view.children:
            assert child.disabled is False
        view.message = None
        await view.on_timeout()
        for child in view.children:
            assert child.disabled is True

    @pytest.mark.asyncio
    async def test_on_timeout_edits_message_when_present(self):
        """on_timeout edits message when self.message exists."""
        view = bg_healing._CopilotFixView(["fix this"])
        mock_msg = MagicMock()
        mock_msg.edit = AsyncMock()
        view.message = mock_msg
        await view.on_timeout()
        mock_msg.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_timeout_http_exception_swallowed(self):
        """HTTPException during on_timeout edit is caught."""
        import discord
        view = bg_healing._CopilotFixView(["fix this"])
        mock_msg = MagicMock()
        mock_msg.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "error"))
        view.message = mock_msg
        await view.on_timeout()  # Should not raise

    @pytest.mark.asyncio
    async def test_interaction_check_returns_false_when_finished(self):
        """interaction_check returns False if view is finished (lines 394-400)."""
        view = bg_healing._CopilotFixView(["fix this"])
        view.stop()  # Mark as finished
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        result = await view.interaction_check(interaction)
        assert result is False
        interaction.response.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interaction_check_returns_true_when_active(self):
        """interaction_check returns True for active view (line 401)."""
        view = bg_healing._CopilotFixView(["fix this"])
        interaction = MagicMock()
        result = await view.interaction_check(interaction)
        assert result is True

    @pytest.mark.asyncio
    async def test_ack_disables_buttons_and_edits(self):
        """_ack disables buttons and calls edit_message (lines 409-412)."""
        view = bg_healing._CopilotFixView(["fix this"])
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        await view._ack(interaction)
        for child in view.children:
            assert child.disabled is True
        interaction.response.edit_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ack_handles_interaction_responded(self):
        """_ack handles InteractionResponded gracefully (line 413-414)."""
        import discord
        view = bg_healing._CopilotFixView(["fix this"])
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock(
            side_effect=discord.InteractionResponded(MagicMock())
        )
        await view._ack(interaction)  # Should not raise

    @pytest.mark.asyncio
    async def test_ack_falls_back_to_defer_on_exception(self):
        """When edit_message raises unexpected exception, falls back to defer_update (lines 415-423)."""
        view = bg_healing._CopilotFixView(["fix this"])
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock(side_effect=RuntimeError("bad"))
        interaction.response.defer_update = AsyncMock()
        await view._ack(interaction)
        interaction.response.defer_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deny_button_sends_skip_message(self):
        """deny_button sends skip confirmation and stops the view (lines 474-481)."""
        view = bg_healing._CopilotFixView(["fix this"])
        channel = MagicMock()
        channel.send = AsyncMock()
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.channel = channel
        interaction.user = MagicMock()
        interaction.user.display_name = "Dave"
        button = view.deny_button  # discord.ui.Button item
        with patch("bg_healing.audit_log", MagicMock()):
            await view.deny_button.callback(interaction)  # _ItemCallback takes just (interaction)
        channel.send.assert_awaited_once()
        sent = channel.send.call_args[0][0]
        assert "skip" in sent.lower() or "skipped" in sent.lower()

    @pytest.mark.asyncio
    async def test_approve_button_runs_fix_and_edits_status(self):
        """approve_button runs copilot_fix and edits status message (lines 428-469)."""
        view = bg_healing._CopilotFixView(["do the thing"])
        status_msg = MagicMock()
        status_msg.edit = AsyncMock()
        channel = MagicMock()
        channel.send = AsyncMock(return_value=status_msg)
        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.channel = channel
        interaction.user = MagicMock()
        interaction.user.display_name = "Dave"
        import types
        fake_maint = types.ModuleType("maintenance_skills")
        fake_maint.copilot_fix = AsyncMock(return_value="Fixed!")
        with patch.dict("sys.modules", {"maintenance_skills": fake_maint}), \
             patch("bg_healing.audit_log", MagicMock()):
            await view.approve_button.callback(interaction)  # _ItemCallback takes just (interaction)
        status_msg.edit.assert_awaited_once()
        content = status_msg.edit.call_args[1].get("content", "")
        assert "Fixed!" in content


class TestRunProactiveScanExtended:
    """Cover copilot_fix pending approval path and exception handling."""

    @pytest.mark.asyncio
    async def test_copilot_fix_pending_attaches_view(self):
        """When analysis contains SELF_HEAL: copilot_fix, a _CopilotFixView is attached (lines 555-556)."""
        channel = _make_channel()
        msg = MagicMock()
        msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=msg)
        bot = _make_bot(channel=channel)

        analysis_with_copilot = (
            "Service sonarr is down.\nSELF_HEAL: copilot_fix restart sonarr service"
        )
        display = "Service sonarr is down."

        with patch("bg_healing.ALERT_CHANNEL_ID", 123), \
             patch.object(bg_healing, "_gather_system_signals",
                          new=AsyncMock(return_value=("system ok", {}))), \
             patch("bg_healing.llm_chat",
                   new=AsyncMock(return_value=(analysis_with_copilot, None, None))), \
             patch.object(bg_healing, "_execute_self_healing",
                          new=AsyncMock(return_value=(display, ["🤖 fix pending"]))), \
             patch("bg_healing.audit_log", MagicMock()):
            await bg_healing._run_proactive_scan(bot)

        msg.edit.assert_awaited_once()
        call_kwargs = msg.edit.call_args[1]
        assert isinstance(call_kwargs.get("view"), bg_healing._CopilotFixView)

    @pytest.mark.asyncio
    async def test_general_exception_in_scan_is_caught(self):
        """Unexpected exceptions from channel.send are caught (lines 562-563)."""
        channel = _make_channel()
        channel.send = AsyncMock(side_effect=RuntimeError("discord crash"))
        bot = _make_bot(channel=channel)

        with patch("bg_healing.ALERT_CHANNEL_ID", 123), \
             patch.object(bg_healing, "_gather_system_signals",
                          new=AsyncMock(return_value=("error found", {}))), \
             patch("bg_healing.llm_chat",
                   new=AsyncMock(return_value=("ALERT: something bad", None, None))), \
             patch.object(bg_healing, "_execute_self_healing",
                          new=AsyncMock(return_value=("ALERT: something bad", []))), \
             patch("bg_healing.audit_log", MagicMock()):
            await bg_healing._run_proactive_scan(bot)  # Should not raise
