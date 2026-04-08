"""Tests for feedback_guardrails.py — deduplication and rate-limiting."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("LOG_DIR", "/tmp")
os.environ.setdefault("AUDIT_DIR", "/tmp")

import feedback_guardrails as mod
from feedback_guardrails import (
    _apply_feedback_guardrails,
    _prune_feedback_event_buffer,
    _reset_feedback_guardrails_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state before each test."""
    _reset_feedback_guardrails_for_tests()
    yield
    _reset_feedback_guardrails_for_tests()


# ---------------------------------------------------------------------------
# _prune_feedback_event_buffer
# ---------------------------------------------------------------------------

class TestPruneFeedbackEventBuffer:
    def test_keeps_events_within_window(self):
        now = 1000.0
        events = [990.0, 995.0, 999.0]
        result = _prune_feedback_event_buffer(events, now, 60.0)
        assert result == [990.0, 995.0, 999.0]

    def test_removes_stale_events(self):
        now = 1000.0
        events = [900.0, 930.0, 970.0, 999.0]
        result = _prune_feedback_event_buffer(events, now, 60.0)
        assert result == [970.0, 999.0]

    def test_empty_list_returns_empty(self):
        assert _prune_feedback_event_buffer([], 1000.0, 60.0) == []

    def test_zero_window(self):
        now = 1000.0
        events = [999.9, 1000.0]
        result = _prune_feedback_event_buffer(events, now, 0.0)
        assert 1000.0 in result
        assert 999.9 not in result

    def test_negative_window_treated_as_zero(self):
        now = 1000.0
        events = [999.9, 1000.0]
        result = _prune_feedback_event_buffer(events, now, -10.0)
        assert 1000.0 in result

    def test_large_window_keeps_all(self):
        now = 1000.0
        events = [1.0, 100.0, 500.0, 999.0]
        result = _prune_feedback_event_buffer(events, now, 9999.0)
        assert result == [1.0, 100.0, 500.0, 999.0]


# ---------------------------------------------------------------------------
# _apply_feedback_guardrails — accepted path
# ---------------------------------------------------------------------------

class TestApplyFeedbackGuardrailsAccepted:
    def test_first_event_accepted(self):
        accepted, reason = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.0
        )
        assert accepted is True
        assert reason == "accepted"

    def test_different_ratings_both_accepted(self):
        accepted1, _ = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.0
        )
        accepted2, _ = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="not_helpful", now=1001.0
        )
        assert accepted1 is True
        assert accepted2 is True

    def test_different_messages_both_accepted(self):
        accepted1, _ = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=100, rating="helpful", now=1000.0
        )
        accepted2, _ = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=200, rating="helpful", now=1000.5
        )
        assert accepted1 is True
        assert accepted2 is True


# ---------------------------------------------------------------------------
# _apply_feedback_guardrails — dedupe path
# ---------------------------------------------------------------------------

class TestApplyFeedbackGuardrailsDedupe:
    def test_duplicate_within_window_rejected(self):
        _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.0
        )
        accepted, reason = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.1
        )
        assert accepted is False
        assert reason == "dedupe"

    def test_same_event_outside_window_accepted(self):
        _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.0
        )
        accepted, reason = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1005.0
        )
        # 5 seconds later — outside the 2-second dedupe window
        assert accepted is True
        assert reason == "accepted"


# ---------------------------------------------------------------------------
# _apply_feedback_guardrails — user rate limit
# ---------------------------------------------------------------------------

class TestApplyFeedbackGuardrailsUserRateLimit:
    def test_user_rate_limit_hit(self):
        """After 6 unique events from same user in window, 7th should be rate limited."""
        now = 1000.0
        for i in range(6):
            _apply_feedback_guardrails(
                user_id=99, channel_id=10, message_id=i, rating="helpful", now=now + i * 0.5
            )
        # 7th unique message
        accepted, reason = _apply_feedback_guardrails(
            user_id=99, channel_id=10, message_id=100, rating="helpful", now=now + 5.0
        )
        assert accepted is False
        assert reason == "rate_limited_user"


# ---------------------------------------------------------------------------
# _apply_feedback_guardrails — channel rate limit
# ---------------------------------------------------------------------------

class TestApplyFeedbackGuardrailsChannelRateLimit:
    def test_channel_rate_limit_hit(self):
        """After 40 unique events in same channel, 41st is rate limited."""
        now = 1000.0
        # Use many different user_ids to avoid user-level rate limit (max 6 per user)
        for i in range(40):
            user_id = i  # each event from different user
            _apply_feedback_guardrails(
                user_id=user_id, channel_id=99, message_id=i, rating="helpful",
                now=now + i * 0.1
            )
        accepted, reason = _apply_feedback_guardrails(
            user_id=9999, channel_id=99, message_id=9999, rating="helpful", now=now + 5.0
        )
        assert accepted is False
        assert reason == "rate_limited_channel"


# ---------------------------------------------------------------------------
# _apply_feedback_guardrails — None/edge inputs
# ---------------------------------------------------------------------------

class TestApplyFeedbackGuardrailsEdgeCases:
    def test_none_user_id_normalizes(self):
        accepted, reason = _apply_feedback_guardrails(
            user_id=None, channel_id=1, message_id=1, rating="helpful", now=1000.0
        )
        assert accepted is True

    def test_none_channel_id_normalizes(self):
        accepted, reason = _apply_feedback_guardrails(
            user_id=1, channel_id=None, message_id=1, rating="helpful", now=1000.0
        )
        assert accepted is True

    def test_rating_normalized_lowercase(self):
        """HELPFUL and helpful should be treated the same."""
        _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="HELPFUL", now=1000.0
        )
        accepted, reason = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.1
        )
        assert reason == "dedupe"

    def test_uses_monotonic_when_now_not_provided(self):
        """Should not raise when now=None."""
        accepted, reason = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=99, rating="helpful"
        )
        assert accepted is True


# ---------------------------------------------------------------------------
# _reset_feedback_guardrails_for_tests
# ---------------------------------------------------------------------------

class TestResetFeedbackGuardrailsForTests:
    def test_reset_clears_state(self):
        _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.0
        )
        _reset_feedback_guardrails_for_tests()
        # After reset, same event should be accepted again
        accepted, _ = _apply_feedback_guardrails(
            user_id=1, channel_id=2, message_id=3, rating="helpful", now=1000.0
        )
        assert accepted is True
