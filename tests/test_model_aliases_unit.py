"""Unit tests for model_aliases.py — additional edge cases beyond test_model_aliases_constants.py."""
from __future__ import annotations

import pytest

from model_aliases import (
    MODEL_INPUT_ALIASES,
    VALID_MODEL_PREFERENCES,
    model_input_suggestion,
    normalize_model_input,
)


# ---------------------------------------------------------------------------
# normalize_model_input — additional edge cases
# ---------------------------------------------------------------------------

class TestNormalizeModelInputEdgeCases:
    def test_mixed_case_alias(self):
        assert normalize_model_input("Claude") == "anthropic"

    def test_leading_trailing_spaces_with_alias(self):
        assert normalize_model_input("  claude  ") == "anthropic"

    def test_unknown_value_preserved_lowercased(self):
        result = normalize_model_input("GPT4")
        assert result == "gpt4"

    def test_single_space_returns_empty(self):
        result = normalize_model_input(" ")
        assert result == ""

    def test_all_valid_preferences_pass_through(self):
        for pref in VALID_MODEL_PREFERENCES:
            assert normalize_model_input(pref) == pref

    def test_alias_dict_values_are_valid_preferences(self):
        for alias, canonical in MODEL_INPUT_ALIASES.items():
            # All canonical values should be in VALID_MODEL_PREFERENCES
            assert canonical in VALID_MODEL_PREFERENCES, (
                f"MODEL_INPUT_ALIASES['{alias}'] = '{canonical}' not in VALID_MODEL_PREFERENCES"
            )


# ---------------------------------------------------------------------------
# model_input_suggestion — additional edge cases
# ---------------------------------------------------------------------------

class TestModelInputSuggestionEdgeCases:
    def test_exact_valid_pref_returns_empty(self):
        for pref in VALID_MODEL_PREFERENCES:
            assert model_input_suggestion(pref) == "", f"Expected empty for valid pref '{pref}'"

    def test_alias_suggestion_contains_canonical(self):
        for alias, canonical in MODEL_INPUT_ALIASES.items():
            result = model_input_suggestion(alias)
            assert canonical in result, f"Expected '{canonical}' in suggestion for alias '{alias}'"

    def test_result_is_always_string(self):
        for val in ["", "claude", "blarg", "  openai  ", "xyz123"]:
            assert isinstance(model_input_suggestion(val), str)

    def test_suggestion_for_whitespace_only(self):
        # Whitespace stripped → empty string → returns ""
        result = model_input_suggestion("   ")
        assert result == ""

    def test_close_typo_for_local(self):
        # "loca" is close to "local"
        result = model_input_suggestion("loca")
        assert isinstance(result, str)  # may or may not suggest, but must be str

    def test_suggestion_for_mixed_case_alias(self):
        result = model_input_suggestion("CLAUDE")
        assert "anthropic" in result.lower() or "claude" in result.lower()


# ---------------------------------------------------------------------------
# DATA — constants structure
# ---------------------------------------------------------------------------

class TestModelAliasesDataIntegrity:
    def test_aliases_dict_is_nonempty(self):
        assert len(MODEL_INPUT_ALIASES) >= 1

    def test_valid_preferences_contains_expected_providers(self):
        expected = {"auto", "local", "gemini", "openai", "anthropic"}
        assert expected.issubset(VALID_MODEL_PREFERENCES)

    def test_copilot_in_valid_preferences(self):
        assert "copilot" in VALID_MODEL_PREFERENCES

    def test_alias_keys_are_strings(self):
        for key in MODEL_INPUT_ALIASES:
            assert isinstance(key, str)

    def test_alias_values_are_strings(self):
        for val in MODEL_INPUT_ALIASES.values():
            assert isinstance(val, str)
