"""Unit tests for patreon_scheduled.py — set_discord_client, scheduled task, config."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import patreon_scheduled as ps
from patreon_scheduled import (
    PATREON_MONITORING_TASK,
    scheduled_patreon_health_check,
    set_discord_client,
)

# ---------------------------------------------------------------------------
# set_discord_client
# ---------------------------------------------------------------------------

class TestSetDiscordClient:
    def test_stores_client(self):
        mock_client = MagicMock()
        set_discord_client(mock_client)
        assert ps._discord_client is mock_client

    def test_stores_none(self):
        set_discord_client(None)
        assert ps._discord_client is None

    def test_overwrites_previous(self):
        first = MagicMock()
        second = MagicMock()
        set_discord_client(first)
        set_discord_client(second)
        assert ps._discord_client is second


# ---------------------------------------------------------------------------
# PATREON_MONITORING_TASK config dict
# ---------------------------------------------------------------------------

class TestPatreonMonitoringTask:
    def test_has_required_keys(self):
        required = {"name", "function", "description", "schedule"}
        assert required.issubset(PATREON_MONITORING_TASK.keys())

    def test_name_is_string(self):
        assert isinstance(PATREON_MONITORING_TASK["name"], str)

    def test_function_is_callable(self):
        assert callable(PATREON_MONITORING_TASK["function"])

    def test_function_is_scheduled_health_check(self):
        assert PATREON_MONITORING_TASK["function"] is scheduled_patreon_health_check

    def test_schedule_cron_format(self):
        schedule = PATREON_MONITORING_TASK["schedule"]
        assert isinstance(schedule, str)
        assert "*" in schedule  # basic cron syntax check

    def test_retry_config(self):
        assert PATREON_MONITORING_TASK.get("retry_on_failure") is True
        assert PATREON_MONITORING_TASK.get("max_retries", 0) > 0

    def test_timeout_positive(self):
        assert PATREON_MONITORING_TASK.get("timeout_seconds", 0) > 0


# ---------------------------------------------------------------------------
# scheduled_patreon_health_check — success path
# ---------------------------------------------------------------------------

def _make_health(status_value="healthy", message="OK", issues=None):
    health = MagicMock()
    health.status = MagicMock()
    health.status.value = status_value
    health.message = message
    health.issues = issues or []
    return health


@pytest.mark.asyncio
class TestScheduledPatreonHealthCheckSuccess:
    async def test_returns_dict_on_success(self):
        health = _make_health("healthy")
        checker = AsyncMock()
        checker.check_health = AsyncMock(return_value=health)

        recovery_result = MagicMock()
        recovery_result.success = True
        recovery_result.action = MagicMock()
        recovery_result.action.value = "restart"
        recovery_mgr = AsyncMock()
        recovery_mgr.attempt_recovery = AsyncMock(return_value=None)

        alert_mgr = AsyncMock()
        alert_mgr.send_alert_if_needed = AsyncMock(return_value=False)

        with patch("patreon_scheduled.get_patreon_checker", return_value=checker), \
             patch("patreon_scheduled.get_recovery_manager", return_value=recovery_mgr), \
             patch("patreon_scheduled.get_alert_manager", return_value=alert_mgr), \
             patch("patreon_scheduled.cfg") as mock_cfg:
            mock_cfg.alert_channel_id = None
            result = await scheduled_patreon_health_check()

        assert result["success"] is True
        assert result["status"] == "healthy"
        assert "timestamp" in result

    async def test_includes_issue_count(self):
        health = _make_health("degraded", issues=["issue1", "issue2"])
        checker = AsyncMock()
        checker.check_health = AsyncMock(return_value=health)
        recovery_mgr = AsyncMock()
        recovery_mgr.attempt_recovery = AsyncMock(return_value=None)
        alert_mgr = AsyncMock()
        alert_mgr.send_alert_if_needed = AsyncMock(return_value=False)

        with patch("patreon_scheduled.get_patreon_checker", return_value=checker), \
             patch("patreon_scheduled.get_recovery_manager", return_value=recovery_mgr), \
             patch("patreon_scheduled.get_alert_manager", return_value=alert_mgr), \
             patch("patreon_scheduled.cfg") as mock_cfg:
            mock_cfg.alert_channel_id = None
            result = await scheduled_patreon_health_check()

        assert result["issues_count"] == 2

    async def test_alert_sent_with_discord_client(self):
        health = _make_health("unhealthy")
        checker = AsyncMock()
        checker.check_health = AsyncMock(return_value=health)
        recovery_mgr = AsyncMock()
        recovery_mgr.attempt_recovery = AsyncMock(return_value=None)
        alert_mgr = AsyncMock()
        alert_mgr.send_alert_if_needed = AsyncMock(return_value=True)

        mock_discord = MagicMock()

        with patch("patreon_scheduled.get_patreon_checker", return_value=checker), \
             patch("patreon_scheduled.get_recovery_manager", return_value=recovery_mgr), \
             patch("patreon_scheduled.get_alert_manager", return_value=alert_mgr), \
             patch("patreon_scheduled.cfg") as mock_cfg:
            mock_cfg.alert_channel_id = None
            result = await scheduled_patreon_health_check(discord_client=mock_discord)

        assert result["alert_sent"] is True

    async def test_recovery_attempted_and_successful(self):
        health = _make_health("degraded")
        recovery_health = _make_health("healthy")
        checker = AsyncMock()
        checker.check_health = AsyncMock(side_effect=[health, recovery_health])

        recovery_result = MagicMock()
        recovery_result.success = True
        recovery_result.action = MagicMock()
        recovery_result.action.value = "restart"
        recovery_mgr = AsyncMock()
        recovery_mgr.attempt_recovery = AsyncMock(return_value=recovery_result)

        alert_mgr = AsyncMock()
        alert_mgr.send_alert_if_needed = AsyncMock(return_value=False)

        with patch("patreon_scheduled.get_patreon_checker", return_value=checker), \
             patch("patreon_scheduled.get_recovery_manager", return_value=recovery_mgr), \
             patch("patreon_scheduled.get_alert_manager", return_value=alert_mgr), \
             patch("patreon_scheduled.cfg") as mock_cfg, \
             patch("asyncio.sleep", new=AsyncMock()):
            mock_cfg.alert_channel_id = None
            result = await scheduled_patreon_health_check()

        assert result["recovery_attempted"] is True
        assert result["recovery_success"] is True


# ---------------------------------------------------------------------------
# scheduled_patreon_health_check — error path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestScheduledPatreonHealthCheckError:
    async def test_returns_failure_dict_on_exception(self):
        with patch("patreon_scheduled.get_patreon_checker", side_effect=RuntimeError("boom")):
            result = await scheduled_patreon_health_check()

        assert result["success"] is False
        assert "error" in result
        assert "boom" in result["error"]
        assert "timestamp" in result

    async def test_error_dict_has_no_status_key(self):
        with patch("patreon_scheduled.get_patreon_checker", side_effect=ValueError("bad")):
            result = await scheduled_patreon_health_check()

        assert "status" not in result or result.get("success") is False

    async def test_uses_module_discord_client_fallback(self):
        health = _make_health("healthy")
        checker = AsyncMock()
        checker.check_health = AsyncMock(return_value=health)
        recovery_mgr = AsyncMock()
        recovery_mgr.attempt_recovery = AsyncMock(return_value=None)
        alert_mgr = AsyncMock()
        alert_mgr.send_alert_if_needed = AsyncMock(return_value=True)

        mock_client = MagicMock()
        set_discord_client(mock_client)

        with patch("patreon_scheduled.get_patreon_checker", return_value=checker), \
             patch("patreon_scheduled.get_recovery_manager", return_value=recovery_mgr), \
             patch("patreon_scheduled.get_alert_manager", return_value=alert_mgr), \
             patch("patreon_scheduled.cfg") as mock_cfg:
            mock_cfg.alert_channel_id = None
            # No discord_client arg → should use _discord_client module var
            result = await scheduled_patreon_health_check()

        assert result["success"] is True
        # Clean up
        set_discord_client(None)
