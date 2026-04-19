"""Unit tests for approval_store.py — ApprovalStore CRUD, emergency stop, auth."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

from approval_models import APPROVAL_TTL, RiskLevel
from approval_store import (
    ApprovalStore,
    is_authorized_approver,
    is_emergency_stopped,
    set_emergency_stop,
)


@pytest.fixture
def store():
    return ApprovalStore()


def _create_req(store: ApprovalStore, **overrides):
    defaults = dict(
        action="restart_container",
        target="sonarr",
        risk_level=RiskLevel.HIGH,
        requester_id=1,
        requester_name="Alice",
        channel_id=99,
    )
    defaults.update(overrides)
    return store.create(**defaults)


class TestApprovalStoreCreate:
    def test_approval_store_unit_create_returns_request(self, store):
        req = _create_req(store)
        assert req is not None
        assert req.action == "restart_container"

    def test_request_id_is_8_hex_chars(self, store):
        req = _create_req(store)
        assert len(req.request_id) == 8
        int(req.request_id, 16)  # must be valid hex

    def test_create_increments_pending_count(self, store):
        assert store.pending_count == 0
        _create_req(store)
        assert store.pending_count == 1

    def test_two_creates_produce_different_ids(self, store):
        r1 = _create_req(store)
        r2 = _create_req(store)
        assert r1.request_id != r2.request_id

    def test_optional_fields_stored(self, store):
        req = _create_req(store, session_id="sess", plan_id="plan", task_id="task", detail="info")
        assert req.session_id == "sess"
        assert req.plan_id == "plan"
        assert req.task_id == "task"
        assert req.detail == "info"


class TestApprovalStoreGet:
    def test_get_returns_existing_request(self, store):
        req = _create_req(store)
        fetched = store.get(req.request_id)
        assert fetched is req

    def test_approval_store_unit_get_returns_none_for_unknown_id(self, store):
        assert store.get("doesnotexist") is None

    def test_get_auto_expires_stale_request(self, store):
        req = _create_req(store)
        req.created_at = time.monotonic() - APPROVAL_TTL - 5
        result = store.get(req.request_id)
        assert result is req
        assert result.resolved is True


class TestApprovalStoreResolve:
    def test_approve_sets_approved_true(self, store):
        req = _create_req(store)
        result = store.resolve(req.request_id, approved=True, resolver_id=2, resolver_name="Bob")
        assert result is req
        assert req.approved is True
        assert req.resolved is True

    def test_deny_sets_approved_false(self, store):
        req = _create_req(store)
        result = store.resolve(req.request_id, approved=False, resolver_id=2, resolver_name="Bob")
        assert result is req
        assert req.approved is False

    def test_approval_store_unit_resolve_already_resolved_returns_none(self, store):
        req = _create_req(store)
        store.resolve(req.request_id, approved=True, resolver_id=2, resolver_name="Bob")
        result = store.resolve(req.request_id, approved=False, resolver_id=3, resolver_name="Carol")
        assert result is None

    def test_resolve_expired_returns_none(self, store):
        req = _create_req(store)
        req.created_at = time.monotonic() - APPROVAL_TTL - 5
        result = store.resolve(req.request_id, approved=True, resolver_id=2, resolver_name="Bob")
        assert result is None

    def test_resolve_unknown_id_returns_none(self, store):
        assert store.resolve("badid", approved=True, resolver_id=2, resolver_name="X") is None

    def test_resolver_fields_set_on_approve(self, store):
        req = _create_req(store)
        store.resolve(req.request_id, approved=True, resolver_id=42, resolver_name="Diana")
        assert req.resolver_id == 42
        assert req.resolver_name == "Diana"


class TestApprovalStoreListAndCount:
    def test_list_pending_returns_active_requests(self, store):
        req = _create_req(store)
        assert req in store.list_pending()

    def test_resolved_request_not_in_list_pending(self, store):
        req = _create_req(store)
        store.resolve(req.request_id, approved=True, resolver_id=1, resolver_name="A")
        assert req not in store.list_pending()

    def test_pending_count_excludes_resolved(self, store):
        req = _create_req(store)
        assert store.pending_count == 1
        store.resolve(req.request_id, approved=True, resolver_id=1, resolver_name="A")
        assert store.pending_count == 0


class TestApprovalStoreCleanup:
    def test_cleanup_removes_old_resolved_requests(self, store):
        req = _create_req(store)
        req.resolved = True
        req.created_at = time.monotonic() - APPROVAL_TTL * 3
        store.cleanup_expired()
        assert store.get(req.request_id) is None

    def test_cleanup_keeps_fresh_requests(self, store):
        req = _create_req(store)
        store.cleanup_expired()
        assert store.get(req.request_id) is not None


class TestEmergencyStop:
    def test_initially_not_stopped(self):
        assert is_emergency_stopped() is False

    def test_set_true_activates(self):
        set_emergency_stop(True)
        assert is_emergency_stopped() is True

    def test_set_false_deactivates(self):
        set_emergency_stop(True)
        set_emergency_stop(False)
        assert is_emergency_stopped() is False

    def test_approval_store_unit_toggle_multiple_times(self):
        for expected in [True, False, True, False]:
            set_emergency_stop(expected)
            assert is_emergency_stopped() == expected


class TestIsAuthorizedApprover:
    def test_approval_store_unit_returns_bool(self):
        assert isinstance(is_authorized_approver(1), bool)

    def test_unknown_user_not_authorized_when_list_configured(self, monkeypatch):
        import approval_store as mod
        monkeypatch.setattr(mod, "ALLOWED_APPROVER_IDS", {999})
        assert is_authorized_approver(1) is False

    def test_known_user_is_authorized(self, monkeypatch):
        import approval_store as mod
        monkeypatch.setattr(mod, "ALLOWED_APPROVER_IDS", {42})
        assert is_authorized_approver(42) is True

    def test_empty_allowed_list_returns_false(self, monkeypatch):
        import approval_store as mod
        monkeypatch.setattr(mod, "ALLOWED_APPROVER_IDS", set())
        assert is_authorized_approver(42) is False
