"""Tests for model_routing_policy mini-model fast-path additions."""

from model_routing_policy import (
    MINI_MODEL_MAX_TOKENS,
    ModelRoute,
    classify_query,
    copilot_model_for_message,
)


class TestModelRouteModelField:
    def test_model_field_defaults_empty(self):
        r = ModelRoute("copilot", "some reason")
        assert r.model == ""

    def test_model_field_set_explicitly(self):
        r = ModelRoute("copilot", "reason", model="gpt-4o-mini")
        assert r.model == "gpt-4o-mini"

    def test_repr_includes_model_when_set(self):
        r = ModelRoute("copilot", "reason", model="gpt-4o-mini")
        assert "gpt-4o-mini" in repr(r)

    def test_repr_omits_model_when_empty(self):
        r = ModelRoute("copilot", "reason")
        assert "model=" not in repr(r)


class TestCopilotModelForMessage:
    def test_short_general_query_returns_mini(self):
        result = copilot_model_for_message("what time is it?", "copilot-first")
        assert result == "gpt-4o-mini"

    def test_coding_query_returns_empty(self):
        result = copilot_model_for_message("fix this python function", "copilot-first")
        assert result == ""

    def test_non_copilot_first_profile_returns_empty(self):
        result = copilot_model_for_message("hello", "gemini-first")
        assert result == ""

    def test_long_query_returns_empty(self):
        long_msg = "word " * (MINI_MODEL_MAX_TOKENS + 5)
        result = copilot_model_for_message(long_msg, "copilot-first")
        assert result == ""

    def test_empty_message_returns_mini(self):
        # Empty message → 0 tokens, no code → fast-path fires
        result = copilot_model_for_message("", "copilot-first")
        assert result == "gpt-4o-mini"

    def test_cost_saver_profile_returns_empty(self):
        result = copilot_model_for_message("hello", "cost-saver")
        assert result == ""


class TestClassifyQueryMiniModelHint:
    def test_mini_fast_path_sets_model_field(self):
        route = classify_query(
            "hello there",
            copilot_available=True,
            routing_profile="copilot-first",
        )
        assert route.model_type == "copilot"
        assert route.model == "gpt-4o-mini"

    def test_coding_query_does_not_set_mini_model(self):
        route = classify_query(
            "write a python script",
            copilot_available=True,
            routing_profile="copilot-first",
        )
        # Coding queries bypass the fast-path; model hint should be empty
        assert route.model == ""

    def test_non_copilot_first_no_mini_model(self):
        # gemini-first should never get mini-model routing (user chose Gemini explicitly).
        route = classify_query(
            "hello",
            copilot_available=True,
            routing_profile="gemini-first",
        )
        assert route.model == ""

    def test_balanced_profile_gets_mini_model(self):
        # balanced profile should get mini-model fast-path for short queries.
        route = classify_query(
            "hello",
            copilot_available=True,
            routing_profile="balanced",
        )
        assert route.model != "", "balanced profile should use mini-model for short queries"
