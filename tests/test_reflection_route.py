"""Tests for reflection route decoupling (select_reflection_route)."""

from __future__ import annotations

from model_routing_policy import ReflectionRouteDecision, select_reflection_route


class TestSelectReflectionRoute:
    def test_copilot_selected_when_available(self):
        decision = select_reflection_route(copilot_available=True)
        assert decision.provider == "copilot"
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0

    def test_gemini_selected_when_copilot_unavailable(self):
        decision = select_reflection_route(copilot_available=False)
        assert decision.provider == "gemini"

    def test_returns_reflection_route_decision_dataclass(self):
        decision = select_reflection_route(copilot_available=True)
        assert isinstance(decision, ReflectionRouteDecision)

    def test_reason_mentions_quota_when_copilot_available(self):
        decision = select_reflection_route(copilot_available=True)
        reason_lower = decision.reason.lower()
        # Should communicate why Copilot is preferred
        assert "quota" in reason_lower or "copilot" in reason_lower

    def test_reason_mentions_fallback_when_no_copilot(self):
        decision = select_reflection_route(copilot_available=False)
        reason_lower = decision.reason.lower()
        assert "gemini" in reason_lower or "fallback" in reason_lower or "unavailable" in reason_lower
