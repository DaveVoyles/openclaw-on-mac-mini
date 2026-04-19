"""
Tests for approvals.py — ApprovalStore, ApprovalRequest, emergency stop.

Focuses on the pure-Python store logic; the Discord UI (ApprovalView) is
not tested here since it requires a running Discord gateway.
"""

import time

import pytest

from approvals import (
    APPROVAL_TTL,
    ApprovalRequest,
    ApprovalStore,
    RiskLevel,
    is_authorized_approver,
    is_emergency_stopped,
    set_emergency_stop,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    return ApprovalStore()


# ---------------------------------------------------------------------------
# ApprovalRequest
# ---------------------------------------------------------------------------


class TestApprovalRequest:
    def _make(self, **overrides):
        defaults = dict(
            request_id="abc12345",
            action="restart_container",
            target="sonarr",
            risk_level=RiskLevel.HIGH,
            requester_id=123,
            requester_name="Alice",
            channel_id=456,
        )
        defaults.update(overrides)
        return ApprovalRequest(**defaults)

    def test_not_expired_when_fresh(self):
        req = self._make()
        assert not req.is_expired

    def test_expired_after_ttl(self):
        req = self._make(created_at=time.monotonic() - APPROVAL_TTL - 1)
        assert req.is_expired

    def test_not_expired_just_before_ttl(self):
        req = self._make(created_at=time.monotonic() - APPROVAL_TTL + 5)
        assert not req.is_expired

    def test_age_seconds_is_non_negative(self):
        req = self._make()
        assert req.age_seconds >= 0

    def test_age_seconds_increases_over_time(self):
        req = self._make(created_at=time.monotonic() - 10)
        assert req.age_seconds >= 10

    def test_default_resolved_false(self):
        req = self._make()
        assert not req.resolved

    def test_default_approved_false(self):
        req = self._make()
        assert not req.approved


# ---------------------------------------------------------------------------
# ApprovalStore — create
# ---------------------------------------------------------------------------


class TestApprovalStoreCreate:
    def test_approvals_create_returns_request(self, store):
        req = store.create("restart_container", "sonarr", RiskLevel.HIGH, 123, "Alice", 456)
        assert req.action == "restart_container"
        assert req.target == "sonarr"
        assert req.risk_level == RiskLevel.HIGH
        assert req.requester_id == 123
        assert req.requester_name == "Alice"
        assert req.channel_id == 456

    def test_create_generates_8char_id(self, store):
        req = store.create("test", "target", RiskLevel.LOW, 1, "X", 1)
        assert len(req.request_id) == 8

    def test_create_stores_request_in_pending(self, store):
        req = store.create("test", "target", RiskLevel.LOW, 1, "X", 1)
        assert store.get(req.request_id) is not None

    def test_create_request_not_resolved(self, store):
        req = store.create("test", "target", RiskLevel.MEDIUM, 1, "X", 1)
        assert not req.resolved

    def test_create_multiple_requests_unique_ids(self, store):
        req1 = store.create("test", "a", RiskLevel.LOW, 1, "X", 1)
        req2 = store.create("test", "b", RiskLevel.LOW, 2, "Y", 2)
        assert req1.request_id != req2.request_id

    def test_create_with_detail(self, store):
        req = store.create("test", "x", RiskLevel.CRITICAL, 1, "A", 1, detail="dry-run output")
        assert req.detail == "dry-run output"


# ---------------------------------------------------------------------------
# ApprovalStore — get
# ---------------------------------------------------------------------------


class TestApprovalStoreGet:
    def test_approvals_get_returns_none_for_unknown_id(self, store):
        assert store.get("nonexistent") is None

    def test_get_returns_request_by_id(self, store):
        req = store.create("test", "x", RiskLevel.LOW, 1, "A", 1)
        result = store.get(req.request_id)
        assert result is req

    def test_get_auto_expires_old_request(self, store):
        req = store.create("test", "x", RiskLevel.LOW, 1, "A", 1)
        req.created_at = time.monotonic() - APPROVAL_TTL - 1
        result = store.get(req.request_id)
        assert result.resolved  # Auto-marked resolved on get


# ---------------------------------------------------------------------------
# ApprovalStore — resolve
# ---------------------------------------------------------------------------


class TestApprovalStoreResolve:
    def test_resolve_approve_marks_resolved_and_approved(self, store):
        req = store.create("restart", "sonarr", RiskLevel.HIGH, 1, "Alice", 1)
        result = store.resolve(req.request_id, approved=True, resolver_id=2, resolver_name="Bob")
        assert result is not None
        assert result.resolved
        assert result.approved
        assert result.resolver_id == 2
        assert result.resolver_name == "Bob"

    def test_resolve_deny_marks_resolved_not_approved(self, store):
        req = store.create("restart", "sonarr", RiskLevel.HIGH, 1, "Alice", 1)
        result = store.resolve(req.request_id, approved=False, resolver_id=2, resolver_name="Bob")
        assert result is not None
        assert result.resolved
        assert not result.approved

    def test_resolve_unknown_request_returns_none(self, store):
        assert store.resolve("noexist", True, 1, "X") is None

    def test_approvals_resolve_already_resolved_returns_none(self, store):
        req = store.create("restart", "sonarr", RiskLevel.HIGH, 1, "Alice", 1)
        store.resolve(req.request_id, True, 2, "Bob")
        result = store.resolve(req.request_id, True, 3, "Charlie")
        assert result is None

    def test_resolve_expired_request_returns_none(self, store):
        req = store.create("restart", "sonarr", RiskLevel.HIGH, 1, "Alice", 1)
        req.created_at = time.monotonic() - APPROVAL_TTL - 1
        result = store.resolve(req.request_id, True, 2, "Bob")
        assert result is None


# ---------------------------------------------------------------------------
# ApprovalStore — pending_count and list_pending
# ---------------------------------------------------------------------------


class TestApprovalStorePending:
    def test_pending_count_starts_at_zero(self, store):
        assert store.pending_count == 0

    def test_pending_count_increases_on_create(self, store):
        store.create("test", "a", RiskLevel.LOW, 1, "X", 1)
        store.create("test", "b", RiskLevel.LOW, 2, "Y", 2)
        assert store.pending_count == 2

    def test_pending_count_decreases_after_resolve(self, store):
        req = store.create("test", "a", RiskLevel.LOW, 1, "X", 1)
        store.resolve(req.request_id, True, 2, "Y")
        assert store.pending_count == 0

    def test_pending_count_excludes_expired(self, store):
        req = store.create("test", "a", RiskLevel.LOW, 1, "X", 1)
        req.created_at = time.monotonic() - APPROVAL_TTL - 1
        assert store.pending_count == 0

    def test_list_pending_returns_only_active(self, store):
        r1 = store.create("test", "a", RiskLevel.LOW, 1, "X", 1)
        r2 = store.create("test", "b", RiskLevel.LOW, 2, "Y", 2)
        store.resolve(r1.request_id, True, 3, "Z")
        pending = store.list_pending()
        ids = [r.request_id for r in pending]
        assert r2.request_id in ids
        assert r1.request_id not in ids


# ---------------------------------------------------------------------------
# ApprovalStore — cleanup_expired
# ---------------------------------------------------------------------------


class TestApprovalStoreCleanup:
    def test_cleanup_removes_very_old_entries(self, store):
        req = store.create("test", "a", RiskLevel.LOW, 1, "X", 1)
        # Must be older than 2*APPROVAL_TTL to be deleted
        req.created_at = time.monotonic() - APPROVAL_TTL * 2 - 1
        store.create("other", "b", RiskLevel.LOW, 2, "Y", 2)  # Active entry
        store.cleanup_expired()
        assert store.get(req.request_id) is None

    def test_cleanup_keeps_recent_entries(self, store):
        req = store.create("test", "a", RiskLevel.LOW, 1, "X", 1)
        store.cleanup_expired()
        assert store.get(req.request_id) is not None


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------


class TestEmergencyStop:
    def test_default_not_stopped(self):
        assert not is_emergency_stopped()

    def test_activate_emergency_stop(self):
        set_emergency_stop(True)
        assert is_emergency_stopped()

    def test_deactivate_emergency_stop(self):
        set_emergency_stop(True)
        set_emergency_stop(False)
        assert not is_emergency_stopped()

    def test_approvals_toggle_multiple_times(self):
        set_emergency_stop(True)
        set_emergency_stop(False)
        set_emergency_stop(True)
        assert is_emergency_stopped()


class TestApprovalAuthorization:
    def test_is_authorized_approver_true_when_in_allowlist(self, monkeypatch):
        import approvals as _approvals_mod
        monkeypatch.setattr(_approvals_mod, "ALLOWED_APPROVER_IDS", {1234, 5678})
        assert _approvals_mod.is_authorized_approver(1234)

    def test_is_authorized_approver_false_when_not_in_allowlist(self, monkeypatch):
        monkeypatch.setattr("approvals.ALLOWED_APPROVER_IDS", {1234, 5678})
        assert not is_authorized_approver(42)

    def test_is_authorized_approver_false_when_allowlist_empty(self, monkeypatch):
        monkeypatch.setattr("approvals.ALLOWED_APPROVER_IDS", set())
        assert not is_authorized_approver(1234)
