"""
Tests for search_provider.py — SearchStats tracking and registry.
"""

import asyncio

import aiohttp
import pytest

import search_provider as mod


@pytest.fixture(autouse=True)
def _clear_stats(monkeypatch):
    """Reset global stats between tests."""
    monkeypatch.setattr(mod, "_stats", {})


# ---------------------------------------------------------------------------
# SearchStats dataclass
# ---------------------------------------------------------------------------


class TestSearchStats:
    def test_record_success_increments(self):
        s = mod.SearchStats(provider="test")
        s.record_success(50.0)
        assert s.total_calls == 1
        assert s.total_successes == 1
        assert s.total_latency_ms == 50.0

    def test_record_failure_increments(self):
        s = mod.SearchStats(provider="test")
        s.record_failure()
        assert s.total_calls == 1
        assert s.total_failures == 1
        assert s.total_successes == 0

    def test_record_retry_increments(self):
        s = mod.SearchStats(provider="test")
        s.record_retry()
        assert s.total_retries == 1
        assert s.total_calls == 0  # retries don't count as calls

    def test_success_rate_zero_calls(self):
        s = mod.SearchStats(provider="test")
        assert s.success_rate == 0.0

    def test_success_rate_mixed(self):
        s = mod.SearchStats(provider="test")
        s.record_success(10.0)
        s.record_success(20.0)
        s.record_failure()
        assert s.success_rate == pytest.approx(2 / 3)

    def test_avg_latency(self):
        s = mod.SearchStats(provider="test")
        s.record_success(100.0)
        s.record_success(200.0)
        assert s.avg_latency_ms == pytest.approx(150.0)

    def test_avg_latency_zero_successes(self):
        s = mod.SearchStats(provider="test")
        s.record_failure()
        assert s.avg_latency_ms == 0.0


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------


class TestStatsRegistry:
    def test_get_stats_creates_new(self):
        s = mod.get_stats("brave")
        assert s.provider == "brave"
        assert s.total_calls == 0

    def test_get_stats_returns_same_instance(self):
        s1 = mod.get_stats("brave")
        s2 = mod.get_stats("brave")
        assert s1 is s2

    def test_all_stats_serializes(self):
        mod.get_stats("a").record_success(10.0)
        mod.get_stats("b").record_failure()
        result = mod.all_stats()
        assert set(result.keys()) == {"a", "b"}
        assert result["a"]["successes"] == 1
        assert result["b"]["failures"] == 1
        assert "success_rate" in result["a"]
        assert "avg_latency_ms" in result["a"]


# ---------------------------------------------------------------------------
# retry_once
# ---------------------------------------------------------------------------


class TestRetryOnce:
    async def test_success_no_retry(self):
        calls = []

        async def factory():
            calls.append(1)
            return "ok"

        result = await mod.retry_once(factory, "test")
        assert result == "ok"
        assert len(calls) == 1

    async def test_retries_on_client_error(self):
        calls = []

        async def factory():
            calls.append(1)
            if len(calls) == 1:
                raise aiohttp.ClientError("boom")
            return "recovered"

        result = await mod.retry_once(factory, "test")
        assert result == "recovered"
        assert len(calls) == 2
        assert mod.get_stats("test").total_retries == 1

    async def test_retries_on_timeout(self):
        calls = []

        async def factory():
            calls.append(1)
            if len(calls) == 1:
                raise asyncio.TimeoutError()
            return "ok"

        result = await mod.retry_once(factory, "test")
        assert result == "ok"
        assert len(calls) == 2
