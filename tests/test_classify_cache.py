"""Tests for W10-A (LRU classify cache) and W10-B (regex bypass in web search route)."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio  # noqa: F401 — needed for async test collection

import model_routing_policy
from model_routing_policy import (
    _HIGH_CONFIDENCE_SEARCH_RE,
    classify_query_llm,
    clear_classify_cache,
    select_web_search_route,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the LRU cache before and after every test."""
    clear_classify_cache()
    yield
    clear_classify_cache()


def _make_llm_mock(return_value="coding"):
    return AsyncMock(return_value=return_value)


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_on_second_call(monkeypatch):
    """Same query twice: the inner LLM function is called only once."""
    mock_llm = _make_llm_mock("coding")
    monkeypatch.setattr(model_routing_policy, "_classify_text_with_llm", mock_llm)

    result1 = await classify_query_llm("write a python script")
    result2 = await classify_query_llm("write a python script")

    assert result1 == result2 == "coding"
    mock_llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_miss_on_different_query(monkeypatch):
    """Two distinct queries both reach the LLM (two cache misses)."""
    mock_llm = _make_llm_mock("general")
    monkeypatch.setattr(model_routing_policy, "_classify_text_with_llm", mock_llm)

    await classify_query_llm("first query")
    await classify_query_llm("second query")

    assert mock_llm.await_count == 2


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(monkeypatch):
    """When TTL is 0 the entry is already stale on the second call."""
    monkeypatch.setattr(model_routing_policy, "_CLASSIFY_CACHE_TTL", 0.0)

    mock_llm = _make_llm_mock("math")
    monkeypatch.setattr(model_routing_policy, "_classify_text_with_llm", mock_llm)

    await classify_query_llm("solve 2+2")
    await classify_query_llm("solve 2+2")

    assert mock_llm.await_count == 2


@pytest.mark.asyncio
async def test_clear_classify_cache(monkeypatch):
    """After clearing the cache a repeated query must reach the LLM again."""
    mock_llm = _make_llm_mock("general")
    monkeypatch.setattr(model_routing_policy, "_classify_text_with_llm", mock_llm)

    await classify_query_llm("tell me a joke")
    clear_classify_cache()
    await classify_query_llm("tell me a joke")

    assert mock_llm.await_count == 2


# ---------------------------------------------------------------------------
# Regex bypass tests
# ---------------------------------------------------------------------------


def test_regex_bypass_fires_for_known_patterns():
    """'what is the current score' hits the high-confidence bypass."""
    decision = select_web_search_route("what is the current score")
    assert decision.prefer_search is True
    assert "regex-bypass" in decision.reason


def test_regex_bypass_fires_for_today():
    """'what happened today' contains 'today' — should trigger bypass."""
    decision = select_web_search_route("what happened today")
    assert decision.prefer_search is True
    assert "regex-bypass" in decision.reason


@pytest.mark.asyncio
async def test_regex_bypass_does_not_fire_for_ambiguous(monkeypatch):
    """'explain quantum computing' must not hit the regex bypass.

    Additionally, classify_query_llm() should delegate to the inner LLM
    helper for this query (no cached bypass short-circuits it).
    """
    decision = select_web_search_route("explain quantum computing")
    assert "regex-bypass" not in decision.reason

    mock_llm = _make_llm_mock("general")
    monkeypatch.setattr(model_routing_policy, "_classify_text_with_llm", mock_llm)

    await classify_query_llm("explain quantum computing")
    mock_llm.assert_awaited_once()


def test_regex_bypass_pattern_coverage():
    """Spot-check a range of expected-match and expected-no-match strings."""
    should_match = [
        "today",
        "tonight",
        "right now",
        "currently",
        "live score",
        "latest news",
        "breaking news",
        "current price",
        "current weather",
        "current standings",
        "what is the current rate",
        "who are the current rankings",
        "this week",
        "last month",
        "this season",
        "yesterday's results",
        "score of the game",
        "standings table",
        "box score",
        "game results",
    ]
    should_not_match = [
        "explain quantum computing",
        "write me a poem",
        "translate this sentence",
        "debug my code",
        "summarize this article",
    ]

    for query in should_match:
        assert _HIGH_CONFIDENCE_SEARCH_RE.search(query), f"Expected match but got none: {query!r}"

    for query in should_not_match:
        assert not _HIGH_CONFIDENCE_SEARCH_RE.search(query), f"Expected no match but got one: {query!r}"
