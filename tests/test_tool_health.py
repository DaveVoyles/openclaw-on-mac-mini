"""Tests for tool_health module — CircuitBreaker and ToolHealthTracker."""

import time
from unittest.mock import patch

import pytest

from tool_health import CircuitBreaker, ToolHealthTracker

# ---------------------------------------------------------------------------
# CircuitBreaker tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_initially_closed(self):
        cb = CircuitBreaker(max_failures=3, cooldown_seconds=60)
        assert not cb.is_open("search_web")

    def test_opens_after_max_failures(self):
        cb = CircuitBreaker(max_failures=3, cooldown_seconds=60)
        cb.record_failure("search_web")
        cb.record_failure("search_web")
        assert not cb.is_open("search_web")
        cb.record_failure("search_web")
        assert cb.is_open("search_web")

    def test_success_resets_counter(self):
        cb = CircuitBreaker(max_failures=2, cooldown_seconds=60)
        cb.record_failure("browse_url")
        cb.record_success("browse_url")
        cb.record_failure("browse_url")
        assert not cb.is_open("browse_url")

    def test_cooldown_allows_retry(self):
        cb = CircuitBreaker(max_failures=2, cooldown_seconds=0.1)
        cb.record_failure("tool_a")
        cb.record_failure("tool_a")
        assert cb.is_open("tool_a")
        time.sleep(0.15)
        # After cooldown, circuit goes half-open (allows one retry)
        assert not cb.is_open("tool_a")

    def test_independent_tools(self):
        cb = CircuitBreaker(max_failures=2, cooldown_seconds=60)
        cb.record_failure("tool_a")
        cb.record_failure("tool_a")
        assert cb.is_open("tool_a")
        assert not cb.is_open("tool_b")

    def test_status_snapshot(self):
        cb = CircuitBreaker(max_failures=2, cooldown_seconds=60)
        cb.record_failure("x")
        cb.record_failure("x")
        status = cb.status()
        assert "x" in status
        assert status["x"]["is_open"] is True
        assert status["x"]["failures"] == 2


# ---------------------------------------------------------------------------
# ToolHealthTracker tests
# ---------------------------------------------------------------------------


class TestToolHealthTracker:
    def test_default_success_rate(self):
        tracker = ToolHealthTracker(persist_every=100)
        assert tracker.success_rate("unknown_tool") == 1.0

    def test_records_success_and_failure(self):
        tracker = ToolHealthTracker(persist_every=100)
        tracker.record("search_web", success=True)
        tracker.record("search_web", success=True)
        tracker.record("search_web", success=False)
        assert tracker.success_rate("search_web") == pytest.approx(2 / 3, rel=0.01)

    def test_summary(self):
        tracker = ToolHealthTracker(persist_every=100)
        tracker.record("a", success=True)
        tracker.record("a", success=False)
        s = tracker.summary()
        assert "a" in s
        assert s["a"]["total"] == 2
        assert s["a"]["success_rate"] == 0.5

    def test_tool_health_persistence(self, tmp_path):
        health_file = tmp_path / "tool_health.json"
        with patch("tool_health._HEALTH_FILE", health_file):
            t1 = ToolHealthTracker(persist_every=1)
            t1.record("x", success=True)
            # File should exist after persist_every writes
            assert health_file.exists()

            t2 = ToolHealthTracker(persist_every=100)
            assert t2.success_rate("x") == 1.0
