"""Tests for property/real-estate search fast-path routing."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from model_routing_policy import PropertySearchRouteDecision, select_property_search_route


class TestSelectPropertySearchRoute:
    def test_find_homes_matches(self):
        decision = select_property_search_route("find me 3 homes in broomall")
        assert decision.prefer_perplexity is True

    def test_show_listings_matches(self):
        decision = select_property_search_route("show me house listings under 450000")
        assert decision.prefer_perplexity is True

    def test_homes_for_sale_matches(self):
        decision = select_property_search_route("homes for sale in havertown pa")
        assert decision.prefer_perplexity is True

    def test_property_search_matches(self):
        decision = select_property_search_route("search for properties in upper darby")
        assert decision.prefer_perplexity is True

    def test_redfin_zillow_trulia_matches(self):
        for site in ["zillow", "redfin", "trulia"]:
            d = select_property_search_route(f"look on {site} for condos")
            assert d.prefer_perplexity is True, f"Expected match for {site}"

    def test_apartments_matches(self):
        decision = select_property_search_route("find apartments near broomall")
        assert decision.prefer_perplexity is True

    def test_houses_available_matches(self):
        decision = select_property_search_route("what houses are available in upper darby")
        assert decision.prefer_perplexity is True

    def test_weather_does_not_match(self):
        decision = select_property_search_route("what is the weather today")
        assert decision.prefer_perplexity is False

    def test_sports_does_not_match(self):
        decision = select_property_search_route("who won the NBA game last night")
        assert decision.prefer_perplexity is False

    def test_yes_does_not_match(self):
        decision = select_property_search_route("yes")
        assert decision.prefer_perplexity is False

    def test_empty_does_not_match(self):
        decision = select_property_search_route("")
        assert decision.prefer_perplexity is False

    def test_returns_dataclass(self):
        decision = select_property_search_route("find homes in broomall")
        assert isinstance(decision, PropertySearchRouteDecision)

    def test_reason_is_non_empty_string(self):
        decision = select_property_search_route("find homes in broomall")
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0

    def test_non_match_reason_is_non_empty(self):
        decision = select_property_search_route("tell me a joke")
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0

    def test_recalled_context_query_with_criteria(self):
        """Simulates model_message with recalled context prepended."""
        ctx_query = (
            "User preferences:\n"
            "- Price $300k–$450k\n"
            "- Property taxes < $8k/year\n"
            "- Neighborhoods: Broomall, Havertown, Upper Darby\n\n"
            "---\nUser's question: Can you find me 3 homes in broomall which match those requirements?"
        )
        decision = select_property_search_route(ctx_query)
        assert decision.prefer_perplexity is True

    def test_answer_policy_marker_registered(self):
        """generate_property_search_report must be in _DIRECT_RETURN_MARKERS."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from answer_policy import _DIRECT_RETURN_MARKERS
        assert "generate_property_search_report" in _DIRECT_RETURN_MARKERS
        assert "_via perplexity-direct_" in _DIRECT_RETURN_MARKERS["generate_property_search_report"]
