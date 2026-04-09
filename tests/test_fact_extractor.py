"""Tests for src/fact_extractor.py — should_extract (pure logic, no external deps)."""
import pytest

# fact_extractor module-level code only uses standard library — no stubs needed
from fact_extractor import EXTRACT_EVERY_N, _extraction_counter, should_extract


@pytest.fixture(autouse=True)
def reset_counter():
    """Reset per-user extraction counter between tests."""
    _extraction_counter.clear()
    yield
    _extraction_counter.clear()


class TestShouldExtract:
    def test_too_short_returns_false(self):
        assert should_extract(1, "hi") is False

    def test_skip_greeting_returns_false(self):
        msg = "hello there how are you doing today sir"  # >30 chars but matches skip pattern
        assert should_extract(1, msg) is False

    def test_skip_slash_command(self):
        assert should_extract(1, "/ask something long enough to pass length check easily") is False

    def test_skip_pure_question(self):
        msg = "what is this"
        assert should_extract(1, msg) is False

    def test_valid_message_rate_limited(self):
        user_id = 42
        msg = "I moved to Seattle last year and started working at a startup there."
        # First two calls should be False (counter doesn't hit modulo yet)
        results = [should_extract(user_id, msg) for _ in range(EXTRACT_EVERY_N)]
        # The EXTRACT_EVERY_N-th call should be True
        assert results[-1] is True
        # Non-final calls should be False
        assert all(r is False for r in results[:-1])

    def test_rate_limit_cycles(self):
        user_id = 99
        msg = "I enjoy hiking in the mountains every weekend with my dog Max."
        true_count = 0
        total = EXTRACT_EVERY_N * 3
        for _ in range(total):
            if should_extract(user_id, msg):
                true_count += 1
        assert true_count == 3

    def test_different_users_tracked_independently(self):
        msg = "I live in Portland and work remotely as a software engineer."
        # Drain user 1's counter to the extraction point
        for _ in range(EXTRACT_EVERY_N - 1):
            should_extract(1, msg)
        # User 2 should still be on their own counter
        assert should_extract(2, msg) is False  # user 2 count = 1, not divisible yet

    def test_empty_message_returns_false(self):
        assert should_extract(1, "") is False

    def test_whitespace_only_returns_false(self):
        assert should_extract(1, "   ") is False
