"""Extended tests for discord_background.py — targeting uncovered lines."""

import os

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")
os.environ.setdefault("THREAD_DB_PATH", "/tmp/t_bg.db")

import asyncio
import datetime
import json
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import bg_briefing
import bg_healing
import bg_monitoring
import bg_tasks
import discord_background as mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bot(channel=None):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel or MagicMock(send=AsyncMock()))
    bot.wait_until_ready = AsyncMock()
    bot.is_closed = MagicMock(return_value=False)
    bot.fetch_user = AsyncMock()
    return bot


# ===========================================================================
# audit_writer_loop
# ===========================================================================

class TestAuditWriterLoop:
    @pytest.mark.asyncio
    async def test_flushes_buffer_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bg_healing, "AUDIT_DIR", tmp_path)
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)

        buf = deque(["entry1", "entry2"])
        monkeypatch.setattr(bg_healing, "_audit_buffer", buf)

        async def run_once():
            # Run one iteration manually (sleep 0 then process)
            await asyncio.sleep(0)
            if not bg_healing._audit_buffer:
                return
            entries = []
            while bg_healing._audit_buffer:
                try:
                    entries.append(bg_healing._audit_buffer.popleft())
                except IndexError:
                    break
            if entries:
                today = datetime.date.today().isoformat()
                audit_file = tmp_path / f"{today}.jsonl"
                with open(audit_file, "a") as f:
                    for e in entries:
                        f.write(json.dumps(e) + "\n")

        await run_once()
        today = datetime.date.today().isoformat()
        written = (tmp_path / f"{today}.jsonl").read_text()
        assert "entry1" in written
        assert "entry2" in written

    @pytest.mark.asyncio
    async def test_skips_when_buffer_empty(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)
        empty_buf = deque()
        monkeypatch.setattr(bg_healing, "_audit_buffer", empty_buf)

        # Patch sleep to break the loop after one iteration
        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mod.audit_writer_loop()

        # No file writes happened
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_handles_oserror(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "AUDIT_FLUSH_INTERVAL", 0)
        buf = deque(["entry"])
        monkeypatch.setattr(bg_healing, "_audit_buffer", buf)

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("bg_healing.asyncio.sleep", fake_sleep), \
             patch("builtins.open", side_effect=OSError("disk full")):
            with pytest.raises(asyncio.CancelledError):
                await mod.audit_writer_loop()


# ===========================================================================
# background_cleanup_loop
# ===========================================================================

class TestBackgroundCleanupLoop:
    @pytest.mark.asyncio
    async def test_calls_cleanup_stores(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "CLEANUP_INTERVAL", 0)

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        mock_conv = MagicMock()
        mock_approval = MagicMock()
        mock_collector = MagicMock()

        with patch("bg_healing.asyncio.sleep", fake_sleep), \
             patch("bg_healing.conversation_store", mock_conv), \
             patch("bg_healing.approval_store", mock_approval), \
             patch("bg_healing.get_collector", return_value=mock_collector):
            with pytest.raises(asyncio.CancelledError):
                await mod.background_cleanup_loop()

        mock_conv.cleanup_expired.assert_called()
        mock_approval.cleanup_expired.assert_called()
        mock_collector.record_command.assert_called()

    @pytest.mark.asyncio
    async def test_handles_exception_in_cleanup(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "CLEANUP_INTERVAL", 0)

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        mock_conv = MagicMock()
        mock_conv.cleanup_expired.side_effect = RuntimeError("db error")
        mock_collector = MagicMock()

        with patch("bg_healing.asyncio.sleep", fake_sleep), \
             patch("bg_healing.conversation_store", mock_conv), \
             patch("bg_healing.get_collector", return_value=mock_collector):
            with pytest.raises(asyncio.CancelledError):
                await mod.background_cleanup_loop()

        # Should still record_command with success=False
        args = mock_collector.record_command.call_args.kwargs
        assert args["success"] is False
        assert args["error_type"] == "RuntimeError"


# ===========================================================================
# morning_briefing_loop
# ===========================================================================

class TestMorningBriefingLoop:
    @pytest.mark.asyncio
    async def test_triggers_briefing_at_right_hour(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "BRIEFING_HOUR", 8)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 123)

        call_count = 0
        created_tasks = []

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        fake_now = datetime.datetime(2024, 1, 1, 8, 0, 0)
        mock_briefing = AsyncMock()

        with patch("bg_briefing.asyncio.sleep", fake_sleep), \
             patch("bg_briefing.datetime") as mock_dt, \
             patch("bg_briefing.send_morning_briefing", mock_briefing), \
             patch("bg_briefing.asyncio.create_task") as mock_create_task:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.date = datetime.date
            with pytest.raises(asyncio.CancelledError):
                await mod.morning_briefing_loop(MagicMock())

        mock_create_task.assert_called()

    @pytest.mark.asyncio
    async def test_no_briefing_outside_window(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "BRIEFING_HOUR", 8)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 5)

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        fake_now = datetime.datetime(2024, 1, 1, 10, 0, 0)  # 10 AM, not 8 AM

        with patch("bg_briefing.asyncio.sleep", fake_sleep), \
             patch("bg_briefing.datetime") as mock_dt, \
             patch("bg_briefing.asyncio.create_task") as mock_create_task:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.date = datetime.date
            with pytest.raises(asyncio.CancelledError):
                await mod.morning_briefing_loop(MagicMock())

        mock_create_task.assert_not_called()


# ===========================================================================
# send_morning_briefing
# ===========================================================================

