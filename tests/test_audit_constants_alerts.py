"""Tests for audit.py, constants.py, and alert_manager.py pure functions."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import alert_manager as am_mod
import audit as audit_mod
from alert_manager import (
    DEFAULT_COOLDOWN,
    reset_bounded_alert_cache,
    should_route_bounded_alert,
)
from audit import _audit_buffer, audit_log


# ===========================================================================
# audit.py
# ===========================================================================

class TestAuditLog:
    def setup_method(self):
        _audit_buffer.clear()

    def test_appends_to_buffer(self):
        audit_log("user", "test_action", "details")
        assert len(_audit_buffer) == 1

    def test_entry_has_required_fields(self):
        audit_log("user", "cmd", "some detail", result="ok")
        entry = _audit_buffer[-1]
        assert "ts" in entry
        assert entry["action"] == "cmd"
        assert entry["detail"] == "some detail"
        assert entry["result"] == "ok"

    def test_none_user_defaults_to_system(self):
        audit_log(None, "action")
        assert _audit_buffer[-1]["user"] == "system"
        assert _audit_buffer[-1]["user_id"] == "0"

    def test_user_object_with_id(self):
        user = MagicMock()
        user.id = 42
        user.__str__ = lambda self: "TestUser"
        audit_log(user, "login")
        entry = _audit_buffer[-1]
        assert entry["user_id"] == "42"

    def test_default_result_is_success(self):
        audit_log("u", "act")
        assert _audit_buffer[-1]["result"] == "success"

    def test_timestamp_is_iso_format(self):
        audit_log("u", "act")
        ts = _audit_buffer[-1]["ts"]
        # ISO 8601 with timezone
        assert "T" in ts and ("+" in ts or "Z" in ts or ts.endswith("+00:00"))

    def test_buffer_respects_maxlen(self):
        for i in range(10_001):
            audit_log("u", f"action_{i}")
        assert len(_audit_buffer) == 10_000


# ===========================================================================
# constants.py
# ===========================================================================

class TestConstants:
    def test_discord_limits_are_positive(self):
        from constants import DISCORD_MESSAGE_LIMIT, EMBED_DESC_LIMIT, EMBED_SPLIT_LIMIT
        assert DISCORD_MESSAGE_LIMIT > 0
        assert EMBED_DESC_LIMIT > 0
        assert EMBED_SPLIT_LIMIT > 0

    def test_embed_split_less_than_desc(self):
        from constants import EMBED_DESC_LIMIT, EMBED_SPLIT_LIMIT
        assert EMBED_SPLIT_LIMIT < EMBED_DESC_LIMIT

    def test_intervals_positive(self):
        from constants import AUDIT_FLUSH_INTERVAL, BRIEFING_CHECK_INTERVAL, CLEANUP_INTERVAL
        assert AUDIT_FLUSH_INTERVAL > 0
        assert CLEANUP_INTERVAL > 0
        assert BRIEFING_CHECK_INTERVAL > 0

    def test_max_file_size_is_mb_range(self):
        from constants import MAX_FILE_SIZE
        assert MAX_FILE_SIZE >= 1024 * 1024  # at least 1 MB


# ===========================================================================
# alert_manager.py — should_route_bounded_alert
# ===========================================================================

class TestShouldRouteBoundedAlert:
    def setup_method(self):
        reset_bounded_alert_cache()

    def test_first_call_routes(self):
        ok, reason = should_route_bounded_alert("key1", fingerprint="fp1")
        assert ok is True
        assert reason == "routed"

    def test_duplicate_within_cooldown_blocked(self):
        now = time.time()
        should_route_bounded_alert("key1", fingerprint="fp1", now_ts=now)
        ok, reason = should_route_bounded_alert("key1", fingerprint="fp1", now_ts=now + 10)
        assert ok is False
        assert reason == "duplicate_within_cooldown"

    def test_different_fingerprint_within_cooldown_blocked(self):
        now = time.time()
        should_route_bounded_alert("key1", fingerprint="fp1", now_ts=now)
        ok, reason = should_route_bounded_alert("key1", fingerprint="fp2", now_ts=now + 10)
        assert ok is False
        assert reason == "cooldown_active"

    def test_after_cooldown_routes_again(self):
        now = time.time()
        should_route_bounded_alert("key1", fingerprint="fp1", now_ts=now)
        ok, reason = should_route_bounded_alert(
            "key1", fingerprint="fp1", now_ts=now + DEFAULT_COOLDOWN + 1
        )
        assert ok is True

    def test_different_keys_independent(self):
        now = time.time()
        should_route_bounded_alert("key1", fingerprint="fp1", now_ts=now)
        ok, _ = should_route_bounded_alert("key2", fingerprint="fp1", now_ts=now + 10)
        assert ok is True

    def test_empty_key_normalizes(self):
        ok, reason = should_route_bounded_alert("", fingerprint="fp")
        assert ok is True

    def test_zero_cooldown_always_routes(self):
        now = time.time()
        should_route_bounded_alert("key", fingerprint="fp", cooldown_seconds=0, now_ts=now)
        ok, _ = should_route_bounded_alert("key", fingerprint="fp", cooldown_seconds=0, now_ts=now + 1)
        assert ok is True

    def test_reset_clears_cache(self):
        now = time.time()
        should_route_bounded_alert("key1", fingerprint="fp1", now_ts=now)
        reset_bounded_alert_cache()
        ok, _ = should_route_bounded_alert("key1", fingerprint="fp1", now_ts=now + 10)
        assert ok is True


# ===========================================================================
# alert_manager.py — format_text_alert (smoke test)
# ===========================================================================

def _make_trend_analysis(topic="bitcoin", category="crypto"):
    from trend_tracker import TrendAnalysis
    return TrendAnalysis(
        topic=topic,
        category=category,
        current_volume=100,
        avg_volume_24h=80,
        avg_volume_7d=75,
        volume_change_pct=25.0,
        current_sentiment=0.7,
        sentiment_change_24h=0.1,
        velocity=1.5,
        is_trending=True,
        is_spike=False,
        is_breakout=False,
        trend_direction="up",
        z_score=2.5,
        peak_time=None,
        sources=[],
    )


class TestFormatTextAlert:
    def test_returns_non_empty_string(self):
        from alert_manager import format_text_alert
        analysis = _make_trend_analysis()
        text = format_text_alert(analysis)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_alert_type_in_output(self):
        from alert_manager import format_text_alert
        analysis = _make_trend_analysis("eth")
        text = format_text_alert(analysis, alert_type="SPIKE")
        assert "SPIKE" in text or "eth" in text.lower()
