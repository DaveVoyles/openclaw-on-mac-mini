"""
Extended tests for approvals.py — emergency-stop thread safety,
store create/resolve lifecycle, expiry, and cleanup.
"""

import concurrent.futures
import time

import pytest

from approvals import (
    APPROVAL_TTL,
    ApprovalStore,
    RiskLevel,
    is_emergency_stopped,
    set_emergency_stop,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    return ApprovalStore()


@pytest.fixture(autouse=True)
def reset_emergency_stop():
    set_emergency_stop(False)
    yield
    set_emergency_stop(False)


# ---------------------------------------------------------------------------
# Emergency stop — extended
# ---------------------------------------------------------------------------


class TestEmergencyStopExtended:
    def test_emergency_stop_toggle(self):
        set_emergency_stop(True)
        assert is_emergency_stopped()
        set_emergency_stop(False)
        assert not is_emergency_stopped()

    def test_emergency_stop_thread_safety(self):
        """Concurrent set/get from multiple threads must not raise."""
        errors: list[Exception] = []

        def _toggle(active: bool):
            try:
                for _ in range(200):
                    set_emergency_stop(active)
                    is_emergency_stopped()
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_toggle, i % 2 == 0) for i in range(8)]
            concurrent.futures.wait(futures)

        assert errors == []
        # Final state is deterministic: last writer wins, but no crash
        assert isinstance(is_emergency_stopped(), bool)


# ---------------------------------------------------------------------------
# ApprovalStore — create
# ---------------------------------------------------------------------------


class TestApprovalStoreCreateExtended:
    def test_approval_store_create(self, store):
        req = store.create(
            action="deploy",
            target="prod",
            risk_level=RiskLevel.CRITICAL,
            requester_id=42,
            requester_name="Dave",
            channel_id=100,
            detail="v2.0 rollout",
        )
        assert req.action == "deploy"
        assert req.target == "prod"
        assert req.risk_level == RiskLevel.CRITICAL
        assert req.requester_id == 42
        assert req.requester_name == "Dave"
        assert req.channel_id == 100
        assert req.detail == "v2.0 rollout"
        assert not req.resolved
        assert not req.approved


# ---------------------------------------------------------------------------
# ApprovalStore — resolve
# ---------------------------------------------------------------------------


class TestApprovalStoreResolveExtended:
    def test_approval_store_resolve(self, store):
        req = store.create("scale", "workers", RiskLevel.MEDIUM, 1, "Alice", 10)
        result = store.resolve(req.request_id, approved=True, resolver_id=2, resolver_name="Bob")
        assert result is not None
        assert result.approved is True
        assert result.resolved is True
        assert result.resolver_name == "Bob"


# ---------------------------------------------------------------------------
# ApprovalStore — expire
# ---------------------------------------------------------------------------


class TestApprovalStoreExpire:
    def test_approval_store_expire(self, store):
        """Request expires after TTL — resolve returns None."""
        req = store.create("test", "x", RiskLevel.LOW, 1, "X", 1)
        req.created_at = time.monotonic() - APPROVAL_TTL - 1
        result = store.resolve(req.request_id, True, 2, "Y")
        assert result is None


# ---------------------------------------------------------------------------
# ApprovalStore — cleanup
# ---------------------------------------------------------------------------


class TestApprovalStoreCleanupExtended:
    def test_approval_store_cleanup(self, store):
        """cleanup_expired removes old resolved requests."""
        old = store.create("old", "a", RiskLevel.LOW, 1, "X", 1)
        old.created_at = time.monotonic() - APPROVAL_TTL * 2 - 1
        old.resolved = True

        fresh = store.create("fresh", "b", RiskLevel.LOW, 2, "Y", 2)

        store.cleanup_expired()

        assert store.get(old.request_id) is None
        assert store.get(fresh.request_id) is not None
