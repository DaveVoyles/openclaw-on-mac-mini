"""Tests for select_web_search_route, WebSearchRouteDecision, answer_policy
registration, and generate_web_search_report."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from model_routing_policy import WebSearchRouteDecision, select_web_search_route

# ---------------------------------------------------------------------------
# select_web_search_route — pattern matching
# ---------------------------------------------------------------------------


class TestSelectWebSearchRoutePatterns:
    def test_news_query_prefers_search(self):
        decision = select_web_search_route("what's in the news today")
        assert decision.prefer_search is True

    def test_sports_query_prefers_search(self):
        decision = select_web_search_route("what are the latest NBA scores")
        assert decision.prefer_search is True

    def test_weather_query_prefers_search(self):
        decision = select_web_search_route("what's the weather in Philadelphia")
        assert decision.prefer_search is True

    def test_finance_query_prefers_search(self):
        decision = select_web_search_route("what is the current stock price of Apple")
        assert decision.prefer_search is True

    def test_property_query_prefers_search(self):
        decision = select_web_search_route(
            "find homes for sale in Broomall PA under 450000"
        )
        assert decision.prefer_search is True

    def test_entertainment_query_prefers_search(self):
        # Matches via question-form ("what" at start)
        decision = select_web_search_route("what movies are playing this weekend")
        assert decision.prefer_search is True

    def test_question_form_prefers_search(self):
        decision = select_web_search_route("who won the Super Bowl?")
        assert decision.prefer_search is True

    def test_coding_question_does_not_prefer_search(self):
        decision = select_web_search_route(
            "write a Python function to sort a list"
        )
        assert decision.prefer_search is False

    def test_empty_string_does_not_prefer_search(self):
        decision = select_web_search_route("")
        assert decision.prefer_search is False


# ---------------------------------------------------------------------------
# select_web_search_route — return type and reason field
# ---------------------------------------------------------------------------


class TestSelectWebSearchRouteReturnType:
    def test_returns_web_search_route_decision_dataclass(self):
        decision = select_web_search_route("latest tech news")
        assert isinstance(decision, WebSearchRouteDecision)

    def test_reason_is_nonempty_when_prefer_search_true(self):
        decision = select_web_search_route("what's the weather today")
        assert decision.prefer_search is True
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0

    def test_reason_is_nonempty_when_prefer_search_false(self):
        decision = select_web_search_route("write a Python function to sort a list")
        assert decision.prefer_search is False
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0


# ---------------------------------------------------------------------------
# answer_policy registration
# ---------------------------------------------------------------------------


class TestAnswerPolicyRegistration:
    def test_generate_web_search_report_is_in_direct_return_markers(self):
        from answer_policy import _DIRECT_RETURN_MARKERS

        assert "generate_web_search_report" in _DIRECT_RETURN_MARKERS

    def test_marker_contains_perplexity_direct_bypass_string(self):
        from answer_policy import _DIRECT_RETURN_MARKERS

        markers = _DIRECT_RETURN_MARKERS["generate_web_search_report"]
        assert any("_via perplexity-direct_" in m for m in markers)


# ---------------------------------------------------------------------------
# generate_web_search_report — smoke tests
# ---------------------------------------------------------------------------


class TestGenerateWebSearchReportSmoke:
    @pytest.mark.asyncio
    async def test_returns_nonempty_string_when_search_succeeds(self):
        from skills.reporting_skills import generate_web_search_report

        long_result = (
            "Here are the top results for your query. "
            "The market closed at 4,500 points today with broad gains across sectors. "
            "Sources: https://example.com/finance https://example.com/news"
        )
        with patch(
            "skills.search_skills.search_web",
            new=AsyncMock(return_value=long_result),
        ):
            result = await generate_web_search_report("current stock market news")

        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_returns_fallback_string_when_search_returns_empty(self):
        from skills.reporting_skills import generate_web_search_report

        with patch(
            "skills.search_skills.search_web",
            new=AsyncMock(return_value=""),
        ):
            result = await generate_web_search_report("current stock market news")

        assert isinstance(result, str)
        assert len(result) > 0
        assert "couldn't find" in result.lower() or "try" in result.lower()
