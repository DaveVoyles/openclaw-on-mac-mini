"""Tests for discord_background.py — self-heal parsing and signal helpers.

After the modularization of discord_background.py into sub-modules, patching
must target the sub-module where the attribute is *defined*, not the
discord_background re-export shim.

Sub-module mapping:
  bg_briefing  — send_morning_briefing, send_evening_digest, ALERT_CHANNEL_ID (briefing)
  bg_healing   — _parse_heal_actions, _execute_self_healing, _SAFE_RESTART_TARGETS,
                 _check_quality_drift_alert, restart_container, ALERT_CHANNEL_ID (healing)
  bg_tasks     — start_background_tasks, stop_background_tasks, _BACKGROUND_TASKS,
                 _BACKGROUND_RESTART_DELAY_SECONDS, get_collector, ALERT_CHANNEL_ID (tasks)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bg_briefing as briefing_mod
import bg_healing as healing_mod
import bg_tasks as tasks_mod
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
        # restart_container and audit_log live in bg_healing, not discord_background
        with patch.object(healing_mod, "_parse_heal_actions", return_value=[("restart_container", "sonarr")]), \
             patch("bg_healing.restart_container", new_callable=AsyncMock, return_value="OK") as mock_rc, \
             patch("bg_healing.audit_log"):
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
            assert name in healing_mod._SAFE_RESTART_TARGETS

    def test_dangerous_targets_absent(self):
        for name in ("postgres", "redis", "nginx", "traefik"):
            assert name not in healing_mod._SAFE_RESTART_TARGETS


class TestBackgroundTaskSupervisor:
    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, monkeypatch):
        async def idle_loop(*args, **kwargs):
            while True:
                await asyncio.sleep(3600)

        # Patch on the sub-modules where these are actually read/called
        monkeypatch.setattr(tasks_mod, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(healing_mod, "background_cleanup_loop", idle_loop)
        monkeypatch.setattr(healing_mod, "audit_writer_loop", idle_loop)
        monkeypatch.setattr(tasks_mod, "reminder_loop", idle_loop)

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

        monkeypatch.setattr(tasks_mod, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(tasks_mod, "_BACKGROUND_RESTART_DELAY_SECONDS", 0)
        monkeypatch.setattr(healing_mod, "background_cleanup_loop", flaky_cleanup)
        monkeypatch.setattr(healing_mod, "audit_writer_loop", idle_loop)
        monkeypatch.setattr(tasks_mod, "reminder_loop", idle_loop)
        monkeypatch.setattr(tasks_mod, "get_collector", lambda: mock_collector)

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
        # _check_quality_drift_alert reads ALERT_CHANNEL_ID from bg_healing
        monkeypatch.setattr(healing_mod, "ALERT_CHANNEL_ID", 123)
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


# ---------------------------------------------------------------------------
# send_morning_briefing
# ---------------------------------------------------------------------------


class TestSendMorningBriefing:
    def _make_bot(self, channel=None):
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=channel)
        return bot

    def _make_channel(self):
        ch = MagicMock()
        ch.send = AsyncMock()
        return ch

    @pytest.mark.asyncio
    async def test_happy_path_sends_embed(self, monkeypatch):
        channel = self._make_channel()
        bot = self._make_bot(channel)
        # Patch on bg_briefing where ALERT_CHANNEL_ID and helpers are defined
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 123)
        monkeypatch.setattr(briefing_mod, "check_arr_health", AsyncMock(return_value="all healthy"))
        monkeypatch.setattr(briefing_mod, "get_download_queue", AsyncMock(return_value="2 items queued"))
        monkeypatch.setattr(briefing_mod, "get_weather", AsyncMock(return_value="Sunny 72°F"))
        monkeypatch.setattr(briefing_mod, "get_system_stats", AsyncMock(return_value="CPU 5%"))
        monkeypatch.setattr(briefing_mod, "llm_chat", AsyncMock(return_value=("Good morning!", {}, "gemini-pro")))
        monkeypatch.setattr(briefing_mod, "audit_log", MagicMock())

        await mod.send_morning_briefing(bot)

        channel.send.assert_awaited_once()
        call_kwargs = channel.send.await_args.kwargs
        assert "embed" in call_kwargs
        assert "Morning Briefing" in call_kwargs["embed"].title

    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        bot = self._make_bot(channel=None)
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 0)

        await mod.send_morning_briefing(bot)

        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_early(self, monkeypatch):
        bot = self._make_bot(channel=None)  # get_channel returns None
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 456)

        await mod.send_morning_briefing(bot)

        bot.get_channel.assert_called_once_with(456)

    @pytest.mark.asyncio
    async def test_llm_error_is_caught(self, monkeypatch):
        channel = self._make_channel()
        bot = self._make_bot(channel)
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 123)
        monkeypatch.setattr(briefing_mod, "check_arr_health", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "get_download_queue", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "get_weather", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "get_system_stats", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "llm_chat", AsyncMock(side_effect=Exception("LLM down")))
        monkeypatch.setattr(briefing_mod, "audit_log", MagicMock())

        # Should not propagate the exception
        await mod.send_morning_briefing(bot)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_channel_override_used_when_provided(self, monkeypatch):
        override_channel = self._make_channel()
        bot = self._make_bot()
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(briefing_mod, "check_arr_health", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "get_download_queue", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "get_weather", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "get_system_stats", AsyncMock(return_value="ok"))
        monkeypatch.setattr(briefing_mod, "llm_chat", AsyncMock(return_value=("Good morning!", {}, "gemini-pro")))
        monkeypatch.setattr(briefing_mod, "audit_log", MagicMock())

        await mod.send_morning_briefing(bot, channel_override=override_channel)

        bot.get_channel.assert_not_called()
        override_channel.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# send_evening_digest
# ---------------------------------------------------------------------------


class TestSendEveningDigest:
    def _make_bot(self, channel=None):
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=channel)
        return bot

    def _make_channel(self):
        ch = MagicMock()
        ch.send = AsyncMock()
        return ch

    @pytest.mark.asyncio
    async def test_happy_path_sends_embed(self, monkeypatch):
        channel = self._make_channel()
        bot = self._make_bot(channel)
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(briefing_mod, "get_system_stats", AsyncMock(return_value="CPU 3%"))
        monkeypatch.setattr(briefing_mod, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(briefing_mod, "audit_log", MagicMock())

        await mod.send_evening_digest(bot)

        channel.send.assert_awaited_once()
        call_kwargs = channel.send.await_args.kwargs
        assert "embed" in call_kwargs
        assert "Digest" in call_kwargs["embed"].title

    @pytest.mark.asyncio
    async def test_no_alert_channel_id_returns_early(self, monkeypatch):
        bot = self._make_bot(channel=None)
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 0)

        await mod.send_evening_digest(bot)

        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_not_found_returns_early(self, monkeypatch):
        bot = self._make_bot(channel=None)
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 999)

        await mod.send_evening_digest(bot)

        bot.get_channel.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_channel_override_bypasses_bot_lookup(self, monkeypatch):
        override_channel = self._make_channel()
        bot = self._make_bot()
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 0)
        monkeypatch.setattr(briefing_mod, "get_system_stats", AsyncMock(return_value="CPU 2%"))
        monkeypatch.setattr(briefing_mod, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(briefing_mod, "audit_log", MagicMock())

        await mod.send_evening_digest(bot, channel_override=override_channel)

        bot.get_channel.assert_not_called()
        override_channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_system_stats_failure_still_sends(self, monkeypatch):
        """Even if get_system_stats raises, the embed is still sent."""
        channel = self._make_channel()
        bot = self._make_bot(channel)
        monkeypatch.setattr(briefing_mod, "ALERT_CHANNEL_ID", 789)
        monkeypatch.setattr(briefing_mod, "get_system_stats", AsyncMock(side_effect=Exception("stats down")))
        monkeypatch.setattr(briefing_mod, "get_download_queue", AsyncMock(return_value="no active downloads"))
        monkeypatch.setattr(briefing_mod, "audit_log", MagicMock())

        await mod.send_evening_digest(bot)

        channel.send.assert_awaited_once()
