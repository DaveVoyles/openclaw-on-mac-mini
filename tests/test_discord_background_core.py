"""Tests for discord_background.py — self-heal parsing and signal helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import discord_background as mod


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
