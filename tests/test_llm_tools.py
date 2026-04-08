"""
Tests for llm_tools.py — tool cache, skill stats, and execution.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import llm_tools as mod


@pytest.fixture(autouse=True)
def _reset_tool_state(monkeypatch):
    """Reset module-level tool caches and counters between tests."""
    monkeypatch.setattr(mod, "_tool_cache", {})
    monkeypatch.setattr(mod, "_skill_call_counts", {})


# ---------------------------------------------------------------------------
# get_skill_stats
# ---------------------------------------------------------------------------


class TestSkillStats:
    def test_empty_stats(self):
        assert mod.get_skill_stats() == {}

    def test_counts_sorted_descending(self, monkeypatch):
        monkeypatch.setattr(mod, "_skill_call_counts", {"a": 3, "b": 10, "c": 1})
        result = mod.get_skill_stats()
        keys = list(result.keys())
        assert keys == ["b", "a", "c"]
        assert result["b"] == 10


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_deterministic(self):
        k1 = mod._cache_key("get_stats", {"host": "nas", "port": 5001})
        k2 = mod._cache_key("get_stats", {"port": 5001, "host": "nas"})
        assert k1 == k2  # sorted args → same hash

    def test_different_name_different_key(self):
        k1 = mod._cache_key("func_a", {"x": 1})
        k2 = mod._cache_key("func_b", {"x": 1})
        assert k1 != k2


# ---------------------------------------------------------------------------
# _evict_tool_cache
# ---------------------------------------------------------------------------


class TestEvictToolCache:
    def test_evicts_expired(self, monkeypatch):
        now = time.monotonic()
        monkeypatch.setattr(mod, "_tool_cache", {
            "fresh": ("data", now),
            "stale": ("data", now - 60),  # well past 30s TTL
        })
        mod._evict_tool_cache()
        assert "fresh" in mod._tool_cache
        assert "stale" not in mod._tool_cache

    def test_evicts_over_max_size(self, monkeypatch):
        now = time.monotonic()
        cache = {f"k{i}": ("data", now - i) for i in range(300)}
        monkeypatch.setattr(mod, "_tool_cache", cache)
        monkeypatch.setattr(mod, "_TOOL_CACHE_MAX_SIZE", 256)
        mod._evict_tool_cache()
        assert len(mod._tool_cache) <= 256


# ---------------------------------------------------------------------------
# _execute_function_call
# ---------------------------------------------------------------------------


class TestExecuteFunctionCall:
    async def test_unknown_skill(self, monkeypatch):
        monkeypatch.setattr(mod, "SKILLS", {})
        result = await mod._execute_function_call("nonexistent", {})
        assert "Unknown function" in result

    async def test_successful_call(self, monkeypatch):
        mock_fn = AsyncMock(return_value="pong")
        monkeypatch.setattr(mod, "SKILLS", {"ping": mock_fn})

        mock_cb = MagicMock()
        mock_cb.is_open = MagicMock(return_value=False)
        mock_cb.record_success = MagicMock()

        mock_th = MagicMock()
        mock_th.record = MagicMock()

        with patch("llm_tools.circuit_breaker", mock_cb, create=True), \
             patch("llm_tools.tool_health", mock_th, create=True), \
             patch("tool_health.circuit_breaker", mock_cb), \
             patch("tool_health.tool_health", mock_th):
            result = await mod._execute_function_call("ping", {})

        assert result == "pong"
        assert mod._skill_call_counts["ping"] == 1

    async def test_circuit_open_fast_fails(self, monkeypatch):
        mock_fn = AsyncMock(return_value="ok")
        monkeypatch.setattr(mod, "SKILLS", {"broken": mock_fn})

        mock_cb = MagicMock()
        mock_cb.is_open = MagicMock(return_value=True)

        with patch("tool_health.circuit_breaker", mock_cb), \
             patch("tool_health.tool_health", MagicMock()):
            result = await mod._execute_function_call("broken", {})

        assert "circuit open" in result.lower()
        mock_fn.assert_not_awaited()

    async def test_skill_exception_returns_error(self, monkeypatch):
        mock_fn = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(mod, "SKILLS", {"fail": mock_fn})

        mock_cb = MagicMock()
        mock_cb.is_open = MagicMock(return_value=False)
        mock_cb.record_failure = MagicMock()

        mock_th = MagicMock()
        mock_th.record = MagicMock()

        with patch("tool_health.circuit_breaker", mock_cb), \
             patch("tool_health.tool_health", mock_th):
            result = await mod._execute_function_call("fail", {})

        assert "Error executing fail" in result
        assert "boom" in result

    async def test_cacheable_tool_returns_cached(self, monkeypatch):
        call_count = 0

        async def slow_fn():
            nonlocal call_count
            call_count += 1
            return "stats_data"

        monkeypatch.setattr(mod, "SKILLS", {"get_system_stats": slow_fn})

        mock_cb = MagicMock()
        mock_cb.is_open = MagicMock(return_value=False)
        mock_cb.record_success = MagicMock()

        mock_th = MagicMock()
        mock_th.record = MagicMock()

        with patch("tool_health.circuit_breaker", mock_cb), \
             patch("tool_health.tool_health", mock_th):
            r1 = await mod._execute_function_call("get_system_stats", {})
            r2 = await mod._execute_function_call("get_system_stats", {})

        assert r1 == r2 == "stats_data"
        assert call_count == 1  # second call served from cache


# ---------------------------------------------------------------------------
# _extract_final_text
# ---------------------------------------------------------------------------


class TestExtractFinalText:
    def test_simple_text_response(self):
        resp = MagicMock()
        resp.text = "Hello, world!"
        assert mod._extract_final_text(resp, 0, None) == "Hello, world!"

    def test_tool_limit_warning_appended(self, monkeypatch):
        monkeypatch.setattr(mod, "MAX_TOOL_ROUNDS", 5)
        resp = MagicMock()
        resp.text = "Here are the results."
        text = mod._extract_final_text(resp, 5, None)
        assert "Tool call limit reached" in text

    def test_response_text_is_none_returns_empty_string_guard(self):
        """response.text == None must not crash — the `or ''` guard handles it."""
        resp = MagicMock()
        resp.text = None  # None, not AttributeError — the recent fix guards this
        # Should fall through to the candidates fallback branch without raising
        resp.candidates = []  # empty so candidates fallback also produces ""
        resp.prompt_feedback = None
        text = mod._extract_final_text(resp, 0, None)
        # Result should be a non-crashing fallback string
        assert isinstance(text, str)

    def test_response_text_none_with_valid_candidates(self):
        """When response.text raises ValueError (e.g. blocked), fallback uses candidates parts."""
        resp = MagicMock()
        # Simulate response.text raising ValueError (blocked content)
        type(resp).text = property(lambda self: (_ for _ in ()).throw(ValueError("blocked")))
        part = MagicMock()
        part.text = "Fallback from candidates"
        resp.candidates = [MagicMock()]
        resp.candidates[0].content.parts = [part]
        resp.prompt_feedback = None
        text = mod._extract_final_text(resp, 0, None)
        assert "Fallback from candidates" in text