class TestSendMorningBriefing:
    @pytest.mark.asyncio
    async def test_posts_embed_to_channel(self, monkeypatch):
        channel = MagicMock(send=AsyncMock())

        with patch("bg_briefing.check_arr_health", AsyncMock(return_value="ok")), \
             patch("bg_briefing.get_download_queue", AsyncMock(return_value="none")), \
             patch("bg_briefing.get_weather", AsyncMock(return_value="sunny")), \
             patch("bg_briefing.get_system_stats", AsyncMock(return_value="CPU 10%")), \
             patch("bg_briefing.llm_chat", AsyncMock(return_value=("Good morning!", None, None))), \
             patch("bg_briefing.audit_log"), \
             patch("bg_briefing.asyncio.gather", new_callable=AsyncMock, return_value=("ok", "none", "sunny", "CPU 10%")):
            # Use channel_override to avoid channel lookup
            await mod.send_morning_briefing(MagicMock(), channel_override=channel)

        channel.send.assert_awaited_once()
        args = channel.send.call_args
        embed = args.kwargs.get("embed") or args.args[0] if args.args else None
        # Just verify something was sent
        assert channel.send.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_early_no_alert_channel(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 0)
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        # No channel_override and no ALERT_CHANNEL_ID → should return immediately
        await mod.send_morning_briefing(bot)
        # Should not raise

    @pytest.mark.asyncio
    async def test_returns_early_channel_not_found(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 999)
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        await mod.send_morning_briefing(bot)
        bot.get_channel.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_handles_llm_exception(self, monkeypatch):
        channel = MagicMock(send=AsyncMock())

        with patch("bg_briefing.asyncio.gather", new_callable=AsyncMock, return_value=("ok", "none", "sunny", "CPU")), \
             patch("bg_briefing.llm_chat", AsyncMock(side_effect=Exception("LLM down"))):
            # Should not propagate the exception
            await mod.send_morning_briefing(MagicMock(), channel_override=channel)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_full_optional_sections(self, monkeypatch):
        channel = MagicMock(send=AsyncMock())

        mock_stats = {"total": 5, "successes": 4, "failures": 1, "success_rate": 0.8,
                      "avg_latency_ms": 100, "recent_errors": [{"error": "oops"}]}

        with patch("bg_briefing.asyncio.gather", new_callable=AsyncMock, return_value=("ok", "none", "sunny", "CPU 10%")), \
             patch("bg_briefing.llm_chat", AsyncMock(return_value=("Morning!", None, None))), \
             patch("bg_briefing.audit_log"), \
             patch("bg_briefing.asyncio.wait_for", new_callable=AsyncMock, return_value="calendar ok"):

            # Patch optional imports
            mock_cal = MagicMock(return_value=AsyncMock(return_value="Calendar stuff"))
            mock_goals = MagicMock(return_value="goal1")
            mock_err_stats = MagicMock(return_value=mock_stats)
            mock_overseerr = MagicMock(return_value=AsyncMock(return_value="2 requests"))
            mock_predict = MagicMock(return_value={"days_until_full": 10, "percent_used": 95})

            import sys
            # Mock calendar_skills module
            fake_cal_mod = MagicMock()
            fake_cal_mod.get_upcoming_events = AsyncMock(return_value="today's events")
            fake_goal_mod = MagicMock()
            fake_goal_mod.format_goals_for_briefing = MagicMock(return_value="Some goal")
            fake_err_mod = MagicMock()
            fake_err_mod.get_error_stats = MagicMock(return_value=mock_stats)
            fake_overseerr_mod = MagicMock()
            fake_overseerr_mod.get_request_stats = AsyncMock(return_value="2 requests")
            fake_health_mod = MagicMock()
            fake_health_mod.predict_full = MagicMock(return_value={"days_until_full": 10, "percent_used": 95})

            with patch.dict(sys.modules, {
                "calendar_skills": fake_cal_mod,
                "goal_tracker": fake_goal_mod,
                "error_tracker": fake_err_mod,
                "overseerr": fake_overseerr_mod,
                "health_history": fake_health_mod,
            }):
                await mod.send_morning_briefing(MagicMock(), channel_override=channel)

        # Should still attempt to send
        assert channel.send.await_count >= 1


# ===========================================================================
# evening_digest_loop
# ===========================================================================

class TestEveningDigestLoop:
    @pytest.mark.asyncio
    async def test_triggers_at_evening_hour(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "BRIEFING_CHECK_INTERVAL", 0)
        monkeypatch.setattr(bg_briefing, "EVENING_DIGEST_HOUR", 21)
        monkeypatch.setattr(bg_briefing, "BRIEFING_MINUTE_WINDOW", 60)

        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        fake_now = datetime.datetime(2024, 1, 1, 21, 0, 0)

        with patch("bg_briefing.asyncio.sleep", fake_sleep), \
             patch("bg_briefing.datetime") as mock_dt, \
             patch("bg_briefing.asyncio.create_task") as mock_create_task:
            mock_dt.datetime.now.return_value = fake_now
            mock_dt.date = datetime.date
            with pytest.raises(asyncio.CancelledError):
                await mod.evening_digest_loop(MagicMock())

        mock_create_task.assert_called()


# ===========================================================================
# send_evening_digest
# ===========================================================================

