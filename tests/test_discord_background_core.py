"""Tests for discord_background.py — self-heal parsing and signal helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import discord_background as mod
from alert_manager import reset_bounded_alert_cache

# ---------------------------------------------------------------------------
# _parse_heal_actions
# ---------------------------------------------------------------------------


class TestParseHealActions:
    def test_restart_safe_target(self):
        analysis = "SELF_HEAL: restart_container sonarr"
        actions = mod._parse_heal_actions(analysis)
        assert actions == [("restart_container", "sonarr")]

    def test_restart_unsafe_target_rejected(self):
        analysis = "SELF_HEAL: restart_container postgres"
        actions = mod._parse_heal_actions(analysis)
        assert actions == []

    def test_multiple_directives(self):
        analysis = (
            "Some analysis text\n"
            "SELF_HEAL: restart_container radarr\n"
            "More text\n"
            "SELF_HEAL: restart_container sabnzbd\n"
        )
        actions = mod._parse_heal_actions(analysis)
        assert len(actions) == 2
        assert ("restart_container", "radarr") in actions
        assert ("restart_container", "sabnzbd") in actions

    def test_copilot_fix_pending(self):
        analysis = "SELF_HEAL: copilot_fix check container logs for errors"
        actions = mod._parse_heal_actions(analysis)
        assert len(actions) == 1
        assert actions[0][0] == "copilot_fix_pending"
        assert "check container logs" in actions[0][1]

    def test_fix_qbit_download_path(self):
        analysis = "SELF_HEAL: fix_qbit_download_path"
        actions = mod._parse_heal_actions(analysis)
        assert actions == [("fix_qbit_download_path", "")]

    def test_fix_arr_remote_path(self):
        analysis = "SELF_HEAL: fix_arr_remote_path"
        actions = mod._parse_heal_actions(analysis)
        assert actions == [("fix_arr_remote_path", "")]

    def test_auto_cleanup_disk(self):
        analysis = "SELF_HEAL: auto_cleanup_disk"
        actions = mod._parse_heal_actions(analysis)
        assert actions == [("auto_cleanup_disk", "")]

    def test_no_directives(self):
        assert mod._parse_heal_actions("everything looks fine") == []

    def test_empty_string(self):
        assert mod._parse_heal_actions("") == []

    def test_copilot_fix_without_prompt_ignored(self):
        # copilot_fix with no follow-up text should yield nothing
        analysis = "SELF_HEAL: copilot_fix"
        actions = mod._parse_heal_actions(analysis)
        assert actions == []


# ---------------------------------------------------------------------------
# _execute_self_healing (mock external calls)
# ---------------------------------------------------------------------------


class TestExecuteSelfHealing:
    @pytest.mark.asyncio
    async def test_restart_calls_restart_container(self):
        with patch.object(mod, "_parse_heal_actions", return_value=[("restart_container", "sonarr")]), \
             patch("discord_background.restart_container", new_callable=AsyncMock, return_value="OK") as mock_rc, \
             patch("discord_background.audit_log"):
            cleaned, results = await mod._execute_self_healing("SELF_HEAL: restart_container sonarr")
            mock_rc.assert_awaited_once_with("sonarr")
            assert any("sonarr" in r for r in results)

    @pytest.mark.asyncio
    async def test_no_actions_returns_empty(self):
        cleaned, results = await mod._execute_self_healing("all good, nothing to heal")
        assert results == []


# ---------------------------------------------------------------------------
# _SAFE_RESTART_TARGETS sanity
# ---------------------------------------------------------------------------


class TestSafeRestartTargets:
    def test_expected_targets_present(self):
        for name in ("sonarr", "radarr", "sabnzbd", "qbittorrent"):
            assert name in mod._SAFE_RESTART_TARGETS

    def test_dangerous_targets_absent(self):
        for name in ("postgres", "redis", "nginx", "traefik"):
            assert name not in mod._SAFE_RESTART_TARGETS


class TestBackgroundTaskSupervisor:
    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, monkeypatch):
        async def idle_loop(*args, **kwargs):
            while True:
                await asyncio.sleep(3600)

        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(mod, "background_cleanup_loop", idle_loop)
        monkeypatch.setattr(mod, "audit_writer_loop", idle_loop)
        monkeypatch.setattr(mod, "reminder_loop", idle_loop)

        bot = object()
        first_count = mod.start_background_tasks(bot)
        first_task_ids = {name: id(task) for name, task in mod._BACKGROUND_TASKS.items()}
        second_count = mod.start_background_tasks(bot)
        second_task_ids = {name: id(task) for name, task in mod._BACKGROUND_TASKS.items()}

        assert first_count == 3
        assert second_count == 3
        assert first_task_ids == second_task_ids
        assert len(first_task_ids) == 3
        await mod.stop_background_tasks()

    @pytest.mark.asyncio
    async def test_crashed_task_is_restarted(self, monkeypatch):
        call_count = 0
        mock_collector = MagicMock()

        async def flaky_cleanup(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            while True:
                await asyncio.sleep(3600)

        async def idle_loop(*args, **kwargs):
            while True:
                await asyncio.sleep(3600)

        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(mod, "_BACKGROUND_RESTART_DELAY_SECONDS", 0)
        monkeypatch.setattr(mod, "background_cleanup_loop", flaky_cleanup)
        monkeypatch.setattr(mod, "audit_writer_loop", idle_loop)
        monkeypatch.setattr(mod, "reminder_loop", idle_loop)
        monkeypatch.setattr(mod, "get_collector", lambda: mock_collector)

        mod.start_background_tasks(object())
        await asyncio.sleep(0.05)

        assert call_count >= 2
        assert mock_collector.record_command.call_count >= 1
        first_call = mock_collector.record_command.call_args_list[0].kwargs
        assert first_call["command"] == "background:background_cleanup"
        assert first_call["workspace"] == "background"
        assert first_call["success"] is False
        assert first_call["error_type"] == "RuntimeError"
        await mod.stop_background_tasks()


class TestQualityDriftAlertRouting:
    @pytest.mark.asyncio
    async def test_severe_quality_drift_alert_is_deduped_within_cooldown(self, monkeypatch):
        reset_bounded_alert_cache()
        monkeypatch.setattr(mod, "ALERT_CHANNEL_ID", 123)
        channel = MagicMock(send=AsyncMock())
        bot = MagicMock(get_channel=MagicMock(return_value=channel))

        calibration_payload = {
            "available": True,
            "drift": {
                "baseline_available": True,
                "status": "drifted",
                "regressed_metrics": ["coverage_proxy", "evidence_completeness"],
                "severity": {"level": "severe", "severe": True, "score": 5, "reasons": ["coverage regression"]},
            },
        }

        with patch("dashboard.api_handlers._build_offline_quality_calibration_payload", return_value=calibration_payload):
            first = await mod._check_quality_drift_alert(bot)
            second = await mod._check_quality_drift_alert(bot)

        assert first is True
        assert second is False
        assert channel.send.await_count == 1
