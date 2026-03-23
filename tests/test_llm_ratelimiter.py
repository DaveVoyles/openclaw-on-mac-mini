"""
Tests for the RateLimiter class and helpers in llm.py.

We only test the pure-Python logic (sliding-window rate limiting,
is_configured, get_rate_info) — no real Gemini API calls are made.
google-generativeai is imported normally; the model is only instantiated
lazily inside chat(), which we do not call here.
"""

import sys
import time
import pytest
from unittest.mock import MagicMock, patch

# If google-generativeai is not installed (e.g. CI without deps), stub it out
# before importing llm so the module loads cleanly.
if "google.generativeai" not in sys.modules:
    sys.modules.setdefault("google", MagicMock())
    sys.modules.setdefault("google.generativeai", MagicMock())
    sys.modules.setdefault("google.generativeai.protos", MagicMock())

from llm import RateLimiter, is_configured, get_rate_info  # noqa: E402


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_fresh_limiter_allows_request(self):
        rl = RateLimiter(per_minute=60, per_hour=500)
        assert rl.check() is True

    def test_record_increases_usage(self):
        rl = RateLimiter(per_minute=60, per_hour=500)
        before = rl.remaining_minute
        rl.record()
        assert rl.remaining_minute == before - 1

    def test_check_returns_false_when_per_minute_limit_hit(self):
        rl = RateLimiter(per_minute=3, per_hour=500)
        for _ in range(3):
            rl.record()
        assert rl.check() is False

    def test_check_returns_false_when_per_hour_limit_hit(self):
        rl = RateLimiter(per_minute=1000, per_hour=3)
        for _ in range(3):
            rl.record()
        assert rl.check() is False

    def test_check_returns_true_after_minute_window_expires(self):
        rl = RateLimiter(per_minute=2, per_hour=500)
        # Inject two timestamps that are >60s in the past
        old_time = time.monotonic() - 70
        rl._timestamps = [old_time, old_time]
        # Old calls should be pruned; new check should succeed
        assert rl.check() is True

    def test_remaining_minute_decreases_after_record(self):
        rl = RateLimiter(per_minute=10, per_hour=500)
        assert rl.remaining_minute == 10
        rl.record()
        assert rl.remaining_minute == 9

    def test_remaining_hour_decreases_after_record(self):
        rl = RateLimiter(per_minute=100, per_hour=10)
        assert rl.remaining_hour == 10
        rl.record()
        assert rl.remaining_hour == 9

    def test_remaining_minute_floored_at_zero(self):
        rl = RateLimiter(per_minute=2, per_hour=500)
        for _ in range(5):
            rl.record()
        assert rl.remaining_minute == 0

    def test_remaining_hour_floored_at_zero(self):
        rl = RateLimiter(per_minute=1000, per_hour=2)
        for _ in range(5):
            rl.record()
        assert rl.remaining_hour == 0

    def test_multiple_records_within_limits_allowed(self):
        rl = RateLimiter(per_minute=10, per_hour=100)
        for _ in range(5):
            rl.record()
        assert rl.check() is True

    def test_stale_minute_calls_are_pruned(self):
        rl = RateLimiter(per_minute=3, per_hour=500)
        old = time.monotonic() - 70  # >60 s ago — outside minute window
        rl._timestamps = [old, old, old]  # All stale for the minute window
        rl.record()  # Triggers prune + record
        assert rl.remaining_minute == 2  # 3 - 1 recent call

    def test_stale_hour_calls_are_pruned(self):
        rl = RateLimiter(per_minute=100, per_hour=3)
        old = time.monotonic() - 3700  # >1 hour ago — pruned from _timestamps
        rl._timestamps = [old, old, old]  # All stale for the hour window
        rl.record()
        assert rl.remaining_hour == 2  # 3 - 1 recent call


# ---------------------------------------------------------------------------
# is_configured / get_rate_info
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_false_when_no_api_key_and_local_llm_disabled(self):
        with patch("llm.GOOGLE_API_KEY", ""):
            with patch("llm.LOCAL_LLM_ENABLED", False):
                assert is_configured() is False

    def test_true_when_api_key_set(self):
        with patch("llm.GOOGLE_API_KEY", "AIzaSy_fake_key_for_testing"):
            assert is_configured() is True

    def test_true_when_local_llm_enabled_no_api_key(self):
        """Local LLM being enabled is sufficient for is_configured() to return True."""
        with patch("llm.GOOGLE_API_KEY", ""):
            with patch("llm.LOCAL_LLM_ENABLED", True):
                assert is_configured() is True


class TestGetRateInfo:
    def test_returns_string(self):
        result = get_rate_info()
        assert isinstance(result, str)

    def test_contains_per_min_and_per_hr(self):
        result = get_rate_info()
        assert "min" in result
        assert "hr" in result
