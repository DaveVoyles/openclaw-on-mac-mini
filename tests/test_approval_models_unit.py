"""Unit tests for approval_models.py — RiskLevel enum, ApprovalRequest dataclass."""

from __future__ import annotations

import os
import time

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

from approval_models import APPROVAL_TTL, ApprovalRequest, RiskLevel


class TestRiskLevel:
    def test_all_values_present(self):
        names = {r.name for r in RiskLevel}
        assert names == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    def test_enum_value_equals_name(self):
        assert RiskLevel.LOW.value == "LOW"
        assert RiskLevel.HIGH.value == "HIGH"

    def test_enum_comparison(self):
        assert RiskLevel.LOW != RiskLevel.HIGH
        assert RiskLevel.MEDIUM == RiskLevel.MEDIUM


class TestApprovalTTL:
    def test_ttl_is_positive(self):
        assert APPROVAL_TTL > 0

    def test_ttl_is_300_seconds(self):
        assert APPROVAL_TTL == 300


class TestApprovalRequest:
    def _make(self, **overrides):
        defaults = dict(
            request_id="test0001",
            action="restart_container",
            target="radarr",
            risk_level=RiskLevel.HIGH,
            requester_id=100,
            requester_name="Bob",
            channel_id=200,
        )
        defaults.update(overrides)
        return ApprovalRequest(**defaults)

    def test_fresh_request_not_expired(self):
        req = self._make()
        assert req.is_expired is False

    def test_old_request_is_expired(self):
        req = self._make(created_at=time.monotonic() - APPROVAL_TTL - 1)
        assert req.is_expired is True

    def test_not_expired_one_second_before_ttl(self):
        req = self._make(created_at=time.monotonic() - APPROVAL_TTL + 1)
        assert req.is_expired is False

    def test_age_seconds_non_negative(self):
        req = self._make()
        assert req.age_seconds >= 0

    def test_age_seconds_reflects_elapsed_time(self):
        req = self._make(created_at=time.monotonic() - 30)
        assert req.age_seconds >= 30

    def test_default_resolved_is_false(self):
        req = self._make()
        assert req.resolved is False

    def test_default_approved_is_false(self):
        req = self._make()
        assert req.approved is False

    def test_default_resolver_fields_are_none(self):
        req = self._make()
        assert req.resolver_id is None
        assert req.resolver_name is None

    def test_detail_field_stored(self):
        req = self._make(detail="dry-run output here")
        assert req.detail == "dry-run output here"

    def test_optional_id_fields_stored(self):
        req = self._make(session_id="s1", plan_id="p1", task_id="t1")
        assert req.session_id == "s1"
        assert req.plan_id == "p1"
        assert req.task_id == "t1"

    def test_risk_level_stored(self):
        req = self._make(risk_level=RiskLevel.CRITICAL)
        assert req.risk_level == RiskLevel.CRITICAL
