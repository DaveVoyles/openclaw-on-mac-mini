"""Tests for src/error_aggregator.py."""

import time

import pytest

from src.error_aggregator import ErrorAggregator, _fingerprint

# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_normalises_long_numbers(self):
        fp1 = _fingerprint("sonarr", "HTTP 500 at ts 1719000000")
        fp2 = _fingerprint("sonarr", "HTTP 500 at ts 1719099999")
        assert fp1 == fp2

    def test_normalises_hex_ids(self):
        fp1 = _fingerprint("radarr", "Job abcdef01 failed")
        fp2 = _fingerprint("radarr", "Job 99887766 failed")
        assert fp1 == fp2

    def test_different_services_differ(self):
        fp1 = _fingerprint("sonarr", "timeout")
        fp2 = _fingerprint("radarr", "timeout")
        assert fp1 != fp2

    def test_truncates_long_messages(self):
        long_msg = "x" * 200
        fp = _fingerprint("svc", long_msg)
        # The normalised part is capped at 100 chars
        assert len(fp) <= len("svc:") + 100


# ---------------------------------------------------------------------------
# ErrorAggregator basics
# ---------------------------------------------------------------------------


class TestErrorAggregator:
    @pytest.fixture()
    def agg(self):
        return ErrorAggregator(window_seconds=3600, flush_interval=300)

    @pytest.mark.asyncio
    async def test_single_error_no_count_suffix(self, agg):
        await agg.record("sonarr", "HTTP 500")
        lines = await agg.flush()
        assert len(lines) == 1
        assert "⚠️ HTTP 500" in lines[0]
        assert "x**" not in lines[0]  # no multiplier for single occurrence

    @pytest.mark.asyncio
    async def test_duplicate_errors_aggregated(self, agg):
        for _ in range(12):
            await agg.record("sonarr", "HTTP 500 from /api/v3/health")
        lines = await agg.flush()
        assert len(lines) == 1
        assert "**12x**" in lines[0]

    @pytest.mark.asyncio
    async def test_different_errors_separate_lines(self, agg):
        await agg.record("sonarr", "HTTP 500")
        await agg.record("radarr", "connection refused")
        lines = await agg.flush()
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_flush_clears_buckets(self, agg):
        await agg.record("sonarr", "HTTP 500")
        await agg.flush()
        assert agg.pending_count == 0
        lines = await agg.flush()
        assert lines == []

    @pytest.mark.asyncio
    async def test_pending_count(self, agg):
        assert agg.pending_count == 0
        await agg.record("a", "err1")
        await agg.record("a", "err1")
        await agg.record("b", "err2")
        assert agg.pending_count == 3

    @pytest.mark.asyncio
    async def test_expired_errors_skipped(self, agg):
        await agg.record("old", "stale error")
        # Simulate the error being very old
        bucket = list(agg._buckets.values())[0]
        bucket.last_seen = time.time() - agg.window_seconds - 1

        lines = await agg.flush()
        assert len(lines) == 0