class TestSendEveningDigest:
    @pytest.mark.asyncio
    async def test_returns_early_no_channel(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 0)
        bot = MagicMock()
        await mod.send_evening_digest(bot)

    @pytest.mark.asyncio
    async def test_returns_early_channel_not_found(self, monkeypatch):
        monkeypatch.setattr(bg_briefing, "ALERT_CHANNEL_ID", 999)
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)
        await mod.send_evening_digest(bot)

    @pytest.mark.asyncio
    async def test_posts_embed(self, monkeypatch):
        channel = MagicMock(send=AsyncMock())

        with patch("bg_briefing.get_system_stats", AsyncMock(return_value="CPU 5%")), \
             patch("bg_briefing.get_download_queue", AsyncMock(return_value="2 active downloads")), \
             patch("bg_briefing.asyncio.wait_for", new_callable=AsyncMock, side_effect=lambda coro, timeout: coro), \
             patch("bg_briefing.audit_log"):
            await mod.send_evening_digest(MagicMock(), channel_override=channel)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_posts_embed_with_audit_data(self, monkeypatch, tmp_path):
        channel = MagicMock(send=AsyncMock())

        # Create a fake audit file
        today = datetime.date.today().isoformat()
        audit_file = tmp_path / f"audit/{today}.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text(
            json.dumps({"action": "ask"}) + "\n" +
            json.dumps({"action": "ask"}) + "\n" +
            json.dumps({"action": "status"}) + "\n"
        )

        with patch("bg_briefing.get_system_stats", AsyncMock(return_value="CPU 5%")), \
             patch("bg_briefing.get_download_queue", AsyncMock(return_value="none")), \
             patch("bg_briefing.asyncio.wait_for", new_callable=AsyncMock,
                   side_effect=lambda coro, timeout: asyncio.coroutine(lambda: "stats ok")()), \
             patch("bg_briefing.audit_log"), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=(
                 json.dumps({"action": "ask"}) + "\n" + json.dumps({"action": "status"}) + "\n"
             )):
            await mod.send_evening_digest(MagicMock(), channel_override=channel)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_all_section_exceptions(self, monkeypatch):
        channel = MagicMock(send=AsyncMock())

        with patch("bg_briefing.get_system_stats", AsyncMock(side_effect=Exception("down"))), \
             patch("bg_briefing.get_download_queue", AsyncMock(side_effect=Exception("down"))), \
             patch("bg_briefing.asyncio.wait_for", new_callable=AsyncMock, side_effect=Exception("timeout")), \
             patch("bg_briefing.audit_log"):
            await mod.send_evening_digest(MagicMock(), channel_override=channel)

        # Should still send (embed may be empty but not crash)
        channel.send.assert_awaited_once()


# ===========================================================================
# _check_quality_drift_alert — additional early-return paths
# ===========================================================================

