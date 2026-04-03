"""Tests for llm_ratelimit.py — sliding-window rate limiter."""

import asyncio
import time
from unittest.mock import patch

import pytest

import llm_ratelimit as mod
from llm_ratelimit import RateLimiter


class TestRateLimiterCheck:
    def test_allows_when_under_limit(self):
        rl = RateLimiter(per_minute=10, per_hour=100)
        assert rl.check() is True

    def test_blocks_when_minute_limit_exceeded(self):
        rl = RateLimiter(per_minute=2, per_hour=100)
        rl.record()
        rl.record()
        assert rl.check() is False

    def test_blocks_when_hour_limit_exceeded(self):
        rl = RateLimiter(per_minute=100, per_hour=3)
        rl.record()
        rl.record()
        rl.record()
        assert rl.check() is False


class TestRateLimiterRecord:
    def test_record_adds_timestamp(self):
        rl = RateLimiter(per_minute=10, per_hour=100)
        assert len(rl._timestamps) == 0
        rl.record()
        assert len(rl._timestamps) == 1

    def test_remaining_minute_decreases(self):
        rl = RateLimiter(per_minute=5, per_hour=100)
        assert rl.remaining_minute == 5
        rl.record()
        assert rl.remaining_minute == 4

    def test_remaining_hour_decreases(self):
        rl = RateLimiter(per_minute=100, per_hour=10)
        assert rl.remaining_hour == 10
        rl.record()
        assert rl.remaining_hour == 9


class TestRateLimiterEviction:
    def test_evicts_old_timestamps(self):
        rl = RateLimiter(per_minute=10, per_hour=100)
        # Insert a timestamp from 2 hours ago
        old_ts = time.monotonic() - 7200
        rl._timestamps.append(old_ts)
        rl._evict()
        assert len(rl._timestamps) == 0


class TestWaitForCapacity:
    @pytest.mark.asyncio
    async def test_returns_true_when_capacity_available(self):
        rl = RateLimiter(per_minute=10, per_hour=100)
        result = await rl.wait_for_capacity(max_wait=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_exhausted(self):
        rl = RateLimiter(per_minute=1, per_hour=1)
        rl.record()
        result = await rl.wait_for_capacity(max_wait=0.5)
        assert result is False
