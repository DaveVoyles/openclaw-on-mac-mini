"""Tests for alert_patreon.py — PatreonAlertManager cooldown and routing logic."""
from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from alert_patreon import AlertState, PatreonAlertManager
from patreon_recovery import PatreonHealthResult, PatreonHealthStatus


def _make_result(
    status=PatreonHealthStatus.CRITICAL,
    message="container is stopped",
    issues=None,
    action_items=None,
    metadata=None,
):
    return PatreonHealthResult(
        status=status,
        message=message,
        timestamp=datetime.now(),
        metadata=metadata or {},
        issues=issues or [],
        action_items=action_items or [],
    )


# ===========================================================================
# AlertState
# ===========================================================================

class TestAlertState:
    def test_alert_patreon_defaults(self):
        state = AlertState()
        assert state.last_alert_time == 0.0
        assert state.alert_count == 0


# ===========================================================================
# PatreonAlertManager._should_send_alert
# ===========================================================================

class TestShouldSendAlert:
    def setup_method(self):
        self.mgr = PatreonAlertManager()

    def test_ok_status_no_alert(self):
        result = _make_result(status=PatreonHealthStatus.OK, message="all good")
        ok, key = self.mgr._should_send_alert(result)
        assert ok is False

    def test_critical_first_alert(self):
        result = _make_result(status=PatreonHealthStatus.CRITICAL, message="container is stopped")
        ok, key = self.mgr._should_send_alert(result)
        assert ok is True
        assert key == "container_stopped"

    def test_warning_first_alert(self):
        result = _make_result(status=PatreonHealthStatus.WARNING, message="cookies expiring soon")
        ok, key = self.mgr._should_send_alert(result)
        assert ok is True
        assert key == "cookies_expiring"

    def test_api_unreachable_key(self):
        result = _make_result(status=PatreonHealthStatus.CRITICAL, message="api unreachable now")
        _, key = self.mgr._should_send_alert(result)
        assert key == "api_unreachable"

    def test_cookies_expired_key(self):
        result = _make_result(status=PatreonHealthStatus.CRITICAL, message="cookies expired yesterday")
        _, key = self.mgr._should_send_alert(result)
        assert key == "cookies_expired"

    def test_downloads_failing_key(self):
        result = _make_result(status=PatreonHealthStatus.WARNING, message="failed downloads detected")
        _, key = self.mgr._should_send_alert(result)
        assert key == "downloads_failing"

    def test_general_warning_fallback_key(self):
        result = _make_result(status=PatreonHealthStatus.WARNING, message="something is wrong")
        _, key = self.mgr._should_send_alert(result)
        assert key == "general_warning"

    def test_cooldown_suppresses_repeat(self):
        result = _make_result(status=PatreonHealthStatus.CRITICAL, message="container is stopped")
        # Simulate a recent alert
        state = AlertState()
        state.last_alert_time = time.time()
        state.last_status = PatreonHealthStatus.CRITICAL
        self.mgr._alert_states["container_stopped"] = state
        ok, _ = self.mgr._should_send_alert(result)
        assert ok is False

    def test_alert_after_cooldown_passes(self):
        from alert_patreon import ALERT_COOLDOWN_SECONDS
        result = _make_result(status=PatreonHealthStatus.CRITICAL, message="container is stopped")
        state = AlertState()
        state.last_alert_time = time.time() - ALERT_COOLDOWN_SECONDS - 1
        state.last_status = PatreonHealthStatus.CRITICAL
        self.mgr._alert_states["container_stopped"] = state
        ok, _ = self.mgr._should_send_alert(result)
        assert ok is True


# ===========================================================================
# PatreonAlertManager.reset_alert_state / get_alert_status
# ===========================================================================

class TestAlertManagerState:
    def setup_method(self):
        self.mgr = PatreonAlertManager()

    def test_reset_specific_key(self):
        state = AlertState()
        state.alert_count = 3
        self.mgr._alert_states["container_stopped"] = state
        self.mgr.reset_alert_state("container_stopped")
        assert "container_stopped" not in self.mgr._alert_states

    def test_reset_all(self):
        self.mgr._alert_states["key1"] = AlertState()
        self.mgr._alert_states["key2"] = AlertState()
        self.mgr.reset_alert_state()
        assert self.mgr._alert_states == {}

    def test_get_alert_status_empty(self):
        status = self.mgr.get_alert_status()
        assert isinstance(status, dict)
        assert status == {}

    def test_get_alert_status_with_state(self):
        state = AlertState()
        state.alert_count = 2
        state.last_status = PatreonHealthStatus.WARNING
        state.last_alert_time = time.time()
        self.mgr._alert_states["test_key"] = state
        status = self.mgr.get_alert_status()
        assert "test_key" in status
        assert status["test_key"]["alert_count"] == 2


# ===========================================================================
# send_alert_if_needed (mocked Discord)
# ===========================================================================

class TestSendAlertIfNeeded:
    @pytest.mark.asyncio
    async def test_no_discord_client_returns_false(self):
        mgr = PatreonAlertManager()
        result = _make_result()
        sent = await mgr.send_alert_if_needed(result, discord_client=None)
        assert sent is False

    @pytest.mark.asyncio
    async def test_ok_status_no_alert_sent(self):
        mgr = PatreonAlertManager()
        result = _make_result(status=PatreonHealthStatus.OK, message="all good")
        client = MagicMock()
        sent = await mgr.send_alert_if_needed(result, discord_client=client)
        assert sent is False

    @pytest.mark.asyncio
    async def test_sends_via_channel(self):
        mgr = PatreonAlertManager()
        result = _make_result()

        channel = MagicMock()
        channel.send = AsyncMock()

        client = MagicMock()
        client.get_channel = MagicMock(return_value=channel)

        sent = await mgr.send_alert_if_needed(result, discord_client=client, channel_id=12345)
        assert sent is True
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alert_state_updated_after_send(self):
        mgr = PatreonAlertManager()
        result = _make_result(message="container is stopped")

        channel = MagicMock()
        channel.send = AsyncMock()
        client = MagicMock()
        client.get_channel = MagicMock(return_value=channel)

        await mgr.send_alert_if_needed(result, discord_client=client, channel_id=99)
        assert "container_stopped" in mgr._alert_states
        assert mgr._alert_states["container_stopped"].alert_count == 1