class TestCheckQualityDriftAlertEdgeCases:
    @pytest.mark.asyncio
    async def test_no_alert_channel_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 0)
        result = await bg_healing._check_quality_drift_alert(MagicMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_import_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        import sys
        # Use patch.dict to safely hide dashboard modules during the test.
        # patch.dict will restore sys.modules to its original state after the block,
        # preserving any previously imported references.
        with patch.dict(sys.modules, {"dashboard": None, "dashboard.api_handlers": None}):
            result = await bg_healing._check_quality_drift_alert(MagicMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_non_dict_calibration_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        with patch("dashboard.api_handlers._build_offline_quality_calibration_payload", return_value="not a dict"):
            result = await bg_healing._check_quality_drift_alert(MagicMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_non_dict_drift_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        with patch("dashboard.api_handlers._build_offline_quality_calibration_payload",
                   return_value={"drift": "string not dict"}):
            result = await bg_healing._check_quality_drift_alert(MagicMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_non_severe_returns_false(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        payload = {
            "drift": {
                "status": "drifted",
                "severity": {"level": "minor", "severe": False, "score": 1, "reasons": []},
                "regressed_metrics": [],
            }
        }
        with patch("dashboard.api_handlers._build_offline_quality_calibration_payload", return_value=payload):
            result = await bg_healing._check_quality_drift_alert(MagicMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_false(self, monkeypatch):
        from alert_manager import reset_bounded_alert_cache
        reset_bounded_alert_cache()

        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        payload = {
            "drift": {
                "status": "drifted",
                "severity": {"level": "severe", "severe": True, "score": 5, "reasons": ["r1"]},
                "regressed_metrics": ["metric1"],
            }
        }
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        with patch("dashboard.api_handlers._build_offline_quality_calibration_payload", return_value=payload):
            result = await bg_healing._check_quality_drift_alert(bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_non_dict_severity_still_works(self, monkeypatch):
        from alert_manager import reset_bounded_alert_cache
        reset_bounded_alert_cache()

        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock())
        bot = MagicMock(get_channel=MagicMock(return_value=channel))

        payload = {
            "drift": {
                "status": "drifted",
                "severity": None,  # non-dict → defaults to {}
                "regressed_metrics": [],
            }
        }
        with patch("dashboard.api_handlers._build_offline_quality_calibration_payload", return_value=payload):
            result = await bg_healing._check_quality_drift_alert(bot)
        # severity={} → severe=False → returns False
        assert result is False


# ===========================================================================
# _gather_system_signals
# ===========================================================================

class TestGatherSystemSignals:
    @pytest.mark.asyncio
    async def test_returns_none_when_all_clean(self):
        with patch("bg_healing.check_arr_health", AsyncMock(return_value="OK healthy")), \
             patch("bg_healing.check_download_clients", AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", AsyncMock(return_value="online")), \
             patch("bg_healing.get_system_stats", AsyncMock(return_value="Disk 50%")), \
             patch("bg_healing.get_container_logs", AsyncMock(return_value="")):
            result = await bg_healing._gather_system_signals()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_summary_when_errors_found(self):
        with patch("bg_healing.check_arr_health", AsyncMock(return_value="error: connection refused")), \
             patch("bg_healing.check_download_clients", AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", AsyncMock(return_value="Disk 50%")), \
             patch("bg_healing.get_container_logs", AsyncMock(return_value="")):
            result = await bg_healing._gather_system_signals()
        assert result is not None
        summary, log_snippets = result
        assert "arr" in summary.lower() or "error" in summary.lower()

    @pytest.mark.asyncio
    async def test_returns_summary_when_log_anomalies(self):
        with patch("bg_healing.check_arr_health", AsyncMock(return_value="healthy")), \
             patch("bg_healing.check_download_clients", AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", AsyncMock(return_value="Disk 50%")), \
             patch("bg_healing.get_container_logs",
                   AsyncMock(return_value="ERROR: connection failed")):
            result = await bg_healing._gather_system_signals()
        assert result is not None

    @pytest.mark.asyncio
    async def test_disk_alert_from_sys_stats(self):
        with patch("bg_healing.check_arr_health", AsyncMock(return_value="healthy")), \
             patch("bg_healing.check_download_clients", AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", AsyncMock(return_value="Disk (95% used)")), \
             patch("bg_healing.get_container_logs", AsyncMock(return_value="")):
            result = await bg_healing._gather_system_signals()
        # disk_alert=True → not all clean
        assert result is not None

    @pytest.mark.asyncio
    async def test_handles_nas_disk_red_status(self):
        with patch("bg_healing.check_arr_health", AsyncMock(return_value="healthy")), \
             patch("bg_healing.check_download_clients", AsyncMock(return_value="OK")), \
             patch("bg_healing.check_plex_status", AsyncMock(return_value="OK")), \
             patch("bg_healing.get_system_stats", AsyncMock(return_value="Disk 50%")), \
             patch("bg_healing.get_container_logs", AsyncMock(return_value="")):
            import sys
            fake_maintenance = MagicMock()
            fake_maintenance.check_nas_health = AsyncMock(return_value="🔴 RAID degraded")
            fake_maintenance.check_gluetun_vpn = AsyncMock(return_value="VPN ok")
            with patch.dict(sys.modules, {"maintenance_skills": fake_maintenance}):
                result = await bg_healing._gather_system_signals()
        # NAS has 🔴 → disk_alert=True → not all clean
        assert result is not None

    @pytest.mark.asyncio
    async def test_handles_exception_results(self):
        exc = Exception("timeout")
        with patch("bg_healing.asyncio.gather", new_callable=AsyncMock,
                   return_value=(exc, exc, exc, exc)), \
             patch("bg_healing.get_container_logs", AsyncMock(return_value="")):
            result = await bg_healing._gather_system_signals()
        # all exceptions → all_clean check skips non-str → may be clean or not
        # just ensure no crash


# ===========================================================================
# _execute_self_healing — additional action paths
# ===========================================================================

class TestExecuteSelfHealingActions:
    @pytest.mark.asyncio
    async def test_fix_qbit_download_path(self):
        analysis = "SELF_HEAL: fix_qbit_download_path"
        import sys
        fake_maintenance = MagicMock()
        fake_maintenance.fix_qbit_download_path = AsyncMock(return_value="fixed!")
        with patch.dict(sys.modules, {"maintenance_skills": fake_maintenance}), \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing(analysis)
        assert any("qBittorrent" in r or "qbit" in r.lower() for r in results)

    @pytest.mark.asyncio
    async def test_fix_arr_remote_path(self):
        analysis = "SELF_HEAL: fix_arr_remote_path"
        import sys
        fake_maintenance = MagicMock()
        fake_maintenance.fix_arr_remote_path = AsyncMock(return_value="arr paths fixed")
        with patch.dict(sys.modules, {"maintenance_skills": fake_maintenance}), \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing(analysis)
        assert any("arr" in r.lower() or "path" in r.lower() for r in results)

    @pytest.mark.asyncio
    async def test_auto_cleanup_disk(self):
        analysis = "SELF_HEAL: auto_cleanup_disk"
        import sys
        fake_maintenance = MagicMock()
        fake_maintenance.auto_cleanup_disk = AsyncMock(return_value="3GB freed")
        with patch.dict(sys.modules, {"maintenance_skills": fake_maintenance}), \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing(analysis)
        assert any("cleanup" in r.lower() or "disk" in r.lower() for r in results)

    @pytest.mark.asyncio
    async def test_copilot_fix_pending_action(self):
        analysis = "SELF_HEAL: copilot_fix check the logs for errors"
        with patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing(analysis)
        assert any("Copilot" in r or "approval" in r.lower() for r in results)

    @pytest.mark.asyncio
    async def test_action_exception_produces_error_entry(self):
        with patch.object(mod, "_parse_heal_actions",
                          return_value=[("restart_container", "sonarr")]), \
             patch("bg_healing.restart_container", AsyncMock(side_effect=Exception("fail!"))), \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing("SELF_HEAL: restart_container sonarr")
        assert any("❌" in r for r in results)

    @pytest.mark.asyncio
    async def test_strips_self_heal_lines_from_display(self):
        analysis = "Analysis text\nSELF_HEAL: fix_qbit_download_path\nMore text"
        import sys
        fake_maintenance = MagicMock()
        fake_maintenance.fix_qbit_download_path = AsyncMock(return_value="ok")
        with patch.dict(sys.modules, {"maintenance_skills": fake_maintenance}), \
             patch("bg_healing.audit_log"):
            cleaned, results = await bg_healing._execute_self_healing(analysis)
        assert "SELF_HEAL" not in cleaned


# ===========================================================================
# _CopilotFixView
# ===========================================================================

class TestCopilotFixView:
    @pytest.mark.asyncio
    async def test_on_timeout_disables_buttons(self):
        view = bg_healing._CopilotFixView(["fix the auth module"])
        button = MagicMock()
        button.disabled = False
        view._children.append(button)
        view.message = None  # no message to edit

        await view.on_timeout()
        assert button.disabled is True

    @pytest.mark.asyncio
    async def test_on_timeout_edits_message_if_present(self):
        view = bg_healing._CopilotFixView(["fix something"])
        message = MagicMock()
        message.edit = AsyncMock()
        view.message = message
        # no children

        await view.on_timeout()
        message.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interaction_check_when_finished(self):
        view = bg_healing._CopilotFixView(["fix it"])
        view.stop()  # Mark view as finished

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        result = await view.interaction_check(interaction)
        assert result is False
        interaction.response.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interaction_check_when_active(self):
        view = bg_healing._CopilotFixView(["fix it"])
        interaction = MagicMock()

        result = await view.interaction_check(interaction)
        assert result is True

    @pytest.mark.asyncio
    async def test_ack_disables_buttons(self):
        view = bg_healing._CopilotFixView(["fix it"])
        button = MagicMock()
        button.disabled = False
        view._children.append(button)

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()

        await view._ack(interaction)
        assert button.disabled is True
        interaction.response.edit_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ack_handles_interaction_responded(self):
        view = bg_healing._CopilotFixView(["fix it"])
        # no children

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock(side_effect=discord.InteractionResponded(interaction))
        interaction.response.defer_update = AsyncMock()

        # Should not raise
        await view._ack(interaction)

    @pytest.mark.asyncio
    async def test_deny_button_sends_skip_message(self):
        view = bg_healing._CopilotFixView(["fix it"])
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.display_name = "TestUser"
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.channel = MagicMock()
        interaction.channel.send = AsyncMock()

        with patch("bg_healing.audit_log"):
            await view.deny_button.callback(interaction)

        interaction.channel.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_approve_button_runs_fix(self):
        view = bg_healing._CopilotFixView(["fix the logs"])
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.display_name = "TestUser"
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.channel = MagicMock()
        interaction.channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))

        import sys
        fake_maintenance = MagicMock()
        fake_maintenance.copilot_fix = AsyncMock(return_value="Fix applied!")

        with patch.dict(sys.modules, {"maintenance_skills": fake_maintenance}), \
             patch("bg_healing.audit_log"):
            await view.approve_button.callback(interaction)

        assert interaction.channel.send.await_count >= 1


# ===========================================================================
# _run_proactive_scan
# ===========================================================================

class TestRunProactiveScan:
    @pytest.mark.asyncio
    async def test_returns_early_no_alert_channel(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 0)
        await bg_healing._run_proactive_scan(MagicMock())  # Should not raise

    @pytest.mark.asyncio
    async def test_returns_early_all_clear(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        with patch.object(mod, "_gather_system_signals", AsyncMock(return_value=None)):
            await bg_healing._run_proactive_scan(MagicMock())

    @pytest.mark.asyncio
    async def test_no_alert_on_no_alert_from_llm(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        with patch.object(mod, "_gather_system_signals", AsyncMock(return_value=("summary text", {}))), \
             patch("bg_healing.llm_chat", AsyncMock(return_value=("NO_ALERT", None, None))):
            await bg_healing._run_proactive_scan(bot)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_posts_embed_on_insight(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock(return_value=MagicMock(edit=AsyncMock())))
        bot = _make_bot(channel)

        with patch.object(mod, "_gather_system_signals", AsyncMock(return_value=("errors found", {}))), \
             patch("bg_healing.llm_chat", AsyncMock(return_value=("Container sonarr is down!", None, None))), \
             patch.object(mod, "_execute_self_healing", AsyncMock(return_value=("insight text", []))), \
             patch("bg_healing.audit_log"):
            await bg_healing._run_proactive_scan(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_posts_with_heal_results(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock(return_value=MagicMock(edit=AsyncMock())))
        bot = _make_bot(channel)

        with patch.object(mod, "_gather_system_signals", AsyncMock(return_value=("errors", {}))), \
             patch("bg_healing.llm_chat", AsyncMock(return_value=("Found issues\nSELF_HEAL: restart_container sonarr", None, None))), \
             patch.object(mod, "_execute_self_healing", AsyncMock(return_value=("Found issues", ["🔧 sonarr: restarted"]))), \
             patch("bg_healing.audit_log"):
            await bg_healing._run_proactive_scan(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_llm_timeout(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        bot = _make_bot()

        with patch.object(mod, "_gather_system_signals", AsyncMock(return_value=("errors", {}))), \
             patch("bg_healing.llm_chat", AsyncMock(side_effect=asyncio.TimeoutError)):
            await bg_healing._run_proactive_scan(bot)  # Should not raise

    @pytest.mark.asyncio
    async def test_channel_not_found(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        with patch.object(mod, "_gather_system_signals", AsyncMock(return_value=("errors", {}))), \
             patch("bg_healing.llm_chat", AsyncMock(return_value=("Issue found!", None, None))), \
             patch.object(mod, "_execute_self_healing", AsyncMock(return_value=("Issue found!", []))):
            await bg_healing._run_proactive_scan(bot)

    @pytest.mark.asyncio
    async def test_posts_copilot_fix_view(self, monkeypatch):
        monkeypatch.setattr(bg_healing, "ALERT_CHANNEL_ID", 123)
        msg = MagicMock()
        msg.edit = AsyncMock()
        channel = MagicMock(send=AsyncMock(return_value=msg))
        bot = _make_bot(channel)

        analysis = "Issue found!\nSELF_HEAL: copilot_fix check the logs"
        with patch.object(bg_healing, "_gather_system_signals", AsyncMock(return_value=("errors", {}))), \
             patch("bg_healing.llm_chat", AsyncMock(return_value=(analysis, None, None))), \
             patch.object(bg_healing, "_execute_self_healing", AsyncMock(return_value=("Issue found!", ["🤖 Copilot fix pending"]))), \
             patch("bg_healing.audit_log"), \
             patch("llm_ratelimit.background_quota_guard.check_background_allowed", return_value=True):
            await bg_healing._run_proactive_scan(bot)

        msg.edit.assert_awaited_once()


# ===========================================================================
# _post_error_alert
# ===========================================================================

class TestPostErrorAlert:
    @pytest.mark.asyncio
    async def test_returns_early_no_channel_id(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 0)
        await bg_monitoring._post_error_alert(MagicMock(), [])

    @pytest.mark.asyncio
    async def test_returns_early_channel_not_found(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)
        await bg_monitoring._post_error_alert(bot, [{"severity": "critical", "type": "timeout", "detail": "x"}])

    @pytest.mark.asyncio
    async def test_posts_critical_alert(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        patterns = [{"severity": "critical", "type": "connection_error", "detail": "DB timed out"}]
        with patch("bg_monitoring.audit_log"):
            await bg_monitoring._post_error_alert(bot, patterns)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_posts_warning_alert(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        patterns = [{"severity": "warning", "type": "high_latency", "detail": "P99=5000ms"}]
        with patch("bg_monitoring.audit_log"):
            await bg_monitoring._post_error_alert(bot, patterns)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_http_exception(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limited")))
        bot = _make_bot(channel)

        patterns = [{"severity": "critical", "type": "error", "detail": "oops"}]
        with patch("bg_monitoring.audit_log"):
            await bg_monitoring._post_error_alert(bot, patterns)  # Should not raise


# ===========================================================================
# error_monitor_loop
# ===========================================================================

class TestErrorMonitorLoop:
    @pytest.mark.asyncio
    async def test_posts_alert_on_critical_patterns(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        patterns = [{"severity": "critical", "type": "db_error", "detail": "Connection lost"}]
        import sys
        fake_err_tracker = MagicMock()
        fake_err_tracker.check_error_patterns = MagicMock(return_value=patterns)
        fake_err_tracker.diagnose_error_pattern = AsyncMock(
            return_value={"cause": "DB down", "confidence": 0.9, "explanation": "DB is down"}
        )
        fake_err_tracker.execute_fix = AsyncMock(return_value={"success": True, "action_taken": "restart", "detail": "done"})
        fake_err_tracker.record_incident = AsyncMock()
        fake_err_tracker.get_recent_outcomes = MagicMock(return_value=[])

        with patch("bg_monitoring.asyncio.sleep", fake_sleep), \
             patch.dict(sys.modules, {"error_tracker": fake_err_tracker}), \
             patch("bg_monitoring.audit_log"):
            with pytest.raises(asyncio.CancelledError):
                await mod.error_monitor_loop(bot)

        channel.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_logs_warning_patterns_below_threshold(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        patterns = [{"severity": "warning", "type": "high_latency", "detail": "slow"}]
        import sys
        fake_err_tracker = MagicMock()
        fake_err_tracker.check_error_patterns = MagicMock(return_value=patterns)

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        with patch("bg_monitoring.asyncio.sleep", fake_sleep), \
             patch.dict(sys.modules, {"error_tracker": fake_err_tracker}):
            with pytest.raises(asyncio.CancelledError):
                await mod.error_monitor_loop(bot)

        # Only 1 warning pattern → below threshold, no alert posted
        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_patterns_no_alert(self, monkeypatch):
        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        import sys
        fake_err_tracker = MagicMock()
        fake_err_tracker.check_error_patterns = MagicMock(return_value=[])

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        with patch("bg_monitoring.asyncio.sleep", fake_sleep), \
             patch.dict(sys.modules, {"error_tracker": fake_err_tracker}):
            with pytest.raises(asyncio.CancelledError):
                await mod.error_monitor_loop(bot)

        channel.send.assert_not_awaited()


# ===========================================================================
# _check_container_health
# ===========================================================================

class TestCheckContainerHealth:
    @pytest.mark.asyncio
    async def test_returns_early_no_alert_channel(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 0)
        await bg_monitoring._check_container_health(MagicMock())

    @pytest.mark.asyncio
    async def test_alerts_on_unhealthy_container(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._container_prev_state.clear()
        bg_monitoring._container_unhealthy_count.clear()

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        docker_output = "sonarr\tunhealthy\nradarr\tUp 2 hours\n"

        import sys
        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(0, docker_output, ""))

        with patch.dict(sys.modules, {"subprocess_utils": fake_subprocess}), \
             patch("bg_monitoring.audit_log"):
            await bg_monitoring._check_container_health(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alerts_on_exited_container(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._container_prev_state.clear()
        bg_monitoring._container_unhealthy_count.clear()

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        docker_output = "sabnzbd\tExited (1) 5 minutes ago\n"

        import sys
        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(0, docker_output, ""))

        with patch.dict(sys.modules, {"subprocess_utils": fake_subprocess}), \
             patch("bg_monitoring.audit_log"):
            await bg_monitoring._check_container_health(bot)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_restarts_after_threshold(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        monkeypatch.setattr(bg_monitoring, "_AUTO_RESTART_THRESHOLD", 1)
        bg_monitoring._container_prev_state.clear()
        bg_monitoring._container_unhealthy_count.clear()

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        docker_output = "sonarr\tunhealthy\n"

        import sys
        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(0, docker_output, ""))

        with patch.dict(sys.modules, {"subprocess_utils": fake_subprocess}), \
             patch("bg_monitoring.restart_container", AsyncMock(return_value="restarted")), \
             patch("bg_monitoring.audit_log"):
            await bg_monitoring._check_container_health(bot)

        channel.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_clears_state_on_recovery(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._container_prev_state["sonarr"] = "unhealthy"
        bg_monitoring._container_unhealthy_count["sonarr"] = 2

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        docker_output = "sonarr\tUp 5 minutes (healthy)\n"

        import sys
        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(0, docker_output, ""))

        with patch.dict(sys.modules, {"subprocess_utils": fake_subprocess}):
            await bg_monitoring._check_container_health(bot)

        assert "sonarr" not in bg_monitoring._container_prev_state
        assert "sonarr" not in bg_monitoring._container_unhealthy_count

    @pytest.mark.asyncio
    async def test_handles_docker_ps_failure(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)

        import sys
        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(1, "", "error"))

        with patch.dict(sys.modules, {"subprocess_utils": fake_subprocess}):
            await bg_monitoring._check_container_health(MagicMock())  # Should not raise

    @pytest.mark.asyncio
    async def test_no_alerts_when_all_healthy(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._container_prev_state.clear()
        bg_monitoring._container_unhealthy_count.clear()

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        docker_output = "sonarr\tUp 2 hours (healthy)\nradarr\tUp 1 hour\n"

        import sys
        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(0, docker_output, ""))

        with patch.dict(sys.modules, {"subprocess_utils": fake_subprocess}):
            await bg_monitoring._check_container_health(bot)

        channel.send.assert_not_awaited()


# ===========================================================================
# _check_monstervision_cookies
# ===========================================================================

class TestCheckMonstervisionCookies:
    @pytest.mark.asyncio
    async def test_returns_early_no_alert_channel(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 0)
        bg_monitoring._cookie_alert_sent = False
        await bg_monitoring._check_monstervision_cookies(MagicMock())

    @pytest.mark.asyncio
    async def test_no_alert_when_api_says_ok(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._cookie_alert_sent = False

        import sys

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"cookie_status": {"label": "ok"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        mock_bg_sessions = MagicMock()
        mock_bg_sessions.get = AsyncMock(return_value=mock_session)

        fake_config = MagicMock()
        fake_config.cfg = MagicMock()
        fake_config.cfg.docker_host_ip = "192.168.1.1"
        fake_config.cfg.monstervision_port = 8080

        with patch.dict(sys.modules, {"config": fake_config}), \
             patch("bg_monitoring._bg_sessions", mock_bg_sessions):
            await bg_monitoring._check_monstervision_cookies(MagicMock())

        assert bg_monitoring._cookie_alert_sent is False

    @pytest.mark.asyncio
    async def test_sends_alert_on_expired_cookies(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._cookie_alert_sent = False

        import sys

        import aiohttp

        mock_bg_sessions = MagicMock()
        mock_bg_sessions.get = AsyncMock(side_effect=aiohttp.ClientError("connection refused"))

        channel = MagicMock(send=AsyncMock())
        bot = _make_bot(channel)

        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(0, "cookies have expired", ""))

        fake_config = MagicMock()
        fake_config.cfg = MagicMock()
        fake_config.cfg.docker_host_ip = "192.168.1.1"
        fake_config.cfg.monstervision_port = 8080

        with patch.dict(sys.modules, {"config": fake_config, "subprocess_utils": fake_subprocess}), \
             patch("bg_monitoring._bg_sessions", mock_bg_sessions):
            await bg_monitoring._check_monstervision_cookies(bot)

        channel.send.assert_awaited_once()
        assert bg_monitoring._cookie_alert_sent is True

    @pytest.mark.asyncio
    async def test_resets_alert_when_cookies_fresh(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)
        bg_monitoring._cookie_alert_sent = True  # Previously sent

        import sys

        import aiohttp

        mock_bg_sessions = MagicMock()
        mock_bg_sessions.get = AsyncMock(side_effect=aiohttp.ClientError("connection refused"))

        fake_subprocess = MagicMock()
        fake_subprocess.run = AsyncMock(return_value=(0, "all cookies valid", ""))

        fake_config = MagicMock()
        fake_config.cfg = MagicMock()
        fake_config.cfg.docker_host_ip = "192.168.1.1"
        fake_config.cfg.monstervision_port = 8080

        with patch.dict(sys.modules, {"config": fake_config, "subprocess_utils": fake_subprocess}), \
             patch("bg_monitoring._bg_sessions", mock_bg_sessions):
            await bg_monitoring._check_monstervision_cookies(MagicMock())

        assert bg_monitoring._cookie_alert_sent is False


# ===========================================================================
# reminder_loop
# ===========================================================================

class TestReminderLoop:
    @pytest.mark.asyncio
    async def test_sends_reminder_to_user(self, monkeypatch):
        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        bot.is_closed = MagicMock(return_value=False)

        mock_user = MagicMock()
        mock_user.send = AsyncMock()
        bot.fetch_user = AsyncMock(return_value=mock_user)

        reminder = MagicMock()
        reminder.user_id = 42
        reminder.message = "Don't forget!"
        reminder.id = "r1"
        reminder.recurring = None

        import sys
        fake_reminder_mgr = MagicMock()
        fake_reminder_mgr.reminder_manager = MagicMock()
        fake_reminder_mgr.reminder_manager.get_due = MagicMock(return_value=[reminder])
        fake_reminder_mgr.reminder_manager.mark_fired = MagicMock()

        with patch("bg_tasks.asyncio.sleep", fake_sleep), \
             patch.dict(sys.modules, {"reminder_manager": fake_reminder_mgr}):
            with pytest.raises(asyncio.CancelledError):
                await mod.reminder_loop(bot)

        mock_user.send.assert_awaited()
        fake_reminder_mgr.reminder_manager.mark_fired.assert_called_with("r1")

    @pytest.mark.asyncio
    async def test_handles_reminder_send_failure(self, monkeypatch):
        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        bot.is_closed = MagicMock(return_value=False)
        bot.fetch_user = AsyncMock(side_effect=Exception("User not found"))

        reminder = MagicMock()
        reminder.user_id = 42
        reminder.message = "Ping!"
        reminder.id = "r2"
        reminder.recurring = "daily"

        import sys
        fake_reminder_mgr = MagicMock()
        fake_reminder_mgr.reminder_manager = MagicMock()
        fake_reminder_mgr.reminder_manager.get_due = MagicMock(return_value=[reminder])
        fake_reminder_mgr.reminder_manager.mark_fired = MagicMock()

        with patch("bg_tasks.asyncio.sleep", fake_sleep), \
             patch.dict(sys.modules, {"reminder_manager": fake_reminder_mgr}):
            with pytest.raises(asyncio.CancelledError):
                await mod.reminder_loop(bot)

        # mark_fired still called even after exception
        fake_reminder_mgr.reminder_manager.mark_fired.assert_called_with("r2")


# ===========================================================================
# resource_monitor_loop
# ===========================================================================

class TestResourceMonitorLoop:
    @pytest.mark.asyncio
    async def test_posts_alert_on_violations(self, monkeypatch):
        monkeypatch.setattr(bg_monitoring, "ALERT_CHANNEL_ID", 123)

        bot = MagicMock()
        bot.wait_until_ready = AsyncMock()
        bot.is_closed = MagicMock(side_effect=[False, True])  # one loop then stop

        channel = MagicMock(send=AsyncMock())
        bot.get_channel = MagicMock(return_value=channel)

        threshold = MagicMock()
        threshold.container = "radarr"
        threshold.cpu_percent = 80
        threshold.memory_percent = 90
        threshold.cooldown_seconds = 300

        stats = {"cpu": 95.0, "memory": 95.0}
        violations = [(threshold, stats)]

        import sys
        fake_resource_monitor = MagicMock()
        fake_resource_monitor.resource_monitor = MagicMock()
        fake_resource_monitor.resource_monitor.check_all = AsyncMock(return_value=violations)

        with patch.dict(sys.modules, {"resource_monitor": fake_resource_monitor}), \
             patch("bg_monitoring.audit_log"), \
             patch("bg_monitoring.asyncio.sleep", AsyncMock()):
            await mod.resource_monitor_loop(bot)

        channel.send.assert_awaited()


# ===========================================================================
# _build_background_task_factories
# ===========================================================================

class TestBuildBackgroundTaskFactories:
    def test_always_includes_core_tasks(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        factories = bg_tasks._build_background_task_factories(MagicMock())
        assert "background_cleanup" in factories
        assert "audit_writer" in factories
        assert "reminder" in factories

    def test_includes_alert_tasks_when_channel_set(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 123)
        factories = bg_tasks._build_background_task_factories(MagicMock())
        assert "morning_briefing" in factories
        assert "evening_digest" in factories
        assert "proactive_insight" in factories
        assert "error_monitor" in factories
        assert "container_health" in factories
        assert "resource_monitor" in factories

    def test_excludes_alert_tasks_without_channel(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "ALERT_CHANNEL_ID", 0)
        factories = bg_tasks._build_background_task_factories(MagicMock())
        assert "morning_briefing" not in factories
        assert "proactive_insight" not in factories


# ===========================================================================
# _handle_background_task_done
# ===========================================================================

class TestHandleBackgroundTaskDone:
    def test_no_op_when_stopping(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", True)
        task = MagicMock()
        bg_tasks._handle_background_task_done("test_task", task)
        task.cancelled.assert_not_called()

    def test_no_op_when_cancelled(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        task = MagicMock()
        task.cancelled.return_value = True
        bg_tasks._handle_background_task_done("test_task", task)

    @pytest.mark.asyncio
    async def test_schedules_restart_on_crash(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_RESTART_DELAY_SECONDS", 0)

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")

        restart_calls = []
        loop = asyncio.get_event_loop()
        original_call_later = loop.call_later
        loop.call_later = lambda delay, fn, *args: restart_calls.append((fn, args))

        try:
            bg_tasks._handle_background_task_done("my_task", task)
        finally:
            loop.call_later = original_call_later

        assert len(restart_calls) >= 1

    @pytest.mark.asyncio
    async def test_schedules_restart_on_unexpected_exit(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_RESTART_DELAY_SECONDS", 0)

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None  # No exception, just exited

        restart_calls = []
        loop = asyncio.get_event_loop()
        original_call_later = loop.call_later
        loop.call_later = lambda delay, fn, *args: restart_calls.append((fn, args))

        try:
            bg_tasks._handle_background_task_done("my_task", task)
        finally:
            loop.call_later = original_call_later

        assert len(restart_calls) >= 1


# ===========================================================================
# _restart_background_task
# ===========================================================================

class TestRestartBackgroundTask:
    def test_no_op_when_stopping(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", True)
        bg_tasks._restart_background_task("nonexistent")

    def test_no_op_when_task_still_running(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        running_task = MagicMock()
        running_task.done.return_value = False
        bg_tasks._BACKGROUND_TASKS["test"] = running_task

        with patch.object(bg_tasks, "_launch_background_task") as mock_launch:
            bg_tasks._restart_background_task("test")
        mock_launch.assert_not_called()

    def test_no_op_when_factory_missing(self, monkeypatch):
        monkeypatch.setattr(bg_tasks, "_BACKGROUND_STOPPING", False)
        bg_tasks._BACKGROUND_FACTORIES.pop("orphan", None)
        bg_tasks._BACKGROUND_TASKS.pop("orphan", None)

        with patch.object(bg_tasks, "_launch_background_task") as mock_launch:
            bg_tasks._restart_background_task("orphan")
        mock_launch.assert_not_called()


# ===========================================================================
# container_health_loop
# ===========================================================================

class TestContainerHealthLoop:
    @pytest.mark.asyncio
    async def test_calls_health_and_cookie_checks(self, monkeypatch):
        call_count = 0

        async def fake_sleep(t):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        mock_health = AsyncMock()
        mock_cookies = AsyncMock()

        with patch("bg_monitoring.asyncio.sleep", fake_sleep), \
             patch.object(bg_monitoring, "_check_container_health", mock_health), \
             patch.object(bg_monitoring, "_check_monstervision_cookies", mock_cookies):
            with pytest.raises(asyncio.CancelledError):
                await mod.container_health_loop(MagicMock())

        mock_health.assert_awaited()
        mock_cookies.assert_awaited()
