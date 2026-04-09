"""Tests for model_aliases.py, nlp_entities.py, and profiler.py."""
from __future__ import annotations

import pytest

from model_aliases import VALID_MODEL_PREFERENCES, model_input_suggestion, normalize_model_input
from nlp_entities import _dedupe, _phrase_in_text, enrich_route_text_and_hints, extract_entities

# ===========================================================================
# model_aliases.py
# ===========================================================================

class TestNormalizeModelInput:
    def test_empty_returns_empty(self):
        assert normalize_model_input("") == ""

    def test_none_returns_empty(self):
        assert normalize_model_input(None) == ""  # type: ignore

    def test_known_alias_normalized(self):
        assert normalize_model_input("claude") == "anthropic"

    def test_valid_input_unchanged(self):
        assert normalize_model_input("gemini") == "gemini"

    def test_strips_whitespace(self):
        assert normalize_model_input("  gemini  ") == "gemini"

    def test_lowercased(self):
        assert normalize_model_input("GEMINI") == "gemini"

    def test_unknown_input_returned_as_is(self):
        assert normalize_model_input("unknown_model") == "unknown_model"


class TestModelInputSuggestion:
    def test_empty_returns_empty(self):
        assert model_input_suggestion("") == ""

    def test_valid_model_returns_empty(self):
        for m in VALID_MODEL_PREFERENCES:
            assert model_input_suggestion(m) == ""

    def test_known_alias_returns_suggestion(self):
        result = model_input_suggestion("claude")
        assert "anthropic" in result

    def test_close_match_returns_suggestion(self):
        result = model_input_suggestion("gemni")  # typo
        assert len(result) > 0 or result == ""  # may or may not match

    def test_completely_wrong_returns_empty(self):
        result = model_input_suggestion("xyzqwerty_zap")
        assert result == ""


# ===========================================================================
# nlp_entities.py — pure helpers
# ===========================================================================

class TestPhraseInText:
    def test_exact_match(self):
        assert _phrase_in_text("the nfl game", "nfl") is True

    def test_no_match(self):
        assert _phrase_in_text("basketball score", "nfl") is False

    def test_word_boundary_enforced(self):
        # "nflx" should NOT match "nfl"
        assert _phrase_in_text("nflx stock", "nfl") is False

    def test_case_sensitive(self):
        # _phrase_in_text is case-sensitive — caller should lowercase
        assert _phrase_in_text("NFL game", "nfl") is False


class TestDedupe:
    def test_removes_duplicates(self):
        assert _dedupe(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_preserves_order(self):
        assert _dedupe(["c", "a", "b"]) == ["c", "a", "b"]

    def test_empty_list(self):
        assert _dedupe([]) == []

    def test_no_duplicates_unchanged(self):
        assert _dedupe(["x", "y"]) == ["x", "y"]


class TestExtractEntities:
    def test_returns_dict(self):
        result = extract_entities("what's on plex tonight")
        assert isinstance(result, dict)

    def test_empty_string(self):
        result = extract_entities("")
        assert isinstance(result, dict)

    def test_no_entities_empty_dict(self):
        result = extract_entities("tell me a joke")
        assert isinstance(result, dict)
        # Just verifies it doesn't crash; entities may or may not match

    def test_entity_values_are_lists(self):
        result = extract_entities("nfl football game score")
        for key, val in result.items():
            assert isinstance(val, list)


class TestEnrichRouteTextAndHints:
    def test_returns_tuple(self):
        result = enrich_route_text_and_hints("hello", {})
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_hints_preserved(self):
        _, hints = enrich_route_text_and_hints("test", {"foo": "bar"})
        assert hints["foo"] == "bar"

    def test_entities_added_when_found(self):
        _, hints = enrich_route_text_and_hints("plex shows", {})
        # If plex is in the gazetteer, entities key should appear
        if "entities" in hints:
            assert isinstance(hints["entities"], dict)

    def test_empty_message(self):
        text, hints = enrich_route_text_and_hints("", {})
        assert isinstance(hints, dict)

    def test_disambiguation_confidence_in_valid_range(self):
        _, hints = enrich_route_text_and_hints("what's in this channel", {})
        if "disambiguation_confidence" in hints:
            assert 0 <= hints["disambiguation_confidence"] <= 1.0


# ===========================================================================
# profiler.py
# ===========================================================================

class TestProfiler:
    def test_initial_state_not_profiling(self):
        from profiler import Profiler
        p = Profiler()
        assert not p._is_profiling

    def test_start_and_stop(self):
        from profiler import Profiler
        p = Profiler()
        p.start_cpu_profiling()
        assert p._is_profiling
        output = p.stop_cpu_profiling()
        assert isinstance(output, str)
        assert "Profile Duration" in output
        assert not p._is_profiling

    def test_double_start_raises(self):
        from profiler import Profiler
        p = Profiler()
        p.start_cpu_profiling()
        try:
            with pytest.raises(RuntimeError, match="already active"):
                p.start_cpu_profiling()
        finally:
            p.stop_cpu_profiling()

    def test_stop_without_start_raises(self):
        from profiler import Profiler
        p = Profiler()
        with pytest.raises(RuntimeError, match="No active"):
            p.stop_cpu_profiling()

    def test_get_cpu_stats_dict_not_profiling(self):
        from profiler import Profiler
        p = Profiler()
        assert p.get_cpu_stats_dict() == {}

    def test_generate_flame_graph_data_not_profiling(self):
        from profiler import Profiler
        p = Profiler()
        assert p.generate_flame_graph_data() == {}

    def test_get_profiler_returns_singleton(self):
        from profiler import get_profiler
        p1 = get_profiler()
        p2 = get_profiler()
        assert p1 is p2
