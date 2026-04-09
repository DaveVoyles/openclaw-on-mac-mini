"""Tests for patreon_recovery.py — recovery action selection and history."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from patreon_recovery import (
    PatreonHealthResult,
    PatreonHealthStatus,
    PatreonRecoveryManager,
    RecoveryAction,
    RecoveryResult,
    get_recovery_manager,
)


def _health(status, message="", metadata=None, issues=None, action_items=None):
    return PatreonHealthResult(
        status=status,
        message=message,
        timestamp=datetime.now(),
        metadata=metadata or {},
        issues=issues or [],
        action_items=action_items or [],
    )


# ===========================================================================
# RecoveryResult dataclass
# ===========================================================================

class TestRecoveryResult:
    def test_fields(self):
        r = RecoveryResult(
            action=RecoveryAction.NONE,
            success=True,
            message="ok",
            timestamp=datetime.now(),
        )
        assert r.action == RecoveryAction.NONE
        assert r.success is True
        assert r.details == ""


# ===========================================================================
# _determine_recovery_action
# ===========================================================================

class TestDetermineRecoveryAction:
    def setup_method(self):
        self.mgr = PatreonRecoveryManager()

    def test_ok_status_returns_none(self):
        result = _health(PatreonHealthStatus.OK)
        assert self.mgr._determine_recovery_action(result) == RecoveryAction.NONE

    def test_stopped_container_starts(self):
        result = _health(PatreonHealthStatus.CRITICAL, metadata={"container_status": "stopped"})
        assert self.mgr._determine_recovery_action(result) == RecoveryAction.START_CONTAINER

    def test_unhealthy_container_restarts(self):
        result = _health(PatreonHealthStatus.CRITICAL, metadata={"container_status": "unhealthy"})
        assert self.mgr._determine_recovery_action(result) == RecoveryAction.RESTART_CONTAINER

    def test_api_unreachable_running_container_restarts(self):
        result = _health(
            PatreonHealthStatus.CRITICAL,
            metadata={"container_status": "running", "api_available": False},
        )
        assert self.mgr._determine_recovery_action(result) == RecoveryAction.RESTART_CONTAINER

    def test_old_cookies_retry_downloads(self):
        result = _health(
            PatreonHealthStatus.WARNING,
            metadata={"cookie_age_hours": 80},  # 72-96h window
        )
        assert self.mgr._determine_recovery_action(result) == RecoveryAction.RETRY_DOWNLOADS

    def test_failed_downloads_fresh_cookies_retry(self):
        result = _health(
            PatreonHealthStatus.WARNING,
            metadata={"failed_downloads": 3, "cookie_age_hours": 24},
        )
        assert self.mgr._determine_recovery_action(result) == RecoveryAction.RETRY_DOWNLOADS

    def test_no_clear_action_returns_none(self):
        result = _health(PatreonHealthStatus.WARNING, metadata={})
        assert self.mgr._determine_recovery_action(result) == RecoveryAction.NONE


# ===========================================================================
# attempt_recovery
# ===========================================================================

class TestAttemptRecovery:
    @pytest.mark.asyncio
    async def test_ok_status_returns_none(self):
        mgr = PatreonRecoveryManager()
        result = _health(PatreonHealthStatus.OK)
        out = await mgr.attempt_recovery(result)
        assert out is None

    @pytest.mark.asyncio
    async def test_no_action_returns_none(self):
        mgr = PatreonRecoveryManager()
        result = _health(PatreonHealthStatus.WARNING, metadata={})
        out = await mgr.attempt_recovery(result)
        assert out is None

    @pytest.mark.asyncio
    async def test_executes_start_container_action(self):
        mgr = PatreonRecoveryManager()
        health = _health(PatreonHealthStatus.CRITICAL, metadata={"container_status": "stopped"})
        mock_result = RecoveryResult(
            action=RecoveryAction.START_CONTAINER,
            success=True,
            message="started",
            timestamp=datetime.now(),
        )
        with patch.object(mgr, "_start_container", AsyncMock(return_value=mock_result)):
            out = await mgr.attempt_recovery(health)
        assert out is not None
        assert out.action == RecoveryAction.START_CONTAINER
        assert len(mgr._recovery_history) == 1

    @pytest.mark.asyncio
    async def test_history_capped_at_max(self):
        mgr = PatreonRecoveryManager()
        mgr._max_history = 3
        # Fill history to exactly max
        for i in range(3):
            mgr._recovery_history.append(
                RecoveryResult(RecoveryAction.NONE, True, f"msg{i}", datetime.now())
            )
        health = _health(PatreonHealthStatus.CRITICAL, metadata={"container_status": "stopped"})
        mock_result = RecoveryResult(
            action=RecoveryAction.START_CONTAINER,
            success=True,
            message="ok",
            timestamp=datetime.now(),
        )
        with patch.object(mgr, "_start_container", AsyncMock(return_value=mock_result)):
            await mgr.attempt_recovery(health)
        # After append + pop: history stays at max
        assert len(mgr._recovery_history) == mgr._max_history


# ===========================================================================
# get_recovery_history / clear_history
# ===========================================================================

class TestRecoveryHistory:
    def test_empty_initially(self):
        mgr = PatreonRecoveryManager()
        assert mgr.get_recovery_history() == []

    def test_limit_respected(self):
        mgr = PatreonRecoveryManager()
        for i in range(20):
            mgr._recovery_history.append(
                RecoveryResult(RecoveryAction.NONE, True, f"msg{i}", datetime.now())
            )
        assert len(mgr.get_recovery_history(limit=5)) == 5

    def test_clear_history(self):
        mgr = PatreonRecoveryManager()
        mgr._recovery_history.append(
            RecoveryResult(RecoveryAction.NONE, True, "x", datetime.now())
        )
        mgr.clear_history()
        assert mgr.get_recovery_history() == []


# ===========================================================================
# get_recovery_manager singleton
# ===========================================================================

class TestGetRecoveryManager:
    def test_returns_instance(self):
        import patreon_recovery as mod
        mod._recovery_manager = None  # reset singleton
        mgr = get_recovery_manager()
        assert isinstance(mgr, PatreonRecoveryManager)

    def test_returns_same_instance_twice(self):
        import patreon_recovery as mod
        mod._recovery_manager = None
        mgr1 = get_recovery_manager()
        mgr2 = get_recovery_manager()
        assert mgr1 is mgr2
